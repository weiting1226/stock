"""資料抓取。**規則 3 就擋在這裡。**

接線說明第 3 節：`hyg` / `ief` / `lqd` 必須是除息調整價。HYG 年配息約 6~7%，
用未調整價會讓合成指數每年假性下跌數個百分點，慢閘會長期誤報。

這個 repo 既有的 `sources/yahoo.fetch_yahoo_close()` 用的是
`auto_adjust=False` 並取 `Close`——那是**未調整**收盤價。直接沿用它會
一路過關、不拋任何例外，只是慢閘從此長期偏紅。所以這裡另外寫一個
明確取調整價的函式，而不是重用那一個。

「重用既有函式」在這裡是錯的：兩者名字像、型別一樣、都能跑，
差別只在一個布林參數，而那個參數決定這個模組是不是在說謊。
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)


def fetch_adjusted_close(ticker: str, start: str, end: str) -> pd.Series:
    """抓取**除息調整後**的每日收盤價。

    `auto_adjust=True` 讓 yfinance 直接回傳調整後的 Close。不用
    `Adj Close` 欄位是因為該欄位在 auto_adjust=True 時不存在，
    而在 False 時與 Close 並存——兩種模式取錯欄位都不會報錯。
    """
    df = yf.download(ticker, start=start, end=end, progress=False,
                     auto_adjust=True, threads=False)
    if df is None or df.empty:
        raise ValueError(f"Yahoo Finance 找不到 '{ticker}' 的資料")
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close.index = pd.to_datetime(close.index).tz_localize(None)
    close.name = ticker
    s = close.dropna()
    if s.empty:
        raise ValueError(f"'{ticker}' 調整後收盤價全為空")
    return s


def fetch_many_adjusted(tickers: dict, start: str, end: str) -> tuple[dict, list]:
    """逐檔抓取，回傳 ({key: Series}, [失敗說明])。

    單一檔失敗不中斷其餘：VVIX 抓不到時，期限結構那一項仍然可以判定。
    但失敗要留下說明——缺資料與「沒有訊號」在畫面上必須分得出來。
    """
    out, problems = {}, []
    for key, ticker in tickers.items():
        try:
            out[key] = fetch_adjusted_close(ticker, start, end)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{key}（{ticker}）：{type(e).__name__}: {e}")
            log.warning("模組六抓取 %s 失敗：%s", ticker, e)
    return out, problems


def realized_vol(close: pd.Series, window: int = 20) -> Optional[float]:
    """年化已實現波動。供擁擠度使用。"""
    r = close.astype(float).pct_change().dropna()
    if len(r) < window:
        return None
    return float(r.iloc[-window:].std() * (252 ** 0.5))


def trailing_return(close: pd.Series, window: int = 250) -> Optional[float]:
    s = close.dropna()
    if len(s) <= window:
        return None
    return float(s.iloc[-1] / s.iloc[-window - 1] - 1.0)


def percentile_of_latest(series: pd.Series, window: int = 250) -> Optional[float]:
    s = series.dropna().iloc[-window:]
    if len(s) < 20:
        return None
    return float((s <= s.iloc[-1]).mean() * 100.0)


def change_bp(series: pd.Series, days: int = 20) -> Optional[float]:
    """N 日變化，單位 bp。HY OAS 在 FRED 是百分點，乘 100 換算。"""
    s = series.dropna()
    if len(s) <= days:
        return None
    return float((s.iloc[-1] - s.iloc[-days - 1]) * 100.0)
