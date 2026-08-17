"""模組二測試：全部使用合成資料，不需網路連線。

重點驗證：
1. 多來源共識價確實取「平均」，且 sources_used 誠實反映實際取得的來源。
2. 缺目標價／缺收盤價時標記暫缺，不臆測填補。
3. upside_pct 計算方向正確（目標價 > 現價 → 正值 → 相對低估）。
4. read_html 解析路徑真的被執行到（模組一踩過的坑，見 test_html_parsers.py）。
"""
from __future__ import annotations

import json
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


def test_dispersion_and_range_are_reported():
    row = aggregate.build_row(
        {"ticker": "T"}, PriceSnapshot(ticker="T", close=100.0),
        [TargetQuote(ticker="T", source="yahoo", mean=120.0, median=118.0,
                     high=180.0, low=60.0, analyst_count=25)],
    )
    assert row.target_high == 180.0
    assert row.target_low == 60.0
    assert row.analyst_count == 25
    # (180-60)/120 = 100%
    assert row.target_dispersion_pct == 100.0
    assert row.range_source == "yahoo"


def test_dispersion_uses_one_source_for_numerator_and_denominator():
    """離散度的高低價與平均必須同源，否則等於拿A的區間除以A+B的混合平均。"""
    yahoo = TargetQuote(ticker="T", source="yahoo", mean=100.0, high=150.0, low=50.0, analyst_count=20)
    finnhub = TargetQuote(ticker="T", source="finnhub", mean=200.0, high=260.0, low=140.0, analyst_count=9)
    row = aggregate.build_row({"ticker": "T"}, PriceSnapshot(ticker="T", close=100.0), [yahoo, finnhub])

    assert row.consensus_target == 150.0   # 共識價仍是兩來源平均
    assert row.range_source == "yahoo"
    # 離散度用 Yahoo 自己的 (150-50)/100 = 100%，不是拿 Yahoo 區間除以 150
    assert row.target_dispersion_pct == 100.0


def test_range_falls_back_to_source_that_has_high_low():
    """Yahoo 沒有高低價時，改用有提供區間的來源，而不是留空。"""
    yahoo = TargetQuote(ticker="T", source="yahoo", mean=100.0, analyst_count=20)
    finnhub = TargetQuote(ticker="T", source="finnhub", mean=110.0, high=130.0, low=90.0)
    row = aggregate.build_row({"ticker": "T"}, PriceSnapshot(ticker="T", close=100.0), [yahoo, finnhub])
    assert row.range_source == "finnhub"
    assert row.target_dispersion_pct == pytest.approx((130 - 90) / 110 * 100, abs=0.01)


def test_dispersion_absent_when_no_high_low():
    row = aggregate.build_row(
        {"ticker": "T"}, PriceSnapshot(ticker="T", close=100.0),
        [TargetQuote(ticker="T", source="yahoo", mean=120.0, analyst_count=8)],
    )
    assert row.target_dispersion_pct is None
    assert row.target_high is None
    assert row.upside_pct == 20.0  # 沒有區間不影響上漲空間的計算


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


# --- 逐機構模式（per_firm basis）------------------------------------------

def _frec(firm, target, source="fmp", published="2026-08-01"):
    from analyst_valuation.firms import TargetRecord
    return TargetRecord(ticker="TEST", firm=firm, target=target, source=source, published=published)


def test_per_firm_basis_computes_own_average_and_overrides_consensus():
    """有逐機構資料時，必須自行平均，不能沿用資料源的共識值。"""
    meta = {"ticker": "TEST", "sector": "Energy"}
    price = PriceSnapshot(ticker="TEST", close=100.0)
    # 資料源共識價說 999（故意與逐機構資料不同），逐機構平均應為 200
    consensus = [_quote("yahoo", 999.0, count=50)]
    firms = [_frec("Goldman Sachs", 100.0), _frec("Morgan Stanley", 200.0), _frec("UBS", 300.0)]

    row = aggregate.build_row(meta, price, consensus, firms)

    assert row.basis == "per_firm"
    assert row.consensus_target == 200.0      # 自行平均，不是 999
    assert row.upside_pct == 100.0            # (200-100)/100
    assert row.analyst_count == 3             # 去重後的機構數
    assert row.target_high == 300.0 and row.target_low == 100.0
    assert len(row.firm_targets) == 3
    assert row.sources_used == ["fmp"]


