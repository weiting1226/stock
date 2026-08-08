"""驗證會實際呼叫 pd.read_html() 的解析路徑不會回歸成「傳純字串」的寫法。

pandas>=2.1 起，pd.read_html() 不再接受純字串（會被誤判成檔案路徑，
拋出 FileNotFoundError，且錯誤訊息會夾帶整頁HTML/JS）。之前
finra_margin.py 與 ndx_breadth.py 都因為忘記包 io.StringIO() 而在
GitHub Actions 上實際壞掉（融資餘額年增率整項暫缺、NDX廣度整項暫缺），
但單元測試因為都直接 monkeypatch 掉 fetch_margin_debt/compute_breadth_200d
本身，從沒真正執行到 pd.read_html()，才會沒抓到。這裡改成 mock
requests.get()，讓解析邏輯本身被真正執行到。
"""
from __future__ import annotations

import io
from unittest import mock

import pandas as pd
import pytest

from liquidity_monitor.sources import finra_margin, ndx_breadth

# FINRA 實際使用 "Jun-25" 這種寫法。之前的 fixture 用 "June 2026"，剛好避開了
# pandas 把 "Jun-25" 解讀成公元1年的陷阱，導致測試綠燈但線上整項長期暫缺。
FINRA_FIXTURE_HTML = """
<html><body>
<table>
  <tr><th>Month/Year</th><th>Debit Balances in Margin Accounts</th><th>Free Credit Balances</th></tr>
  <tr><td>May-25</td><td>$780,123</td><td>$210,000</td></tr>
  <tr><td>Jun-25</td><td>$799,456</td><td>$215,000</td></tr>
</table>
</body></html>
"""

WIKI_FIXTURE_HTML = """
<html><body>
<table>
  <tr><th>Company</th><th>Ticker</th><th>GICS Sector</th></tr>
  <tr><td>Apple Inc.</td><td>AAPL</td><td>Technology</td></tr>
  <tr><td>Microsoft Corp.</td><td>MSFT</td><td>Technology</td></tr>
</table>
</body></html>
"""


def _mock_response(html: str):
    resp = mock.Mock()
    resp.text = html
    resp.raise_for_status = mock.Mock()
    return resp


def test_fetch_margin_debt_parses_real_html_via_requests():
    with mock.patch.object(finra_margin.requests, "get", return_value=_mock_response(FINRA_FIXTURE_HTML)):
        s = finra_margin.fetch_margin_debt("2025-01-01", "2025-12-31")
    assert len(s) == 2
    assert s.iloc[-1] == pytest.approx(799_456 * 1_000_000)
    # 索引必須是 2025 年的月底，不能是 pandas 誤判的公元 1 年
    assert [str(d.date()) for d in s.index] == ["2025-05-31", "2025-06-30"]


@pytest.mark.parametrize("raw,expected", [
    ("Jun-25", "2025-06-30"),     # FINRA 慣用格式，正是先前踩雷的那個
    ("Jun-2025", "2025-06-30"),
    ("June 2025", "2025-06-30"),
    ("2025-06", "2025-06-30"),
    ("06/2025", "2025-06-30"),
    ("Feb-24", "2024-02-29"),     # 閏年月底
])
def test_parse_period_handles_finra_date_formats(raw, expected):
    assert str(finra_margin._parse_period(raw).date()) == expected


@pytest.mark.parametrize("raw", ["", "nan", "Total", "—", None])
def test_parse_period_rejects_junk(raw):
    assert finra_margin._parse_period(raw) is None


def test_fetch_margin_debt_raises_instead_of_returning_empty_on_unknown_format():
    """解析不出日期時必須大聲報錯 —— 靜默回傳空序列正是先前查不出問題的原因。"""
    html = FINRA_FIXTURE_HTML.replace("May-25", "??").replace("Jun-25", "??")
    with mock.patch.object(finra_margin.requests, "get", return_value=_mock_response(html)):
        with pytest.raises(ValueError, match="沒有任何有效資料列"):
            finra_margin.fetch_margin_debt("2025-01-01", "2025-12-31")


def test_fetch_margin_debt_raises_when_all_data_outside_requested_range():
    with mock.patch.object(finra_margin.requests, "get", return_value=_mock_response(FINRA_FIXTURE_HTML)):
        with pytest.raises(ValueError, match="之外"):
            finra_margin.fetch_margin_debt("2030-01-01", "2030-12-31")


def _wiki_html(header: str, n: int = 60) -> str:
    """產生一個有 n 檔成分股的維基百科式表格，欄位標題可自訂。"""
    rows = "".join(
        f"<tr><td>Company {i}</td><td>{chr(65 + i // 26)}{chr(65 + i % 26)}X</td><td>Tech</td></tr>"
        for i in range(n)
    )
    return f"<html><body><table><tr><th>Company</th><th>{header}</th><th>Sector</th></tr>{rows}</table></body></html>"


