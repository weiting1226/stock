"""慢閘的授權資料版：HY OAS + 融資壓力。

**本專案預設不用這一個**（接線說明第 2 節建議先用 CreditProxyGate），
但仍實作出來，理由有二：

  1. 若日後取得 ICE 授權，兩者可並行，取較保守的 regime。
  2. 它的融資壓力偵測（SOFR-IORB）是 CreditProxyGate 沒有的能力，
     而那兩個 FRED 系列**沒有授權限制**，現在就抓得到。

實測提醒：FRED 的 `BAMLH0A0HYM2` 只給得出近三年（本專案已診斷確認，
見 `liquidity_monitor/sources/fred.py`）。三年不足以定百分位門檻，
所以即使用這個閘門，OAS 的百分位仍然是不可靠的——這正是說明第 2 節
「HY OAS 完整歷史需 ICE 授權」那一行在現實中的樣子。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from .config import REGIME_CEILING
from .credit_proxy import GateOutput, rolling_percentile


@dataclass
class SlowCreditInputs:
    hy_oas: pd.Series
    sofr: Optional[pd.Series] = None
    iorb: Optional[pd.Series] = None
    fra_ois: Optional[pd.Series] = None      # 需授權，可留空
    xccy_basis: Optional[pd.Series] = None   # 需授權，可留空


# SOFR 高於 IORB 多少 bp 算融資壓力。2019/9 回購事件當時的量級約數十 bp。
# 這個門檻同樣**未經校準**。
FUNDING_STRESS_BP = 10.0


class SlowCreditGate:
    name = "SlowCreditGate"
    uses_licensed_data = True
    detects_funding_stress = True

    def evaluate(self, inputs: SlowCreditInputs) -> GateOutput:
        oas = inputs.hy_oas.dropna().astype(float)
        if oas.empty:
            raise ValueError("SlowCreditGate 需要 HY OAS，但序列是空的")

        level_pctile = rolling_percentile(oas, window=250)
        chg60 = float(oas.iloc[-1] - oas.iloc[-61]) * 100 if len(oas) > 61 else 0.0

        funding_bp = None
        if inputs.sofr is not None and inputs.iorb is not None:
            pair = pd.concat([inputs.sofr, inputs.iorb], axis=1).dropna()
            if not pair.empty:
                funding_bp = float(pair.iloc[-1, 0] - pair.iloc[-1, 1]) * 100

        score = 0.6 * level_pctile + 0.4 * min(100.0, max(0.0, chg60 / 2.0))
        if funding_bp is not None and funding_bp >= FUNDING_STRESS_BP:
            # 融資壓力是獨立的升級條件，不是加權項：2019/9 那種事件
            # 信用利差幾乎沒動，靠加權永遠升不上去
            score = max(score, 75.0)

        score = float(max(0.0, min(100.0, score)))
        regime = ("EXPANSION" if score < 25 else "WATCH" if score < 50
                  else "CONTRACTION" if score < 75 else "STRESS")
        return GateOutput(
            regime=regime, equity_ceiling=REGIME_CEILING[regime], score=score,
            detail={"oas_last": round(float(oas.iloc[-1]), 3),
                    "oas_pctile_250d": round(level_pctile, 2),
                    "oas_chg60_bp": round(chg60, 1),
                    "funding_spread_bp": None if funding_bp is None else round(funding_bp, 2),
                    "oas_history_days": int(len(oas)),
                    "uncalibrated": True},
        )


def more_conservative(a: GateOutput, b: GateOutput) -> GateOutput:
    """兩個慢閘並行時取較保守者（說明第 2 節）。"""
    return a if a.equity_ceiling <= b.equity_ceiling else b
