#!/usr/bin/env python3
"""模組六：回補歷史，把 EV 模型的假設換成量測值。

    python3 scripts/backfill_tail_gate.py --start 2016-08-12 -v

**目的**：模組六的 EV 模型五項假設全部未實測，其中 `whipsaw_cost` 只要比假設高
約 27% 就讓整個模組期望值歸零。回補十年可以把 `false_positive_per_year` 與
`whipsaw_cost` 兩項變成量測值——那正是說明第 5 節存收盤價的用意，也是第 6 節
第二步（快閘的真實回測）。

**唯一真正的風險是前視偏誤，而且它不會報錯。** 回補的每一天都先把序列切到當天
為止再交給閘門；閘門本身不知道自己在回測。細節見 `tail_gate/backfill.py`。

回補的列在日誌裡標為 `source=backfill`，與 `live` 分開統計——重建的不是觀測到的。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from liquidity_monitor.sources.fred import fetch_fred_series  # noqa: E402
from tail_gate import backfill as bf  # noqa: E402
from tail_gate import data as tdata  # noqa: E402
from tail_gate.config import (  # noqa: E402
    CREDIT_PROXY_TICKERS,
    DAILY_LOG_PATH,
    DATA_ROOT,
    HY_OAS_SERIES,
    VALIDATION_TICKERS,
    VOL_TICKERS,
)
from tail_gate.daily_log import COLUMNS  # noqa: E402

SCORES_PATH = "docs/data/scores_history_reconstructed.csv"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default=None, help="回補起日（預設用分數歷史的起點）")
    ap.add_argument("--end", default=None)
    ap.add_argument("--scores", default=SCORES_PATH)
    ap.add_argument("--log-path", default=DAILY_LOG_PATH)
    ap.add_argument("--data-root", default=DATA_ROOT)
    ap.add_argument("--dry-run", action="store_true", help="只算不寫檔")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    scores_df = pd.read_csv(args.scores)
    scores = pd.Series(scores_df["composite_score"].values,
                       index=pd.to_datetime(scores_df["as_of"])).sort_index()
    start = args.start or str(scores.index.min().date())
    # 慢閘要 500 日回撤與 250 日百分位，價格得比回補起點更早開始
    fetch_start = (date.fromisoformat(start) - timedelta(days=900)).isoformat()
    end = args.end or str(scores.index.max().date())
    fetch_end = (date.fromisoformat(end) + timedelta(days=1)).isoformat()

    print(f"回補區間 {start} → {end}（價格自 {fetch_start} 起抓，供滾動視窗暖機）")

    vol, p1 = tdata.fetch_many_adjusted(VOL_TICKERS, fetch_start, fetch_end)
    credit, p2 = tdata.fetch_many_adjusted(CREDIT_PROXY_TICKERS, fetch_start, fetch_end)
    val, p3 = tdata.fetch_many_adjusted(VALIDATION_TICKERS, fetch_start, fetch_end)
    problems = p1 + p2 + p3

    hy_oas = None
    try:
        hy_oas = fetch_fred_series(HY_OAS_SERIES, fetch_start, fetch_end)
    except Exception as e:  # noqa: BLE001
        problems.append(f"HY OAS：{type(e).__name__}: {e}")

    for key in ("hyg", "ief"):
        if key not in credit:
            print(f"缺少 {key}，慢閘算不出來，中止", file=sys.stderr)
            return 1

    rows, summary = bf.replay(vol, credit, val, scores, hy_oas=hy_oas,
                              start=start, end=end)
    if not rows:
        print("沒有可回補的日子", file=sys.stderr)
        return 1

    measured = bf.measure_assumptions(rows)

    # 各輸入實際涵蓋到哪裡——缺哪一段，對應的類別在那段期間就是沒作用的
    coverage = {}
    for name, s in list(vol.items()) + list(credit.items()) + list(val.items()):
        coverage[name] = {"from": str(s.index.min().date()), "n": int(len(s))}
    if hy_oas is not None and not hy_oas.empty:
        coverage["hy_oas"] = {"from": str(hy_oas.index.min().date()), "n": int(len(hy_oas))}

    report = {
        "generated_for": {"start": start, "end": end},
        "summary": summary,
        "measured": measured,
        "input_coverage": coverage,
        "problems": problems,
        "caveats": [
            "base_alloc 由 composite_score 經部位階梯重建，**未套用 Gate A/B/C 上限**"
            "（分數歷史只存了 composite_score）。實際部位只會更保守，"
            "因此回補的 base_alloc 偏積極，模組六可砍的空間偏大。",
            "HY OAS 受 ICE 授權限制只有近三年，更早期間「信用急變」這一類別"
            "沒有輸入，快閘在那段期間最多只有兩類可觸發。",
            "回補列標為 source=backfill：這是重建，不是觀測到的影子運行。",
        ],
    }

    print(f"\n重播 {summary['days_replayed']} 日"
          f"（跳過 {summary['days_skipped_short_history']} 日：歷史長度不足）")
    print(f"  否決事件 {measured['veto_episodes']} 次／{measured['years_covered']} 年"
          f" = {measured['false_positive_per_year_measured']} 次/年")
    print(f"  處於否決狀態的日數佔比 {measured['share_of_days_under_veto']:.1%}")
    w = measured.get("whipsaw", {})
    if measured.get("whipsaw_cost_measured") is not None:
        print(f"  實測 whipsaw_cost = {measured['whipsaw_cost_measured']:+.4f}"
              f"（{w.get('episodes')} 次完整進出）")
    else:
        print(f"  whipsaw：{w.get('note')}")
    for p in problems:
        print(f"  ! {p}")

    if args.dry_run:
        print("\n--dry-run：不寫檔")
        return 0

    # 保留既有的 live 列：回補不該蓋掉真的跑過的那幾天
    path = Path(args.log_path)
    df_new = pd.DataFrame(rows).reindex(columns=COLUMNS)
    if path.exists():
        old = pd.read_csv(path)
        if "source" not in old.columns:
            old["source"] = "live"
        keep = old[old["source"] != "backfill"]
        df_new = df_new[~df_new["as_of"].astype(str).isin(keep["as_of"].astype(str))]
        df_new = pd.concat([keep, df_new], ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    df_new.reindex(columns=COLUMNS).sort_values("as_of").to_csv(path, index=False)

    out_dir = Path(args.data_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "backfill.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n日誌：{path}（{len(df_new)} 列）\n報告：{out_dir / 'backfill.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
