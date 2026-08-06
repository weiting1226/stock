#!/usr/bin/env python3
"""每日排程進入點：抓資料 -> 計分 -> 套用閘門 -> 寫入 docs/data/。

由 .github/workflows/daily-scrape.yml 每日呼叫一次，也可以在本機手動執行：

    python3 scripts/run_daily.py
    python3 scripts/run_daily.py --as-of 2026-08-05
    python3 scripts/run_daily.py --margin-override-csv path/to/margin.csv
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from liquidity_monitor import pipeline, storage  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default=None, help="計算基準日 (YYYY-MM-DD)，預設為今天(UTC)")
    parser.add_argument("--margin-override-csv", default=None, help="FINRA 融資餘額網頁解析失敗時的人工備援CSV")
    parser.add_argument("--data-root", default="docs/data", help="輸出資料夾")
    parser.add_argument("--overrides-path", default="docs/data/manual_overrides.json")
    parser.add_argument("--ndx-cache-path", default="docs/data/ndx100_constituents.json")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    storage.ensure_manual_overrides_template(args.overrides_path)

    report = pipeline.run(
        as_of=args.as_of,
        margin_override_csv=args.margin_override_csv,
        overrides_path=args.overrides_path,
        ndx_cache_path=args.ndx_cache_path,
    )
    storage.save_report(report, data_root=args.data_root)

    print(f"[{report['as_of']}] 綜合分數={report['composite_score']} 燈號={report['light']} "
          f"部位={report['position']['final']} "
          f"缺失/低信心項={report['missing_or_low_confidence_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
