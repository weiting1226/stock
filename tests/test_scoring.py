"""驗證 scoring.py 各子項門檻是否與 v3.0 文件表格一致，以及綜合分數/燈號/
權重重分配/結構性警示的邏輯正確。"""
from __future__ import annotations

from liquidity_monitor import scoring


def test_fed_bs_3m_chg_buckets():
    assert scoring.score_fed_bs_3m_chg(2.5) == 2
    assert scoring.score_fed_bs_3m_chg(1.0) == 1
    assert scoring.score_fed_bs_3m_chg(0.0) == 0
    assert scoring.score_fed_bs_3m_chg(-1.0) == -1
    assert scoring.score_fed_bs_3m_chg(-2.5) == -2
    assert scoring.score_fed_bs_3m_chg(None) is None


def test_on_rrp_dormant_is_neutral_not_negative():
    # 文件第76-77行例外條款：結構性休眠不得判為 -2
    assert scoring.score_on_rrp_pctile(5.0, structurally_dormant=True) == 0
    assert scoring.score_on_rrp_pctile(5.0, structurally_dormant=False) == -2
    assert scoring.score_on_rrp_pctile(85.0) == 1


def test_hy_oas_bell_curve():
    assert scoring.score_hy_oas_level(7.0) == -2
    assert scoring.score_hy_oas_level(5.0) == -1
    assert scoring.score_hy_oas_level(3.5) == 1
    assert scoring.score_hy_oas_level(2.8) == 0
    assert scoring.score_hy_oas_level(2.0) == -1  # 過度壓縮，鐘型收斂為負分而非+2


def test_margin_debt_yoy_bell_curve():
    assert scoring.score_margin_debt_yoy(-20) == -2
    assert scoring.score_margin_debt_yoy(-5) == -1
    assert scoring.score_margin_debt_yoy(10) == 1
    assert scoring.score_margin_debt_yoy(30) == 0
    assert scoring.score_margin_debt_yoy(50) == -1  # 槓桿泡沫化，不再是+2


def test_usdjpy_yen_spike_is_penalized():
    # 正值 = 日圓貶值(USD/JPY上升)；負值 = 日圓升值(USD/JPY下跌，carry trade解體風險)
    assert scoring.score_usdjpy_20d_chg(4) == 1
    assert scoring.score_usdjpy_20d_chg(0) == 0
    assert scoring.score_usdjpy_20d_chg(-4) == -1
    assert scoring.score_usdjpy_20d_chg(-6) == -2


def test_composite_score_weight_reallocation_when_category_missing():
    # 只有①有分數，其餘全暫缺 -> 綜合分數應等於①的類別平均分(權重100%重分配)
    item_scores = {k: None for cat in scoring.CATEGORY_ITEMS.values() for k in cat}
    item_scores["fed_bs_3m_chg"] = 2
    item_scores["on_rrp_pctile"] = 2
    item_scores["m2_yoy_3m_chg"] = 2
    composite, cat_avgs, weights = scoring.composite_score(item_scores)
    assert composite == 2.0
    assert weights == {"monetary_liquidity": 1.0}


def test_light_bands_cover_full_range():
    assert scoring.light_for_score(1.5) == "🟢🟢 深綠"
    assert scoring.light_for_score(0.5) == "🟢 綠"
    assert scoring.light_for_score(0.0) == "🟡 黃"
    assert scoring.light_for_score(-0.5) == "🔴 紅"
    assert scoring.light_for_score(-1.5) == "🔴🔴 深紅"


def test_position_ladder_matches_lights():
    assert scoring.position_for_score(1.5) == ("50% TQQQ + 50% QQQ", 2.0)
    assert scoring.position_for_score(-1.5) == ("100% SGOV", 0.0)


def test_dominance_check_flags_single_category():
    item_scores = {k: None for cat in scoring.CATEGORY_ITEMS.values() for k in cat}
    item_scores["fomc_decision"] = 2
    item_scores["fedwatch_path"] = 2
    item_scores["yield30y_60d_momentum"] = 2
    composite, cat_avgs, weights = scoring.composite_score(item_scores)
    result = scoring.dominance_check(item_scores, cat_avgs, weights)
    assert result is not None
    assert result["category"] == "policy_direction"
