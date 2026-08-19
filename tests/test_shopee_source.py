"""蝦皮自動查價來源的測試。

`shopee.py` 開頭寫明了：純用 requests 打搜尋 API 已經證實會被 403 擋下，
現在改用 headless browser 載入搜尋頁、攔截頁面自己呼叫的 API 回應。這裡
分兩層測試：`_parse_payload` 是純函式，直接餵合成的 JSON payload 進去，
驗證「假設對的時候解析對」以及「假設錯的時候不會炸掉」；`fetch_brand`／
`fetch_all` 用 `browser_fetcher` 替身驗證瀏覽器抓取失敗、成功兩種情況都
有正確串接到 `_parse_payload`，完全不需要真的開瀏覽器、也打不到真實
網路（開發環境的出口本來就把 shopee.tw 擋了，不管是 requests 還是
headless browser）。
"""
from __future__ import annotations

from diaper_monitor.sources import shopee


# --- _parse_payload：正常解析 ----------------------------------------------------

def test_parses_a_well_formed_payload():
    payload = {"items": [
        {"item_basic": {"name": "滿意寶寶 日本境內版 M 62片", "price": 51900000, "itemid": 111, "shopid": 222}},
    ]}
    quotes = shopee._parse_payload("滿意寶寶日本境內版", "滿意寶寶 日本境內版 M", payload)
    assert len(quotes) == 1
    q = quotes[0]
    assert q["brand"] == "滿意寶寶日本境內版"
    assert q["platform"] == "蝦皮"
    assert q["piece_count"] == 62
    assert q["pack_price"] == 519.0
    assert q["url"] == "https://shopee.tw/-i.222.111"
    assert "自動爬蟲" in q["note"]


def test_tolerates_items_flattened_without_item_basic_wrapper():
    payload = {"items": [
        {"name": "Aiwibi 愛薇彼 M 48片", "price": 30800000, "itemid": 1, "shopid": 2},
    ]}
    quotes = shopee._parse_payload("Aiwibi", "Aiwibi 愛薇彼 M", payload)
    assert len(quotes) == 1
    assert quotes[0]["piece_count"] == 48


def test_tolerates_price_min_field_name():
    payload = {"items": [
        {"name": "奢寵幫 M 60片", "price_min": 78000000, "itemid": 1, "shopid": 2},
    ]}
    quotes = shopee._parse_payload("奢寵幫", "奢寵幫 M", payload)
    assert len(quotes) == 1


# --- _parse_payload：篩選 ----------------------------------------------------------

def test_rejects_items_without_a_recognizable_piece_count():
    payload = {"items": [{"name": "滿意寶寶 日本境內版 M 箱購組合", "price": 99900000, "itemid": 1, "shopid": 2}]}
    assert shopee._parse_payload("滿意寶寶日本境內版", "q", payload) == []


def test_rejects_items_that_are_not_size_m():
    payload = {"items": [{"name": "滿意寶寶 日本境內版 L 54片", "price": 99900000, "itemid": 1, "shopid": 2}]}
    assert shopee._parse_payload("滿意寶寶日本境內版", "q", payload) == []


def test_rejects_trial_size_packs():
    payload = {"items": [{
        "name": "零觸感瞬吸 褲型紙尿褲 2片/包 M-2XL(褲型/紙尿褲/尿布/體驗包)",
        "price": 2900000, "itemid": 1, "shopid": 2,
    }]}
    assert shopee._parse_payload("Aiwibi", "q", payload) == []


def test_rejects_box_purchases_with_a_pack_multiplier():
    payload = {"items": [{
        "name": "奢寵幫 M 38片入*3包入(箱購)", "price": 152000000, "itemid": 1, "shopid": 2,
    }]}
    assert shopee._parse_payload("奢寵幫", "q", payload) == []


def test_rejects_implausible_prices():
    """換算係數如果錯了，算出來的售價會離譜到不像一包尿布——這裡故意用
    一個「係數是對的」但售價本身就離譜的例子，驗證合理範圍檢查會擋下它。"""
    payload = {"items": [{
        "name": "滿意寶寶 日本境內版 M 62片", "price": 500000000000, "itemid": 1, "shopid": 2,
    }]}
    assert shopee._parse_payload("滿意寶寶日本境內版", "q", payload) == []


