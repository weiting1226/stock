"""三道閘門（文件第163-200行）與軌道二第一層（簡化版每日收盤近似）。

Gate A / C 是規則式判定，不需要歷史統計。Gate B 需要「過去10年同期
分布」的百分位——本專案用自己爬到、累積儲存的每日歷史序列計算滾動
百分位；若累積歷史不足（例如剛部署、還沒有10年資料），改用文件表格
提供的 2026 年參考門檻（`config.GATE_B_FALLBACK_THRESHOLDS`）並明確
標註為「近似值」，不得改用固定門檻造假成真百分位（文件第191行的告誡）。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .config import GATE_B_FALLBACK_THRESHOLDS

TEN_YEAR_TRADING_DAYS = 252 * 10


def percentile_rank(series: pd.Series, value: float, window_days: int = TEN_YEAR_TRADING_DAYS) -> Optional[float]:
    """回傳 `value` 在 `series` 最近 `window_days` 筆歷史中的百分位（0-100）。"""
    hist = series.dropna().tail(window_days)
    if len(hist) < 60 or value is None or np.isnan(value):
        return None
    return round(100.0 * (hist < value).sum() / len(hist), 2)


# ---------------------------------------------------------------------------
# Gate A — 價格閘門
# ---------------------------------------------------------------------------

def gate_a(ndx_close: pd.Series, as_of: str) -> dict:
    s = ndx_close.dropna().loc[:as_of]
    if len(s) < 220:
        return {"status": "暫缺", "cap": None, "detail": "NDX 歷史資料不足以計算200日線"}

    ma200 = s.rolling(200).mean()
    ma50 = s.rolling(50).mean()
    price = float(s.iloc[-1])
    ma200_now = float(ma200.iloc[-1])
    ma50_now = float(ma50.iloc[-1])
    ma200_20d_ago = float(ma200.iloc[-21]) if len(ma200) > 21 else None
    ma200_sloping_up = (ma200_20d_ago is not None) and (ma200_now > ma200_20d_ago)

    below_200 = price < ma200_now
    below_200_and_50_below_200 = below_200 and (ma50_now < ma200_now)

    if below_200_and_50_below_200:
        return {
            "status": "觸發：強制清倉",
            "cap": "SGOV",
            "detail": f"NDX({price:.1f}) < 200日線({ma200_now:.1f}) 且 50日線({ma50_now:.1f}) < 200日線",
        }
    if below_200:
        return {
            "status": "觸發：禁用槓桿",
            "cap": "QQQ",
            "detail": f"NDX({price:.1f}) < 200日線({ma200_now:.1f})",
        }
    if ma200_sloping_up:
        return {
            "status": "未觸發：無上限",
            "cap": None,
            "detail": f"NDX({price:.1f}) > 200日線({ma200_now:.1f}) 且200日線上彎",
        }
    return {
        "status": "邊界情況：保守禁用槓桿",
        "cap": "QQQ",
        "detail": (
            f"NDX({price:.1f}) > 200日線({ma200_now:.1f}) 但200日線未明確上彎"
            "（文件未定義此情境，本專案採保守假設禁用槓桿）"
        ),
    }


# ---------------------------------------------------------------------------
# Gate B — 脆弱度閘門
# ---------------------------------------------------------------------------

def gate_b(
    margin_debt_yoy_hist: pd.Series,
    hy_oas_hist: pd.Series,
    margin_debt_gdp_hist: pd.Series,
    vix_hist: pd.Series,
    skew_hist: pd.Series,
    ndx_fwd_pe_hist: Optional[pd.Series],
    as_of: str,
) -> dict:
    checks = {}

    def _check(name: str, hist: Optional[pd.Series], fallback_key: str, direction: str):
        if hist is None or hist.dropna().empty:
            checks[name] = {"triggered": None, "note": "暫缺", "approx": True}
            return
        latest = float(hist.dropna().iloc[-1])
        pctile = percentile_rank(hist, latest)
        if pctile is not None:
            triggered = pctile > 85 if direction == "high" else pctile < 15
            checks[name] = {"triggered": triggered, "percentile": pctile, "value": latest, "approx": False}
        else:
            threshold = GATE_B_FALLBACK_THRESHOLDS.get(fallback_key)
            if threshold is None:
                checks[name] = {"triggered": None, "note": "暫缺", "approx": True}
                return
            triggered = latest > threshold if direction == "high" else latest < threshold
            checks[name] = {
                "triggered": triggered,
                "value": latest,
                "threshold": threshold,
                "approx": True,
                "note": "歷史資料不足10年，使用文件2026年參考門檻近似值",
            }

    _check("margin_debt_yoy", margin_debt_yoy_hist, "margin_debt_yoy", "high")
    _check("hy_oas", hy_oas_hist, "hy_oas_level", "low")
    _check("margin_debt_gdp", margin_debt_gdp_hist, "margin_debt_gdp", "high")
    _check("ndx_fwd_pe", ndx_fwd_pe_hist, "ndx_fwd_pe", "high")

    vix_latest = float(vix_hist.dropna().iloc[-1]) if vix_hist is not None and not vix_hist.dropna().empty else None
    skew_latest = float(skew_hist.dropna().iloc[-1]) if skew_hist is not None and not skew_hist.dropna().empty else None
    vix_pctile = percentile_rank(vix_hist, vix_latest) if vix_latest is not None else None
    skew_pctile = percentile_rank(skew_hist, skew_latest) if skew_latest is not None else None
    if vix_pctile is not None and skew_pctile is not None:
        checks["vix_low_skew_high"] = {
            "triggered": (vix_pctile < 20) and (skew_pctile > 85),
            "vix_percentile": vix_pctile,
            "skew_percentile": skew_pctile,
            "approx": False,
        }
    else:
        checks["vix_low_skew_high"] = {"triggered": None, "note": "暫缺", "approx": True}

    triggered_count = sum(1 for c in checks.values() if c.get("triggered") is True)
    disable_leverage = triggered_count >= 3

    return {
        "checks": checks,
        "triggered_count": triggered_count,
        "cap": "QQQ" if disable_leverage else None,
        "status": f"觸發 {triggered_count}/5 項" + ("：禁用槓桿(上限QQQ)" if disable_leverage else "：未達門檻"),
    }


# ---------------------------------------------------------------------------
# Gate C — 資料完整度閘門
# ---------------------------------------------------------------------------

def gate_c(
    missing_or_low_conf_count: int,
    composite_score: float,
    policy_direction_any_missing: bool,
    light_bands: list[tuple[float, float, str]],
) -> dict:
    reasons = []
    cap = None

    if missing_or_low_conf_count >= 3:
        cap = "QQQ"
        reasons.append(f"缺失或低信心項達 {missing_or_low_conf_count} 項 (>=3)")

    if policy_direction_any_missing:
        cap = "QQQ"
        reasons.append("⑥ 政策方向類別至少一子項缺失")

    near_boundary = False
    for lo, hi, _label in light_bands:
        for bound in (lo, hi):
            if bound in (float("inf"), float("-inf")):
                continue
            if abs(composite_score - bound) <= 0.10:
                near_boundary = True
    downgrade_one_level = near_boundary
    if near_boundary:
        reasons.append(f"綜合分數 {composite_score} 落在燈號邊界 ±0.10 內，降一級處理")

    return {
        "cap": cap,
        "downgrade_one_level": downgrade_one_level,
        "reasons": reasons or ["未觸發"],
    }


# ---------------------------------------------------------------------------
# 軌道二（簡化）— 僅用每日收盤資料近似第一層訊號，非真正盤中監控
# ---------------------------------------------------------------------------

def track2_layer1_daily_approx(
    vix: float, vix9d: Optional[float], ndx_1d_chg_pct: Optional[float],
    dgs10_1d_chg_bp: Optional[float], dxy_1d_chg_pct: Optional[float],
    gold_1d_chg_pct: Optional[float], tlt_1d_chg_pct: Optional[float],
) -> dict:
    """以「日收盤對日收盤」近似文件第207-211行的第一層訊號。

    這不是真正的盤中即時監控（文件本身也說明軌道二屬於盤中機制），
    僅供儀表板顯示「若用日頻資料回推，訊號是否成立」的參考，
    使用時務必留意其局限性（見文件第230行）。
    """
    signals = {}
    signals["vix9d_gt_vix"] = (vix9d is not None and vix is not None and vix9d > vix)
    signals["treasury_1d_move_gt_15bp"] = (
        dgs10_1d_chg_bp is not None and abs(dgs10_1d_chg_bp) > 15
    )
    signals["ndx_1d_drop_gt_2pct"] = (
        ndx_1d_chg_pct is not None and ndx_1d_chg_pct < -2
    )
    cross_asset_down = (
        ndx_1d_chg_pct is not None and tlt_1d_chg_pct is not None
        and gold_1d_chg_pct is not None and dxy_1d_chg_pct is not None
        and ndx_1d_chg_pct < 0 and tlt_1d_chg_pct < 0
        and gold_1d_chg_pct < 0 and dxy_1d_chg_pct < 0
    )
    signals["stocks_bonds_gold_dollar_all_down"] = cross_asset_down

    n_true = sum(1 for v in signals.values() if v is True)
    n_unknown = sum(1 for v in signals.values() if v is None)

    return {
        "signals": signals,
        "triggered_count": n_true,
        "unknown_count": n_unknown,
        "circuit_breaker_suggested": n_true >= 2,
        "reliability_note": (
            "軌道二第二、三層與熔斷判定需盤中即時資料，每日批次爬蟲無法取得，"
            "本區僅為收盤後近似估計，不得作為加碼依據（比照文件第230行原則）。"
        ),
    }
