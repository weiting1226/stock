"""④ 股票型 ETF 資金流：從 ETF.com 取得美股 ETF 淨流入／流出。

規格第124行只給了五個等級（大幅淨流出 −2 ／淨流出 −1 ／持平 0 ／淨流入 +1 ／
大幅淨流入 +2），沒有給金額門檻。因此**方向由淨流的正負決定，「大幅」與否
則以它自己的歷史分布判定**——不自行發明「超過 X 億美元算大幅」這種數字。
這與 Gate B 用十年滾動百分位、而不是寫死絕對值，是同一個原則。

ETF.com 是商業網站、頁面由 JavaScript 產生，且開發環境的代理不放行該網域
（連不上，無法先行實測）。因此：

- 依序嘗試幾個已知位址與幾種解析方式，**每一種都記下實際結果**
  （狀態碼、內容型態、長度、有沒有 __NEXT_DATA__、表格數與形狀、樣本片段），
  全部失敗時把這些證據一起拋出。一次執行就要能看出結構長什麼樣，
  否則就是盲改重跑好幾輪。
- 抓不到一律拋錯，由呼叫端退回人工填寫值，絕不臆測填補。
"""
from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd
import requests

log = logging.getLogger(__name__)

# 依序嘗試。第一個是資金流工具頁，其餘為曾經出現過的替代路徑。
CANDIDATE_URLS = (
    "https://www.etf.com/etfanalytics/etf-fund-flows-tool",
    "https://www.etf.com/etf-fund-flows",
    "https://www.etf.com/etfanalytics/etf-fund-flows",
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Next.js 會把伺服器端資料放進這個 script 標籤，比解析畫面穩定得多
_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE
)

# 判斷一筆記錄是不是「美國股票型」的關鍵字
_EQUITY_HINTS = ("equity", "股票", "stock")
_US_HINTS = ("u.s.", "us ", "united states", "domestic", "north america")
# 金額欄位可能的名稱
# 比對時一律轉小寫並去掉底線，因此這裡只需列出正規化後的形式
_FLOW_KEYS = ("netflows", "netflow", "flows", "fundflows", "netcashflow", "value")


@dataclass
class FlowObservation:
    """一次觀測到的淨資金流。"""
    as_of: str
    net_flow_musd: float          # 百萬美元，正值為淨流入
    scope: str                    # 這筆數字涵蓋的範圍（美國股票型 ETF 等）
    period: str                   # 統計期間（1日／1週／1月…）
    source_url: str


