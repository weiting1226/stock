"""momo 自動查價來源的測試。

`momo.py` 開頭寫明了：這是三個來源裡最不確定的一個——沒有已知的公開 JSON
搜尋 API，改成直接解析 HTML，多了一層「DOM 結構猜得對不對」的不確定性，
而且完全沒機會在真實頁面上驗證過。這裡的測試用的是**照程式碼自己的假設**
組出來的合成 HTML，驗證的是「假設對的時候解析對」以及「假設錯的時候
（找不到候選區塊、抓不到片數、格式不對）不會炸掉、不會安靜地回傳錯資料」，
不能證明真實的 momo 頁面長這樣。
"""
from __future__ import annotations

import requests

from diaper_monitor.sources import momo


class _FakeResponse:
    def __init__(self, text="", status=200, url="https://m.momoshop.com.tw/search.momo"):
        self.text = text
        self.status_code = status
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


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


def _page(*items_html: str) -> str:
    return f"<html><body><ul>{''.join(items_html)}</ul></body></html>"


def _item(name, price, i_code="12345", price_prefix=""):
    """組一個跟 momo.py 假設的結構一致的商品區塊：<li> 包一個帶 title
    屬性的商品連結，附近有 $價格 文字。price_prefix 可以塞一段額外文字
    （例如劃線原價）模擬區塊裡不只一個價格的情況。"""
    return (f'<li><a href="/goods.momo?i_code={i_code}" title="{name}">圖片</a>'
            f'<p>{price_prefix}$ {price}</p></li>')


# --- 正常解析 ------------------------------------------------------------------

def test_parses_a_well_formed_page():
    html = _page(_item("滿意寶寶 日本境內版 M 62片", "519"))
    session = _FakeSession(_FakeResponse(html))
    quotes = momo.fetch_brand("滿意寶寶日本境內版", session=session)
    assert len(quotes) == 1
    q = quotes[0]
    assert q["brand"] == "滿意寶寶日本境內版"
    assert q["platform"] == "momo購物網"
    assert q["piece_count"] == 62
    assert q["pack_price"] == 519.0
    assert q["url"] == "https://m.momoshop.com.tw/goods.momo?i_code=12345"
    assert "自動爬蟲" in q["note"]


def test_takes_the_first_price_when_a_block_has_more_than_one():
    """商品區塊常見「劃線原價 + 促銷價」兩個數字，這裡只取第一個符合的——
    這是沒驗證過的假設，測試至少要把這個行為釘住，改動時才看得出來。"""
    html = _page(_item("Aiwibi 愛薇彼 M 48片", "699", price_prefix="原價 $999 特價 "))
    session = _FakeSession(_FakeResponse(html))
    quotes = momo.fetch_brand("Aiwibi", session=session)
    assert len(quotes) == 1
    assert quotes[0]["pack_price"] == 999.0  # 釘住「取第一個」這個行為


def test_falls_back_to_link_text_when_title_attribute_is_missing():
    html = _page('<li><a href="/goods.momo?i_code=1">奢寵幫 M 38片</a><p>$780</p></li>')
    session = _FakeSession(_FakeResponse(html))
    quotes = momo.fetch_brand("奢寵幫", session=session)
    assert len(quotes) == 1
    assert quotes[0]["piece_count"] == 38


# --- 篩選：M 號 + 找得到片數 + 找得到價格 ----------------------------------------

def test_rejects_blocks_without_a_price():
    html = _page('<li><a href="/goods.momo?i_code=1" title="滿意寶寶 日本境內版 M 62片">連結</a></li>')
    session = _FakeSession(_FakeResponse(html))
    assert momo.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_rejects_items_without_a_recognizable_piece_count():
    html = _page(_item("滿意寶寶 日本境內版 M 箱購組合", "999"))
    session = _FakeSession(_FakeResponse(html))
    assert momo.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_rejects_items_that_are_not_size_m():
    html = _page(_item("滿意寶寶 日本境內版 L 54片", "999"))
    session = _FakeSession(_FakeResponse(html))
    assert momo.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_rejects_trial_size_packs():
    """跟 pchome.py／shopee.py 同一個真實案例，驗證共用的過濾邏輯在這裡也生效。"""
    html = _page(_item("零觸感瞬吸 褲型紙尿褲 2片/包 M-2XL(褲型/紙尿褲/尿布/體驗包)", "29"))
    session = _FakeSession(_FakeResponse(html))
    assert momo.fetch_brand("Aiwibi", session=session) == []


