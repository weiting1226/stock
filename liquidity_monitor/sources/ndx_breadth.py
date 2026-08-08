"""③ NDX 成分股站上 200 日線比例（市場廣度）。

流程：
1. 從維基百科「Nasdaq-100」頁面抓取目前成分股清單（附本地快取，
   避免每天重新解析；成分股每季調整一次即可）。
2. 用 yfinance 批次抓取每檔成分股近 220 個交易日的收盤價。
3. 計算「收盤價 > 200 日均線」的成分股占比。

若清單抓取失敗，回傳 None 並由呼叫端標記該指標「暫缺」，不得用舊快取
以外的資料臆測填補。
"""
from __future__ import annotations

import io
import json
import logging
import re
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from . import yahoo

from ..config import NDX_CONSTITUENTS_WIKI_URL, QQQ_HOLDINGS_URL

WIKI_URL = NDX_CONSTITUENTS_WIKI_URL
_HEADERS = {"User-Agent": "Mozilla/5.0 (liquidity-monitor-v3 scraper)"}

log = logging.getLogger(__name__)

_TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
_FOOTNOTE_RE = re.compile(r"\[[^\]]*\]")
MIN_EXPECTED_CONSTITUENTS = 50  # NDX 約100檔；低於此數視為抓到錯的表格


def _clean_symbol(value) -> str:
    """去掉維基百科註腳與交易所前綴：'AAPL[a]' / 'NASDAQ: AAPL' -> 'AAPL'。"""
    s = _FOOTNOTE_RE.sub("", str(value)).strip()
    if ":" in s:
        s = s.split(":")[-1].strip()
    return s


def _iter_columns(t: pd.DataFrame):
    """以「位置」逐欄取值，回傳 (欄位標籤, Series)。

    不能用 t[col]：維基百科表格常有重複欄名，此時 t[col] 回傳的是 DataFrame，
    對它做迭代拿到的是欄名而不是儲存格內容，整欄就被誤判成只有兩三筆資料。
    """
    for i in range(t.shape[1]):
        series = t.iloc[:, i]
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        label = t.columns[i]
        if isinstance(label, tuple):
            label = " ".join(str(x) for x in label)
        yield str(label), series


def _column_ticker_ratio(values: list[str]) -> float:
    if not values:
        return 0.0
    return sum(1 for v in values if _TICKER_RE.match(v)) / len(values)


def _extract_tickers(tables: list[pd.DataFrame]) -> list[str]:
    """從維基百科的多個表格中找出成分股代號欄。

    先看欄位名稱是否含 ticker/symbol；找不到就改用「欄位內容是否長得像股票
    代號」的啟發式判斷。維基百科改過欄位標題就整個抓不到（2026-08 實際發生過），
    因此不能只依賴標題文字。
    """
    best: tuple[float, list[str]] = (0.0, [])
    diagnostics: list[str] = []

    for t in tables:
        for label, series in _iter_columns(t):
            values = [_clean_symbol(v) for v in series.dropna()]
            values = [v for v in values if v and v.lower() != "nan"]
            if len(values) < MIN_EXPECTED_CONSTITUENTS:
                continue

            ratio = _column_ticker_ratio(values)
            named = any(k in label.lower() for k in ("ticker", "symbol"))
            diagnostics.append(f"{label!r}({len(values)}列, 像代號{ratio:.0%}, 例{values[:2]})")

            # 標題明確的欄位放寬門檻，靠內容判斷的欄位則要求幾乎全部像代號
            if ((named and ratio > 0.5) or ratio > 0.9) and ratio > best[0]:
                best = (ratio, [v for v in values if _TICKER_RE.match(v)])

    if not best[1]:
        # 印出最大的幾個表格的形狀與欄位名稱，才能判斷成分股表到底在不在、長什麼樣，
        # 否則只知道「找不到」而無從修起。
        shapes = sorted(
            ((t.shape[0], t.shape[1], [str(c)[:16] for c in list(t.columns)[:4]]) for t in tables),
            key=lambda x: -x[0],
        )[:5]
        detail = "；".join(f"{r}列x{c}欄{cols}" for r, c, cols in shapes)
        raise ValueError(
            "無法從維基百科 Nasdaq-100 頁面找到成分股代號欄位（已同時嘗試欄位名稱與內容比對）；"
            f"共{len(tables)}個表格，最大的幾個：{detail}"
            + (f"｜通過列數門檻的欄位：{'；'.join(diagnostics[:3])}" if diagnostics else "")
        )

    # 維基百科用 BRK.B，Yahoo Finance 用 BRK-B
    return sorted({v.replace(".", "-") for v in best[1]})


def _read_holdings_csv(text: str) -> pd.DataFrame:
    """解析持股 CSV，跳過標題列之前的前言列。

    Invesco 的下載檔在真正的欄位標題之前有數行說明文字（欄數不一致），
    直接 pd.read_csv 會拋 ParserError: Expected 1 fields ... saw 13。
    因此先找出真正的標題列（含 ticker 字樣、且有多個逗號），再從該列開始解析。
    """
    stripped = text.lstrip()
    if stripped.startswith("<") or "<html" in stripped[:2000].lower():
        # Invesco 的下載網址會被導向 React 產品頁，回傳 HTML 而不是檔案。
        # 不擋下來的話 read_csv 會把 HTML 當成上千列資料，錯誤訊息完全誤導。
        raise ValueError("持股下載網址回傳 HTML 網頁而非 CSV（網址可能已失效或被導向產品頁）")

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("持股 CSV 內容為空")

    # 含 ticker 字樣的候選列中取「逗號最多」的那列：前言若剛好也提到 ticker，
    # 欄位數也不會比真正的標題列多。
    candidates = [(ln.count(","), i) for i, ln in enumerate(lines)
                  if "ticker" in ln.lower() and "," in ln]
    header_idx = max(candidates)[1] if candidates else None
    if header_idx is None:  # 沒有 ticker 字樣時退回「第一個欄位數夠多的列」
        header_idx = next((i for i, ln in enumerate(lines) if ln.count(",") >= 4), None)
    if header_idx is None:
        raise ValueError(f"持股 CSV 找不到標題列；前3行內容：{lines[:3]}")

    return pd.read_csv(io.StringIO("\n".join(lines[header_idx:])))


