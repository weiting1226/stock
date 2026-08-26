"""PChome 自動查價來源的測試。

`pchome.py` 開頭寫明了：這支程式碼從沒在真正的 PChome API 上跑過，回應格式
是照公開資料猜的。這裡的測試因此**不打真實網路**（本來也打不到——開發環境的
出口把 ecshweb.pchome.com.tw 擋了），而是驗證「假設對的時候解析對」以及
「假設錯的時候不會炸掉、不會安靜地回傳錯資料」這兩件事本身的邏輯正確。
"""
from __future__ import annotations

import pytest
import requests

from diaper_monitor.sources import pchome


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
    """呼叫端傳什麼 params/回應就回什麼——測試只在乎 fetch_brand 怎麼處理
    回應，不在乎 requests 本身怎麼運作。"""

    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append({"url": url, "params": params})
        if self._exc:
            raise self._exc
        return self._response


# --- 正常解析 ------------------------------------------------------------------

def test_parses_a_well_formed_response():
    payload = {"prods": [
        {"Id": "ABC123", "name": "滿意寶寶 日本境內版 M 62片", "price": 519},
    ]}
    session = _FakeSession(_FakeResponse(payload))
    quotes = pchome.fetch_brand("滿意寶寶日本境內版", session=session)
    assert len(quotes) == 1
    q = quotes[0]
    assert q["brand"] == "滿意寶寶日本境內版"
    assert q["platform"] == "PChome24h購物"
    assert q["piece_count"] == 62
    assert q["pack_price"] == 519.0
    assert q["url"] == "https://24h.pchome.com.tw/prod/ABC123"
    assert "自動爬蟲" in q["note"]


def test_tolerates_capitalized_field_names():
    """PChome 這類舊版 API 欄位大小寫不一致是常態；猜錯一種大小寫
    不該讓整批都抓不到。"""
    payload = {"Prods": [
        {"Id": "XYZ999", "Name": "Aiwibi 愛薇彼 M 48片", "Price": 308},
    ]}
    session = _FakeSession(_FakeResponse(payload))
    quotes = pchome.fetch_brand("Aiwibi", session=session)
    assert len(quotes) == 1
    assert quotes[0]["piece_count"] == 48


# --- 篩選：M 號 + 找得到片數 ----------------------------------------------------

def test_rejects_items_without_a_recognizable_piece_count():
    payload = {"prods": [{"Id": "1", "name": "滿意寶寶 日本境內版 M 箱購組合", "price": 999}]}
    session = _FakeSession(_FakeResponse(payload))
    assert pchome.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_rejects_items_that_are_not_size_m():
    payload = {"prods": [{"Id": "1", "name": "滿意寶寶 日本境內版 L 54片", "price": 999}]}
    session = _FakeSession(_FakeResponse(payload))
    assert pchome.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_does_not_false_positive_on_m_inside_another_word():
    """「XM」「ML」這類字串裡的 M 不該被當成尺寸 M 號——標題必須有獨立的 M。"""
    payload = {"prods": [{"Id": "1", "name": "某商品 XML 認證 62片", "price": 500}]}
    session = _FakeSession(_FakeResponse(payload))
    assert pchome.fetch_brand("滿意寶寶日本境內版", session=session) == []


# --- 三個 2026-08-18 在 GitHub Actions 上真的抓錯過的案例 -----------------------
#
# 這三個不是抓不到、是**算出一個看起來合理但其實錯誤的單片價**——比抓不到更
# 危險，因為不會觸發「查到 0 筆」的警告，錯誤資料會直接被當成報價收下。

def test_rejects_trial_size_packs():
    """真實案例：「2片/包 M-2XL(體驗包)」29 元算出 14.5 元/片——
    試用包單價本來就貴，不是市售單位，混進來會讓「今日最便宜」失真。"""
    payload = {"prods": [{"Id": "1",
        "name": "零觸感瞬吸 褲型紙尿褲 2片/包 M-2XL(褲型/紙尿褲/尿布/體驗包)", "price": 29}]}
    session = _FakeSession(_FakeResponse(payload))
    assert pchome.fetch_brand("Aiwibi", session=session) == []


