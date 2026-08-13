"""主控制器：把慢閘、快閘、擁擠度合成每日配置，並維護跨日狀態。

**接線說明第 3 節的三條規則，這裡逐條落實成程式碼裡擋得住的東西：**

1. `base_alloc` 是唯一能加碼的來源。本模組是**純否決式**——它不能把你從
   SGOV 拉回 TQQQ。實作上：每一個權益部位只會等於或小於 base，
   釋出的權重一律進 SGOV。`_assert_veto_only()` 每次都檢查，不是只寫在註解裡。

2. `state` 必須跨日持久化。`consec_inversion` 是 `persist_days` 的計數器，
   忘記存就等於**閘門失效**——每天都從 0 重數，永遠到不了 4。
   而且不會報錯，只會安靜地讓快閘永遠不觸發。

3. `hyg` / `ief` / `lqd` 必須是除息調整價。這一條在 data 層擋（見 data.py）。

第 2 條是這個模組最容易出錯、又最看不出來的地方，所以除了持久化本身，
還提供 `assert_state_consistent()`：說明第 7 節建議在日誌寫入後加一行斷言，
若 `fg_curve_inverted` 為真但 `state_consec_inversion` 為 0，直接拋錯。
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Optional

from .config import PERSIST_DAYS, REENTRY_CLEAN_DAYS, TQQQ_HARD_CAP, VETO_CEILING
from .crowding import CrowdingInputs, evaluate_crowding
from .fast import FastGateInputs, evaluate_fast

log = logging.getLogger(__name__)


@dataclass
class GateState:
    """必須跨日保存的三個欄位（說明第 5 節，全部標為必存）。"""

    active_veto: int = 0
    clean_days: int = 0
    consec_inversion: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "GateState":
        d = d or {}
        return cls(
            active_veto=int(d.get("active_veto", 0)),
            clean_days=int(d.get("clean_days", 0)),
            consec_inversion=int(d.get("consec_inversion", 0)),
        )


@dataclass
class GateResult:
    base_alloc: tuple
    out_alloc: tuple
    state: GateState
    slow: object = None
    fast: object = None
    crowd: object = None
    equity_ceiling: float = 1.0
    detail: dict = field(default_factory=dict)


class TailFragilityGate:
    def __init__(self, slow_gate, persist_days: int = PERSIST_DAYS,
                 reentry_clean_days: int = REENTRY_CLEAN_DAYS):
        self.slow_gate = slow_gate
        self.persist_days = int(persist_days)
        self.reentry_clean_days = int(reentry_clean_days)

    def apply(self, base_alloc: tuple, slow, fast: FastGateInputs,
              crowd: Optional[CrowdingInputs] = None,
              state: Optional[GateState] = None) -> GateResult:
        state = GateState.from_dict(state.to_dict()) if isinstance(state, GateState) \
            else GateState.from_dict(state)

        crowd_out = evaluate_crowding(crowd or CrowdingInputs())
        slow_out = self.slow_gate.evaluate(slow)
        fast_out = evaluate_fast(fast, threshold_shift=crowd_out.threshold_shift)

        # --- 規則 2 的核心：倒掛計數器必須從**傳進來的**狀態接著數 ---------
        if fast_out.curve_inverted:
            state.consec_inversion += 1
        else:
            state.consec_inversion = 0

        # 持續期閘門只作用在期限結構那一項：說明第 3 節寫的是
        # 「曲線倒掛需連續 4 日才降級」，不是整個快閘延後。
        # 這個區別有實質後果——信用急變是急性事件，等四天等於錯過。
        effective = [c for c in fast_out.detail["triggered"]
                     if c != "term_structure" or state.consec_inversion >= self.persist_days]
        gated_veto = len(effective)

        if gated_veto == 0:
            state.clean_days += 1
        else:
            state.clean_days = 0

        # 回場延遲：clean_days 未達門檻前，維持前一日的否決等級。
        # 預設 reentry_clean_days=0 代表不延遲（說明第 3 節的校準結論），
        # 此時 max() 右項一定成立，等於直接採用今天的等級。
        if gated_veto >= state.active_veto or state.clean_days >= self.reentry_clean_days:
            state.active_veto = gated_veto
        # else: 保持原等級不變（等 clean_days 累積）

        ceiling = min(slow_out.equity_ceiling, VETO_CEILING[state.active_veto])
        out_alloc = self._scale(base_alloc, ceiling)
        _assert_veto_only(base_alloc, out_alloc)

        return GateResult(
            base_alloc=tuple(base_alloc), out_alloc=out_alloc, state=state,
            slow=slow_out, fast=fast_out, crowd=crowd_out, equity_ceiling=ceiling,
            detail={
                "raw_veto": fast_out.raw_veto,
                "gated_veto": gated_veto,
                "effective_categories": effective,
                "persist_days": self.persist_days,
                "reentry_clean_days": self.reentry_clean_days,
                "slow_ceiling": slow_out.equity_ceiling,
                "veto_ceiling": VETO_CEILING[state.active_veto],
                "tqqq_hard_cap": TQQQ_HARD_CAP,
            },
        )

    @staticmethod
    def _scale(base_alloc: tuple, ceiling: float) -> tuple:
        """按上限縮減權益部位，釋出的權重一律進 SGOV。

        TQQQ 另外套用事前硬上限。說明第 8 節最後一句：那個上限比任何訊號
        都重要，因為它**不依賴任何一個需要校準的參數**——而這個模組裡
        其他每一個門檻都還沒校準。
        """
        w_tqqq, w_qqq, _ = (float(x) for x in base_alloc)
        out_tqqq = min(w_tqqq, TQQQ_HARD_CAP) * ceiling
        out_qqq = w_qqq * ceiling
        return (round(out_tqqq, 6), round(out_qqq, 6),
                round(1.0 - out_tqqq - out_qqq, 6))


def _assert_veto_only(base_alloc: tuple, out_alloc: tuple, tol: float = 1e-9) -> None:
    """規則 1 的實體檢查。

    寫成斷言而不是註解，是因為這條規則違反時的症狀是「績效變好了」——
    模組在某些日子把部位加上去，回測看起來更漂亮，然後在真正需要它的那天
    才發現它根本不是保險。那時已經來不及了。
    """
    b_t, b_q, b_s = (float(x) for x in base_alloc)
    o_t, o_q, o_s = (float(x) for x in out_alloc)
    if o_t > b_t + tol or o_q > b_q + tol:
        raise ValueError(
            f"模組六違反純否決規則：權益部位被加碼了"
            f"（TQQQ {b_t:.4f}→{o_t:.4f}、QQQ {b_q:.4f}→{o_q:.4f}）"
        )
    if o_s < b_s - tol:
        raise ValueError(f"模組六違反純否決規則：SGOV 被減碼（{b_s:.4f}→{o_s:.4f}）")
    if abs(o_t + o_q + o_s - 1.0) > 1e-6:
        raise ValueError(f"配置權重加總不為 1：{o_t + o_q + o_s}")


def assert_state_consistent(row: dict) -> None:
    """說明第 7 節建議的斷言：日誌寫入後檢查狀態真的被保存了。

    「第 5 步的 out.state 漏寫是最常見的失效模式，且不會報錯，
    只會安靜地讓快閘永遠不觸發。」——這一行就是為了讓它報錯。
    """
    if row.get("fg_curve_inverted") and int(row.get("state_consec_inversion", 0)) == 0:
        raise ValueError(
            "曲線倒掛為真，但 state_consec_inversion 是 0——"
            "跨日狀態沒有被保存，快閘等於已經失效（每天都從 0 重數，永遠到不了 "
            f"persist_days）。檢查日誌寫入是否漏了 out.state。"
        )
