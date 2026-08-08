"""全美股逐檔抓取的測試：清單解析、帳本重試、結果逐筆保存、續跑。"""
from __future__ import annotations

import json
from unittest import mock

import pytest

from analyst_valuation import universe_runner
from analyst_valuation.ledger import (
    STATUS_DEAD,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_PENDING,
    Ledger,
)
from analyst_valuation.result_store import ResultStore, shard_for
from analyst_valuation.sources import us_listings

# --- 上市清單解析 ---------------------------------------------------------

NASDAQ_TXT = (
    "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
    "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\n"
    "QQQ|Invesco QQQ Trust|Q|N|N|100|Y|N\n"
    "ZTEST|Nasdaq Test Security|Q|Y|N|100|N|N\n"
    "File Creation Time: 0808202622:00|||||||\n"
)

OTHER_TXT = (
    "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
    "BRK.B|Berkshire Hathaway Inc. Class B|N|BRK.B|N|100|N|BRK.B\n"
    "BAC$B|Bank of America Depositary Shares|N|BACpB|N|100|N|BAC-B\n"
    "File Creation Time: 0808202622:00|||||||\n"
)


def test_parses_nasdaq_listings_and_drops_test_issues():
    rows = us_listings._parse_symbol_directory(NASDAQ_TXT, "Symbol", "NASDAQ", "nasdaqtrader")
    tickers = {r.ticker for r in rows}
    assert "AAPL" in tickers
    assert "ZTEST" not in tickers            # Test Issue = Y 必須濾掉
    assert "File Creation Time: 0808202622:00" not in tickers  # 檔尾那行不是證券
    assert next(r for r in rows if r.ticker == "QQQ").is_etf is True


def test_normalizes_symbols_for_yahoo():
    rows = us_listings._parse_symbol_directory(OTHER_TXT, "ACT Symbol", "OTHER", "nasdaqtrader")
    tickers = {r.ticker for r in rows}
    assert "BRK-B" in tickers      # 交易所寫 BRK.B，Yahoo 用 BRK-B
    assert "BAC-PB" in tickers     # 特別股 BAC$B -> BAC-PB


def test_fetch_us_listings_excludes_etf_by_default():
    def _get(url, **kw):
        text = NASDAQ_TXT if "nasdaqlisted" in url else OTHER_TXT
        return mock.Mock(text=text, raise_for_status=mock.Mock())

    with mock.patch.object(us_listings.requests, "get", side_effect=_get), \
         mock.patch.object(us_listings, "MIN_EXPECTED_LISTINGS", 1):
        rows = us_listings.fetch_us_listings()
    tickers = {r.ticker for r in rows}
    assert "AAPL" in tickers and "QQQ" not in tickers


def test_fetch_us_listings_rejects_suspiciously_short_list():
    """來源檔案殘缺時要報錯，不能用不完整的清單覆蓋既有資料。"""
    def _get(url, **kw):
        return mock.Mock(text=NASDAQ_TXT, raise_for_status=mock.Mock())

    with mock.patch.object(us_listings.requests, "get", side_effect=_get):
        with pytest.raises(ValueError, match="拒絕以殘缺清單覆蓋"):
            us_listings.fetch_us_listings()


def test_fetch_us_listings_falls_back_to_sec():
    sec_payload = {str(i): {"cik_str": i, "ticker": f"T{i}", "title": f"Co {i}"} for i in range(5)}

    def _get(url, **kw):
        if "nasdaqtrader" in url:
            raise RuntimeError("nasdaq down")
        return mock.Mock(json=mock.Mock(return_value=sec_payload), raise_for_status=mock.Mock())

    with mock.patch.object(us_listings.requests, "get", side_effect=_get), \
         mock.patch.object(us_listings, "MIN_EXPECTED_LISTINGS", 1):
        rows = us_listings.fetch_us_listings()
    assert len(rows) == 5 and rows[0].source == "sec"


# --- 帳本：重試與續跑 ------------------------------------------------------

def test_ledger_roundtrip_and_sync(tmp_path):
    led = Ledger(str(tmp_path / "l.csv"), max_attempts=3)
    led.sync_universe(["AAA", "BBB"])
    led.record_success("AAA")
    led.record_failure("BBB", "boom")
    led.save()

    reloaded = Ledger(str(tmp_path / "l.csv")).load()
    assert reloaded.entries["AAA"].status == STATUS_OK
    assert reloaded.entries["BBB"].status == STATUS_FAILED
    assert reloaded.entries["BBB"].attempts == 1
    assert "boom" in reloaded.entries["BBB"].last_error


