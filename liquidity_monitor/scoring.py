"""軌道一計分引擎：把每個子項的原始數值轉換成 -2..+2 分數，
再依文件第53-160行的規則彙總成類別分數、綜合分數與燈號。

本模組是純函式（不做任何網路存取），輸入是已經算好的「原始特徵值」
（例如「3個月變化的百分點數」「20日變化的bp數」），輸出是分數。
特徵值的計算（抓資料、算變化率）在 pipeline.py。

部分子項文件僅給質化描述、未給精確數字門檻（如⑥ 30Y殖利率動能的
「大幅下行/下行/持平/上行/創多年新高」），本模組採用的量化門檻已在
函式註解中明確標出為「本專案自訂假設」，供人工複核。
"""
from __future__ import annotations

from typing import Optional

from .config import (
    CATEGORY_ITEMS,
    CATEGORY_LABELS,
    CATEGORY_WEIGHTS,
    LIGHT_BANDS,
    MANUAL_OVERRIDE_ITEMS,
    POSITION_LADDER,
)

Number = Optional[float]


def _na(x: Number) -> bool:
    return x is None


# ---------------------------------------------------------------------------
# ① 總體貨幣流動性
# ---------------------------------------------------------------------------

def score_fed_bs_3m_chg(pp: Number) -> Optional[int]:
    if _na(pp):
        return None
    if pp > 2:
        return 2
    if pp >= 0.5:
        return 1
    if pp >= -0.5:
        return 0
    if pp >= -2:
        return -1
    return -2


def score_on_rrp_pctile(pctile: Number, structurally_dormant: bool = False) -> Optional[int]:
    """`structurally_dormant`：過去12個月 ON RRP 全區間 < $100B（文件第76-77行例外條款）。"""
    if structurally_dormant:
        return 0
    if _na(pctile):
        return None
    if pctile > 80:
        return 1
    if pctile >= 40:
        return 0
    if pctile >= 10:
        return -1
    return -2


def score_m2_yoy_3m_chg(pp: Number) -> Optional[int]:
    return score_fed_bs_3m_chg(pp)  # 同一組門檻


# ---------------------------------------------------------------------------
# ② 資金成本與信用
# ---------------------------------------------------------------------------

def score_hy_oas_level(pct: Number) -> Optional[int]:
    """鐘型計分（文件第87、92行）。"""
    if _na(pct):
        return None
    if pct > 6:
        return -2
    if pct > 4:
        return -1
    if pct > 3:
        return 1
    if pct > 2.5:
        return 0
    return -1


def score_hy_oas_20d_chg(bp: Number) -> Optional[int]:
    if _na(bp):
        return None
    if bp <= -30:
        return 1
    if bp < 30:
        return 0
    if bp <= 75:
        return -1
    return -2


def score_t2s10s_bp(bp: Number) -> Optional[int]:
    if _na(bp):
        return None
    if bp < -50:
        return -2
    if bp < 0:
        return -1
    if bp < 30:
        return 0
    if bp <= 80:
        return 1
    return 2


def score_sofr_iorb_bp(bp: Number) -> Optional[int]:
    if _na(bp):
        return None
    if bp > 10:
        return -2
    if bp >= 5:
        return -1
    if bp >= 0:
        return 0
    return 1


# ---------------------------------------------------------------------------
# ③ 市場微觀結構
# ---------------------------------------------------------------------------

def score_move_index(level: Number) -> Optional[int]:
    if _na(level):
        return None
    if level < 70:
        return 1
    if level <= 100:
        return 0
    if level <= 130:
        return -1
    return -2


def score_ndx_breadth_200d(pct: Number) -> Optional[int]:
    if _na(pct):
        return None
    if pct > 70:
        return 1
    if pct >= 50:
        return 0
    if pct >= 30:
        return -1
    return -2


# ---------------------------------------------------------------------------
# ④ 風險偏好情緒
# ---------------------------------------------------------------------------

def score_vix_level(level: Number) -> Optional[int]:
    if _na(level):
        return None
    if level > 35:
        return -2
    if level >= 25:
        return -1
    if level >= 15:
        return 0
    if level >= 12:
        return 1
    return 2


def score_ivts(ratio: Number) -> Optional[int]:
    if _na(ratio):
        return None
    if ratio > 1.10:
        return -2
    if ratio >= 1.00:
        return -1
    if ratio >= 0.95:
        return 0
    if ratio >= 0.90:
        return 1
    return 2


def score_margin_debt_yoy(pct: Number) -> Optional[int]:
    """鐘型計分（文件第113、115行）。"""
    if _na(pct):
        return None
    if pct < -15:
        return -2
    if pct < 0:
        return -1
    if pct <= 20:
        return 1
    if pct <= 40:
        return 0
    return -1


# ---------------------------------------------------------------------------
# ⑤ 跨資產資金流向
# ---------------------------------------------------------------------------

def score_dxy_1m_chg(pct: Number) -> Optional[int]:
    if _na(pct):
        return None
    if pct > 3:
        return -2
    if pct > 1:
        return -1
    if pct >= -1:
        return 0
    if pct >= -3:
        return 1
    return 2


def score_usdjpy_20d_chg(pct: Number) -> Optional[int]:
    """正值＝日圓貶值（USD/JPY上升），負值＝日圓升值。"""
    if _na(pct):
        return None
    if pct > 3:
        return 1
    if pct >= -3:
        return 0
    if pct >= -5:
        return -1
    return -2


# ---------------------------------------------------------------------------
# ⑥ 政策方向
# ---------------------------------------------------------------------------

