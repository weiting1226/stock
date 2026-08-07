"""每日收盤價與近一週／近一個月漲跌幅（批次抓取）。

用 yfinance 一次抓多檔的歷史收盤價，再以「交易日」為單位計算變化率
（近一週=5個交易日、近一個月=21個交易日），避免用日曆天在遇到連假時
把比較基準抓錯天。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional

import pandas as pd
import yfinance as yf

from ..config import MONTH_TRADING_DAYS, PRICE_HISTORY_PERIOD, WEEK_TRADING_DAYS

log = logging.getLogger(__name__)

CHUNK_SIZE = 100


@dataclass
class PriceSnapshot:
    ticker: str
    close: Optional[float] = None
    close_date: Optional[str] = None
    change_1w_pct: Optional[float] = None
    change_1m_pct: Optional[float] = None
    error: Optional[str] = None


def _pct_change_n_sessions(series: pd.Series, n: int) -> Optional[float]:
    s = series.dropna()
    if len(s) <= n:
        return None
    latest, prior = float(s.iloc[-1]), float(s.iloc[-1 - n])
    if prior <= 0:
        return None
    return round((latest / prior - 1) * 100, 2)


def _extract_close_frame(raw: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """把 yf.download 的回傳整理成「欄=ticker、值=收盤價」的寬表。"""
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        out = {}
        for t in tickers:
            try:
                col = raw[t]["Close"]
            except (KeyError, TypeError):
                continue
            col = col.dropna()
            if not col.empty:
                out[t] = col
        return pd.DataFrame(out)
    # 單一 ticker 時 yfinance 回傳單層欄位
    if "Close" in raw.columns:
        col = raw["Close"].dropna()
        return pd.DataFrame({tickers[0]: col}) if not col.empty else pd.DataFrame()
    return pd.DataFrame()


def fetch_price_snapshots(
    tickers: Iterable[str], period: str = PRICE_HISTORY_PERIOD
) -> dict[str, PriceSnapshot]:
    """批次抓取收盤價，回傳 {ticker: PriceSnapshot}。抓不到的標的仍會有一筆帶 error 的紀錄。"""
    tickers = [t for t in dict.fromkeys(tickers) if t]
    snapshots: dict[str, PriceSnapshot] = {t: PriceSnapshot(ticker=t) for t in tickers}

    for i in range(0, len(tickers), CHUNK_SIZE):
        chunk = tickers[i:i + CHUNK_SIZE]
        try:
            raw = yf.download(
                chunk, period=period, progress=False, auto_adjust=False,
                threads=True, group_by="ticker",
            )
        except Exception as e:  # noqa: BLE001 — 一個批次失敗不影響其他批次
            log.warning("價格批次下載失敗 (%s…): %s", chunk[0], e)
            for t in chunk:
                snapshots[t].error = f"{type(e).__name__}: {e}"
            continue

        closes = _extract_close_frame(raw, chunk)
        for t in chunk:
            snap = snapshots[t]
            if t not in closes.columns:
                snap.error = "無收盤價資料"
                continue
            series = closes[t].dropna()
            if series.empty:
                snap.error = "無收盤價資料"
                continue
            snap.close = round(float(series.iloc[-1]), 4)
            snap.close_date = str(pd.Timestamp(series.index[-1]).date())
            snap.change_1w_pct = _pct_change_n_sessions(series, WEEK_TRADING_DAYS)
            snap.change_1m_pct = _pct_change_n_sessions(series, MONTH_TRADING_DAYS)

    return snapshots
