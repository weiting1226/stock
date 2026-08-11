#!/usr/bin/env python3
"""模組三：用近十五年的實際價格回測模組一的策略。

    python3 scripts/run_backtest.py -v

依使用者要求，回測**不納入 ⑥ 政策方向**。另有兩項指標因為沒有可回溯的
歷史而無法納入（NDX 廣度需要當時的成分股名單、ETF 資金流是本專案自行
累積的觀測），報告會逐項列出，不會默默少算。

抓到的原始序列會快取在 data/backtest/，重跑時預設沿用快取；
要重新抓網路資料請加 --no-cache。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import run as backtest_run  # noqa: E402
from backtest.config import BACKTEST_YEARS, COST_BPS_PER_SIDE, DATA_ROOT  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--years", type=int, default=BACKTEST_YEARS)
    parser.add_argument("--cost-bps", type=float, default=COST_BPS_PER_SIDE,
                        help="單邊換手成本（基點）")
    parser.add_argument("--no-cache", action="store_true", help="忽略快取，重新抓網路資料")
    parser.add_argument("--no-gates", action="store_true", help="不套用三道閘門，只看計分階梯")
    parser.add_argument("--out", default=f"{DATA_ROOT}/latest.json")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        report = backtest_run.run_backtest(
            as_of=args.as_of,
            years=args.years,
            use_cache=not args.no_cache,
            use_gates=not args.no_gates,
            cost_bps=args.cost_bps,
        )
    except Exception as e:  # noqa: BLE001
        print(f"回測失敗：{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    p, m = report["period"], report["metrics"]
    print(f"[{p['start']} ~ {p['end']}] {p['trading_days']} 個交易日")
    for key in ("strategy", "qqq", "tqqq"):
        s = m[key]
        print(f"  {s['label']:<22} 總報酬 {s['total_return_pct']:>10,.1f}%"
              f"  年化 {s['cagr_pct']:>6.2f}%"
              f"  最大回撤 {s['max_drawdown_pct']:>7.2f}%"
              f"  Sharpe {s['sharpe'] if s['sharpe'] is not None else float('nan'):>6.2f}")
    cov = report["coverage"]
    print(f"  採用指標 {len(cov['items_used'])} 項；"
          f"依要求排除 {len(cov['items_excluded_by_request'])} 項；"
          f"無歷史 {len(cov['items_unavailable'])} 項")
    print(f"檔案：{out}（{out.stat().st_size / 1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
