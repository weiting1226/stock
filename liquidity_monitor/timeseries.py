"""時間序列工具：發布時滯位移、對齊每個交易日、年增率/區間變化計算。

這些都是純函式，不做網路存取，方便單獨測試。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
import pandas_market_calendars as mcal


def nyse_trading_days(start: str, end: str) -> pd.DatetimeIndex:
    nyse = mcal.get_calendar("NYSE")
    schedule = nyse.schedule(start_date=start, end_date=end)
    idx = pd.DatetimeIndex(schedule.index).tz_localize(None).normalize()
    return idx


def apply_publish_lag(series: pd.Series, lag_days: int) -> pd.Series:
    """把序列索引往後平移 `lag_days` 天，模擬「此數值直到 D+lag 天才公開可得」。

    對齊文件第38-48行的資料時滯規則：之後用 `to_daily_panel` 做前向填補時，
    任何一天只會看到「已經公開」的最新值，不會有前視偏誤。
    """
    if lag_days <= 0:
        return series
    shifted = series.copy()
    shifted.index = shifted.index + pd.Timedelta(days=lag_days)
    return shifted


def to_daily_panel(series: pd.Series, trading_days: pd.DatetimeIndex) -> pd.Series:
    """把（已套用時滯的）序列前向填補對齊到每個交易日。"""
    s = series.sort_index()
    return s.reindex(trading_days.union(s.index)).ffill().reindex(trading_days)


def yoy(series: pd.Series) -> pd.Series:
    """以「日期回推最接近365天前」的方式計算年增率(%)，適用於月/週頻序列。"""
    s = series.sort_index().dropna()
    out = {}
    for dt, val in s.items():
        target = dt - pd.Timedelta(days=365)
        prior = s.loc[:target]
        if prior.empty or val == 0:
            continue
        base = prior.iloc[-1]
        if base == 0 or pd.isna(base):
            continue
        out[dt] = (val / base - 1) * 100
    return pd.Series(out, name=f"{series.name}_yoy")


def n_trading_day_change(daily_series: pd.Series, n: int, pct: bool = False) -> Optional[float]:
    """已對齊每日交易日的序列，回傳最新值相對於 n 個交易日前的變化。
    `pct=True` 回傳百分比變化，否則回傳絕對差（原始單位）。
    """
    s = daily_series.dropna()
    if len(s) <= n:
        return None
    latest = s.iloc[-1]
    prior = s.iloc[-1 - n]
    if pd.isna(latest) or pd.isna(prior):
        return None
    if pct:
        if prior == 0:
            return None
        return float((latest / prior - 1) * 100)
    return float(latest - prior)


def latest_value(daily_series: pd.Series) -> Optional[float]:
    s = daily_series.dropna()
    if s.empty:
        return None
    return float(s.iloc[-1])


def is_trailing_max(daily_series: pd.Series, window_days: int) -> bool:
    s = daily_series.dropna().tail(window_days)
    if s.empty:
        return False
    return bool(s.iloc[-1] >= s.max())
