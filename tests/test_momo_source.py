"""momo 自動查價來源的測試。

`momo.py` 開頭寫明了：純用 requests 抓靜態 HTML 已經證實走不通（momo
的搜尋頁是 Next.js 用戶端渲染），現在改用 headless browser 載入頁面、
等 JS 執行完，再把渲染後的 HTML 餵給解析邏輯。這裡分兩層測試：
`_parse_html` 是純函式，直接餵合成的 HTML 字串進去，驗證「假設對的時候
解析對」以及「假設錯的時候（找不到候選區塊、抓不到片數、格式不對）不會
炸掉、不會安靜地回傳錯資料」；`fetch_brand`／`fetch_all` 用 `html_fetcher`
替身驗證瀏覽器抓取失敗、成功兩種情況都有正確串接到 `_parse_html`，完全
不需要真的開瀏覽器、也打不到真實網路。這些測試都不能證明真實的 momo
頁面長這樣，也不能證明 headless browser 真的能讓 JS 跑完再擷取內容。
"""
from __future__ import annotations

from diaper_monitor.sources import momo


def _page(*items_html: str) -> str:
    return f"<html><body><ul>{''.join(items_html)}</ul></body></html>"


def _item(name, price, i_code="12345", price_prefix=""):
    """組一個跟 momo.py 假設的結構一致的商品區塊：<li> 包一個帶 title
    屬性的商品連結，附近有 $價格 文字。price_prefix 可以塞一段額外文字
    （例如劃線原價）模擬區塊裡不只一個價格的情況。"""
    return (f'<li><a href="/goods.momo?i_code={i_code}" title="{name}">圖片</a>'
            f'<p>{price_prefix}$ {price}</p></li>')


FINAL_URL = "https://m.momoshop.com.tw/search.momo?searchKeyword=x"


# --- _parse_html：正常解析 --------------------------------------------------------

def test_parses_a_well_formed_page():
    html = _page(_item("滿意寶寶 日本境內版 M 62片", "519"))
    quotes = momo._parse_html("滿意寶寶日本境內版", "q", html, FINAL_URL)
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
    quotes = momo._parse_html("Aiwibi", "q", html, FINAL_URL)
    assert len(quotes) == 1
    assert quotes[0]["pack_price"] == 999.0  # 釘住「取第一個」這個行為


def test_falls_back_to_link_text_when_title_attribute_is_missing():
    html = _page('<li><a href="/goods.momo?i_code=1">奢寵幫 M 38片</a><p>$780</p></li>')
    quotes = momo._parse_html("奢寵幫", "q", html, FINAL_URL)
    assert len(quotes) == 1
    assert quotes[0]["piece_count"] == 38


# --- 篩選：M 號 + 找得到片數 + 找得到價格 ----------------------------------------

def test_rejects_blocks_without_a_price():
    html = _page('<li><a href="/goods.momo?i_code=1" title="滿意寶寶 日本境內版 M 62片">連結</a></li>')
    assert momo._parse_html("滿意寶寶日本境內版", "q", html, FINAL_URL) == []


def test_rejects_items_without_a_recognizable_piece_count():
    html = _page(_item("滿意寶寶 日本境內版 M 箱購組合", "999"))
    assert momo._parse_html("滿意寶寶日本境內版", "q", html, FINAL_URL) == []


def test_rejects_items_that_are_not_size_m():
    html = _page(_item("滿意寶寶 日本境內版 L 54片", "999"))
    assert momo._parse_html("滿意寶寶日本境內版", "q", html, FINAL_URL) == []


def test_rejects_trial_size_packs():
    """跟 pchome.py／shopee.py 同一個真實案例，驗證共用的過濾邏輯在這裡也生效。"""
    html = _page(_item("零觸感瞬吸 褲型紙尿褲 2片/包 M-2XL(褲型/紙尿褲/尿布/體驗包)", "29"))
    assert momo._parse_html("Aiwibi", "q", html, FINAL_URL) == []


def test_rejects_box_purchases_with_a_pack_multiplier():
    html = _page(_item("奢寵幫 M 38片入*3包入(箱購)", "1520"))
    assert momo._parse_html("奢寵幫", "q", html, FINAL_URL) == []


def test_rejects_implausible_prices():
    html = _page(_item("滿意寶寶 日本境內版 M 62片", "5000000"))
    assert momo._parse_html("滿意寶寶日本境內版", "q", html, FINAL_URL) == []


def test_caps_results_per_brand():
    items = [_item(f"滿意寶寶 日本境內版 M {60 + i}片", str(500 + i), i_code=str(i))
             for i in range(momo.MAX_RESULTS_PER_BRAND + 5)]
    quotes = momo._parse_html("滿意寶寶日本境內版", "q", _page(*items), FINAL_URL)
    assert len(quotes) == momo.MAX_RESULTS_PER_BRAND


# --- 找不到候選區塊：跟「有候選但被篩掉」分開記錄 --------------------------------

def test_no_product_links_returns_empty_not_an_exception():
    """頁面結構完全跟預期不同時，回應裡連一個候選商品連結都找不到——
    這種情況要能回傳空 list，不能拋例外。"""
    html = "<html><body><div>找不到任何商品連結的頁面</div></body></html>"
    assert momo._parse_html("滿意寶寶日本境內版", "q", html, FINAL_URL) == []


