"""④ 股票型 ETF 資金流：解析、計分、歷史累積。

ETF.com 連不上開發環境（代理只放行少數網域），因此測試守兩件事：
已知結構要解析得出來；以及**失敗時錯誤訊息帶得出足以定位問題的證據**。
另外計分完全不依賴網路，可以完整驗證——那是這個指標真正的邏輯所在。
"""
from __future__ import annotations

import json
from unittest import mock

import pytest

from liquidity_monitor import scoring, storage
from liquidity_monitor.sources import etf_flows


def _resp(text, status=200):
    return mock.Mock(status_code=status, text=text)


NEXT_DATA_HTML = """
<html><body><script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"fundFlows":[
  {"name":"U.S. Equity","netFlows":"1,234.5"},
  {"name":"International Equity","netFlows":"-321.0"},
  {"name":"U.S. Fixed Income","netFlows":"800.0"}
]}}}
</script></body></html>
"""

TABLE_HTML = """
<html><body><table>
<tr><th>Segment</th><th>Net Flows ($M)</th></tr>
<tr><td>U.S. Equity</td><td>2,500.0</td></tr>
<tr><td>Commodities</td><td>-50.0</td></tr>
</table></body></html>
"""


# --- 解析 -------------------------------------------------------------------

def test_parses_next_data_and_picks_us_equity():
    session = mock.Mock(get=mock.Mock(return_value=_resp(NEXT_DATA_HTML)))
    obs = etf_flows.fetch_equity_etf_flow(session=session, as_of="2026-08-09")
    assert obs.net_flow_musd == 1234.5
    assert "U.S. Equity" in obs.scope        # 挑到的是美國股票型，不是國際或債券


def test_falls_back_to_html_table_when_no_next_data():
    session = mock.Mock(get=mock.Mock(return_value=_resp(TABLE_HTML)))
    obs = etf_flows.fetch_equity_etf_flow(session=session, as_of="2026-08-09")
    assert obs.net_flow_musd == 2500.0


def test_tries_next_url_when_first_one_is_blocked():
    """Cloudflare 擋下第一個位址時要換下一個，而不是直接放棄。"""
    session = mock.Mock(get=mock.Mock(side_effect=[
        _resp("<html>blocked</html>", status=403),
        _resp(NEXT_DATA_HTML),
    ]))
    obs = etf_flows.fetch_equity_etf_flow(session=session)
    assert obs.net_flow_musd == 1234.5
    assert session.get.call_count == 2


@pytest.mark.parametrize("raw,expected", [
    ("1,234.5", 1234.5),
    ("-321", -321.0),
    ("$1.2B", 1200.0),          # 十億要換算成百萬
    ("3.4M", 3.4),
    ("(500)", -500.0),          # 會計式負數
    ("--", None),
    ("N/A", None),
    ("", None),
    (None, None),
])
def test_amount_parsing_covers_the_shapes_the_site_uses(raw, expected):
    assert etf_flows._to_musd(raw) == expected


# --- 失敗時要帶出證據 -------------------------------------------------------

def test_all_urls_blocked_reports_status_codes():
    session = mock.Mock(get=mock.Mock(return_value=_resp("<html>nope</html>", status=403)))
    with pytest.raises(ValueError) as exc:
        etf_flows.fetch_equity_etf_flow(session=session)
    msg = str(exc.value)
    assert "HTTP 403" in msg
    assert "Cloudflare" in msg                       # 提示可能的原因
    assert msg.count("etf.com") >= len(etf_flows.CANDIDATE_URLS)


def test_page_without_equity_records_lists_the_labels_it_did_find():
    """結構變了的時候，要能一次看出它現在長什麼樣，而不是只知道「失敗」。"""
    html = NEXT_DATA_HTML.replace("U.S. Equity", "Total Market").replace(
        "International Equity", "Municipal Bonds")
    session = mock.Mock(get=mock.Mock(return_value=_resp(html)))
    with pytest.raises(ValueError) as exc:
        etf_flows.fetch_equity_etf_flow(session=session)
    msg = str(exc.value)
    assert "候選標籤" in msg
    assert "Total Market" in msg or "Municipal Bonds" in msg


# --- 計分：方向由正負決定，強度由自身歷史決定 -------------------------------

def test_direction_only_when_history_is_too_short():
    """歷史不足時只判方向。方向是確定的，「大幅與否」還沒有依據——
    少說一級，也不要硬給 ±2。"""
    assert scoring.score_etf_fund_flow(5000.0, []) == 1
    assert scoring.score_etf_fund_flow(-5000.0, [1.0, 2.0]) == -1
    assert scoring.score_etf_fund_flow(0.0, []) == 0


