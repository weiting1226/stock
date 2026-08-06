"""FRED (Federal Reserve Economic Data) 抓取器。

使用免金鑰的公開 CSV 端點（`fred.stlouisfed.org/graph/fredgraph.csv`），
即每個 FRED 系列頁面「Download」按鈕背後的相同端點，不需申請 API key。
"""
from __future__ import annotations

import io

import pandas as pd
import requests

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0 (liquidity-monitor-v3 scraper)"})


def fetch_fred_series(series_id: str, start: str, end: str, timeout: int = 30) -> pd.Series:
    """抓取單一 FRED 系列，回傳以日期為索引的 Series（已去除缺值列）。"""
    params = {"id": series_id, "cosd": start, "coed": end}
    resp = _SESSION.get(FRED_CSV_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    text = resp.text
    if text.strip().startswith("<") or "series does not exist" in text.lower():
        raise ValueError(f"FRED 系列 '{series_id}' 無法解析（代碼錯誤或已下架）")
    df = pd.read_csv(io.StringIO(text))
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    s = df.dropna().set_index("date")["value"].sort_index()
    s.name = series_id
    return s
