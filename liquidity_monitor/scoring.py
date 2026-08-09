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


def _median(sorted_values: list) -> float:
    """已排序序列的中位數；空序列回 0（呼叫端據此判定「沒有規模基準」）。"""
    n = len(sorted_values)
    if n == 0:
        return 0.0
    mid = n // 2
    return sorted_values[mid] if n % 2 else (sorted_values[mid - 1] + sorted_values[mid]) / 2


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


# --- ④ 股票型 ETF 資金流 ---------------------------------------------------
#
# 規格第124行只給五個等級（大幅淨流出 −2 ／淨流出 −1 ／持平 0 ／淨流入 +1 ／
# 大幅淨流入 +2），沒有給金額門檻。方向由淨流的正負決定，「大幅」則以**這個
# 數列自己的歷史分布**判定——不自行發明「超過 X 億美元算大幅」這種數字。
# 同 Gate B 用滾動百分位而非寫死絕對值的原則。
MIN_FLOW_HISTORY_FOR_MAGNITUDE = 20   # 少於這麼多筆觀測就只判方向，不判大小
# 以「常態規模」（歷史淨流絕對值的中位數）為單位來衡量今天這一筆。
# 刻意不用百分位當「持平」的門檻：規格的持平指的是**進出接近打平**，
# 不是「跟平常差不多」。若資金流長期都很大，它的後 20% 依然是很大的金額，
# 把那叫做持平就錯了。改用相對常態規模的比例，語意才對得上。
FLAT_FLOW_RATIO = 0.3                 # 不到常態規模的三成 -> 持平
LARGE_FLOW_RATIO = 2.0                # 超過常態規模兩倍   -> 大幅


def score_etf_fund_flow(flow_musd: Number, history: Optional[list] = None) -> Optional[int]:
    """由淨流金額與其歷史規模計分。

    `history` 為過去各期的淨流（含正負，內部取絕對值當規模）。
    歷史不足時只回 ±1／0：方向是確定的，但「大幅與否」無從判斷——
    與其硬給 ±2，不如少說一級。
    """
    if _na(flow_musd):
        return None
    flow = float(flow_musd)

    # 取絕對值：流出期間的規模同樣是規模，只看正值會讓熊市完全沒有比較基準
    magnitudes = sorted(abs(float(v)) for v in (history or []) if not _na(v))
    scale = _median(magnitudes)
    if len(magnitudes) < MIN_FLOW_HISTORY_FOR_MAGNITUDE or not scale:
        if flow == 0:
            return 0
        return 1 if flow > 0 else -1

    ratio = abs(flow) / scale
    if ratio < FLAT_FLOW_RATIO:
        return 0
    if flow > 0:
        return 2 if ratio > LARGE_FLOW_RATIO else 1
    return -2 if ratio > LARGE_FLOW_RATIO else -1


# fomc_decision、fedwatch_path 已由來源端（fomc.py 計算 /
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