def test_sync_adds_new_listings_and_drops_delisted(tmp_path):
    led = Ledger(str(tmp_path / "l.csv"))
    led.sync_universe(["AAA", "BBB"])
    stats = led.sync_universe(["BBB", "CCC"])
    assert stats == {"added": 1, "removed": 1, "total": 2}
    assert set(led.entries) == {"BBB", "CCC"}


def test_failure_becomes_dead_after_max_attempts(tmp_path):
    led = Ledger(str(tmp_path / "l.csv"), max_attempts=3)
    led.sync_universe(["AAA"])
    for _ in range(2):
        led.record_failure("AAA", "err")
    assert led.entries["AAA"].status == STATUS_FAILED
    led.record_failure("AAA", "err")
    assert led.entries["AAA"].status == STATUS_DEAD
    # 放棄重試後不會再被排入工作
    assert "AAA" not in led.select_work()


def test_select_work_prioritises_untried_then_fewest_attempts(tmp_path):
    led = Ledger(str(tmp_path / "l.csv"), max_attempts=5)
    led.sync_universe(["NEW", "F1", "F2"])
    led.record_failure("F1", "e")
    led.record_failure("F2", "e")
    led.record_failure("F2", "e")     # F2 試過兩次，應排在 F1 之後
    work = led.select_work()
    assert work[0] == "NEW"
    assert work.index("F1") < work.index("F2")


def test_select_work_honours_limit(tmp_path):
    led = Ledger(str(tmp_path / "l.csv"))
    led.sync_universe([f"T{i}" for i in range(50)])
    assert len(led.select_work(limit=10)) == 10


def test_new_cycle_can_keep_successful_entries(tmp_path):
    led = Ledger(str(tmp_path / "l.csv"))
    led.sync_universe(["AAA", "BBB"])
    led.record_success("AAA")
    led.record_failure("BBB", "e")

    led.reset_for_new_cycle(keep_ok=True)
    assert led.entries["AAA"].status == STATUS_OK        # 已完成的保留
    assert led.entries["BBB"].status == STATUS_PENDING   # 失敗的重新排隊

    led.reset_for_new_cycle(keep_ok=False)
    assert led.entries["AAA"].status == STATUS_PENDING   # 全量重跑


def test_reset_dead_requeues(tmp_path):
    led = Ledger(str(tmp_path / "l.csv"), max_attempts=1)
    led.sync_universe(["AAA"])
    led.record_failure("AAA", "e")
    assert led.entries["AAA"].status == STATUS_DEAD
    assert led.reset_dead() == 1
    assert led.entries["AAA"].status == STATUS_PENDING


def test_ledger_error_is_single_line_and_truncated(tmp_path):
    led = Ledger(str(tmp_path / "l.csv"))
    led.record_failure("AAA", "line1\nline2\n" + "x" * 500)
    err = led.entries["AAA"].last_error
    assert "\n" not in err and len(err) <= 300


# --- 結果庫：逐筆保存 ------------------------------------------------------

def test_result_store_writes_and_reads_per_ticker(tmp_path):
    store = ResultStore(str(tmp_path))
    store.write_many([
        {"ticker": "AAPL", "covered": True},
        {"ticker": "BRK-B", "covered": False},
    ])
    assert store.get("AAPL")["covered"] is True
    assert store.count() == 2


def test_result_store_updates_existing_ticker_in_place(tmp_path):
    store = ResultStore(str(tmp_path))
    store.write_many([{"ticker": "AAPL", "v": 1}])
    store.write_many([{"ticker": "AAPL", "v": 2}])
    assert store.get("AAPL")["v"] == 2
    assert store.count() == 1        # 覆寫而非新增


def test_result_store_shards_by_first_letter(tmp_path):
    assert shard_for("AAPL") == "A"
    assert shard_for("9XYZ") == "_OTHER"
    store = ResultStore(str(tmp_path))
    store.write_many([{"ticker": "AAPL"}, {"ticker": "BBB"}])
    assert (tmp_path / "A.jsonl").exists() and (tmp_path / "B.jsonl").exists()


def test_result_store_survives_a_corrupt_line(tmp_path):
    store = ResultStore(str(tmp_path))
    store.write_many([{"ticker": "AAA"}, {"ticker": "ABC"}])
    path = tmp_path / "A.jsonl"
    path.write_text(path.read_text() + "{not valid json\n")
    assert set(store.load_shard("A")) == {"AAA", "ABC"}   # 壞掉的單行不影響其餘


