"""模組五（總體經濟觀察）測試。

總經儀表板最容易出的錯不是抓不到資料，而是：把水準當成變化率、
把參考期當成今天、把「還沒公布」當成「來源壞了」。這些都不會報錯，
只會產出一整排看起來很正常的錯誤數字。
"""
from __future__ import annotations

import json
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
    d = pd.Timestamp("2026-01-01")
    assert indicators.is_stale(d, _spec("ICSA"), "2026-03-01") is True
    assert indicators.is_stale(d, _spec("GDPC1"), "2026-03-01") is False


@pytest.mark.parametrize("spec", [s for s in SERIES if s.publish_lag_days],
                         ids=[s.fred_id for s in SERIES if s.publish_lag_days])
def test_stale_threshold_exceeds_the_normal_maximum_lag(spec):
    """落後天數在兩次發布之間會持續增加，門檻必須大於「發布時滯＋發布間隔」。

    第一版直接把發布時滯當門檻（月頻 70 天），結果 7 條指標在發布週期尾聲
    被誤標為過期——例如 2026-08-12 當天，CPI 最新的仍是 6 月資料、落後 72 天，
    而那完全正常（7 月資料要到 08-13 才公布）。
    """
    from macro_monitor.config import RELEASE_INTERVAL_DAYS
    normal_max = spec.publish_lag_days + RELEASE_INTERVAL_DAYS[spec.freq]
    assert spec.stale_after_days > normal_max


def test_a_monthly_series_right_before_the_next_release_is_not_stale():
    """實測案例：2026-08-12 的 CPI，最新參考期 2026-06-01、落後 72 天。
    下一次發布（7 月資料）是 08-13，所以這是正常狀態，不是過期。"""
    assert indicators.is_stale(pd.Timestamp("2026-06-01"), _spec("CPIAUCSL"),
                               "2026-08-12") is False


def test_a_monthly_series_that_missed_a_release_is_stale():
    """真的漏掉一次發布就要標出來——門檻放寬不等於關掉。"""
    assert indicators.is_stale(pd.Timestamp("2026-04-01"), _spec("CPIAUCSL"),
                               "2026-08-12") is True


def test_discontinued_series_are_not_carried_in_the_list():
    """USSLIND（費城聯準銀行美國領先指標）最後一期停在 2020-02、落後 2384 天。
    那是停止更新，不是「還沒公布」；留著只會每天多一則假警告。"""
    assert "USSLIND" not in {s.fred_id for s in SERIES}


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

def test_report_separates_missing_stale_and_discontinued(tmp_path, monkeypatch):
    """三種狀態的處置完全不同，畫面上卻都是「沒有新數字」：

      缺失      抓不到          -> 要去修來源
      延遲      這一期還沒公布   -> 等就好
      停止更新  這條序列不發了   -> 從清單移除
    """
    from macro_monitor import release_log
    monkeypatch.setattr(release_log, "RELEASE_LOG_PATH", str(tmp_path / "r.csv"))
    monkeypatch.setattr(release_log, "SCAN_LOG_PATH", str(tmp_path / "s.csv"))

    data = {s.fred_id: _monthly([1.0] * 40, start="2023-05-01") for s in SERIES}
    data["UNRATE"] = pd.Series(dtype=float)                        # 缺失
    # ICSA 門檻 20 天：落後 60 天是延遲，還沒到停止更新的程度
    data["ICSA"] = pd.Series([200.0] * 40,
                             index=pd.date_range("2026-06-13", periods=40, freq="-1W"))
    # Case-Shiller 落後十年 -> 停止更新
    data["CSUSHPINSA"] = _monthly([100.0] * 40, start="2012-01-01")

    rep = report.build_report(as_of="2026-08-12", data=data)
    assert "UNRATE" in {r["fred_id"] for r in rep["missing_series"]}
    assert "ICSA" in {r["fred_id"] for r in rep["stale_series"]}
    assert "CSUSHPINSA" in {r["fred_id"] for r in rep["discontinued_series"]}
    # 已停止更新的不該同時被算成「延遲」——那會讓人以為等一等就有
    assert "CSUSHPINSA" not in {r["fred_id"] for r in rep["stale_series"]}
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


# --- 發布日誌 ---------------------------------------------------------------

def test_release_log_records_when_a_period_first_appeared(tmp_path):
    """FRED 只帶參考期，不帶「這個數字哪天公布」。發布日必須靠每日掃描觀測。"""
    from macro_monitor import release_log
    p = str(tmp_path / "log.csv")
    assert len(release_log.record_scan({"CPIAUCSL": ("2026-06-01", 3.73)}, "2026-08-12", p)) == 1
    assert len(release_log.record_scan({"CPIAUCSL": ("2026-07-01", 3.60)}, "2026-08-13", p)) == 1
    df = release_log.load_log(p)
    assert list(df["observed_at"]) == ["2026-08-12", "2026-08-13"]


