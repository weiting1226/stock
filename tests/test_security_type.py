"""證券種類分類：決定哪些標的要納入抓取。

這裡的每一個案例都來自實際誤判，不是想像出來的：分類寫錯會**靜悄悄地**
把真實公司排除在抓取範圍外——沒有錯誤訊息，只是那檔從此不再出現，
沒人會發現。因此每修正一次就在這裡釘一次。
"""
from __future__ import annotations

import pytest

from analyst_valuation.security_type import (
    COMMON,
    NOTE,
    PREFERRED,
    RIGHT,
    UNIT,
    WARRANT,
    classify_security,
    is_operating_company,
)


@pytest.mark.parametrize("ticker,name", [
    ("AAPL", "Apple Inc. - Common Stock"),
    ("BRK-B", "Berkshire Hathaway Inc. Class B"),
    ("MSFT", "Microsoft Corporation - Common Stock"),
])
def test_plain_common_stock(ticker, name):
    assert classify_security(ticker, name) == COMMON


@pytest.mark.parametrize("ticker,name", [
    # 存託憑證「代表」什麼與它自己的種類無關。第一版把括號裡的描述當成種類，
    # 於是這些大型 ADR 全被濾掉。
    ("ITUB", "Itau Unibanco Banco Holding SA American Depositary Shares (Each repstg 500 Preferred shares)"),
    ("AMX", "America Movil, S.A.B. de C.V. American Depositary Shares (each representing the right to receive twenty (20) Series B Shares"),
    ("KOF", "Coca Cola Femsa S.A.B. de C.V.  American Depositary Shares, each representing 10 Units (each Unit consists of 3 Series B Shares)"),
    ("BSBR", "Banco Santander Brasil SA American Depositary Shares, each representing one unit"),
    ("AVAL", "Grupo Aval Acciones y Valores S.A. ADR (Each representing 20 preferred shares)"),
    ("IRS", "IRSA Inversiones Y Representaciones S.A. Global Depositary Shares (Each representing ten shares of Common Stock)"),
])
def test_depositary_receipts_are_ordinary_equity(ticker, name):
    """ADR／GDR 是外國公司的普通權益，有真實分析師覆蓋，不得被當成特別股或單位。"""
    assert classify_security(ticker, name) == COMMON


@pytest.mark.parametrize("ticker,name", [
    # 合夥事業的權益就叫 Units，它們是真實營運公司而不是 SPAC 認購單位
    ("AB", "AllianceBernstein Holding L.P.  Units"),
    ("ARLP", "Alliance Resource Partners, L.P. - Common Units Representing Limited Partner Interests"),
    ("BEP", "Brookfield Renewable Partners L.P. Limited Partnership Units"),
    ("KNOP", "KNOT Offshore Partners LP Common Units representing Limited Partner Interests"),
])
def test_partnership_units_are_operating_companies(ticker, name):
    assert classify_security(ticker, name) == COMMON


def test_company_name_containing_bond_is_not_a_bond():
    """"Our Bond, Inc." 是公司名稱，不是債券——明寫的證券形式要壓過它。"""
    assert classify_security("OBAI", "Our Bond, Inc. - Common Stock") == COMMON


def test_preferred_ticker_suffix_beats_the_name():
    """交易所的 $ 後綴（正規化為 -PX）是最硬的證據。

    GLOP-PA 的名稱沒有出現 Preferred 字樣，只靠名稱會被當成合夥單位。
    """
    assert classify_security(
        "GLOP-PA",
        "GasLog Partners LP 8.625% Series A Cumulative Redeemable Perpetual Fixed to Floating Rate",
    ) == PREFERRED


@pytest.mark.parametrize("ticker,name,expected", [
    ("FGIIW", "FG Imperii Acquisition Corp. - Warrants", WARRANT),
    ("CTAAU", "ClearThink 1 Acquisition Corp. - Units", UNIT),
    ("FSHPR", "Flag Ship Acquisition Corp. - Right", RIGHT),
    ("CIMO", "Chimera Investment Corporation 9.250% Senior Notes due 2029", NOTE),
    ("GOODO", "Gladstone Commercial Corporation - 6.00% Series G Cumulative Redeemable Preferred Stock", PREFERRED),
    ("FCNCN", "First Citizens BancShares, Inc. - Depositary Shares, each representing a 1/100th interest in a share of Series C Preferred", PREFERRED),
])
def test_non_operating_securities(ticker, name, expected):
    assert classify_security(ticker, name) == expected
    assert not is_operating_company(expected)


def test_only_common_is_fetched_by_default():
    assert is_operating_company(COMMON)
    for t in (PREFERRED, WARRANT, UNIT, RIGHT, NOTE):
        assert not is_operating_company(t)


def test_missing_name_does_not_crash():
    assert classify_security("AAA", "") == COMMON
    assert classify_security("AAA", None) == COMMON
