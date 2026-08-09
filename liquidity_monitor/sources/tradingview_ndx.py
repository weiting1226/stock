"""從 TradingView 取得 NASDAQ-100 成分股與市場廣度指標。

為什麼用 TradingView：它的 screener 端點一次就把**成分股清單和每檔的均線、
RSI、漲跌幅、市值**一起回傳。原本的做法是「先取得清單，再用 yfinance 逐檔
下載 220 個交易日的收盤價自行算 200 日均線」——約 100 次下載換一個百分比，
而且清單來源（維基百科、Invesco）先後失效過。這裡一個請求就取代那整段。

代價是這個端點沒有官方文件，請求格式可能變動。因此：
- 依序嘗試幾種已知的查詢格式，**每一種都記下實際結果**（狀態碼、回傳筆數、
  內容前綴），全部失敗時把這些證據一起拋出。盲修改再重跑一次要好幾分鐘，
  訊息裡沒有證據就只能猜。
- 解析失敗一律拋錯，由呼叫端標記「暫缺」，絕不臆測填補。

均線由 TradingView 計算（欄位 SMA20/SMA50/SMA200），這正是採用它的目的。
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from typing import Optional

import requests

log = logging.getLogger(__name__)

SCANNER_URL = "https://scanner.tradingview.com/america/scan"

# TradingView screener 的欄位代號。順序即回傳陣列 d[] 的順序。
COLUMNS = [
    "name",              # 代號，如 AAPL
    "description",       # 公司名稱
    "close",
    "change",            # 當日漲跌幅 %
    "SMA20",
    "SMA50",
    "SMA200",
    "RSI",               # RSI(14)
    "market_cap_basic",
    "price_52_week_high",
    "price_52_week_low",
]

_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (liquidity-monitor-v3)",
    "Accept": "application/json",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
}

# NDX 為 100 檔（偶有 101 檔，因同一公司雙級別股票）。低於此數代表抓到的
# 不是成分股全集，寧可報錯也不要用殘缺清單算出一個看似正常的百分比。
MIN_EXPECTED_CONSTITUENTS = 50


def _payload_symbolset() -> dict:
    """目前 TradingView screener 取指數成分股的方式。"""
    return {
        "symbols": {"symbolset": ["SYML:NASDAQ;NDX"]},
        "columns": COLUMNS,
        "range": [0, 200],
        "options": {"lang": "en"},
    }


def _payload_index_filter() -> dict:
    """舊版格式：用 filter 指定所屬指數。"""
    return {
        "filter": [{"left": "index", "operation": "equal", "right": "NASDAQ:NDX"}],
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": COLUMNS,
        "range": [0, 200],
        "options": {"lang": "en"},
    }


_ATTEMPTS = (
    ("symbolset", _payload_symbolset),
    ("index-filter", _payload_index_filter),
)


@dataclass
class Component:
    symbol: str
    name: Optional[str] = None
    close: Optional[float] = None
    change_pct: Optional[float] = None
    sma20: Optional[float] = None
    sma50: Optional[float] = None
    sma200: Optional[float] = None
    rsi: Optional[float] = None
    market_cap: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None


def _as_float(value) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f      # NaN


def _parse_rows(data: list) -> list[Component]:
    """把 {"s": "NASDAQ:AAPL", "d": [...]} 轉成 Component。"""
    index = {name: i for i, name in enumerate(COLUMNS)}
    out: list[Component] = []
    for row in data:
        values = row.get("d") or []
        if len(values) != len(COLUMNS):
            continue

        def get(field):
            return values[index[field]]

        # 優先用 name 欄；缺了就從 "NASDAQ:AAPL" 這種完整代號取後半
        symbol = str(get("name") or "").strip() or str(row.get("s") or "").split(":")[-1].strip()
        if not symbol:
            continue
        out.append(Component(
            symbol=symbol.replace(".", "-"),   # TradingView 用 BRK.B，Yahoo 用 BRK-B
            name=(str(get("description")).strip() or None) if get("description") else None,
            close=_as_float(get("close")),
            change_pct=_as_float(get("change")),
            sma20=_as_float(get("SMA20")),
            sma50=_as_float(get("SMA50")),
            sma200=_as_float(get("SMA200")),
            rsi=_as_float(get("RSI")),
            market_cap=_as_float(get("market_cap_basic")),
            high_52w=_as_float(get("price_52_week_high")),
            low_52w=_as_float(get("price_52_week_low")),
        ))
    return out


def fetch_ndx_components(timeout: int = 30, session=None) -> list[Component]:
    """取得 NDX 成分股與各項技術數據。全部查詢格式都失敗時拋出並附上證據。"""
    http = session or requests
    diagnostics: list[str] = []

    for label, build in _ATTEMPTS:
        try:
            resp = http.post(SCANNER_URL, json=build(), headers=_HEADERS, timeout=timeout)
        except Exception as e:  # noqa: BLE001 — 換下一種格式再試
            diagnostics.append(f"{label}: 連線失敗 {type(e).__name__}: {e}")
            continue

        if resp.status_code != 200:
            body = (resp.text or "")[:160].replace("\n", " ")
            diagnostics.append(f"{label}: HTTP {resp.status_code}，內容前綴 {body!r}")
            continue

        try:
            payload = resp.json()
        except ValueError:
            body = (resp.text or "")[:160].replace("\n", " ")
            diagnostics.append(f"{label}: 回應不是 JSON，內容前綴 {body!r}")
            continue

        data = payload.get("data")
        if not isinstance(data, list):
            diagnostics.append(
                f"{label}: 回應缺少 data 陣列，實際鍵={list(payload)[:6]}"
            )
            continue

        components = _parse_rows(data)
        if len(components) < MIN_EXPECTED_CONSTITUENTS:
            sample = data[0] if data else None
            diagnostics.append(
                f"{label}: 只解析出 {len(components)} 檔（totalCount="
                f"{payload.get('totalCount')}、data {len(data)} 列）；首列={str(sample)[:160]}"
            )
            continue

        log.info("TradingView 取得 NDX 成分股 %d 檔（查詢格式：%s）", len(components), label)
        return components

    raise ValueError(
        "TradingView 無法取得 NDX 成分股；已嘗試的查詢格式與結果 —— "
        + "｜".join(diagnostics)
    )


def _pct_above(components: list[Component], attr: str) -> tuple[Optional[float], int]:
    """站上某條均線的比例，以及計算所依據的樣本數。

    回傳樣本數是必要的：若有數檔缺均線資料，分母就不是 100。不講清楚的話，
    「62%」看起來一樣正常，但可能是 62/98 而不是 62/100。
    """
    usable = [
        c for c in components
        if c.close is not None and getattr(c, attr) is not None and getattr(c, attr) > 0
    ]
    if not usable:
        return None, 0
    above = sum(1 for c in usable if c.close > getattr(c, attr))
    return round(100 * above / len(usable), 2), len(usable)


def compute_breadth(components: list[Component]) -> dict:
    """由成分股資料算出市場廣度指標。

    ③ 只需要「站上 200 日線比例」，但同一份資料本來就含 20／50 日線與 RSI，
    一起算出來可以看出廣度是短線鬆動還是中期轉弱——單看 200 日線看不出來。
    """
    pct200, n200 = _pct_above(components, "sma200")
    pct50, n50 = _pct_above(components, "sma50")
    pct20, n20 = _pct_above(components, "sma20")

    changes = [c.change_pct for c in components if c.change_pct is not None]
    advancers = sum(1 for v in changes if v > 0)
    decliners = sum(1 for v in changes if v < 0)

    rsis = [c.rsi for c in components if c.rsi is not None]
    caps = sorted((c.market_cap for c in components if c.market_cap), reverse=True)

    # 距 52 週高點 10% 以內視為接近新高，跌破 52 週低點 10% 以內視為接近新低
    near_high = sum(1 for c in components
                    if c.close and c.high_52w and c.close >= c.high_52w * 0.9)
    near_low = sum(1 for c in components
                   if c.close and c.low_52w and c.close <= c.low_52w * 1.1)

    return {
        "constituents": len(components),
        "pct_above_200dma": pct200,
        "pct_above_200dma_sample": n200,
        "pct_above_50dma": pct50,
        "pct_above_50dma_sample": n50,
        "pct_above_20dma": pct20,
        "pct_above_20dma_sample": n20,
        "advancers": advancers,
        "decliners": decliners,
        # 全數上漲時比值無意義（分母為零），用 None 表示而不是硬給一個大數字
        "advance_decline_ratio": round(advancers / decliners, 2) if decliners else None,
        "median_rsi": round(statistics.median(rsis), 2) if rsis else None,
        "overbought_rsi70": sum(1 for v in rsis if v >= 70),
        "oversold_rsi30": sum(1 for v in rsis if v <= 30),
        "near_52w_high": near_high,
        "near_52w_low": near_low,
        # NDX 極度集中於少數幾檔，前十大占比是判讀「廣度」不可少的背景
        "top10_market_cap_pct": (
            round(100 * sum(caps[:10]) / sum(caps), 2) if caps else None
        ),
    }


def fetch_ndx_breadth(timeout: int = 30, session=None) -> tuple[dict, list[Component]]:
    components = fetch_ndx_components(timeout=timeout, session=session)
    return compute_breadth(components), components
