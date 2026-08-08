"""Finnhub 分析師目標價（選用，需免費 API 金鑰）。

設定環境變數 `FINNHUB_API_KEY` 後自動啟用，成為第二個獨立的共識來源，
此時 consensus_target 會是 Yahoo 與 Finnhub 兩組共識價的平均。
沒有金鑰就整個來源停用（不是失敗），輸出中 sources_used 只會列出 yahoo。

免費金鑰申請：https://finnhub.io/register（60 calls/min）
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

from ..config import FINNHUB_API_URL, FINNHUB_ENV_KEY
from ..secrets_redaction import redact_secrets
from .yahoo_targets import TargetQuote, _as_float

log = logging.getLogger(__name__)


def is_enabled(api_key: Optional[str] = None) -> bool:
    return bool(api_key or os.environ.get(FINNHUB_ENV_KEY))


def fetch_finnhub_target(
    ticker: str, api_key: Optional[str] = None, timeout: int = 15,
    session: Optional[requests.Session] = None,
) -> TargetQuote:
    """抓取 Finnhub 的共識目標價。未設金鑰時回傳帶 error 的空 quote。"""
    quote = TargetQuote(ticker=ticker, source="finnhub")
    key = api_key or os.environ.get(FINNHUB_ENV_KEY)
    if not key:
        quote.error = "未設定 FINNHUB_API_KEY，此來源停用"
        return quote

    try:
        http = session or requests
        resp = http.get(
            FINNHUB_API_URL, params={"symbol": ticker, "token": key}, timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json() or {}
        quote.mean = _as_float(data.get("targetMean"))
        quote.median = _as_float(data.get("targetMedian"))
        quote.high = _as_float(data.get("targetHigh"))
        quote.low = _as_float(data.get("targetLow"))
        if not quote.ok:
            quote.error = "Finnhub 無此標的目標價資料"
    except Exception as e:  # noqa: BLE001
        # 金鑰在 query string 裡，例外訊息含完整URL；務必先遮蔽再輸出或記錄
        quote.error = redact_secrets(f"{type(e).__name__}: {e}")
        log.debug("Finnhub 目標價抓取失敗 %s: %s", ticker, quote.error)
    return quote
