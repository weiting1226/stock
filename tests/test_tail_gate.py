"""模組六（尾部脆弱度雙閘門）的測試。

接線說明點名了三種**不會報錯**的失效方式，這份測試主要就是守它們：

  規則 1 違反  模組把部位加上去 —— 症狀是「回測變好看了」
  規則 2 違反  狀態沒跨日保存 —— 症狀是快閘永遠不觸發，畫面一切正常
  規則 3 違反  用了未調整價 —— 症狀是慢閘長期偏紅，沒有人起疑

三者的共同點：正常路徑全部通過，錯誤只在真正需要這個模組的那天才顯現。
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from tail_gate import credit_proxy, crowding, daily_log, ev, state as tstate
from tail_gate.config import TQQQ_HARD_CAP
from tail_gate.controller import (
    GateState,
    TailFragilityGate,
    _assert_veto_only,
    assert_state_consistent,
)
from tail_gate.credit_proxy import CreditProxyGate, CreditProxyInputs, GateOutput
from tail_gate.crowding import CrowdingInputs, evaluate_crowding
from tail_gate.fast import FastGateInputs, evaluate_fast


def _series(values, start="2023-01-02"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def _flat(n=600, level=100.0, drift=0.0):
    return _series([level * (1 + drift) ** i for i in range(n)])


class _StubSlow:
    """慢閘的替身，讓快閘測試不受信用資料影響。"""

    def __init__(self, ceiling=1.0, regime="EXPANSION", score=0.0):
        self._out = GateOutput(regime, ceiling, score, {"stub": True})

    def evaluate(self, inputs):
        return self._out


CALM = FastGateInputs(vix9d=12.0, vix=14.0, vix3m=16.0, vvix_pctile=40.0, hy_chg20_bp=5.0)
INVERTED = FastGateInputs(vix9d=30.0, vix=26.0, vix3m=22.0, vvix_pctile=40.0, hy_chg20_bp=5.0)


# --- 規則 1：純否決 ---------------------------------------------------------

def test_the_gate_can_never_increase_an_equity_position():
    """違反這條時的症狀是「績效變好了」——模組在某些日子把部位加上去，
    回測更漂亮，然後在真正需要它的那天才發現它不是保險。"""
    gate = TailFragilityGate(slow_gate=_StubSlow(ceiling=1.0))
    out = gate.apply((0.25, 0.75, 0.0), slow=None, fast=CALM, state=GateState())
    assert out.out_alloc[0] <= 0.25 and out.out_alloc[1] <= 0.75


def test_a_veto_moves_weight_to_sgov_not_between_equities():
    gate = TailFragilityGate(slow_gate=_StubSlow(ceiling=0.5))
    out = gate.apply((0.5, 0.5, 0.0), slow=None, fast=CALM, state=GateState())
    assert out.out_alloc == (0.25, 0.25, 0.5)


def test_the_module_cannot_pull_you_back_from_sgov():
    """說明第 3 節規則 1：它不能把你從 SGOV 拉回 TQQQ。"""
    gate = TailFragilityGate(slow_gate=_StubSlow(ceiling=1.0))
    out = gate.apply((0.0, 0.0, 1.0), slow=None, fast=CALM, state=GateState())
    assert out.out_alloc == (0.0, 0.0, 1.0)


def test_the_veto_only_assertion_catches_an_added_position():
    with pytest.raises(ValueError, match="純否決"):
        _assert_veto_only((0.25, 0.75, 0.0), (0.50, 0.75, -0.25))


def test_the_tqqq_hard_cap_holds_regardless_of_any_signal():
    """說明第 8 節最後一句：50% TQQQ 的事前上限比任何訊號都重要，
    因為它不依賴任何一個需要校準的參數——而這個模組其他門檻全都沒校準。"""
    gate = TailFragilityGate(slow_gate=_StubSlow(ceiling=1.0))
    out = gate.apply((0.9, 0.1, 0.0), slow=None, fast=CALM, state=GateState())
    assert out.out_alloc[0] <= TQQQ_HARD_CAP


# --- 規則 2：跨日狀態 -------------------------------------------------------

def test_inversion_must_persist_before_it_downgrades():
    """曲線倒掛需連續 4 日才降級（說明第 3 節）。"""
    gate = TailFragilityGate(slow_gate=_StubSlow(), persist_days=4)
    st = GateState()
    seen = []
    for _ in range(5):
        out = gate.apply((0.5, 0.5, 0.0), slow=None, fast=INVERTED, state=st)
        st = out.state
        seen.append(out.detail["gated_veto"])
    assert seen == [0, 0, 0, 1, 1]


def test_forgetting_to_persist_state_silently_disables_the_fast_gate():
    """這是說明第 7 節點名的最常見失效模式。每天都傳全新的 state，
    計數器永遠從 0 重數，倒掛連續一個月也到不了 persist_days。"""
    gate = TailFragilityGate(slow_gate=_StubSlow(), persist_days=4)
    for _ in range(30):
        out = gate.apply((0.5, 0.5, 0.0), slow=None, fast=INVERTED, state=GateState())
        assert out.detail["gated_veto"] == 0
        assert out.out_alloc == (0.5, 0.5, 0.0)     # 閘門完全沒有作用


def test_the_consistency_assertion_catches_the_unsaved_state():
    """說明第 7 節建議的那一行斷言。"""
    with pytest.raises(ValueError, match="跨日狀態沒有被保存"):
        assert_state_consistent({"fg_curve_inverted": True, "state_consec_inversion": 0})


def test_a_clean_day_resets_the_inversion_counter():
    gate = TailFragilityGate(slow_gate=_StubSlow(), persist_days=4)
    st = GateState()
    for _ in range(3):
        st = gate.apply((0.5, 0.5, 0.0), slow=None, fast=INVERTED, state=st).state
    assert st.consec_inversion == 3
    st = gate.apply((0.5, 0.5, 0.0), slow=None, fast=CALM, state=st).state
    assert st.consec_inversion == 0


def test_credit_shock_is_not_delayed_by_the_persistence_gate():
    """持續期閘門只作用在期限結構那一項。信用急變是急性事件，
    等四天等於錯過——說明寫的是「曲線倒掛需連續 4 日」，不是整個快閘延後。"""
    gate = TailFragilityGate(slow_gate=_StubSlow(), persist_days=4)
    shock = FastGateInputs(vix9d=12.0, vix=14.0, vix3m=16.0,
                           vvix_pctile=40.0, hy_chg20_bp=200.0)
    out = gate.apply((0.5, 0.5, 0.0), slow=None, fast=shock, state=GateState())
    assert out.detail["gated_veto"] == 1


def test_state_round_trips_through_disk(tmp_path):
    p = str(tmp_path / "state.json")
    tstate.save_state(GateState(active_veto=2, clean_days=1, consec_inversion=5),
                      "2026-08-12", path=p)
    st, meta = tstate.load_state(p)
    assert (st.active_veto, st.consec_inversion) == (2, 5)
    assert meta["found"] and meta["as_of"] == "2026-08-12"


def test_a_corrupt_state_file_is_not_silently_treated_as_a_fresh_start(tmp_path):
    """壞掉的狀態檔與首次執行的症狀一樣（計數器都是 0），必須分得出來。"""
    p = tmp_path / "state.json"
    p.write_text("{not json", encoding="utf-8")
    st, meta = tstate.load_state(str(p))
    assert st.consec_inversion == 0
    assert not meta["found"] and "無法解析" in meta["reason"]


def test_a_stale_state_file_is_reported_in_days(tmp_path):
    p = str(tmp_path / "state.json")
    tstate.save_state(GateState(), "2026-08-01", path=p)
    _, meta = tstate.load_state(p)
    assert tstate.staleness_days(meta, "2026-08-12") == 11


# --- 規則 3：除息調整價 -----------------------------------------------------

def test_unadjusted_prices_would_make_the_slow_gate_permanently_red():
    """HYG 年配息約 6~7%。用未調整價，合成指數每年假性下跌數個百分點，
    「500 日回撤」永遠看起來很大。這個錯不會拋例外，只會讓紅燈變成常態。"""
    n = 600
    ief = _flat(n)
    adjusted = _flat(n)                                   # 總報酬持平
    unadjusted = _series([100.0 * (1 - 0.065 / 252) ** i for i in range(n)])

    calm = CreditProxyGate().evaluate(CreditProxyInputs(hyg=adjusted, ief=ief))
    fake = CreditProxyGate().evaluate(CreditProxyInputs(hyg=unadjusted, ief=ief))
    assert fake.score > calm.score
    assert fake.detail["stress_level"] > calm.detail["stress_level"]


def test_the_adjusted_fetcher_asks_yfinance_for_adjusted_prices():
    """既有的 sources/yahoo.fetch_yahoo_close 用 auto_adjust=False——沿用它
    會一路過關、不拋例外，只是慢閘從此長期偏紅。"""
    from unittest import mock
    from tail_gate import data as tdata

    captured = {}

    def fake_download(ticker, **kw):
        captured.update(kw)
        idx = pd.bdate_range("2024-01-01", periods=30)
        return pd.DataFrame({"Close": np.linspace(100, 110, 30)}, index=idx)

    with mock.patch.object(tdata.yf, "download", side_effect=fake_download):
        tdata.fetch_adjusted_close("HYG", "2024-01-01", "2024-02-01")
    assert captured.get("auto_adjust") is True


# --- 慢閘 -------------------------------------------------------------------

def test_duration_is_hedged_out_so_a_rate_move_is_not_read_as_credit_stress():
    """利率大跌時 HYG 會漲，但那不代表信用轉好。不對沖的話，
    2022 年的升息會被誤讀成信用惡化。"""
    n = 400
    rates_rally = _series([100.0 * 1.0005 ** i for i in range(n)])   # IEF 走高
    hyg = _series([100.0 * 1.0005 ** i for i in range(n)])           # 同步走高
    index, beta = credit_proxy.synthetic_credit_index(hyg, rates_rally)
    assert abs(float(index.iloc[-1]) - 100.0) < 5.0                  # 信用面幾乎沒變


def test_stress_takes_the_max_not_the_sum():
    """兩者測的是同一件事的不同切面，相加會把單一事件放大成兩倍。"""
    n = 600
    values = [100.0] * (n - 50) + list(np.linspace(100.0, 80.0, 50))
    s = _series(values)
    level = credit_proxy.stress_level(s)
    assert 0.15 < level < 0.25          # 約 20% 回撤，不是 40%


def test_missing_lqd_does_not_lower_the_score_ceiling():
    """少一項時若不重新分配權重，缺 LQD 就永遠進不了 STRESS，
    而畫面上看不出來。"""
    n = 600
    crash = _series([100.0] * (n - 60) + list(np.linspace(100.0, 55.0, 60)))
    ief = _flat(n)
    out = CreditProxyGate().evaluate(CreditProxyInputs(hyg=crash, ief=ief))
    assert out.detail["hyg_lqd_chg60"] is None
    assert out.regime == "STRESS"


def test_the_slow_gate_marks_itself_uncalibrated():
    """門檻沒校準過這件事必須跟著資料走，不能只寫在文件裡。"""
    out = CreditProxyGate().evaluate(CreditProxyInputs(hyg=_flat(400), ief=_flat(400)))
    assert out.detail["uncalibrated"] is True


def test_regime_maps_to_the_documented_ceilings():
    from tail_gate.config import REGIME_CEILING
    assert REGIME_CEILING == {"EXPANSION": 1.00, "WATCH": 0.75,
                              "CONTRACTION": 0.50, "STRESS": 0.25}


def test_calibration_reports_not_yet_known_separately_from_failed():
    """「還不知道」與「沒通過」是兩件事，混在一起會讓人以為驗證做過了。"""
    r = credit_proxy.calibrate_against_oas(
        CreditProxyInputs(hyg=_flat(80), ief=_flat(80)), hy_oas=_flat(80, level=4.0))
    assert r.get("passed") is None or "error" in r


# --- 快閘 -------------------------------------------------------------------

def test_the_curve_must_be_fully_inverted_not_merely_elevated():
    """VIX9D > VIX > VIX3M 是完整的順序條件。只有前半段成立不算。"""
    half = FastGateInputs(vix9d=30.0, vix=26.0, vix3m=28.0, vvix_pctile=40.0)
    assert evaluate_fast(half).curve_inverted is False


def test_a_missing_input_is_not_counted_as_a_calm_reading():
    """缺資料與「沒有訊號」在畫面上必須分得出來。"""
    out = evaluate_fast(FastGateInputs(vix9d=30.0, vix=26.0, vix3m=22.0))
    assert out.n_categories == 1
    assert set(out.detail["missing"]) == {"vol_of_vol", "credit_shock"}


def test_all_three_categories_give_the_maximum_veto():
    out = evaluate_fast(FastGateInputs(vix9d=40.0, vix=35.0, vix3m=30.0,
                                       vvix_pctile=95.0, hy_chg20_bp=300.0))
    assert out.raw_veto == 3


def test_the_fast_gate_carries_its_sample_size_caveat():
    """n=1 這件事要跟著結果走。一個看起來很篤定的等級，
    使用者不會知道它背後只有一個樣本。"""
    out = evaluate_fast(CALM)
    assert "n=1" in out.detail["sample_size_note"]


# --- 擁擠度 -----------------------------------------------------------------

def test_no_crowding_data_means_unknown_not_uncrowded():
    """缺資料回中性、位移 0——不是回傳「不擁擠」。後者會讓人以為
    已經確認過市場不擁擠。"""
    out = evaluate_crowding(CrowdingInputs())
    assert out.score == 50.0 and out.threshold_shift == 0.0
    assert out.detail["known"] is False


def test_crowding_only_loosens_thresholds_never_tightens_them():
    """平靜期收緊門檻等於主動關閉保險，而保險要保的正是「平靜之後」。"""
    calm = evaluate_crowding(CrowdingInputs(realized_vol_20d=0.30,
                                            spx_trailing_250d_ret=-0.10))
    assert calm.threshold_shift <= 0.0


def test_a_crowded_market_makes_the_fast_gate_easier_to_trigger():
    crowded = evaluate_crowding(CrowdingInputs(realized_vol_20d=0.06,
                                               spx_trailing_250d_ret=0.35,
                                               cftc_jpy_net_short=200_000,
                                               margin_debt_yoy=0.30))
    assert crowded.threshold_shift < 0
    borderline = FastGateInputs(vix9d=12.0, vix=14.0, vix3m=16.0, vvix_pctile=75.0)
    assert evaluate_fast(borderline).n_categories == 0
    assert evaluate_fast(borderline, crowded.threshold_shift).n_categories == 1


# --- 日誌與 EV --------------------------------------------------------------

def test_the_log_refuses_to_write_without_the_required_columns(tmp_path):
    """必存欄位缺漏要當場失敗，否則會留下一份事後無法驗證、
    當下卻看起來正常的日誌。"""
    with pytest.raises(ValueError, match="必存欄位"):
        daily_log.append_row({"as_of": "2026-08-12"}, path=str(tmp_path / "log.csv"))


def test_the_log_keeps_the_base_weights_so_contribution_can_be_computed(tmp_path):
    """沒有 base_w_*，事後算不出這個模組到底貢獻了什麼。"""
    row = {c: 0 for c in daily_log.REQUIRED_COLUMNS}
    row["as_of"] = "2026-08-12"
    p = daily_log.append_row(row, path=str(tmp_path / "log.csv"))
    cols = pd.read_csv(p).columns.tolist()
    for c in ("base_w_tqqq", "base_w_qqq", "base_w_sgov", "tqqq_close", "qqq_close"):
        assert c in cols


def test_rerunning_the_same_day_overwrites_instead_of_appending(tmp_path):
    p = str(tmp_path / "log.csv")
    row = {c: 0 for c in daily_log.REQUIRED_COLUMNS}
    row["as_of"] = "2026-08-12"
    daily_log.append_row(row, path=p)
    daily_log.append_row({**row, "state_active_veto": 2}, path=p)
    df = pd.read_csv(p)
    assert len(df) == 1 and int(df.iloc[0]["state_active_veto"]) == 2


def test_whipsaw_says_it_has_no_sample_rather_than_inventing_one():
    """在真的觸發過之前，誠實的答案是「還沒有樣本」，不是拿估計值充數。"""
    r = daily_log.measure_whipsaw(pd.DataFrame(columns=daily_log.COLUMNS))
    assert r["episodes"] == 0


def test_whipsaw_is_measured_from_the_logged_closes():
    df = pd.DataFrame({
        "as_of": ["2026-01-02", "2026-01-05", "2026-01-06"],
        "state_active_veto": [0, 1, 0],
        "tqqq_close": [100.0, 80.0, 88.0],
        "qqq_close": [100.0, 93.0, 96.0],
    })
    r = daily_log.measure_whipsaw(df)
    assert r["episodes"] == 1
    assert r["detail"][0]["avoided"] == pytest.approx(88 / 80 - 96 / 93, abs=1e-5)


def test_expected_value_states_that_nothing_is_validated():
    r = ev.expected_value()
    assert r["unvalidated_count"] == 5
    assert not any(r["validated"].values())


def test_the_ev_model_reproduces_the_specs_headline_numbers():
    """自己另編一組假設、算出 4%/年然後印在畫面上，那才是真的誤導。
    參數是回推的，但至少要回推到說明給的那兩個結論上。"""
    r = ev.expected_value()
    assert r["reproduces_doc"], r
    assert r["expected_annual"] == pytest.approx(ev.DOC_EXPECTED_ANNUAL, abs=ev.DOC_TOLERANCE)
    assert r["crisis_share"] == pytest.approx(ev.DOC_CRISIS_SHARE, abs=0.02)


def test_the_module_goes_negative_on_a_modest_whipsaw_miss():
    """假設值 5.52%，兩平點約 7.02%——誤觸成本只要比假設高約四分之一，
    整個模組的期望值就歸零。"""
    rows = ev.sensitivity_to_whipsaw()
    assert any(r["positive"] for r in rows) and any(not r["positive"] for r in rows)
    be = ev.breakeven_whipsaw()
    assert be / ev.EVAssumptions().whipsaw_cost < 1.35


# --- 接線 -------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("50% TQQQ + 50% QQQ", (0.5, 0.5, 0.0)),
    ("25% TQQQ + 75% QQQ", (0.25, 0.75, 0.0)),
    ("100% QQQ", (0.0, 1.0, 0.0)),
    ("100% SGOV", (0.0, 0.0, 1.0)),
])
def test_module_one_position_strings_parse_to_weights(text, expected):
    from tail_gate.report import parse_base_alloc
    assert parse_base_alloc(text) == expected


def test_an_unparseable_position_fails_instead_of_defaulting():
    """預設值在這裡是危險的：base_alloc 猜錯，整個模組的輸出都建立在
    一個沒有人選過的部位上。"""
    from tail_gate.report import parse_base_alloc
    with pytest.raises(ValueError):
        parse_base_alloc("暫缺")


def test_module_ones_ladder_never_exceeds_the_hard_cap():
    """主評分階梯最高就是 50% TQQQ——與說明第 8 節的事前上限同一個數字。
    兩邊若哪天不一致，這個測試會先講話。"""
    from liquidity_monitor.config import POSITION_LADDER
    from tail_gate.report import parse_base_alloc
    for _, _, text, _ in POSITION_LADDER:
        assert parse_base_alloc(text)[0] <= TQQQ_HARD_CAP


def test_every_dashboard_page_links_to_all_six_modules():
    """六頁的導覽列必須一致。少一個連結，那一頁就成了死路——
    而新增模組時最容易漏掉的就是「其他五頁」。"""
    from pathlib import Path
    docs = Path(__file__).resolve().parents[1] / "docs"
    pages = ["index.html", "valuation.html", "backtest.html",
             "rotation.html", "macro.html", "tail.html"]
    for page in pages:
        html = (docs / page).read_text(encoding="utf-8")
        for target in pages:
            assert f'href="{target}"' in html, f"{page} 缺少往 {target} 的連結"
        assert html.count('class="active"') >= 1, f"{page} 沒有標出目前頁面"


def test_the_shadow_mode_caveats_are_on_the_page_not_only_in_the_readme():
    """一個紅色的 STRESS 看起來跟已驗證過的訊號一模一樣。
    「門檻沒校準」「n=1」必須出現在畫面上。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    js = (root / "docs" / "tail.js").read_text(encoding="utf-8")
    html = (root / "docs" / "tail.html").read_text(encoding="utf-8")
    for phrase in ("未經真實資料校準", "n=1", "影子運行"):
        assert phrase in js or phrase in html, f"畫面上找不到「{phrase}」"


