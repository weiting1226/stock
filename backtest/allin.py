"""全押式部位規則（模組三的策略變體）。

與模組一原本的階梯有三處不同，都是使用者指定的調整：

1. **只持有單一標的**：TQQQ／QQQ／SGOV 三選一，不做 50/50、25/75 這種混合。
2. **從衰退回升時加速**：上一個部位是 SGOV 時，用一個較低的門檻直接跳進
   TQQQ，跳過 QQQ 這一階。
3. **緩衝區（遲滯）**：進場與出場用不同的門檻，分數在門檻附近來回時不會
   跟著來回換檔。

**這不是模組一文件裡的階梯。** 原本的階梯仍然完整保留並一起回測，兩條
績效並列呈現——否則「改了之後比較好」這句話沒有比較基準。

遲滯是有代價的，不是純粹的改進：它讓出場慢半拍。分數真的轉壞時，
多抱的那幾天在 TQQQ 上會放大成好幾個百分點。緩衝區寬度就是在
「少換手」與「慢出場」之間選一個點，沒有免費的午餐。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

TQQQ, QQQ, SGOV = "TQQQ", "QQQ", "SGOV"


@dataclass(frozen=True)
class AllInRules:
    """門檻設定。中心線沿用模組一的燈號分界（±0.4），不另外發明數字。"""

    tqqq_center: float = 0.4      # 綠區下緣：以上才考慮槓桿
    sgov_center: float = -0.4     # 紅區上緣：以下轉為現金
    buffer: float = 0.15          # 緩衝半寬（遲滯）
    # 從 SGOV 回升時直接進 TQQQ 的門檻。預設等於 TQQQ 的出場線——
    # 設得比出場線低的話，剛跳進 TQQQ 就會立刻被判定該退場，來回空轉。
    recovery_enter: float = None

    def __post_init__(self):
        if self.recovery_enter is None:
            object.__setattr__(self, "recovery_enter", self.tqqq_center - self.buffer)
        if self.recovery_enter < self.tqqq_exit:
            raise ValueError(
                f"加速門檻 {self.recovery_enter} 低於 TQQQ 出場線 {self.tqqq_exit}，"
                "會造成剛進場就出場的來回空轉"
            )

    # 進場門檻高、出場門檻低，中間那段就是緩衝區
    @property
    def tqqq_enter(self) -> float:
        return self.tqqq_center + self.buffer

    @property
    def tqqq_exit(self) -> float:
        return self.tqqq_center - self.buffer

    @property
    def qqq_enter(self) -> float:
        return self.sgov_center + self.buffer

    @property
    def sgov_enter(self) -> float:
        return self.sgov_center - self.buffer


def _initial_state(score: float, rules: AllInRules) -> str:
    """第一天沒有前一個狀態，緩衝區無從適用，直接用中心線分。"""
    if score >= rules.tqqq_center:
        return TQQQ
    if score >= rules.sgov_center:
        return QQQ
    return SGOV


def next_state(state: str, score: float, rules: AllInRules) -> str:
    """單日狀態轉移。純函式，所有規則都在這裡。"""
    if state is None:
        return _initial_state(score, rules)

    if state == SGOV:
        # 加速條款：從現金回升時跳過 QQQ 直接進 TQQQ
        if score >= rules.recovery_enter:
            return TQQQ
        if score >= rules.qqq_enter:
            return QQQ
        return SGOV

    if state == QQQ:
        if score < rules.sgov_enter:
            return SGOV
        if score >= rules.tqqq_enter:
            return TQQQ
        return QQQ

    # state == TQQQ
    if score < rules.sgov_enter:
        return SGOV
    if score < rules.tqqq_exit:
        return QQQ
    return TQQQ


def _apply_cap(state: str, cap) -> str:
    """閘門上限只會讓部位更保守。

    上限也會改寫**狀態**而不只是當天的持倉：使用者看到的是實際部位，
    「從 SGOV 回升」講的就是實際從現金回來，而不是「計分上的假想部位」。
    因此被閘門壓成現金之後，解除時同樣走加速條款。
    """
    if not isinstance(cap, str) or not cap:
        return state
    if cap == SGOV:
        return SGOV
    if cap == QQQ and state == TQQQ:
        return QQQ
    return state


def allin_weights(
    composite: pd.DataFrame,
    gate_a: pd.DataFrame,
    gate_b: pd.Series,
    rules: AllInRules = None,
    use_gates: bool = True,
) -> pd.DataFrame:
    """逐日跑狀態機，回傳目標權重（尚未套用執行時滯，那是引擎的責任）。

    路徑相依，沒辦法向量化：今天的部位取決於昨天的部位。
    """
    rules = rules or AllInRules()
    records, state = [], None

    for ts in composite.index:
        score = composite.at[ts, "composite_score"]
        if pd.isna(score):
            # 沒有分數就維持現狀，不是「歸零」也不是「進場」
            records.append({"date": ts, "state": state, "cap": None, "score": np.nan})
            continue

        state = next_state(state, float(score), rules)
        cap = None
        if use_gates:
            caps = [c for c in (gate_a.at[ts, "cap"], gate_b.at[ts]) if isinstance(c, str) and c]
            if caps:
                cap = SGOV if SGOV in caps else QQQ
                state = _apply_cap(state, cap)
        records.append({"date": ts, "state": state, "cap": cap, "score": float(score)})

    out = pd.DataFrame(records).set_index("date")
    for asset in (QQQ, TQQQ, SGOV):
        out[asset] = (out["state"] == asset).astype(float)
    # 尚未進場的日子要留成缺值，不能是「三個都 0」——後者會被當成一個決定
    out.loc[out["state"].isna(), [QQQ, TQQQ, SGOV]] = np.nan
    out["alloc"] = out["state"].map(lambda s: f"100% {s}" if isinstance(s, str) else None)
    return out
