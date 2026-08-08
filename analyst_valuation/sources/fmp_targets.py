"""Financial Modeling Prep — 逐機構分析師目標價（選用，需 API 金鑰）。

這是本專案目前唯一能取得「**每一家券商各自的目標價**」的可程式化來源；
Yahoo 與 Finnhub 的免費端點都只給彙總後的共識值（平均／最高／最低），
無法據以做機構層級的去重與自行平均。

設定環境變數 `FMP_API_KEY` 後自動啟用。未設金鑰時整個來源停用（不是失敗），
系統會退回使用各資料源的共識值，並在輸出中誠實標示 basis 為 "consensus"。

金鑰申請：https://site.financialmodelingprep.com/developer/docs
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

from ..config import FMP_CALLS_PER_MIN, FMP_ENV_KEY, FMP_PRICE_TARGET_URL
from ..throttle import get_circuit, get_limiter
from ..firms import TargetRecord
from ..secrets_redaction import redact_secrets

log = logging.getLogger(__name__)


def is_enabled(api_key: Optional[str] = None) -> bool:
    return bool(api_key or os.environ.get(FMP_ENV_KEY))


def _as_float(value) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f and f > 0 else None


def fetch_fmp_firm_targets(
    ticker: str,
    api_key: Optional[str] = None,
    timeout: int = 15,
    session: Optional[requests.Session] = None,
) -> tuple[list[TargetRecord], Optional[str]]:
    """回傳 (逐機構目標價記錄, 錯誤訊息)。未設金鑰時回傳空清單與說明。"""
    key = api_key or os.environ.get(FMP_ENV_KEY)
    if not key:
        return [], "未設定 FMP_API_KEY，逐機構目標價來源停用"

    circuit = get_circuit("fmp")
    if circuit.is_open():          # 402 之後不再對其餘幾百檔重複發同樣的請求
        return [], circuit.reason

    try:
        http = session or requests
        get_limiter("fmp", FMP_CALLS_PER_MIN).acquire()
        resp = http.get(
            FMP_PRICE_TARGET_URL, params={"symbol": ticker, "apikey": key}, timeout=timeout
        )
        circuit.trip_if_fatal(
            resp.status_code,
            f"FMP 回應 {resp.status_code}：免費方案不支援逐機構目標價端點"
            f"（需付費方案），本次執行已停用此來源",
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:  # noqa: BLE001
        msg = redact_secrets(f"{type(e).__name__}: {e}")
        log.debug("FMP 逐機構目標價抓取失敗 %s: %s", ticker, msg)
        if circuit.record_failure(type(e).__name__):
            return [], circuit.reason
        return [], msg

    if not isinstance(payload, list):
        return [], redact_secrets(f"FMP 回傳非預期格式：{str(payload)[:160]}")

    circuit.record_success()
    records: list[TargetRecord] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        target = _as_float(row.get("priceTarget") or row.get("adjPriceTarget"))
        firm = row.get("analystCompany") or row.get("analystName") or row.get("publisher")
        if target is None or not firm:
            continue
        published = row.get("publishedDate") or row.get("date")
        records.append(TargetRecord(
            ticker=ticker,
            firm=str(firm).strip(),
            target=target,
            source="fmp",
            published=str(published)[:10] if published else None,
        ))
    return records, None
