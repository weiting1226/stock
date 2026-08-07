"""Yahoo Finance 分析師目標價（免金鑰）。

`yfinance.Ticker.analyst_price_targets` 取自 Yahoo quoteSummary 的
`financialData` 模組，回傳 {'low','high','mean','median','current'}；
分析師家數與推薦評級另從 `Ticker.info` 的 numberOfAnalystOpinions /
recommendationMean 取得。

抓不到就回傳 None，由呼叫端標記「暫缺」，不得以其他數字頂替。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import yfinance as yf

log = logging.getLogger(__name__)


@dataclass
class TargetQuote:
    ticker: str
    source: str
    mean: Optional[float] = None
    median: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    analyst_count: Optional[int] = None
    recommendation_mean: Optional[float] = None   # 1=Strong Buy … 5=Sell
    recommendation_key: Optional[str] = None
    currency: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.mean is not None and self.mean > 0


def _as_float(value) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f <= 0:  # NaN 或非正值一律視為無效
        return None
    return f


def _as_int(value) -> Optional[int]:
    try:
        i = int(value)
    except (TypeError, ValueError):
        return None
    return i if i > 0 else None


def fetch_yahoo_target(ticker: str) -> TargetQuote:
    """抓取單一標的的 Yahoo 共識目標價。任何例外都收斂成 error 欄位。"""
    quote = TargetQuote(ticker=ticker, source="yahoo")
    try:
        t = yf.Ticker(ticker)
        targets = t.analyst_price_targets or {}
        quote.mean = _as_float(targets.get("mean"))
        quote.median = _as_float(targets.get("median"))
        quote.high = _as_float(targets.get("high"))
        quote.low = _as_float(targets.get("low"))

        info = t.info or {}
        quote.analyst_count = _as_int(info.get("numberOfAnalystOpinions"))
        quote.recommendation_mean = _as_float(info.get("recommendationMean"))
        key = info.get("recommendationKey")
        quote.recommendation_key = str(key) if key else None
        quote.currency = info.get("currency")
    except Exception as e:  # noqa: BLE001 — 單一標的失敗不能中斷整批
        quote.error = f"{type(e).__name__}: {e}"
        log.debug("Yahoo 目標價抓取失敗 %s: %s", ticker, e)
    return quote
