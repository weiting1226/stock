"""快閘：波動率期限結構倒掛 + 波動的波動 + 信用急變。

**這個閘門的統計基礎是 n=1。** 樣本裡真正的危機型事件只有 2020 一個
（接線說明第 0 節）。它每天都會給出一個看起來很篤定的等級，但那個等級背後
沒有足以支撐的樣本數。畫面必須說出這件事。

三個類別各自獨立判定，數量決定否決等級（`fg_n_categories` 0-3）：

  期限結構   VIX9D > VIX > VIX3M —— 近月恐慌溢價高於遠月，典型的急跌訊號
  波動的波動 VVIX 百分位偏高 —— 市場在為「波動還會更大」付錢
  信用急變   HY OAS 20 日變化（bp）—— 信用市場的急性反應

**持續期閘門是刻意放棄一部分事件的。** 說明第 8 節：Type II（踩踏型）的
干預 EV ≈ 0，是最頻繁的事件類型卻無法獲利，因此用 `persist_days` 主動放棄。
把 persist_days 調小會抓到更多事件，但抓到的多半是那一類——看起來反應更快，
實際上是在賠手續費。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FastGateInputs:
    """欄位名稱沿用接線說明第 3 節的寫法。

    全部可省略：資料缺漏時該類別不計分，而不是當成「沒有訊號」。
    這兩者在畫面上必須分得出來——`missing` 會被列進 detail。
    """

    vix9d: Optional[float] = None
    vix: Optional[float] = None
    vix3m: Optional[float] = None
    vvix: Optional[float] = None
    vvix_pctile: Optional[float] = None
    hy_oas: Optional[float] = None
    hy_chg20_bp: Optional[float] = None


@dataclass
class FastGateOutput:
    raw_veto: int
    n_categories: int
    curve_inverted: bool
    detail: dict = field(default_factory=dict)


# 門檻。說明沒有給 VVIX 與 HY 的具體數值，以下是本專案訂的，**未經校準**。
VVIX_PCTILE_TRIGGER = 80.0
HY_CHG20_TRIGGER_BP = 75.0


def evaluate_fast(inputs: FastGateInputs, threshold_shift: float = 0.0) -> FastGateOutput:
    """回傳未經持續期閘門的原始等級。

    `threshold_shift` 來自擁擠度：市場越擁擠，門檻越低（越容易觸發）。
    位移量是加在百分位／bp 上的絕對值，正數代表「更難觸發」，
    因此擁擠時傳入負數。
    """
    triggered, missing = [], []

    # 期限結構：三個值缺一不可，少一個就無法判斷順序
    if None in (inputs.vix9d, inputs.vix, inputs.vix3m):
        missing.append("term_structure")
        curve_inverted = False
    else:
        curve_inverted = inputs.vix9d > inputs.vix > inputs.vix3m
        if curve_inverted:
            triggered.append("term_structure")

    if inputs.vvix_pctile is None:
        missing.append("vol_of_vol")
    elif inputs.vvix_pctile >= VVIX_PCTILE_TRIGGER + threshold_shift:
        triggered.append("vol_of_vol")

    if inputs.hy_chg20_bp is None:
        missing.append("credit_shock")
    elif inputs.hy_chg20_bp >= HY_CHG20_TRIGGER_BP + threshold_shift:
        triggered.append("credit_shock")

    n = len(triggered)
    return FastGateOutput(
        raw_veto=n,
        n_categories=n,
        curve_inverted=curve_inverted,
        detail={
            "triggered": triggered,
            "missing": missing,
            "vvix_pctile": inputs.vvix_pctile,
            "hy_chg20_bp": inputs.hy_chg20_bp,
            "threshold_shift": threshold_shift,
            "vvix_trigger": VVIX_PCTILE_TRIGGER + threshold_shift,
            "hy_trigger_bp": HY_CHG20_TRIGGER_BP + threshold_shift,
            "sample_size_note": "危機型事件樣本 n=1（2020），等級不代表統計信心",
        },
    )
