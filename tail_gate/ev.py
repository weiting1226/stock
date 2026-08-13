"""期望值模型。接線說明第 0 節的數字，以及它們憑什麼。

**這一份存在的目的不是算出一個漂亮的數字，是讓那個數字的來源攤開。**
說明第 0 節說期望值約 +1.2%/年、89% 來自約七年一遇的危機型事件。
若只把 1.2% 印在畫面上，讀的人會以為那是回測出來的。它不是——
它是幾個假設相乘的結果，其中 `whipsaw_cost` 完全沒有實測過。

**參數是回推出來的，這件事必須講明白。** 說明只給了結論（1.2%／89%）與
兩個可查的量（七年重現期、0.5~1.0 次/年的誤觸目標），沒有給每一項假設。
因此這裡把預設值訂成「能重現說明那兩個結論」的一組——先固定可查的量，
再解出剩下的。這不是獨立驗證，是把說明的結論翻譯成可以做敏感度分析的形式。
自己另編一組假設、算出 4%/年然後印在畫面上，那才是真的誤導。

事件分型沿用說明第 8 節：

  Type I    危機型（2008/2020）—— 效益幾乎全部來自這裡，樣本 n=1~2
  Type II   踩踏型 —— 最頻繁，但干預 EV ≈ 0，已由 persist_days 主動放棄
  Type IV   SVB/LDI 型 —— 零領先期，完全防不到，只能靠事前部位上限
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 說明第 0 節給的結論值
DOC_EXPECTED_ANNUAL = 0.012
DOC_CRISIS_SHARE = 0.89

# 重現說明結論所需的容差。超過就代表本模組的假設已經與原始模型脫節，
# 那件事要看得見，不能靠「差不多」蓋過去。
DOC_TOLERANCE = 0.002


@dataclass
class EVAssumptions:
    """每一項都是假設，不是量測值。

    前兩項來自說明（七年重現期、誤觸 0.5~1.0 次/年的校準目標）；
    後三項是為了重現說明的 1.2%／89% 回推出來的。
    """

    crisis_period_years: float = 7.0        # 說明第 0 節
    crisis_avoided_drawdown: float = 0.35   # 危機中避開的 TQQQ 相對損失
    moderate_gain_annual: float = 0.00618   # 回推：使危機佔比 = 89%
    false_positive_per_year: float = 0.8    # 說明第 6 節目標區間 0.5~1.0 的中點
    whipsaw_cost: float = 0.0552            # 回推：使 EV = 1.2%。**未實測**

    validated: dict = field(default_factory=lambda: {
        "crisis_period_years": False,       # n=1，無法驗證
        "crisis_avoided_drawdown": False,
        "moderate_gain_annual": False,
        "false_positive_per_year": False,   # 待說明第 6 節第二步的真實回測
        "whipsaw_cost": False,              # 待日誌累積（第 5 節存收盤價的用意）
    })


def expected_value(a: EVAssumptions = None) -> dict:
    """年化期望值 = 毛效益 − 誤觸成本。

    刻意寫得很簡單。複雜的 EV 模型在這裡是有害的：輸入全是未校準的假設，
    模型再精緻只是把不確定性藏得更深。
    """
    a = a or EVAssumptions()
    crisis_gain = a.crisis_avoided_drawdown / a.crisis_period_years
    gross = crisis_gain + a.moderate_gain_annual
    false_cost = a.false_positive_per_year * a.whipsaw_cost
    ev = gross - false_cost
    share = crisis_gain / gross if gross > 0 else None

    return {
        "expected_annual": round(ev, 5),
        "gross_benefit_annual": round(gross, 5),
        "crisis_gain_annual": round(crisis_gain, 5),
        "false_positive_cost_annual": round(false_cost, 5),
        "crisis_share": None if share is None else round(share, 4),
        "doc_expected_annual": DOC_EXPECTED_ANNUAL,
        "doc_crisis_share": DOC_CRISIS_SHARE,
        "reproduces_doc": (abs(ev - DOC_EXPECTED_ANNUAL) <= DOC_TOLERANCE
                           and share is not None
                           and abs(share - DOC_CRISIS_SHARE) <= 0.02),
        "assumptions": {
            "crisis_period_years": a.crisis_period_years,
            "crisis_avoided_drawdown": a.crisis_avoided_drawdown,
            "moderate_gain_annual": a.moderate_gain_annual,
            "false_positive_per_year": a.false_positive_per_year,
            "whipsaw_cost": a.whipsaw_cost,
        },
        "validated": a.validated,
        "unvalidated_count": sum(1 for v in a.validated.values() if not v),
        "note": ("參數是為了重現說明的 1.2%／89% 回推而得，不是獨立驗證。"
                 "五項假設全部未實測，其中 whipsaw_cost 是最大的不確定來源。"),
    }


def breakeven_whipsaw(a: EVAssumptions = None) -> float:
    """讓整個模組期望值歸零的 whipsaw_cost。"""
    a = a or EVAssumptions()
    gross = a.crisis_avoided_drawdown / a.crisis_period_years + a.moderate_gain_annual
    return gross / a.false_positive_per_year


def sensitivity_to_whipsaw(a: EVAssumptions = None, span=None) -> list:
    """whipsaw_cost 掃描：讓「這個參數有多要緊」看得見。

    預設區間刻意跨過損益兩平點。實際結論相當刺耳——假設值 5.52%，
    兩平點約 7.02%，也就是**誤觸成本只要比假設高約四分之一，
    整個模組的期望值就歸零**。這比任何一句「最未經驗證的一環」都具體。
    """
    a = a or EVAssumptions()
    be = breakeven_whipsaw(a)
    span = span or (0.02, 0.04, a.whipsaw_cost, round(be, 4), 0.09)
    out = []
    for w in sorted(set(span)):
        r = expected_value(EVAssumptions(
            a.crisis_period_years, a.crisis_avoided_drawdown, a.moderate_gain_annual,
            a.false_positive_per_year, w))
        out.append({"whipsaw_cost": w, "expected_annual": r["expected_annual"],
                    "positive": r["expected_annual"] > 0,
                    "is_assumed": abs(w - a.whipsaw_cost) < 1e-9,
                    "is_breakeven": abs(w - be) < 5e-4})
    return out
