#!/usr/bin/env python3
"""模組九進入點：從模組二的估值報告中，篩出機構買評裡缺口最大的股票。

    python3 scripts/run_gap_radar.py -v

前提：docs/data/valuation/latest.json 已存在（先跑過 scripts/run_valuation.py）。
本模組每週排程一次，非每日——詳見 .github/workflows/gap-radar.yml。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gap_radar import pipeline, storage  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--valuation-path", default="docs/data/valuation/latest.json")
    parser.add_argument("--data-root", default="docs/data/gap_radar")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    report = pipeline.run(valuation_path=args.valuation_path, top_n=args.top_n)
    storage.save_report(report, data_root=args.data_root)

    c = report["counts"]
    print(
        f"[{report['as_of']}] 買評股 {c['candidates']} 檔（買入 {c['buy']} / 強力買入 {c['strong_buy']}），"
        f"取上漲空間前 {len(report['top10'])} 檔"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
