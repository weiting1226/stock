#!/usr/bin/env python3
"""模組五：美股總體經濟指標觀察。

    python3 scripts/run_macro.py -v

27 條 FRED 序列，分為成長／就業／通膨／利率／金融條件／消費房市六類。
每一條都標明參考期與資料落後天數——總體數據的「最新值」永遠是過去某一期的，
把它當成今天的狀況是最常見的誤讀。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macro_monitor import report as macro_report  # noqa: E402
from macro_monitor.config import DATA_ROOT  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--data-root", default=DATA_ROOT)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        rep = macro_report.build_report(as_of=args.as_of)
    except Exception as e:  # noqa: BLE001
        print(f"總經報告產生失敗：{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    out = macro_report.write_report(rep, args.data_root)
    c = rep["counts"]
    print(f"[{rep['as_of']}] {c['available']}/{c['total']} 條可用"
          f"（過期 {c['stale']}、缺失 {c['missing']}）")
    for key, s in rep["summary"].items():
        print(f"  {s['label']:<16} 改善 {s['improving']}／惡化 {s['worsening']}／共 {s['count']}")
    if rep["stale_series"]:
        print("  過期：" + "、".join(f"{s['label']}（落後 {s['lag_days']} 天）"
                                   for s in rep["stale_series"][:5]))
    if rep["missing_series"]:
        print("  缺失：" + "、".join(f"{s['label']}" for s in rep["missing_series"][:5]))
    print(f"檔案：{out}（{out.stat().st_size / 1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
