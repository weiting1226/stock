#!/usr/bin/env python3
"""把全美股爬取結果彙整成儀表板讀的 JSON。

    python3 scripts/publish_universe.py -v

爬蟲（`fetch_universe.py`）負責把每一檔的原始查詢結果逐筆存進 data/results/；
這支腳本負責把那些原料算成上漲空間、類股彙總，輸出成前端讀的一份檔案。
兩者分開是因為爬取跨多次執行、要跑好幾個小時，但重新產生報告只要幾秒——
每批爬完都重出一次報告，儀表板就能隨進度持續更新，不必等整輪跑完。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyst_valuation import universe_publish  # noqa: E402
from analyst_valuation.secrets_redaction import redact_secrets  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", default="data/results")
    parser.add_argument("--universe", default="data/universe.csv")
    parser.add_argument("--ledger", default="data/ledger.csv")
    parser.add_argument("--out", default="docs/data/valuation/universe_latest.json")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--include-all", action="store_true",
                        help="連非普通股也一併納入報告（需與抓取端設定一致）")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    report = universe_publish.build_report(
        results_root=args.results,
        universe_path=args.universe,
        ledger_path=args.ledger,
        as_of=args.as_of,
        include_all=args.include_all,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # 比照 storage.save_report：落地前再遮蔽一次憑證。這份檔案會 commit 進公開 repo。
    out.write_text(redact_secrets(json.dumps(report, ensure_ascii=False, default=str)))

    c = report["counts"]
    print(
        f"[{report['as_of']}] 已產生 {out}："
        f"清單 {c['universe']} 檔／已抓取 {c['fetched']} 檔／"
        f"可比較 {c['with_target_and_price']} 檔／無覆蓋 {c['missing']} 檔／"
        f"尚未輪到 {c['not_yet_fetched']} 檔"
    )
    print(f"檔案大小：{out.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
