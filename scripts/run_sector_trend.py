#!/usr/bin/env python3
"""模組七：美股各類股主要 ETF 的漲跌趨勢。

    python3 scripts/run_sector_trend.py -v

與模組四的分工：模組四看 1 日～3 月的輪動與資金流；本模組看 1 月～3 年的
趨勢狀態，而且每個類股列多檔 ETF——「科技類股漲多少」取決於你拿哪一檔。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sector_trend import report as trend_report  # noqa: E402
from sector_trend.config import DATA_ROOT  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--data-root", default=DATA_ROOT)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        rep = trend_report.build_report(as_of=args.as_of)
    except Exception as e:  # noqa: BLE001
        print(f"模組七執行失敗：{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    out = trend_report.write_report(rep, args.data_root)
    bd = rep["breadth"]
    print(f"[{rep['close_date']}] 收盤日基準")
    print(f"  廣度：{bd['above_ma200']}/{bd['total']} 檔代表 ETF 站上 200 日線"
          f"（{bd.get('pct_above_ma200')}%），{bd.get('above_ma50')} 檔站上 50 日線")
    print(f"  基準 {rep['benchmark']['ticker']} 近一年 {rep['benchmark']['returns'].get('1y')}%")
    for s in rep["sectors"]:
        p = next((e for e in s["etfs"] if e["is_primary"]), s["etfs"][0])
        r1y = p["returns"].get("1y")
        disp = s["dispersion_1y"]
        flag = "" if disp is None or disp < 5 else f"  ⚠ 同類股 ETF 一年報酬差 {disp} 個百分點"
        print(f"  {s['sector']:<8} {p['ticker']:<5} 近一年 {r1y if r1y is not None else '—':>7}%"
              f"  200MA {'上' if p.get('above_ma200') else '下'}{flag}")
    if rep["missing"]:
        print(f"  ! 未取得：{'、'.join(rep['missing'])}")
    print(f"檔案：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
