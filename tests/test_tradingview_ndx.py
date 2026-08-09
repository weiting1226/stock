"""TradingView NDX 成分股與市場廣度指標的測試。

這個來源沒有官方文件、也無法在開發環境直連（代理只放行少數網域），
因此測試守的是兩件事：回應能正確解析成指標；以及**失敗時錯誤訊息帶得出
足以定位問題的證據**——盲改再重跑一次要好幾分鐘，訊息裡沒有證據就只能猜。
"""
from __future__ import annotations

from unittest import mock

import pytest

from liquidity_monitor.sources import tradingview_ndx as tv


def _row(symbol, close, sma20, sma50, sma200, change=0.5, rsi=55.0,
         cap=1e12, hi=None, lo=None):
    """依 COLUMNS 順序組出一列回應資料。"""
    return {"s": f"NASDAQ:{symbol}", "d": [
        symbol, f"{symbol} Inc", close, change, sma20, sma50, sma200, rsi, cap,
        hi if hi is not None else close * 1.2,
        lo if lo is not None else close * 0.7,
    ]}


def _response(rows, status=200, total=None):
    resp = mock.Mock()
    resp.status_code = status
    resp.json.return_value = {"totalCount": total if total is not None else len(rows),
                              "data": rows}
    resp.text = "{}"
    return resp


def _many(n, above=True):
    """n 檔全部站上（或全部跌破）各條均線。"""
    return [_row(f"T{i}", 110.0 if above else 90.0, 100.0, 100.0, 100.0) for i in range(n)]


# --- 解析 -------------------------------------------------------------------

def test_parses_components_and_normalises_symbols():
    rows = _many(60)
    rows.append(_row("BRK.B", 110.0, 100.0, 100.0, 100.0))
    session = mock.Mock(post=mock.Mock(return_value=_response(rows)))

    comps = tv.fetch_ndx_components(session=session)
    assert len(comps) == 61
    # TradingView 寫 BRK.B，本專案其餘部分一律用 Yahoo 格式
    assert "BRK-B" in {c.symbol for c in comps}
    assert comps[0].close == 110.0 and comps[0].sma200 == 100.0


def test_uses_first_query_shape_that_works():
    """第一種格式失敗時要換下一種，而不是直接放棄。"""
    bad = mock.Mock(status_code=400, text="unknown field")
    good = _response(_many(60))
    session = mock.Mock(post=mock.Mock(side_effect=[bad, good]))

    comps = tv.fetch_ndx_components(session=session)
    assert len(comps) == 60
    assert session.post.call_count == 2


# --- 廣度指標 ---------------------------------------------------------------

def test_breadth_percentages():
    comps = [
        tv.Component("A", close=110, sma20=100, sma50=100, sma200=100, change_pct=1.0, rsi=75),
        tv.Component("B", close=90, sma20=100, sma50=100, sma200=100, change_pct=-1.0, rsi=25),
        tv.Component("C", close=120, sma20=100, sma50=130, sma200=100, change_pct=2.0, rsi=60),
        tv.Component("D", close=80, sma20=100, sma50=100, sma200=130, change_pct=0.0, rsi=50),
    ]
    b = tv.compute_breadth(comps)
    assert b["pct_above_200dma"] == 50.0      # A、C 站上
    assert b["pct_above_50dma"] == 25.0       # 只有 A
    assert b["advancers"] == 2 and b["decliners"] == 1
    assert b["advance_decline_ratio"] == 2.0
    assert b["overbought_rsi70"] == 1 and b["oversold_rsi30"] == 1
    assert b["median_rsi"] == 55.0


def test_breadth_reports_sample_size_when_moving_average_missing():
    """有幾檔缺均線時分母就不是 100。不講清楚的話，62% 看起來一樣正常，
    但可能是 62/98 而不是 62/100。"""
    comps = [
        tv.Component("A", close=110, sma200=100),
        tv.Component("B", close=90, sma200=100),
        tv.Component("C", close=110, sma200=None),   # 缺資料，不能算進分母
    ]
    b = tv.compute_breadth(comps)
    assert b["pct_above_200dma"] == 50.0
    assert b["pct_above_200dma_sample"] == 2
    assert b["constituents"] == 3


def test_advance_decline_ratio_is_none_when_nothing_declined():
    """全數上漲時比值的分母為零，要用 None 表示而不是硬給一個大數字。"""
    b = tv.compute_breadth([tv.Component("A", close=110, change_pct=1.0)])
    assert b["advance_decline_ratio"] is None


def test_top10_concentration():
    comps = [tv.Component(f"T{i}", close=1, market_cap=10.0) for i in range(20)]
    b = tv.compute_breadth(comps)
    assert b["top10_market_cap_pct"] == 50.0


# --- 失敗時要帶出證據 -------------------------------------------------------

def test_rejects_short_list_instead_of_reporting_a_plausible_number():
    """只抓到幾檔卻照算，會得到一個看起來正常、實際上無意義的百分比。"""
    session = mock.Mock(post=mock.Mock(return_value=_response(_many(3))))
    with pytest.raises(ValueError) as exc:
        tv.fetch_ndx_components(session=session)
    assert "只解析出 3 檔" in str(exc.value)


@pytest.mark.parametrize("resp,expected", [
    (mock.Mock(status_code=403, text="Forbidden by WAF"), "HTTP 403"),
    (mock.Mock(status_code=200, text="<!doctype html><html>", **{"json.side_effect": ValueError()}),
     "不是 JSON"),
])
def test_failure_message_carries_the_evidence_needed_to_fix_it(resp, expected):
    session = mock.Mock(post=mock.Mock(return_value=resp))
    with pytest.raises(ValueError) as exc:
        tv.fetch_ndx_components(session=session)
    msg = str(exc.value)
    assert expected in msg
    # 兩種查詢格式都要試過，且各自的結果都要出現在訊息裡
    assert msg.count("symbolset") + msg.count("index-filter") >= 2


def test_missing_data_array_reports_actual_keys():
    resp = mock.Mock(status_code=200, text="{}")
    resp.json.return_value = {"error": "invalid symbolset", "code": 42}
    session = mock.Mock(post=mock.Mock(return_value=resp))
    with pytest.raises(ValueError) as exc:
        tv.fetch_ndx_components(session=session)
    assert "缺少 data 陣列" in str(exc.value)
    assert "error" in str(exc.value)      # 實際拿到的鍵要列出來
