"""模組四（類股資金輪動）測試。

輪動指標最容易出的錯，是那些**算得出數字但意義不對**的：缺值變成 0 後
排進中間名次、用日曆天當視窗讓連假前後長度不同、拿不同區間的基準算超額
報酬、以及來源沒更新卻被讀成「資金持平」。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from liquidity_monitor.sources.etf_creation_flows import EtfSnapshot
from sector_rotation import flows, metrics, storage


def _px(n=100, start="2025-01-02", **series):
    idx = pd.bdate_range(start, periods=n)
    return pd.DataFrame({k: v(n) if callable(v) else v for k, v in series.items()}, index=idx)


META = {"XLK": ("Information Technology", "資訊科技"), "XLE": ("Energy", "能源")}


# --- 視窗與缺值 -------------------------------------------------------------

def test_window_is_measured_in_trading_days_not_calendar_days():
    """連假前後的日曆週會是不同長度的區間，跨日期比較就沒有意義。"""
    idx = pd.bdate_range("2025-01-02", periods=30)
    s = pd.Series(np.arange(100, 130, dtype=float), index=idx)
    # 5 個交易日前的值是 iloc[-6]，與日曆七天前無關
    assert metrics.window_return(s, 5) == pytest.approx((s.iloc[-1] / s.iloc[-6] - 1) * 100)


def test_insufficient_history_is_missing_not_zero():
    """XLC 2018 才成立、XLRE 2015 才成立。把缺值當成 0% 報酬，
    排名時會直接排到中間，看起來完全正常。"""
    s = pd.Series([100.0, 101.0], index=pd.bdate_range("2025-01-02", periods=2))
    assert metrics.window_return(s, 21) is None


def test_missing_values_do_not_take_a_rank():
    ranks = metrics.rank_series({"A": 5.0, "B": None, "C": 1.0})
    assert ranks == {"A": 1, "B": None, "C": 2}


def test_a_missing_sector_is_not_ranked_last():
    """把缺值當成最差，會讓「還沒成立的類股」看起來像「表現最爛的類股」。"""
    ranks = metrics.rank_series({"A": -9.0, "B": None})
    assert ranks["A"] == 1 and ranks["B"] is None


# --- 相對強弱 ---------------------------------------------------------------

def test_excess_return_uses_the_same_window_for_the_benchmark():
    """拿類股的近一月去比基準的近三月，算出來的只是兩段不同區間的差。"""
    px = _px(100, SPY=lambda n: np.linspace(100, 110, n), XLK=lambda n: np.linspace(100, 120, n),
             XLE=lambda n: np.linspace(100, 100, n))
    rows, bench = metrics.build_sector_rows(px, "SPY", META)
    xlk = next(r for r in rows if r["ticker"] == "XLK")
    for key in ("1d", "1w", "1m", "3m"):
        assert xlk["excess"][key] == pytest.approx(xlk["returns"][key] - bench[key], abs=1e-6)


def test_relative_strength_does_not_depend_on_how_much_history_was_fetched():
    """第一版把比值標準化成「起點 = 100」，而起點就是 HISTORY_DAYS 決定的那一天。
    改一個設定常數，所有類股的 RS 就整批位移、四象限歸類跟著變。

    實測 2026-08-11 的第一版結果：11 檔裡 9 檔 RS < 100，象限嚴重偏向左側，
    純粹因為那段期間大盤贏過多數類股——那不是輪動訊息，是錨點造成的假象。
    """
    idx = pd.bdate_range("2024-01-01", periods=300)
    sector = pd.Series(np.r_[np.linspace(100, 95, 150), np.linspace(95, 120, 150)], index=idx)
    bench = pd.Series(np.linspace(100, 115, 300), index=idx)

    full = metrics.relative_strength(sector, bench)
    short = metrics.relative_strength(sector.iloc[-200:], bench.iloc[-200:])
    assert full.iloc[-1] == pytest.approx(short.iloc[-1], abs=1e-9)


def test_relative_strength_is_100_when_a_sector_tracks_its_own_recent_trend():
    """RS = 100 的意思是「與自己近期的相對強弱持平」，不是「與大盤持平」。"""
    idx = pd.bdate_range("2024-01-01", periods=200)
    # 類股與大盤等比例同步 -> 比值恆定 -> 恆等於自己的移動平均
    sector = pd.Series(np.linspace(100, 150, 200), index=idx)
    bench = pd.Series(np.linspace(50, 75, 200), index=idx)
    assert metrics.relative_strength(sector, bench).iloc[-1] == pytest.approx(100.0, abs=1e-6)


def test_relative_strength_only_uses_days_where_both_series_exist():
    """用前向填補補其中一邊的缺口，會造出「類股不動而基準在動」的假訊號。"""
    idx = pd.bdate_range("2025-01-02", periods=10)
    sector = pd.Series([np.nan] * 5 + [100.0, 101, 102, 103, 104], index=idx)
    bench = pd.Series(np.linspace(100, 110, 10), index=idx)
    # 移動平均需要足夠樣本，缺口那幾天不該被填補進來
    assert len(metrics.price_ratio(sector, bench)) == 5


def test_relative_strength_survives_a_flat_market():
    """大盤不動而類股在漲時，RS 要高於 100，且不能除到零。"""
    idx = pd.bdate_range("2025-01-02", periods=60)
    rs = metrics.relative_strength(pd.Series(np.linspace(100, 130, 60), index=idx),
                                   pd.Series([100.0] * 60, index=idx))
    assert rs.iloc[-1] > 100


# --- 輪動象限 ---------------------------------------------------------------

@pytest.mark.parametrize("rs,mom,expected", [
    (105, 2, "領漲"),
    (105, -2, "轉弱"),
    (95, 2, "改善"),
    (95, -2, "落後"),
    (None, 1, "暫缺"),
    (105, None, "暫缺"),
])
def test_quadrants_describe_where_money_is_going_not_just_past_returns(rs, mom, expected):
    """漲幅排行只告訴你過去誰強；象限告訴你資金正在往哪走。
    「轉弱」與「改善」正是輪動實際發生的地方。"""
    assert metrics.rotation_signal(rs, mom) == expected


def test_breadth_shows_whether_a_rally_is_broad_or_narrow():
    """單一指數漲跌看不出是全面上漲還是少數幾檔撐著。"""
    rows = [{"returns": {"1d": v}} for v in (1.0, 0.5, -0.2, -1.0, None)]
    b = metrics.breadth(rows, "1d")
    assert (b["up"], b["down"], b["total"]) == (2, 2, 4)


def test_rank_changes_are_empty_without_a_previous_observation():
    """沒有前一次紀錄時不能假裝有變動。"""
    rows = [{"ticker": "XLK", "sector_zh": "資訊科技", "rank": {"1w": 1}}]
    assert metrics.rotation_changes(rows, {}, "1w") == []


def test_rank_change_sign_means_moving_up():
    rows = [{"ticker": "XLK", "sector_zh": "資訊科技", "rank": {"1w": 1}}]
    out = metrics.rotation_changes(rows, {"XLK": 5}, "1w")
    assert out[0]["change"] == 4          # 從第 5 名爬到第 1 名


# --- 資金流 -----------------------------------------------------------------

def _snap(t, shares=None, aum=None, close=100.0, as_of="2026-08-11"):
    return EtfSnapshot(as_of=as_of, ticker=t, shares_outstanding=shares,
                       total_assets=aum, close=close)


def _prev(shares=None, aum=None, close=100.0, as_of="2026-08-10"):
    return {"as_of": as_of, "shares_outstanding": shares, "total_assets": aum, "close": close}


def test_flow_is_reported_per_sector_not_aggregated():
    """輪動要看的正是「錢從哪個類股出來、進到哪個類股」。"""
    today = [_snap("XLK", shares=1_010_000, aum=101_000_000),
             _snap("XLE", shares=990_000, aum=99_000_000)]
    prev = {"XLK": _prev(shares=1_000_000, aum=100_000_000),
            "XLE": _prev(shares=1_000_000, aum=100_000_000)}
    out, _ = flows.compute_sector_flows(today, prev)
    assert out["XLK"]["flow_musd"] > 0 and out["XLE"]["flow_musd"] < 0


def test_flow_is_expressed_as_a_share_of_the_funds_own_assets():
    """XLK 的規模是 XLRE 的十幾倍。比絕對金額等於在比誰規模大，
    不是在比誰的資金動能強。"""
    today = [_snap("BIG", shares=10_100_000, aum=1_010_000_000),
             _snap("SMALL", shares=1_010_000, aum=101_000_000)]
    prev = {"BIG": _prev(shares=10_000_000, aum=1_000_000_000),
            "SMALL": _prev(shares=1_000_000, aum=100_000_000)}
    out, _ = flows.compute_sector_flows(today, prev)
    assert out["BIG"]["flow_musd"] > out["SMALL"]["flow_musd"]         # 金額差十倍
    assert out["BIG"]["flow_pct_of_aum"] == pytest.approx(out["SMALL"]["flow_pct_of_aum"], abs=1e-6)


def test_identical_source_values_are_stale_data_not_flat_flows():
    """實測 Yahoo 的 totalAssets 對 ETF 不是每日更新。回報 0 會被讀成
    「資金持平」——那是假訊號，而且看起來完全正常。

    三欄全部相同時，最先攔下來的是「同一個交易日」那條（收盤價也相同）；
    無論訊息是哪一句，重點是**不得回報成資金持平**。
    """
    today = [_snap(t, aum=100_000_000, close=100.0) for t in ("XLK", "XLF", "XLV")]
    prev = {t: _prev(aum=100_000_000, close=100.0) for t in ("XLK", "XLF", "XLV")}
    out, _ = flows.compute_sector_flows(today, prev)
    assert flows.stale_reason(out) is not None


def test_frozen_assets_while_prices_moved_is_impossible():
    today = [_snap(t, aum=100_000_000, close=110.0) for t in ("XLK", "XLF")]
    prev = {t: _prev(aum=100_000_000, close=100.0) for t in ("XLK", "XLF")}
    out, _ = flows.compute_sector_flows(today, prev)
    assert "未更新" in flows.stale_reason(out)


def test_a_genuinely_small_flow_is_not_mistaken_for_stale_data():
    """真的只是流量很小，不能跟「資料沒更新」混為一談。

    收盤價要給不同的值：真正的隔一天不可能 11 檔全部收在同一個價位，
    用相同收盤價當測資等於在測一個不會發生的情境。
    """
    today = [_snap(t, aum=100_200_000, close=100.5) for t in ("XLK", "XLF")]
    prev = {t: _prev(aum=100_000_000, close=100.0) for t in ("XLK", "XLF")}
    out, _ = flows.compute_sector_flows(today, prev)
    assert flows.stale_reason(out) is None
    assert out["XLK"]["flow_musd"] != 0


def test_stale_observations_are_skipped_rather_than_averaged_in():
    """跨越太多天的觀測若照樣採計，會把多日累積的申贖當成單日訊號。"""
    today = [_snap("XLK", shares=1_010_000, aum=101_000_000)]
    prev = {"XLK": _prev(shares=1_000_000, aum=100_000_000, as_of="2026-06-01")}
    out, skipped = flows.compute_sector_flows(today, prev)
    assert out == {}
    assert "過久不採計" in skipped[0][1]


def test_internal_fields_do_not_leak_into_the_report():
    today = [_snap("XLK", shares=1_010_000, aum=101_000_000)]
    prev = {"XLK": _prev(shares=1_000_000, aum=100_000_000)}
    out, _ = flows.compute_sector_flows(today, prev)
    assert all(not k.startswith("_") for k in flows.strip_internals(out)["XLK"])


# --- 歷史累積 ---------------------------------------------------------------

def test_rank_history_roundtrip_and_same_day_rerun_does_not_duplicate(tmp_path):
    p = str(tmp_path / "h.csv")
    storage.append_ranks("2026-08-10", {"XLK": 1, "XLE": 2}, path=p)
    storage.append_ranks("2026-08-10", {"XLK": 3, "XLE": 1}, path=p)   # 同日重跑
    assert len(pd.read_csv(p)) == 1
    storage.append_ranks("2026-08-11", {"XLK": 2, "XLE": 1}, path=p)
    assert storage.load_previous_ranks(p, before="2026-08-11") == {"XLK": 3, "XLE": 1}


def test_missing_history_file_is_empty_not_an_error(tmp_path):
    assert storage.load_previous_ranks(str(tmp_path / "nope.csv")) == {}
    assert storage.load_previous_snapshots(str(tmp_path / "nope.csv")) == {}


def test_identical_closes_across_all_etfs_mean_it_is_the_same_trading_day():
    """實測漏掉的情況：同一個交易日跑第二次。

    淨資產與收盤價全部相同、但股數欄位剛好從空值變成有值，於是
    「三欄全部相同」不成立、「淨資產不動但價格有動」也不成立——
    兩道檢查都沒攔住，11 檔全部回報 +0.000%，畫面讀成「資金持平」。
    收盤價全數相同就是同一筆市場觀測，根本沒有「隔日」可言。
    """
    today = [_snap(t, shares=1_000_000, aum=100_000_000, close=100.0)
             for t in ("XLK", "XLF", "XLV")]
    # 前一次的股數是空的（Yahoo 該欄位時有時無），其餘完全相同
    prev = {t: _prev(shares=None, aum=100_000_000, close=100.0)
            for t in ("XLK", "XLF", "XLV")}
    out, _ = flows.compute_sector_flows(today, prev)
    reason = flows.stale_reason(out)
    assert reason is not None and "同一個交易日" in reason


def test_a_real_next_day_observation_is_not_blocked():
    """價格有動就是新的一天，不能被上面那條誤擋。"""
    today = [_snap(t, aum=101_000_000, close=101.0) for t in ("XLK", "XLF")]
    prev = {t: _prev(aum=100_000_000, close=100.0) for t in ("XLK", "XLF")}
    out, _ = flows.compute_sector_flows(today, prev)
    assert flows.stale_reason(out) is None


def test_snapshots_are_keyed_by_market_close_date_not_run_date(monkeypatch, tmp_path):
    """同一個交易日重跑要覆蓋，不能製造出一組假的「隔日比較」。"""
    import pandas as pd
    from sector_rotation import report as rep

    idx = pd.bdate_range("2026-01-02", periods=120)
    px = pd.DataFrame({t: np.linspace(100, 120, 120)
                       for t in ("SPY", "XLK", "XLF", "XLV", "XLY", "XLC",
                                 "XLI", "XLP", "XLE", "XLU", "XLRE", "XLB")}, index=idx)
    monkeypatch.setattr(rep.storage, "SNAPSHOT_PATH", str(tmp_path / "s.csv"))
    monkeypatch.setattr(rep, "fetch_snapshots", lambda *a, **k: [])
    monkeypatch.setattr(rep.storage, "save_snapshots", lambda *a, **k: None)
    monkeypatch.setattr(rep.storage, "load_previous_snapshots", lambda **k: {})
    monkeypatch.setattr(rep.storage, "load_previous_ranks", lambda **k: {})
    monkeypatch.setattr(rep.storage, "append_ranks", lambda *a, **k: None)

    # 執行日期比最後一個交易日晚（例如週末補跑）
    out = rep.build_report(as_of="2026-06-20", prices=px)
    assert out["close_date"] == str(idx[-1].date())
    assert out["close_date"] != out["as_of"]
