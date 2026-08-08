"""配合各資料源免費方案限制的節流與熔斷。

兩個實際遇到的問題：

1. **速率上限**：Finnhub 免費方案為 60 calls/min，但抓取是 8 執行緒並發，
   40 檔就把配額打爆，整批回 429、一筆資料都拿不到。
2. **方案不支援**：FMP 免費方案對 price-target 端點回 402 Payment Required。
   這種錯誤重試無用，繼續對其餘幾百檔發同樣的請求只是浪費時間與配額。

因此：速率上限用 `RateLimiter` 把該來源的呼叫攤平到方案允許的頻率；
方案／授權類錯誤（401/402/403）用 `SourceCircuit` 直接熔斷，
本次執行不再呼叫該來源，並只記錄一次原因而不是每檔都記一遍。
"""
from __future__ import annotations

import threading
import time
from typing import Optional


class RateLimiter:
    """把呼叫攤平成固定間隔，跨執行緒共用。

    用「下一次可呼叫時間」而非計數視窗：實作簡單、不會在視窗邊界突刺，
    對免費方案這種硬上限比較安全。
    """

    def __init__(self, calls_per_min: int):
        self._interval = 60.0 / max(1, calls_per_min)
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            self._next_allowed = max(now, self._next_allowed) + self._interval
        if wait > 0:
            time.sleep(wait)


class SourceCircuit:
    """遇到方案／授權類錯誤就熔斷該來源，本次執行不再呼叫。"""

    # 這些狀態碼代表「這把金鑰／這個方案就是不能用這個端點」，重試沒有意義
    FATAL_STATUS = {401, 402, 403}

    def __init__(self, name: str):
        self.name = name
        self._lock = threading.Lock()
        self._reason: Optional[str] = None

    @property
    def reason(self) -> Optional[str]:
        return self._reason

    def is_open(self) -> bool:
        return self._reason is not None

    def trip(self, reason: str) -> None:
        with self._lock:
            if self._reason is None:
                self._reason = reason

    def trip_if_fatal(self, status_code: Optional[int], reason: str) -> bool:
        """狀態碼屬於致命類就熔斷，回傳是否已熔斷。"""
        if status_code in self.FATAL_STATUS:
            self.trip(reason)
            return True
        return False


_limiters: dict[str, RateLimiter] = {}
_circuits: dict[str, SourceCircuit] = {}
_registry_lock = threading.Lock()


def get_limiter(name: str, calls_per_min: int) -> RateLimiter:
    with _registry_lock:
        if name not in _limiters:
            _limiters[name] = RateLimiter(calls_per_min)
        return _limiters[name]


def get_circuit(name: str) -> SourceCircuit:
    with _registry_lock:
        if name not in _circuits:
            _circuits[name] = SourceCircuit(name)
        return _circuits[name]


def tripped_circuits() -> dict[str, str]:
    """回傳本次執行被熔斷的來源與原因，供報告呈現。"""
    with _registry_lock:
        return {n: c.reason for n, c in _circuits.items() if c.is_open()}


def reset_all() -> None:
    """重置狀態（測試用，也讓同一行程可重複執行）。"""
    with _registry_lock:
        _limiters.clear()
        _circuits.clear()
