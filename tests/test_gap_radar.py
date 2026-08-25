"""模組九測試：全部使用合成資料，不需網路連線。

重點驗證：
1. 只有「買入」「強力買入」評等入選，其餘（hold、缺評等、implausible）被排除。
2. Top N 依上漲空間由大到小排序。
3. 風險分層門檻是相對這份買評清單本身的分歧度分布（中位數／前25%／前10%），
   不是寫死的絕對數字。
4. storage 會記錄「較上次」的名次變化，且能正確辨識新進榜的標的。
"""
from __future__ import annotations

from gap_radar import pipeline, storage


def _row(ticker, upside, dispersion, rec="buy", implausible=False, **kw):
    return {
        "ticker": ticker,
        "name": f"{ticker} Inc.",
        "sector": "Technology",
        "close": 100.0,
        "consensus_target": 100.0 * (1 + upside / 100),
        "target_low": 90.0,
        "target_high": 110.0,
        "upside_pct": upside,
        "target_dispersion_pct": dispersion,
        "recommendation_key": rec,
        "recommendation_mean": 2.0,
        "analyst_count": 10,
        "market_cap": 1e10,
        "confidence": "中",
        "implausible": implausible,
        **kw,
    }


def _valuation_report(rows):
    return {
        "as_of": "2026-08-14",
        "generated_at": "2026-08-14T23:00:00Z",
        "counts": {"universe": len(rows)},
        "rows": rows,
    }


def test_excludes_non_buy_rated_and_ineligible_rows():
    rows = [
        _row("BUY1", 20, 40, rec="buy"),
        _row("HOLD1", 90, 5, rec="hold"),          # 不是買評，排除
        _row("NONE1", 90, 5, rec="none"),           # 缺評等，排除
        _row("IMPL1", 90, 5, rec="strong_buy", implausible=True),  # 標記異常，排除
    ]
    report = pipeline.build_report(_valuation_report(rows), top_n=10)
    tickers = {r["ticker"] for r in report["top10"]}
    assert tickers == {"BUY1"}
    assert report["counts"]["candidates"] == 1


def test_top_n_is_sorted_by_upside_descending():
    rows = [
        _row("LOW", 5, 30),
        _row("HIGH", 50, 30, rec="strong_buy"),
        _row("MID", 20, 30),
    ]
    report = pipeline.build_report(_valuation_report(rows), top_n=2)
    assert [r["ticker"] for r in report["top10"]] == ["HIGH", "MID"]
    assert [r["rank"] for r in report["top10"]] == [1, 2]


def test_risk_tiers_are_relative_to_the_candidate_pool_not_fixed_numbers():
    # 100 檔候選，分歧度大多集中在 20~40，只有極少數落在 90~100 的極端值。
    # 門檻由這個分布本身算出來，而不是寫死的絕對百分比。
    rows = [_row(f"T{i}", 10 + i, 20 + (i % 21)) for i in range(96)]
    rows += [_row(f"X{i}", 10, 95 + i) for i in range(4)]  # 極端離群值
    report = pipeline.build_report(_valuation_report(rows), top_n=100)
    by_ticker = {r["ticker"]: r for r in report["top10"]}
    # 集中區間裡分歧度最小的應落在 low；離群的極端值應落在 extreme
    assert by_ticker["T0"]["risk_tier"] == "low"
    assert by_ticker["X0"]["risk_tier"] == "extreme"
    tiers_seen = {r["risk_tier"] for r in report["top10"]}
    assert tiers_seen <= set(pipeline.RISK_TIERS)


def test_rows_missing_dispersion_or_upside_are_not_candidates():
    rows = [
        _row("OK", 20, 40),
        {**_row("NOUP", 20, 40), "upside_pct": None},
        {**_row("NODISP", 20, 40), "target_dispersion_pct": None},
    ]
    report = pipeline.build_report(_valuation_report(rows), top_n=10)
    assert [r["ticker"] for r in report["top10"]] == ["OK"]


def test_background_series_covers_every_candidate_for_the_scatter_chart():
    rows = [_row(f"T{i}", 10 + i, 20 + i) for i in range(15)]
    report = pipeline.build_report(_valuation_report(rows), top_n=5)
    assert len(report["background"]) == 15
    assert len(report["top10"]) == 5


def test_load_valuation_report_raises_a_helpful_error_when_missing(tmp_path):
    import pytest
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(FileNotFoundError, match="模組二"):
        pipeline.load_valuation_report(str(missing))


def test_universe_label_passes_through_from_the_source_report():
    """讓前端能標示這份候選股池是 S&P 500 還是 Nasdaq-100。"""
    report = _valuation_report([_row("AAA", 20, 40)])
    report["universe"] = "Nasdaq-100 (TradingView constituents)"
    built = pipeline.build_report(report, top_n=10)
    assert built["universe"] == "Nasdaq-100 (TradingView constituents)"


# --- 上游資料的新鮮度 -------------------------------------------------------
#
# 這個模組自己不抓資料，整份輸出都是模組二 latest.json 的再加工。於是
# 「模組二今天還沒跑」與「跑完了」產出的東西**形狀完全一樣**：名單排得出來、
# 分層算得出來、頁面照顯示，沒有例外也沒有空值，只有 as_of 少了一天。
# 排程一旦飄移（原本是每週一次，而且與模組二只差 20 分鐘），沒有任何地方會叫。