# --- 校準指標本身也要被驗證 -------------------------------------------------
#
# 第一次真實執行的 regime_agreement 是 0.2865，看起來像代理失敗。
# 實際上是量尺壞了：拿絕對門檻分級去比四分位分級，而四分位在結構上
# 強制各 25%。一個過不了的量尺，量不出任何東西。

def _perfectly_correlated_pair(n=614):
    """完美相關、但壓力水準很低的一對序列（模擬平靜的近三年）。"""
    rng = np.random.default_rng(0)
    stress = np.abs(rng.normal(0, 0.02, n)).cumsum()
    stress = (stress - stress.min())
    stress = stress / stress.max() * 0.06      # 最大僅 6% 回撤
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.Series(stress, index=idx), pd.Series(stress, index=idx)


def test_absolute_vs_quartile_comparison_fails_even_at_perfect_correlation():
    """守住那個診斷：舊指標在 corr=1.0 時仍只有約 0.25。
    這就是為什麼 0.2865 不能被讀成「代理失敗」。"""
    from tail_gate.credit_proxy import _quartile_regimes, _regime_from_score
    score_src, oas = _perfectly_correlated_pair()
    score = np.minimum(100.0, score_src / 0.15 * 100.0)
    oas_regimes = _quartile_regimes(oas)
    absolute = np.mean([_regime_from_score(s) == b
                        for s, b in zip(score, oas_regimes)])
    assert absolute < 0.35, "舊指標若能過關，這個診斷就不成立了"