def score_yield30y_60d_momentum(chg_bp: Number, is_multiyear_high: bool = False) -> Optional[int]:
    """文件未給精確bp門檻（僅質化描述），以下為本專案自訂量化假設：
    創多年新高且仍在上行 -> -2（優先於一般門檻）；
    60個交易日變化 <= -30bp:+2 / -30~-5bp:+1 / -5~+5bp:0 / +5~+30bp:-1 / >+30bp或創新高:-2。
    """
    if _na(chg_bp):
        return None
    if is_multiyear_high and chg_bp > 0:
        return -2
    if chg_bp <= -30:
        return 2
    if chg_bp <= -5:
        return 1
    if chg_bp < 5:
        return 0
    if chg_bp < 30:
        return -1
    return -2


# fomc_decision、fedwatch_path、etf_fund_flow 已由來源端（fomc.py 計算 /
# 使用者手動填入 manual_overrides.json）直接給出 -2..+2 的最終分數，
# 此處僅需原樣傳遞（None 表示暫缺）。
def score_passthrough(score: Number) -> Optional[int]:
    if _na(score):
        return None
    return int(score)


SCORERS = {
    "fed_bs_3m_chg": score_fed_bs_3m_chg,
    "on_rrp_pctile": None,  # 特殊簽章，pipeline 直接呼叫 score_on_rrp_pctile
    "m2_yoy_3m_chg": score_m2_yoy_3m_chg,
    "hy_oas_level": score_hy_oas_level,
    "hy_oas_20d_chg": score_hy_oas_20d_chg,
    "t2s10s_bp": score_t2s10s_bp,
    "sofr_iorb_bp": score_sofr_iorb_bp,
    "move_index": score_move_index,
    "ndx_breadth_200d": score_ndx_breadth_200d,
    "vix_level": score_vix_level,
    "ivts": score_ivts,
    "margin_debt_yoy": score_margin_debt_yoy,
    "dxy_1m_chg": score_dxy_1m_chg,
    "etf_fund_flow": score_passthrough,
    "usdjpy_20d_chg": score_usdjpy_20d_chg,
    "fomc_decision": score_passthrough,
    "fedwatch_path": score_passthrough,
    "yield30y_60d_momentum": None,  # 特殊簽章（需 is_multiyear_high）
}


def category_average(item_scores: dict[str, Optional[int]], category: str) -> Optional[float]:
    items = CATEGORY_ITEMS[category]
    valid = [item_scores[i] for i in items if item_scores.get(i) is not None]
    if not valid:
        return None
    return sum(valid) / len(valid)


def composite_score(item_scores: dict[str, Optional[int]]) -> tuple[float, dict[str, Optional[float]], dict[str, float]]:
    """回傳 (綜合分數, 各類別平均分, 實際採用的類別權重(缺類別時已按比例分攤))。"""
    cat_avgs = {cat: category_average(item_scores, cat) for cat in CATEGORY_ITEMS}
    available = {cat: w for cat, w in CATEGORY_WEIGHTS.items() if cat_avgs[cat] is not None}
    total_w = sum(available.values())
    if total_w == 0:
        return 0.0, cat_avgs, {}
    effective_weights = {cat: w / total_w for cat, w in available.items()}
    score = sum(cat_avgs[cat] * effective_weights[cat] for cat in effective_weights)
    return round(score, 4), cat_avgs, effective_weights


def light_for_score(score: float) -> str:
    for lo, hi, label in LIGHT_BANDS:
        if lo <= score < hi or (hi == float("inf") and score >= lo):
            return label
    return "🟡 黃"


def position_for_score(score: float) -> tuple[str, float]:
    for lo, hi, alloc, leverage in POSITION_LADDER:
        if lo <= score < hi or (hi == float("inf") and score >= lo):
            return alloc, leverage
    return "100% QQQ", 1.0


def dominance_check(item_scores: dict[str, Optional[int]], cat_avgs: dict[str, Optional[float]], effective_weights: dict[str, float]) -> Optional[dict]:
    """結構性警示①：單類別支配檢查（文件第158行）。"""
    contributions = {
        cat: cat_avgs[cat] * effective_weights[cat]
        for cat in effective_weights
        if cat_avgs[cat] is not None
    }
    if not contributions:
        return None
    total_score = sum(contributions.values())
    sign = 1 if total_score >= 0 else -1
    same_sign_total = sum(abs(v) for v in contributions.values() if (v >= 0) == (sign >= 0))
    if same_sign_total == 0:
        return None
    for cat, contrib in contributions.items():
        if abs(contrib) > 0.7 * same_sign_total:
            alt_score = round(total_score - contrib, 4)
            return {
                "category": cat,
                "label": CATEGORY_LABELS[cat],
                "contribution": round(contrib, 4),
                "share_of_same_sign": round(abs(contrib) / same_sign_total, 3),
                "score_if_excluded": alt_score,
            }
    return None


def divergence_check(cat_avgs: dict[str, Optional[float]]) -> list[dict]:
    """結構性警示②：②與③或④評分差距 >= 1.5（文件第159行）。"""
    warnings = []
    c2 = cat_avgs.get("credit_funding")
    for other_key, other_label in (("microstructure", "③"), ("risk_sentiment", "④")):
        c_other = cat_avgs.get(other_key)
        if c2 is not None and c_other is not None and abs(c2 - c_other) >= 1.5:
            warnings.append({
                "pair": f"② vs {other_label}",
                "credit_funding": round(c2, 3),
                "other": round(c_other, 3),
                "gap": round(abs(c2 - c_other), 3),
            })
    return warnings
