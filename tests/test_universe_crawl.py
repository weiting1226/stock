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
    """查得到但沒有分析師覆蓋，是有效結果，不該被當成失敗而不斷重試。

    來源有回應（responded=True）卻沒有目標價，就是「這檔沒人追蹤」的正常結果。
    """
    from analyst_valuation.sources.yahoo_targets import TargetQuote
    monkeypatch.setattr(universe_runner, "fetch_yahoo_target",
                        lambda t: TargetQuote(ticker=t, source="yahoo", responded=True))
    res = universe_runner.run(["NOCOV"], str(tmp_path / "l.csv"), str(tmp_path / "r"), max_workers=1)
    assert res["ok"] == 1
    rec = ResultStore(str(tmp_path / "r")).get("NOCOV")
    assert rec["covered"] is False


# --- 來源壞掉不得污染對個別標的的判斷 --------------------------------------

def test_broken_source_does_not_mark_uncovered_ticker_as_failed(_patched_runner, monkeypatch, tmp_path):
    """線上實測 300 檔有 145 檔被誤記為失敗：Finnhub 熔斷後每檔都帶著它的
    錯誤訊息，讓「本來就沒有分析師覆蓋」的標的（SPAC單位、權證）被判成失敗。
    來源自己壞掉是來源的問題，不是這一檔的問題。"""
    from analyst_valuation.sources.yahoo_targets import TargetQuote

    # Yahoo 正常回應但這檔沒有覆蓋；Finnhub 壞掉
    monkeypatch.setattr(universe_runner, "fetch_yahoo_target",
                        lambda t: TargetQuote(ticker=t, source="yahoo", responded=True))
    monkeypatch.setattr(universe_runner.finnhub_targets, "is_enabled", lambda *a, **k: True)
    monkeypatch.setattr(universe_runner.finnhub_targets, "fetch_finnhub_target",
                        lambda t: TargetQuote(ticker=t, source="finnhub",
                                              error="連續 5 檔以相同方式失敗，已停用此來源"))

    res = universe_runner.run(["SPACU"], str(tmp_path / "l.csv"), str(tmp_path / "r"), max_workers=1)
    assert res["ok"] == 1 and res["failed"] == 0
    assert ResultStore(str(tmp_path / "r")).get("SPACU")["covered"] is False


def test_rate_limited_ticker_is_deferred_not_failed(_patched_runner, monkeypatch, tmp_path):
    """線上實測：1500 檔那批有 1190 檔敗在 Yahoo 限流。來源被限流時整批都會
    這樣，若照樣記成失敗，一次限流就讓上千檔各耗掉一次重試次數，三次之後
    全部變 dead——問題其實出在來源。這種情況要留在 pending 等下次執行。"""
    from analyst_valuation.sources.yahoo_targets import TargetQuote

    monkeypatch.setattr(universe_runner, "fetch_yahoo_target",
                        lambda t: TargetQuote(ticker=t, source="yahoo",
                                              error="YFRateLimitError: Too Many Requests",
                                              source_unavailable=True))
    ledger_path = str(tmp_path / "l.csv")
    res = universe_runner.run(["AAA", "BBB"], ledger_path, str(tmp_path / "r"), max_workers=1)

    assert res["deferred"] == 2 and res["failed"] == 0 and res["ok"] == 0
    led = Ledger(ledger_path).load()
    for t in ("AAA", "BBB"):
        assert led.entries[t].status == STATUS_PENDING
        assert led.entries[t].attempts == 0      # 關鍵：沒有消耗重試次數
    # 下次執行時它們仍在待辦清單裡
    assert set(led.select_work()) == {"AAA", "BBB"}


def test_yahoo_rate_limit_trips_circuit_and_skips_rest(monkeypatch):
    """第一檔被限流之後就該熔斷，不再對其餘標的發請求——繼續打只會延長封鎖。"""
    from analyst_valuation.sources import yahoo_targets
    from analyst_valuation.throttle import reset_all

    reset_all()
    calls = []

    class _Boom:
        def __init__(self, ticker):
            calls.append(ticker)

        @property
        def analyst_price_targets(self):
            raise RuntimeError("YFRateLimitError: Too Many Requests. Rate limited.")

    monkeypatch.setattr(yahoo_targets.yf, "Ticker", _Boom)
    monkeypatch.setattr(yahoo_targets, "YAHOO_CALLS_PER_MIN", 100000)

    first = yahoo_targets.fetch_yahoo_target("AAA")
    second = yahoo_targets.fetch_yahoo_target("BBB")

    assert first.source_unavailable and second.source_unavailable
    assert calls == ["AAA"]              # 熔斷後第二檔完全沒有發出請求
    assert "限流" in second.error
    reset_all()