def test_rank_comparison_passes_at_perfect_correlation():
    """公平的量尺在完美相關時必須接近 1.0，否則它同樣量不出東西。"""
    from tail_gate.credit_proxy import _quartile_regimes
    score_src, oas = _perfectly_correlated_pair()
    score = np.minimum(100.0, score_src / 0.15 * 100.0)
    rank = np.mean([a == b for a, b in
                    zip(_quartile_regimes(score), _quartile_regimes(oas))])
    assert rank > 0.95


def test_calibration_reports_both_agreements_so_neither_hides_the_other():
    """絕對門檻那一項預期不會過（門檻本來就沒校準）。保留它是為了讓
    那件事有數字，而不是被新指標蓋掉。"""
    n = 400
    idx = pd.bdate_range("2023-01-02", periods=n)
    hyg = pd.Series(np.concatenate([np.full(340, 100.0), np.linspace(100, 88, 60)]), index=idx)
    ief = pd.Series(np.full(n, 100.0), index=idx)
    oas = pd.Series(np.concatenate([np.full(340, 3.2), np.linspace(3.2, 6.0, 60)]), index=idx)
    r = credit_proxy.calibrate_against_oas(CreditProxyInputs(hyg=hyg, ief=ief), hy_oas=oas)
    assert "regime_agreement_rank" in r and "regime_agreement_absolute" in r
    assert r["regime_agreement"] == r["regime_agreement_rank"]


