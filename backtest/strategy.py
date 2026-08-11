"""指標面板 -> 每日分數 -> 綜合分數 -> 燈號 -> 目標權重。

計分一律呼叫 `liquidity_monitor.scoring` 的既有函式，不重寫門檻。
回測的意義在於「模組一那套規則跑起來如何」，若這裡自己寫一份門檻，
測到的就是另一套策略了。
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from liquidity_monitor import gates, scoring
from liquidity_monitor.config import CATEGORY_ITEMS, LIGHT_BANDS, POSITION_LADDER

from .config import CAP_WEIGHTS, EXCLUDED_CATEGORIES, LADDER_WEIGHTS, UNAVAILABLE_ITEMS

log = logging.getLogger(__name__)

# 逐項計分函式。簽章單純的直接對應；on_rrp_pctile 另需休眠旗標，單獨處理。
SCORERS = {
    "fed_bs_3m_chg": scoring.score_fed_bs_3m_chg,
    "m2_yoy_3m_chg": scoring.score_m2_yoy_3m_chg,
    "hy_oas_level": scoring.score_hy_oas_level,
    "hy_oas_20d_chg": scoring.score_hy_oas_20d_chg,
    "t2s10s_bp": scoring.score_t2s10s_bp,
    "sofr_iorb_bp": scoring.score_sofr_iorb_bp,
    "move_index": scoring.score_move_index,
    "vix_level": scoring.score_vix_level,
    "ivts": scoring.score_ivts,
    "margin_debt_yoy": scoring.score_margin_debt_yoy,
    "dxy_1m_chg": scoring.score_dxy_1m_chg,
    "usdjpy_20d_chg": scoring.score_usdjpy_20d_chg,
}

GATE_B_PERCENTILE_WINDOW = gates.TEN_YEAR_TRADING_DAYS
GATE_B_MIN_OBS = 252 * 3      # 不足三年就不判定，寧可留空也不用近似門檻（見下）


def backtestable_items() -> list[str]:
    """回測期間真的算得出來的指標清單。"""
    excluded_items = {i for cat in EXCLUDED_CATEGORIES for i in CATEGORY_ITEMS[cat]}
    return [
        item
        for cat, items in CATEGORY_ITEMS.items()
        for item in items
        if item not in excluded_items and item not in UNAVAILABLE_ITEMS
    ]


def score_panel(panel: pd.DataFrame) -> pd.DataFrame:
    """把原始值面板轉成 -2..+2 的分數面板（缺值保持 NaN）。"""
    scores = pd.DataFrame(index=panel.index)

    for item, fn in SCORERS.items():
        if item not in panel.columns:
            continue
        scores[item] = panel[item].map(lambda v: np.nan if pd.isna(v) else fn(v))

    if "on_rrp_pctile" in panel.columns:
        # 休眠條款要逐日判定：ON RRP 在 2021-22 年高達兩兆、2025 年後歸零，
        # 用單一旗標套整段歷史會兩邊都錯
        scores["on_rrp_pctile"] = [
            scoring.score_on_rrp_pctile(None if pd.isna(v) else v, bool(dormant))
            for v, dormant in zip(panel["on_rrp_pctile"], panel.get("on_rrp_dormant", False))
        ]

    return scores[[c for c in backtestable_items() if c in scores.columns]]


def composite_panel(scores: pd.DataFrame) -> pd.DataFrame:
    """逐日算類別平均、綜合分數與燈號。

    直接呼叫 `scoring.composite_score`：⑥ 的項目根本沒放進字典，
    它算出來的類別平均就是 None，權重會自動按比例重新分配到其餘五類
    ——這是模組一既有的行為，不是回測另外做的處理。
    """
    rows = []
    for ts, row in scores.iterrows():
        item_scores = {k: (None if pd.isna(v) else int(v)) for k, v in row.items()}
        score, cat_avgs, weights = scoring.composite_score(item_scores)
        rec = {"composite_score": score, "light": scoring.light_for_score(score)}
        valid = sum(1 for v in item_scores.values() if v is not None)
        rec["items_used"] = valid
        for cat, avg in cat_avgs.items():
            rec[f"cat_{cat}"] = avg
        rows.append(rec)
    out = pd.DataFrame(rows, index=scores.index)
    # 一項有效指標都沒有的日子不能給 0 分——0 分在階梯上是「100% QQQ」，
    # 那是一個有方向的決定，不是「不知道」。留成 NaN，由引擎決定怎麼處理。
    out.loc[out["items_used"] == 0, ["composite_score", "light"]] = np.nan
    return out


def ladder_weights(score: float) -> dict:
    """綜合分數 -> 目標權重。階梯文字必須在對照表裡，否則直接失敗。

    找不到就丟例外而不是給預設值：預設值會讓回測安靜地配出錯誤的部位，
    跑出一條看起來很正常的績效曲線。
    """
    alloc, _leverage = scoring.position_for_score(score)
    if alloc not in LADDER_WEIGHTS:
        raise KeyError(f"部位階梯「{alloc}」沒有對應的權重設定（backtest.config.LADDER_WEIGHTS）")
    return dict(LADDER_WEIGHTS[alloc])


def gate_a_panel(ndx_close: pd.Series) -> pd.DataFrame:
    """Gate A（價格閘門）的向量化版本，判定條件與 `gates.gate_a` 相同。"""
    s = ndx_close.astype(float)
    ma200 = s.rolling(200).mean()
    ma50 = s.rolling(50).mean()
    sloping_up = ma200 > ma200.shift(21)

    below200 = s < ma200
    out = pd.DataFrame(index=s.index)
    out["cap"] = np.where(
        below200 & (ma50 < ma200), "SGOV",
        np.where(below200, "QQQ",
                 # 站上200日線但均線未明確上彎：文件未定義，模組一採保守禁用槓桿
                 np.where(sloping_up, None, "QQQ")),
    )
    # 均線還沒算出來的期間是「不知道」，不是「未觸發」——兩者的 cap 都是空的，
    # 但意義完全不同。線上的 gates.gate_a 在資料不足時同樣回 cap=None（status
    # 標「暫缺」），這裡保持一致，只是另外用 status 欄把差別記下來，
    # 讓「暖身期不足」不會被誤讀成「閘門判定通過」。
    unknown = ma200.isna() | s.isna()
    out.loc[unknown, "cap"] = None
    out["status"] = np.where(unknown, "unknown",
                             np.where(out["cap"].isna(), "clear", "capped"))
    return out


def gate_b_panel(panel: pd.DataFrame) -> pd.Series:
    """Gate B（脆弱度閘門）：五項檢查中三項成立就禁用槓桿。

    **刻意不使用 `GATE_B_FALLBACK_THRESHOLDS`。** 那組數字是依 2026 年的
    分布訂出來的參考門檻；拿它去判 2013 年的某一天，等於讓當時的決策用到
    十幾年後才知道的資訊。滾動百分位算不出來時（歷史不足），該項就記為
    「未知」而不是硬套一個門檻——寧可少觸發，也不要用未來的知識回測。
    """
    from .panel import rolling_percentile

    def pct(col: str) -> pd.Series:
        if col not in panel.columns:
            return pd.Series(np.nan, index=panel.index)
        return rolling_percentile(panel[col].astype(float),
                                  GATE_B_PERCENTILE_WINDOW, GATE_B_MIN_OBS)

    margin_p, hy_p = pct("margin_debt_yoy"), pct("hy_oas_level")
    vix_p, skew_p = pct("vix_level"), pct("skew")

    triggered = pd.DataFrame(index=panel.index)
    triggered["margin_debt_yoy"] = margin_p > 85
    triggered["hy_oas"] = hy_p < 15
    triggered["vix_low_skew_high"] = (vix_p < 20) & (skew_p > 85)
    # margin_debt_gdp 與 ndx_fwd_pe 本專案沒有可回測的序列，記為未知（不觸發）
    for col, source in (("margin_debt_yoy", margin_p), ("hy_oas", hy_p)):
        triggered.loc[source.isna(), col] = False
    triggered.loc[vix_p.isna() | skew_p.isna(), "vix_low_skew_high"] = False

    count = triggered.sum(axis=1)
    return pd.Series(np.where(count >= 3, "QQQ", None), index=panel.index, name="gate_b_cap")


def apply_cap(weights: dict, cap) -> dict:
    """套用閘門上限。上限只會讓部位更保守，不會更積極。"""
    if not cap:
        return weights
    return dict(CAP_WEIGHTS[cap])


def target_weights(
    composite: pd.DataFrame,
    gate_a: pd.DataFrame,
    gate_b: pd.Series,
    use_gates: bool = True,
) -> pd.DataFrame:
    """每日目標權重（尚未套用執行時滯，那是引擎的責任）。"""
    records = []
    for ts in composite.index:
        score = composite.at[ts, "composite_score"]
        if pd.isna(score):
            records.append({"date": ts, "alloc": None, "cap": None})
            continue
        alloc, _ = scoring.position_for_score(float(score))
        w = ladder_weights(float(score))
        cap = None
        if use_gates:
            # 兩道閘門都可能給上限，取較嚴格的那個（SGOV 比 QQQ 嚴格）。
            #
            # 判斷「有沒有上限」必須檢查型別，不能用真假值：pandas 會把 None
            # 存成 NaN，而 **bool(nan) 是 True**。用 `if c` 篩選的話，每一個
            # 「未觸發」的日子都會被當成有上限而降級成 100% QQQ——TQQQ 從頭到尾
            # 一股都不會持有，槓桿階梯等於沒被測到。這不會拋錯，只會跑出一條
            # 看起來非常合理的防禦型績效曲線（實測：15 年 3769 天全部被降級）。
            caps = [c for c in (gate_a.at[ts, "cap"], gate_b.at[ts]) if isinstance(c, str) and c]
            if caps:
                cap = "SGOV" if "SGOV" in caps else "QQQ"
            w = apply_cap(w, cap)
        rec = {"date": ts, "alloc": alloc, "cap": cap}
        rec.update(w)
        records.append(rec)

    out = pd.DataFrame(records).set_index("date")
    for col in ("QQQ", "TQQQ", "SGOV"):
        if col not in out.columns:
            out[col] = 0.0
    out[["QQQ", "TQQQ", "SGOV"]] = out[["QQQ", "TQQQ", "SGOV"]].fillna(0.0)
    return out