def test_magnitude_is_judged_against_the_series_own_typical_scale():
    history = [100.0] * 30                      # 常態規模 = 100
    assert scoring.score_etf_fund_flow(100.0, history) == 1    # 就是常態，不算大幅
    assert scoring.score_etf_fund_flow(500.0, history) == 2    # 常態的五倍
    assert scoring.score_etf_fund_flow(-500.0, history) == -2
    assert scoring.score_etf_fund_flow(-100.0, history) == -1


def test_flat_means_near_zero_not_merely_typical():
    """規格的「持平」指進出接近打平。資金流長期都很大時，它的後 20% 依然是
    很大的金額，用百分位當門檻會把那種日子誤判成持平。"""
    big = [1000.0] * 30                         # 每期都是大額進出
    assert scoring.score_etf_fund_flow(900.0, big) == 1        # 偏小但仍是明確流入
    assert scoring.score_etf_fund_flow(50.0, big) == 0         # 相對常態微不足道
    assert scoring.score_etf_fund_flow(-50.0, big) == 0


def test_history_uses_absolute_values_so_outflows_count_toward_scale():
    """歷史若只看正值，流出期間的規模就完全沒有比較基準。"""
    history = [-100.0] * 30                     # 全是流出
    assert scoring.score_etf_fund_flow(500.0, history) == 2    # 相對常態規模是大幅流入
    assert scoring.score_etf_fund_flow(-500.0, history) == -2


def test_zero_scale_history_falls_back_to_direction_only():
    """歷史全是 0 時沒有規模可比，只能判方向——不能除以零，也不能假裝知道強度。"""
    assert scoring.score_etf_fund_flow(500.0, [0.0] * 30) == 1


def test_missing_flow_is_unavailable_not_zero():
    assert scoring.score_etf_fund_flow(None, [1.0] * 30) is None


# --- 歷史累積 ---------------------------------------------------------------

def test_history_roundtrip_and_same_day_rerun_does_not_duplicate(tmp_path):
    path = str(tmp_path / "flows.csv")
    obs = etf_flows.FlowObservation("2026-08-09", 1000.0, "U.S. Equity", "latest", "u")
    storage.append_etf_flow(obs, path=path)
    storage.append_etf_flow(obs, path=path)          # 同一天重跑
    assert storage.load_etf_flow_history(path) == [1000.0]

    storage.append_etf_flow(
        etf_flows.FlowObservation("2026-08-10", -500.0, "U.S. Equity", "latest", "u"), path=path)
    assert storage.load_etf_flow_history(path) == [1000.0, -500.0]


def test_history_excludes_today_so_it_is_not_compared_against_itself(tmp_path):
    """拿今天的值去跟「含今天的分布」比，等於部分拿自己當基準。"""
    path = str(tmp_path / "flows.csv")
    for day, v in (("2026-08-08", 100.0), ("2026-08-09", 999.0)):
        storage.append_etf_flow(
            etf_flows.FlowObservation(day, v, "U.S. Equity", "latest", "u"), path=path)
    assert storage.load_etf_flow_history(path, before="2026-08-09") == [100.0]


def test_missing_history_file_is_empty_not_an_error(tmp_path):
    assert storage.load_etf_flow_history(str(tmp_path / "nope.csv")) == []


# --- 由流通股數自行計算資金流 ----------------------------------------------
#
# ETF.com 從 Actions 存取時三個位址全部回 403（Cloudflare 擋資料中心 IP，實測），
# 因此改由 ETF 的申贖流量自行計算。流通股數只會因申購／贖回而變動，
# 「Δ股數 × 價格」就是資金流的定義本身，不是代理指標。

from liquidity_monitor.sources import etf_creation_flows as ecf


def _snap(ticker, shares=None, aum=None, close=100.0, as_of="2026-08-09"):
    return ecf.EtfSnapshot(as_of=as_of, ticker=ticker, shares_outstanding=shares,
                           total_assets=aum, close=close)


def _prev(ticker, shares=None, aum=None, close=100.0, as_of="2026-08-08"):
    return {"as_of": as_of, "ticker": ticker, "shares_outstanding": shares,
            "total_assets": aum, "close": close}


def test_share_increase_is_an_inflow():
    """股數增加 = 授權參與者申購 = 資金流入。"""
    today = [_snap(f"E{i}", shares=1_010_000, close=100.0) for i in range(4)]
    prev = {f"E{i}": _prev(f"E{i}", shares=1_000_000) for i in range(4)}
    r = ecf.compute_flow(today, prev)
    # 每檔 +10,000 股 × $100 = $1M，四檔共 $4M
    assert r.net_flow_musd == 4.0
    assert r.basis == "shares" and r.etfs_used == 4


