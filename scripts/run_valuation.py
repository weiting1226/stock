#!/usr/bin/env python3
"""模組二進入點：抓取分析師目標價與收盤價，輸出到 docs/data/valuation/。

    python3 scripts/run_valuation.py -v
    python3 scripts/run_valuation.py --limit 20 -v        # 只跑前20檔，快速驗證
    FMP_API_KEY=xxx python3 scripts/run_valuation.py      # 啟用逐機構目標價（依機構去重後自行平均）
    FINNHUB_API_KEY=xxx python3 scripts/run_valuation.py  # 加入第二個共識來源
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyst_valuation import pipeline, storage  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default=None, help="資料基準日 (YYYY-MM-DD)，預設今天(UTC)")
    parser.add_argument("--data-root", default="docs/data/valuation")
    parser.add_argument("--universe-cache", default="docs/data/valuation/sp500_universe.json")
    parser.add_argument("--limit", type=int, default=None, help="只處理前 N 檔（測試用）")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    report = pipeline.run(
        as_of=args.as_of,
        universe_cache_path=args.universe_cache,
        limit=args.limit,
        max_workers=args.max_workers,
    )
    storage.save_report(report, data_root=args.data_root)

    counts = report["counts"]
    print(
        f"[{report['as_of']}] 股票池 {counts['universe']} 檔，"
        f"取得目標價+收盤價 {counts['with_target_and_price']} 檔，"
        f"暫缺 {counts['missing']} 檔，來源：{'/'.join(report['sources_available']) or '無'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
