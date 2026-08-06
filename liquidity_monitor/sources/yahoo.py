"""Yahoo Finance 抓取器（透過 yfinance）。

用於 VIX、VIX3M、SKEW、MOVE、NDX、DXY、USD/JPY 等每日行情，
以及 NASDAQ-100 成分股的批次歷史收盤價（供 ③ 廣度計算使用）。
"""
from __future__ import annotations

from typing import Iterable

import pandas as pd
import yfinance as yf


def fetch_yahoo_close(ticker: str, start: str, end: str) -> pd.Series:
    """抓取單一 ticker 的每日收盤價。"""
    df = yf.download(
        ticker, start=start, end=end, progress=False, auto_adjust=False, threads=False
    )
    if df.empty:
        raise ValueError(f"Yahoo Finance 找不到 '{ticker}' 的資料（可能代碼錯誤或暫時無回應）")
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close.index = pd.to_datetime(close.index).tz_localize(None)
    close.name = ticker
    return close.dropna()


def fetch_yahoo_close_bulk(tickers: Iterable[str], start: str, end: str) -> pd.DataFrame:
    """批次抓取多檔 ticker 的每日收盤價，回傳寬表（欄=ticker）。

    用於 NASDAQ-100 上百檔成分股的 200 日線廣度計算，比逐檔抓取快很多。
    個別 ticker 若抓取失敗會被靜默排除（下市/代碼變更等），不中斷整批。
    """
    tickers = list(tickers)
    df = yf.download(
        tickers, start=start, end=end, progress=False, auto_adjust=False,
        threads=True, group_by="ticker",
    )
    if df.empty:
        raise ValueError("Yahoo Finance 批次下載回傳空資料")
    out = {}
    for t in tickers:
        try:
            col = df[t]["Close"] if isinstance(df.columns, pd.MultiIndex) else df["Close"]
        except (KeyError, TypeError):
            continue
        col = col.dropna()
        if not col.empty:
            out[t] = col
    result = pd.DataFrame(out)
    result.index = pd.to_datetime(result.index).tz_localize(None)
    return result.sort_index()