def test_yahoo_blanket_outage_defers_instead_of_burning_retries(monkeypatch):
    """整個網路不通時每一檔都會失敗；連續同型失敗要熔斷成來源層級問題，
    否則一次斷網就把全部標的的重試次數耗掉。"""
    from analyst_valuation.sources import yahoo_targets
    from analyst_valuation.throttle import SourceCircuit, reset_all

    reset_all()

    class _Down:
        def __init__(self, ticker):
            pass

        @property
        def analyst_price_targets(self):
            raise ConnectionError("Failed to perform, curl: (7) CONNECT tunnel failed")

    monkeypatch.setattr(yahoo_targets.yf, "Ticker", _Down)
    monkeypatch.setattr(yahoo_targets, "YAHOO_CALLS_PER_MIN", 100000)

    quotes = [yahoo_targets.fetch_yahoo_target(f"T{i}") for i in range(8)]
    # 熔斷門檻之前算個別失敗，之後轉為來源層級
    assert not quotes[0].source_unavailable
    assert quotes[SourceCircuit.CONSECUTIVE_FAILURE_LIMIT].source_unavailable
    assert quotes[-1].source_unavailable
    reset_all()


def test_all_sources_unavailable_is_a_real_failure(_patched_runner, monkeypatch, tmp_path):
    """反之，若沒有任何來源成功回應，這檔就是還沒問到，必須重試。"""
    from analyst_valuation.sources.yahoo_targets import TargetQuote

    monkeypatch.setattr(universe_runner, "fetch_yahoo_target",
                        lambda t: TargetQuote(ticker=t, source="yahoo",
                                              error="HTTPError: 503", responded=False))
    res = universe_runner.run(["AAA"], str(tmp_path / "l.csv"), str(tmp_path / "r"), max_workers=1)
    assert res["failed"] == 1
    assert Ledger(str(tmp_path / "l.csv")).load().entries["AAA"].status == STATUS_FAILED


# --- 把爬取結果彙整成儀表板報告 --------------------------------------------

@pytest.fixture
def _crawl_output(tmp_path):
    """做出一份小型的結果庫：一檔正常、一檔異常、一檔沒有分析師覆蓋。"""
    from analyst_valuation.result_store import ResultStore

    results = tmp_path / "r"
    ResultStore(str(results)).write_many([
        {
            "ticker": "AAA", "covered": True, "sector": "Technology",
            "quotes": [{"source": "yahoo", "mean": 120.0, "median": 118.0,
                        "high": 150.0, "low": 90.0, "analyst_count": 12}],
            "price": {"close": 100.0, "close_date": "2026-08-07",
                      "change_1w_pct": 1.5, "change_1m_pct": -2.0},
        },
        {
            # 現價 0.20、目標價 5.00 -> 上漲 2400%，全市場掃描裡滿地都是這種
            "ticker": "PENNY", "covered": True, "sector": "Healthcare",
            "quotes": [{"source": "yahoo", "mean": 5.0, "analyst_count": 1}],
            "price": {"close": 0.20, "close_date": "2026-08-07"},
        },
        {
            # 普通股也有三成沒有分析師追蹤，這才是過濾之後「無覆蓋」的典型樣貌
            "ticker": "NOCOV", "covered": False, "note": "查詢成功但無分析師目標價覆蓋",
            "price": {"close": 10.01, "close_date": "2026-08-07"},
        },
    ])

    universe = tmp_path / "u.csv"
    universe.write_text(
        "ticker,name,exchange,is_etf,security_type,source\n"
        "AAA,Alpha Inc. Common Stock,Q,False,common,nasdaqtrader\n"
        "PENNY,Penny Corp Common Stock,Q,False,common,nasdaqtrader\n"
        "NOCOV,No Coverage Inc. Common Stock,Q,False,common,nasdaqtrader\n"
        "LATER,Not Yet Fetched Inc. Common Stock,Q,False,common,nasdaqtrader\n"
        # 非普通股：預設不納入抓取，因此不該計入 universe
        "SPACU,Example Acquisition Corp. - Units,Q,False,unit,nasdaqtrader\n",
        encoding="utf-8",
    )
    return {"results": str(results), "universe": str(universe),
            "ledger": str(tmp_path / "nonexistent.csv")}


