#!/usr/bin/env python3
"""模組九進入點：從模組二的估值報告中，篩出機構買評裡缺口最大的股票。

    python3 scripts/run_gap_radar.py -v
    python3 scripts/run_gap_radar.py --universe ndx100 -v   # Nasdaq-100 而非 S&P 500

前提：對應的模組二報告已存在（先跑過 scripts/run_valuation.py，
S&P 500 用 --universe sp500、Nasdaq-100 用 --universe ndx100）。
排程綁在模組二跑完的事件上（不是固定時刻），因此每個交易日更新一次——
詳見 .github/workflows/gap-radar.yml 裡的說明。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gap_radar import pipeline, storage  # noqa: E402

_DEFAULT_VALUATION_PATH = {
    "sp500": "docs/data/valuation/latest.json",
    "ndx100": "docs/data/valuation/ndx100_latest.json",
}
_PREFIX = {"sp500": "", "ndx100": "ndx100_"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", choices=["sp500", "ndx100"], default="sp500",
                        help="股票池：S&P 500（預設）或 Nasdaq-100")
    parser.add_argument("--valuation-path", default=None,
                        help="預設依 --universe 自動決定（模組二對應股票池的 latest.json）")
    parser.add_argument("--data-root", default="docs/data/gap_radar")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    valuation_path = args.valuation_path or _DEFAULT_VALUATION_PATH[args.universe]

    report = pipeline.run(valuation_path=valuation_path, top_n=args.top_n)
    storage.save_report(report, data_root=args.data_root, prefix=_PREFIX[args.universe])

    c = report["counts"]
    print(
        f"[{report['as_of']}] {report.get('universe') or args.universe}　買評股 {c['candidates']} 檔"
        f"（買入 {c['buy']} / 強力買入 {c['strong_buy']}），取上漲空間前 {len(report['top10'])} 檔"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
