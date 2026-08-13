"""每日日誌。欄位與接線說明第 5 節的 CSV schema 逐一對應。

**為什麼要照抄那份 schema**：其中幾欄不是給人看的，是給「事後能不能驗證」
用的。`base_w_*` 保留主評分原始值，才算得出模組貢獻；`tqqq_close` / `qqq_close`
是為了在快閘真的觸發後，實測出場到回場之間的報酬差——也就是 `whipsaw_cost`。
那個參數目前純屬估計值，是整個 EV 模型最未經驗證的一環（說明第 5、8 節）。

少存一欄，日後就少一個能回答的問題，而且要等到下一次危機才會發現。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from .config import DAILY_LOG_PATH

# 順序即說明第 5 節的分組順序。REQUIRED 是該節標粗體的「必存」欄位——
# 少了它們，狀態機無法運作或事後無法驗證。
REQUIRED_COLUMNS = [
    "state_active_veto",
    "state_clean_days",
    "state_consec_inversion",
    "base_w_tqqq", "base_w_qqq", "base_w_sgov",
]

COLUMNS = [
    "as_of",
    # 狀態持久化
    "state_active_veto", "state_clean_days", "state_consec_inversion",
    # 慢閘
    "cp_beta", "cp_stress_level", "cp_chg250_pctile", "cp_hyg_lqd_chg60",
    "cp_score", "cp_regime", "cp_equity_ceiling",
    # 快閘
    "fg_vix9d", "fg_vix", "fg_vix3m", "fg_curve_inverted", "fg_vvix_pctile",
    "fg_hy_chg20_bp", "fg_n_categories", "fg_raw_veto", "fg_gated_veto",
    # 擁擠度與輸出
    "cr_score", "cr_threshold_shift",
    "out_w_tqqq", "out_w_qqq", "out_w_sgov",
    "base_w_tqqq", "base_w_qqq", "base_w_sgov",
    # 驗證專用
    "tqqq_close", "qqq_close",
]


def build_row(as_of: str, result, vol: dict, hy_chg20_bp: Optional[float],
              prices: dict) -> dict:
    """把一次 apply() 的結果攤平成一列。"""
    slow_d = (result.slow.detail or {}) if result.slow else {}
    fast_d = (result.fast.detail or {}) if result.fast else {}
    o_t, o_q, o_s = result.out_alloc
    b_t, b_q, b_s = result.base_alloc

    return {
        "as_of": as_of,
        "state_active_veto": result.state.active_veto,
        "state_clean_days": result.state.clean_days,
        "state_consec_inversion": result.state.consec_inversion,

        "cp_beta": slow_d.get("beta"),
        "cp_stress_level": slow_d.get("stress_level"),
        "cp_chg250_pctile": slow_d.get("chg250_pctile"),
        "cp_hyg_lqd_chg60": slow_d.get("hyg_lqd_chg60"),
        "cp_score": None if result.slow is None else round(result.slow.score, 3),
        "cp_regime": None if result.slow is None else result.slow.regime,
        "cp_equity_ceiling": None if result.slow is None else result.slow.equity_ceiling,

        "fg_vix9d": vol.get("vix9d"),
        "fg_vix": vol.get("vix"),
        "fg_vix3m": vol.get("vix3m"),
        "fg_curve_inverted": bool(result.fast.curve_inverted) if result.fast else False,
        "fg_vvix_pctile": fast_d.get("vvix_pctile"),
        "fg_hy_chg20_bp": hy_chg20_bp,
        "fg_n_categories": result.fast.n_categories if result.fast else 0,
        "fg_raw_veto": result.fast.raw_veto if result.fast else 0,
        "fg_gated_veto": result.detail.get("gated_veto"),

        "cr_score": result.crowd.score if result.crowd else None,
        "cr_threshold_shift": result.crowd.threshold_shift if result.crowd else None,

        "out_w_tqqq": o_t, "out_w_qqq": o_q, "out_w_sgov": o_s,
        "base_w_tqqq": b_t, "base_w_qqq": b_q, "base_w_sgov": b_s,

        "tqqq_close": prices.get("tqqq"),
        "qqq_close": prices.get("qqq"),
    }


def missing_required(row: dict) -> list:
    """回傳缺漏的必存欄位。空 list 代表齊全。"""
    return [c for c in REQUIRED_COLUMNS if row.get(c) is None]


def append_row(row: dict, path: str = DAILY_LOG_PATH) -> Path:
    """寫入一列；同一天重跑覆蓋而不是追加。"""
    missing = missing_required(row)
    if missing:
        # 必存欄位缺漏要當場失敗。讓它靜靜寫進去，等於留下一份
        # 事後無法驗證、當下卻看起來正常的日誌
        raise ValueError(f"日誌缺少必存欄位：{'、'.join(missing)}")

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([{c: row.get(c) for c in COLUMNS}])
    if p.exists():
        old = pd.read_csv(p)
        old = old[old["as_of"].astype(str) != str(row["as_of"])]
        df = pd.concat([old, df], ignore_index=True)
    df = df.reindex(columns=COLUMNS).sort_values("as_of")
    df.to_csv(p, index=False)
    return p


def load_log(path: str = DAILY_LOG_PATH) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_csv(p)


def measure_whipsaw(log: pd.DataFrame) -> dict:
    """實測 whipsaw_cost：快閘出場到回場之間，TQQQ 相對 QQQ 的報酬差。

    說明第 5 節存 `tqqq_close` / `qqq_close` 就是為了這件事。
    在真的觸發過之前，這裡只會回報「還沒有樣本」——那是誠實的答案，
    而不是拿估計值充數。
    """
    if log.empty or "state_active_veto" in log and log["state_active_veto"].isna().all():
        return {"episodes": 0, "note": "尚無日誌"}

    df = log.dropna(subset=["state_active_veto"]).sort_values("as_of").reset_index(drop=True)
    veto = df["state_active_veto"].astype(float) > 0
    episodes = []
    start = None
    for i, on in enumerate(veto):
        if on and start is None:
            start = i
        elif not on and start is not None:
            episodes.append((start, i))
            start = None

    out = []
    for a, b in episodes:
        try:
            t0, t1 = float(df.loc[a, "tqqq_close"]), float(df.loc[b, "tqqq_close"])
            q0, q1 = float(df.loc[a, "qqq_close"]), float(df.loc[b, "qqq_close"])
        except (ValueError, TypeError, KeyError):
            continue
        if not (t0 > 0 and q0 > 0):
            continue
        out.append({"from": str(df.loc[a, "as_of"]), "to": str(df.loc[b, "as_of"]),
                    "days": int(b - a),
                    "tqqq_ret": round(t1 / t0 - 1.0, 5),
                    "qqq_ret": round(q1 / q0 - 1.0, 5),
                    "avoided": round((t1 / t0) - (q1 / q0), 5)})

    if not out:
        return {"episodes": 0,
                "note": "快閘尚未完整觸發並回場過，whipsaw_cost 仍是估計值（EV 模型最未驗證的一環）"}
    avg = sum(e["avoided"] for e in out) / len(out)
    return {"episodes": len(out), "mean_avoided": round(avg, 5), "detail": out[-10:]}
