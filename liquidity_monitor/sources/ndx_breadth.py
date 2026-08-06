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

import json
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from . import yahoo

WIKI_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
_HEADERS = {"User-Agent": "Mozilla/5.0 (liquidity-monitor-v3 scraper)"}


def fetch_constituents(cache_path: Optional[str] = None, max_cache_age_days: int = 30) -> list[str]:
    """回傳 NASDAQ-100 成分股 ticker 清單，優先用未過期的本地快取。"""
    cache = Path(cache_path) if cache_path else None
    if cache and cache.exists():
        age_days = (pd.Timestamp.utcnow().tz_localize(None) - pd.Timestamp(cache.stat().st_mtime, unit="s")).days
        if age_days < max_cache_age_days:
            return json.loads(cache.read_text())["tickers"]

    resp = requests.get(WIKI_URL, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(resp.text)
    ticker_table = None
    for t in tables:
        cols = [str(c).strip().lower() for c in t.columns]
        if any("ticker" in c for c in cols) or any("symbol" in c for c in cols):
            ticker_table = t
            break
    if ticker_table is None:
        raise ValueError("無法從維基百科 Nasdaq-100 頁面找到成分股表格，網站結構可能已變更")

    col = next(c for c in ticker_table.columns if "ticker" in str(c).lower() or "symbol" in str(c).lower())
    tickers = sorted({str(x).strip().replace(".", "-") for x in ticker_table[col].dropna()})
    tickers = [t for t in tickers if t and t.lower() != "nan"]

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