def test_the_vectorised_stress_series_matches_the_scalar_definition():
    """向量化只是為了快，定義不能跟著變——否則校準量的就不是上線用的那個閘門。"""
    from tail_gate.credit_proxy import _stress_series, stress_level
    n = 600
    s = _series(np.concatenate([np.full(540, 100.0), np.linspace(100, 82, 60)]))
    assert _stress_series(s).iloc[-1] == pytest.approx(stress_level(s), abs=1e-9)


def test_the_specs_three_targets_are_not_mutually_consistent():
    """一致率 0.70 需要 corr ≈ 0.93，而說明自己的相關係數門檻只有 0.60
    （對應一致率僅約 0.43）。一個剛好通過前兩項的代理，**數學上不可能**
    通過第三項。這不是代理的問題，是門檻的問題，必須看得見。"""
    from tail_gate.config import CALIBRATION_TARGETS
    from tail_gate.credit_proxy import corr_needed_for_agreement, expected_agreement_at

    needed = corr_needed_for_agreement(CALIBRATION_TARGETS["regime_agreement"])
    assert needed > CALIBRATION_TARGETS["corr_level_dd_vs_oas"]
    assert needed > 0.9
    # 剛好通過相關係數門檻的代理，其一致率期望值遠低於一致率門檻
    assert expected_agreement_at(CALIBRATION_TARGETS["corr_level_dd_vs_oas"]) < 0.5


