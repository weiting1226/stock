#!/usr/bin/env python3
"""模組八：尿布（M 號）各平台電商單片價格監控。

    python3 scripts/run_diaper_monitor.py -v
    python3 scripts/run_diaper_monitor.py --as-of 2026-08-14
    python3 scripts/run_diaper_monitor.py --skip-scrape

資料以人工填入的 data/diaper_monitor/manual_prices.csv 為主（格式見同資料夾
README.md），另外會先跑一輪 PChome 自動查價（見 diaper_monitor/sources/pchome.py）
補人工沒查到的空檔，寫進 data/diaper_monitor/scraped_prices.csv——兩邊都有資料
時人工的蓋過爬蟲的。爬蟲抓失敗（連線、逾時、格式對不上）不會中斷整支腳本，
只會在 -v 模式下印出警告，那個平台當天就當作沒抓到。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from diaper_monitor import pipeline, storage  # noqa: E402
from diaper_monitor.config import (  # noqa: E402
    BRANDS,
    DATA_ROOT,
    DROP_THRESHOLD_PCT,
    MANUAL_PRICES_PATH,
    SCRAPED_PRICES_PATH,
)
from diaper_monitor.sources import pchome  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", default=None, help="計算基準日 (YYYY-MM-DD)，預設為今天")
    ap.add_argument("--manual-path", default=MANUAL_PRICES_PATH)
    ap.add_argument("--scraped-path", default=SCRAPED_PRICES_PATH)
    ap.add_argument("--data-root", default=DATA_ROOT)
    ap.add_argument("--skip-scrape", action="store_true", help="不跑自動爬蟲，只用人工資料")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    storage.ensure_manual_prices_template(args.manual_path)
    as_of = args.as_of or date.today().isoformat()

    if args.skip_scrape:
        scraped_path = None
    else:
        quotes = pchome.fetch_all(BRANDS)
        storage.write_scraped_prices(quotes, as_of, args.scraped_path)
        print(f"PChome 自動爬蟲：取得 {len(quotes)} 筆報價"
              + ("（-v 查看各品牌明細／略過原因）" if not args.verbose and not quotes else ""))
        scraped_path = args.scraped_path

    try:
        report = pipeline.build_report(as_of=as_of, manual_path=args.manual_path,
                                        scraped_path=scraped_path)
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
