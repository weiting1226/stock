#!/usr/bin/env python3
"""模組四：類股資金輪動觀察。

    python3 scripts/run_sector_rotation.py -v

每日監看十一個 GICS 類股的漲跌（當日／近一週／近一月／近三月）、相對大盤的
強弱與動能、四象限輪動位置，以及由 ETF 申贖流量算出的資金動能。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sector_rotation import report as rotation_report  # noqa: E402
from sector_rotation.config import DATA_ROOT  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--data-root", default=DATA_ROOT)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        rep = rotation_report.build_report(as_of=args.as_of)
    except Exception as e:  # noqa: BLE001
        print(f"類股輪動報告產生失敗：{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    out = rotation_report.write_report(rep, args.data_root)
    b = rep["breadth"]["1d"]
    print(f"[{rep['as_of']}] 基準 {rep['benchmark']['ticker']} "
          f"當日 {rep['benchmark']['returns']['1d']}%　"
          f"上漲 {b['up']}/{b['total']} 個類股")
    ranked = sorted((r for r in rep["rows"] if r.get("returns", {}).get("1w") is not None),
                    key=lambda r: -r["returns"]["1w"])
    for r in ranked[:3]:
        print(f"  領先 {r['sector_zh']:<6} 週 {r['returns']['1w']:+6.2f}%　"
              f"超額 {r['excess']['1w']:+6.2f}%　{r['quadrant']}")
    for r in ranked[-2:]:
        print(f"  落後 {r['sector_zh']:<6} 週 {r['returns']['1w']:+6.2f}%　"
              f"超額 {r['excess']['1w']:+6.2f}%　{r['quadrant']}")
    if rep.get("flow_note"):
        print(f"  資金流：{rep['flow_note'][:120]}")
    print(f"檔案：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
