"""⑥ FOMC 決議偏向：AI 摘要與分類。

原本用 `re.search("voting against")` 判斷異議——只知道有沒有人反對，
不知道**反對的方向**。但規格第135行寫的是「持平但有**升息**異議：−1」：
反對者主張升息才是鷹派訊號，主張降息反而偏鴿。同一個正則把兩者算成
同一件事，等於把一個訊號和它的反面混為一談。

這裡守三件事：分類正確、**模型不得亂編**、以及硬資料永遠勝過模型判斷。
"""
from __future__ import annotations

import json
from unittest import mock

import pytest

from liquidity_monitor.sources import fomc_ai


STATEMENT = (
    "The Federal Open Market Committee decided to maintain the target range for the "
    "federal funds rate at 4 to 4-1/4 percent. Voting against this action was "
    "Governor Smith, who preferred to raise the target range by 25 basis points at "
    "this meeting. The Committee will continue to monitor incoming data."
)


def _api(payload: dict, status=200):
    """模擬 Anthropic Messages API 的回應。"""
    resp = mock.Mock(status_code=status, text=json.dumps(payload))
    resp.json.return_value = payload
    return mock.Mock(post=mock.Mock(return_value=resp))


def _content(obj) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}]}


def _classification(**kw):
    base = {
        "decision": "hold", "rate_change_bp": 0, "dissent_count": 1,
        "dissent_direction": "hawkish",
        "dissent_quote": "Voting against this action was Governor Smith, who preferred "
                         "to raise the target range by 25 basis points at this meeting.",
        "summary_zh": "維持利率不變，一位理事主張升息一碼。",
    }
    base.update(kw)
    return base


# --- 分類 -------------------------------------------------------------------

def test_extracts_dissent_direction_which_the_regex_could_not():
    session = _api(_content(_classification()))
    c = fomc_ai.classify_statement(STATEMENT, api_key="k", session=session)
    assert c.decision == "hold"
    assert c.dissent_count == 1
    assert c.dissent_direction == "hawkish"
    assert c.has_hawkish_dissent
    assert "升息" in c.summary_zh


def test_json_wrapped_in_a_code_fence_is_still_parsed():
    payload = {"content": [{"type": "text",
                            "text": "```json\n" + json.dumps(_classification()) + "\n```"}]}
    c = fomc_ai.classify_statement(STATEMENT, api_key="k", session=_api(payload))
    assert c.dissent_direction == "hawkish"


# --- 防幻覺 -----------------------------------------------------------------

def test_fabricated_dissent_quote_is_rejected():
    """模型可以編出一段看起來合理的敘述，但編不出一段剛好逐字出現在原文的句子。
    引文對不上就不採信它的異議判定——這是最有效的一道防線。"""
    session = _api(_content(_classification(
        dissent_quote="Voting against was Governor Nobody, who preferred a larger cut.")))
    c = fomc_ai.classify_statement(STATEMENT, api_key="k", session=session)
    assert c.dissent_count == 0
    assert c.dissent_direction == "none"
    assert any("未出現在聲明全文" in w for w in c.warnings)


def test_dissent_without_a_quote_is_rejected():
    session = _api(_content(_classification(dissent_quote="")))
    c = fomc_ai.classify_statement(STATEMENT, api_key="k", session=session)
    assert c.dissent_count == 0
    assert any("未提供原文引用" in w for w in c.warnings)


def test_quote_matching_tolerates_whitespace_and_punctuation_variants():
    """原文經過去標籤處理，空白與引號樣式可能與模型輸出略有出入，
    但那不是幻覺，不該因此丟掉正確的判定。"""
    session = _api(_content(_classification(
        dissent_quote="Voting  against this action was Governor Smith, who preferred\n"
                      "to raise the target range by 25 basis points at this meeting.")))
    c = fomc_ai.classify_statement(STATEMENT, api_key="k", session=session)
    assert c.dissent_count == 1 and not c.warnings


