"""類股輪動指標（純函式，無網路存取）。

三個容易出錯的地方，都在這裡處理：

1. **視窗一律用交易日**。用日曆天的話，連假前後的「近一週」會是不同長度的
   區間，跨日期比較就沒有意義。
2. **缺值不能變成 0**。XLC 2018-06 才成立、XLRE 2015-10 才成立；把它們的
   缺值當成 0% 報酬，排名時會直接排到中間，看起來完全正常。
3. **相對強弱要用同一段區間的基準**。拿類股的近一月去比基準的近三月，
   算出來的「超額報酬」只是兩個不同區間的差。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .config import RS_MOMENTUM_WINDOW, WINDOWS


def window_return(prices: pd.Series, days: int) -> Optional[float]:
    """N 個交易日的報酬率（%）。資料不足回 None，不回 0。"""
    s = prices.dropna()
    if len(s) <= days:
        return None
    prior = s.iloc[-1 - days]
    if prior == 0 or pd.isna(prior):
        return None
    return float((s.iloc[-1] / prior - 1) * 100)


def relative_strength(sector: pd.Series, benchmark: pd.Series) -> pd.Series:
    """相對強弱比率＝類股價格 ÷ 基準價格，並標準化為起點 100。

    只在兩者**都有值**的交易日上計算。用前向填補去補其中一邊的缺口，
    會在那幾天造出「類股不動而基準在動」的假訊號。
    """
    both = pd.concat([sector.rename("s"), benchmark.rename("b")], axis=1).dropna()
    if both.empty or (both["b"] == 0).any():
        return pd.Series(dtype=float)
    ratio = both["s"] / both["b"]
    return ratio / ratio.iloc[0] * 100


def rs_momentum(rs: pd.Series, days: int = RS_MOMENTUM_WINDOW) -> Optional[float]:
    """RS 比率相對它自己 N 天前的變化（%）。

    正值代表這個類股「相對基準」的優勢正在擴大——也就是資金還在流進來；
    負值代表優勢在收斂。這是判斷輪動方向的核心，比單看報酬率有用：
    大盤大跌那天所有類股都是負報酬，但誰跌得少才是資金在守的地方。
    """
    s = rs.dropna()
    if len(s) <= days:
        return None
    prior = s.iloc[-1 - days]
    if prior == 0 or pd.isna(prior):
        return None
    return float((s.iloc[-1] / prior - 1) * 100)


def rank_series(values: dict[str, Optional[float]]) -> dict[str, Optional[int]]:
    """由大到小排名（1 = 最強）。缺值不參與排名，也不佔名次。

    把缺值當成最差來排，會讓「還沒成立的類股」看起來像「表現最爛的類股」。
    """
    usable = {k: v for k, v in values.items() if v is not None and not pd.isna(v)}
    order = sorted(usable, key=lambda k: usable[k], reverse=True)
    ranks = {k: i + 1 for i, k in enumerate(order)}
    return {k: ranks.get(k) for k in values}


def rotation_signal(rs_ratio: Optional[float], momentum: Optional[float]) -> str:
    """把 (相對強弱, 動能) 分成四個象限——這就是輪動狀況本身。

    領漲：已經強、還在變強      轉弱：已經強、但開始走弱
    落後：還弱、也還在變弱      改善：還弱、但正在變強（資金開始流入）

    「轉弱」與「改善」是輪動真正發生的地方，也是這張圖比單純的漲跌排行
    多出來的資訊：漲幅排行只告訴你過去誰強，象限告訴你資金正在往哪走。
    """
    if rs_ratio is None or momentum is None or pd.isna(rs_ratio) or pd.isna(momentum):
        return "暫缺"
    strong = rs_ratio >= 100
    rising = momentum >= 0
    if strong and rising:
        return "領漲"
    if strong and not rising:
        return "轉弱"
    if not strong and rising:
        return "改善"
    return "落後"


def build_sector_rows(
    prices: pd.DataFrame,
    benchmark: str,
    sector_meta: dict,
) -> list[dict]:
    """每個類股一列：各視窗報酬、相對基準的超額報酬、RS 與動能、輪動象限。"""
    if benchmark not in prices.columns:
        raise ValueError(f"缺少基準 {benchmark} 的價格，無法計算相對強弱")
    bench = prices[benchmark].dropna()
    bench_returns = {k: window_return(bench, n) for k, n in WINDOWS.items()}

    rows = []
    for ticker, (gics, zh) in sector_meta.items():
        if ticker not in prices.columns:
            rows.append({"ticker": ticker, "sector": gics, "sector_zh": zh,
                         "error": "無價格資料"})
            continue
        px = prices[ticker].dropna()
        rets = {k: window_return(px, n) for k, n in WINDOWS.items()}
        # 超額報酬必須用**同一個視窗**的基準報酬，否則只是兩段不同區間的差
        excess = {
            k: (None if rets[k] is None or bench_returns[k] is None
                else round(rets[k] - bench_returns[k], 3))
            for k in WINDOWS
        }
        rs = relative_strength(px, bench)
        rs_now = float(rs.iloc[-1]) if not rs.empty else None
        mom = rs_momentum(rs)

        rows.append({
            "ticker": ticker,
            "sector": gics,
            "sector_zh": zh,
            "close": round(float(px.iloc[-1]), 2) if not px.empty else None,
            "as_of": str(px.index[-1].date()) if not px.empty else None,
            "returns": {k: (None if v is None else round(v, 3)) for k, v in rets.items()},
            "excess": excess,
            "rs_ratio": None if rs_now is None else round(rs_now, 2),
            "rs_momentum": None if mom is None else round(mom, 3),
            "quadrant": rotation_signal(rs_now, mom),
            "history_days": int(len(px)),
        })

    # 各視窗的名次
    for key in WINDOWS:
        ranks = rank_series({r["ticker"]: r.get("returns", {}).get(key) for r in rows})
        for r in rows:
            r.setdefault("rank", {})[key] = ranks.get(r["ticker"])

    return rows, {k: (None if v is None else round(v, 3)) for k, v in bench_returns.items()}


def breadth(rows: list[dict], key: str = "1d") -> dict:
    """多少類股上漲——單一指數漲跌看不出「是全面上漲還是少數幾檔撐著」。"""
    vals = [r["returns"][key] for r in rows
            if r.get("returns", {}).get(key) is not None]
    if not vals:
        return {"up": None, "down": None, "total": 0}
    up = sum(1 for v in vals if v > 0)
    return {"up": up, "down": len(vals) - up, "total": len(vals),
            "median_pct": round(float(np.median(vals)), 3)}


def rotation_changes(rows: list[dict], previous_ranks: dict, key: str = "1w") -> list[dict]:
    """名次變動——輪動的直接證據。沒有前一次紀錄時回空清單，不假裝有變動。"""
    out = []
    for r in rows:
        now = r.get("rank", {}).get(key)
        before = previous_ranks.get(r["ticker"])
        if now is None or before is None:
            continue
        out.append({"ticker": r["ticker"], "sector_zh": r["sector_zh"],
                    "rank_now": now, "rank_before": int(before),
                    "change": int(before) - now})
    return sorted(out, key=lambda d: -abs(d["change"]))