def test_result_store_redacts_credentials(tmp_path):
    store = ResultStore(str(tmp_path))
    store.write_many([{"ticker": "AAA", "err": "url?token=LEAKEDKEY123"}])
    assert "LEAKEDKEY123" not in (tmp_path / "A.jsonl").read_text()


# --- 執行器：續跑 ----------------------------------------------------------

@pytest.fixture
def _patched_runner(monkeypatch):
    from analyst_valuation.sources.prices import PriceSnapshot
    from analyst_valuation.sources.yahoo_targets import TargetQuote

    monkeypatch.setattr(universe_runner.prices, "fetch_price_snapshots",
                        lambda tickers, **kw: {
                            t: PriceSnapshot(t, close=100.0, close_date="2026-08-08") for t in tickers
                        })
    monkeypatch.setattr(universe_runner.finnhub_targets, "is_enabled", lambda *a, **k: False)
    monkeypatch.setattr(universe_runner.fmp_targets, "is_enabled", lambda *a, **k: False)

    def _target(ticker):
        if ticker == "BAD":
            raise RuntimeError("fetch exploded")
        return TargetQuote(ticker=ticker, source="yahoo", mean=120.0, analyst_count=7)

    monkeypatch.setattr(universe_runner, "fetch_yahoo_target", _target)
    return monkeypatch


def test_runner_records_success_and_failure_separately(_patched_runner, tmp_path):
    res = universe_runner.run(
        universe_tickers=["AAA", "BAD", "CCC"],
        ledger_path=str(tmp_path / "l.csv"),
        results_root=str(tmp_path / "r"),
        max_workers=2,
    )
    assert res["ok"] == 2 and res["failed"] == 1
    led = Ledger(str(tmp_path / "l.csv")).load()
    assert led.entries["AAA"].status == STATUS_OK
    assert led.entries["BAD"].status == STATUS_FAILED
    assert "fetch exploded" in led.entries["BAD"].last_error
    # 成功的結果已逐筆落地
    assert ResultStore(str(tmp_path / "r")).get("AAA")["price"]["close"] == 100.0


def test_runner_resumes_only_unfinished_work(_patched_runner, tmp_path):
    ledger_path, results = str(tmp_path / "l.csv"), str(tmp_path / "r")
    tickers = ["AAA", "BBB", "CCC", "BAD"]

    first = universe_runner.run(tickers, ledger_path, results, max_tickers=2, max_workers=2)
    assert first["processed"] == 2      # 依序取 AAA、BAD
    after_first = Ledger(ledger_path).load()
    assert after_first.entries["AAA"].attempts == 1

    second = universe_runner.run(tickers, ledger_path, results, max_tickers=10, max_workers=2)
    # 剩下未處理的 BBB、CCC，加上可重試的 BAD
    assert second["processed"] == 3
    after_second = Ledger(ledger_path).load()
    # 關鍵：已完成的 AAA 不會被重做
    assert after_second.entries["AAA"].attempts == 1
    assert after_second.entries["BAD"].attempts == 2

    third = universe_runner.run(tickers, ledger_path, results, max_tickers=10, max_workers=2)
    # 只剩 BAD 還能重試（第3次後達上限）
    assert third["processed"] == 1
    assert Ledger(ledger_path).load().entries["BAD"].status == STATUS_DEAD


def test_runner_stops_retrying_after_max_attempts(_patched_runner, tmp_path):
    ledger_path, results = str(tmp_path / "l.csv"), str(tmp_path / "r")
    for _ in range(5):
        universe_runner.run(["BAD"], ledger_path, results, max_workers=1)
    led = Ledger(ledger_path).load()
    assert led.entries["BAD"].status == STATUS_DEAD
    assert led.select_work() == []


def test_uncovered_ticker_is_success_not_failure(_patched_runner, monkeypatch, tmp_path):
    """查得到但沒有分析師覆蓋，是有效結果，不該被當成失敗而不斷重試。"""
    from analyst_valuation.sources.yahoo_targets import TargetQuote
    monkeypatch.setattr(universe_runner, "fetch_yahoo_target",
                        lambda t: TargetQuote(ticker=t, source="yahoo", error="Finnhub 無此標的目標價資料"))
    res = universe_runner.run(["NOCOV"], str(tmp_path / "l.csv"), str(tmp_path / "r"), max_workers=1)
    assert res["ok"] == 1
    rec = ResultStore(str(tmp_path / "r")).get("NOCOV")
    assert rec["covered"] is False
