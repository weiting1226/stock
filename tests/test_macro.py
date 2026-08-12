"""模組五（總體經濟觀察）測試。

總經儀表板最容易出的錯不是抓不到資料，而是：把水準當成變化率、
把參考期當成今天、把「還沒公布」當成「來源壞了」。這些都不會報錯，
只會產出一整排看起來很正常的錯誤數字。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from macro_monitor import indicators, report
from macro_monitor.config import SERIES, Series


def _spec(fred_id: str) -> Series:
    return next(s for s in SERIES if s.fred_id == fred_id)


def _monthly(values, start="2020-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"),
                     dtype=float)


# --- 轉換方式 ---------------------------------------------------------------

def test_index_series_are_converted_to_year_over_year():
    """CPI 原始序列是指數（314.5）。直接顯示水準等於給出一個沒有意義的數字。"""
    raw = _monthly([100.0] * 12 + [103.0])
    out = indicators.transform(raw, _spec("CPIAUCSL"))
    assert out.iloc[-1] == pytest.approx(3.0)


def test_rate_series_are_left_as_levels():
    """失業率原始序列本身就是百分比，再算一次年增率毫無意義。"""
    raw = _monthly([3.5, 3.6, 4.0])
    assert indicators.transform(raw, _spec("UNRATE")).iloc[-1] == pytest.approx(4.0)


def test_payrolls_show_the_monthly_change_not_the_level():
    """每月公布時市場看的是「這個月增加幾人」，不是總就業人數 1.6 億。"""
    raw = _monthly([150000.0, 150200.0, 150350.0])
    assert indicators.transform(raw, _spec("PAYEMS")).iloc[-1] == pytest.approx(150.0)


def test_an_unknown_transform_fails_loudly():
    """設定打錯字時要拋錯，不能默默當成水準顯示。"""
    bad = Series("X", "測試", "growth", "%", "logarithm", "M", True)
    with pytest.raises(ValueError, match="未知的轉換方式"):
        indicators.transform(_monthly([1.0, 2.0]), bad)


@pytest.mark.parametrize("spec", SERIES, ids=[s.fred_id for s in SERIES])
def test_every_series_declares_a_supported_transform(spec):
    """新增序列時漏填或填錯轉換方式，這裡就會擋下來。"""
    assert spec.transform in {"level", "yoy", "mom", "diff"}
    assert spec.category in {"growth", "labor", "inflation", "rates", "financial", "consumer"}


# --- 參考期與時滯 -----------------------------------------------------------

def test_the_reference_period_is_reported_not_the_fetch_date():
    """CPI 的 7 月數字要到 8 月中才公布。顯示成「今天的通膨」是最常見的誤讀。"""
    raw = _monthly([100.0] * 13 + [103.0])       # 最後一期 2021-02
    out = indicators.build_indicator(raw, _spec("CPIAUCSL"), as_of="2021-04-15")
    assert out["reference_date"] == "2021-02-01"
    assert out["lag_days"] == 73


def test_stale_detection_uses_each_series_own_release_cadence():
    """週頻的初領失業金與季頻的 GDP 不能用同一把尺量——
    不是誤報就是漏報。"""
    weekly = _spec("ICSA")            # 21 天門檻
    quarterly = _spec("GDPC1")        # 150 天門檻
    d = pd.Timestamp("2026-01-01")
    assert indicators.is_stale(d, weekly, "2026-03-01") is True
    assert indicators.is_stale(d, quarterly, "2026-03-01") is False


def test_a_freshly_published_series_is_not_flagged_stale():
    assert indicators.is_stale(pd.Timestamp("2026-08-01"), _spec("UNRATE"), "2026-08-12") is False


# --- 方向好壞 ---------------------------------------------------------------

def test_direction_uses_the_declared_polarity():
    """程式無從推論失業率上升是壞事，因此方向好壞由設定檔明確指定。"""
    assert indicators.direction_label(0.5, higher_is_better=False) == "惡化"   # 失業率上升
    assert indicators.direction_label(0.5, higher_is_better=True) == "改善"    # GDP 上升
    assert indicators.direction_label(-0.5, higher_is_better=False) == "改善"


def test_indicators_without_a_clear_polarity_show_no_direction():
    """通膨與殖利率沒有單一方向的好壞，硬套會誤導。"""
    assert indicators.direction_label(0.5, higher_is_better=None) == "—"
    assert _spec("CPIAUCSL").higher_is_better is None
    assert _spec("DGS10").higher_is_better is None


def test_no_change_is_not_a_direction():
    assert indicators.direction_label(0.0, higher_is_better=True) == "—"


# --- 百分位 -----------------------------------------------------------------

def test_percentile_returns_none_when_the_sample_is_too_small():
    """樣本不足時留空，不硬給一個看起來很精確的數字。"""
    assert indicators.percentile_of_latest(_monthly([1.0, 2.0, 3.0])) is None


def test_percentile_is_measured_against_the_series_own_history():
    values = _monthly(list(np.arange(1.0, 61.0)))
    assert indicators.percentile_of_latest(values) == pytest.approx(100.0, abs=2)


# --- 衰退區間 ---------------------------------------------------------------

def test_recession_bands_are_paired_start_to_end():
    s = pd.Series([0, 1, 1, 0, 0, 1, 0],
                  index=pd.date_range("2020-01-01", periods=7, freq="MS"))
    bands = indicators.recession_bands(s)
    assert len(bands) == 2
    assert bands[0]["start"] == "2020-02-01" and bands[0]["end"] == "2020-04-01"


def test_an_unfinished_recession_still_produces_a_band():
    """NBER 尚未宣告結束時，區間要延伸到資料末端，不能整段消失。"""
    s = pd.Series([0, 1, 1], index=pd.date_range("2020-01-01", periods=3, freq="MS"))
    assert len(indicators.recession_bands(s)) == 1


def test_no_recession_data_is_an_empty_list_not_an_error():
    assert indicators.recession_bands(pd.Series(dtype=float)) == []


# --- 報告組裝 ---------------------------------------------------------------

def test_report_separates_missing_from_stale():
    """「這個月還沒公布」與「來源壞了」在畫面上都是沒有新數字，
    但處置完全不同。"""
    data = {s.fred_id: _monthly([1.0] * 40) for s in SERIES}
    data["UNRATE"] = pd.Series(dtype=float)                       # 缺失
    data["ICSA"] = _monthly([200.0] * 40, start="2015-01-01")     # 過期
    rep = report.build_report(as_of="2026-08-12", data=data)
    assert "UNRATE" in {r["fred_id"] for r in rep["missing_series"]}
    assert "ICSA" in {r["fred_id"] for r in rep["stale_series"]}
    assert rep["counts"]["available"] == len(SERIES) - 1


def test_report_is_json_serialisable():
    """NaN 漏掉的話前端解析失敗，畫面只是一片空白。"""
    import json
    data = {s.fred_id: _monthly([1.0, 2.0, np.nan] * 15) for s in SERIES}
    text = json.dumps(report.build_report(as_of="2026-08-12", data=data), ensure_ascii=False)
    assert "NaN" not in text and "Infinity" not in text


def test_every_category_appears_in_the_summary():
    data = {s.fred_id: _monthly([1.0] * 40) for s in SERIES}
    rep = report.build_report(as_of="2026-08-12", data=data)
    assert set(rep["summary"]) == set(rep["categories"])
    assert sum(v["count"] for v in rep["summary"].values()) == len(SERIES)
