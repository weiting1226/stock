"""回補歷史：逐日重播閘門，把「沒有樣本」換成量測值。

**為什麼值得做**：模組六目前最大的問題不是它會不會動，是它的 EV 模型五項假設
全部沒有實測過，其中 `whipsaw_cost` 只要比假設高約 27% 就讓整個模組歸零。
回補十年的話，`false_positive_per_year` 與 `whipsaw_cost` 兩項都變成**量測值**——
那正是說明第 5 節要求存 `tqqq_close` / `qqq_close` 的用意，也是第 6 節第二步。

**這件事唯一真正的風險是前視偏誤**，而且它不會報錯：拿今天的完整序列去算
2018 年的壓力水準，`stress_level` 的 500 日高點會包含 2020 年的崩盤，
於是 2018 年的每一天看起來都「離高點很遠」。回測會變得非常漂亮。

因此這裡的規則只有一條，但沒有例外：**每一天都先把所有序列切到當天為止，
再交給閘門。** 閘門本身完全不知道自己在回測——它拿到的東西跟正式執行時
一模一樣。這也是為什麼回補直接呼叫 `TailFragilityGate.apply()`，
而不是另外寫一份「回測版」的邏輯：另寫一份，兩邊就會慢慢長歪。

**回補的列要標記出來。** 混進日誌而不標，畫面上就會看起來像已經影子運行了
十年——但那是重建的，不是觀測到的。兩者的可信度差很多。
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from liquidity_monitor.config import POSITION_LADDER

from . import data as tdata
from .controller import GateState, TailFragilityGate
from .credit_proxy import CreditProxyGate, CreditProxyInputs
from .crowding import CrowdingInputs
from .daily_log import build_row
from .fast import FastGateInputs

log = logging.getLogger(__name__)


def base_alloc_from_score(score: float) -> tuple:
    """用模組一的部位階梯把綜合分數轉成權重。

    **這是重建，不是模組一當天的實際輸出。** 重建的只有階梯結果，
    沒有套用 Gate A/B/C 的上限——因為 `scores_history_reconstructed.csv`
    只存了 composite_score。實際部位只會比這個更保守，所以回補出來的
    base_alloc **偏積極**，模組六可以砍的空間也偏大。
    這個偏差要記在報告裡，不能讓它看起來像實際歷史。
    """
    from .report import parse_base_alloc

    for low, high, text, _ in POSITION_LADDER:
        if low <= score < high:
            return parse_base_alloc(text)
    return parse_base_alloc(POSITION_LADDER[-1][2])


def _slice(series: Optional[pd.Series], upto) -> Optional[pd.Series]:
    """切到當天為止。回補唯一的規則。"""
    if series is None:
        return None
    return series.loc[:upto]


def replay(
    vol: dict,
    credit: dict,
    validation: dict,
    scores: pd.Series,
    hy_oas: Optional[pd.Series] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    persist_days: int = None,
    reentry_clean_days: int = None,
) -> tuple[list, dict]:
    """逐日重播，回傳 (日誌列, 摘要)。

    `scores` 是模組一的 composite_score（以日期為索引）。
    """
    from .config import PERSIST_DAYS, REENTRY_CLEAN_DAYS

    gate = TailFragilityGate(
        slow_gate=CreditProxyGate(),
        persist_days=PERSIST_DAYS if persist_days is None else persist_days,
        reentry_clean_days=(REENTRY_CLEAN_DAYS if reentry_clean_days is None
                            else reentry_clean_days),
    )

    # 只在「所有必要序列都有值」的日子重播。缺 HYG/IEF 就算不出慢閘，
    # 硬跑會得到一個看似正常、其實是用更早資料算出來的分數
    days = scores.dropna().index
    for key in ("hyg", "ief"):
        days = days.intersection(credit[key].dropna().index)
    if start:
        days = days[days >= pd.Timestamp(start)]
    if end:
        days = days[days <= pd.Timestamp(end)]

    state = GateState()
    rows, skipped = [], 0

    for day in days:
        c_hyg, c_ief = _slice(credit["hyg"], day), _slice(credit["ief"], day)
        # 慢閘要算 500 日回撤與 250 日百分位；樣本太短時算出來的不是壓力，
        # 是「歷史還不夠長」。這種日子跳過，而不是給一個看起來像數字的東西
        if len(c_hyg) < 260 or len(c_ief) < 260:
            skipped += 1
            continue

        vol_last, fast_kwargs = {}, {}
        for key in ("vix9d", "vix", "vix3m"):
            s = _slice(vol.get(key), day)
            vol_last[key] = None if s is None or s.empty else round(float(s.iloc[-1]), 4)
        vvix = _slice(vol.get("vvix"), day)
        fast_kwargs["vvix_pctile"] = (None if vvix is None or vvix.empty
                                      else tdata.percentile_of_latest(vvix))
        oas_slice = _slice(hy_oas, day)
        hy_chg20 = (None if oas_slice is None or oas_slice.empty
                    else tdata.change_bp(oas_slice, 20))

        qqq = _slice(validation.get("qqq"), day)
        crowd = CrowdingInputs(
            realized_vol_20d=None if qqq is None else tdata.realized_vol(qqq),
            spx_trailing_250d_ret=None if qqq is None else tdata.trailing_return(qqq),
        )

        result = gate.apply(
            base_alloc=base_alloc_from_score(float(scores.loc[day])),
            slow=CreditProxyInputs(hyg=c_hyg, ief=c_ief, lqd=_slice(credit.get("lqd"), day)),
            fast=FastGateInputs(vix9d=vol_last.get("vix9d"), vix=vol_last.get("vix"),
                                vix3m=vol_last.get("vix3m"), **fast_kwargs),
            crowd=crowd,
            state=state,
        )
        state = result.state

        prices = {}
        for key in ("tqqq", "qqq"):
            s = _slice(validation.get(key), day)
            prices[key] = None if s is None or s.empty else round(float(s.iloc[-1]), 4)

        row = build_row(str(day.date()), result, vol_last, hy_chg20, prices)
        row["source"] = "backfill"
        rows.append(row)

    summary = {
        "days_replayed": len(rows),
        "days_skipped_short_history": skipped,
        "first": rows[0]["as_of"] if rows else None,
        "last": rows[-1]["as_of"] if rows else None,
        "final_state": state.to_dict(),
    }
    return rows, summary


def measure_assumptions(rows: list) -> dict:
    """從回補結果算出 EV 模型那兩項最要緊的假設。

    這是整個回補的目的：把「未實測」換成數字。算不出來時要說算不出來，
    不能退回估計值——那樣就回到原點了。
    """
    from .daily_log import measure_whipsaw

    if not rows:
        return {"error": "沒有可用的回補列"}

    df = pd.DataFrame(rows)
    years = max(1e-9, (pd.Timestamp(rows[-1]["as_of"]) - pd.Timestamp(rows[0]["as_of"])).days / 365.25)

    veto = df["state_active_veto"].astype(float) > 0
    starts = int(((veto) & (~veto.shift(1, fill_value=False))).sum())

    whipsaw = measure_whipsaw(df)
    out = {
        "years_covered": round(years, 2),
        "veto_episodes": starts,
        "false_positive_per_year_measured": round(starts / years, 3),
        "days_under_veto": int(veto.sum()),
        "share_of_days_under_veto": round(float(veto.mean()), 4),
        "whipsaw": whipsaw,
    }
    if whipsaw.get("episodes"):
        # 避開的損失為正代表閘門幫上忙；負代表它砍在低點、回場在高點，
        # 那才是 whipsaw_cost 的實際樣子
        out["whipsaw_cost_measured"] = round(-whipsaw["mean_avoided"], 5)
    return out
