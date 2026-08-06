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

from unittest import mock

import pandas as pd
import pytest

from liquidity_monitor.sources import finra_margin, ndx_breadth

FINRA_FIXTURE_HTML = """
<html><body>
<table>
  <tr><th>Month/Year</th><th>Debit Balances in Margin Accounts</th><th>Free Credit Balances</th></tr>
  <tr><td>May 2026</td><td>$780,123</td><td>$210,000</td></tr>
  <tr><td>June 2026</td><td>$799,456</td><td>$215,000</td></tr>
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
        s = finra_margin.fetch_margin_debt("2026-01-01", "2026-12-31")
    assert len(s) == 2
    assert s.iloc[-1] == pytest.approx(799_456 * 1_000_000)


def test_fetch_constituents_parses_real_html_via_requests(tmp_path):
    with mock.patch.object(ndx_breadth.requests, "get", return_value=_mock_response(WIKI_FIXTURE_HTML)):
        tickers = ndx_breadth.fetch_constituents(cache_path=str(tmp_path / "ndx.json"))
    assert "AAPL" in tickers
    assert "MSFT" in tickers


def test_find_margin_table_raises_clear_error_when_structure_changes():
    empty_table = pd.DataFrame({"foo": [1], "bar": [2]})
    with pytest.raises(ValueError, match="無法從 FINRA 頁面找到"):
        finra_margin._find_margin_table([empty_table])
