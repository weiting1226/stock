"""組裝模組四的報告：抓價 -> 算指標 -> 算資金流 -> 產出儀表板 JSON。"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from liquidity_monitor.sources import yahoo
from liquidity_monitor.sources.etf_creation_flows import fetch_snapshots

from . import flows as flow_mod
from . import history as hist
from . import metrics, storage
from .config import (
    ALL_TICKERS,
    BENCHMARK,
    HISTORY_DAYS,
    SECTOR_ETFS,
    WINDOW_LABELS,
    WINDOWS,
)

log = logging.getLogger(__name__)


def fetch_prices(as_of: str, days: int = HISTORY_DAYS) -> pd.DataFrame:
    start = str((pd.Timestamp(as_of) - pd.Timedelta(days=days)).date())
    end = str((pd.Timestamp(as_of) + pd.Timedelta(days=1)).date())
    df = yahoo.fetch_yahoo_close_bulk(ALL_TICKERS, start, end)
    return df.loc[df.index <= pd.Timestamp(as_of)]


def build_report(as_of: str = None, prices: pd.DataFrame = None,
                 snapshots: list = None, previous: dict = None) -> dict:
    as_of = as_of or date.today().isoformat()
    prices = fetch_prices(as_of) if prices is None else prices
    if prices.empty:
        raise ValueError("類股 ETF 價格抓取結果為空")

    rows, bench_returns = metrics.build_sector_rows(prices, BENCHMARK, SECTOR_ETFS)

    # 快照以**市場收盤日**為鍵，不是執行日期。
    # 用執行日期的話，同一個交易日跑第二次會被當成新的一天，
    # 於是「今天」與「昨天」其實是同一筆收盤資料——算出來每一檔都是 0，
    # 而畫面會把它讀成「資金持平」。實測就是這樣發生的。
    close_date = str(prices.index[-1].date())

    # --- 資金流（申贖流量）-------------------------------------------------
    flow_note, flow_data = None, {}
    try:
        snaps = (fetch_snapshots(tuple(SECTOR_ETFS), as_of=close_date)
                 if snapshots is None else snapshots)
        prev = (storage.load_previous_snapshots(before=close_date)
                if previous is None else previous)
        raw_flows, skipped = flow_mod.compute_sector_flows(snaps, prev)
        stale = flow_mod.stale_reason(raw_flows)
        if stale:
            flow_note = f"資金流未採計：{stale}"
        elif raw_flows:
            flow_data = flow_mod.strip_internals(raw_flows)
            if skipped:
                flow_note = f"{len(skipped)} 檔未納入：{skipped[:3]}"
        else:
            flow_note = (f"尚無可比較的前一次觀測（本日已取得 {len(snaps)} 檔快照並存檔）；"
                         "資金流需要相隔一天的兩次觀測，明日起即可計算")
        storage.save_snapshots(snaps)
    except Exception as e:  # noqa: BLE001 — 資金流算不出來不該讓整份報告失敗
        log.warning("類股資金流計算失敗: %s", e)
        flow_note = f"資金流計算失敗：{type(e).__name__}: {e}"

    for r in rows:
        f = flow_data.get(r["ticker"])
        r["flow"] = f
        r["flow_pct_of_aum"] = f["flow_pct_of_aum"] if f else None

    # 資金流的名次另外排：報酬排名看的是價格，資金排名看的是錢的流向，
    # 兩者不一致本身就是訊息（例如漲最多但資金在流出）
    flow_ranks = metrics.rank_series({r["ticker"]: r["flow_pct_of_aum"] for r in rows})
    for r in rows:
        r["flow_rank"] = flow_ranks.get(r["ticker"])

    prev_ranks = storage.load_previous_ranks(before=as_of)
    changes = metrics.rotation_changes(rows, prev_ranks, key="1w")
    storage.append_ranks(as_of, {r["ticker"]: r.get("rank", {}).get("1w") for r in rows})

    # 回溯重建的價格面歷史。只需要價格，所以有多久的價格就有多久的歷史；
    # 資金流回溯不了（需要當時的逐日股數快照，沒有任何來源提供），
    # 因此歷史只含價格面，兩者不混在一起。
    try:
        series = hist.sector_history(prices, BENCHMARK)
        history_block = {
            "trails": hist.build_trails(series),
            "quadrant_timeline": hist.quadrant_timeline(series),
            "rank_history": hist.rank_history(series),
            "days": max((len(df) for df in series.values()), default=0),
            "note": ("由價格回溯重建：報酬、相對強弱、動能、象限、名次都只需要價格。"
                     "**資金流不在其中**——它需要當時的逐日流通股數與淨資產快照，"
                     "沒有任何來源提供回溯查詢，因此資金流仍從部署當日起累積。"),
        }
    except Exception as e:  # noqa: BLE001
        log.warning("輪動歷史重建失敗: %s", e)
        history_block = {"error": f"{type(e).__name__}: {e}"}

    quadrants = {}
    for r in rows:
        quadrants.setdefault(r["quadrant"], []).append(r["sector_zh"])

    return {
        "as_of": as_of,
        "close_date": close_date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "benchmark": {"ticker": BENCHMARK, "returns": bench_returns},
        "window_labels": WINDOW_LABELS,
        "rows": rows,
        "breadth": {k: metrics.breadth(rows, k) for k in WINDOWS},
        "quadrants": quadrants,
        "rank_changes": changes,
        "history": history_block,
        "flow_note": flow_note,
        "flow_available": bool(flow_data),
        "notes": [
            "視窗一律以交易日計（近一週＝5 個交易日、近一月＝21 個），"
            "不用日曆天——連假前後的日曆週會是不同長度的區間，跨日期無法比較。",
            "資金流由 ETF 流通股數變化計算（Δ股數 × 價格＝申贖金額本身），"
            "股數不可信時退回淨資產分解（估計），每一筆都標明用的是哪一種。",
            "資金流以「佔自身淨資產的比例」呈現：XLK 的規模是 XLRE 的十幾倍，"
            "比絕對金額等於在比誰規模大，不是在比誰的資金動能強。",
        ],
    }


def write_report(report: dict, data_root: str) -> Path:
    import json
    out = Path(data_root) / "latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
