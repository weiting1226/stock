#!/usr/bin/env python3
"""模組六：尾部脆弱度雙閘門。

    python3 scripts/run_tail_gate.py -v

**影子運行：只記錄、不下單。** 接線說明第 0 節的兩項限制照抄：慢閘門檻未經
真實資料校準，快閘的危機型事件樣本 n=1。在跑完第 6 節的驗證之前，
這個模組的輸出不該接上任何下單邏輯。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tail_gate import report as tail_report  # noqa: E402
from tail_gate.config import DATA_ROOT  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--data-root", default=DATA_ROOT)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        rep = tail_report.build_report(as_of=args.as_of, data_root=args.data_root)
    except Exception as e:  # noqa: BLE001
        print(f"模組六執行失敗：{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    out = tail_report.write_report(rep, args.data_root)
    b, o = rep["base_alloc"], rep["out_alloc"]
    print(f"[{rep['as_of']}] 影子運行")
    print(f"  慢閘 {rep['slow']['regime']}（分數 {rep['slow']['score']}，"
          f"上限 {rep['slow']['equity_ceiling']:.2f}）")
    print(f"  快閘 原始 {rep['fast']['raw_veto']} → 經持續期 {rep['fast']['gated_veto']}"
          f"（倒掛連續 {rep['state']['consec_inversion']} 日／需 "
          f"{rep['controller']['persist_days']} 日）")
    print(f"  主評分 TQQQ {b['tqqq']:.0%}／QQQ {b['qqq']:.0%}／SGOV {b['sgov']:.0%}")
    print(f"  本模組 TQQQ {o['tqqq']:.0%}／QQQ {o['qqq']:.0%}／SGOV {o['sgov']:.0%}"
          f"{'（有調整）' if rep['changed'] else '（未調整）'}")
    if rep["state_meta"].get("staleness_days"):
        print(f"  ⚠ 狀態檔停在 {rep['state_meta']['staleness_days']} 天前")
    for p in rep["problems"]:
        print(f"  ! {p}")
    print(f"檔案：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
