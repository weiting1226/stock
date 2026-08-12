#!/usr/bin/env python3
"""回溯重建模組一的綜合分數歷史。

    python3 scripts/backfill_score_history.py -v --years 10

**重建出來的不是模組一線上的那個分數。** 這一點決定了整支腳本的設計：

  線上：6 個類別、18 項指標
  重建：5 個類別、13 項指標（缺 ⑥ 政策方向全部三項、NDX 廣度、ETF 資金流）

缺少的類別會讓其餘類別的權重按比例放大，因此同一天的重建值與線上值不會
相同。既然不相同，就**不能寫進 scores_history.csv**——那個檔案是線上每日
執行的實際紀錄，混進來源不同的數字之後，將來沒有人分得出哪些是真的跑出來的。

因此寫到獨立檔案 `docs/data/scores_history_reconstructed.csv`，
畫面上以不同的線型呈現，並標明它是什麼。

回溯不了的三項各有原因：
  fomc_decision      需要對每一次歷史會議聲明做 AI 判讀（做得到但成本高）
  ndx_breadth_200d   需要當時的成分股名單，用今日名單回推有存活者偏誤
  etf_fund_flow      由本專案每日自行觀測累積，沒有回溯資料
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import data as bt_data, panel as bt_panel, strategy as bt_strategy  # noqa: E402
from backtest.run import _start_date  # noqa: E402
from liquidity_monitor.config import CATEGORY_ITEMS  # noqa: E402
from liquidity_monitor.timeseries import nyse_trading_days  # noqa: E402

log = logging.getLogger(__name__)

OUT_PATH = "docs/data/scores_history_reconstructed.csv"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--out", default=OUT_PATH)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    as_of = args.as_of or pd.Timestamp.today().date().isoformat()
    start = _start_date(as_of, args.years)
    warm = _start_date(start, 3)          # 年增率與均線需要暖身期
    use_cache = not args.no_cache

    market = bt_data.fetch_market_panel(warm, as_of, use_cache)
    fred_panel = bt_data.fetch_fred_panel(warm, as_of, use_cache)
    margin = bt_data.fetch_margin_debt(warm, as_of, use_cache)
    days = nyse_trading_days(warm, as_of)

    raw = bt_panel.build_indicator_panel(fred_panel, market, margin, days)
    scores = bt_strategy.score_panel(raw)
    comp = bt_strategy.composite_panel(scores)

    window = comp.index[(comp.index >= pd.Timestamp(start)) & (comp.index <= pd.Timestamp(as_of))]
    comp = comp.loc[window].dropna(subset=["composite_score"])
    if comp.empty:
        print("重建結果為空：指標面板沒有任何可用的日子", file=sys.stderr)
        return 1

    out = pd.DataFrame({
        "as_of": [str(d.date()) for d in comp.index],
        "composite_score": comp["composite_score"].round(4).values,
        "light": comp["light"].values,
        "items_used": comp["items_used"].astype(int).values,
    })
    # 欄名刻意與 scores_history.csv 不同一份語意：這裡多一個 items_used，
    # 讓讀檔的人一眼看出每一天實際用了幾項指標（不是恆定的 13）
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)

    used = list(scores.columns)
    missing = [i for cat, items in CATEGORY_ITEMS.items() for i in items if i not in used]
    print(f"[{out['as_of'].iloc[0]} ~ {out['as_of'].iloc[-1]}] 重建 {len(out)} 個交易日")
    print(f"  採用 {len(used)} 項指標；未納入 {len(missing)} 項：{missing}")
    print(f"  每日實際可用指標數：中位數 {int(out['items_used'].median())}、"
          f"最少 {int(out['items_used'].min())}、最多 {int(out['items_used'].max())}")
    print(f"  分數範圍 {out['composite_score'].min():.3f} ~ {out['composite_score'].max():.3f}")
    print(f"檔案：{path}")
    print("  注意：這不是線上那個 18 項指標的分數，兩者不可直接比較。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
