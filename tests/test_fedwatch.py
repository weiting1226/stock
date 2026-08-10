"""⑥ 市場隱含 12 個月路徑（CME FedWatch）：AI 判讀與計分。

規格第136行：降息≥2碼 +2 ／降息1碼 +1 ／持平 0 ／升息1碼 −1 ／升息≥2碼 −2。

這裡最關鍵的一條紅線：**模型只能讀畫面上的數字，不能回憶市場預期**。
讓它憑記憶說「市場預期降兩碼」是編造，不是解讀——頁面抓不到就不呼叫它。
"""
from __future__ import annotations

import json
from unittest import mock

import pytest

from liquidity_monitor.sources import fedwatch


PAGE = (
    "CME FedWatch Tool. Target Rate Probabilities for 29 Jul 2027 Fed Meeting. "
    "Current target rate is 375-400. The probability of 325-350 is 62.4%, "
    "the probability of 350-375 is 24.1%, and the probability of 300-325 is 13.5%."
)


def _model(obj):
    payload = {"content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}]}
    resp = mock.Mock(status_code=200, text=json.dumps(payload))
    resp.json.return_value = payload
    return mock.Mock(post=mock.Mock(return_value=resp))


def _reading(**kw):
    base = {
        "found": True,
        "implied_rate_pct": 3.375,          # 325-350 的中點
        "meeting_label": "29 Jul 2027 Fed Meeting",
        "evidence": "the probability of 325-350 is 62.4%",
        "summary_zh": "市場隱含一年後利率降至 3.25%–3.50% 區間。",
    }
    base.update(kw)
    return base


# --- 計分（規格第136行）-----------------------------------------------------

@pytest.mark.parametrize("bp,expected,why", [
    (-75, 2, "降息3碼"),
    (-50, 2, "降息2碼"),
    (-25, 1, "降息1碼"),
    (0, 0, "持平"),
    (25, -1, "升息1碼"),
    (50, -2, "升息2碼"),
    (100, -2, "升息4碼"),
])
def test_scoring_matches_the_spec_table(bp, expected, why):
    assert fedwatch.score_implied_path(bp) == expected, why


@pytest.mark.parametrize("bp,expected", [
    (-37, 1),    # 不到 1.5 碼，歸為降息1碼
    (-38, 2),    # 超過 1.5 碼，歸為降息2碼
    (-12, 0),    # 不到半碼，視為持平
    (-13, 1),
    (12, 0),
    (13, -1),
])
def test_continuous_basis_points_round_to_the_nearest_quarter_point(bp, expected):
    """規格是以「碼」描述的，−37bp 這種連續值必須先歸到最近的碼才有對應等級。"""
    assert fedwatch.score_implied_path(bp) == expected


def test_missing_value_is_unavailable_not_flat():
    assert fedwatch.score_implied_path(None) is None


# --- AI 判讀 ---------------------------------------------------------------

def test_reads_implied_rate_and_converts_to_change():
    path = fedwatch.implied_path_from_page(PAGE, current_rate_pct=3.875,
                                           api_key="k", session=_model(_reading()))
    assert path.implied_rate_pct == 3.375
    assert path.change_bp == pytest.approx(-50.0)      # 3.375 − 3.875 = −0.5%
    assert fedwatch.score_implied_path(path.change_bp) == 2
    assert path.basis == "fedwatch_ai"


def test_model_saying_it_cannot_read_the_page_is_not_treated_as_flat():
    """機率表由 JavaScript 載入時頁面只有框架。此時回報 0（持平）會是假訊號。"""
    with pytest.raises(ValueError, match="不足以判讀"):
        fedwatch.implied_path_from_page(PAGE, 3.875, api_key="k",
                                        session=_model(_reading(found=False)))


def test_fabricated_evidence_is_rejected():
    """佐證片段沒有出現在頁面裡，就是模型自己編的——不採信。"""
    with pytest.raises(ValueError, match="幻覺"):
        fedwatch.implied_path_from_page(
            PAGE, 3.875, api_key="k",
            session=_model(_reading(evidence="the probability of 250-275 is 88%")))


def test_reading_without_evidence_is_rejected():
    with pytest.raises(ValueError, match="未提供頁面原文佐證"):
        fedwatch.implied_path_from_page(PAGE, 3.875, api_key="k",
                                        session=_model(_reading(evidence="")))


@pytest.mark.parametrize("bad_rate", [62.4, -1.0, 250.0])
def test_out_of_range_rate_is_rejected_as_a_misread_column(bad_rate):
    """讀到 62.4 這種數字，多半是把「機率」當成「利率」——那會算出離譜的路徑。"""
    with pytest.raises(ValueError, match="不在合理範圍"):
        fedwatch.implied_path_from_page(
            PAGE, 3.875, api_key="k",
            session=_model(_reading(implied_rate_pct=bad_rate)))


# --- 抓不到頁面 -------------------------------------------------------------

def test_blocked_page_reports_status_codes():
    resp = mock.Mock(status_code=403, text="<html>denied</html>")
    session = mock.Mock(get=mock.Mock(return_value=resp))
    with pytest.raises(ValueError) as exc:
        fedwatch.fetch_fedwatch_page(session=session)
    msg = str(exc.value)
    assert "HTTP 403" in msg and "Cloudflare" in msg


def test_javascript_shell_page_is_treated_as_unusable():
    """只有框架、沒有實際內容的頁面不能拿去給模型讀——那正是幻覺的溫床。"""
    resp = mock.Mock(status_code=200, text="<html><body><div id='app'></div></body></html>")
    session = mock.Mock(get=mock.Mock(return_value=resp))
    with pytest.raises(ValueError, match="內容過短"):
        fedwatch.fetch_fedwatch_page(session=session)


# --- 公債近似備援 -----------------------------------------------------------

def test_treasury_proxy_is_labelled_as_an_approximation():
    """一年期公債含期限溢酬，不等於市場隱含路徑。畫面上必須看得出差別，
    否則使用者會以為那是 FedWatch 的數字。"""
    path = fedwatch.implied_path_from_treasury(one_year_yield_pct=3.40, current_rate_pct=3.875)
    assert path.change_bp == pytest.approx(-47.5)
    assert path.basis == "treasury_proxy"
    assert any("期限溢酬" in w for w in path.warnings)
    assert fedwatch.score_implied_path(path.change_bp) == 2