def test_rejects_titles_that_list_multiple_sizes_piece_counts_together():
    """真實案例：「(單包入48/44/40/36片)」規則會抓到最後一個數字「36」，
    但完全不知道哪個數字才是對應 M 號——與其猜，不如整批跳過。"""
    payload = {"prods": [{"Id": "1",
        "name": "夜用甄柔瞬吸 褲型紙尿褲 M-XXL(單包入48/44/40/36片)", "price": 529}]}
    session = _FakeSession(_FakeResponse(payload))
    assert pchome.fetch_brand("Aiwibi", session=session) == []


def test_box_purchases_are_multiplied_out_rather_than_skipped():
    """同一個真實案例，結論翻過來了。

    「奢寵幫 M 38片入*3包入(箱購)」原本整批跳過，因為只抓到 38、沒乘以 3，
    會算出 40 元/片而不是接近 13。但那個標題把兩個數字都寫清楚了——
    38 片、3 包——乘起來是**算得出來的，不是猜的**。

    2026-08-25 的實測讓這件事變得要緊：奢寵幫在 PChome 查到 4 筆，
    只有這一筆同時標明 M 號與片數，擋掉它等於這個品牌完全沒有自動報價。"""
    payload = {"prods": [{"Id": "1", "name": "奢寵幫 M 38片入*3包入(箱購)", "price": 1520}]}
    session = _FakeSession(_FakeResponse(payload))
    quotes = pchome.fetch_brand("奢寵幫", session=session)
    assert len(quotes) == 1
    assert quotes[0]["piece_count"] == 114                      # 38 x 3
    assert quotes[0]["pack_price"] / quotes[0]["piece_count"] == pytest.approx(13.33, abs=0.01)


def test_a_box_purchase_whose_multiplier_is_wrong_is_caught_by_the_unit_price_guard():
    """換算開了之後，「乘錯」就成了新的失敗方式——而且它不會拋例外，
    只會產生一個看起來正常的單片價。所以最終單片價要再過一關。

    這裡用同一個標題配一個明顯不對的售價：1520 元若真的只有 38 片，
    單片價 40 元；片數若被誇大成 1140 片，單片價 1.33 元。兩端都要擋下。"""
    high = {"prods": [{"Id": "1", "name": "奢寵幫 M 38片入(單包)", "price": 1520}]}
    assert pchome.fetch_brand("奢寵幫", session=_FakeSession(_FakeResponse(high))) == []
    low = {"prods": [{"Id": "2", "name": "奢寵幫 M 38片入*30包入(箱購)", "price": 1520}]}
    assert pchome.fetch_brand("奢寵幫", session=_FakeSession(_FakeResponse(low))) == []


# --- 逐筆略過的原因要記錄成 log，不能只留下總數 ------------------------------
#
# 2026-08-18 23:00 UTC 那次排程實跑，三個品牌合計查到 20 筆結果、全部略過，
# 但 log 只印出「查到 N 筆、略過 N 筆」這種總數，沒辦法回答「被略過的商品
# 標題到底長怎樣、卡在哪一關」。以下測試釘住補上的逐筆 log.info。

def test_logs_the_title_and_reason_for_each_skipped_item(caplog):
    payload = {"prods": [
        {"Id": "1", "name": "滿意寶寶 日本境內版 L 54片", "price": 999},
        {"Id": "2", "name": "零觸感瞬吸 褲型紙尿褲 2片/包 M-2XL(褲型/紙尿褲/尿布/體驗包)", "price": 29},
        {"Id": "3", "name": "滿意寶寶 日本境內版 M 箱購組合", "price": 999},
        {"Id": "4", "name": "滿意寶寶 日本境內版 M 62片", "price": 5000000},
    ]}
    session = _FakeSession(_FakeResponse(payload))
    with caplog.at_level("INFO"):
        assert pchome.fetch_brand("滿意寶寶日本境內版", session=session) == []
    assert "L 54片" in caplog.text and "判定不是 M 號" in caplog.text
    assert "體驗包" in caplog.text and "不可靠" in caplog.text
    assert "箱購組合" in caplog.text and "找不到片數" in caplog.text
    assert "62片" in caplog.text and "不合理" in caplog.text
    # 總結那行要說出「哪一關擋了幾筆」，不能只給總數
    assert "各關卡擋下的筆數" in caplog.text
    assert "判定不是 M 號 1" in caplog.text


