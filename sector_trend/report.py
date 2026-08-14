"""模組七的每日報告。"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import (
    BENCHMARK,
    CHART_POINTS,
    DATA_ROOT,
    HISTORY_DAYS,
    HISTORY_PATH,
    PRIMARY,
    RETURN_WINDOWS,
    SECTOR_FAMILIES,
    THEMATIC,
    WINDOW_LABELS,
    all_tickers,
)
from . import metrics

log = logging.getLogger(__name__)


def build_report(as_of: Optional[str] = None) -> dict:
    as_of = as_of or date.today().isoformat()
    start = (date.fromisoformat(as_of) - timedelta(days=HISTORY_DAYS)).isoformat()
    end = (date.fromisoformat(as_of) + timedelta(days=1)).isoformat()

    wide, missing = metrics.fetch_adjusted(all_tickers(), start, end)
    if BENCHMARK not in wide.columns:
        raise ValueError(f"缺少基準 {BENCHMARK}，無法計算超額報酬")

    bench = wide[BENCHMARK]
    # 資料基準日取自實際收盤日，不是執行日。同一天重跑或週末執行時，
    # 用執行日會讓歷史多出一筆其實是同一個收盤的紀錄（模組四踩過這個坑）
    close_date = str(wide.index.max().date())

    rows_by_ticker: dict = {}
    for t in wide.columns:
        if t == BENCHMARK:
            continue
        rows_by_ticker[t] = metrics.build_row(t, wide[t], bench)

    sectors = []
    for sector, etfs in SECTOR_FAMILIES.items():
        family = []
        for ticker, issuer, desc in etfs:
            row = rows_by_ticker.get(ticker)
            if row is None:
                continue
            family.append({**row, "issuer": issuer, "tracks": desc,
                           "is_primary": ticker == PRIMARY[sector]})
        if not family:
            continue
        sectors.append({
            "sector": sector,
            "primary": PRIMARY[sector],
            "etfs": family,
            "dispersion_1y": metrics.family_dispersion(family, "1y"),
            "dispersion_3y": metrics.family_dispersion(family, "3y"),
        })

    thematic = []
    for ticker, name, parent in THEMATIC:
        row = rows_by_ticker.get(ticker)
        if row:
            thematic.append({**row, "name": name, "parent_sector": parent})

    all_rows = list(rows_by_ticker.values())
    bd = metrics.breadth(all_rows, list(PRIMARY.values()))
    _append_breadth_history(close_date, bd)

    charts = {PRIMARY[s]: metrics.rebased_series(wide[PRIMARY[s]], CHART_POINTS)
              for s in SECTOR_FAMILIES if PRIMARY[s] in wide.columns}
    charts[BENCHMARK] = metrics.rebased_series(bench, CHART_POINTS)

    return {
        "as_of": as_of,
        "close_date": close_date,
        "benchmark": {"ticker": BENCHMARK,
                      "close": round(float(bench.iloc[-1]), 4),
                      "returns": {k: (None if (v := metrics._pct_change_over(bench, d)) is None
                                      else round(v, 2))
                                  for k, d in RETURN_WINDOWS.items()}},
        "window_labels": WINDOW_LABELS,
        "sectors": sectors,
        "thematic": thematic,
        "breadth": bd,
        "breadth_history": _load_breadth_history(),
        "charts": charts,
        "missing": missing,
        "notes": _notes(),
    }


def _notes() -> list:
    return [
        "價格為**除息調整後**。公用事業與必需消費殖利率約 3%、資訊科技不到 1%，"
        "用未調整價比較一年以上的報酬會系統性低估防禦類股。",
        "模組四用未調整價（三個月內影響有限），因此兩頁的短期數字會有些微差異——"
        "這是預期中的，不是其中一邊算錯。",
        "本頁沒有自創的綜合分數。呈現的是均線位置、距 52 週高低點、各視窗報酬——"
        "全部可以自己驗算。50/200 日均線是市場慣用的長度，不是本專案挑的。",
        "同一類股列多檔 ETF，是因為它們追不同指數：「科技類股今年漲多少」"
        "取決於你拿哪一檔。離散度大的時候，用單一 ETF 代表整個類股會失真。",
    ]


def _append_breadth_history(close_date: str, bd: dict, path: str = HISTORY_PATH) -> None:
    if bd.get("above_ma200") is None:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = pd.DataFrame([{"close_date": close_date,
                         "above_ma200": bd["above_ma200"],
                         "above_ma50": bd.get("above_ma50"),
                         "total": bd["total"]}])
    if p.exists():
        old = pd.read_csv(p)
        # 以收盤日為鍵：同一個收盤日重跑要覆蓋，不是追加
        old = old[old["close_date"].astype(str) != str(close_date)]
        row = pd.concat([old, row], ignore_index=True)
    row.sort_values("close_date").to_csv(p, index=False)


def _load_breadth_history(path: str = HISTORY_PATH, keep: int = 250) -> dict:
    p = Path(path)
    if not p.exists():
        return {"dates": [], "above_ma200": [], "total": []}
    df = pd.read_csv(p).sort_values("close_date").tail(keep)
    return {"dates": [str(x) for x in df["close_date"]],
            "above_ma200": [int(x) for x in df["above_ma200"]],
            "total": [int(x) for x in df["total"]]}


def write_report(report: dict, data_root: str = DATA_ROOT) -> Path:
    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)
    out = root / "latest.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
