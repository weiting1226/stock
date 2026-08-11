"""模組三主流程：抓資料 -> 建面板 -> 計分 -> 配權重 -> 模擬 -> 產出報告。"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from liquidity_monitor.config import CATEGORY_ITEMS, CATEGORY_LABELS, ITEM_LABELS
from liquidity_monitor.timeseries import nyse_trading_days

from . import allin, data, engine, metrics, panel, strategy
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

# 替代序列的採用門檻。日變化相關看得比水準相關重：兩項 HY OAS 指標中有一項
# 是「20 日變化」，水準相關再高、變化不同步的話，換上去只是換了一個同名的
# 不同訊號。0.7／0.8 不是實測出來的數字，是「要能當替身」的常識下限。
MIN_PROXY_CHANGE_CORR = 0.7
MIN_PROXY_LEVEL_CORR = 0.8

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


def _hy_oas_proxy_check(raw: pd.DataFrame, fred_panel: dict, trading_days) -> dict:
    """量 BAA10Y（投資等級利差，1986 年起）能不能代表 HY OAS（只有近三年）。

    只在兩者都有值的重疊期間比較，回報相關係數與線性關係。**不改動任何計分**
    ——這裡產出的是決策所需的證據，不是決策本身。相關係數高不代表可以直接替換：
    兩者的水準完全不同（HY OAS 約 3~8%、BAA10Y 約 1.5~3.5%），現行門檻是照
    HY OAS 的水準訂的，換序列就必須重訂門檻，而重訂門檻這件事本身已經證實
    無法從歷史穩健地學到。
    """
    from liquidity_monitor.timeseries import to_daily_panel
    hy = raw.get("hy_oas_level")
    baa_raw = fred_panel.get("BAA10Y")
    if hy is None or baa_raw is None or baa_raw.empty:
        return {"available": False, "reason": "BAA10Y 未取得"}
    baa = to_daily_panel(baa_raw, trading_days)
    both = pd.concat([hy.rename("hy"), baa.rename("baa")], axis=1).dropna()
    if len(both) < 250:
        return {"available": False, "reason": f"重疊期間僅 {len(both)} 個交易日，不足以判斷"}

    corr_level = float(both["hy"].corr(both["baa"]))
    d = both.diff().dropna()
    corr_change = float(d["hy"].corr(d["baa"])) if len(d) > 60 else None
    slope, intercept = np.polyfit(both["baa"], both["hy"], 1)
    # 判定門檻：兩項指標中有一項是「20 日變化」，因此**變化量**的相關性
    # 才是能不能替換的關鍵。水準相關高但變化不同步的序列，換上去之後
    # 名字一樣、訊號完全是另一回事。
    usable = (corr_change is not None and corr_change >= MIN_PROXY_CHANGE_CORR
              and corr_level >= MIN_PROXY_LEVEL_CORR)
    return {
        "available": True,
        "usable_as_substitute": bool(usable),
        "verdict": (
            "可考慮替換（仍需以百分位對映重訂門檻）" if usable else
            f"不可替換：日變化相關僅 {corr_change:.2f}"
            f"（需 ≥{MIN_PROXY_CHANGE_CORR}），水準相關 {corr_level:.2f}"
            f"（需 ≥{MIN_PROXY_LEVEL_CORR}）"
        ),
        "overlap_days": int(len(both)),
        "overlap_start": str(both.index.min().date()),
        "baa_history_start": str(pd.Timestamp(baa_raw.index.min()).date()),
        "corr_level": round(corr_level, 3),
        "corr_daily_change": round(corr_change, 3) if corr_change is not None else None,
        "hy_range": [round(float(both["hy"].min()), 2), round(float(both["hy"].max()), 2)],
        "baa_range": [round(float(both["baa"].min()), 2), round(float(both["baa"].max()), 2)],
        "linear_fit": {"slope": round(float(slope), 3), "intercept": round(float(intercept), 3)},
        "note": ("水準相關高不代表可以直接替換：現行門檻是照 HY OAS 的水準訂的，"
                 "換序列就要重訂門檻，而門檻搜尋已證實無法從歷史穩健地學到"),
    }


def _count_recovery_jumps(w: pd.DataFrame) -> int:
    """「從 SGOV 直接跳到 TQQQ」發生了幾次——加速條款到底有沒有被用到。"""
    if "state" not in w.columns:
        return 0
    prev = w["state"].shift()
    return int(((prev == "SGOV") & (w["state"] == "TQQQ")).sum())


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

    # 兩種部位規則並列回測。使用者要的是全押式變體，但只給一條曲線的話，
    # 「調整之後比較好」這句話沒有比較基準——原本的階梯必須一起跑。
    rules = allin.AllInRules()
    variants = {
        "allin": allin.allin_weights(comp, gate_a, gate_b, rules, use_gates=use_gates),
        "ladder": strategy.target_weights(comp, gate_a, gate_b, use_gates=use_gates),
    }
    weights = variants["allin"]

    # --- 模擬 ---------------------------------------------------------------
    # 三個標的都要有價格才能開始（TQQQ 2010-02 成立）
    px = px.dropna()
    window = px.index[(px.index >= pd.Timestamp(start)) & (px.index <= pd.Timestamp(as_of))]
    px = px.loc[window]
    if px.empty:
        raise ValueError(f"回測區間 {start}~{as_of} 沒有三個標的都有價格的交易日")

    sims = {
        name: engine.simulate(w, px, cost_bps_per_side=cost_bps,
                              rebalance_on_change_only=REBALANCE_ON_CHANGE_ONLY)
        for name, w in variants.items()
    }
    result = sims["allin"]
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
        "ladder": {
            "label": "原階梯（混合持有，對照組）",
            **metrics.summarize(sims["ladder"]["net_return"], sims["ladder"]["equity"], rf),
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
            "ladder": _series_for_chart(sims["ladder"]["equity"].reindex(sample), 4),
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

    # HY OAS 被 FRED 限制在近三年（已由端點診斷確認是來源限制，不是抓法問題）。
    # 與其臆測「換成 BAA10Y 應該也可以」，不如在兩者都有資料的重疊期間實際量
    # 它們的關係，把數字擺出來讓人決定要不要換。沒有這個量測，換源就只是換一種猜。
    hy_proxy = _hy_oas_proxy_check(raw, fred_panel, trading_days)

    coverage = {
        "items_used": [
            {"key": k, "label": ITEM_LABELS.get(k, k), "coverage_pct": item_coverage.get(k)}
            for k in used
        ],
        "item_coverage_pct": item_coverage,
        "source_series_start": series_start,
        "hy_oas_proxy_check": hy_proxy,
        # 只對「歷史明顯偏短」的序列附上端點明細：其餘的沒有疑點，附上只是噪音
        "source_fetch_diagnostics": {
            sid: d for sid, d in data.FRED_DIAGNOSTICS.items()
            if d.get("attempts") and len(d["attempts"]) > 1
        },
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
            "position_rule": {
                "mode": "全押單一標的（TQQQ／QQQ／SGOV 三選一）",
                "tqqq_enter": rules.tqqq_enter,
                "tqqq_exit": rules.tqqq_exit,
                "sgov_enter": rules.sgov_enter,
                "qqq_enter": rules.qqq_enter,
                "recovery_enter": rules.recovery_enter,
                "buffer": rules.buffer,
                "note": ("進出場門檻不同（緩衝區），分數在門檻附近來回時不換檔；"
                         "上一個部位是 SGOV 時以較低門檻直接進 TQQQ（加速）"),
            },
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
            # 緩衝區的作用就是少換手，不並列對照組的換手次數就看不出有沒有效
            "rebalances_ladder": int((sims["ladder"]["turnover"] > 0).sum()),
            "recovery_jumps": int(_count_recovery_jumps(variants["allin"])),
        },
        "chart": chart,
    }
