#!/usr/bin/env python3
"""建立全美股上市公司清單，輸出 data/universe.csv。

    python3 scripts/build_universe.py -v
    python3 scripts/build_universe.py --include-etf -v

來源優先序：NASDAQ Trader Symbol Directory → SEC company_tickers.json（備援）。
兩者都失敗，或解析出的筆數明顯過少時，會直接報錯而**不覆蓋既有清單**，
避免用殘缺資料把好的清單洗掉。
"""
from __future__ import annotations

import argparse
import collections
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyst_valuation import universe_file  # noqa: E402
from analyst_valuation.sources import us_listings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/universe.csv")
    parser.add_argument("--include-etf", action="store_true", help="一併納入 ETF")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    listings = us_listings.fetch_us_listings(include_etf=args.include_etf)

    # 清單保留全部證券與其分類；要不要抓由讀取端決定（見 universe_file）
    universe_file.save_universe([x.as_dict() for x in listings], args.out)

    by_exchange = collections.Counter(x.exchange for x in listings)
    by_type = collections.Counter(x.security_type for x in listings)
    print(f"公司清單已寫出：{args.out}（共 {len(listings)} 檔）")
    for ex, n in by_exchange.most_common():
        print(f"  交易所 {ex}: {n}")
    print("證券種類：")
    for t, n in by_type.most_common():
        print(f"  {t}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
