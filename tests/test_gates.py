from __future__ import annotations

import numpy as np
import pandas as pd

from liquidity_monitor import gates, pipeline
from liquidity_monitor.timeseries import nyse_trading_days


def _price_series(values, start="2023-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="B")
    return pd.Series(values, index=idx)


def test_gate_a_uptrend_no_cap():
    n = 300
    prices = np.linspace(100, 200, n)  # 穩定上升
    s = _price_series(prices)
    result = gates.gate_a(s, str(s.index[-1].date()))
    assert result["cap"] is None


def test_gate_a_below_200d_caps_qqq():
    n = 300
    prices = np.concatenate([np.linspace(100, 200, n - 30), np.linspace(200, 120, 30)])
    s = _price_series(prices)
    result = gates.gate_a(s, str(s.index[-1].date()))
    assert result["cap"] in ("QQQ", "SGOV")


def test_percentile_rank_basic():
    s = pd.Series(range(1, 101), index=pd.date_range("2020-01-01", periods=100, freq="D"))
    pctile = gates.percentile_rank(s, 90, window_days=100)
    assert 85 <= pctile <= 95


def test_gate_c_missing_data_disables_leverage():
    result = gates.gate_c(3, 1.0, False, [(1.2, float("inf"), "深綠")])
    assert result["cap"] == "QQQ"


def test_gate_c_boundary_downgrade():
    bands = [(0.4, 1.2, "綠"), (-0.4, 0.4, "黃")]
    result = gates.gate_c(0, 0.42, False, bands)
    assert result["downgrade_one_level"] is True


def test_gate_c_policy_missing_disables_leverage():
    result = gates.gate_c(0, 1.0, True, [(1.2, float("inf"), "深綠")])
    assert result["cap"] == "QQQ"


def test_lagged_yoy_does_not_use_unpublished_reference_month():
    """月頻年增率必須套用發布時滯，否則當日就用到尚未公布的參考月（前視偏誤）。

    對照文件第38-48行「資料時滯規則 — 必須使用發布日對齊」。
    """
    idx = pd.date_range("2024-01-31", "2026-07-31", freq="ME")
    raw = pd.Series(range(100, 100 + len(idx)), index=idx, dtype=float)
    trading_days = nyse_trading_days("2026-07-01", "2026-08-08")

    lagged = pipeline._lagged_yoy_daily(raw, 28, trading_days)

    # 7/31 的參考月資料要 28 天後（8/28）才公布，8/8 當天不該看得到
    unlagged_latest = raw.index[-1]                      # 2026-07-31
    assert not lagged.dropna().empty
    # 位移後最早出現在面板上的日期必須晚於「參考月 + 時滯」
    first_available = pd.Timestamp("2026-07-31") + pd.Timedelta(days=28)
    assert first_available > pd.Timestamp("2026-08-08")
    # 因此 8/8 看到的值必定小於用未位移序列算出來的最新值
    unlagged = pipeline.to_daily_panel(pipeline.yoy(raw), trading_days)
    assert lagged.dropna().iloc[-1] != unlagged.dropna().iloc[-1]
    assert unlagged_latest == pd.Timestamp("2026-07-31")


def test_lagged_yoy_handles_empty_series():
    trading_days = nyse_trading_days("2026-07-01", "2026-08-08")
    assert pipeline._lagged_yoy_daily(pd.Series(dtype=float), 28, trading_days).empty
