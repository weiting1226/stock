"""模組三主流程：抓資料 -> 建面板 -> 計分 -> 配權重 -> 模擬 -> 產出報告。"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from liquidity_monitor.config import CATEGORY_ITEMS, CATEGORY_LABELS, ITEM_LABELS
from liquidity_monitor.timeseries import nyse_trading_days

from . import data, engine, metrics, panel, strategy
from .config import (
    BACKTEST_YEARS,
    COST_BPS_PER_SIDE,
    EXCLUDED_CATEGORIES,
    EXECUTION_LAG_DAYS,
    QQQ,
    REBALANCE_ON_CHANGE_ONLY,
    SGOV,
    TQQQ,
    UNAVAILABLE_ITEMS,
)

log = logging.getLogger(__name__)

# 覆蓋率低於這個比例就特別標示：名列「已採用」但多數時間沒有值的指標，
# 對回測結果的影響遠小於它在名單上看起來的樣子。
LOW_COVERAGE_PCT = 50.0

# 圖表要呈現「指標與 QQQ 的關係」，逐日 3800 點 × 十幾條線太重。
# 取樣到每週一點，形狀完全保留，檔案小一個數量級。
CHART_SAMPLE_DAYS = 5


def _start_date(end: str, years: int) -> str:
    e = pd.Timestamp(end)
    return str((e - pd.DateOffset(years=years)).date())


def _clean(v):
    """把 NaN／numpy 型別轉成 JSON 寫得出去的值。"""
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def _series_for_chart(s: pd.Series, digits: int = 3) -> list:
    return [None if pd.isna(v) else round(float(v), digits) for v in s]


def run_backtest(
    as_of: str = None,
    years: int = BACKTEST_YEARS,
    use_cache: bool = True,
    use_gates: bool = True,
    cost_bps: float = COST_BPS_PER_SIDE,
) -> dict:
    as_of = as_of or date.today().isoformat()
    start = _start_date(as_of, years)
    log.info("回測期間 %s ~ %s", start, as_of)

    # --- 資料 ---------------------------------------------------------------
    # 指標需要更早的資料才能在回測起點就算得出來（年增率要一年、200日線要一年、
    # Gate B 的滾動百分位要更久）。多抓三年當暖身期，回測本身仍從 start 開始。
    warmup_start = _start_date(start, 3)
    prices = data.fetch_prices(warmup_start, as_of, use_cache)
    market = data.fetch_market_panel(warmup_start, as_of, use_cache)
    fred_panel = data.fetch_fred_panel(warmup_start, as_of, use_cache)
    margin = data.fetch_margin_debt(warmup_start, as_of, use_cache)

    cash_px, cash_info = data.splice_cash(prices)
    px = pd.DataFrame({
        QQQ: prices.get(QQQ),
        TQQQ: prices.get(TQQQ),
        SGOV: cash_px,
    }).dropna(how="all")

    trading_days = nyse_trading_days(warmup_start, as_of)

    # --- 指標與計分 ---------------------------------------------------------
    raw = panel.build_indicator_panel(fred_panel, market, margin, trading_days)
    scores = strategy.score_panel(raw)
    comp = strategy.composite_panel(scores)

    gate_a = strategy.gate_a_panel(raw["ndx_close"])
    gate_b = strategy.gate_b_panel(raw)
    weights = strategy.target_weights(comp, gate_a, gate_b, use_gates=use_gates)

    # --- 模擬 ---------------------------------------------------------------
    # 三個標的都要有價格才能開始（TQQQ 2010-02 成立）
    px = px.dropna()
    window = px.index[(px.index >= pd.Timestamp(start)) & (px.index <= pd.Timestamp(as_of))]
    px = px.loc[window]
    if px.empty:
        raise ValueError(f"回測區間 {start}~{as_of} 沒有三個標的都有價格的交易日")

    result = engine.simulate(weights, px, cost_bps_per_side=cost_bps,
                             rebalance_on_change_only=REBALANCE_ON_CHANGE_ONLY)
    if result.empty:
        raise ValueError("模擬結果為空：訊號與價格沒有重疊的交易日")

    rf = px[SGOV].pct_change().reindex(result.index).fillna(0.0)
    qqq_bh = engine.buy_and_hold(px[QQQ].loc[result.index])
    tqqq_bh = engine.buy_and_hold(px[TQQQ].loc[result.index])

    strategies = {
        "strategy": {
            "label": "模組一策略（含成本）",
            **metrics.summarize(result["net_return"], result["equity"], rf),
        },
        "strategy_gross": {
            "label": "模組一策略（不計成本）",
            **metrics.summarize(result["gross_return"], result["equity_gross"], rf),
        },
        "qqq": {"label": "QQQ 買進持有", **metrics.summarize(qqq_bh["net_return"], qqq_bh["equity"], rf)},
        "tqqq": {"label": "TQQQ 買進持有", **metrics.summarize(tqqq_bh["net_return"], tqqq_bh["equity"], rf)},
    }

    # --- 圖表資料 -----------------------------------------------------------
    sample = result.index[::CHART_SAMPLE_DAYS]
    comp_s = comp.reindex(result.index)
    chart = {
        "dates": [str(d.date()) for d in sample],
        "equity": {
            "strategy": _series_for_chart(result["equity"].reindex(sample), 4),
            "qqq": _series_for_chart(qqq_bh["equity"].reindex(sample), 4),
            "tqqq": _series_for_chart(tqqq_bh["equity"].reindex(sample), 4),
        },
        "composite_score": _series_for_chart(comp_s["composite_score"].reindex(sample), 3),
        "qqq_price": _series_for_chart(px[QQQ].reindex(sample), 2),
        "weights": {
            a: _series_for_chart(result[f"w_{a}"].reindex(sample), 3)
            for a in (QQQ, TQQQ, SGOV) if f"w_{a}" in result.columns
        },
        "indicator_scores": {
            col: _series_for_chart(scores[col].reindex(sample), 0)
            for col in scores.columns
        },
        "category_scores": {
            cat: _series_for_chart(comp_s.get(f"cat_{cat}", pd.Series(dtype=float)).reindex(sample), 3)
            for cat in CATEGORY_ITEMS if cat not in EXCLUDED_CATEGORIES
        },
    }

    # --- 誠實交代這個回測「不是」什麼 ---------------------------------------
    used = list(scores.columns)
    excluded_by_request = [i for c in EXCLUDED_CATEGORIES for i in CATEGORY_ITEMS[c]]

    # 每一項在回測期間**實際有值的比例**。
    #
    # 這一段是必要的：一個指標可能整條流程都接得上、名列「已採用」，實際上
    # 卻只在最後三年有資料。實測就有兩個這種案例——FINRA 融資餘額只發布近
    # 13 個月（覆蓋 0.1%）、FRED 的 HY OAS 只回傳近三年（覆蓋 19.9%）。
    # 少了這張表，畫面會顯示「採用 13 項指標」，讀者自然以為十五年間都有
    # 13 項在運作，而實際上多數時間只有 8 項。名單與覆蓋率必須一起看。
    scores_in_window = scores.reindex(result.index)
    n_days = len(scores_in_window)
    item_coverage = {
        k: round(100.0 * scores_in_window[k].notna().sum() / n_days, 1)
        for k in used
    } if n_days else {}
    # 原始序列的起始日：拿來分辨「來源只給得出這麼多」與「我們算錯了」
    series_start = {}
    for k, s in list(fred_panel.items()) + [("finra_margin_debt", margin)]:
        if s is not None and not s.empty:
            series_start[k] = str(pd.Timestamp(s.index.min()).date())

    coverage = {
        "items_used": [
            {"key": k, "label": ITEM_LABELS.get(k, k), "coverage_pct": item_coverage.get(k)}
            for k in used
        ],
        "item_coverage_pct": item_coverage,
        "source_series_start": series_start,
        "low_coverage_items": [
            {"key": k, "label": ITEM_LABELS.get(k, k), "coverage_pct": v}
            for k, v in sorted(item_coverage.items(), key=lambda kv: kv[1])
            if v < LOW_COVERAGE_PCT
        ],
        "items_excluded_by_request": [
            {"key": k, "label": ITEM_LABELS.get(k, k), "reason": "使用者指定：回測不考慮政策指標"}
            for k in excluded_by_request
        ],
        "items_unavailable": [
            {"key": k, "label": ITEM_LABELS.get(k, k), "reason": reason}
            for k, reason in (
                ("ndx_breadth_200d", "需要當時的成分股名單才能正確回推，用今日名單會有存活者偏誤"),
                ("etf_fund_flow", "由本專案每日自行觀測累積，沒有回溯資料"),
            )
            if k in UNAVAILABLE_ITEMS
        ],
        "categories_used": [
            {"key": c, "label": CATEGORY_LABELS[c]}
            for c in CATEGORY_ITEMS if c not in EXCLUDED_CATEGORIES
        ],
    }

    ladder_days = weights.reindex(result.index)["alloc"].value_counts()
    cap_days = weights.reindex(result.index)["cap"].value_counts(dropna=False)

    return {
        "as_of": as_of,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "period": {"start": str(result.index[0].date()), "end": str(result.index[-1].date()),
                   "trading_days": int(len(result))},
        "assumptions": {
            "execution_lag_days": EXECUTION_LAG_DAYS,
            "cost_bps_per_side": cost_bps,
            "rebalance_on_change_only": REBALANCE_ON_CHANGE_ONLY,
            "gates_applied": use_gates,
            "cash_splice": {k: _clean(v) for k, v in cash_info.items()},
        },
        "coverage": coverage,
        "metrics": strategies,
        "exposure": {
            "ladder_days": {str(k): int(v) for k, v in ladder_days.items()},
            "cap_days": {("none" if pd.isna(k) else str(k)): int(v) for k, v in cap_days.items()},
            "avg_weights": {a: round(float(result[f"w_{a}"].mean()), 4)
                            for a in (QQQ, TQQQ, SGOV) if f"w_{a}" in result.columns},
            "total_turnover": round(float(result["turnover"].sum()), 2),
            "rebalances": int((result["turnover"] > 0).sum()),
        },
        "chart": chart,
    }
