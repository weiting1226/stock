"""單一指標的深入分析（純函式，無網路存取）。

原則：**只做資料本身說得出來的判斷，不發明分數。**

每一項分析都要能回答「這個數字現在的位置，跟歷史比起來意味著什麼」，
而且判斷依據必須看得見。因此這裡產出的都是可查證的量——z 分數、距離
極值多遠、上次轉折在什麼時候、衰退期與擴張期的分布差異——而不是把它們
壓成一個「總經健康度 72 分」。

衰退期對照是這裡最有價值的一項：同一個失業率 4.1%，在擴張期的分布裡與在
衰退期的分布裡意義完全不同。把兩組分布擺出來，比任何自訂門檻都誠實。
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# 局部極值的判定視窗（期數）。太小會把雜訊當轉折，太大會漏掉真的轉折。
TURNING_POINT_WINDOW = 6


def _clean(v):
    if v is None:
        return None
    f = float(v)
    return None if (np.isnan(f) or np.isinf(f)) else round(f, 3)


def zscore(values: pd.Series, window: Optional[int] = None) -> Optional[float]:
    """最新值的標準化分數。

    與百分位互補：百分位說「排在多少名」，z 分數說「離平均幾個標準差」。
    分布有厚尾時兩者會給出很不一樣的印象，一起看才完整。
    """
    s = values.dropna()
    if window:
        s = s.tail(window)
    if len(s) < 20:
        return None
    sd = float(s.std())
    if sd == 0:
        return None
    return _clean((s.iloc[-1] - s.mean()) / sd)


def extremes(values: pd.Series) -> dict:
    """歷史高低點、發生時間，以及目前距離它們多遠。"""
    s = values.dropna()
    if len(s) < 10:
        return {}
    hi, lo = float(s.max()), float(s.min())
    latest = float(s.iloc[-1])
    span = hi - lo
    return {
        "high": _clean(hi), "high_date": str(s.idxmax().date()),
        "low": _clean(lo), "low_date": str(s.idxmin().date()),
        # 目前值落在歷史區間的哪個位置（0 = 歷史低點，100 = 歷史高點）
        "position_in_range_pct": _clean(100 * (latest - lo) / span) if span else None,
        "vs_high_pct": _clean(100 * (latest - hi) / abs(hi)) if hi else None,
    }


def acceleration(values: pd.Series, periods: int) -> Optional[float]:
    """變化的變化：這一段的變化量，減去前一段的變化量。

    水準在上升、但上升的速度在放慢——這是轉折最早的跡象之一，
    只看「三個月變化」看不出來。
    """
    s = values.dropna()
    if len(s) <= 2 * periods:
        return None
    recent = s.iloc[-1] - s.iloc[-1 - periods]
    prior = s.iloc[-1 - periods] - s.iloc[-1 - 2 * periods]
    return _clean(recent - prior)


def last_turning_point(values: pd.Series, window: int = TURNING_POINT_WINDOW) -> dict:
    """上一次局部高點／低點在什麼時候，距今幾期。

    用滾動視窗的極值判定：某一期是它前後各 `window` 期裡的最大（或最小）值。
    因此**最近 `window` 期無法判定**——那不是漏掉，是還沒有足夠的後續資料
    確認它是不是轉折點。硬要判會產生一堆事後被推翻的假轉折。
    """
    s = values.dropna()
    if len(s) < 2 * window + 1:
        return {}
    body = s.iloc[:-window] if window else s
    peaks, troughs = [], []
    for i in range(window, len(body)):
        seg = s.iloc[i - window: i + window + 1]
        if len(seg) < window + 1:
            continue
        if s.iloc[i] == seg.max():
            peaks.append(s.index[i])
        if s.iloc[i] == seg.min():
            troughs.append(s.index[i])

    out = {"confirmation_lag_periods": window}
    if peaks:
        out["last_peak_date"] = str(peaks[-1].date())
        out["periods_since_peak"] = int(len(s) - 1 - s.index.get_loc(peaks[-1]))
    if troughs:
        out["last_trough_date"] = str(troughs[-1].date())
        out["periods_since_trough"] = int(len(s) - 1 - s.index.get_loc(troughs[-1]))
    return out


def recession_contrast(values: pd.Series, usrec: pd.Series) -> dict:
    """把這條指標在衰退期與擴張期的分布拆開比較。

    同一個失業率 4.1%，在擴張期的分布裡與在衰退期的分布裡意義完全不同。
    把兩組分布擺出來，比任何自訂門檻都誠實——讀者自己就看得出目前值
    比較靠近哪一邊，而不必相信一個來歷不明的分數。
    """
    if usrec is None or usrec.dropna().empty:
        return {}
    s = values.dropna()
    flag = usrec.dropna().reindex(s.index, method="ffill")
    both = pd.concat([s.rename("v"), flag.rename("r")], axis=1).dropna()
    if len(both) < 40:
        return {}
    rec = both.loc[both["r"] == 1, "v"]
    exp = both.loc[both["r"] == 0, "v"]
    if len(rec) < 10 or len(exp) < 10:
        return {}

    latest = float(s.iloc[-1])
    rec_mean, exp_mean = float(rec.mean()), float(exp.mean())
    # 目前值落在「擴張期均值」與「衰退期均值」之間的哪個位置。
    # 0% = 完全在擴張期的水準，100% = 完全在衰退期的水準。
    gap = rec_mean - exp_mean
    toward = 100 * (latest - exp_mean) / gap if gap else None
    return {
        "recession_months": int(len(rec)),
        "expansion_months": int(len(exp)),
        "recession_mean": _clean(rec_mean),
        "expansion_mean": _clean(exp_mean),
        "toward_recession_pct": _clean(max(-50, min(150, toward))) if toward is not None else None,
        "note": ("0% = 目前值等於擴張期平均，100% = 等於衰退期平均。"
                 "這是分布位置，不是衰退機率——單一指標算不出機率。"),
    }


def volatility_regime(values: pd.Series, window: int = 24) -> dict:
    """近期波動相對長期波動。波動放大往往先於水準的轉折出現。"""
    s = values.dropna()
    if len(s) < window * 2:
        return {}
    d = s.diff().dropna()
    recent = float(d.tail(window).std())
    longrun = float(d.std())
    if longrun == 0:
        return {}
    return {"recent_vol": _clean(recent), "long_run_vol": _clean(longrun),
            "vol_ratio": _clean(recent / longrun)}


def analyse(values: pd.Series, usrec: pd.Series, freq: str) -> dict:
    """單一指標的完整分析。資料不足的項目留空，不硬給數字。"""
    per_year = {"D": 252, "W": 52, "M": 12, "Q": 4}.get(freq, 12)
    quarter = max(1, round(per_year / 4))

    out = {
        "zscore_full": zscore(values),
        "zscore_10y": zscore(values, window=per_year * 10),
        "acceleration_3m": acceleration(values, quarter),
        "extremes": extremes(values),
        "turning_point": last_turning_point(values),
        "volatility": volatility_regime(values, window=max(8, quarter * 2)),
        "recession_contrast": recession_contrast(values, usrec),
        "observations": int(len(values.dropna())),
        "history_start": str(values.dropna().index.min().date()) if not values.dropna().empty else None,
    }
    return {k: v for k, v in out.items() if v not in ({}, None) or k == "observations"}
