"""機構名稱正規化與逐機構去重的測試。

「按照分析機構作為價格的主要來源分類去除重複」的正確性全靠這一層：
同一家券商若因寫法不同被當成多家，平均值就會被重複計票的機構拉偏。
"""
from __future__ import annotations

import pytest

from analyst_valuation.firms import (
    TargetRecord,
    build_firm_consensus,
    dedupe_by_firm,
    normalize_firm,
)


@pytest.mark.parametrize("raw,expected", [
    ("J.P. Morgan Securities LLC", "jp morgan"),
    ("JPMorgan Chase & Co.", "jp morgan"),
    ("JP Morgan", "jp morgan"),
    ("Goldman Sachs Group Inc", "goldman sachs"),
    ("Goldman Sachs & Co.", "goldman sachs"),
    ("Morgan Stanley", "morgan stanley"),
    ("Morgan Stanley & Co. LLC", "morgan stanley"),
    ("BofA Securities", "bank of america"),
    ("Merrill Lynch", "bank of america"),
    ("RBC Capital Markets", "rbc"),
    ("UBS Group AG", "ubs"),
    ("Wells Fargo Securities, LLC", "wells fargo"),
])
def test_normalize_firm_collapses_known_variants(raw, expected):
    assert normalize_firm(raw) == expected


def test_normalize_firm_handles_empty():
    assert normalize_firm(None) == ""
    assert normalize_firm("") == ""


def test_normalize_firm_keeps_name_that_is_all_noise_words():
    """整串都是修飾詞時不能變成空鍵，否則會跟其他空鍵誤合併。"""
    assert normalize_firm("Capital Group") != ""


def _rec(firm, target, source="fmp", published="2026-08-01"):
    return TargetRecord(ticker="T", firm=firm, target=target, source=source, published=published)


def test_dedupe_keeps_one_record_per_firm():
    records = [
        _rec("J.P. Morgan Securities LLC", 200.0),
        _rec("JPMorgan Chase & Co.", 210.0, source="other"),  # 同一家，不同寫法
        _rec("Goldman Sachs", 180.0),
    ]
    unique = dedupe_by_firm(records)
    assert len(unique) == 2
    assert {r.firm_key for r in unique} == {"jp morgan", "goldman sachs"}


def test_dedupe_prefers_most_recent_report():
    records = [
        _rec("Goldman Sachs", 180.0, published="2026-01-01"),
        _rec("Goldman Sachs", 220.0, published="2026-08-01"),
    ]
    unique = dedupe_by_firm(records)
    assert len(unique) == 1
    assert unique[0].target == 220.0


def test_dedupe_drops_invalid_targets():
    records = [_rec("A Firm", 0), _rec("B Firm", -5), _rec("C Firm", 100.0)]
    assert len(dedupe_by_firm(records)) == 1


def test_build_firm_consensus_computes_own_statistics():
    records = [
        _rec("Goldman Sachs", 100.0),
        _rec("Morgan Stanley", 200.0),
        _rec("UBS Group AG", 300.0),
    ]
    c = build_firm_consensus(records)
    assert c.firm_count == 3
    assert c.mean == 200.0        # 自行平均，非沿用任何資料源的共識值
    assert c.median == 200.0
    assert c.high == 300.0 and c.low == 100.0
    assert c.duplicates_removed == 0


def test_build_firm_consensus_reports_duplicates_removed():
    records = [
        _rec("J.P. Morgan Securities LLC", 100.0),
        _rec("JPMorgan Chase & Co.", 400.0, published="2026-08-02"),
        _rec("Goldman Sachs", 200.0),
    ]
    c = build_firm_consensus(records)
    assert c.firm_count == 2
    assert c.duplicates_removed == 1
    # 重複的 JP Morgan 只採計最新那筆(400)，平均應為 (400+200)/2
    assert c.mean == 300.0


def test_build_firm_consensus_even_count_median():
    c = build_firm_consensus([_rec("A Firm", 100.0), _rec("B Firm", 200.0)])
    assert c.median == 150.0


def test_build_firm_consensus_empty():
    c = build_firm_consensus([])
    assert c.firm_count == 0 and c.mean is None
