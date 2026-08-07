"""模組二測試：全部使用合成資料，不需網路連線。

重點驗證：
1. 多來源共識價確實取「平均」，且 sources_used 誠實反映實際取得的來源。
2. 缺目標價／缺收盤價時標記暫缺，不臆測填補。
3. upside_pct 計算方向正確（目標價 > 現價 → 正值 → 相對低估）。
4. read_html 解析路徑真的被執行到（模組一踩過的坑，見 test_html_parsers.py）。
"""
from __future__ import annotations

from unittest import mock

import pandas as pd
import pytest

from analyst_valuation import aggregate, pipeline, storage
from analyst_valuation.sources import universe
from analyst_valuation.sources.prices import PriceSnapshot
from analyst_valuation.sources.yahoo_targets import TargetQuote

SP500_FIXTURE_HTML = """
<html><body>
<table>
  <tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th></tr>
  <tr><td>AAPL</td><td>Apple Inc.</td><td>Information Technology</td><td>Technology Hardware</td></tr>
  <tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td><td>Multi-Sector Holdings</td></tr>
  <tr><td>XOM</td><td>Exxon Mobil</td><td>Energy</td><td>Integrated Oil &amp; Gas</td></tr>
</table>
</body></html>
"""


def _quote(source, mean, count=10, **kw):
    return TargetQuote(ticker="TEST", source=source, mean=mean, analyst_count=count, **kw)


# --- universe 解析 --------------------------------------------------------

def test_parse_sp500_table_extracts_sector_and_normalizes_ticker():
    rows = universe._parse_sp500_table(SP500_FIXTURE_HTML)
    assert len(rows) == 3
    by_ticker = {r["ticker"]: r for r in rows}
    assert by_ticker["AAPL"]["sector"] == "Information Technology"
    # 維基百科的 BRK.B 必須轉成 Yahoo 用的 BRK-B，否則抓不到價格
    assert "BRK-B" in by_ticker
    assert by_ticker["BRK-B"]["sector"] == "Financials"


def test_fetch_sp500_universe_uses_requests_and_parses(tmp_path):
    resp = mock.Mock(text=SP500_FIXTURE_HTML, raise_for_status=mock.Mock())
    with mock.patch.object(universe.requests, "get", return_value=resp):
        rows = universe.fetch_sp500_universe(cache_path=str(tmp_path / "u.json"))
    assert {r["ticker"] for r in rows} == {"AAPL", "BRK-B", "XOM"}
    assert (tmp_path / "u.json").exists()  # 快取有寫出


def test_parse_sp500_table_raises_when_structure_changes():
    with pytest.raises(ValueError, match="無法從維基百科 S&P 500"):
        universe._parse_sp500_table("<html><table><tr><th>foo</th></tr></table></html>")


# --- aggregate 核心計算 ---------------------------------------------------

def test_consensus_is_mean_of_sources_and_upside_is_positive_when_undervalued():
    meta = {"ticker": "TEST", "name": "Test Co", "sector": "Energy"}
    price = PriceSnapshot(ticker="TEST", close=100.0, close_date="2026-08-07",
                          change_1w_pct=1.5, change_1m_pct=-3.0)
    row = aggregate.build_row(meta, price, [_quote("yahoo", 120.0), _quote("finnhub", 140.0)])

    assert row.consensus_target == 130.0          # (120+140)/2
    assert row.upside_pct == 30.0                 # (130-100)/100
    assert row.sources_used == ["yahoo", "finnhub"]
    assert row.source_targets == {"yahoo": 120.0, "finnhub": 140.0}
    assert row.confidence == "高"                  # >=5位分析師 且 2個來源
    assert row.change_1w_pct == 1.5 and row.change_1m_pct == -3.0


def test_single_source_does_not_pretend_to_be_averaged():
    meta = {"ticker": "TEST", "sector": "Energy"}
    price = PriceSnapshot(ticker="TEST", close=50.0)
    row = aggregate.build_row(meta, price, [_quote("yahoo", 60.0)])
    assert row.consensus_target == 60.0
    assert row.sources_used == ["yahoo"]
    assert row.confidence == "中"  # 家數夠但只有單一來源


def test_overvalued_stock_gets_negative_upside():
    row = aggregate.build_row(
        {"ticker": "T"}, PriceSnapshot(ticker="T", close=200.0), [_quote("yahoo", 150.0)]
    )
    assert row.upside_pct == -25.0


def test_missing_target_marks_unavailable_without_guessing():
    row = aggregate.build_row(
        {"ticker": "T"}, PriceSnapshot(ticker="T", close=100.0),
        [TargetQuote(ticker="T", source="yahoo", error="no data")],
    )
    assert row.consensus_target is None
    assert row.upside_pct is None
    assert row.confidence == "暫缺"
    assert any("yahoo" in n for n in row.notes)


