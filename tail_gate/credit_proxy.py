"""慢閘的免費資料版：用 HYG / LQD / IEF 合成信用壓力代理。

**為什麼需要代理**：真正想看的是 HY OAS，但那是 ICE 授權資料。本專案實測
FRED 只給得出近三年（`liquidity_monitor/sources/fred.py` 有完整診斷），
三年不足以定百分位門檻。HYG 自 2007-04 上市，涵蓋 2008 與 2020 兩次危機。

**核心處理：把久期洗掉。** HYG 的價格同時受兩件事影響——信用利差、以及
利率本身。利率大跌時 HYG 會上漲，但那不代表信用轉好。若不對沖，
2022 年的升息會被誤讀成信用惡化，2020 年三月的降息則會抵銷掉真正的信用崩壞。
因此合成指數取 `HYG 報酬 − beta × IEF 報酬`，beta 由滾動迴歸估計。

**除息調整不是細節，是這個模組能不能用的前提。** HYG 年配息約 6~7%；
用未調整價，合成指數每年會假性下跌數個百分點，於是「500 日回撤」永遠看起來
很大，慢閘長期誤報。這個錯不會拋例外，只會讓紅燈變成常態而沒有人起疑。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .config import (
    CALIBRATION_TARGETS,
    HEDGE_BETA_FIXED,
    HEDGE_BETA_WINDOW,
    REGIME_CEILING,
    REGIME_ORDER,
)

log = logging.getLogger(__name__)


@dataclass
class CreditProxyInputs:
    """三條**除息調整後**的價格序列。欄位名稱沿用接線說明的寫法。"""

    hyg: pd.Series
    ief: pd.Series
    lqd: Optional[pd.Series] = None


@dataclass
class GateOutput:
    """兩種慢閘共用的回傳型別。

    介面相容是說明第 3 節明講的要求：`.regime` / `.equity_ceiling` /
    `.score` / `.detail` 四個欄位，換閘門時上層不必改。
    """

    regime: str
    equity_ceiling: float
    score: float
    detail: dict = field(default_factory=dict)


def _log_returns(s: pd.Series) -> pd.Series:
    return np.log(s.astype(float)).diff()


def rolling_hedge_beta(hyg: pd.Series, ief: pd.Series,
                       window: int = HEDGE_BETA_WINDOW) -> float:
    """估計 HYG 對 IEF 的久期 beta。

    樣本不足時退回固定值而不是拋錯：模組剛上線、或序列剛好被截斷時，
    「算不出 beta」不該讓整個閘門停擺。但要把用了哪一種記進 detail——
    固定值與估計值的差異正是說明第 6 節要求比較的東西。
    """
    a, b = _log_returns(hyg), _log_returns(ief)
    df = pd.concat([a, b], axis=1).dropna()
    if len(df) < max(60, window // 4):
        return HEDGE_BETA_FIXED
    df = df.iloc[-window:]
    var = float(df.iloc[:, 1].var())
    if not np.isfinite(var) or var <= 0:
        return HEDGE_BETA_FIXED
    return float(df.cov().iloc[0, 1] / var)


def synthetic_credit_index(hyg: pd.Series, ief: pd.Series,
                           beta: Optional[float] = None) -> tuple[pd.Series, float]:
    """久期對沖後的信用指數（起點 100）。"""
    b = rolling_hedge_beta(hyg, ief) if beta is None else float(beta)
    r = (_log_returns(hyg) - b * _log_returns(ief)).dropna()
    return 100.0 * np.exp(r.cumsum()), b


def stress_level(index: pd.Series, lookback: int = 500, ma_window: int = 200) -> float:
    """壓力水準：500 日回撤與「低於 200 日均線的幅度」取大者。

    取大者而不是相加：兩者測的是同一件事的不同切面（絕對回撤 vs 趨勢破壞），
    相加會在兩者同時出現時重複計分，把單一事件放大成兩倍。
    """
    s = index.dropna()
    if s.empty:
        return 0.0
    recent = s.iloc[-lookback:]
    peak = float(recent.max())
    last = float(s.iloc[-1])
    drawdown = 0.0 if peak <= 0 else max(0.0, (peak - last) / peak)

    ma = s.rolling(ma_window, min_periods=max(20, ma_window // 4)).mean()
    below = 0.0
    if not ma.dropna().empty:
        m = float(ma.iloc[-1])
        if m > 0:
            below = max(0.0, (m - last) / m)
    return max(drawdown, below)


def rolling_percentile(series: pd.Series, window: int = 250) -> float:
    """最新值在最近 `window` 期分布中的百分位（0-100）。"""
    s = series.dropna().iloc[-window:]
    if len(s) < 20:
        return 50.0
    return float((s <= s.iloc[-1]).mean() * 100.0)


def _regime_from_score(score: float) -> str:
    # 說明沒有給分數到 regime 的切點，這是本專案訂的，且**未經校準**。
    # 均分四段是刻意的選擇：在沒有資料支持的情況下，任何「看起來更講究」的
    # 切點都只是把主觀包裝成客觀。
    if score < 25:
        return "EXPANSION"
    if score < 50:
        return "WATCH"
    if score < 75:
        return "CONTRACTION"
    return "STRESS"


class CreditProxyGate:
    """免費資料版慢閘。介面與 SlowCreditGate 相容。"""

    name = "CreditProxyGate"
    uses_licensed_data = False
    detects_funding_stress = False      # 說明第 2 節：這一格是「無」

    def evaluate(self, inputs: CreditProxyInputs) -> GateOutput:
        index, beta = synthetic_credit_index(inputs.hyg, inputs.ief)
        level = stress_level(index)
        chg250 = index.pct_change(250)
        chg_pctile = rolling_percentile(chg250)

        # HY vs IG 的 60 日相對變化：品質離散。HY 跑輸 IG 代表市場在挑品質，
        # 那通常比整體下跌更早出現。LQD 缺漏時這一項不計分（不是給 0 分——
        # 給 0 會被當成「品質沒有離散」，那是資料缺漏冒充成訊號）。
        hyg_lqd_chg60 = None
        if inputs.lqd is not None and not inputs.lqd.dropna().empty:
            pair = pd.concat([inputs.hyg, inputs.lqd], axis=1).dropna()
            if len(pair) > 61:
                rel = (pair.iloc[:, 0] / pair.iloc[:, 1])
                hyg_lqd_chg60 = float(rel.iloc[-1] / rel.iloc[-61] - 1.0)

        # 分數 0-100。三項各自標準化後加權；權重是本專案訂的，未經校準。
        # 壓力水準給最高權重，因為它是唯一直接對應「已經發生的損失」的項目。
        parts = {
            "stress": min(1.0, level / 0.15) * 55.0,
            "chg_pctile": (1.0 - chg_pctile / 100.0) * 30.0,
        }
        if hyg_lqd_chg60 is not None:
            parts["quality_dispersion"] = min(1.0, max(0.0, -hyg_lqd_chg60 / 0.05)) * 15.0
            score = sum(parts.values())
        else:
            # 少一項時按可得權重重新分配，而不是讓總分天花板憑空降低——
            # 否則缺 LQD 就永遠進不了 STRESS，而畫面上看不出來
            score = sum(parts.values()) / 85.0 * 100.0

        score = float(max(0.0, min(100.0, score)))
        regime = _regime_from_score(score)
        return GateOutput(
            regime=regime,
            equity_ceiling=REGIME_CEILING[regime],
            score=score,
            detail={
                "beta": round(beta, 4),
                "beta_source": "rolling" if beta != HEDGE_BETA_FIXED else "fixed_or_fallback",
                "stress_level": round(level, 4),
                "chg250_pctile": round(chg_pctile, 2),
                "hyg_lqd_chg60": None if hyg_lqd_chg60 is None else round(hyg_lqd_chg60, 5),
                "index_last": round(float(index.iloc[-1]), 3) if not index.empty else None,
                "components": {k: round(v, 2) for k, v in parts.items()},
                "uncalibrated": True,
            },
        )


def calibrate_against_oas(inputs: CreditProxyInputs, hy_oas: pd.Series) -> dict:
    """說明第 6 節第一步的驗證程序。

    三年樣本不足以定百分位門檻，但足以判斷**方向對不對**。方向錯了，
    門檻調再多也沒用——所以這一步是「必做」，而且要在接下單邏輯之前做完。
    """
    index, beta = synthetic_credit_index(inputs.hyg, inputs.ief)
    oas = hy_oas.dropna().astype(float)

    # 代理指數的回撤序列 vs OAS 水準：兩者應同向（壓力大時都變大）
    roll_peak = index.cummax()
    dd = (roll_peak - index) / roll_peak
    joined = pd.concat([dd.rename("dd"), oas.rename("oas")], axis=1, sort=True).dropna()

    out: dict = {"n_obs": int(len(joined)), "beta": round(beta, 4)}
    if len(joined) < 60:
        out["error"] = f"重疊樣本僅 {len(joined)} 筆，不足以判讀"
        return out

    out["corr_level_dd_vs_oas"] = round(float(joined["dd"].corr(joined["oas"])), 4)

    chg = pd.concat([index.pct_change(60).rename("idx"),
                     oas.diff(60).rename("oas")], axis=1, sort=True).dropna()
    out["corr_change_60d"] = (round(float(chg["idx"].corr(chg["oas"])), 4)
                              if len(chg) >= 60 else None)

    # --- 分級一致率 --------------------------------------------------------
    #
    # **這裡原本量錯了東西。** 第一版拿代理的「絕對門檻分級」去比 OAS 的
    # 「四分位分級」，實測得到 0.2865，看起來像代理失敗。但四分位在結構上
    # 強制各 25%——平靜期裡它會把最近三年相對最差的 25% 天標成 STRESS，
    # 而那些日子的絕對壓力可能只有 3% 回撤，代理當然說 EXPANSION。
    #
    # 用**完美相關**（corr=1.0）的合成序列驗證過：舊指標回 0.2508，
    # 與實測的 0.2865 幾乎一樣。也就是說那個指標無論代理好壞都不會過關——
    # 一個過不了的量尺，量不出任何東西。
    #
    # 因此改為兩個指標，各自回答不同的問題，兩個都報出來：
    #   rank      兩邊都用四分位 —— 代理**排序**日子的方式跟 OAS 一樣嗎
    #             （這才是「方向對不對」，doc 第 6 節的 0.70 門檻用這個）
    #   absolute  代理的正式門檻 vs OAS 四分位 —— 出貨用的門檻對不對
    #             （門檻本來就未校準，這一項預期不會過，保留是為了讓那件事
    #              有數字，而不是被新指標蓋掉）
    stress_series = _stress_series(index).reindex(joined.index).dropna()
    if len(stress_series) >= 60:
        proxy_score = np.minimum(100.0, stress_series / 0.15 * 100.0)
        oas_aligned = joined["oas"].reindex(stress_series.index)

        oas_regimes = _quartile_regimes(oas_aligned)
        out["regime_agreement_rank"] = round(float(np.mean(
            [a == b for a, b in zip(_quartile_regimes(proxy_score), oas_regimes)])), 4)
        out["regime_agreement_absolute"] = round(float(np.mean(
            [_regime_from_score(s) == b
             for s, b in zip(proxy_score, oas_regimes)])), 4)
        # 對外仍叫 regime_agreement（doc 的用語），指向公平的那一個
        out["regime_agreement"] = out["regime_agreement_rank"]

    out["targets"] = CALIBRATION_TARGETS
    out["passed"] = _passes(out)
    return out


def _stress_series(index: pd.Series, lookback: int = 500, ma_window: int = 200) -> pd.Series:
    """逐日的壓力水準。與 stress_level() 同一套定義，但向量化。

    原本在校準迴圈裡逐列呼叫 stress_level()，614 列各做一次 500 列的滾動——
    結果一樣，但慢得沒有道理。
    """
    s = index.dropna()
    peak = s.rolling(lookback, min_periods=20).max()
    drawdown = ((peak - s) / peak).clip(lower=0.0)
    ma = s.rolling(ma_window, min_periods=max(20, ma_window // 4)).mean()
    below = ((ma - s) / ma).clip(lower=0.0)
    return pd.concat([drawdown, below], axis=1).max(axis=1).dropna()


def _quartile_regimes(values) -> list:
    """用序列自己的四分位切成四級。兩邊都用同一套，比較才公平。"""
    s = pd.Series(values).dropna()
    q = s.quantile([0.25, 0.50, 0.75]).tolist()
    return [REGIME_ORDER[sum(v > t for t in q)] for v in s]


def _passes(result: dict) -> Optional[bool]:
    """三項全達標才算通過。任一項缺值就回 None——「還不知道」與「沒通過」
    是兩件事，混在一起會讓人以為驗證做過了。"""
    checks = []
    for key, target in CALIBRATION_TARGETS.items():
        value = result.get(key)
        if value is None:
            return None
        checks.append(value < target if key == "corr_change_60d" else value > target)
    return all(checks)