def test_the_benchmark_is_monotonic_and_anchored():
    """基準表要單調（相關越高、一致率越高），兩端要對得上：
    零相關約等於隨機猜四選一（0.25），完全相關為 1.0。"""
    from tail_gate.credit_proxy import BIVARIATE_NORMAL_QUARTILE_AGREEMENT as T
    xs = sorted(T)
    ys = [T[x] for x in xs]
    assert ys == sorted(ys)
    assert T[0.0] == pytest.approx(0.25, abs=0.02)
    assert T[1.0] == 1.0


def test_agreement_is_reported_against_what_the_correlation_predicts():
    """觀測值要與「這個相關係數應該得到多少」並列。只報 0.395 vs 0.70，
    讀者無從判斷是代理差、還是門檻本來就不可能達到。"""
    n = 400
    idx = pd.bdate_range("2023-01-02", periods=n)
    hyg = pd.Series(np.concatenate([np.full(340, 100.0), np.linspace(100, 88, 60)]), index=idx)
    ief = pd.Series(np.full(n, 100.0), index=idx)
    oas = pd.Series(np.concatenate([np.full(340, 3.2), np.linspace(3.2, 6.0, 60)]), index=idx)
    r = credit_proxy.calibrate_against_oas(CreditProxyInputs(hyg=hyg, ief=ief), hy_oas=oas)
    assert "regime_agreement_expected" in r
    assert "regime_agreement_shortfall" in r
    assert r["targets_are_mutually_consistent"] is False