@pytest.mark.parametrize("bad", [
    {"decision": "easing"},                  # 不在允許值內
    {"dissent_direction": "slightly hawkish"},
])
def test_out_of_schema_values_are_rejected_rather_than_coerced(bad):
    session = _api(_content(_classification(**bad)))
    with pytest.raises(ValueError):
        fomc_ai.classify_statement(STATEMENT, api_key="k", session=session)


def test_non_json_response_fails_loudly():
    payload = {"content": [{"type": "text", "text": "I think they held rates steady."}]}
    with pytest.raises(ValueError, match="找不到 JSON"):
        fomc_ai.classify_statement(STATEMENT, api_key="k", session=_api(payload))


def test_api_error_carries_the_status_code():
    resp = mock.Mock(status_code=401, text='{"error":{"message":"invalid x-api-key"}}')
    session = mock.Mock(post=mock.Mock(return_value=resp))
    with pytest.raises(ValueError, match="401"):
        fomc_ai.classify_statement(STATEMENT, api_key="k", session=session)


def test_missing_api_key_is_a_clear_failure_not_a_silent_default():
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        fomc_ai.classify_statement(STATEMENT, api_key=None)


# --- 硬資料勝過模型 ---------------------------------------------------------

def test_actual_rate_data_overrides_the_models_direction():
    """升降息幅度是硬資料，沒有讓模型裁量的理由。不一致時採用 FRED 並記下矛盾
    ——那通常代表聲明抓錯了場次，值得人看一眼。"""
    c = fomc_ai.FomcClassification("hold", 0, 0, "none")
    c = fomc_ai.reconcile_with_rate_data(c, 4.25, 4.00)
    assert c.decision == "cut"
    assert c.rate_change_bp == -25
    assert any("以實際利率為準" in w for w in c.warnings)


def test_agreement_with_rate_data_produces_no_warning():
    c = fomc_ai.FomcClassification("hold", 0, 0, "none")
    c = fomc_ai.reconcile_with_rate_data(c, 4.25, 4.25)
    assert c.decision == "hold" and not c.warnings


# --- 計分（規格第135行）-----------------------------------------------------

@pytest.mark.parametrize("decision,count,direction,expected,why", [
    ("cut",  0, "none",    2,  "降息無異議"),
    ("cut",  1, "hawkish", 1,  "降息有異議"),
    ("hold", 0, "none",    0,  "持平無異議"),
    ("hold", 1, "hawkish", -1, "持平但有升息異議"),
    ("hike", 0, "none",    -2, "升息"),
    ("hike", 2, "dovish",  -2, "升息（有異議仍為 -2）"),
])
def test_scoring_matches_the_spec_table(decision, count, direction, expected, why):
    c = fomc_ai.FomcClassification(decision, 0, count, direction)
    assert fomc_ai.score_from_classification(c) == expected, why


def test_dovish_dissent_on_a_hold_is_not_scored_as_hawkish():
    """規格寫的是「持平但有**升息**異議」。反對者主張降息時偏鴿，不該記 −1——
    正則式判斷無法分辨這一點，兩種情況都會被算成 −1。"""
    dovish = fomc_ai.FomcClassification("hold", 0, 1, "dovish")
    assert fomc_ai.score_from_classification(dovish) == 0

    hawkish = fomc_ai.FomcClassification("hold", 0, 1, "hawkish")
    assert fomc_ai.score_from_classification(hawkish) == -1


def test_mixed_dissent_on_a_hold_counts_as_hawkish_present():
    """同時有鷹派與鴿派反對票時，鷹派異議確實存在，符合規格那一列的條件。"""
    c = fomc_ai.FomcClassification("hold", 0, 2, "mixed")
    assert fomc_ai.score_from_classification(c) == -1


# --- 抓取失敗要說得出是哪一種失敗 -------------------------------------------
#
# 實測 GitHub Actions 取不到聯準會新聞稿，但原本的訊息只寫「網路或網站問題」
# ——403（被擋）、逾時、DNS 失敗全都長一樣，完全無從判斷該修什麼。

