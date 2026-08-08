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

from ..config import FINNHUB_API_URL, FINNHUB_CALLS_PER_MIN, FINNHUB_ENV_KEY
from ..throttle import get_circuit, get_limiter
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

    circuit = get_circuit("finnhub")
    if circuit.is_open():          # 方案不支援時不再浪費配額
        quote.error = circuit.reason
        return quote

    try:
        http = session or requests
        get_limiter("finnhub", FINNHUB_CALLS_PER_MIN).acquire()
        resp = http.get(
            FINNHUB_API_URL, params={"symbol": ticker, "token": key}, timeout=timeout
        )
        circuit.trip_if_fatal(
            resp.status_code,
            f"Finnhub 回應 {resp.status_code}：金鑰無效或方案不支援此端點，本次執行已停用此來源",
        )
        resp.raise_for_status()
        try:
            data = resp.json() or {}
        except ValueError:
            # 免費方案打到付費端點時，Finnhub 可能回 200 但內容不是 JSON
            #（空字串或純文字說明）。直接讓 JSONDecodeError 冒出來只會得到
            # 「Expecting value: line 1 column 1」，看不出是方案問題。
            body = (resp.text or "").strip()
            hint = body[:120] if body else "空的回應內容"
            raise RuntimeError(
                f"Finnhub 回應 {resp.status_code} 但不是 JSON（方案可能不支援此端點）：{hint}"
            ) from None
        quote.mean = _as_float(data.get("targetMean"))
        quote.median = _as_float(data.get("targetMedian"))
        quote.high = _as_float(data.get("targetHigh"))
        quote.low = _as_float(data.get("targetLow"))
        quote.responded = True
        if not quote.ok:
            quote.error = "Finnhub 無此標的目標價資料"
        circuit.record_success()
    except Exception as e:  # noqa: BLE001
        # 金鑰在 query string 裡，例外訊息含完整URL；務必先遮蔽再輸出或記錄
        quote.error = redact_secrets(f"{type(e).__name__}: {e}")
        log.debug("Finnhub 目標價抓取失敗 %s: %s", ticker, quote.error)
        if circuit.record_failure(type(e).__name__):
            quote.error = circuit.reason
    return quote