def test_rescanning_the_same_period_is_not_a_new_release(tmp_path):
    from macro_monitor import release_log
    p = str(tmp_path / "log.csv")
    release_log.record_scan({"CPIAUCSL": ("2026-06-01", 3.73)}, "2026-08-12", p)
    assert release_log.record_scan({"CPIAUCSL": ("2026-06-01", 3.73)}, "2026-08-13", p) == []


def test_a_revision_is_not_counted_as_a_new_release(tmp_path):
    """數值被事後修正而參考期不變——那是修正，不是發布。
    算成發布的話，觀測到的發布時滯會被大量假訊號污染。"""
    from macro_monitor import release_log
    p = str(tmp_path / "log.csv")
    release_log.record_scan({"GDPC1": ("2026-04-01", 2.1)}, "2026-07-30", p)
    assert release_log.record_scan({"GDPC1": ("2026-04-01", 2.4)}, "2026-08-28", p) == []


def test_observed_lag_needs_at_least_two_periods(tmp_path):
    """一期算不出節奏。硬給一個數字會讓人以為那是量出來的結果。"""
    from macro_monitor import release_log
    p = str(tmp_path / "log.csv")
    release_log.record_scan({"CPIAUCSL": ("2026-06-01", 3.7)}, "2026-07-13", p)
    assert release_log.observed_lag_stats(release_log.load_log(p), "CPIAUCSL") is None
    release_log.record_scan({"CPIAUCSL": ("2026-07-01", 3.6)}, "2026-08-13", p)
    stats = release_log.observed_lag_stats(release_log.load_log(p), "CPIAUCSL")
    assert stats["observations"] == 2 and stats["median_lag_days"] > 0


def test_scan_log_distinguishes_no_change_from_no_scan(tmp_path):
    """少了掃描紀錄，「資料停止更新」與「排程壞掉沒跑」在日誌上長得一模一樣。"""
    from macro_monitor import release_log
    p = str(tmp_path / "scan.csv")
    release_log.record_scan_time("2026-08-12", ["A", "B"], p)
    release_log.record_scan_time("2026-08-13", ["A", "B"], p)
    df = pd.read_csv(p)
    assert list(df["scan_date"]) == ["2026-08-12", "2026-08-13"]


# --- 深入分析 ---------------------------------------------------------------

def _series(values, start="2000-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"),
                     dtype=float)


def test_acceleration_detects_a_slowing_rise():
    """水準仍在上升、但上升速度在放慢——只看「三個月變化」看不出來。"""
    from macro_monitor import analysis
    rising_then_slowing = _series([0, 3, 6, 9, 10, 11, 12])
    assert analysis.acceleration(rising_then_slowing, 3) < 0


def test_turning_points_are_not_claimed_for_the_most_recent_periods():
    """轉折需要後續資料才確認得了。硬判最近幾期會產生一堆事後被推翻的假轉折。"""
    from macro_monitor import analysis
    v = _series(list(np.arange(30)) + list(np.arange(30, 0, -1)))
    tp = analysis.last_turning_point(v, window=6)
    assert tp["confirmation_lag_periods"] == 6
    last = pd.Timestamp(tp["last_peak_date"])
    assert last <= v.index[-7]


def test_recession_contrast_reports_distributions_not_a_probability():
    """單一指標算不出衰退機率。把兩組分布擺出來比自訂門檻誠實。"""
    from macro_monitor import analysis
    n = 200
    v = _series([4.0] * n)
    v.iloc[50:70] = 8.0
    rec = pd.Series(0, index=v.index)
    rec.iloc[50:70] = 1
    out = analysis.recession_contrast(v, rec)
    assert out["recession_mean"] == pytest.approx(8.0)
    assert out["expansion_mean"] == pytest.approx(4.0)
    assert "不是衰退機率" in out["note"]


def test_analysis_leaves_out_what_it_cannot_compute():
    """樣本不足的項目留空，不硬給數字。"""
    from macro_monitor import analysis
    out = analysis.analyse(_series([1.0, 2.0, 3.0]), None, "M")
    assert out["observations"] == 3
    assert "zscore_full" not in out
    assert "recession_contrast" not in out


