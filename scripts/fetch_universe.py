#!/usr/bin/env python3
"""逐檔抓取全美股資料：由帳本驅動，可續跑、失敗自動重試、逐筆保存。

    # 第一次／每日新一輪（保留已成功的，只補未完成與失敗的）
    python3 scripts/fetch_universe.py --new-cycle --keep-ok --max-tickers 1500 -v

    # 接著跑（不重置，直接取帳本裡還沒完成的）
    python3 scripts/fetch_universe.py --max-tickers 1500 -v

    # 人工排除問題後，把放棄重試的項目放回佇列
    python3 scripts/fetch_universe.py --reset-dead -v

單次執行刻意限量：全美股 8000+ 檔無法在一次 GitHub Actions 執行內跑完
（有 6 小時上限，外部來源也會限流）。帳本會記住進度，下次執行接著做。
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyst_valuation import universe_runner  # noqa: E402


def _load_universe(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        raise SystemExit(
            f"找不到公司清單 {path}；請先執行 scripts/build_universe.py 建立。"
        )
    with p.open(newline="", encoding="utf-8") as fh:
        return [
            (row.get("ticker") or "").strip()
            for row in csv.DictReader(fh)
            if (row.get("ticker") or "").strip()
        ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", default="data/universe.csv")
    parser.add_argument("--ledger", default="data/ledger.csv")
    parser.add_argument("--results", default="data/results")
    parser.add_argument("--max-tickers", type=int, default=1500,
                        help="本次最多處理幾檔（預設1500，避免超過執行時間上限）")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--new-cycle", action="store_true", help="開始新一輪，重置狀態")
    parser.add_argument("--keep-ok", action="store_true",
                        help="搭配 --new-cycle：只重跑失敗的，保留已成功的")
    parser.add_argument("--reset-dead", action="store_true",
                        help="把已達重試上限的項目放回佇列")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    tickers = _load_universe(args.universe)
    result = universe_runner.run(
        universe_tickers=tickers,
        ledger_path=args.ledger,
        results_root=args.results,
        max_tickers=args.max_tickers,
        max_workers=args.max_workers,
        new_cycle=args.new_cycle,
        keep_ok=args.keep_ok,
        reset_dead=args.reset_dead,
    )

    s = result["summary"]
    print(
        f"本次處理 {result['processed']} 檔（成功 {result.get('ok', 0)}、"
        f"失敗 {result.get('failed', 0)}、順延 {result.get('deferred', 0)}）｜"
        f"帳本：完成 {s.get('ok', 0)}／待處理 {s.get('pending', 0)}／"
        f"可重試 {s.get('failed', 0)}／已放棄 {s.get('dead', 0)}，共 {s.get('total', 0)} 檔"
    )
    print(f"已保存結果總數：{result.get('stored_total', 0)}")
    if result.get("disabled_sources"):
        print("本次熔斷的來源：" + json.dumps(result["disabled_sources"], ensure_ascii=False))
    if result.get("dead_sample"):
        print("放棄重試的樣本（需人工檢視）：")
        for d in result["dead_sample"][:5]:
            print(f"  {d['ticker']}（{d['attempts']}次）: {d['error'][:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
