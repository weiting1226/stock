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
import re
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from . import yahoo

from ..config import NDX_CONSTITUENTS_WIKI_URL, QQQ_HOLDINGS_URL

WIKI_URL = NDX_CONSTITUENTS_WIKI_URL
_HEADERS = {"User-Agent": "Mozilla/5.0 (liquidity-monitor-v3 scraper)"}

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
        raise ValueError(
            "無法從維基百科 Nasdaq-100 頁面找到成分股代號欄位（已同時嘗試欄位名稱與內容比對）；"
            f"共解析到 {len(tables)} 個表格。候選欄位："
            + ("；".join(diagnostics[:6]) if diagnostics
               else f"沒有任何欄位達到 {MIN_EXPECTED_CONSTITUENTS} 列以上")
        )

    # 維基百科用 BRK.B，Yahoo Finance 用 BRK-B
    return sorted({v.replace(".", "-") for v in best[1]})


def fetch_qqq_holdings(timeout: int = 30) -> list[str]:
    """從 Invesco 公開的 QQQ 持股 CSV 取得 NASDAQ-100 成分股。

    QQQ 完整複製 NASDAQ-100，持股清單即成分股清單，由發行商每日更新，
    比維基百科條目穩定得多（2026-08 維基百科該頁已解析不出成分股表格）。
    """
    resp = requests.get(QQQ_HOLDINGS_URL, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
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
        raise ValueError(
            f"QQQ 持股 CSV 只解析出 {len(tickers)} 檔（預期約100檔），格式可能已變更"
        )
    return tickers


def fetch_constituents(cache_path: Optional[str] = None, max_cache_age_days: int = 30) -> list[str]:
    """回傳 NASDAQ-100 成分股 ticker 清單。

    順序：未過期的本地快取 → Invesco QQQ 持股 CSV（主）→ 維基百科（備援）。
    兩個來源都失敗才拋錯，且錯誤訊息同時帶出兩邊的失敗原因。
    """
    cache = Path(cache_path) if cache_path else None
    if cache and cache.exists():
        age_days = (pd.Timestamp.utcnow().tz_localize(None) - pd.Timestamp(cache.stat().st_mtime, unit="s")).days
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