def test_http_status_is_surfaced_when_the_index_is_blocked():
    import requests as rq
    from liquidity_monitor.sources import fomc

    def blocked(url, **kw):
        err = rq.HTTPError("403 Forbidden")
        err.response = mock.Mock(status_code=403)
        raise err

    with mock.patch.object(fomc.requests, "get", side_effect=blocked):
        with pytest.raises(ValueError) as exc:
            fomc.latest_meeting_on_or_before("2026-08-10")
    assert "HTTP 403" in str(exc.value)


def test_network_failure_reads_differently_from_being_blocked():
    import requests as rq
    from liquidity_monitor.sources import fomc

    with mock.patch.object(fomc.requests, "get",
                           side_effect=rq.ConnectTimeout("connection timed out")):
        with pytest.raises(ValueError) as exc:
            fomc.latest_meeting_on_or_before("2026-08-10")
    msg = str(exc.value)
    assert "ConnectTimeout" in msg
    assert "HTTP" not in msg          # 兩種失敗不能長得一樣


# --- 索引來源：RSS 為主，HTML 索引為備援 ------------------------------------
#
# 實測發現原本唯一的年度索引網址回 **404**——網站是連得到的（404 代表 DNS、
# TCP、TLS、HTTP 全部成功），只是路徑早就不存在。先前把它誤判成
# 「GitHub Actions 連不到 federalreserve.gov」，正是因為錯誤訊息把所有失敗
# 都寫成同一句「網路或網站問題」。

_RSS = ('<?xml version="1.0"?><rss><channel>'
        '<item><link>https://www.federalreserve.gov/newsevents/pressreleases/'
        'monetary20260729a.htm</link></item>'
        '<item><link>https://www.federalreserve.gov/newsevents/pressreleases/'
        'monetary20260617a.htm</link></item></channel></rss>')
_STATEMENT_HTML = ("<p>The Committee decided to maintain the target range. "
                   "Voting against this action was Governor Smith.</p>")


def test_rss_feed_is_parsed_even_though_it_has_no_href_attributes():
    """RSS 用的是 <link>…</link>。原本的正則綁死 href=，會整份漏掉——
    而 RSS 正是最穩定的來源。"""
    from liquidity_monitor.sources import fomc

    def get(url, **kw):
        text = _RSS if "feeds" in url else _STATEMENT_HTML
        return mock.Mock(text=text, raise_for_status=mock.Mock())

    with mock.patch.object(fomc.requests, "get", side_effect=get) as m:
        meeting = fomc.latest_meeting_on_or_before("2026-08-10")

    assert str(meeting.date.date()) == "2026-07-29"      # 取最新且不晚於 as_of
    assert meeting.has_dissent
    # 第一個來源就成功時不該再打其餘索引：索引 + 聲明本文，共兩次請求
    assert m.call_count == 2


def test_falls_through_to_html_index_when_the_feed_is_gone():
    import requests as rq
    from liquidity_monitor.sources import fomc

    def get(url, **kw):
        if "feeds" in url:
            err = rq.HTTPError("404")
            err.response = mock.Mock(status_code=404)
            raise err
        if "fomccalendars" in url:
            return mock.Mock(
                text='<a href="/newsevents/pressreleases/monetary20260729a.htm">x</a>',
                raise_for_status=mock.Mock())
        return mock.Mock(text=_STATEMENT_HTML, raise_for_status=mock.Mock())

    with mock.patch.object(fomc.requests, "get", side_effect=get):
        meeting = fomc.latest_meeting_on_or_before("2026-08-10")
    assert str(meeting.date.date()) == "2026-07-29"


def test_future_dated_statements_are_not_used():
    """as_of 之後才發布的聲明不能拿來當今天的訊號（前視偏誤）。"""
    from liquidity_monitor.sources import fomc

    def get(url, **kw):
        text = _RSS if "feeds" in url else _STATEMENT_HTML
        return mock.Mock(text=text, raise_for_status=mock.Mock())

    with mock.patch.object(fomc.requests, "get", side_effect=get):
        meeting = fomc.latest_meeting_on_or_before("2026-07-01")
    assert str(meeting.date.date()) == "2026-06-17"      # 7/29 那場尚未發生
