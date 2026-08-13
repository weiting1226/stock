"""模組六的每日報告：抓資料、跑閘門、存狀態與日誌、產出儀表板 JSON。

**這個模組只做影子運行。** 接線說明第 6 節第三步建議先只記錄不執行，
累積三個月後再比對；儀表板天生就是那個形態。畫面上會標明這件事，
因為一個紅色的「STRESS」看起來跟已經驗證過的訊號一模一樣。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from liquidity_monitor.sources.fred import fetch_fred_series

from . import data as tdata
from .config import (
    CREDIT_PROXY_TICKERS,
    DATA_ROOT,
    FUNDING_SERIES,
    HISTORY_DAYS,
    HY_OAS_SERIES,
    INPUT_LAG_DAYS,
    PERSIST_DAYS,
    REENTRY_CLEAN_DAYS,
    TQQQ_HARD_CAP,
    VALIDATION_TICKERS,
    VOL_TICKERS,
)
from .controller import GateState, TailFragilityGate, assert_state_consistent
from .credit_proxy import CreditProxyGate, CreditProxyInputs, calibrate_against_oas
from .crowding import CrowdingInputs
from .daily_log import append_row, build_row, load_log, measure_whipsaw
from .ev import expected_value, sensitivity_to_whipsaw
from .fast import FastGateInputs
from .state import load_state, save_state, staleness_days

log = logging.getLogger(__name__)

_LADDER_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(TQQQ|QQQ|SGOV)", re.IGNORECASE)


def parse_base_alloc(position_text: str) -> tuple:
    """把模組一的部位字串轉成 (w_tqqq, w_qqq, w_sgov)。

    模組一輸出的是「50% TQQQ + 50% QQQ」這種人話。解析而不是重算，
    是因為 base_alloc 必須**就是**主評分的結論——重算一份等於讓兩邊
    有機會不一致，而不一致時沒有人會發現是哪一邊錯了。
    """
    if not position_text:
        raise ValueError("模組一沒有提供部位字串，無從取得 base_alloc")
    found = {k.upper(): float(v) / 100.0 for v, k in _LADDER_RE.findall(position_text)}
    if not found:
        raise ValueError(f"無法解析模組一的部位字串：{position_text!r}")
    alloc = (found.get("TQQQ", 0.0), found.get("QQQ", 0.0), found.get("SGOV", 0.0))
    total = sum(alloc)
    if abs(total - 1.0) > 0.01:
        raise ValueError(f"部位字串加總不為 100%：{position_text!r} → {total:.2%}")
    return alloc


def load_module_one_position(path: str = "docs/data/latest.json") -> tuple:
    p = Path(path)
    if not p.exists():
        raise ValueError(f"找不到模組一的輸出 {path}——模組六的 base_alloc 只能來自主評分")
    d = json.loads(p.read_text(encoding="utf-8"))
    pos = (d.get("position") or {})
    text = pos.get("final") or pos.get("ladder_result")
    return parse_base_alloc(text), {"as_of": d.get("as_of"), "text": text}


def build_report(as_of: Optional[str] = None, data_root: str = DATA_ROOT) -> dict:
    as_of = as_of or date.today().isoformat()
    start = (date.fromisoformat(as_of) - timedelta(days=HISTORY_DAYS)).isoformat()
    end = (date.fromisoformat(as_of) + timedelta(days=1)).isoformat()
    problems: list = []

    base_alloc, base_meta = load_module_one_position()

    vol_series, p1 = tdata.fetch_many_adjusted(VOL_TICKERS, start, end)
    credit_series, p2 = tdata.fetch_many_adjusted(CREDIT_PROXY_TICKERS, start, end)
    val_series, p3 = tdata.fetch_many_adjusted(VALIDATION_TICKERS, start, end)
    problems += p1 + p2 + p3

    if "hyg" not in credit_series or "ief" not in credit_series:
        raise ValueError("慢閘需要 HYG 與 IEF 的調整價，兩者缺一不可")

    hy_oas = None
    try:
        hy_oas = fetch_fred_series(HY_OAS_SERIES, start, end)
    except Exception as e:  # noqa: BLE001
        problems.append(f"HY OAS（{HY_OAS_SERIES}）：{type(e).__name__}: {e}")

    funding = {}
    for key, sid in FUNDING_SERIES.items():
        try:
            funding[key] = fetch_fred_series(sid, start, end)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{key}（{sid}）：{type(e).__name__}: {e}")

    # --- 跨日狀態：規則 2 ---------------------------------------------------
    state, state_meta = load_state()
    stale = staleness_days(state_meta, as_of)

    vol_last = {k: (round(float(s.iloc[-1]), 4) if not s.empty else None)
                for k, s in vol_series.items()}
    vvix_pctile = (tdata.percentile_of_latest(vol_series["vvix"])
                   if "vvix" in vol_series else None)
    hy_chg20 = tdata.change_bp(hy_oas, 20) if hy_oas is not None else None

    crowd = CrowdingInputs(
        realized_vol_20d=(tdata.realized_vol(val_series["qqq"])
                          if "qqq" in val_series else None),
        spx_trailing_250d_ret=(tdata.trailing_return(val_series["qqq"])
                               if "qqq" in val_series else None),
        # CFTC 與 margin debt 留空：兩者延遲分別是 T+3 與 T+30，
        # 接上去之前要先想清楚怎麼在畫面標示「這是三天前的值」
        cftc_jpy_net_short=None,
        margin_debt_yoy=None,
    )

    gate = TailFragilityGate(slow_gate=CreditProxyGate(),
                             persist_days=PERSIST_DAYS,
                             reentry_clean_days=REENTRY_CLEAN_DAYS)
    result = gate.apply(
        base_alloc=base_alloc,
        slow=CreditProxyInputs(hyg=credit_series["hyg"], ief=credit_series["ief"],
                               lqd=credit_series.get("lqd")),
        fast=FastGateInputs(vix9d=vol_last.get("vix9d"), vix=vol_last.get("vix"),
                            vix3m=vol_last.get("vix3m"), vvix=vol_last.get("vvix"),
                            vvix_pctile=vvix_pctile, hy_chg20_bp=hy_chg20),
        crowd=crowd,
        state=state,
    )

    prices = {k: (round(float(s.iloc[-1]), 4) if not s.empty else None)
              for k, s in val_series.items()}
    row = build_row(as_of, result, vol_last, hy_chg20, prices)

    append_row(row)
    save_state(result.state, as_of)
    # 說明第 7 節建議的斷言。放在寫入之後，因為它要驗證的正是「寫入這件事」
    assert_state_consistent(row)

    calibration = None
    if hy_oas is not None and not hy_oas.dropna().empty:
        try:
            calibration = calibrate_against_oas(
                CreditProxyInputs(hyg=credit_series["hyg"], ief=credit_series["ief"],
                                  lqd=credit_series.get("lqd")),
                hy_oas=hy_oas)
        except Exception as e:  # noqa: BLE001
            problems.append(f"代理驗證失敗：{type(e).__name__}: {e}")

    history = load_log()
    report = {
        "as_of": as_of,
        "mode": "shadow",
        "shadow_note": ("影子運行：只記錄、不下單。慢閘門檻未經真實資料校準，"
                        "快閘的危機型事件樣本 n=1。"),
        "base_alloc": {"tqqq": base_alloc[0], "qqq": base_alloc[1], "sgov": base_alloc[2],
                       "source": base_meta},
        "out_alloc": {"tqqq": result.out_alloc[0], "qqq": result.out_alloc[1],
                      "sgov": result.out_alloc[2]},
        "changed": result.out_alloc != result.base_alloc,
        "equity_ceiling": result.equity_ceiling,
        "tqqq_hard_cap": TQQQ_HARD_CAP,
        "slow": {"gate": "CreditProxyGate", "regime": result.slow.regime,
                 "score": round(result.slow.score, 2),
                 "equity_ceiling": result.slow.equity_ceiling,
                 "detail": result.slow.detail},
        "fast": {"raw_veto": result.fast.raw_veto,
                 "gated_veto": result.detail["gated_veto"],
                 "n_categories": result.fast.n_categories,
                 "curve_inverted": result.fast.curve_inverted,
                 "vix9d": vol_last.get("vix9d"), "vix": vol_last.get("vix"),
                 "vix3m": vol_last.get("vix3m"),
                 "detail": result.fast.detail},
        "crowding": {"score": result.crowd.score,
                     "threshold_shift": result.crowd.threshold_shift,
                     "detail": result.crowd.detail},
        "state": result.state.to_dict(),
        "state_meta": {**state_meta, "staleness_days": stale},
        "controller": result.detail,
        "ev": expected_value(),
        "ev_sensitivity": sensitivity_to_whipsaw(),
        "whipsaw": measure_whipsaw(history),
        "calibration": calibration,
        "input_lags": INPUT_LAG_DAYS,
        "problems": problems,
        "history": _history_for_chart(history),
        "limitations": _limitations(),
    }
    return report


def _history_for_chart(df: pd.DataFrame, keep: int = 180) -> dict:
    if df.empty:
        return {"dates": [], "series": {}}
    d = df.sort_values("as_of").tail(keep)
    cols = ["cp_score", "state_active_veto", "fg_n_categories",
            "out_w_tqqq", "base_w_tqqq", "cr_score"]
    return {"dates": [str(x) for x in d["as_of"]],
            "series": {c: [None if pd.isna(v) else float(v) for v in d[c]]
                       for c in cols if c in d.columns}}


def _limitations() -> list:
    """說明第 8 節的已知限制表，原樣呈現在畫面上。

    這張表不放在頁尾小字，是因為它決定了整個模組該被怎麼讀。
    """
    return [
        {"limit": "Type IV（SVB／LDI 型）零領先期", "impact": "完全防不到",
         "solvable": "否，只能靠事前部位上限"},
        {"limit": "Type II（踩踏型）干預 EV ≈ 0", "impact": "最頻繁的事件類型無法獲利",
         "solvable": "已由 persist_days 主動放棄"},
        {"limit": "慢閘門檻未校準", "impact": "可能系統性過鬆或過緊",
         "solvable": "跑代理驗證（本頁下方）"},
        {"limit": "whipsaw_cost 未實測", "impact": "EV 估計的最大不確定來源",
         "solvable": "日誌已記錄 tqqq/qqq 收盤價，觸發後可算"},
        {"limit": "危機型事件樣本 n=1", "impact": "尾部效益估計不穩健",
         "solvable": "無法解決，時間問題"},
    ]


def write_report(report: dict, data_root: str = DATA_ROOT) -> Path:
    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)
    out = root / "latest.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
