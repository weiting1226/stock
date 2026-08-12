"""從價格歷史回溯重建輪動指標。

**哪些回溯得了、哪些回溯不了**，必須先講清楚：

  可以：報酬率、相對強弱、動能、象限、名次
        ——這些只需要價格，而價格有多年歷史
  不行：資金流（申贖流量）
        ——需要「當時的流通股數與淨資產」逐日快照。那是本專案每天自己記
        下來的，沒有任何來源提供回溯查詢。硬要補只能用今天的股數往回推，
        而那會得到一條完全錯誤的曲線

因此回溯出來的歷史**只含價格面**，資金流仍然從今天開始累積。
報告會標明這件事，不讓兩者混在一起。
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .config import RS_MOMENTUM_WINDOW, RS_TREND_WINDOW, SECTOR_ETFS, WINDOWS
from .metrics import price_ratio, rotation_signal

log = logging.getLogger(__name__)


def rs_series(sector: pd.Series, benchmark: pd.Series,
              window: int = RS_TREND_WINDOW) -> pd.Series:
    """整段相對強弱序列（與 metrics.relative_strength 同定義，回傳全序列）。"""
    ratio = price_ratio(sector, benchmark)
    if ratio.empty:
        return pd.Series(dtype=float)
    ma = ratio.rolling(window, min_periods=max(10, window // 3)).mean()
    return (ratio / ma * 100).dropna()


def momentum_series(rs: pd.Series, days: int = RS_MOMENTUM_WINDOW) -> pd.Series:
    prior = rs.shift(days)
    return ((rs / prior.where(prior != 0) - 1) * 100).dropna()


def sector_history(prices: pd.DataFrame, benchmark: str) -> dict[str, pd.DataFrame]:
    """每個類股一份逐日的 RS／動能／各視窗報酬。

    向量化計算：整條序列一次算完，不逐日重跑。逐日重算 500 天 × 11 檔
    會慢上兩個數量級，而結果完全相同。
    """
    if benchmark not in prices.columns:
        raise ValueError(f"缺少基準 {benchmark}")
    bench = prices[benchmark].dropna()

    out = {}
    for ticker in SECTOR_ETFS:
        if ticker not in prices.columns:
            continue
        px = prices[ticker].dropna()
        if px.empty:
            continue
        rs = rs_series(px, bench)
        mom = momentum_series(rs)
        df = pd.DataFrame({"rs": rs, "momentum": mom})
        for key, n in WINDOWS.items():
            prior = px.shift(n)
            df[f"ret_{key}"] = ((px / prior.where(prior != 0) - 1) * 100).reindex(df.index)
        # 象限逐日判定：同一組規則，只是套用在每一天上
        df["quadrant"] = [
            rotation_signal(r, m) for r, m in zip(df["rs"], df["momentum"])
        ]
        out[ticker] = df.dropna(subset=["rs", "momentum"])
    return out


def build_trails(history: dict[str, pd.DataFrame], points: int = 8,
                 step_days: int = 5) -> dict:
    """四象限的軌跡：每檔類股最近 N 個時點的位置。

    取樣間隔用交易日（預設每週一點）而不是逐日：逐日的軌跡會糾成一團，
    看不出方向；每週一點才看得出來這個類股是往哪一格移動。
    """
    trails = {}
    for ticker, df in history.items():
        if df.empty:
            continue
        sampled = df.iloc[::-1].iloc[::step_days].iloc[:points].iloc[::-1]
        trails[ticker] = {
            "dates": [str(d.date()) for d in sampled.index],
            "rs": [round(float(v), 2) for v in sampled["rs"]],
            "momentum": [round(float(v), 3) for v in sampled["momentum"]],
        }
    return trails


def quadrant_timeline(history: dict[str, pd.DataFrame], days: int = 120) -> dict:
    """每一天各象限有幾檔——看得出市場是「多數改善」還是「多數轉弱」。"""
    if not history:
        return {"dates": [], "counts": {}}
    idx = sorted(set().union(*(df.index for df in history.values())))[-days:]
    counts = {q: [] for q in ("領漲", "改善", "轉弱", "落後")}
    for ts in idx:
        day = [df.at[ts, "quadrant"] for df in history.values() if ts in df.index]
        for q in counts:
            counts[q].append(day.count(q))
    return {"dates": [str(pd.Timestamp(d).date()) for d in idx], "counts": counts}


def rank_history(history: dict[str, pd.DataFrame], key: str = "1w",
                 days: int = 120) -> dict:
    """逐日名次。名次的變化就是輪動本身。"""
    if not history:
        return {"dates": [], "ranks": {}}
    idx = sorted(set().union(*(df.index for df in history.values())))[-days:]
    ranks = {t: [] for t in history}
    col = f"ret_{key}"
    for ts in idx:
        vals = {t: (df.at[ts, col] if ts in df.index else np.nan) for t, df in history.items()}
        usable = {t: v for t, v in vals.items() if pd.notna(v)}
        order = sorted(usable, key=lambda t: usable[t], reverse=True)
        pos = {t: i + 1 for i, t in enumerate(order)}
        for t in ranks:
            ranks[t].append(pos.get(t))
    return {"dates": [str(pd.Timestamp(d).date()) for d in idx], "ranks": ranks}
