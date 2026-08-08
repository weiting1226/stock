"""節流與熔斷測試：配合各資料源免費方案的限制。"""
from __future__ import annotations

import time
from unittest import mock

import pytest
import requests

from analyst_valuation import throttle
from analyst_valuation.sources import finnhub_targets, fmp_targets


@pytest.fixture(autouse=True)
def _reset():
    throttle.reset_all()
    yield
    throttle.reset_all()


def test_rate_limiter_spaces_calls_out():
    """免費方案是硬上限，呼叫必須被攤平成固定間隔。"""
    limiter = throttle.RateLimiter(calls_per_min=600)   # 每次間隔 0.1 秒
    start = time.monotonic()
    for _ in range(3):
        limiter.acquire()
    elapsed = time.monotonic() - start
    # 第一次不等待，之後兩次各等 0.1 秒
    assert elapsed >= 0.18


def test_rate_limiter_is_thread_safe():
    import threading
    limiter = throttle.RateLimiter(calls_per_min=1200)  # 0.05 秒
    start = time.monotonic()
    threads = [threading.Thread(target=limiter.acquire) for _ in range(6)]
    for t in threads: t.start()
    for t in threads: t.join()
    # 6 次呼叫至少要花 5 個間隔，證明並發也有被序列化
    assert time.monotonic() - start >= 0.2


@pytest.mark.parametrize("status", [401, 402, 403])
def test_circuit_trips_on_plan_and_auth_errors(status):
    c = throttle.SourceCircuit("x")
    assert c.trip_if_fatal(status, "方案不支援") is True
    assert c.is_open() and c.reason == "方案不支援"


@pytest.mark.parametrize("status", [200, 429, 500])
def test_circuit_ignores_retryable_statuses(status):
    """429/5xx 是暫時性的，不該熔斷整個來源。"""
    c = throttle.SourceCircuit("x")
    assert c.trip_if_fatal(status, "r") is False
    assert not c.is_open()


def test_circuit_keeps_first_reason():
    c = throttle.SourceCircuit("x")
    c.trip("第一個原因")
    c.trip("第二個原因")
    assert c.reason == "第一個原因"


def _resp(status):
    r = mock.Mock(status_code=status)
    err = requests.HTTPError(f"{status} Client Error")
    r.raise_for_status = mock.Mock(side_effect=err)
    return r


def test_fmp_stops_calling_after_payment_required():
    """402 之後不該再對其餘幾百檔重複發同樣的請求。"""
    session = mock.Mock()
    session.get = mock.Mock(return_value=_resp(402))

    first, err1 = fmp_targets.fetch_fmp_firm_targets("AAA", api_key="k", session=session)
    assert first == [] and "402" in err1
    assert session.get.call_count == 1

    second, err2 = fmp_targets.fetch_fmp_firm_targets("BBB", api_key="k", session=session)
    assert second == []
    assert "停用此來源" in err2
    assert session.get.call_count == 1        # 完全沒有再送出請求


def test_finnhub_stops_calling_after_auth_error():
    session = mock.Mock()
    session.get = mock.Mock(return_value=_resp(401))

    q1 = finnhub_targets.fetch_finnhub_target("AAA", api_key="k", session=session)
    assert not q1.ok and session.get.call_count == 1

    q2 = finnhub_targets.fetch_finnhub_target("BBB", api_key="k", session=session)
    assert "停用此來源" in q2.error
    assert session.get.call_count == 1


def test_finnhub_keeps_trying_after_rate_limit():
    """429 是暫時性的，不能因此把整個來源關掉。"""
    session = mock.Mock()
    session.get = mock.Mock(return_value=_resp(429))

    finnhub_targets.fetch_finnhub_target("AAA", api_key="k", session=session)
    finnhub_targets.fetch_finnhub_target("BBB", api_key="k", session=session)
    assert session.get.call_count == 2        # 仍會繼續嘗試


def test_tripped_circuits_reports_disabled_sources():
    throttle.get_circuit("fmp").trip("免費方案不支援")
    assert throttle.tripped_circuits() == {"fmp": "免費方案不支援"}


# --- 連續失敗熔斷（來源用 200 空白內容表達「不支援」時的防線）-------------

def test_circuit_trips_after_repeated_identical_failures():
    c = throttle.SourceCircuit("x")
    for _ in range(throttle.SourceCircuit.CONSECUTIVE_FAILURE_LIMIT - 1):
        assert c.record_failure("JSONDecodeError") is False
    assert c.record_failure("JSONDecodeError") is True
    assert "連續" in c.reason and "JSONDecodeError" in c.reason


def test_success_resets_the_failure_streak():
    """偶發失敗不該累積成熔斷。"""
    c = throttle.SourceCircuit("x")
    for _ in range(4):
        c.record_failure("timeout")
    c.record_success()
    for _ in range(4):
        assert c.record_failure("timeout") is False
    assert not c.is_open()


def test_finnhub_stops_after_repeated_non_json_bodies():
    """Finnhub 對不支援的端點回 200 空白內容，沒有致命狀態碼可判斷，
    只能靠「每一檔都以同樣方式失敗」來停手。"""
    session = mock.Mock()
    bad = mock.Mock(status_code=200, raise_for_status=mock.Mock())
    bad.json = mock.Mock(side_effect=ValueError("Expecting value: line 1 column 1 (char 0)"))
    session.get = mock.Mock(return_value=bad)

    for i in range(throttle.SourceCircuit.CONSECUTIVE_FAILURE_LIMIT + 4):
        finnhub_targets.fetch_finnhub_target(f"T{i}", api_key="k", session=session)

    # 達上限後就不再送出請求
    assert session.get.call_count == throttle.SourceCircuit.CONSECUTIVE_FAILURE_LIMIT
    assert throttle.get_circuit("finnhub").is_open()
