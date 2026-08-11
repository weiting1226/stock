"""把歷史原始序列轉成「每個交易日的指標數值」面板。

線上流程一次只算**當天**一個數字（各序列取 `.iloc[-1]`）。回測需要每一天
都有，因此這裡是同一批定義的向量化版本。定義必須與 `liquidity_monitor.pipeline`
一致，否則回測的是另一套策略。

**這個檔案的全部重點是前視偏誤。** 每一個數值都只能用「那一天真的已經
公開」的資訊算出來：

1. 月頻／週頻序列先套用發布時滯（`apply_publish_lag`），再前向填補到每個
   交易日——同線上流程。
2. 所有變化量都用 `shift(n)` 取自身過去，不碰未來。
3. 百分位一律用 `rolling(...)`，不是對整段歷史算一次。用全期分布去評
   2013 年的那一天，等於讓 2013 年的判斷用到 2020 年才發生的事。
"""
from __future__ import annotations

import logging

import pandas as pd

from liquidity_monitor.config import FINRA_PUBLISH_LAG_DAYS, FRED_SERIES
from liquidity_monitor.timeseries import apply_publish_lag, to_daily_panel, yoy

log = logging.getLogger(__name__)

# 與 pipeline 相同的視窗長度（交易日）
FED_BS_WINDOW = 63       # 約 3 個月
M2_WINDOW = 63
HY_OAS_WINDOW = 20
DXY_WINDOW = 21
USDJPY_WINDOW = 20
RRP_PCTILE_WINDOW = 252  # 12 個月
RRP_DORMANT_USD = 100e9
RRP_MIN_OBS = 60


def _daily(raw: pd.Series, lag_days: int, trading_days: pd.DatetimeIndex) -> pd.Series:
    """套用發布時滯後對齊到每個交易日。"""
    if raw is None or raw.empty:
        return pd.Series(index=trading_days, dtype=float)
    return to_daily_panel(apply_publish_lag(raw.sort_index(), lag_days), trading_days)


def _lagged_yoy_daily(raw: pd.Series, lag_days: int, trading_days: pd.DatetimeIndex) -> pd.Series:
    """先算年增率、再套時滯、最後對齊——順序與 pipeline._lagged_yoy_daily 相同。

    順序不能顛倒：年增率要從原始（未位移）序列算，否則等於拿位移後的日期
    去對位移後的日期；但算完一定要套時滯，否則當天就看得到還沒公布的數字。
    """
    if raw is None or raw.empty:
        return pd.Series(index=trading_days, dtype=float)
    return to_daily_panel(apply_publish_lag(yoy(raw), lag_days), trading_days)


def _diff(s: pd.Series, n: int, scale: float = 1.0) -> pd.Series:
    return (s - s.shift(n)) * scale


def _pct_change(s: pd.Series, n: int) -> pd.Series:
    prior = s.shift(n)
    return (s / prior.where(prior != 0) - 1) * 100


def rolling_percentile(s: pd.Series, window: int, min_obs: int) -> pd.Series:
    """每一天在自己**過去** `window` 天分布中的百分位（0-100）。

    含當日本身，與線上 `gates.percentile_rank` 的算法一致
    （它取 `tail(window)` 也包含最新值）。關鍵是視窗只往回看。
    """
    def _rank(window_values):
        current = window_values[-1]
        return 100.0 * (window_values[:-1] < current).sum() / len(window_values)

    return s.rolling(window, min_periods=min_obs).apply(_rank, raw=True)


def build_indicator_panel(
    fred: dict[str, pd.Series],
    yahoo: dict[str, pd.Series],
    margin_debt: pd.Series,
    trading_days: pd.DatetimeIndex,
) -> pd.DataFrame:
    """回傳 index=交易日、每欄一個指標原始值的 DataFrame。

    `fred` 以 FRED series id 為鍵、`yahoo` 以 config.YAHOO_TICKERS 的名稱為鍵，
    值都是**未套用時滯**的原始序列（時滯在這裡統一處理，來源層不該先動手腳）。
    """
    def fred_daily(sid: str) -> pd.Series:
        return _daily(fred.get(sid), FRED_SERIES.get(sid, 0), trading_days)

    def px(name: str) -> pd.Series:
        # 市場價格沒有發布時滯：收盤價當天收盤就知道了
        return to_daily_panel(yahoo.get(name, pd.Series(dtype=float)), trading_days)

    out = pd.DataFrame(index=trading_days)

    # ① 總體貨幣流動性 ------------------------------------------------------
    walcl_yoy = _lagged_yoy_daily(fred.get("WALCL"), FRED_SERIES["WALCL"], trading_days)
    out["fed_bs_3m_chg"] = _diff(walcl_yoy, FED_BS_WINDOW)

    m2_yoy = _lagged_yoy_daily(fred.get("M2SL"), FRED_SERIES["M2SL"], trading_days)
    out["m2_yoy_3m_chg"] = _diff(m2_yoy, M2_WINDOW)

    rrp = fred_daily("RRPONTSYD")
    out["on_rrp_pctile"] = rolling_percentile(rrp, RRP_PCTILE_WINDOW, RRP_MIN_OBS)
    # 「結構性休眠」是計分的例外條款，必須逐日判定：2021-22 年 ON RRP 高達
    # 兩兆，2025 年後才歸零。用單一旗標套用到整段歷史會兩邊都錯。
    out["on_rrp_dormant"] = (
        rrp.rolling(RRP_PCTILE_WINDOW, min_periods=RRP_MIN_OBS).max() < RRP_DORMANT_USD
    )

    # ② 資金成本與信用 ------------------------------------------------------
    hy = fred_daily("BAMLH0A0HYM2")
    out["hy_oas_level"] = hy
    out["hy_oas_20d_chg"] = _diff(hy, HY_OAS_WINDOW, scale=100)      # % -> bp

    out["t2s10s_bp"] = (fred_daily("DGS10") - fred_daily("DGS2")) * 100
    out["sofr_iorb_bp"] = (fred_daily("SOFR") - fred_daily("IORB")) * 100

    # ③ 市場微觀結構 -------------------------------------------------------
    out["move_index"] = px("move")
    # ndx_breadth_200d 沒有可回溯的歷史（需要當時的成分股名單），見 config

    # ④ 風險偏好情緒 -------------------------------------------------------
    out["vix_level"] = px("vix")
    vix3m = px("vix3m")
    out["ivts"] = px("vix") / vix3m.where(vix3m > 0)

    margin_yoy = _lagged_yoy_daily(margin_debt, FINRA_PUBLISH_LAG_DAYS, trading_days)
    out["margin_debt_yoy"] = margin_yoy

    # ⑤ 跨資產資金流向 -----------------------------------------------------
    out["dxy_1m_chg"] = _pct_change(px("dxy"), DXY_WINDOW)
    out["usdjpy_20d_chg"] = _pct_change(px("usdjpy"), USDJPY_WINDOW)
    # etf_fund_flow 沒有回溯資料，見 config

    # ⑥ 政策方向：依使用者要求排除，不計算

    # 閘門與圖表用的附帶序列
    out["ndx_close"] = px("ndx")
    out["skew"] = px("skew")

    return out
