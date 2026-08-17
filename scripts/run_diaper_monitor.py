#!/usr/bin/env python3
"""模組八：尿布（M 號）各平台電商單片價格監控。

    python3 scripts/run_diaper_monitor.py -v
    python3 scripts/run_diaper_monitor.py --as-of 2026-08-14

資料來源是人工填入的 data/diaper_monitor/manual_prices.csv（格式見同資料夾
README.md），本腳本只負責換算單片價、累積歷史、並與近期平均比較是否顯著下跌。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from diaper_monitor import pipeline, storage  # noqa: E402
from diaper_monitor.config import DATA_ROOT, DROP_THRESHOLD_PCT, MANUAL_PRICES_PATH  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", default=None, help="計算基準日 (YYYY-MM-DD)，預設為今天")
    ap.add_argument("--manual-path", default=MANUAL_PRICES_PATH)
    ap.add_argument("--data-root", default=DATA_ROOT)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    storage.ensure_manual_prices_template(args.manual_path)

    try:
        report = pipeline.build_report(as_of=args.as_of, manual_path=args.manual_path)
    except Exception as e:  # noqa: BLE001
        print(f"模組八執行失敗：{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    out = storage.write_report(report, args.data_root)

    print(f"[{report['as_of']}] 尿布 {report['size']} 號單片價監控")
    for b in report["brands"]:
        if not b["has_data"]:
            print(f"  {b['brand']:<12} 尚無任何報價紀錄")
            continue
        stale_tag = f"  [{b['data_date']}，非今日]" if b["is_stale"] else ""
        line = (f"  {b['brand']:<12} 最便宜 {b['cheapest_unit_price']:.2f} 元/片"
                f"（{b['cheapest_platform']}，{b['offer_count']} 個平台報價）{stale_tag}")
        if b["baseline_avg"] is not None:
            line += f"  近{report['baseline_window_days']}日均 {b['baseline_avg']:.2f}  變動 {b['pct_change_vs_baseline']:+.1f}%"
            if b["significant_drop"]:
                line += f"  ⚠ 顯著下跌（跌幅達 {DROP_THRESHOLD_PCT:.0f}%）"
        else:
            line += f"  近期資料不足（{b['baseline_points']} 筆），未判定"
        print(line)
    print(f"檔案：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
