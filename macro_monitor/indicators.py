"""把原始 FRED 序列轉成儀表板要的數字（純函式，無網路存取）。

三件事必須分清楚，否則整排數字看起來正常但意義錯了：

1. **參考期 ≠ 發布日 ≠ 今天。** CPI 的 7 月數字在 8 月中才公布。畫面上要顯示
   的是「這是哪一個月的數字」，而不是「我今天抓到它」。
2. **水準 vs 變化。** CPI 原始序列是指數（314.5），要看年增率；失業率原始
   序列本身就是百分比。用錯轉換不會報錯，只會給出一整排錯的數字。
3. **「還沒公布」 vs 「來源壞了」。** 兩者在畫面上都是「沒有新數字」，
   但處置完全不同。用各序列自己的更新頻率判定。
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from .config import PERCENTILE_YEARS, Series

log = logging.getLogger(__name__)

# 各頻率一年約有幾筆——算百分位視窗與趨勢用
OBS_PER_YEAR = {"D": 252, "W": 52, "M": 12, "Q": 4}


def transform(series: pd.Series, spec: Series) -> pd.Series:
    """依設定把原始序列轉成要呈現的量。"""
    s = series.dropna().sort_index()
    if s.empty:
        return s
    if spec.transform == "level":
        return s
    if spec.transform == "yoy":
        periods = OBS_PER_YEAR.get(spec.freq, 12)
        prior = s.shift(periods)
        return ((s / prior.where(prior != 0) - 1) * 100).dropna()
    if spec.transform == "mom":
        prior = s.shift(1)
        return ((s / prior.where(prior != 0) - 1) * 100).dropna()
    if spec.transform == "diff":
        # 非農就業要看的是「這個月增加幾人」，不是總就業人數
        return s.diff().dropna()
    raise ValueError(f"未知的轉換方式 {spec.transform}（{spec.fred_id}）")


def percentile_of_latest(values: pd.Series, years: int = PERCENTILE_YEARS,
                         freq: str = "M") -> Optional[float]:
    """最新值在過去 N 年分布中的百分位。樣本不足回 None，不硬給數字。"""
    n = years * OBS_PER_YEAR.get(freq, 12)
    hist = values.dropna().tail(n)
    if len(hist) < 20:
        return None
    return round(float(100.0 * (hist < hist.iloc[-1]).sum() / len(hist)), 1)


def change_over(values: pd.Series, periods: int) -> Optional[float]:
    """相對 N 期前的絕對變化（原單位）。資料不足回 None。"""
    s = values.dropna()
    if len(s) <= periods:
        return None
    return float(s.iloc[-1] - s.iloc[-1 - periods])


def is_stale(last_date, spec: Series, as_of: str) -> bool:
    """這條序列是不是太久沒更新了。

    門檻用各序列自己的發布節奏（設定在 stale_after_days）。用單一門檻的話，
    週頻的初領失業金與季頻的 GDP 會被同一把尺量——不是誤報就是漏報。
    """
    if not spec.stale_after_days or last_date is None:
        return False
    return (pd.Timestamp(as_of) - pd.Timestamp(last_date)).days > spec.stale_after_days


def direction_label(delta: Optional[float], higher_is_better: Optional[bool]) -> str:
    """把變化方向翻成「改善／惡化」。

    程式無從推論失業率上升是壞事，因此方向好壞由設定檔明確指定；
    沒有單一方向好壞的（如通膨、殖利率）一律回「—」，不硬套。
    """
    if delta is None or higher_is_better is None or abs(delta) < 1e-12:
        return "—"
    improving = (delta > 0) == higher_is_better
    return "改善" if improving else "惡化"


def build_indicator(raw: pd.Series, spec: Series, as_of: str) -> dict:
    """單一指標的完整結果。"""
    if raw is None or raw.dropna().empty:
        return {"fred_id": spec.fred_id, "label": spec.label, "category": spec.category,
                "available": False, "error": "無資料"}

    values = transform(raw, spec)
    if values.empty:
        return {"fred_id": spec.fred_id, "label": spec.label, "category": spec.category,
                "available": False, "error": f"轉換後無資料（{spec.transform}）"}

    per_year = OBS_PER_YEAR.get(spec.freq, 12)
    latest_date = values.index[-1]
    # 觀察期長度依頻率換算，讓「三個月變化」對日頻與月頻都是三個月
    three_m = max(1, round(per_year / 4))
    twelve_m = per_year

    d3 = change_over(values, three_m)
    return {
        "fred_id": spec.fred_id,
        "label": spec.label,
        "category": spec.category,
        "unit": spec.unit,
        "transform": spec.transform,
        "freq": spec.freq,
        "available": True,
        "value": round(float(values.iloc[-1]), 3),
        # 參考期：這是哪一期的數字，不是我今天抓到它
        "reference_date": str(latest_date.date()),
        "lag_days": int((pd.Timestamp(as_of) - latest_date).days),
        "stale": is_stale(latest_date, spec, as_of),
        "change_3m": None if d3 is None else round(d3, 3),
        "change_12m": (lambda v: None if v is None else round(v, 3))(change_over(values, twelve_m)),
        "direction_3m": direction_label(d3, spec.higher_is_better),
        "percentile_20y": percentile_of_latest(values, freq=spec.freq),
        "higher_is_better": spec.higher_is_better,
        "note": spec.note,
    }


def sparkline(values: pd.Series, points: int = 60, freq: str = "M") -> dict:
    """縮圖用的近期序列。日頻取樣到約每週一點，否則 60 點只涵蓋三個月。"""
    s = values.dropna()
    if s.empty:
        return {"dates": [], "values": []}
    step = 5 if freq == "D" else 1
    s = s.iloc[::-1].iloc[::step].iloc[:points].iloc[::-1]
    return {"dates": [str(d.date()) for d in s.index],
            "values": [round(float(v), 3) for v in s]}


def recession_bands(usrec: pd.Series) -> list[dict]:
    """把 NBER 衰退指標轉成 [{start, end}] 區間，供圖表畫陰影。

    最近期不會有標記——NBER 的認定往往在衰退開始後一年才公布。
    那是「還沒認定」，不是「沒有衰退」，畫面上要講清楚。
    """
    if usrec is None or usrec.dropna().empty:
        return []
    s = usrec.dropna().astype(int)
    bands, start = [], None
    for date, v in s.items():
        if v == 1 and start is None:
            start = date
        elif v == 0 and start is not None:
            bands.append({"start": str(start.date()), "end": str(date.date())})
            start = None
    if start is not None:
        bands.append({"start": str(start.date()), "end": str(s.index[-1].date())})
    return bands