def test_missing_price_marks_unavailable():
    row = aggregate.build_row(
        {"ticker": "T"}, PriceSnapshot(ticker="T", error="無收盤價資料"), [_quote("yahoo", 60.0)]
    )
    assert row.upside_pct is None
    assert row.confidence == "暫缺"


def test_low_analyst_count_downgrades_confidence():
    row = aggregate.build_row(
        {"ticker": "T"}, PriceSnapshot(ticker="T", close=100.0), [_quote("yahoo", 120.0, count=2)]
    )
    assert row.confidence == "低"
    assert row.upside_pct == 20.0


def test_implausible_target_is_flagged_but_kept():
    row = aggregate.build_row(
        {"ticker": "T"}, PriceSnapshot(ticker="T", close=1.0), [_quote("yahoo", 500.0)]
    )
    assert row.upside_pct is not None
    assert any("疑似資料異常" in n for n in row.notes)


def test_sector_summary_ranks_by_median_upside():
    rows = [
        aggregate.build_row({"ticker": "A", "sector": "Energy"},
                            PriceSnapshot(ticker="A", close=100.0), [_quote("yahoo", 150.0)]),
        aggregate.build_row({"ticker": "B", "sector": "Energy"},
                            PriceSnapshot(ticker="B", close=100.0), [_quote("yahoo", 130.0)]),
        aggregate.build_row({"ticker": "C", "sector": "Utilities"},
                            PriceSnapshot(ticker="C", close=100.0), [_quote("yahoo", 105.0)]),
        # 無目標價者不應影響類股統計
        aggregate.build_row({"ticker": "D", "sector": "Utilities"},
                            PriceSnapshot(ticker="D", close=100.0), []),
    ]
    summary = aggregate.sector_summary(rows)
    assert summary[0]["sector"] == "Energy"
    assert summary[0]["count"] == 2
    assert summary[1]["sector"] == "Utilities"
    assert summary[1]["count"] == 1


# --- pipeline 端對端 ------------------------------------------------------

@pytest.fixture
def _patched_pipeline(monkeypatch, tmp_path):
    consts = [
        {"ticker": "AAA", "name": "Alpha", "sector": "Energy", "sub_industry": "Oil"},
        {"ticker": "BBB", "name": "Beta", "sector": "Utilities", "sub_industry": "Electric"},
    ]
    monkeypatch.setattr(universe, "fetch_sp500_universe", lambda **kw: consts)
    monkeypatch.setattr(pipeline.universe, "fetch_sp500_universe", lambda **kw: consts)
    monkeypatch.setattr(pipeline.prices, "fetch_price_snapshots", lambda tickers, **kw: {
        "AAA": PriceSnapshot("AAA", close=100.0, close_date="2026-08-07",
                             change_1w_pct=2.0, change_1m_pct=5.0),
        "BBB": PriceSnapshot("BBB", close=80.0, close_date="2026-08-07",
                             change_1w_pct=-1.0, change_1m_pct=-4.0),
    })
    monkeypatch.setattr(pipeline, "fetch_yahoo_target",
                        lambda t: _quote("yahoo", 120.0 if t == "AAA" else 70.0))
    monkeypatch.setattr(pipeline.finnhub_targets, "is_enabled", lambda key=None: False)
    return tmp_path


def test_pipeline_end_to_end(_patched_pipeline):
    report = pipeline.run(as_of="2026-08-07", universe_cache_path=str(_patched_pipeline / "u.json"))

    assert report["as_of"] == "2026-08-07"
    assert report["counts"]["universe"] == 2
    assert report["counts"]["with_target_and_price"] == 2
    assert report["sources_available"] == ["yahoo"]
    assert report["finnhub_enabled"] is False

    by_ticker = {r["ticker"]: r for r in report["rows"]}
    assert by_ticker["AAA"]["upside_pct"] == 20.0    # 120 vs 100
    assert by_ticker["BBB"]["upside_pct"] == -12.5   # 70 vs 80
    assert by_ticker["AAA"]["sector"] == "Energy"


def test_storage_roundtrip(_patched_pipeline, tmp_path):
    report = pipeline.run(as_of="2026-08-07", universe_cache_path=str(_patched_pipeline / "u.json"))
    root = tmp_path / "out"
    storage.save_report(report, data_root=str(root))

    assert (root / "latest.json").exists()
    assert (root / "snapshots" / "2026-08-07.json").exists()

    hist = pd.read_csv(root / "valuation_history.csv")
    assert len(hist) == 1
    assert hist.iloc[0]["covered_count"] == 2
    assert hist.iloc[0]["pct_undervalued"] == 50.0  # 2檔中1檔為正