def test_staleness_is_measured_against_the_run_date_not_just_recorded():
    """只把模組二的 as_of 抄過來是不夠的——讀的人得自己記得今天幾號才看得出
    它是舊的。落後幾天要算出來。"""
    report = _valuation_report([_row("AAA", 20, 40)])
    report["as_of"] = "2026-08-18"
    built = pipeline.build_report(report, run_date="2026-08-22")
    assert built["source_staleness"]["days"] == 4
    assert built["source_staleness"]["source_as_of"] == "2026-08-18"


def test_a_same_day_run_reports_zero_staleness():
    """排在模組二之後跑的正常情況：兩邊同一個 UTC 日期，落後 0 天。"""
    report = _valuation_report([_row("AAA", 20, 40)])
    report["as_of"] = "2026-08-22"
    built = pipeline.build_report(report, run_date="2026-08-22")
    assert built["source_staleness"]["days"] == 0


def test_stale_source_still_produces_a_report_rather_than_failing():
    """刻意不丟例外：估值報告本來就會在週末與假日落後，把它變成失敗只會讓
    排程在大多數日子紅一片。數字要在，判斷留給讀的人。"""
    report = _valuation_report([_row("AAA", 20, 40), _row("BBB", 10, 30)])
    report["as_of"] = "2026-01-01"
    built = pipeline.build_report(report, run_date="2026-08-22")
    assert built["source_staleness"]["days"] > 200
    assert len(built["top10"]) == 2


# --- storage ---------------------------------------------------------------

def _report(as_of, top10):
    return {
        "as_of": as_of,
        "generated_at": f"{as_of}T23:00:00Z",
        "counts": {"universe": 500, "candidates": len(top10), "buy": len(top10), "strong_buy": 0},
        "dispersion_stats": {"median": 30, "p75": 50, "p90": 70},
        "median_upside_pct": 10,
        "top10": top10,
        "background": [],
    }


def _top10_row(ticker, rank, upside=20, disp=30, tier="moderate"):
    return {
        "rank": rank, "ticker": ticker, "name": ticker, "sector": "Technology",
        "close": 100, "consensus_target": 120, "target_low": 90, "target_high": 150,
        "upside_pct": upside, "target_dispersion_pct": disp, "risk_tier": tier,
        "recommendation_key": "buy", "recommendation_mean": 2.0, "analyst_count": 10,
        "market_cap": 1e10, "confidence": "中",
    }


def test_first_snapshot_has_no_previous_rank_and_is_marked_new(tmp_path):
    report = _report("2026-08-11", [_top10_row("AAA", 1), _top10_row("BBB", 2)])
    saved = storage.save_report(report, data_root=str(tmp_path))
    for row in saved["top10"]:
        assert row["prev_rank"] is None
        assert row["is_new"] is True
    assert (tmp_path / "latest.json").exists()
    assert (tmp_path / "history.csv").exists()


def test_second_snapshot_tracks_rank_movement_and_new_entrants(tmp_path):
    storage.save_report(
        _report("2026-08-11", [_top10_row("AAA", 1), _top10_row("BBB", 2)]),
        data_root=str(tmp_path),
    )
    second = storage.save_report(
        _report("2026-08-18", [_top10_row("BBB", 1), _top10_row("CCC", 2)]),
        data_root=str(tmp_path),
    )
    by_ticker = {r["ticker"]: r for r in second["top10"]}
    # BBB 從第2名進步到第1名
    assert by_ticker["BBB"]["prev_rank"] == 2
    assert by_ticker["BBB"]["is_new"] is False
    # CCC 是新進榜（上次不在榜上）
    assert by_ticker["CCC"]["prev_rank"] is None
    assert by_ticker["CCC"]["is_new"] is True


def test_rerunning_the_same_as_of_upserts_instead_of_duplicating(tmp_path):
    storage.save_report(_report("2026-08-11", [_top10_row("AAA", 1)]), data_root=str(tmp_path))
    storage.save_report(_report("2026-08-11", [_top10_row("AAA", 1)]), data_root=str(tmp_path))
    import pandas as pd
    df = pd.read_csv(tmp_path / "history.csv")
    assert len(df) == 1


def test_prefix_keeps_two_universes_in_the_same_data_root(tmp_path):
    """S&P 500（無前綴）與 Nasdaq-100（"ndx100_"）共用一個 data_root，
    檔名跟彼此的「較上次」歷史都不能互相覆蓋或污染。"""
    sp500 = storage.save_report(
        _report("2026-08-17", [_top10_row("AAA", 1)]), data_root=str(tmp_path),
    )
    ndx100 = storage.save_report(
        _report("2026-08-17", [_top10_row("BBB", 1)]), data_root=str(tmp_path), prefix="ndx100_",
    )
    assert (tmp_path / "latest.json").exists()
    assert (tmp_path / "ndx100_latest.json").exists()
    assert (tmp_path / "snapshots" / "2026-08-17.json").exists()
    assert (tmp_path / "ndx100_snapshots" / "2026-08-17.json").exists()
    assert sp500["top10"][0]["ticker"] == "AAA"
    assert ndx100["top10"][0]["ticker"] == "BBB"

    # 第二週：兩邊的「較上次」歷史各自獨立累積，不會互相看到對方的名次
    second_sp500 = storage.save_report(
        _report("2026-08-24", [_top10_row("AAA", 1)]), data_root=str(tmp_path),
    )
    second_ndx100 = storage.save_report(
        _report("2026-08-24", [_top10_row("CCC", 1)]), data_root=str(tmp_path), prefix="ndx100_",
    )
    assert second_sp500["top10"][0]["is_new"] is False   # AAA 上週在 S&P 500 榜上
    assert second_ndx100["top10"][0]["is_new"] is True   # CCC 沒有出現在 ndx100 上週榜單（BBB）裡
