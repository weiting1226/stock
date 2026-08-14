"""趨勢指標。全部是可查證的事實，沒有自創分數。

**除息調整在這裡不是細節。** 公用事業與必需消費的殖利率約 3%，資訊科技不到 1%。
用未調整價比較一年報酬，等於系統性低估防禦類股約兩個百分點——而畫面上完全
看不出來，只會得出「防禦類股長期就是比較弱」這種被股利吃掉的結論。

模組四用的是未調整價（三個月內影響有限），本模組拉到一年與三年，
所以一律用調整價。兩邊的短期數字會有些微差異，這是預期中的，
不是其中一邊算錯——README 有說明。
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from .config import MA_WINDOWS, RETURN_WINDOWS

log = logging.getLogger(__name__)


def fetch_adjusted(tickers: list, start: str, end: str) -> tuple[pd.DataFrame, list]:
    """批次抓**除息調整後**的收盤價。回傳 (寬表, 失敗清單)。

    `auto_adjust=True` 讓 yfinance 直接回傳調整後的 Close。
    個別 ticker 失敗不中斷整批，但要留下清單——少一檔而畫面不說，
    那一格會靜靜消失。
    """
    df = yf.download(list(tickers), start=start, end=end, progress=False,
                     auto_adjust=True, threads=True, group_by="ticker")
    if df is None or df.empty:
        raise ValueError("Yahoo Finance 批次下載回傳空資料")

    out, missing = {}, []
    for t in tickers:
        try:
            col = df[t]["Close"] if isinstance(df.columns, pd.MultiIndex) else df["Close"]
        except (KeyError, TypeError):
            missing.append(t)
            continue
        col = col.dropna()
        if col.empty:
            missing.append(t)
            continue
        out[t] = col

    if not out:
        raise ValueError("所有 ticker 都沒有取得資料")
    wide = pd.DataFrame(out)
    wide.index = pd.to_datetime(wide.index).tz_localize(None)
    return wide.sort_index(), missing


def _pct_change_over(s: pd.Series, days: int) -> Optional[float]:
    s = s.dropna()
    if len(s) <= days:
        return None
    return float(s.iloc[-1] / s.iloc[-days - 1] - 1.0) * 100.0


def moving_average_state(s: pd.Series) -> dict:
    """收盤價相對 50/200 日均線的位置，以及最近一次穿越距今幾個交易日。

    「距今幾天」比「現在在上面還是下面」有用得多：剛穿越與站上兩年，
    在畫面上長得一樣，但意義完全不同。
    """
    s = s.dropna()
    out: dict = {}
    for w in MA_WINDOWS:
        if len(s) < w:
            out[f"ma{w}"] = None
            out[f"above_ma{w}"] = None
            continue
        ma = s.rolling(w).mean()
        out[f"ma{w}"] = round(float(ma.iloc[-1]), 4)
        out[f"above_ma{w}"] = bool(s.iloc[-1] > ma.iloc[-1])

    short, long = MA_WINDOWS
    if len(s) >= long:
        ma_s, ma_l = s.rolling(short).mean(), s.rolling(long).mean()
        pair = pd.concat([ma_s, ma_l], axis=1).dropna()
        if len(pair) >= 2:
            above = pair.iloc[:, 0] > pair.iloc[:, 1]
            out["golden_cross"] = bool(above.iloc[-1])
            # 從最後一次狀態改變算起
            changed = above != above.shift(1)
            idx = np.where(changed.values)[0]
            last = idx[-1] if len(idx) and idx[-1] != 0 else None
            out["days_since_cross"] = (None if last is None
                                       else int(len(above) - 1 - last))
    return out


def drawdown_stats(s: pd.Series, window: int = 252) -> dict:
    """距 52 週高點的回撤，以及距 52 週低點的漲幅。

    兩個一起看才完整：離高點 -18% 可能是「剛開始跌」，也可能是
    「從谷底漲了 40% 但還沒回到前高」。
    """
    s = s.dropna().iloc[-window:]
    if s.empty:
        return {}
    last = float(s.iloc[-1])
    hi, lo = float(s.max()), float(s.min())
    return {
        "high_52w": round(hi, 4),
        "low_52w": round(lo, 4),
        "from_high_pct": round((last / hi - 1.0) * 100.0, 2) if hi > 0 else None,
        "from_low_pct": round((last / lo - 1.0) * 100.0, 2) if lo > 0 else None,
    }


def build_row(ticker: str, s: pd.Series, benchmark: Optional[pd.Series] = None) -> dict:
    s = s.dropna()
    row = {
        "ticker": ticker,
        "close": round(float(s.iloc[-1]), 4),
        "history_days": int(len(s)),
        "returns": {k: (None if (v := _pct_change_over(s, d)) is None else round(v, 2))
                    for k, d in RETURN_WINDOWS.items()},
    }
    row.update(moving_average_state(s))
    row.update(drawdown_stats(s))

    if benchmark is not None:
        b = benchmark.dropna()
        row["excess"] = {}
        for k, d in RETURN_WINDOWS.items():
            a, c = _pct_change_over(s, d), _pct_change_over(b, d)
            row["excess"][k] = None if a is None or c is None else round(a - c, 2)
    return row


def breadth(rows: list, primary_tickers: list) -> dict:
    """代表性 ETF 裡有幾檔站上 200 日均線。

    只用固定的代表 ETF 計算——把所有 ETF 都算進去的話，
    「11 檔裡有幾檔」會隨我今天列了幾檔科技 ETF 而變動，那不是廣度，是取樣。
    """
    sel = [r for r in rows if r["ticker"] in primary_tickers]
    counted = [r for r in sel if r.get("above_ma200") is not None]
    if not counted:
        return {"above_ma200": None, "total": 0}
    n = sum(1 for r in counted if r["above_ma200"])
    above50 = [r for r in counted if r.get("above_ma50")]
    return {
        "above_ma200": n,
        "above_ma50": len(above50),
        "total": len(counted),
        "pct_above_ma200": round(n / len(counted) * 100.0, 1),
    }


def family_dispersion(family_rows: list, window: str = "1y") -> Optional[float]:
    """同一類股不同 ETF 的報酬差距（最大 − 最小，百分點）。

    這是本模組相對模組四多出來的那件事：「科技類股今年漲多少」其實取決於
    你拿哪一檔——追的指數不同，成分與權重就不同。差距大的時候，
    用單一 ETF 代表整個類股會失真。
    """
    vals = [r["returns"].get(window) for r in family_rows
            if r.get("returns", {}).get(window) is not None]
    if len(vals) < 2:
        return None
    return round(max(vals) - min(vals), 2)


def rebased_series(s: pd.Series, points: int) -> dict:
    """趨勢圖用：取樣並重新基準化到 100。"""
    s = s.dropna().iloc[-points:]
    if s.empty:
        return {"dates": [], "values": []}
    base = float(s.iloc[0])
    step = max(1, len(s) // 130)
    sampled = s.iloc[::step]
    return {"dates": [str(d.date()) for d in sampled.index],
            "values": [round(float(v) / base * 100.0, 3) for v in sampled]}
