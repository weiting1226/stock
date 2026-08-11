#!/usr/bin/env python3
"""搜尋更有效的買賣門檻，並以逐年前進驗證檢查它是不是只是過度配適。

    python3 scripts/optimize_thresholds.py -v

輸出三個數字，缺一不可：

  樣本內最佳    在整段歷史上挑出來的最好結果——**這個數字沒有意義**，
                只是拿來當作過度配適的上界參考
  樣本外        逐年前進：每年的門檻只用該年之前的資料選出來
  現行預設      目前採用的門檻，當作對照

樣本內與樣本外的落差，就是過度配適的程度。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import allin, data, engine, metrics, optimize, panel, strategy  # noqa: E402
from backtest.config import COST_BPS_PER_SIDE, DATA_ROOT, QQQ, SGOV, TQQQ  # noqa: E402
from backtest.run import _start_date  # noqa: E402
from liquidity_monitor.timeseries import nyse_trading_days  # noqa: E402

log = logging.getLogger(__name__)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", default="2026-08-10")
    ap.add_argument("--years", type=int, default=15)
    ap.add_argument("--objective", default="sharpe", choices=["sharpe", "calmar", "cagr"])
    ap.add_argument("--cost-bps", type=float, default=COST_BPS_PER_SIDE)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--out", default=f"{DATA_ROOT}/thresholds.json")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    start = _start_date(args.as_of, args.years)
    warm = _start_date(start, 3)
    use_cache = not args.no_cache

    prices_raw = data.fetch_prices(warm, args.as_of, use_cache)
    market = data.fetch_market_panel(warm, args.as_of, use_cache)
    fred_panel = data.fetch_fred_panel(warm, args.as_of, use_cache)
    margin = data.fetch_margin_debt(warm, args.as_of, use_cache)

    days = nyse_trading_days(warm, args.as_of)
    raw = panel.build_indicator_panel(fred_panel, market, margin, days)
    comp = strategy.composite_panel(strategy.score_panel(raw))
    ga, gb = strategy.gate_a_panel(raw["ndx_close"]), strategy.gate_b_panel(raw)

    cash, _ = data.splice_cash(prices_raw)
    px = pd.DataFrame({QQQ: prices_raw[QQQ], TQQQ: prices_raw[TQQQ], SGOV: cash}).dropna()
    px = px.loc[(px.index >= pd.Timestamp(start)) & (px.index <= pd.Timestamp(args.as_of))]
    rf = px[SGOV].pct_change().fillna(0.0)

    log.info("候選門檻 %d 組，期間 %s ~ %s", len(optimize.candidate_rules()),
             px.index[0].date(), px.index[-1].date())

    # --- 樣本內最佳（僅供對照，不是成果）---------------------------------
    weights_by_label = optimize.build_weights(comp, ga, gb)
    ranked = optimize.search(weights_by_label, px, rf, px.index, args.objective, args.cost_bps)
    best_c, best_m, best_s = ranked[0]
    plateau = optimize.plateau(ranked)

    # --- 樣本外：逐年前進 --------------------------------------------------
    wf = optimize.walk_forward(comp, ga, gb, px, rf, args.objective, args.cost_bps)

    # --- 現行預設與買進持有 ------------------------------------------------
    #
    # 對照組必須跟樣本外**同一段期間**。逐年前進要留前幾年當訓練期，
    # 所以它的樣本外只從第 5 年開始；拿 2011 年起算的全期績效去比 2015 年
    # 起算的樣本外，比的是兩段不同的市場，不是兩套方法。
    default_rules = allin.AllInRules()
    default_w = allin.allin_weights(comp, ga, gb, default_rules)
    oos_index = wf["returns"].index
    default_m = optimize.evaluate(default_w, px, rf, px.index, args.cost_bps)
    default_oos = optimize.evaluate(default_w, px, rf, oos_index, args.cost_bps)

    def bh(idx):
        r = px[QQQ].reindex(idx).pct_change().fillna(0.0)
        return metrics.summarize(r, (1 + r).cumprod(), rf.reindex(idx).fillna(0.0))

    qqq_m, qqq_oos = bh(px.index), bh(oos_index)

    report = {
        "as_of": args.as_of,
        "objective": args.objective,
        "period": {"start": str(px.index[0].date()), "end": str(px.index[-1].date())},
        "candidates": len(ranked),
        "in_sample_best": {
            "label": best_c.label,
            "tqqq_center": best_c.rules.tqqq_center,
            "sgov_center": best_c.rules.sgov_center,
            "buffer": best_c.rules.buffer,
            "metrics": best_m,
            "warning": "在同一段歷史上挑出來的最佳值，必然偏樂觀，不可當成預期績效",
        },
        "walk_forward": {
            "metrics": wf["metrics"],
            "picks": wf["picks"],
            "note": "每一年的門檻只用該年之前的資料選出；這才是這套方法真正的成績",
        },
        "current_default": {
            "tqqq_center": default_rules.tqqq_center,
            "sgov_center": default_rules.sgov_center,
            "buffer": default_rules.buffer,
            "metrics": default_m,
            "metrics_same_window_as_walk_forward": default_oos,
        },
        "qqq_buy_and_hold": {"metrics": qqq_m,
                             "metrics_same_window_as_walk_forward": qqq_oos},
        "comparison_window": {
            "walk_forward_start": str(oos_index[0].date()),
            "note": "逐年前進需留訓練期，樣本外從第 5 年才開始；同期欄位才可直接比較",
        },
        "plateau": plateau,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    def line(tag, m):
        if not m:
            print(f"  {tag:<26} （無結果）")
            return
        print(f"  {tag:<26} 年化 {m['cagr_pct']:>7.2f}%  Sharpe {m['sharpe']:>6.2f}"
              f"  最大回撤 {m['max_drawdown_pct']:>7.1f}%  換倉 {m.get('rebalances','—')}")

    print(f"[{report['period']['start']} ~ {report['period']['end']}] "
          f"候選 {report['candidates']} 組，目標函式 {args.objective}")
    line("樣本內最佳（僅供對照）", best_m)
    print(f"  —— 以下三者為同一段期間（{oos_index[0].date()} 起）——")
    line("樣本外（逐年前進）", wf["metrics"])
    line("現行預設門檻（同期）", default_oos)
    line("QQQ 買進持有（同期）", qqq_oos)
    print(f"  樣本內最佳門檻：{best_c.label}")
    print(f"  高原檢查：最佳 {plateau.get('best_score')}／中位數 {plateau.get('median_score')}"
          f"／前十名分數差 {plateau.get('top_score_spread')}")
    print(f"檔案：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