def fetch_qqq_holdings(timeout: int = 30) -> list[str]:
    """從 Invesco 公開的 QQQ 持股 CSV 取得 NASDAQ-100 成分股。

    QQQ 完整複製 NASDAQ-100，持股清單即成分股清單，由發行商每日更新，
    比維基百科條目穩定得多（2026-08 維基百科該頁已解析不出成分股表格）。
    """
    resp = requests.get(QQQ_HOLDINGS_URL, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    df = _read_holdings_csv(resp.text)
    df.columns = [str(c).strip() for c in df.columns]

    col = next(
        (c for c in df.columns if "holding ticker" in c.lower()),
        next((c for c in df.columns if "ticker" in c.lower() and "fund" not in c.lower()), None),
    )
    if col is None:
        raise ValueError(f"QQQ 持股 CSV 找不到持股代號欄位；實際欄位：{list(df.columns)[:8]}")

    tickers = sorted({
        s.replace(".", "-")
        for s in (_clean_symbol(v) for v in df[col].dropna())
        if _TICKER_RE.match(s)
    })
    if len(tickers) < MIN_EXPECTED_CONSTITUENTS:
        # 帶出足以直接定位問題的證據：選到哪個欄位、幾列資料、有哪些欄位、值長什麼樣
        sample = [repr(str(v)) for v in df[col].dropna().head(3)]
        raise ValueError(
            f"QQQ 持股 CSV 只解析出 {len(tickers)} 檔（預期約100檔）；"
            f"採用欄位={col!r}、資料{df.shape[0]}列、"
            f"欄位={list(df.columns)[:7]}、樣本={sample}"
        )
    return tickers


def fetch_constituents(cache_path: Optional[str] = None, max_cache_age_days: int = 30) -> list[str]:
    """回傳 NASDAQ-100 成分股 ticker 清單。

    順序：未過期的本地快取 → Invesco QQQ 持股 CSV（主）→ 維基百科（備援）。
    兩個來源都失敗才拋錯，且錯誤訊息同時帶出兩邊的失敗原因。
    """
    cache = Path(cache_path) if cache_path else None
    if cache and cache.exists():
        # pd.Timestamp.utcnow() 已被 pandas 標記為 deprecated
        now = pd.Timestamp.now(tz="UTC").tz_localize(None)
        age_days = (now - pd.Timestamp(cache.stat().st_mtime, unit="s")).days
        if age_days < max_cache_age_days:
            return json.loads(cache.read_text())["tickers"]

    errors: list[str] = []
    try:
        tickers = fetch_qqq_holdings()
    except Exception as e:  # noqa: BLE001 — 主來源失敗要能退回備援
        errors.append(f"QQQ持股CSV: {type(e).__name__}: {e}")
        try:
            resp = requests.get(WIKI_URL, headers=_HEADERS, timeout=30)
            resp.raise_for_status()
            tickers = _extract_tickers(pd.read_html(io.StringIO(resp.text)))
        except Exception as e2:  # noqa: BLE001
            errors.append(f"維基百科: {type(e2).__name__}: {e2}")
            # 最後手段：用過期的快取。成分股每季才調整一次，一份舊清單遠比
            # 完全沒有資料有用；而且這是先前真實抓到（或使用者手動維護）的清單，
            # 不是臆測出來的內容。
            if cache and cache.exists():
                try:
                    cached = json.loads(cache.read_text())
                    if cached.get("tickers"):
                        log.warning(
                            "成分股即時來源皆失敗，改用 %s 的快取清單（%d檔）：%s",
                            cached.get("as_of", "未知日期"), len(cached["tickers"]), " | ".join(errors),
                        )
                        return cached["tickers"]
                except (ValueError, KeyError):
                    pass
            raise ValueError("NASDAQ-100 成分股所有來源皆失敗 -> " + " | ".join(errors)) from e2

    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"tickers": tickers, "as_of": str(pd.Timestamp.today().date())}, ensure_ascii=False, indent=2))
    return tickers


def compute_breadth_200d(tickers: list[str], as_of: str) -> Optional[float]:
    """回傳「當日收盤價 > 200日均線」的成分股占比（0-100）。"""
    end = pd.Timestamp(as_of) + pd.Timedelta(days=1)
    start = pd.Timestamp(as_of) - pd.Timedelta(days=320)
    prices = yahoo.fetch_yahoo_close_bulk(tickers, str(start.date()), str(end.date()))
    if prices.empty or len(prices) < 200:
        return None
    ma200 = prices.rolling(200, min_periods=150).mean()
    latest_price = prices.iloc[-1]
    latest_ma = ma200.iloc[-1]
    valid = latest_price.notna() & latest_ma.notna()
    if valid.sum() == 0:
        return None
    above = (latest_price[valid] > latest_ma[valid]).sum()
    return round(100.0 * above / valid.sum(), 2)
