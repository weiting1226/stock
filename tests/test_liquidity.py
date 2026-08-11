"""資料來源的歷史長度與失敗診斷。

這一檔守的不是「抓不抓得到」，而是「抓得到最新值不代表抓得到歷史」——
實測 FRED 對 BAMLH0A0HYM2 只回傳近三年，最新值正確、計分照常運作，
只有回測缺了 80% 的期間，而畫面上完全看不出來。
"""
from __future__ import annotations

import pytest




# --- 資料歷史長度：抓得到最新值不代表抓得到歷史 -----------------------------
#
# 實測 2026-08：FRED 對 BAMLH0A0HYM2 只回傳近三年，對 DGS10 卻正常給滿。
# 最新值是對的、計分照常運作，只有回測會缺 80% 的期間——而畫面上完全看不出來。

def test_fred_falls_back_to_another_endpoint_when_history_is_truncated():
    from unittest import mock
    from liquidity_monitor.sources import fred

    short = "DATE,BAMLH0A0HYM2\n2023-08-11,3.83\n2023-08-14,3.79\n"
    full = "".join(["DATE,BAMLH0A0HYM2\n"]
                   + [f"20{y:02d}-01-02,3.5\n" for y in range(10, 27)])

    def get(url, params=None, timeout=None):
        r = mock.Mock(status_code=200)
        r.raise_for_status = lambda: None
        r.text = short if (params or {}).get("cosd") else full
        return r

    with mock.patch.object(fred._SESSION, "get", side_effect=get):
        s = fred.fetch_fred_series("BAMLH0A0HYM2", "2010-01-01", "2026-08-10")
    assert s.index.min().year == 2010, "帶區間的端點被截斷時要改用完整端點"


def test_fred_keeps_the_ranged_endpoint_when_it_is_not_truncated():
    """沒問題的序列不該多打兩次網路請求。"""
    from unittest import mock
    from liquidity_monitor.sources import fred

    calls = []

    def get(url, params=None, timeout=None):
        calls.append(params)
        r = mock.Mock(status_code=200)
        r.raise_for_status = lambda: None
        r.text = "DATE,DGS10\n2010-01-04,3.85\n2026-08-07,4.20\n"
        return r

    with mock.patch.object(fred._SESSION, "get", side_effect=get):
        fred.fetch_fred_series("DGS10", "2010-01-01", "2026-08-10")
    assert len(calls) == 1


def test_fred_diagnostics_record_every_endpoint_tried():
    """要分得出「來源只給得出這麼多」與「我們少帶了參數」，就得留下每個端點的結果。"""
    from unittest import mock
    from liquidity_monitor.sources import fred

    def get(url, params=None, timeout=None):
        r = mock.Mock(status_code=200)
        r.raise_for_status = lambda: None
        r.text = "DATE,X\n2024-01-02,1.0\n"       # 三個端點都只給得出近期
        return r

    diag = fred.FetchDiagnostics("X", "2010-01-01")
    with mock.patch.object(fred._SESSION, "get", side_effect=get):
        fred.fetch_fred_series("X", "2010-01-01", "2026-08-10", diagnostics=diag)
    assert [a["endpoint"] for a in diag.attempts] == ["csv_with_range", "csv_full", "txt"]
    assert all(a["start"] == "2024-01-02" for a in diag.attempts if a["rows"])


def test_fred_text_endpoint_parsing_skips_the_metadata_header():
    from liquidity_monitor.sources import fred
    text = (
        "Title:               ICE BofA US High Yield Index Option-Adjusted Spread\n"
        "Source:              ICE Data Indices, LLC\n"
        "\nDATE          VALUE\n"
        "1996-12-31     3.05\n"
        "1997-01-01        .\n"          # FRED 用 "." 表示缺值
        "1997-01-02     3.08\n"
    )
    s = fred._parse_txt(text, "BAMLH0A0HYM2")
    assert len(s) == 2
    assert str(s.index.min().date()) == "1996-12-31"


def test_finra_merges_the_history_file_with_the_page_table():
    """頁面表格只有近十來個月，歷史檔才有十五年。兩邊都要。"""
    from unittest import mock
    from liquidity_monitor.sources import finra_margin as fm

    page = ('<a href="/sites/default/files/margin-statistics.csv">Historical margin data</a>'
            '<table><tr><th>Month/Year</th><th>Debit Balances</th></tr>'
            '<tr><td>Jun-26</td><td>1,100,000</td></tr></table>')
    hist = "Month/Year,Debit Balances\nJan-12,300000\nFeb-12,310000\n"

    def get(url, headers=None, timeout=None):
        r = mock.Mock(status_code=200)
        r.raise_for_status = lambda: None
        r.text = page
        r.content = hist.encode()
        return r

    with mock.patch.object(fm.requests, "get", side_effect=get):
        s = fm.fetch_margin_debt("2010-01-01", "2026-12-31")
    assert len(s) == 3
    assert str(s.index.min().date()) == "2012-01-31"


def test_finra_still_works_when_the_history_file_is_missing():
    """歷史檔抓不到時仍要回傳頁內表格，不能整個失敗。"""
    from unittest import mock
    from liquidity_monitor.sources import finra_margin as fm

    page = ('<table><tr><th>Month/Year</th><th>Debit Balances</th></tr>'
            '<tr><td>Jun-26</td><td>1,100,000</td></tr></table>')

    def get(url, headers=None, timeout=None):
        r = mock.Mock(status_code=200)
        r.raise_for_status = lambda: None
        r.text = page
        return r

    with mock.patch.object(fm.requests, "get", side_effect=get):
        s = fm.fetch_margin_debt("2010-01-01", "2026-12-31")
    assert len(s) == 1


def test_fred_text_endpoint_reports_html_responses_instead_of_a_blank_failure():
    """實測第一版只回報「沒有解析出任何資料列」，三條序列全都這樣——
    完全看不出是格式跟我想的不一樣，還是根本拿到一頁 HTML。
    診斷訊息說不出原因，就等於沒有診斷。"""
    from liquidity_monitor.sources import fred
    with pytest.raises(ValueError, match="回傳 HTML"):
        fred._parse_txt("<!DOCTYPE html><html><body>Page not found</body></html>", "X")


def test_fred_text_parse_failure_includes_a_sample_of_the_response():
    from liquidity_monitor.sources import fred
    with pytest.raises(ValueError, match="回應開頭"):
        fred._parse_txt("something entirely unexpected\nno dates here\n", "X")
