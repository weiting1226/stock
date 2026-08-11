"""組合模擬：目標權重 -> 每日報酬 -> 淨值曲線。

兩件事決定這個回測可不可信：

1. **訊號與成交的時間差。** 分數由當日收盤資料算出，最快只能在下一個
   交易日成交。用當日收盤價成交等於拿當天收盤才知道的資訊去買當天的
   收盤價——這種回測一定很漂亮，而且完全不能複製。
2. **換手成本。** 策略在階梯之間切換，成本會累積。報告同時給零成本與
   含成本兩個版本；若兩者差距大到會改變結論，那個結論本來就不成立。
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .config import COST_BPS_PER_SIDE, EXECUTION_LAG_DAYS, TRADING_DAYS_PER_YEAR

log = logging.getLogger(__name__)

ASSETS = ("QQQ", "TQQQ", "SGOV")


def daily_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """由收盤價算每日報酬率。第一天沒有前一日，必為 NaN -> 補 0。"""
    return prices.pct_change().fillna(0.0)


def shift_to_executable(weights: pd.DataFrame, lag_days: int = EXECUTION_LAG_DAYS) -> pd.DataFrame:
    """把目標權重往後移，代表「今天算出的訊號，明天才生效」。

    這一行就是整個回測最重要的一行。移位之後，第 t 天的持倉是用第 t-1 天
    收盤的資訊決定的，第 t 天的報酬才是可實現的。
    """
    return weights.shift(lag_days)


def _rebalance_only_on_change(weights: pd.DataFrame) -> pd.DataFrame:
    """只在目標配置真的改變時才換倉。

    策略本身是離散的（五個分級），每天照權重微調會產生大量無意義的換手，
    把成本灌爆而且不符合這套規則的實際操作方式。
    """
    changed = (weights != weights.shift()).any(axis=1)
    return weights.where(changed).ffill()


def simulate(
    weights: pd.DataFrame,
    prices: pd.DataFrame,
    cost_bps_per_side: float = COST_BPS_PER_SIDE,
    rebalance_on_change_only: bool = True,
) -> pd.DataFrame:
    """回傳含每日報酬、淨值、持倉與換手的 DataFrame。

    `weights` 為**訊號日**的目標權重（尚未位移），`prices` 為各標的收盤價。
    """
    assets = [a for a in ASSETS if a in prices.columns]
    w = weights.reindex(prices.index)[assets].astype(float)

    if rebalance_on_change_only:
        w = _rebalance_only_on_change(w)

    held = shift_to_executable(w)
    # 訊號還沒出現的期間不持有任何部位（不是「持有現金賺利息」，是還沒開始）
    held = held.dropna(how="all")
    rets = daily_returns(prices).reindex(held.index)[assets]

    gross = (held.fillna(0.0) * rets).sum(axis=1)

    # 換手：相鄰兩天持倉的絕對差總和的一半＝單邊換手率
    # （一次換倉是「賣掉 X、買進 X」，sum|Δw| = 2X，單邊才是 X）
    held0 = held.fillna(0.0)
    turnover = (held0 - held0.shift().fillna(0.0)).abs().sum(axis=1) / 2
    # 第一天例外：前一天根本沒有部位，是純買進而不是換倉，不能只算一半。
    # 影響很小（一次性），但除以二會讓建倉成本平白少算一半。
    if len(turnover):
        turnover.iloc[0] = float(held0.iloc[0].abs().sum())
    cost = turnover * (cost_bps_per_side / 10_000.0)
    net = gross - cost

    out = pd.DataFrame({
        "gross_return": gross,
        "cost": cost,
        "net_return": net,
        "turnover": turnover,
    }, index=held.index)
    out["equity_gross"] = (1 + gross).cumprod()
    out["equity"] = (1 + net).cumprod()
    for a in assets:
        out[f"w_{a}"] = held[a].fillna(0.0)
    return out


def buy_and_hold(prices: pd.Series) -> pd.DataFrame:
    """單一標的買進持有，作為對照組。"""
    rets = prices.pct_change().fillna(0.0)
    return pd.DataFrame({"net_return": rets, "equity": (1 + rets).cumprod()}, index=prices.index)
