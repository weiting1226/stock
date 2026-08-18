"""蝦皮自動查價來源的測試。

`shopee.py` 開頭寫明了：這支程式碼從沒在真正的蝦皮 API 上跑過，回應格式、
價格換算係數都是照公開資料猜的，而且蝦皮的防爬機制公認比 PChome 積極。
這裡的測試因此**不打真實網路**，而是驗證「假設對的時候解析對」「假設錯的
時候不會炸掉、不會安靜地回傳錯資料」，以及蝦皮特有的兩個風險點：
被防爬機制擋下時回應裡的 error 欄位、以及價格換算係數猜錯時的保底防呆。
"""
from __future__ import annotations

import requests

from diaper_monitor.sources import shopee


class _FakeResponse:
    def __init__(self, payload=None, status=200, json_error=False):
        self._payload = payload
        self.status_code = status
        self._json_error = json_error

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._payload


class _FakeSession:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append({"url": url, "params": params})
        if self._exc:
            raise self._exc
        return self._response


def _item(name, price_scaled, itemid="111", shopid="222"):
    """price_scaled 是「元的十萬分之一」——蝦皮 API 假設的價格單位。"""
    return {"item_basic": {"name": name, "price": price_scaled, "itemid": itemid, "shopid": shopid}}


# --- 正常解析（含價格換算） -----------------------------------------------------

def test_parses_a_well_formed_response_and_converts_the_price_scale():
    payload = {"items": [_item("滿意寶寶 日本境內版 M 62片", 51900000)]}  # 519 元
    session = _FakeSession(_FakeResponse(payload))
    quotes = shopee.fetch_brand("滿意寶寶日本境內版", session=session)
    assert len(quotes) == 1
    q = quotes[0]
    assert q["brand"] == "滿意寶寶日本境內版"
    assert q["platform"] == "蝦皮"
    assert q["piece_count"] == 62
    assert q["pack_price"] == 519.0
    assert q["url"] == "https://shopee.tw/-i.222.111"
    assert "自動爬蟲" in q["note"]


def test_tolerates_items_without_the_item_basic_wrapper():
    """新舊版 API 有時把欄位直接攤平在最外層，不包在 item_basic 底下。"""
    payload = {"items": [{"name": "Aiwibi 愛薇彼 M 48片", "price": 30800000,
                           "itemid": "1", "shopid": "2"}]}
    session = _FakeSession(_FakeResponse(payload))
    quotes = shopee.fetch_brand("Aiwibi", session=session)
    assert len(quotes) == 1
    assert quotes[0]["pack_price"] == 308.0


# --- 蝦皮特有：防爬機制的 error 欄位 --------------------------------------------

def test_treats_an_error_envelope_as_a_block_not_zero_results():
    """蝦皮被防爬機制擋下時常見的回應是 HTTP 200 加上 error 欄位，
    不是連線失敗——要能分辨「真的沒有商品」跟「被擋下來了」。"""
    payload = {"error": 4, "error_msg": "unusual traffic detected"}
    session = _FakeSession(_FakeResponse(payload))
    assert shopee.fetch_brand("滿意寶寶日本境內版", session=session) == []


# --- 蝦皮特有：價格換算係數的保底防呆 -------------------------------------------

def test_rejects_implausible_prices_from_a_wrong_scale_factor():
    """如果價格換算係數猜錯，算出來的售價會離譜到不像一包尿布
    （這裡刻意不除以 100000，模擬係數整個錯掉的情況）。"""
    payload = {"items": [_item("滿意寶寶 日本境內版 M 62片", 519)]}  # 未縮放 = 0.00519 元
    session = _FakeSession(_FakeResponse(payload))
    assert shopee.fetch_brand("滿意寶寶日本境內版", session=session) == []


# --- 篩選：M 號 + 找得到片數（跟 pchome 共用同一套規則） ------------------------

