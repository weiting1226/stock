"""組裝模組五的報告：抓 FRED -> 轉換 -> 分類 -> 產出儀表板 JSON。"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from liquidity_monitor.sources import fred

from . import indicators
from .config import (
    CATEGORIES,
    HISTORY_YEARS,
    PERCENTILE_YEARS,
    RECESSION_SERIES,
    SERIES,
)

log = logging.getLogger(__name__)


def fetch_all(as_of: str, years: int = HISTORY_YEARS) -> tuple[dict, dict]:
    """抓所有序列。回傳 (資料, 抓取診斷)。

    單一序列失敗不中斷整批——少一條指標仍然是有用的儀表板，
    但失敗原因要留下來，否則畫面上「這個月還沒公布」與「來源壞了」
    長得一模一樣。
    """
    start = str((pd.Timestamp(as_of) - pd.DateOffset(years=years)).date())
    data, diag = {}, {}
    for spec in (*SERIES,):
        d = fred.FetchDiagnostics(spec.fred_id, start)
        try:
            data[spec.fred_id] = fred.fetch_fred_series(spec.fred_id, start, as_of, diagnostics=d)
        except Exception as e:  # noqa: BLE001
            log.warning("FRED %s 抓取失敗：%s", spec.fred_id, e)
            data[spec.fred_id] = pd.Series(dtype=float)
            diag[spec.fred_id] = {"error": f"{type(e).__name__}: {e}",
                                  "attempts": d.attempts}
            continue
        # 只留有疑點的診斷（試過一個以上端點），其餘附上只是噪音
        if len(d.attempts) > 1:
            diag[spec.fred_id] = {"chosen": d.chosen, "attempts": d.attempts}

    try:
        data[RECESSION_SERIES] = fred.fetch_fred_series(RECESSION_SERIES, start, as_of)
    except Exception as e:  # noqa: BLE001
        log.warning("衰退指標抓取失敗：%s", e)
        data[RECESSION_SERIES] = pd.Series(dtype=float)
    return data, diag


def build_report(as_of: str = None, data: dict = None, diagnostics: dict = None) -> dict:
    as_of = as_of or date.today().isoformat()
    if data is None:
        data, diagnostics = fetch_all(as_of)
    diagnostics = diagnostics or {}

    rows, charts = [], {}
    for spec in SERIES:
        raw = data.get(spec.fred_id)
        item = indicators.build_indicator(raw, spec, as_of)
        if item.get("available"):
            values = indicators.transform(raw, spec)
            charts[spec.fred_id] = indicators.sparkline(values, freq=spec.freq)
        rows.append(item)

    available = [r for r in rows if r.get("available")]
    stale = [r for r in available if r.get("stale")]
    missing = [r for r in rows if not r.get("available")]

    by_category = {}
    for key, label in CATEGORIES.items():
        items = [r for r in rows if r["category"] == key]
        improving = sum(1 for r in items if r.get("direction_3m") == "改善")
        worsening = sum(1 for r in items if r.get("direction_3m") == "惡化")
        by_category[key] = {
            "label": label,
            "count": len(items),
            "improving": improving,
            "worsening": worsening,
        }

    return {
        "as_of": as_of,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "categories": CATEGORIES,
        "summary": by_category,
        "rows": rows,
        "charts": charts,
        "recession_bands": indicators.recession_bands(data.get(RECESSION_SERIES)),
        "percentile_years": PERCENTILE_YEARS,
        "counts": {"total": len(rows), "available": len(available),
                   "stale": len(stale), "missing": len(missing)},
        "stale_series": [{"fred_id": r["fred_id"], "label": r["label"],
                          "reference_date": r["reference_date"], "lag_days": r["lag_days"]}
                         for r in stale],
        "missing_series": [{"fred_id": r["fred_id"], "label": r["label"],
                            "error": r.get("error")} for r in missing],
        "fetch_diagnostics": diagnostics,
        "notes": [
            "每一條指標顯示的是**參考期**的數字，不是抓取日期——CPI 的 7 月數字"
            "要到 8 月中才公布，畫面上的「資料落後天數」就是這段差距。",
            "「改善／惡化」的方向由設定檔明確指定（失業率上升是惡化、GDP 上升是改善）；"
            "通膨與殖利率這類沒有單一方向好壞的指標一律顯示「—」，不硬套。",
            f"百分位以最近 {PERCENTILE_YEARS} 年為比較期間，樣本不足時留空而不是硬給數字。",
            "衰退陰影取自 NBER 認定（FRED USREC）。NBER 往往在衰退開始一年後才公布認定，"
            "因此最近期不會有標記——那是「還沒認定」，不是「沒有衰退」。",
            "總體數據會被事後修正，本頁顯示的一律是目前最新的修正版本。",
        ],
    }


def write_report(report: dict, data_root: str) -> Path:
    import json
    out = Path(data_root) / "latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