def test_share_decrease_is_an_outflow():
    today = [_snap(f"E{i}", shares=990_000, close=100.0) for i in range(4)]
    prev = {f"E{i}": _prev(f"E{i}", shares=1_000_000) for i in range(4)}
    assert ecf.compute_flow(today, prev).net_flow_musd == -4.0


def test_price_move_alone_is_not_a_flow():
    """關鍵性質：只有股價漲跌、股數沒變，就不是資金流。
    這正是「股數法」勝過「看 AUM 變化」的地方。"""
    today = [_snap(f"E{i}", shares=1_000_000, close=150.0) for i in range(4)]
    prev = {f"E{i}": _prev(f"E{i}", shares=1_000_000, close=100.0) for i in range(4)}
    assert ecf.compute_flow(today, prev).net_flow_musd == 0.0


def test_split_is_not_mistaken_for_a_giant_inflow():
    """ETF 分割會讓股數瞬間倍增，那不是申贖。不擋掉的話會產生一筆
    虛假的巨額流入，而且看起來完全合理。"""
    today = [_snap("SPLIT", shares=2_000_000, close=50.0)] + \
            [_snap(f"E{i}", shares=1_010_000, close=100.0) for i in range(4)]
    prev = {"SPLIT": _prev("SPLIT", shares=1_000_000, close=100.0)}
    prev.update({f"E{i}": _prev(f"E{i}", shares=1_000_000) for i in range(4)})
    r = ecf.compute_flow(today, prev)
    assert r.net_flow_musd == 4.0                      # 只算正常那四檔
    assert any("分割" in reason for _, reason in r.etfs_skipped)


def test_aum_basis_strips_market_return():
    """退回淨資產法時，必須把漲跌造成的市值變動扣掉，剩下的才是資金進出。"""
    # 價格 +50%，AUM 從 100M 變成 160M：其中 50M 是漲出來的，10M 才是流入
    today = [_snap(f"E{i}", aum=160_000_000, close=150.0) for i in range(4)]
    prev = {f"E{i}": _prev(f"E{i}", aum=100_000_000, close=100.0) for i in range(4)}
    r = ecf.compute_flow(today, prev)
    assert r.basis == "aum"
    assert r.net_flow_musd == 40.0                     # 每檔 10M，共 40M


def test_multi_day_gap_is_averaged_to_a_daily_figure():
    """長假後那一筆若不攤平，會被當成暴量單日訊號。"""
    today = [_snap(f"E{i}", shares=1_030_000, close=100.0) for i in range(4)]
    prev = {f"E{i}": _prev(f"E{i}", shares=1_000_000, as_of="2026-08-06") for i in range(4)}
    r = ecf.compute_flow(today, prev)      # 相隔 3 天
    assert r.days_spanned == 3
    assert r.net_flow_musd == 4.0          # 共 12M / 3 天


def test_stale_previous_observation_is_not_used():
    today = [_snap(f"E{i}", shares=1_010_000) for i in range(4)]
    prev = {f"E{i}": _prev(f"E{i}", shares=1_000_000, as_of="2026-06-01") for i in range(4)}
    with pytest.raises(ValueError, match="沒有任何一檔"):
        ecf.compute_flow(today, prev)


def test_first_ever_run_fails_loudly_rather_than_reporting_zero():
    """第一次執行必然沒有前一次觀測。回報 0 會被當成「持平」——那是假訊號。"""
    with pytest.raises(ValueError) as exc:
        ecf.compute_flow([_snap("SPY", shares=1_000_000)], {})
    assert "無前一次觀測" in str(exc.value)


def test_snapshot_history_roundtrip_and_excludes_today(tmp_path):
    path = str(tmp_path / "snaps.csv")
    storage.save_etf_snapshots([_snap("SPY", shares=1_000_000, as_of="2026-08-08")], path=path)
    storage.save_etf_snapshots([_snap("SPY", shares=1_010_000, as_of="2026-08-09")], path=path)

    prev = storage.load_previous_etf_snapshots(path, before="2026-08-09")
    assert prev["SPY"]["shares_outstanding"] == 1_000_000      # 拿到的是昨天那筆
    latest = storage.load_previous_etf_snapshots(path)
    assert latest["SPY"]["shares_outstanding"] == 1_010_000


def test_snapshot_same_day_rerun_does_not_duplicate(tmp_path):
    path = str(tmp_path / "snaps.csv")
    for _ in range(3):
        storage.save_etf_snapshots([_snap("SPY", shares=1_000_000)], path=path)
    import pandas as pd
    assert len(pd.read_csv(path)) == 1