def test_per_firm_dedup_is_reflected_in_row():
    meta = {"ticker": "TEST"}
    price = PriceSnapshot(ticker="TEST", close=100.0)
    firms = [
        _frec("J.P. Morgan Securities LLC", 100.0),
        _frec("JPMorgan Chase & Co.", 300.0, published="2026-08-02"),
        _frec("Goldman Sachs", 200.0),
    ]
    row = aggregate.build_row(meta, price, [], firms)
    assert row.analyst_count == 2          # JP Morgan 只算一次
    assert row.duplicates_removed == 1
    assert row.consensus_target == 250.0   # (300+200)/2


def test_falls_back_to_consensus_when_no_firm_records():
    row = aggregate.build_row(
        {"ticker": "TEST"}, PriceSnapshot(ticker="TEST", close=100.0),
        [_quote("yahoo", 120.0)], firm_records=[],
    )
    assert row.basis == "consensus"
    assert row.consensus_target == 120.0


def test_per_firm_confidence_high_without_needing_two_sources():
    """逐機構資料可逐筆驗證且已去重，單一來源也能給高信心。"""
    firms = [_frec(f"Firm {i}", 100.0 + i) for i in range(8)]
    row = aggregate.build_row({"ticker": "T"}, PriceSnapshot(ticker="T", close=50.0), [], firms)
    assert row.basis == "per_firm"
    assert row.confidence == "高"


# --- 憑證遮蔽 -------------------------------------------------------------

def test_redact_secrets_strips_query_credentials():
    """Finnhub 把金鑰放在 query string，例外訊息含完整URL；
    2026-08 曾因此把金鑰 commit 進公開 repo。"""
    from analyst_valuation.secrets_redaction import redact_secrets
    leaked = ("HTTPError: 429 Client Error for url: "
              "https://finnhub.io/api/v1/price-target?symbol=ADBE&token=d9r720pr01qnlhclnung")
    out = redact_secrets(leaked)
    assert "d9r720pr01qnlhclnung" not in out
    assert "***REDACTED***" in out
    assert "finnhub.io" in out        # 其餘診斷資訊必須保留


@pytest.mark.parametrize("raw,secret", [
    ("https://x.com/a?apikey=SECRET123&s=AAPL", "SECRET123"),
    ("https://x.com/a?api_key=SECRET123", "SECRET123"),
    ("https://x.com/a?key=SECRET123", "SECRET123"),
    ("Authorization: Bearer SECRET123", "SECRET123"),
    ("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"),
])
def test_redact_secrets_covers_common_credential_shapes(raw, secret):
    from analyst_valuation.secrets_redaction import redact_secrets
    assert secret not in redact_secrets(raw)


@pytest.mark.parametrize("text", [
    "Basic Materials",          # 類股名稱：實際被寫成「Basic ***REDACTED***」存進資料檔
    "Basic Industries",
    "Bearer Corporation",
])
def test_redact_secrets_does_not_eat_ordinary_english(text):
    """遮蔽做過頭一樣是損壞資料，而且壞得更安靜：外洩看得出來，被吃掉的字沒人會發現。"""
    from analyst_valuation.secrets_redaction import redact_secrets
    assert redact_secrets(text) == text


def test_storage_redacts_before_writing(tmp_path):
    """就算某個來源忘了遮蔽，落地前也必須再擋一次。"""
    from analyst_valuation import storage
    report = {
        "as_of": "2026-08-08",
        "counts": {"universe": 1, "with_target_and_price": 0},
        "sources_available": [],
        "rows": [{"ticker": "T", "upside_pct": None,
                  "notes": ["HTTPError for url: https://finnhub.io/x?token=LEAKEDKEY123"]}],
    }
    storage.save_report(report, data_root=str(tmp_path))
    written = (tmp_path / "latest.json").read_text()
    assert "LEAKEDKEY123" not in written
    assert "***REDACTED***" in written