def test_rejects_box_purchases_with_a_pack_multiplier():
    html = _page(_item("奢寵幫 M 38片入*3包入(箱購)", "1520"))
    session = _FakeSession(_FakeResponse(html))
    assert momo.fetch_brand("奢寵幫", session=session) == []


def test_rejects_implausible_prices():
    html = _page(_item("滿意寶寶 日本境內版 M 62片", "5000000"))
    session = _FakeSession(_FakeResponse(html))
    assert momo.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_caps_results_per_brand():
    items = [_item(f"滿意寶寶 日本境內版 M {60 + i}片", str(500 + i), i_code=str(i))
             for i in range(momo.MAX_RESULTS_PER_BRAND + 5)]
    session = _FakeSession(_FakeResponse(_page(*items)))
    quotes = momo.fetch_brand("滿意寶寶日本境內版", session=session)
    assert len(quotes) == momo.MAX_RESULTS_PER_BRAND


# --- 找不到候選區塊：跟「有候選但被篩掉」分開記錄 --------------------------------

def test_no_product_links_returns_empty_not_an_exception():
    """頁面結構完全跟預期不同（或需要 JS 才有內容）時，回應裡連一個候選
    商品連結都找不到——這種情況要能回傳空 list，不能拋例外。"""
    html = "<html><body><div>找不到任何商品連結的頁面</div></body></html>"
    session = _FakeSession(_FakeResponse(html))
    assert momo.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_no_candidates_logs_diagnostic_details(caplog):
    """真的抓不到候選商品時，警告要帶足夠線索判斷問題出在哪一層
    （有沒有被重新導向、頁面標題、總連結數、原始 HTML 開頭）——
    不能只是一句「找不到」，那樣下次還是只能用猜的。"""
    html = "<html><head><title>找不到結果</title></head><body>無商品</body></html>"
    session = _FakeSession(_FakeResponse(html, url="https://m.momoshop.com.tw/redirected.momo"))
    with caplog.at_level("WARNING"):
        momo.fetch_brand("滿意寶寶日本境內版", session=session)
    assert "redirected.momo" in caplog.text
    assert "找不到結果" in caplog.text
    assert "總連結數=0" in caplog.text


# --- 失敗要安靜地回傳空 list，不能拋例外 ----------------------------------------

def test_network_exception_returns_empty_not_raised():
    session = _FakeSession(exc=requests.ConnectionError("boom"))
    assert momo.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_timeout_returns_empty_not_raised():
    session = _FakeSession(exc=requests.Timeout("too slow"))
    assert momo.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_http_error_status_returns_empty_not_raised():
    session = _FakeSession(_FakeResponse(status=500))
    assert momo.fetch_brand("滿意寶寶日本境內版", session=session) == []


def test_unconfigured_brand_returns_empty_without_hitting_network():
    session = _FakeSession()
    assert momo.fetch_brand("不存在的品牌", session=session) == []
    assert session.calls == []


# --- fetch_all ------------------------------------------------------------------

def test_fetch_all_aggregates_across_brands():
    html = _page(_item("測試 M 60片", "400"))
    session = _FakeSession(_FakeResponse(html))
    quotes = momo.fetch_all(["滿意寶寶日本境內版", "Aiwibi", "奢寵幫"], session=session)
    assert len(quotes) == 3
    assert {q["brand"] for q in quotes} == {"滿意寶寶日本境內版", "Aiwibi", "奢寵幫"}


def test_fetch_all_keeps_going_when_one_brand_raises_unexpectedly(monkeypatch):
    calls = []

    def fake_fetch_brand(brand, query=None, session=None):
        calls.append(brand)
        if brand == "Aiwibi":
            raise RuntimeError("模擬 fetch_brand 防呆漏掉的例外")
        return [{"brand": brand}]

    monkeypatch.setattr(momo, "fetch_brand", fake_fetch_brand)
    quotes = momo.fetch_all(["滿意寶寶日本境內版", "Aiwibi", "奢寵幫"])
    assert calls == ["滿意寶寶日本境內版", "Aiwibi", "奢寵幫"]
    assert {q["brand"] for q in quotes} == {"滿意寶寶日本境內版", "奢寵幫"}
