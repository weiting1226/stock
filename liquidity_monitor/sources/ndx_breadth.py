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

WIKI_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
_HEADERS = {"User-Agent": "Mozilla/5.0 (liquidity-monitor-v3 scraper)"}

_TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
MIN_EXPECTED_CONSTITUENTS = 50  # NDX 約100檔；低於此數視為抓到錯的表格


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

    for t in tables:
        for col in t.columns:
            values = [str(v).strip() for v in t[col].dropna()]
            values = [v for v in values if v and v.lower() != "nan"]
            if len(values) < MIN_EXPECTED_CONSTITUENTS:
                continue

            named = any(k in str(col).lower() for k in ("ticker", "symbol"))
            ratio = _column_ticker_ratio(values)
            # 標題明確的欄位放寬門檻，靠內容判斷的欄位則要求幾乎全部像代號
            if (named and ratio > 0.5) or ratio > 0.9:
                if ratio > best[0]:
                    best = (ratio, values)

    if not best[1]:
        raise ValueError(
            "無法從維基百科 Nasdaq-100 頁面找到成分股代號欄位（已同時嘗試欄位名稱與內容比對）；"
            f"共解析到 {len(tables)} 個表格，網站結構可能已變更。"
        )

    # 維基百科用 BRK.B，Yahoo Finance 用 BRK-B
    return sorted({v.replace(".", "-") for v in best[1]})


def fetch_constituents(cache_path: Optional[str] = None, max_cache_age_days: int = 30) -> list[str]:
    """回傳 NASDAQ-100 成分股 ticker 清單，優先用未過期的本地快取。"""
    cache = Path(cache_path) if cache_path else None
    if cache and cache.exists():
        age_days = (pd.Timestamp.utcnow().tz_localize(None) - pd.Timestamp(cache.stat().st_mtime, unit="s")).days
        if age_days < max_cache_age_days:
            return json.loads(cache.read_text())["tickers"]

    resp = requests.get(WIKI_URL, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))  # pandas>=2.1 不再接受純字串
    tickers = _extract_tickers(tables)

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