# --- 淨值篩選 ---------------------------------------------------------------

def _bv_quote(**kw):
    from analyst_valuation.sources.yahoo_targets import TargetQuote
    base = dict(ticker="AAA", source="yahoo", mean=120.0, analyst_count=8, responded=True)
    base.update(kw)
    return TargetQuote(**base)


def _bv_price(close):
    from analyst_valuation.sources.prices import PriceSnapshot
    return PriceSnapshot(ticker="AAA", close=close, close_date="2026-08-07")


def test_price_to_book_uses_our_own_close_not_the_sources_ratio():
    """分子分母必須同一時點。用資料源現成的比值，會與畫面上的收盤價對不起來。"""
    from analyst_valuation.aggregate import build_row
    row = build_row({"ticker": "AAA"}, _bv_price(50.0), [_bv_quote(book_value=25.0)])
    assert row.book_value == 25.0
    assert row.price_to_book == 2.0          # 50 / 25，用的是我們自己的收盤價


def test_price_below_book_is_representable():
    from analyst_valuation.aggregate import build_row
    row = build_row({"ticker": "AAA"}, _bv_price(8.0), [_bv_quote(book_value=10.0)])
    assert row.price_to_book == 0.8
    assert row.book_value_issue is None


def test_negative_book_value_does_not_produce_a_cheap_looking_ratio():
    """負淨值除下去會得到正的小數字，看起來像「跌破淨值」——那是假訊號。"""
    from analyst_valuation.aggregate import build_row
    row = build_row({"ticker": "AAA"}, _bv_price(12.0), [_bv_quote(book_value=-4.0)])
    assert row.book_value_issue == "negative"
    assert row.price_to_book is None
    assert any("負" in n for n in row.notes)
    assert row.book_value == -4.0            # 負淨值本身是真實資訊，要保留


def test_share_class_mismatch_is_not_reported_as_the_cheapest_stock():
    """實測案例 BRK-B：收盤 521.80，但資料源給的每股淨值 505,559 是 A 股的
    數字（B 股為 A 股的 1/1500）。算出來 P/B = 0.001，會登上「最便宜」榜首。"""
    from analyst_valuation.aggregate import build_row
    row = build_row({"ticker": "BRK-B"}, _bv_price(521.80), [_bv_quote(book_value=505559.44)])
    assert row.book_value_issue == "share_class_mismatch"
    assert row.price_to_book is None
    assert any("股別" in n for n in row.notes)


def test_genuinely_cheap_stock_is_not_mistaken_for_a_mismatch():
    """真正陷入困境的公司大約落在 0.2 上下，不該被門檻誤擋。"""
    from analyst_valuation.aggregate import build_row
    row = build_row({"ticker": "AAA"}, _bv_price(2.0), [_bv_quote(book_value=10.0)])
    assert row.price_to_book == 0.2 and row.book_value_issue is None


def test_very_high_ratio_is_kept_because_it_is_real():
    """大量庫藏股買回會讓帳上權益逼近於零，比值極高是實打實的，不是資料錯誤
    （實測：GDDY 1718、CL 315）。只在低端設防，高端不設限。"""
    from analyst_valuation.aggregate import build_row
    row = build_row({"ticker": "GDDY"}, _bv_price(91.07), [_bv_quote(book_value=0.053)])
    assert row.price_to_book > 1000 and row.book_value_issue is None


def test_missing_book_value_leaves_ratio_empty():
    from analyst_valuation.aggregate import build_row
    row = build_row({"ticker": "AAA"}, _bv_price(10.0), [_bv_quote(book_value=None)])
    assert row.book_value is None and row.price_to_book is None
    assert row.book_value_issue is None      # 「查無資料」不是「負淨值」


def test_book_value_survives_a_missing_close_price():
    from analyst_valuation.aggregate import build_row
    row = build_row({"ticker": "AAA"}, None, [_bv_quote(book_value=10.0)])
    assert row.book_value == 10.0 and row.price_to_book is None