def test_zscore_and_percentile_are_both_reported():
    """百分位說「排第幾」，z 分數說「離平均幾個標準差」。
    分布有厚尾時兩者印象很不一樣，一起看才完整。"""
    from macro_monitor import analysis
    v = _series(list(np.random.default_rng(1).normal(0, 1, 200)) + [6.0])
    z = analysis.zscore(v)
    p = indicators.percentile_of_latest(v)
    assert z > 3 and p > 99


# --- AI 新聞摘要：反幻覺 ----------------------------------------------------
#
# 散文比數字更危險：數字錯了看得出來，一段流暢的解釋不會有人懷疑。
# 因此這一組測試守的是「模型不能編」，而不是「摘要寫得好不好」。

DOC = (
    "Consumer Price Index - July 2026. The Consumer Price Index for All Urban "
    "Consumers rose 0.3 percent in July on a seasonally adjusted basis. "
    "The index for shelter rose 0.4 percent and was the largest contributor. "
    "The energy index declined 1.1 percent over the month. " * 6
)


def _model(obj):
    from unittest import mock
    payload = {"content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}]}
    resp = mock.Mock(status_code=200, text=json.dumps(payload))
    resp.json.return_value = payload
    return mock.Mock(post=mock.Mock(return_value=resp))


def _answer(**kw):
    base = {"found": True,
            "summary_zh": "7 月 CPI 月增 0.3%，住房分項上漲 0.4% 為最大貢獻來源，能源分項下跌 1.1%。",
            "drivers": ["住房 +0.4%", "能源 −1.1%"],
            "evidence": "The index for shelter rose 0.4 percent and was the largest contributor."}
    base.update(kw)
    return base


_ROW = {"fred_id": "CPIAUCSL", "label": "CPI 消費者物價", "reference_date": "2026-07-01",
        "value": 3.6, "unit": "%", "change_3m": 0.4, "available": True}

# CPI 這一條已被歸為「已確認接受無摘要」，不會發出請求。要測抓取與模型路徑，
# 必須用一條**真的還會去抓**的指標，否則測到的只是短路那一行。
_FETCHABLE_ROW = {"fred_id": "RSAFS", "label": "零售銷售", "reference_date": "2026-07-01",
                  "value": 4.1, "unit": "%", "change_3m": 0.6, "available": True}


def test_summary_is_accepted_when_the_quote_exists_in_the_document():
    from macro_monitor import news_ai
    out = news_ai.summarise_release(_ROW, DOC, "https://bls.gov/x",
                                    api_key="k", session=_model(_answer()))
    assert "住房" in out.summary_zh
    assert out.drivers == ["住房 +0.4%", "能源 −1.1%"]


def test_a_fabricated_quote_discards_the_whole_summary():
    """引句編得出來，摘要同樣不可信——只丟掉引句、留下摘要是不夠的。"""
    from macro_monitor import news_ai
    with pytest.raises(ValueError, match="幻覺"):
        news_ai.summarise_release(
            _ROW, DOC, "https://bls.gov/x", api_key="k",
            session=_model(_answer(evidence="Used car prices surged 12 percent in July.")))


def test_a_summary_without_evidence_is_rejected():
    from macro_monitor import news_ai
    with pytest.raises(ValueError, match="未提供原文佐證"):
        news_ai.summarise_release(_ROW, DOC, "https://bls.gov/x",
                                  api_key="k", session=_model(_answer(evidence="")))


def test_the_model_saying_it_cannot_read_the_document_is_not_forced():
    """文件抓錯或與指標無關時，模型說「看不出來」要被接受，不能硬要一段摘要。"""
    from macro_monitor import news_ai
    with pytest.raises(ValueError, match="不足以摘要"):
        news_ai.summarise_release(_ROW, DOC, "https://bls.gov/x",
                                  api_key="k", session=_model(_answer(found=False)))


def test_a_javascript_shell_page_is_not_sent_to_the_model():
    """只有框架、沒有內容的頁面正是幻覺的溫床——寧可不摘要。"""
    from unittest import mock
    from macro_monitor import news_ai
    resp = mock.Mock(status_code=200, text="<html><body><div id='app'></div></body></html>")
    with pytest.raises(ValueError, match="內容過短"):
        news_ai.fetch_release_document("RSAFS", session=mock.Mock(get=lambda *a, **k: resp))


def test_market_price_indicators_have_no_release_source():
    """殖利率與利差沒有對應的統計發布。硬配一個來源，只會讓模型對著
    不相干的文件生出一段像樣的解釋。"""
    from macro_monitor import news_ai
    for fid in ("DGS10", "T10Y2Y", "T5YIFR"):
        assert fid not in news_ai.RELEASE_SOURCES
        with pytest.raises(ValueError, match="無對應的官方統計發布"):
            news_ai.fetch_release_document(fid)


def test_no_api_key_skips_quietly_with_a_reason():
    from macro_monitor import news_ai
    out, notes = news_ai.summarise_updated([_ROW], ["CPIAUCSL"], api_key="")
    assert out == {} and any("ANTHROPIC_API_KEY" in n for n in notes)


def test_fetch_failure_is_reported_per_url():
    """抓不到時，「來源改版了」與「這個指標本來就沒有發布」必須分得出來。"""
    from unittest import mock
    from macro_monitor import news_ai
    resp = mock.Mock(status_code=404, text="")
    with pytest.raises(ValueError) as exc:
        news_ai.fetch_release_document("RSAFS", session=mock.Mock(get=lambda *a, **k: resp))
    assert "HTTP 404" in str(exc.value) and "census.gov" in str(exc.value)


# --- 摘要儲存 ---------------------------------------------------------------

def test_successful_summaries_persist_and_are_not_regenerated(tmp_path):
    """同一期的數字不會變，重算只是多花一次模型成本。"""
    from macro_monitor import news_store
    p = str(tmp_path / "n.json")
    rows = [dict(_ROW)]
    store = news_store.save({}, {"CPIAUCSL": {"summary_zh": "x", "drivers": [],
                                              "evidence": "e", "source_url": "u",
                                              "model": "m"}}, rows, p)
    assert news_store.missing(rows, store) == []


def test_failed_summaries_are_retried_on_the_next_run(tmp_path):
    """若以「發布日誌記錄過這一期」為判準，今天 API 出錯的指標明天不會再試，
    那一期就永遠沒有摘要。改以「摘要存不存在」為判準。"""
    from macro_monitor import news_store
    rows = [dict(_ROW)]
    assert news_store.missing(rows, {}) == ["CPIAUCSL"]


def test_a_new_period_needs_a_new_summary(tmp_path):
    from macro_monitor import news_store
    rows = [dict(_ROW)]
    store = news_store.save({}, {"CPIAUCSL": {"summary_zh": "x", "drivers": [],
                                              "evidence": "e", "source_url": "u", "model": "m"}},
                            rows, str(tmp_path / "n.json"))
    rows[0]["reference_date"] = "2026-08-01"       # 新的一期
    assert news_store.missing(rows, store) == ["CPIAUCSL"]


def test_store_keeps_only_recent_periods(tmp_path):
    from macro_monitor import news_store
    p = str(tmp_path / "n.json")
    store = {}
    for m in range(1, 11):
        rows = [{**_ROW, "reference_date": f"2026-{m:02d}-01"}]
        store = news_store.save(store, {"CPIAUCSL": {"summary_zh": f"s{m}", "drivers": [],
                                                     "evidence": "e", "source_url": "u",
                                                     "model": "m"}}, rows, p)
    assert len(store) == news_store.KEEP_PER_SERIES
    assert news_store.key("CPIAUCSL", "2026-10-01") in store


def test_every_summary_failure_names_the_indicator():
    """實測有一則失敗訊息是「模型回應中找不到 JSON」，沒有指標代碼——
    因為那是共用的 call_model 拋出的、不知道自己在處理哪一條。
    報告上於是出現一個無主的錯誤，無從追查。"""
    from unittest import mock
    from macro_monitor import news_ai

    doc_resp = mock.Mock(status_code=200, text="<p>" + ("Retail sales data. " * 100) + "</p>")
    empty = {"content": [{"type": "text", "text": ""}]}
    api_resp = mock.Mock(status_code=200, text="{}")
    api_resp.json.return_value = empty
    session = mock.Mock(get=mock.Mock(return_value=doc_resp),
                        post=mock.Mock(return_value=api_resp))

    _, notes = news_ai.summarise_updated([_FETCHABLE_ROW], ["RSAFS"], api_key="k", session=session)
    assert notes and all("RSAFS" in n for n in notes)


# --- 已確認接受無摘要 -------------------------------------------------------
#
# 這個機制本身是對的：查證過確實無路可走的來源，該明講「接受」而不是每天重報
# 一次失敗，也不是靜靜刪掉。
#
# 但它一度被套用在七條 BLS 指標上，理由是「bls.gov 全站 403、沒有替代來源可
# 換」——後半句從來沒查證過，而且是錯的（Wayback 拿得到同一份原文）。
# 因此這一組測試改用一個**合成的**指標來守機制，不再把「哪幾條該被接受」
# 寫死進測試裡：那個結論屬於探測結果，不屬於測試。

_FAKE_ACCEPTED = "ZZTESTONLY"


@pytest.fixture
def accepted_indicator(monkeypatch):
    from macro_monitor import news_ai
    monkeypatch.setitem(news_ai.UNAVAILABLE_SOURCES, _FAKE_ACCEPTED, "查證過三條路都不通")
    return _FAKE_ACCEPTED


def test_accepted_sources_make_no_request_at_all(accepted_indicator):
    """決定接受，就要真的不打——否則每天照樣多打一次註定失敗的請求。"""
    from unittest import mock
    from macro_monitor import news_ai
    session = mock.Mock()
    with pytest.raises(ValueError) as exc:
        news_ai.fetch_release_document(accepted_indicator, session=session)
    assert session.get.call_count == 0
    assert news_ai.classify_failure(str(exc.value)) == "accepted"


def test_accepted_indicators_skip_the_model_and_keep_an_explanation(accepted_indicator):
    """不是靜靜刪掉：畫面仍要說得出為什麼沒有摘要，讓「刻意不做」與
    「壞掉了」分得出來。"""
    from unittest import mock
    from macro_monitor import news_ai
    session = mock.Mock()
    row = {**_ROW, "fred_id": accepted_indicator}
    out, notes = news_ai.summarise_updated([row], [accepted_indicator], api_key="k",
                                           session=session)
    assert out == {}
    assert session.get.call_count == 0 and session.post.call_count == 0
    assert len(notes) == 1
    assert accepted_indicator in notes[0] and "查證過" in notes[0]
    assert news_ai.classify_failure(notes[0]) == "accepted"


def test_nothing_is_currently_accepted_without_a_probe_result():
    """空的才是對的。這裡若又冒出項目，應該伴隨一次探測結果——
    「主要來源擋住了」不等於「沒有別的路」，那一步被跳過過一次。"""
    from macro_monitor import news_ai
    assert news_ai.UNAVAILABLE_SOURCES == {}


def test_accepted_is_not_reported_as_a_blocked_failure():
    """訊息裡同時有「403」與接受字樣。若順序寫反，它會被歸成 blocked，
    於是每天照樣在「該換來源」那一列出現一次。"""
    from macro_monitor import news_ai
    msg = f"CPIAUCSL：{news_ai.UNAVAILABLE_REASON}——bls.gov 對資料中心 IP 回 403（已確認接受無摘要）"
    assert news_ai.classify_failure(msg) == "accepted"


def test_every_failure_kind_has_a_label_on_the_dashboard():
    """分類器多一種、畫面沒跟上，那一列就只會顯示代碼本身。"""
    import re
    from pathlib import Path
    from macro_monitor import news_ai

    js = Path(__file__).resolve().parents[1] / "docs" / "macro.js"
    block = re.search(r"const FAILURE_LABEL = \{(.*?)\n\};", js.read_text(), re.S).group(1)
    labelled = set(re.findall(r"^\s*(\w+):", block, re.M))
    kinds = {news_ai.classify_failure(m) for m in (
        f"X：{news_ai.UNAVAILABLE_REASON}——y",
        "X 官方發布抓取失敗——https://y: HTTP 403",
        f"X：{news_ai.NO_SOURCE_REASON}",
        "X：模型判定文件內容不足以摘要這一期的數字",
        "X：Anthropic API 回應 500",
    )}
    assert kinds <= labelled, f"docs/macro.js 缺少這些失敗類別的說明：{kinds - labelled}"


@pytest.mark.parametrize("message,expected", [
    ("CPIAUCSL 官方發布抓取失敗——https://www.bls.gov/x: HTTP 403", "blocked"),
    ("DGS10：無對應的官方統計發布（市場價格類指標）", "no_source"),
    ("RSAFS：模型判定文件內容不足以摘要這一期的數字", "content"),
    ("HOUST 官方發布抓取失敗——https://x: 內容過短（120 字）", "content"),
    ("CPIAUCSL：Anthropic API 回應 500", "model"),
])
def test_failure_reasons_are_classified(message, expected):
    """來源封鎖、沒有來源、內容不符、模型出錯——四種的處置完全不同：
    封鎖要換來源、沒有來源是正常、內容不符要檢查網址、模型出錯下次會重試。
    混成一份清單就看不出該做什麼。"""
    from macro_monitor import news_ai
    assert news_ai.classify_failure(message) == expected


def test_the_no_source_reason_has_exactly_one_wording():
    """同一個原因寫成兩種說法，分類器就得猜——實測就是這樣漏判的。"""
    from macro_monitor import news_ai
    _, notes = news_ai.summarise_updated(
        [{"fred_id": "DGS10", "available": True, "reference_date": "2026-08-11"}],
        ["DGS10"], api_key="k")
    assert notes and news_ai.classify_failure(notes[0]) == "no_source"
    try:
        news_ai.fetch_release_document("DGS10")
    except ValueError as e:
        assert news_ai.classify_failure(str(e)) == "no_source"


# --- 替代來源探測 -----------------------------------------------------------
#
# 探測器的價值全在「它會不會放過爛頁面」。只檢查 HTTP 200 的話，一頁導覽選單
# 也能拿到綠燈——而那正是目前那四條「內容不符」的來源被選中的原因。
# 因此這一組測試每一則都在餵一種**看起來像成功的失敗**。

from datetime import date as _date  # noqa: E402


def _resp(text="", status=200, url=None):
    from unittest import mock
    return mock.Mock(status_code=status, text=text, url=url)


def _session(resp):
    from unittest import mock
    return mock.Mock(get=mock.Mock(return_value=resp))


_AS_OF = _date(2026, 8, 12)

# 一頁「像樣」的 CPI 當期發布：夠長、主題對、提到參考月份
_GOOD_PAGE = "<p>" + (
    "Consumer Price Index News Release. The CPI for All Urban Consumers rose in July 2026. "
    "The index for shelter rose 0.4 percent, the largest contributor to inflation. " * 12
) + "</p>"


def _cand(url="https://www.example.gov/cpi.htm", kind="official"):
    from macro_monitor.source_probe import Candidate
    return Candidate(url, kind)


def test_a_real_release_page_passes_every_check():
    from macro_monitor import source_probe
    r = source_probe.probe("CPIAUCSL", _cand(), session=_session(_resp(_GOOD_PAGE)), as_of=_AS_OF)
    assert r.verdict == "usable"
    assert len(r.topic_hits) >= source_probe.MIN_TOPIC_HITS
    assert "July" in r.recent_months


def test_a_navigation_menu_is_not_mistaken_for_a_release():
    """五千字的導覽選單也會回 200、也夠長。分辨它靠的是主題關鍵詞。"""
    from macro_monitor import source_probe
    menu = "<p>" + ("Home About Contact Careers Data Tools Publications Help FAQ " * 80) + "</p>"
    r = source_probe.probe("CPIAUCSL", _cand(), session=_session(_resp(menu)), as_of=_AS_OF)
    assert r.verdict == "off_topic"


def test_a_static_explainer_page_is_not_mistaken_for_the_current_release():
    """這正是目前那四條「內容不符」的病灶：頁面主題對、內容也夠長，
    但講的是「這項指標是什麼」，沒有這一期的數字。"""
    from macro_monitor import source_probe
    explainer = "<p>" + (
        "The Consumer Price Index measures inflation faced by urban consumers. "
        "The CPI basket includes shelter, food and energy components. " * 20) + "</p>"
    r = source_probe.probe("CPIAUCSL", _cand(), session=_session(_resp(explainer)), as_of=_AS_OF)
    assert r.verdict == "stale_page"


def test_a_redirect_to_the_homepage_is_not_a_success():
    """實測很多站台把不存在的頁面 302 回首頁再回 200。
    只看狀態碼，就會把首頁當成發布頁收下。"""
    from macro_monitor import source_probe
    r = source_probe.probe(
        "CPIAUCSL", _cand("https://www.bls.gov/news.release/cpi.nr0.htm"),
        session=_session(_resp(_GOOD_PAGE, url="https://www.somewhereelse.com/")), as_of=_AS_OF)
    assert r.verdict == "redirected"


def test_a_redirect_within_the_same_site_is_still_fine():
    """http→https、加 www、換路徑都算同一個來源，不該因此淘汰。"""
    from macro_monitor import source_probe
    r = source_probe.probe(
        "CPIAUCSL", _cand("https://bls.gov/news.release/cpi.nr0.htm"),
        session=_session(_resp(_GOOD_PAGE, url="https://www.bls.gov/news.release/cpi.nr0.htm")),
        as_of=_AS_OF)
    assert r.verdict == "usable"


def test_a_403_is_recorded_as_blocked_not_as_a_generic_error():
    """封鎖與「網址寫錯」的處置完全不同，報告上必須分得出來。"""
    from macro_monitor import source_probe
    r = source_probe.probe("CPIAUCSL", _cand(), session=_session(_resp("", status=403)),
                           as_of=_AS_OF)
    assert r.verdict == "blocked" and "403" in r.reason


def test_a_js_shell_is_rejected_before_topic_checks():
    from macro_monitor import source_probe
    r = source_probe.probe("NFCI", _cand(), session=_session(_resp("<div id='app'></div>")),
                           as_of=_AS_OF)
    assert r.verdict == "too_short"


def test_a_network_error_is_a_result_not_a_crash():
    """探測十幾個網址，其中一個逾時不該讓整份報告消失。"""
    from unittest import mock
    from macro_monitor import source_probe
    session = mock.Mock(get=mock.Mock(side_effect=TimeoutError("timed out")))
    r = source_probe.probe("CPIAUCSL", _cand(), session=session, as_of=_AS_OF)
    assert r.verdict == "error" and not r.usable


def test_recent_months_crosses_the_year_boundary():
    """一月往回推是去年十二月。用 month-1 減會得到 0，索引就爆了。"""
    from macro_monitor import source_probe
    assert source_probe.recent_month_names(_date(2026, 1, 15)) == ["January", "December", "November"]


def test_every_candidate_has_topic_terms_to_be_checked_against():
    """少了主題關鍵詞，那條指標的第三道檢查會自動放行——
    看起來有四道檢查，實際只有三道。"""
    from macro_monitor import source_probe
    for fred_id in source_probe.CANDIDATES:
        terms = source_probe.TOPIC_TERMS.get(fred_id, [])
        assert len(terms) >= source_probe.MIN_TOPIC_HITS, fred_id


def test_the_probe_never_changes_the_live_source_table():
    """探測器只提供證據。自動採用等於把「沒驗證」換個地方藏起來。"""
    from macro_monitor import news_ai, source_probe
    before = {k: list(v) for k, v in news_ai.RELEASE_SOURCES.items()}
    source_probe.probe_all({"CPIAUCSL": [_cand()]}, session=_session(_resp(_GOOD_PAGE)),
                           as_of=_AS_OF)
    assert news_ai.RELEASE_SOURCES == before


def test_the_report_explains_why_each_url_failed():
    """只寫「可用／不可用」的話，「來源改版了」與「我們挑錯頁面」
    在報告上又會長得一模一樣。"""
    from macro_monitor import source_probe
    results = [
        source_probe.probe("CPIAUCSL", _cand(), session=_session(_resp("", status=403)),
                           as_of=_AS_OF),
        source_probe.probe("CPIAUCSL", _cand(), session=_session(_resp(_GOOD_PAGE)),
                           as_of=_AS_OF),
    ]
    md = source_probe.to_markdown(results)
    assert "blocked" in md and "usable" in md and "HTTP 403" in md


def test_fedfunds_uses_the_proven_fomc_resolver_not_a_static_url():
    """原本指向 press_monetary.xml——那是發布索引的 RSS，去標籤後只剩一串標題，
    看不出這一期的利率決定。模型正確地拒絕摘要，卻被記成「內容不符」。
    病灶是我們挑錯文件，不是來源不給。"""
    from unittest import mock
    from macro_monitor import news_ai

    assert "FEDFUNDS" not in news_ai.RELEASE_SOURCES
    assert "FEDFUNDS" in news_ai.RESOLVERS

    meeting = mock.Mock(statement_text="The Committee decided to maintain the target range. " * 40,
                        statement_url="https://www.federalreserve.gov/x/monetary20260729a.htm")
    with mock.patch("liquidity_monitor.sources.fomc.latest_meeting_on_or_before",
                    return_value=meeting):
        doc, url = news_ai.fetch_release_document("FEDFUNDS")
    assert "target range" in doc and "monetary2026" in url


def test_a_resolver_that_finds_nothing_fails_loudly():
    """解析器回 None 時若靜靜略過，FEDFUNDS 就會從失敗清單裡消失，
    看起來像「本來就沒有來源」——那是另一件事。"""
    from unittest import mock
    from macro_monitor import news_ai
    with mock.patch("liquidity_monitor.sources.fomc.latest_meeting_on_or_before",
                    return_value=None):
        with pytest.raises(ValueError, match="FEDFUNDS"):
            news_ai.fetch_release_document("FEDFUNDS")


def test_indicators_with_a_resolver_are_not_reported_as_having_no_source():
    """RESOLVERS 裡的指標不在 RELEASE_SOURCES 裡。若只檢查後者，
    FEDFUNDS 每天會被歸成「本來就沒有來源」——一個完全錯誤的結論。"""
    from unittest import mock
    from macro_monitor import news_ai
    row = {"fred_id": "FEDFUNDS", "label": "聯邦資金利率", "reference_date": "2026-07-01",
           "value": 4.25, "unit": "%", "change_3m": 0.0, "available": True}
    with mock.patch("liquidity_monitor.sources.fomc.latest_meeting_on_or_before",
                    side_effect=RuntimeError("boom")):
        _, notes = news_ai.summarise_updated([row], ["FEDFUNDS"], api_key="k",
                                             session=mock.Mock())
    assert notes and news_ai.classify_failure(notes[0]) != "no_source"


# --- 期別比對：採用存檔快照之後才變成必要的一道檢查 -------------------------
#
# Wayback 給的是「最新的快照」，不是「最新的發布」。快照停在上一期時，文件本身
# 完全真實、引句逐字對得上、摘要毫無破綻，只有一件事錯了：它講的是六月，而我們
# 把它掛在七月的數字底下。三道反幻覺防線全都擋不住——模型並沒有編造任何東西。

_JULY_DOC = "Consumer Price Index for July 2026. The index for shelter rose 0.4 percent. " * 8
_JUNE_DOC = "Consumer Price Index for June 2026. The index for shelter rose 0.2 percent. " * 8


def test_the_right_period_passes():
    from macro_monitor import news_ai
    news_ai.assert_document_covers_period(_JULY_DOC, "CPIAUCSL", "2026-07-01", "M")


def test_a_snapshot_stuck_on_last_month_is_rejected():
    """這是採用 Wayback 之後最危險的失敗：一份真實、通順、引句對得上的摘要，
    掛在錯的期別下。"""
    from macro_monitor import news_ai
    with pytest.raises(ValueError, match="未提到這一期"):
        news_ai.assert_document_covers_period(_JUNE_DOC, "CPIAUCSL", "2026-07-01", "M")


def test_the_wrong_year_is_rejected_even_when_the_month_matches():
    """去年七月的存檔一樣有「July」。只比對月份會放它過關。"""
    from macro_monitor import news_ai
    doc = _JULY_DOC.replace("2026", "2025")
    with pytest.raises(ValueError, match="未提到這一期"):
        news_ai.assert_document_covers_period(doc, "CPIAUCSL", "2026-07-01", "M")


def test_quarterly_series_are_matched_by_quarter_wording():
    """GDP 發布不會寫「April」，它寫「second quarter」。用月份比對會誤殺。"""
    from macro_monitor import news_ai
    doc = "Real gross domestic product increased in the second quarter of 2026. " * 8
    news_ai.assert_document_covers_period(doc, "GDPC1", "2026-04-01", "Q")
    with pytest.raises(ValueError, match="未提到這一期"):
        news_ai.assert_document_covers_period(doc, "GDPC1", "2026-07-01", "Q")


@pytest.mark.parametrize("freq", ["W", "D"])
def test_weekly_and_daily_series_skip_the_period_check(freq):
    """那些發布不用「某月」指稱期別，硬比只會誤殺。無法可靠判斷時就不判斷。"""
    from macro_monitor import news_ai
    news_ai.assert_document_covers_period("anything at all", "MORTGAGE30US", "2026-08-07", freq)


def test_a_stale_snapshot_never_reaches_the_model():
    """擋下來的重點不只是省一次呼叫——是那段摘要根本不該存在。"""
    from unittest import mock
    from macro_monitor import news_ai
    doc_resp = mock.Mock(status_code=200, text=f"<p>{_JUNE_DOC}</p>", url=None)
    session = mock.Mock(get=mock.Mock(return_value=doc_resp), post=mock.Mock())
    row = {**_ROW, "reference_date": "2026-07-01", "freq": "M"}
    out, notes = news_ai.summarise_updated([row], ["CPIAUCSL"], api_key="k", session=session)
    assert out == {}
    assert session.post.call_count == 0
    assert news_ai.classify_failure(notes[0]) == "content"


def test_the_bls_series_now_have_sources_again():
    """探測前的結論是「沒有替代來源可換」——那句話沒有查證過，而且是錯的。
    Wayback 拿得到同一份 BLS 原文。"""
    from macro_monitor import news_ai
    for fid in ("CPIAUCSL", "CPILFESL", "UNRATE", "PAYEMS", "CIVPART",
                "SAHMREALTIME", "JTSJOL"):
        assert fid in news_ai.RELEASE_SOURCES, fid
        assert fid not in news_ai.UNAVAILABLE_SOURCES, fid


def test_nfci_has_no_release_text_and_that_is_a_finding_not_an_oversight():
    """兩個候選網址實測都是 stale_page：芝加哥聯準銀行把 NFCI 當資料發布，
    沒有隨期別更新的敘述可摘要。歸在「本來就沒有來源」才是對的。"""
    from macro_monitor import news_ai
    assert "NFCI" not in news_ai.RELEASE_SOURCES and "NFCI" not in news_ai.RESOLVERS
    with pytest.raises(ValueError) as exc:
        news_ai.fetch_release_document("NFCI")
    assert news_ai.classify_failure(str(exc.value)) == "no_source"