def _to_musd(value) -> Optional[float]:
    """把各種寫法的金額轉成百萬美元。

    來源可能寫成 "1,234.5"、"-1.2B"、"$3.4M"、或已經是數字。
    看不懂就回 None，不臆測。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return None if f != f else f

    text = str(value).strip().replace(",", "").replace("$", "")
    if not text or text in {"-", "--", "N/A", "NA"}:
        return None

    negative = text.startswith("(") and text.endswith(")")   # 會計式負數
    text = text.strip("()")
    multiplier = 1.0
    if text[-1:].upper() == "B":
        multiplier, text = 1000.0, text[:-1]
    elif text[-1:].upper() == "M":
        multiplier, text = 1.0, text[:-1]
    elif text[-1:].upper() == "K":
        multiplier, text = 0.001, text[:-1]

    try:
        amount = float(text) * multiplier
    except ValueError:
        return None
    return -amount if negative else amount


def _looks_like_us_equity(label: str) -> bool:
    low = (label or "").lower()
    if not any(h in low for h in _EQUITY_HINTS):
        return False
    # 只寫 "Equity" 沒指明地區時，仍視為候選（工具頁預設多為美國市場）
    return True


def _walk_json(node, found: list, path: str = "") -> None:
    """在未知結構的 JSON 裡找出「有標籤 + 有金額」的記錄。

    刻意不假設固定路徑：ETF.com 改版時路徑會變，但「一個名稱配一個金額」
    這個形狀相對穩定。找不到時由呼叫端把結構傾印出來，人再據以修正。
    """
    if isinstance(node, dict):
        label = next(
            (str(node[k]) for k in ("name", "label", "segment", "category", "assetClass", "title")
             if k in node and isinstance(node[k], (str, int, float))),
            None,
        )
        # 鍵名大小寫不能寫死：實際回應用的是 netFlows 這種駝峰式，
        # 只比對小寫字串會一筆都找不到，卻不會拋錯——只是安靜地回報「沒有資料」
        flow = next(
            (_to_musd(v) for k, v in node.items()
             if k.lower().replace("_", "") in _FLOW_KEYS and _to_musd(v) is not None),
            None,
        )
        if label and flow is not None:
            found.append({"label": label, "flow_musd": flow, "path": path})
        for key, value in node.items():
            _walk_json(value, found, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node[:400]):     # 防止異常大的陣列拖垮解析
            _walk_json(value, found, f"{path}[{i}]")


def _extract_from_next_data(html: str) -> tuple[list[dict], str]:
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return [], "無 __NEXT_DATA__"
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        return [], f"__NEXT_DATA__ 不是合法 JSON: {e}"
    found: list[dict] = []
    _walk_json(payload.get("props", payload), found)
    return found, f"__NEXT_DATA__ 找到 {len(found)} 筆「標籤+金額」記錄"


def _extract_from_tables(html: str) -> tuple[list[dict], str]:
    try:
        tables = pd.read_html(io.StringIO(html))     # 必須用 StringIO，不可直接傳字串
    except Exception as e:  # noqa: BLE001
        return [], f"表格解析失敗: {type(e).__name__}: {e}"
    if not tables:
        return [], "頁面沒有表格"

    found: list[dict] = []
    for t in tables:
        cols = [str(c).lower() for c in t.columns]
        flow_idx = next((i for i, c in enumerate(cols)
                         if "flow" in c or "net" in c), None)
        if flow_idx is None:
            continue
        for _, row in t.iterrows():
            label = str(row.iloc[0])
            flow = _to_musd(row.iloc[flow_idx])
            if flow is not None and label:
                found.append({"label": label, "flow_musd": flow, "path": "table"})
    shapes = "；".join(f"{t.shape[0]}x{t.shape[1]}{[str(c)[:14] for c in list(t.columns)[:4]]}"
                      for t in tables[:4])
    return found, f"共{len(tables)}個表格（{shapes}），取出 {len(found)} 筆"


def _select_us_equity(records: list[dict]) -> Optional[dict]:
    """從候選記錄中挑出美國股票型的那一筆。

    優先「同時提到美國與股票」，其次「只提到股票」。挑不到就回 None，
    交由呼叫端連同候選標籤一起拋出——猜一個標籤等於猜一個數字。
    """
    scored = []
    for r in records:
        low = r["label"].lower()
        if not _looks_like_us_equity(low):
            continue
        score = 2 if any(h in low for h in _US_HINTS) else 1
        scored.append((score, r))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return scored[0][1]


def fetch_equity_etf_flow(timeout: int = 30, session=None, as_of: Optional[str] = None) -> FlowObservation:
    """取得美國股票型 ETF 的最新淨資金流（百萬美元，正值為淨流入）。"""
    http = session or requests
    diagnostics: list[str] = []

    for url in CANDIDATE_URLS:
        try:
            resp = http.get(url, headers=_HEADERS, timeout=timeout)
        except Exception as e:  # noqa: BLE001 — 換下一個位址再試
            diagnostics.append(f"{url}: 連線失敗 {type(e).__name__}: {e}")
            continue

        if resp.status_code != 200:
            diagnostics.append(
                f"{url}: HTTP {resp.status_code}"
                + ("（可能是 Cloudflare 或需登入）" if resp.status_code in (401, 403, 503) else "")
            )
            continue

        text = resp.text or ""
        records, how = _extract_from_next_data(text)
        if not records:
            table_records, table_how = _extract_from_tables(text)
            records, how = table_records, f"{how}；{table_how}"

        chosen = _select_us_equity(records)
        if chosen is None:
            labels = sorted({r["label"][:40] for r in records})[:12]
            diagnostics.append(
                f"{url}: 取得頁面（{len(text)} 字元）但找不到美國股票型記錄；"
                f"{how}｜候選標籤={labels}"
            )
            continue

        log.info("ETF.com 取得資金流：%s = %.1f 百萬美元（%s）",
                 chosen["label"], chosen["flow_musd"], url)
        return FlowObservation(
            as_of=as_of or date.today().isoformat(),
            net_flow_musd=chosen["flow_musd"],
            scope=chosen["label"],
            period="latest",
            source_url=url,
        )

    raise ValueError("ETF.com 無法取得股票型ETF資金流；已嘗試 —— " + "｜".join(diagnostics))
