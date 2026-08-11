"""模組三（回測）測試。

回測最容易出的錯不是會拋例外的那種，而是**跑得出一條很漂亮的曲線、
而那條曲線用到了當時還不知道的資訊**。因此測試的重心放在：

1. 前視偏誤：訊號與成交的時間差、發布時滯、滾動視窗只看過去
2. 階梯對照表的完整性（少一個對應就會安靜配錯部位）
3. 現金銜接不得產生假跳空
4. 「沒有資訊」與「分數為零」必須分得開
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest import config, engine, metrics, panel, strategy


def _px(n=300, start="2015-01-02"):
    idx = pd.bdate_range(start, periods=n)
    return pd.DataFrame({
        "QQQ": np.linspace(100, 200, n),
        "TQQQ": np.linspace(100, 400, n),
        "SGOV": np.linspace(100, 104, n),
    }, index=idx)


def _weights(idx, qqq=1.0, tqqq=0.0, sgov=0.0):
    return pd.DataFrame({"QQQ": qqq, "TQQQ": tqqq, "SGOV": sgov}, index=idx)


# --- 前視偏誤 ---------------------------------------------------------------

def test_signal_is_traded_the_next_day_not_the_same_day():
    """當日收盤算出的訊號只能在下一個交易日成交。

    用當日收盤價成交等於拿當天才知道的資訊去買當天的收盤價——這種回測
    一定很漂亮，而且完全無法複製。
    """
    px = _px(10)
    w = _weights(px.index, qqq=0.0, sgov=1.0)
    w.loc[px.index[4], ["QQQ", "SGOV"]] = [1.0, 0.0]      # 第 5 天發出買進訊號
    w.loc[px.index[5]:, ["QQQ", "SGOV"]] = [1.0, 0.0]

    r = engine.simulate(w, px, cost_bps_per_side=0)
    # 第 5 天（index 4）當天仍是現金，第 6 天才持有 QQQ
    assert r.loc[px.index[4], "w_QQQ"] == 0.0
    assert r.loc[px.index[5], "w_QQQ"] == 1.0


def test_a_perfect_foresight_signal_cannot_capture_the_same_day_move():
    """反證：就算給一個「隔天大漲就今天買進」的完美訊號，
    因為要隔天才成交，也吃不到那一天的漲幅。若吃得到，就是位移沒生效。"""
    idx = pd.bdate_range("2015-01-02", periods=6)
    prices = pd.DataFrame({
        "QQQ": [100, 100, 100, 200, 200, 200],      # 第 4 天翻倍
        "TQQQ": [100.0] * 6,
        "SGOV": [100.0] * 6,
    }, index=idx, dtype=float)
    w = _weights(idx, qqq=0.0, sgov=1.0)
    w.loc[idx[2], ["QQQ", "SGOV"]] = [1.0, 0.0]     # 在漲之前一天就知道
    w.loc[idx[3]:, ["QQQ", "SGOV"]] = [1.0, 0.0]

    r = engine.simulate(w, prices, cost_bps_per_side=0)
    # 第 4 天（idx[3]）的 +100% 由第 3 天的訊號決定，位移後恰好吃得到；
    # 但第 3 天（idx[2]）當天不能有部位
    assert r.loc[idx[2], "w_QQQ"] == 0.0


def test_rolling_percentile_never_looks_at_future_values():
    """百分位若用全期分布算，2013 年那一天就會用到 2020 年才發生的事。"""
    s = pd.Series([1, 2, 3, 100, 4, 5], dtype=float)
    out = panel.rolling_percentile(s, window=3, min_obs=3)
    # 第 3 個位置（值=3）只看得到 [1,2,3]，是區間最大 -> 66.7%
    assert out.iloc[2] == pytest.approx(200 / 3)
    # 若偷看未來的 100，3 就不會是最大值，百分位會低很多
    assert out.iloc[2] > 50


def test_publish_lag_keeps_unreleased_data_out_of_the_panel():
    """月頻資料要到發布日之後才算「已知」。沒有時滯的話，模型在月底
    就看得到那個月的 M2——而真實世界要再等四週。"""
    monthly = pd.Series([100.0, 110.0],
                        index=pd.to_datetime(["2020-01-01", "2020-02-01"]))
    days = pd.bdate_range("2020-01-01", "2020-03-31")
    from liquidity_monitor.timeseries import apply_publish_lag, to_daily_panel
    lagged = to_daily_panel(apply_publish_lag(monthly, 28), days)
    assert pd.isna(lagged.loc["2020-01-15"]) or lagged.loc["2020-01-15"] != 100.0
    assert lagged.loc["2020-02-20"] == 100.0        # 1月的值，2月才看得到
    assert lagged.loc["2020-03-16"] == 110.0        # 2月的值，3月才看得到


# --- 階梯對照 ---------------------------------------------------------------

def test_every_ladder_rung_has_a_weight_mapping():
    """階梯文案改一個字，回測就會安靜地配出錯誤的部位。這個測試讓它改了就失敗。"""
    from liquidity_monitor.config import POSITION_LADDER
    for _lo, _hi, alloc, _lev in POSITION_LADDER:
        assert alloc in config.LADDER_WEIGHTS, f"階梯「{alloc}」沒有對應權重"


def test_ladder_weights_always_sum_to_one():
    for alloc, w in config.LADDER_WEIGHTS.items():
        assert sum(w.values()) == pytest.approx(1.0), alloc


def test_unknown_ladder_label_fails_loudly():
    """找不到對應時要拋錯，不能給預設值——預設值會跑出一條看起來很正常的曲線。"""
    from unittest import mock
    with mock.patch.object(strategy.scoring, "position_for_score", return_value=("100% 比特幣", 3.0)):
        with pytest.raises(KeyError, match="沒有對應的權重設定"):
            strategy.ladder_weights(0.0)


@pytest.mark.parametrize("score,expected", [
    (1.5, {"TQQQ": 0.5, "QQQ": 0.5}),
    (0.8, {"TQQQ": 0.25, "QQQ": 0.75}),
    (0.0, {"QQQ": 1.0}),
    (-0.8, {"SGOV": 1.0}),
    (-2.0, {"SGOV": 1.0}),
])
def test_ladder_maps_scores_to_the_documented_allocations(score, expected):
    assert strategy.ladder_weights(score) == expected


# --- 缺資訊 vs 分數為零 -----------------------------------------------------

def test_no_usable_indicator_is_not_a_zero_score():
    """0 分在階梯上是「100% QQQ」，那是一個有方向的決定，不是「不知道」。"""
    idx = pd.bdate_range("2015-01-02", periods=3)
    empty = pd.DataFrame({"vix_level": [np.nan] * 3}, index=idx)
    comp = strategy.composite_panel(empty)
    assert comp["composite_score"].isna().all()


def test_days_without_a_signal_are_not_traded():
    """訊號還沒出現的期間不該有部位（也不該被當成持有現金賺利息）。"""
    px = _px(10)
    w = _weights(px.index)
    w.iloc[:4] = np.nan
    r = engine.simulate(w, px, cost_bps_per_side=0)
    assert r.index[0] >= px.index[4]


# --- 成本與換手 -------------------------------------------------------------

def test_cost_is_charged_on_the_switch_not_every_day():
    """階梯是離散的，每日照權重微調會產生大量無意義換手。"""
    px = _px(20)
    w = _weights(px.index)
    w.loc[px.index[10]:, ["QQQ", "TQQQ"]] = [0.0, 1.0]
    r = engine.simulate(w, px, cost_bps_per_side=10)
    charged = (r["cost"] > 0).sum()
    assert charged == 2, "只該在建倉與那一次切換時收費"


def test_opening_the_position_is_a_full_purchase_not_half():
    """第一天沒有前一日部位，是純買進而非換倉，除以二會少算一半建倉成本。"""
    px = _px(5)
    r = engine.simulate(_weights(px.index), px, cost_bps_per_side=100)
    assert r["turnover"].iloc[0] == pytest.approx(1.0)


def test_costs_reduce_returns():
    px = _px(60)
    w = _weights(px.index)
    w.loc[px.index[20]:, ["QQQ", "TQQQ"]] = [0.0, 1.0]
    free = engine.simulate(w, px, cost_bps_per_side=0)
    paid = engine.simulate(w, px, cost_bps_per_side=50)
    assert paid["equity"].iloc[-1] < free["equity"].iloc[-1]


# --- 現金銜接 ---------------------------------------------------------------

def test_cash_splice_does_not_create_a_phantom_jump():
    """SGOV 約 100 元、BIL 約 91 元。直接把兩段價格拼起來，交界日會冒出
    一筆將近 10% 的「報酬」——現金部位的曲線近乎直線，這種錯誤肉眼看不出來。"""
    from backtest.data import splice_cash
    idx = pd.bdate_range("2018-01-01", periods=800)
    bil = pd.Series(91 * np.cumprod(1 + np.full(800, 0.00008)), index=idx)
    sgov_idx = idx[400:]
    sgov = pd.Series(100 * np.cumprod(1 + np.full(len(sgov_idx), 0.00008)), index=sgov_idx)
    spliced, info = splice_cash(pd.DataFrame({"SGOV": sgov, "BIL": bil}))
    assert spliced.pct_change().abs().max() < 0.001
    assert info["basis"] == "BIL→SGOV"


def test_cash_splice_refuses_instruments_that_are_not_interchangeable():
    """兩檔差太多就不能互換。現金線系統性偏移會影響整個回測，而且看不出來。"""
    from backtest.data import splice_cash
    idx = pd.bdate_range("2018-01-01", periods=800)
    bil = pd.Series(91 * np.cumprod(1 + np.full(800, 0.0004)), index=idx)   # 年化約 10%
    sgov_idx = idx[400:]
    sgov = pd.Series(100 * np.cumprod(1 + np.full(len(sgov_idx), 0.00002)), index=sgov_idx)
    with pytest.raises(ValueError, match="不可互換"):
        splice_cash(pd.DataFrame({"SGOV": sgov, "BIL": bil}))


# --- 閘門 -------------------------------------------------------------------

def test_gate_a_forces_cash_when_price_and_both_averages_break_down():
    n = 400
    idx = pd.bdate_range("2015-01-02", periods=n)
    # 前段緩漲、後段急跌，讓價格與 50 日線都跌破 200 日線
    close = pd.Series(np.r_[np.linspace(100, 200, 300), np.linspace(200, 80, 100)], index=idx)
    out = strategy.gate_a_panel(close)
    assert out["cap"].iloc[-1] == "SGOV"
    assert pd.isna(out["cap"].iloc[250])         # 上升段不設限


def test_gate_a_is_unknown_before_the_200day_average_exists():
    """均線還沒算得出來時是「不知道」，不是「未觸發」。"""
    idx = pd.bdate_range("2015-01-02", periods=50)
    out = strategy.gate_a_panel(pd.Series(np.linspace(100, 110, 50), index=idx))
    assert out["cap"].isna().all()
    assert (out["status"] == "unknown").all()     # 與「未觸發」要分得開


def test_gate_b_does_not_use_2026_thresholds_on_historical_days():
    """GATE_B_FALLBACK_THRESHOLDS 是依 2026 年分布訂的。拿它去判 2013 年，
    等於讓當時的決策用到十幾年後才知道的資訊。歷史不足時該項記為未觸發。"""
    idx = pd.bdate_range("2015-01-02", periods=100)
    raw = pd.DataFrame({
        "margin_debt_yoy": np.full(100, 99.0),    # 遠高於 2026 參考門檻 22%
        "hy_oas_level": np.full(100, 1.0),        # 遠低於參考門檻 3.1%
        "vix_level": np.full(100, 10.0),
        "skew": np.full(100, 160.0),
    }, index=idx)
    caps = strategy.gate_b_panel(raw)
    assert caps.isna().all()


# --- 績效指標 ---------------------------------------------------------------

def test_max_drawdown_is_measured_from_the_peak():
    eq = pd.Series([1.0, 2.0, 1.0, 1.5])
    assert metrics.max_drawdown(eq) == pytest.approx(-50.0)


def test_sharpe_is_none_without_a_risk_free_series():
    """回測橫跨零利率與 5% 兩種環境，偷偷用 0% 會讓近年的 Sharpe 好看很多。"""
    idx = pd.bdate_range("2015-01-02", periods=50)
    r = pd.Series(np.full(50, 0.001), index=idx)
    eq = (1 + r).cumprod()
    assert metrics.summarize(r, eq, rf_returns=None)["sharpe"] is None


def test_policy_category_is_excluded_from_the_backtest():
    """使用者指定回測不考慮政策指標。"""
    used = strategy.backtestable_items()
    from liquidity_monitor.config import CATEGORY_ITEMS
    for item in CATEGORY_ITEMS["policy_direction"]:
        assert item not in used


def test_items_without_history_are_excluded():
    used = strategy.backtestable_items()
    assert "ndx_breadth_200d" not in used     # 需要當時的成分股名單
    assert "etf_fund_flow" not in used        # 沒有回溯資料


# --- 端對端 -----------------------------------------------------------------

def _synthetic_sources(n_years=6, seed=7):
    """造一組形狀合理的假資料，讓整條流程跑得起來（不驗證數字，只驗證接得上）。"""
    rng = np.random.default_rng(seed)
    end = pd.Timestamp("2026-06-30")
    days = pd.bdate_range(end - pd.DateOffset(years=n_years + 3), end)

    def walk(start, vol, drift=0.0):
        return pd.Series(start * np.cumprod(1 + rng.normal(drift, vol, len(days))), index=days)

    qqq = walk(100, 0.012, 0.0005)
    prices = pd.DataFrame({
        "QQQ": qqq,
        "TQQQ": 100 * (1 + 3 * qqq.pct_change().fillna(0)).cumprod(),
        "SGOV": pd.Series(100 * np.cumprod(1 + np.full(len(days), 0.00008)), index=days),
        "BIL": pd.Series(91 * np.cumprod(1 + np.full(len(days), 0.00008)), index=days),
    })
    market = {
        "vix": walk(18, 0.05), "vix3m": walk(20, 0.04), "move": walk(90, 0.03),
        "ndx": qqq * 250, "dxy": walk(100, 0.004), "usdjpy": walk(140, 0.005),
        "skew": walk(140, 0.02), "vix9d": walk(17, 0.06),
        "gold": walk(1800, 0.008), "tlt": walk(95, 0.007),
    }
    monthly = pd.date_range(days[0], days[-1], freq="MS")
    fred_panel = {
        "WALCL": pd.Series(np.linspace(7e6, 6e6, len(monthly)), index=monthly),
        "M2SL": pd.Series(np.linspace(19e3, 21e3, len(monthly)), index=monthly),
        "RRPONTSYD": pd.Series(np.linspace(2e12, 1e9, len(days)), index=days),
        "BAMLH0A0HYM2": walk(3.5, 0.01),
        "DGS2": walk(3.5, 0.01), "DGS10": walk(4.0, 0.01),
        "DGS30": walk(4.3, 0.01), "DGS1": walk(3.8, 0.01),
        "SOFR": walk(4.0, 0.005), "IORB": walk(4.0, 0.004),
        "DTWEXBGS": walk(120, 0.003), "DFEDTARU": walk(4.0, 0.001),
        "DFEDTARL": walk(3.75, 0.001), "GDP": pd.Series(np.linspace(25e3, 29e3, len(monthly)), index=monthly),
    }
    margin = pd.Series(np.linspace(700e3, 1100e3, len(monthly)), index=monthly)
    return prices, market, fred_panel, margin


def test_end_to_end_backtest_produces_a_complete_report(monkeypatch):
    """整條流程接得上，而且報告一定要交代「哪些指標沒算進去」。"""
    from backtest import data as bt_data, run as bt_run
    prices, market, fred_panel, margin = _synthetic_sources()

    monkeypatch.setattr(bt_data, "fetch_prices", lambda *a, **k: prices)
    monkeypatch.setattr(bt_data, "fetch_market_panel", lambda *a, **k: market)
    monkeypatch.setattr(bt_data, "fetch_fred_panel", lambda *a, **k: fred_panel)
    monkeypatch.setattr(bt_data, "fetch_margin_debt", lambda *a, **k: margin)

    report = bt_run.run_backtest(as_of="2026-06-30", years=5)

    assert report["period"]["trading_days"] > 1000
    for key in ("strategy", "strategy_gross", "qqq", "tqqq"):
        assert report["metrics"][key]["cagr_pct"] is not None
    # 排除的項目必須逐項列出來，不能默默少算
    excluded = {i["key"] for i in report["coverage"]["items_excluded_by_request"]}
    assert excluded == {"fomc_decision", "fedwatch_path", "yield30y_60d_momentum"}
    assert {i["key"] for i in report["coverage"]["items_unavailable"]} == {
        "ndx_breadth_200d", "etf_fund_flow"}
    # ⑥ 不得出現在採用的類別裡
    assert "policy_direction" not in {c["key"] for c in report["coverage"]["categories_used"]}
    # 圖表資料長度要一致，否則畫出來會錯位
    n = len(report["chart"]["dates"])
    assert len(report["chart"]["equity"]["strategy"]) == n
    assert all(len(v) == n for v in report["chart"]["indicator_scores"].values())
    assert all(len(v) == n for v in report["chart"]["category_scores"].values())


def test_report_is_json_serialisable(monkeypatch):
    """NaN 與 numpy 型別若漏掉，前端會拿到壞掉的 JSON 而畫面只是一片空白。"""
    import json
    from backtest import data as bt_data, run as bt_run
    prices, market, fred_panel, margin = _synthetic_sources()
    monkeypatch.setattr(bt_data, "fetch_prices", lambda *a, **k: prices)
    monkeypatch.setattr(bt_data, "fetch_market_panel", lambda *a, **k: market)
    monkeypatch.setattr(bt_data, "fetch_fred_panel", lambda *a, **k: fred_panel)
    monkeypatch.setattr(bt_data, "fetch_margin_debt", lambda *a, **k: margin)

    report = bt_run.run_backtest(as_of="2026-06-30", years=5)
    text = json.dumps(report, ensure_ascii=False)
    assert "NaN" not in text and "Infinity" not in text
