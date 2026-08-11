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

from ..config import YAHOO_CALLS_PER_MIN
from ..throttle import get_circuit, get_limiter

log = logging.getLogger(__name__)

# yfinance 的限流例外在不同版本下類別名稱不一，用名稱比對比 import 穩。
_RATE_LIMIT_MARKERS = ("ratelimit", "too many requests", "429")


def _is_rate_limited(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(m in text for m in _RATE_LIMIT_MARKERS)


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
    # 公司基本資料：全市場掃描時沒有 S&P 500 那份現成的類股對照表，
    # 而這些欄位就在同一份 info 裡，順手帶出來才能做類股篩選。
    name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    # 每股淨值（最近一季）。股價淨值比刻意不取資料源的現成值，而是用本專案
    # 自己的收盤價去除——否則分子（我們顯示的收盤價）與分母（資料源當時的
    # 價格快照）來自不同時點，算出來的比值跟畫面上的收盤價對不起來。
    book_value: Optional[float] = None
    # 市值。與每股淨值同屬基本面欄位，就在同一份 info 裡，一併帶出。
    market_cap: Optional[float] = None
    error: Optional[str] = None
    # 這次失敗是「來源整個不能用」（限流／熔斷），不是這一檔的問題。
    # 用來決定該不該把這一檔記成失敗並消耗重試次數。
    source_unavailable: bool = False
    # 來源是否「成功回應」——即使回應內容是「這檔沒有分析師覆蓋」也算 True。
    # 用來區分「這檔沒人追蹤」（有效結果）與「這個來源壞掉」（不是這檔的問題）。
    responded: bool = False

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


def _as_signed_float(value) -> Optional[float]:
    """允許負值的數值轉換。

    每股淨值可以是負的（股東權益為負，多見於長期虧損或大額買回的公司），
    那是真實資訊而不是缺值。用 `_as_float` 會把負淨值直接吃掉，變成
    「查無資料」——兩者在篩選時的意義完全相反。
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f      # 只擋 NaN


def _as_text(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_int(value) -> Optional[int]:
    try:
        i = int(value)
    except (TypeError, ValueError):
        return None
    return i if i > 0 else None


def fetch_yahoo_target(ticker: str) -> TargetQuote:
    """抓取單一標的的 Yahoo 共識目標價。任何例外都收斂成 error 欄位。"""
    quote = TargetQuote(ticker=ticker, source="yahoo")
    circuit = get_circuit("yahoo")
    if circuit.is_open():
        # 已經被限流了，再打只會延長封鎖；這一檔留給下次執行。
        quote.error = circuit.reason
        quote.source_unavailable = True
        return quote

    try:
        get_limiter("yahoo", YAHOO_CALLS_PER_MIN).acquire()
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
        quote.name = _as_text(info.get("shortName") or info.get("longName"))
        quote.sector = _as_text(info.get("sector"))
        quote.industry = _as_text(info.get("industry"))
        quote.book_value = _as_signed_float(info.get("bookValue"))
        quote.market_cap = _as_float(info.get("marketCap"))
        quote.responded = True
        circuit.record_success()
    except Exception as e:  # noqa: BLE001 — 單一標的失敗不能中斷整批
        quote.error = f"{type(e).__name__}: {e}"
        log.debug("Yahoo 目標價抓取失敗 %s: %s", ticker, e)
        if _is_rate_limited(e):
            # 被限流代表「這一批問太快了」，不是這一檔有問題。熔斷後本次執行
            # 剩下的標的直接跳過，留在帳本裡等下一班次，避免整批被判死刑。
            circuit.trip("Yahoo 回報限流（Too Many Requests），本次執行已停用此來源，"
                         "未處理的標的留待下次執行")
            quote.source_unavailable = True
        elif circuit.record_failure(type(e).__name__):
            # 連續同型失敗（例如整個網路不通）同樣是來源層級的問題。
            # 少了這一道，一次斷網就會讓每一檔各消耗一次重試次數。
            quote.error = circuit.reason
            quote.source_unavailable = True
    return quote