def test_negative_book_value_is_not_swallowed_by_the_float_parser():
    """一般的數值轉換會把非正值當成無效值丟掉；淨值不能這樣處理。"""
    from analyst_valuation.sources.yahoo_targets import _as_float, _as_signed_float
    assert _as_float(-4.0) is None           # 目標價為負確實無效
    assert _as_signed_float(-4.0) == -4.0    # 淨值為負是真實資訊
    assert _as_signed_float(float("nan")) is None


# --- 市值篩選 ---------------------------------------------------------------
#
# 市值篩選是拿數字比大小，因此單位必須一致。這裡守的是「算得出來但不能用」
# 的那一類：不會拋錯，只會靜靜給出一個看起來完全正常的排名。

def test_market_cap_flows_from_the_quote_to_the_row():
    from analyst_valuation.aggregate import build_row
    row = build_row({"ticker": "AAA"}, _bv_price(50.0),
                    [_bv_quote(market_cap=1.5e10, currency="USD")])
    assert row.market_cap == 1.5e10
    assert row.market_cap_issue is None


def test_market_cap_in_another_currency_is_rejected_not_compared():
    """一檔日圓計價的中型企業，市值數字會是美元的一百多倍——不擋掉的話，
    它會被算成「巨型股」排在名單最上面，而且看起來完全正常。"""
    from analyst_valuation.aggregate import build_row
    row = build_row({"ticker": "JPX"}, _bv_price(50.0),
                    [_bv_quote(market_cap=8.0e11, currency="JPY")])
    assert row.market_cap is None                 # 不可比就不採用
    assert row.market_cap_issue == "non_usd"      # 但要說得出為什麼
    assert any("JPY" in n for n in row.notes)


def test_unknown_currency_is_accepted_because_the_universe_is_us_listed():
    """幣別欄位缺值時不該一律丟棄：這份股票池全是美國掛牌，預設就是美元。
    把「沒寫幣別」當成「可疑」，會讓大量正常標的平白消失。"""
    from analyst_valuation.aggregate import build_row
    row = build_row({"ticker": "AAA"}, _bv_price(50.0),
                    [_bv_quote(market_cap=3.0e9, currency=None)])
    assert row.market_cap == 3.0e9


@pytest.mark.parametrize("bad", [0, -1.0, None])
def test_non_positive_market_cap_is_missing_not_zero(bad):
    """市值 0 不是「這家公司不值錢」，是資料缺值。留成 0 會排進「最小」那一端。"""
    from analyst_valuation.aggregate import build_row
    row = build_row({"ticker": "AAA"}, _bv_price(50.0), [_bv_quote(market_cap=bad)])
    assert row.market_cap is None
    assert row.market_cap_issue is None           # 缺值不是「不可用」，別混為一談


def test_uncovered_ticker_still_gets_a_market_cap():
    """沒有分析師覆蓋的標的沒有 quotes。「沒人追蹤的小型股」正是這個篩選
    最有意義的情境，不能因為沒人追蹤就少了這個欄位。"""
    from analyst_valuation.universe_publish import row_from_record
    row = row_from_record(
        {"ticker": "TINY", "covered": False, "market_cap": 4.2e7,
         "market_cap_currency": "USD", "price": {"close": 1.5}},
        {"name": "Tiny Corp"},
    )
    assert row.market_cap == 4.2e7
    assert row.upside_pct is None                 # 確實沒有分析師覆蓋


def test_market_cap_currency_is_paired_with_the_cap_it_describes():
    """紀錄裡另有一個 currency 欄位取自主要報價來源，未必與市值同一筆。
    拿甲來源的幣別去驗乙來源的市值，這道防線就形同虛設。"""
    from analyst_valuation.universe_publish import row_from_record
    row = row_from_record(
        {"ticker": "ADR", "covered": False,
         "currency": "USD",                    # 報價幣別
         "market_cap": 9.9e12, "market_cap_currency": "KRW"},   # 市值幣別才是關鍵
        {},
    )
    assert row.market_cap is None
    assert row.market_cap_issue == "non_usd"


def test_market_cap_reaches_the_dashboard_payload():
    """欄位沒進 _UI_FIELDS 的話，前端拿到的就是空的——而畫面只會顯示「—」，
    看起來像「查無資料」，不像「這個欄位根本沒送出來」。"""
    from analyst_valuation.universe_publish import compact_row
    out = compact_row({"ticker": "AAA", "market_cap": 1.2e10, "market_cap_issue": None})
    assert out["market_cap"] == 1.2e10


