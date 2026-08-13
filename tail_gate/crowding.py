"""擁擠度：市場多擠，決定快閘門檻要位移多少。

擁擠本身不是訊號——它不預測下跌。它決定的是**下跌時會有多痛**：
部位擁擠時同一個衝擊會引發更多強制平倉。因此這裡不產生否決，
只調整快閘的門檻。

四項輸入全部可省略（接線說明第 4 節）。缺資料時回傳中性值、位移 0——
**不是回傳「不擁擠」**。這兩者差很多：前者是「不知道」，後者會讓門檻
維持原位並讓人以為已經確認過市場不擁擠。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CrowdingInputs:
    realized_vol_20d: Optional[float] = None       # 已實現波動（年化，小數）
    spx_trailing_250d_ret: Optional[float] = None  # 過去 250 日報酬（小數）
    cftc_jpy_net_short: Optional[float] = None     # 口數，正=淨空。**T+3**
    margin_debt_yoy: Optional[float] = None        # 年增率（小數）。約 T+30


@dataclass
class CrowdingOutput:
    score: float                # 0-100，50 為中性
    threshold_shift: float      # 加在快閘門檻上的絕對值，負數=更容易觸發
    detail: dict = field(default_factory=dict)


# 擁擠度最多讓門檻鬆動多少。上限存在的理由：擁擠度的三項輸入分別有
# T+3 與 T+30 的延遲，讓一個過期的訊號無限制地鬆動門檻是危險的。
MAX_SHIFT = 10.0


def evaluate_crowding(inputs: CrowdingInputs) -> CrowdingOutput:
    parts: dict = {}
    missing = []

    # 低波動 + 高漲幅 = 典型的擁擠組合（波動率壓縮期的槓桿累積）
    if inputs.realized_vol_20d is None:
        missing.append("realized_vol_20d")
    else:
        # 年化波動 8% 以下算極低，25% 以上算高
        parts["low_vol"] = _clamp((0.25 - inputs.realized_vol_20d) / 0.17)

    if inputs.spx_trailing_250d_ret is None:
        missing.append("spx_trailing_250d_ret")
    else:
        parts["trailing_gain"] = _clamp(inputs.spx_trailing_250d_ret / 0.30)

    if inputs.cftc_jpy_net_short is None:
        missing.append("cftc_jpy_net_short")
    else:
        # 日圓淨空部位是套利交易（carry trade）規模的代理
        parts["carry_trade"] = _clamp(inputs.cftc_jpy_net_short / 150_000.0)

    if inputs.margin_debt_yoy is None:
        missing.append("margin_debt_yoy")
    else:
        parts["margin_debt"] = _clamp(inputs.margin_debt_yoy / 0.25)

    if not parts:
        # 全缺：中性、不位移。這是「不知道」，不是「不擁擠」
        return CrowdingOutput(50.0, 0.0, {"missing": missing, "known": False})

    score = float(sum(parts.values()) / len(parts) * 100.0)
    # 只有明顯高於中性才鬆動門檻；低於中性不收緊——收緊等於在平靜期
    # 主動關閉保險，而保險要保的正是「平靜之後」
    shift = -MAX_SHIFT * _clamp((score - 50.0) / 50.0)
    return CrowdingOutput(
        score=round(score, 2),
        threshold_shift=round(shift, 3),
        detail={"components": {k: round(v, 3) for k, v in parts.items()},
                "missing": missing, "known": True,
                "coverage": f"{len(parts)}/4"},
    )


def _clamp(x: float) -> float:
    return float(max(0.0, min(1.0, x)))