def test_logs_which_fields_are_missing_when_name_or_price_absent(caplog):
    payload = {"prods": [{"Id": "1", "price": 999}]}
    session = _FakeSession(_FakeResponse(payload))
    with caplog.at_level("INFO"):
        assert pchome.fetch_brand("滿意寶寶日本境內版", session=session) == []
    assert "沒有標題" in caplog.text and "price 欄位＝999" in caplog.text


def test_caps_results_per_brand():
    items = [{"Id": str(i), "name": f"滿意寶寶 日本境內版 M {60 + i}片", "price": 500 + i}
             for i in range(pchome.MAX_RESULTS_PER_BRAND + 5)]
    session = _FakeSession(_FakeResponse({"prods": items}))
    quotes = pchome.fetch_brand("滿意寶寶日本境內版", session=session)
    assert len(quotes) == pchome.MAX_RESULTS_PER_BRAND


# --- 失敗要安靜地回傳空 list，不能拋例外 ----------------------------------------

def test_missing_prods_key_returns_empty_not_an_exception():
    session = _FakeSession(_FakeResponse({"totalRows": 0}))
    assert pchome.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_non_list_prods_returns_empty_not_an_exception():
    session = _FakeSession(_FakeResponse({"prods": "unexpected string"}))
    assert pchome.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_network_exception_returns_empty_not_raised():
    session = _FakeSession(exc=requests.ConnectionError("boom"))
    assert pchome.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_timeout_returns_empty_not_raised():
    session = _FakeSession(exc=requests.Timeout("too slow"))
    assert pchome.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_http_error_status_returns_empty_not_raised():
    session = _FakeSession(_FakeResponse(status=500))
    assert pchome.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_invalid_json_returns_empty_not_raised():
    session = _FakeSession(_FakeResponse(json_error=True))
    assert pchome.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_unconfigured_brand_returns_empty_without_hitting_network():
    session = _FakeSession()
    assert pchome.fetch_brand("不存在的品牌", session=session) == []
    assert session.calls == []


# --- fetch_all ------------------------------------------------------------------

def test_fetch_all_aggregates_across_brands():
    payload = {"prods": [{"Id": "1", "name": "測試 M 60片", "price": 400}]}
    session = _FakeSession(_FakeResponse(payload))
    quotes = pchome.fetch_all(["滿意寶寶日本境內版", "Aiwibi", "奢寵幫"], session=session)
    assert len(quotes) == 3
    assert {q["brand"] for q in quotes} == {"滿意寶寶日本境內版", "Aiwibi", "奢寵幫"}


def test_fetch_all_keeps_going_when_one_brand_raises_unexpectedly(monkeypatch):
    """就算 fetch_brand 自己的防呆有漏洞（這裡故意讓它拋例外模擬那種情況），
    fetch_all 仍要繼續查完其他品牌，不能讓整批查價中斷。"""
    calls = []

    def fake_fetch_brand(brand, query=None, session=None):
        calls.append(brand)
        if brand == "Aiwibi":
            raise RuntimeError("模擬 fetch_brand 防呆漏掉的例外")
        return [{"brand": brand}]

    monkeypatch.setattr(pchome, "fetch_brand", fake_fetch_brand)
    quotes = pchome.fetch_all(["滿意寶寶日本境內版", "Aiwibi", "奢寵幫"])
    assert calls == ["滿意寶寶日本境內版", "Aiwibi", "奢寵幫"]
    assert {q["brand"] for q in quotes} == {"滿意寶寶日本境內版", "奢寵幫"}
