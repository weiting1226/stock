from __future__ import annotations

import numpy as np
import pandas as pd

from liquidity_monitor import gates


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