def test_caps_results_per_brand():
    items = [{"name": f"滿意寶寶 日本境內版 M {60 + i}片", "price": (500 + i) * 100000,
              "itemid": i, "shopid": 1}
             for i in range(shopee.MAX_RESULTS_PER_BRAND + 5)]
    quotes = shopee._parse_payload("滿意寶寶日本境內版", "q", {"items": items})
    assert len(quotes) == shopee.MAX_RESULTS_PER_BRAND


# --- _parse_payload：格式不對／被擋下，要回傳空 list 不能炸掉 ---------------------

def test_non_dict_payload_returns_empty():
    assert shopee._parse_payload("滿意寶寶日本境內版", "q", "unexpected string") == []


def test_error_envelope_returns_empty():
    payload = {"error": 4, "error_msg": "blocked"}
    assert shopee._parse_payload("滿意寶寶日本境內版", "q", payload) == []


def test_missing_items_key_returns_empty():
    assert shopee._parse_payload("滿意寶寶日本境內版", "q", {"totalCount": 0}) == []


def test_non_list_items_returns_empty():
    assert shopee._parse_payload("滿意寶寶日本境內版", "q", {"items": "unexpected"}) == []


# --- fetch_brand：串接 headless browser 攔截結果 ----------------------------------

def test_fetch_brand_uses_browser_fetcher_and_parses_result():
    payload = {"items": [{"name": "測試 M 60片", "price": 40000000, "itemid": 1, "shopid": 2}]}
    calls = []

    def fake_fetcher(page_url, api_substr):
        calls.append((page_url, api_substr))
        return payload

    quotes = shopee.fetch_brand("滿意寶寶日本境內版", browser_fetcher=fake_fetcher)
    assert len(quotes) == 1
    assert calls == [("https://shopee.tw/search?keyword=%E6%BB%BF%E6%84%8F%E5%AF%B6%E5%AF%B6%20%E6%97%A5%E6%9C%AC%E5%A2%83%E5%85%A7%E7%89%88%20M", "search_items")]


def test_fetch_brand_returns_empty_when_browser_fetcher_returns_none():
    quotes = shopee.fetch_brand("滿意寶寶日本境內版", browser_fetcher=lambda page_url, api_substr: None)
    assert quotes == []


def test_unconfigured_brand_returns_empty_without_calling_fetcher():
    calls = []
    shopee.fetch_brand("不存在的品牌", browser_fetcher=lambda page_url, api_substr: calls.append(1))
    assert calls == []


# --- fetch_all ------------------------------------------------------------------

def test_fetch_all_aggregates_across_brands_using_browser_fetcher():
    payload = {"items": [{"name": "測試 M 60片", "price": 40000000, "itemid": 1, "shopid": 2}]}
    quotes = shopee.fetch_all(["滿意寶寶日本境內版", "Aiwibi", "奢寵幫"],
                               browser_fetcher=lambda page_url, api_substr: payload)
    assert len(quotes) == 3
    assert {q["brand"] for q in quotes} == {"滿意寶寶日本境內版", "Aiwibi", "奢寵幫"}


def test_fetch_all_keeps_going_when_one_brand_raises_unexpectedly(monkeypatch):
    calls = []

    def fake_fetch_brand(brand, query=None, browser=None, browser_fetcher=None):
        calls.append(brand)
        if brand == "Aiwibi":
            raise RuntimeError("模擬 fetch_brand 防呆漏掉的例外")
        return [{"brand": brand}]

    monkeypatch.setattr(shopee, "fetch_brand", fake_fetch_brand)
    quotes = shopee.fetch_all(["滿意寶寶日本境內版", "Aiwibi", "奢寵幫"], browser_fetcher=lambda *a: None)
    assert calls == ["滿意寶寶日本境內版", "Aiwibi", "奢寵幫"]
    assert {q["brand"] for q in quotes} == {"滿意寶寶日本境內版", "奢寵幫"}


def test_fetch_all_returns_empty_when_browser_fails_to_start(monkeypatch):
    """沒給 browser_fetcher 時，fetch_all 會嘗試真的開瀏覽器——這裡讓
    `new_browser` 回傳 None（模擬啟動失敗），驗證整批查價乖乖回傳空 list
    而不是炸掉，也不會真的嘗試連網路。"""
    monkeypatch.setattr(shopee, "new_browser", lambda: None)
    assert shopee.fetch_all(["滿意寶寶日本境內版"]) == []