def test_report_counts_distinguish_uncovered_from_not_yet_fetched(_crawl_output):
    """全市場掃描跨多班次完成，讀者必須能分辨「沒人追蹤」與「今天還沒輪到」。"""
    from analyst_valuation import universe_publish

    rep = universe_publish.build_report(
        results_root=_crawl_output["results"],
        universe_path=_crawl_output["universe"],
        ledger_path=_crawl_output["ledger"],
    )
    c = rep["counts"]
    assert c["universe"] == 4          # 清單 4 檔
    assert c["fetched"] == 3           # 已抓 3 檔
    assert c["not_yet_fetched"] == 1   # LATER 還沒輪到
    assert c["with_target_and_price"] == 2
    assert c["missing"] == 1           # NOCOV 查得到但沒覆蓋


def test_implausible_rows_sink_and_are_excluded_from_sector_medians(_crawl_output):
    """雞蛋水餃股的上千 % 上漲空間若排在最前面，儀表板等於失去用途。"""
    from analyst_valuation import universe_publish

    rep = universe_publish.build_report(
        results_root=_crawl_output["results"],
        universe_path=_crawl_output["universe"],
        ledger_path=_crawl_output["ledger"],
    )
    order = [r["ticker"] for r in rep["rows"]]
    assert order[0] == "AAA"                      # 上漲 20%，合理
    assert order.index("PENNY") > order.index("AAA")
    assert rep["counts"]["implausible"] == 1
    assert next(r for r in rep["rows"] if r["ticker"] == "PENNY")["implausible"] is True
    # Healthcare 只有那檔異常列，因此不該出現在類股中位數裡
    assert "Healthcare" not in {s["sector"] for s in rep["sector_summary"]}


def test_report_hoists_repeated_source_errors_out_of_every_row(tmp_path):
    """來源層級的錯誤逐列存等於把同一句話抄 7,498 遍，佔掉整份 JSON 兩成。"""
    from analyst_valuation import universe_publish
    from analyst_valuation.result_store import ResultStore

    results = tmp_path / "r"
    err = "FMP 回應 402：免費方案不支援逐機構目標價端點"
    ResultStore(str(results)).write_many([
        {"ticker": t, "covered": True, "firm_source_error": err,
         "quotes": [{"source": "yahoo", "mean": 120.0, "analyst_count": 5}],
         "price": {"close": 100.0, "close_date": "2026-08-07"}}
        for t in ("AAA", "BBB", "CCC")
    ])

    rep = universe_publish.build_report(
        results_root=str(results),
        universe_path=str(tmp_path / "missing.csv"),
        ledger_path=str(tmp_path / "missing.csv"),
    )
    assert rep["source_errors"] == [{"error": err, "affected_tickers": 3}]
    for row in rep["rows"]:
        assert err not in "".join(row.get("notes", []))


def test_compact_row_keeps_fields_the_dashboard_filters_on():
    """省略欄位是為了縮小檔案，但篩選與排序用得到的欄位缺鍵會讓
    「未知」與「不符合條件」變得無法區分。"""
    from analyst_valuation.universe_publish import compact_row

    out = compact_row({
        "ticker": "AAA", "name": "Alpha", "sector": "Unknown",
        "close": None, "consensus_target": None, "upside_pct": None,
        "confidence": "暫缺", "analyst_count": None,
        "basis": "consensus", "duplicates_removed": 0, "implausible": False,
        "sources_used": ["yahoo"], "firm_targets": [], "notes": [],
    })
    for key in ("ticker", "name", "sector", "close", "consensus_target",
                "upside_pct", "confidence", "analyst_count"):
        assert key in out
    # 預設值與空值不必逐列重複
    for key in ("basis", "duplicates_removed", "implausible",
                "sources_used", "firm_targets", "notes"):
        assert key not in out