# --- 類股名稱統一 -----------------------------------------------------------
#
# 兩份資料源用不同的分類體系：S&P 500 清單是 GICS，全市場掃描取自 Yahoo。
# 同一個「類股」下拉，切換資料源後選項整批換一套名字，使用者會以為是兩種東西。

@pytest.mark.parametrize("yahoo,gics", [
    ("Technology", "Information Technology"),
    ("Healthcare", "Health Care"),
    ("Consumer Cyclical", "Consumer Discretionary"),
    ("Consumer Defensive", "Consumer Staples"),
    ("Financial Services", "Financials"),
    ("Basic Materials", "Materials"),
])
def test_yahoo_sector_names_are_mapped_to_gics(yahoo, gics):
    from analyst_valuation.aggregate import normalize_sector
    assert normalize_sector(yahoo) == gics


@pytest.mark.parametrize("same", [
    "Communication Services", "Industrials", "Energy", "Utilities", "Real Estate",
])
def test_sectors_that_already_agree_are_left_alone(same):
    from analyst_valuation.aggregate import normalize_sector
    assert normalize_sector(same) == same


@pytest.mark.parametrize("gics", [
    "Information Technology", "Health Care", "Consumer Discretionary",
    "Consumer Staples", "Financials", "Materials",
])
def test_mapping_is_idempotent_so_the_sp500_path_is_unaffected(gics):
    """S&P 500 清單帶的已經是 GICS 名稱，再過一次轉換必須原封不動。"""
    from analyst_valuation.aggregate import normalize_sector
    assert normalize_sector(gics) == gics


def test_unknown_sector_names_pass_through_untouched():
    """日後資料源多出新類別時，照原樣顯示比硬塞進既有類別好——
    後者會讓一整群標的無聲地被歸錯類。"""
    from analyst_valuation.aggregate import normalize_sector
    assert normalize_sector("Quantum Widgets") == "Quantum Widgets"


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_missing_sector_becomes_unknown(blank):
    from analyst_valuation.aggregate import normalize_sector
    assert normalize_sector(blank) == "Unknown"


def test_both_data_paths_end_up_with_the_same_vocabulary():
    """S&P 走 build_row、全市場走 row_from_record，兩條路徑都要正規化，
    否則下拉選單仍會出現兩套名字。"""
    from analyst_valuation.aggregate import build_row
    from analyst_valuation.universe_publish import row_from_record
    sp = build_row({"ticker": "AAA", "sector": "Information Technology"}, None, [])
    uni = row_from_record({"ticker": "BBB", "covered": False, "sector": "Technology"}, {})
    assert sp.sector == uni.sector == "Information Technology"


# --- Nasdaq-100 股票池 -------------------------------------------------------
#
# 維基百科的 Nasdaq-100 頁面自 2026-08 起已無法解析出成分股表格（模組一的
# NDX 廣度指標已因此改用 TradingView），因此這裡直接沿用同一支抓取，
# 不重蹈覆轍。類股沒有現成資料，改用 S&P 500 清單對照補上。

def _ndx_component(symbol, name=None):
    from liquidity_monitor.sources.tradingview_ndx import Component
    return Component(symbol=symbol, name=name)


def test_fetch_ndx100_universe_backfills_sector_from_sp500_overlap():
    sp500 = [
        {"ticker": "AAPL", "name": "Apple Inc.", "sector": "Information Technology", "sub_industry": "Hardware"},
    ]
    with mock.patch.object(universe, "fetch_ndx_components",
                           return_value=[_ndx_component("AAPL"), _ndx_component("ZZZZ", "Not In SP500")]), \
         mock.patch.object(universe, "fetch_sp500_universe", return_value=sp500):
        rows = universe.fetch_ndx100_universe()

    by_ticker = {r["ticker"]: r for r in rows}
    assert by_ticker["AAPL"]["sector"] == "Information Technology"
    assert by_ticker["AAPL"]["name"] == "Apple Inc."
    # 不在 S&P 500 對照表裡的成分股：類股標 Unknown，不臆測填補
    assert by_ticker["ZZZZ"]["sector"] == "Unknown"
    assert by_ticker["ZZZZ"]["name"] == "Not In SP500"