def test_fetch_constituents_parses_real_html_via_requests(tmp_path):
    with mock.patch.object(ndx_breadth.requests, "get", return_value=_mock_response(_wiki_html("Ticker"))):
        tickers = ndx_breadth.fetch_constituents(cache_path=str(tmp_path / "ndx.json"))
    assert len(tickers) == 60
    assert "AAX" in tickers


def test_fetch_constituents_survives_header_rename_via_content_heuristic(tmp_path):
    """2026-08 線上實際故障：維基百科欄位標題不再叫 Ticker/Symbol，
    只靠標題比對就整項抓不到。改用內容形狀判斷後仍須成功。"""
    with mock.patch.object(ndx_breadth.requests, "get",
                           return_value=_mock_response(_wiki_html("Trading code"))):
        tickers = ndx_breadth.fetch_constituents(cache_path=str(tmp_path / "ndx2.json"))
    assert len(tickers) == 60


def test_extract_tickers_normalizes_dotted_symbols():
    html = "<html><body><table><tr><th>Ticker</th></tr>" + \
        "".join(f"<tr><td>{chr(65 + i // 26)}{chr(65 + i % 26)}X</td></tr>" for i in range(59)) + \
        "<tr><td>BRK.B</td></tr></table></body></html>"
    tables = pd.read_html(io.StringIO(html))
    tickers = ndx_breadth._extract_tickers(tables)
    assert "BRK-B" in tickers and "BRK.B" not in tickers


def test_extract_tickers_handles_duplicate_column_names():
    """維基百科表格常有重複欄名，此時 t[col] 回傳 DataFrame，
    迭代它拿到的是欄名而非儲存格內容，整欄會被誤判成只有兩三筆資料。"""
    rows = "".join(
        f"<tr><td>Co {i}</td><td>{chr(65 + i // 26)}{chr(65 + i % 26)}X</td></tr>" for i in range(60)
    )
    html = f"<html><table><tr><th>Ticker</th><th>Ticker</th></tr>{rows}</table></html>"
    tickers = ndx_breadth._extract_tickers(pd.read_html(io.StringIO(html)))
    assert len(tickers) == 60


@pytest.mark.parametrize("cell,expected", [
    ("AAPL[a]", "AAPL"),        # 維基百科註腳
    ("NASDAQ: AAPL", "AAPL"),   # 交易所前綴
    ("  MSFT  ", "MSFT"),
])
def test_clean_symbol_strips_wiki_noise(cell, expected):
    assert ndx_breadth._clean_symbol(cell) == expected


def test_extract_tickers_error_reports_actual_columns_for_diagnosis():
    """失敗訊息必須帶出實際欄位名稱與樣本，否則下次還是只能盲猜。"""
    html = "<html><table><tr><th>Note</th></tr>" + \
        "".join("<tr><td>long text here</td></tr>" for _ in range(60)) + "</table></html>"
    with pytest.raises(ValueError) as exc:
        ndx_breadth._extract_tickers(pd.read_html(io.StringIO(html)))
    msg = str(exc.value)
    assert "Note" in msg and "60列" in msg and "long text here" in msg


def test_extract_tickers_raises_when_no_plausible_column():
    html = "<html><body><table><tr><th>Note</th></tr>" + \
        "".join("<tr><td>some long descriptive text</td></tr>" for _ in range(60)) + \
        "</table></body></html>"
    tables = pd.read_html(io.StringIO(html))
    with pytest.raises(ValueError, match="無法從維基百科 Nasdaq-100"):
        ndx_breadth._extract_tickers(tables)


def test_find_margin_table_raises_clear_error_when_structure_changes():
    empty_table = pd.DataFrame({"foo": [1], "bar": [2]})
    with pytest.raises(ValueError, match="無法從 FINRA 頁面找到"):
        finra_margin._find_margin_table([empty_table])


# --- FOMC 聲明索引解析 ---------------------------------------------------

def _fed_index_html(*paths: str) -> str:
    links = "".join(f'<a href="{p}">statement</a>' for p in paths)
    return f"<html><body>{links}</body></html>"