def test_rejects_items_without_a_recognizable_piece_count():
    payload = {"items": [_item("滿意寶寶 日本境內版 M 箱購組合", 99900000)]}
    session = _FakeSession(_FakeResponse(payload))
    assert shopee.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_rejects_items_that_are_not_size_m():
    payload = {"items": [_item("滿意寶寶 日本境內版 L 54片", 99900000)]}
    session = _FakeSession(_FakeResponse(payload))
    assert shopee.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_rejects_trial_size_packs():
    """跟 pchome.py 同一個真實案例，驗證共用的過濾邏輯在蝦皮這邊也生效。"""
    payload = {"items": [_item(
        "零觸感瞬吸 褲型紙尿褲 2片/包 M-2XL(褲型/紙尿褲/尿布/體驗包)", 2900000)]}
    session = _FakeSession(_FakeResponse(payload))
    assert shopee.fetch_brand("Aiwibi", session=session) == []


def test_rejects_box_purchases_with_a_pack_multiplier():
    payload = {"items": [_item("奢寵幫 M 38片入*3包入(箱購)", 152000000)]}
    session = _FakeSession(_FakeResponse(payload))
    assert shopee.fetch_brand("奢寵幫", session=session) == []


def test_caps_results_per_brand():
    items = [_item(f"滿意寶寶 日本境內版 M {60 + i}片", 50000000 + i, itemid=str(i))
             for i in range(shopee.MAX_RESULTS_PER_BRAND + 5)]
    session = _FakeSession(_FakeResponse({"items": items}))
    quotes = shopee.fetch_brand("滿意寶寶日本境內版", session=session)
    assert len(quotes) == shopee.MAX_RESULTS_PER_BRAND


# --- 失敗要安靜地回傳空 list，不能拋例外 ----------------------------------------

def test_missing_items_key_returns_empty_not_an_exception():
    session = _FakeSession(_FakeResponse({"total_count": 0}))
    assert shopee.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_non_list_items_returns_empty_not_an_exception():
    session = _FakeSession(_FakeResponse({"items": "unexpected string"}))
    assert shopee.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_non_dict_payload_returns_empty_not_an_exception():
    session = _FakeSession(_FakeResponse(["unexpected", "list"]))
    assert shopee.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_network_exception_returns_empty_not_raised():
    session = _FakeSession(exc=requests.ConnectionError("boom"))
    assert shopee.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_timeout_returns_empty_not_raised():
    session = _FakeSession(exc=requests.Timeout("too slow"))
    assert shopee.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_http_error_status_returns_empty_not_raised():
    session = _FakeSession(_FakeResponse(status=500))
    assert shopee.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_invalid_json_returns_empty_not_raised():
    session = _FakeSession(_FakeResponse(json_error=True))
    assert shopee.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_unconfigured_brand_returns_empty_without_hitting_network():
    session = _FakeSession()
    assert shopee.fetch_brand("不存在的品牌", session=session) == []
    assert session.calls == []


# --- fetch_all ------------------------------------------------------------------

def test_fetch_all_aggregates_across_brands():
    payload = {"items": [_item("測試 M 60片", 40000000)]}
    session = _FakeSession(_FakeResponse(payload))
    quotes = shopee.fetch_all(["滿意寶寶日本境內版", "Aiwibi", "奢寵幫"], session=session)
    assert len(quotes) == 3
    assert {q["brand"] for q in quotes} == {"滿意寶寶日本境內版", "Aiwibi", "奢寵幫"}


def test_fetch_all_keeps_going_when_one_brand_raises_unexpectedly(monkeypatch):
    calls = []

    def fake_fetch_brand(brand, query=None, session=None):
        calls.append(brand)
        if brand == "Aiwibi":
            raise RuntimeError("模擬 fetch_brand 防呆漏掉的例外")
        return [{"brand": brand}]

    monkeypatch.setattr(shopee, "fetch_brand", fake_fetch_brand)
    quotes = shopee.fetch_all(["滿意寶寶日本境內版", "Aiwibi", "奢寵幫"])
    assert calls == ["滿意寶寶日本境內版", "Aiwibi", "奢寵幫"]
    assert {q["brand"] for q in quotes} == {"滿意寶寶日本境內版", "奢寵幫"}