def test_fetch_ndx100_universe_survives_sp500_lookup_failure():
    """類股只是加值資訊，S&P 500 對照抓不到時，成分股清單本身仍要回傳。"""
    with mock.patch.object(universe, "fetch_ndx_components", return_value=[_ndx_component("AAPL")]), \
         mock.patch.object(universe, "fetch_sp500_universe", side_effect=RuntimeError("boom")):
        rows = universe.fetch_ndx100_universe()

    assert rows == [{"ticker": "AAPL", "name": "AAPL", "sector": "Unknown", "sub_industry": ""}]


def test_fetch_ndx100_universe_uses_unexpired_cache(tmp_path):
    cache = tmp_path / "ndx.json"
    cache.write_text(json.dumps({
        "as_of": str(pd.Timestamp.today().date()),
        "constituents": [{"ticker": "CACHED", "name": "Cached Co", "sector": "Unknown", "sub_industry": ""}],
    }))
    with mock.patch.object(universe, "fetch_ndx_components",
                           side_effect=AssertionError("不該重抓")):
        rows = universe.fetch_ndx100_universe(cache_path=str(cache))
    assert rows[0]["ticker"] == "CACHED"


def test_pipeline_runs_against_ndx100_universe(monkeypatch, tmp_path):
    consts = [{"ticker": "AAA", "name": "Alpha", "sector": "Energy", "sub_industry": "Oil"}]
    monkeypatch.setattr(pipeline.universe, "fetch_ndx100_universe", lambda **kw: consts)
    monkeypatch.setattr(pipeline.prices, "fetch_price_snapshots", lambda tickers, **kw: {
        "AAA": PriceSnapshot("AAA", close=100.0, close_date="2026-08-07"),
    })
    monkeypatch.setattr(pipeline, "fetch_yahoo_target", lambda t: _quote("yahoo", 130.0))
    monkeypatch.setattr(pipeline.finnhub_targets, "is_enabled", lambda key=None: False)

    report = pipeline.run(
        as_of="2026-08-07", universe_source="ndx100",
        universe_cache_path=str(tmp_path / "ndx.json"),
    )
    assert report["universe"] == "Nasdaq-100 (TradingView constituents)"
    assert report["rows"][0]["ticker"] == "AAA"


def test_pipeline_rejects_unknown_universe_source():
    with pytest.raises(ValueError, match="sp500 或 ndx100"):
        pipeline.run(universe_source="nyse-composite")


def test_storage_prefix_keeps_two_universes_in_the_same_data_root(tmp_path):
    """S&P 500（無前綴）與 Nasdaq-100（"ndx100_"）共用一個 data_root，
    檔名不能互相覆蓋。"""
    sp500_report = {
        "as_of": "2026-08-07", "counts": {"universe": 1, "with_target_and_price": 1},
        "sources_available": ["yahoo"],
        "rows": [{"ticker": "AAA", "upside_pct": 10.0}],
    }
    ndx_report = {
        "as_of": "2026-08-07", "counts": {"universe": 1, "with_target_and_price": 1},
        "sources_available": ["yahoo"],
        "rows": [{"ticker": "BBB", "upside_pct": -5.0}],
    }
    storage.save_report(sp500_report, data_root=str(tmp_path))
    storage.save_report(ndx_report, data_root=str(tmp_path), prefix="ndx100_")

    assert (tmp_path / "latest.json").exists()
    assert (tmp_path / "ndx100_latest.json").exists()
    assert (tmp_path / "snapshots" / "2026-08-07.json").exists()
    assert (tmp_path / "ndx100_snapshots" / "2026-08-07.json").exists()

    sp_saved = json.loads((tmp_path / "latest.json").read_text())
    ndx_saved = json.loads((tmp_path / "ndx100_latest.json").read_text())
    assert sp_saved["rows"][0]["ticker"] == "AAA"
    assert ndx_saved["rows"][0]["ticker"] == "BBB"