def test_no_candidates_logs_diagnostic_details(caplog):
    """真的抓不到候選商品時，警告要帶足夠線索判斷問題出在哪一層
    （有沒有被重新導向、頁面標題、總連結數、原始 HTML 開頭）——
    不能只是一句「找不到」，那樣下次還是只能用猜的。"""
    html = "<html><head><title>找不到結果</title></head><body>無商品</body></html>"
    with caplog.at_level("WARNING"):
        momo._parse_html("滿意寶寶日本境內版", "q", html, "https://www.momoshop.com.tw/redirected")
    assert "redirected" in caplog.text
    assert "找不到結果" in caplog.text
    assert "總連結數=0" in caplog.text


def test_detects_the_real_nextjs_client_rendered_shell_page(caplog):
    """2026-08-18 實跑 GitHub Actions 時、還在用純 requests 的版本拿到的
    真實回應：momo 的行動版搜尋頁被 302 導去 www.momoshop.com.tw/search/
    <關鍵字>，回應是 Next.js 的空殼 HTML（帶 _next/static 資源連結），
    完全沒有商品連結。現在即使改用 headless browser，如果 JS 沒有真的
    執行完就被擷取內容，理論上還是會拿到這種殼頁面——這裡驗證還是能辨識
    出這個特徵，警告訊息要指出這是「JS 沒等到跑完」，不能只說「可能是
    頁面結構不同」。"""
    html = (
        '<!DOCTYPE html><html lang="zh-TW"><head><meta charSet="utf-8"/>'
        '<title>滿意寶寶 日本境內版 M - momo購物網 - 好評推薦 - 2026年8月</title>'
        '<link rel="stylesheet" href="/search/_next/static/css/62ed5c88d356ea37.css" '
        'data-precedence="next"/></head><body><div id="__next"></div></body></html>'
    )
    with caplog.at_level("WARNING"):
        assert momo._parse_html(
            "滿意寶寶日本境內版", "q", html,
            "https://www.momoshop.com.tw/search/%E6%BB%BF%E6%84%8F%E5%AF%B6%E5%AF%B6?viewport=mobile",
        ) == []
    assert "headless browser" in caplog.text
    assert "JS" in caplog.text


# --- fetch_brand：串接 headless browser 渲染結果 -----------------------------------

def test_fetch_brand_uses_html_fetcher_and_parses_result():
    html = _page(_item("測試 M 60片", "400"))
    calls = []

    def fake_fetcher(url):
        calls.append(url)
        return html, FINAL_URL

    quotes = momo.fetch_brand("滿意寶寶日本境內版", html_fetcher=fake_fetcher)
    assert len(quotes) == 1
    assert len(calls) == 1
    assert "searchKeyword=" in calls[0]


def test_fetch_brand_returns_empty_when_html_fetcher_returns_none():
    assert momo.fetch_brand("滿意寶寶日本境內版", html_fetcher=lambda url: None) == []


def test_unconfigured_brand_returns_empty_without_calling_fetcher():
    calls = []
    momo.fetch_brand("不存在的品牌", html_fetcher=lambda url: calls.append(1))
    assert calls == []


# --- fetch_all ------------------------------------------------------------------

def test_fetch_all_aggregates_across_brands_using_html_fetcher():
    html = _page(_item("測試 M 60片", "400"))
    quotes = momo.fetch_all(["滿意寶寶日本境內版", "Aiwibi", "奢寵幫"],
                             html_fetcher=lambda url: (html, FINAL_URL))
    assert len(quotes) == 3
    assert {q["brand"] for q in quotes} == {"滿意寶寶日本境內版", "Aiwibi", "奢寵幫"}


def test_fetch_all_keeps_going_when_one_brand_raises_unexpectedly(monkeypatch):
    calls = []

    def fake_fetch_brand(brand, query=None, browser=None, html_fetcher=None):
        calls.append(brand)
        if brand == "Aiwibi":
            raise RuntimeError("模擬 fetch_brand 防呆漏掉的例外")
        return [{"brand": brand}]

    monkeypatch.setattr(momo, "fetch_brand", fake_fetch_brand)
    quotes = momo.fetch_all(["滿意寶寶日本境內版", "Aiwibi", "奢寵幫"], html_fetcher=lambda url: None)
    assert calls == ["滿意寶寶日本境內版", "Aiwibi", "奢寵幫"]
    assert {q["brand"] for q in quotes} == {"滿意寶寶日本境內版", "奢寵幫"}


def test_fetch_all_returns_empty_when_browser_fails_to_start(monkeypatch):
    """沒給 html_fetcher 時，fetch_all 會嘗試真的開瀏覽器——這裡讓
    `new_browser` 回傳 None（模擬啟動失敗），驗證整批查價乖乖回傳空 list
    而不是炸掉，也不會真的嘗試連網路。"""
    monkeypatch.setattr(momo, "new_browser", lambda: None)
    assert momo.fetch_all(["滿意寶寶日本境內版"]) == []