def test_fomc_accepts_suffix_variants_and_absolute_urls():
    """聯準會聲明連結出現過 a / a1 等後綴，也可能是絕對網址。"""
    from liquidity_monitor.sources import fomc

    html = _fed_index_html(
        "/newsevents/pressreleases/monetary20260318a1.htm",
        "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm",
    )
    statement = mock.Mock(text="<p>voting for the action</p>", raise_for_status=mock.Mock())
    with mock.patch.object(fomc, "_fetch_index", return_value=html), \
         mock.patch.object(fomc.requests, "get", return_value=statement):
        meeting = fomc.latest_meeting_on_or_before("2026-08-08")

    assert str(meeting.date.date()) == "2026-07-29"      # 取最近一次
    assert meeting.statement_url.startswith("https://www.federalreserve.gov")
    assert meeting.has_dissent is False


def test_fomc_raises_with_diagnostics_instead_of_returning_none():
    """⑥ 佔30%權重，查無聲明必須帶診斷資訊報錯，不能靜默回傳 None。"""
    from liquidity_monitor.sources import fomc

    html = _fed_index_html("/newsevents/pressreleases/monetary-policy-something-else.htm")
    with mock.patch.object(fomc, "_fetch_index", return_value=html):
        with pytest.raises(ValueError, match="找不到符合格式的 FOMC 聲明連結"):
            fomc.latest_meeting_on_or_before("2026-08-08")


# --- QQQ 持股（NDX 成分股主來源）-----------------------------------------

QQQ_CSV = (
    "Fund Ticker,Holding Ticker,Name,Weight,Sector\n"
    + "".join(f"QQQ,{chr(65 + i // 26)}{chr(65 + i % 26)}X ,Company {i},0.5,Tech\n" for i in range(60))
    + "QQQ,-,US DOLLARS,0.1,Cash\n"          # 現金列必須被濾掉
)


def test_fetch_qqq_holdings_parses_csv_and_filters_non_tickers():
    with mock.patch.object(ndx_breadth.requests, "get", return_value=_mock_response(QQQ_CSV)):
        tickers = ndx_breadth.fetch_qqq_holdings()
    assert len(tickers) == 60          # 現金那列被排除
    assert "AAX" in tickers
    assert "-" not in tickers


def test_fetch_qqq_holdings_raises_when_too_few_rows():
    csv = "Fund Ticker,Holding Ticker\nQQQ,AAPL\nQQQ,MSFT\n"
    with mock.patch.object(ndx_breadth.requests, "get", return_value=_mock_response(csv)):
        with pytest.raises(ValueError, match="只解析出"):
            ndx_breadth.fetch_qqq_holdings()


def test_fetch_constituents_falls_back_to_wikipedia_when_qqq_fails(tmp_path):
    """QQQ 來源掛掉時要能退回維基百科，而不是整項消失。"""
    with mock.patch.object(ndx_breadth, "fetch_qqq_holdings", side_effect=RuntimeError("boom")), \
         mock.patch.object(ndx_breadth.requests, "get", return_value=_mock_response(_wiki_html("Ticker"))):
        tickers = ndx_breadth.fetch_constituents(cache_path=str(tmp_path / "c.json"))
    assert len(tickers) == 60


def test_fetch_constituents_reports_both_sources_when_all_fail(tmp_path):
    with mock.patch.object(ndx_breadth, "fetch_qqq_holdings", side_effect=RuntimeError("qqq down")), \
         mock.patch.object(ndx_breadth.requests, "get", side_effect=RuntimeError("wiki down")):
        with pytest.raises(ValueError) as exc:
            ndx_breadth.fetch_constituents(cache_path=str(tmp_path / "c2.json"))
    assert "qqq down" in str(exc.value) and "wiki down" in str(exc.value)


# Invesco 的下載檔在標題列前有數行說明文字，欄數不一致，
# 直接 pd.read_csv 會拋 "Expected 1 fields in line 11, saw 13"（線上實際發生）。
QQQ_CSV_WITH_PREAMBLE = (
    "Invesco QQQ Trust, Series 1\n"
    "Holdings as of 08/07/2026\n"
    "This file is provided for informational purposes\n"
    "\n"
    + QQQ_CSV
)


def test_read_holdings_csv_skips_invesco_preamble():
    df = ndx_breadth._read_holdings_csv(QQQ_CSV_WITH_PREAMBLE)
    assert "Holding Ticker" in [c.strip() for c in df.columns]
    assert len(df) == 61  # 60 檔 + 1 列現金


def test_fetch_qqq_holdings_works_with_preamble():
    with mock.patch.object(ndx_breadth.requests, "get",
                           return_value=_mock_response(QQQ_CSV_WITH_PREAMBLE)):
        tickers = ndx_breadth.fetch_qqq_holdings()
    assert len(tickers) == 60
    assert "AAX" in tickers


def test_read_holdings_csv_reports_content_when_no_header_found():
    with pytest.raises(ValueError, match="找不到標題列"):
        ndx_breadth._read_holdings_csv("just one line\nanother line\n")
