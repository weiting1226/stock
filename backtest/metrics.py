"""績效指標。全部由每日報酬序列算出，不做任何年化以外的加工。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import TRADING_DAYS_PER_YEAR


def _years(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 0.0
    return (index[-1] - index[0]).days / 365.25


def max_drawdown(equity: pd.Series) -> float:
    """最大回撤（負值，%）。槓桿策略看不到這個數字就等於沒看。"""
    if equity.empty:
        return float("nan")
    peak = equity.cummax()
    return float(((equity / peak - 1).min()) * 100)


def summarize(returns: pd.Series, equity: pd.Series, rf_returns: pd.Series = None) -> dict:
    """年化報酬、波動、Sharpe、最大回撤、勝率。

    Sharpe 用真實的現金報酬當無風險利率（回測期間橫跨零利率與 5% 兩種環境，
    用固定的 0% 或 2% 都會嚴重誤導）。拿不到現金序列時回報 None 而不是
    偷偷用 0——那會讓 2022-2025 年的 Sharpe 看起來好很多。
    """
    r = returns.dropna()
    if r.empty or equity.empty:
        return {}
    years = _years(r.index)
    total = float(equity.iloc[-1])
    cagr = (total ** (1 / years) - 1) * 100 if years > 0 and total > 0 else float("nan")
    vol = float(r.std() * np.sqrt(TRADING_DAYS_PER_YEAR) * 100)

    sharpe = None
    if rf_returns is not None and not rf_returns.dropna().empty:
        excess = (r - rf_returns.reindex(r.index).fillna(0.0)).dropna()
        if excess.std() > 0:
            sharpe = float(excess.mean() / excess.std() * np.sqrt(TRADING_DAYS_PER_YEAR))

    # NaN 一路傳到 JSON 會變成無效的 `NaN` 字面值，前端解析失敗後畫面只是
    # 一片空白——看起來像「還沒產生資料」，而不是「這個數字算不出來」。
    def num(v, digits=2):
        if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
            return None
        return round(float(v), digits)

    return {
        "total_return_pct": num((total - 1) * 100),
        "cagr_pct": num(cagr),
        "volatility_pct": num(vol),
        "sharpe": num(sharpe, 3),
        "max_drawdown_pct": num(max_drawdown(equity)),
        "win_rate_pct": num(float((r > 0).mean() * 100)),
        "years": num(years),
        "days": int(len(r)),
    }
