"""蝦皮購物 的自動查價來源。

**目前策略：headless browser。** 2026-08-18 直接用 `requests` 打蝦皮的
搜尋 API（`SEARCH_URL`）在 GitHub Actions 上實測收到 `HTTP 403
Forbidden`——是連線層級被拒，程式碼的防呆邏輯沒問題，是蝦皮真的擋下了
這種簡單請求（詳見下方「先前的失敗記錄」）。2026-08-19 改用 Playwright
開一個真正的 headless Chromium 分頁載入蝦皮的搜尋頁，讓頁面自己的前端
JS 去呼叫這支搜尋 API，再攔截那個回應的 JSON（見 `_browser.
fetch_intercepted_json`）——這樣可以直接沿用原本就寫好、也還沒被推翻
的 JSON 解析邏輯（`_parse_payload`），只換掉「怎麼拿到這包 JSON」這一
層，不用另外刻一套解析真實渲染頁面 DOM 的邏輯。

**這個策略本身還沒有機會驗證過。** 「一個真正的瀏覽器 session（完整
TLS 指紋、cookie、JS 執行）能不能繞過蝦皮這層防護」只是一個合理的猜測，
不是已驗證的結論——開發用的沙盒環境連 headless browser 都連不到
shopee.tw（在 CONNECT 階段被沙盒自己的網路政策擋下，不是蝦皮擋的），
要等下一次真實的 GitHub Actions 執行、看攔不攔得到 `search_items` 這支
API 的回應，才會知道行不行。

**先前的失敗記錄（2026-08-18，純 requests）：** 三個品牌的查詢全部收到
`HTTP 403 Forbidden`，不是原本猜測的「HTTP 200 但回應包含 `error` 欄位」
那種擋法——`_parse_payload()` 仍然保留了 `error` 欄位的檢查（防禦性
寫法，萬一 headless browser 這條路線遇到的是不同的擋法），但這條路徑
目前沒有實測案例。

**價格單位是另一個還沒被驗證過的假設**（因為到目前為止都還沒拿到過
真實回應）。蝦皮這個搜尋端點的價格欄位，公開資料普遍記載是以「元的
十萬分之一」為單位（也就是要除以 100000 才是實際售價）——如果係數是
錯的，換算出來的售價會離譜到不像一包尿布，`config.PLAUSIBLE_PACK_PRICE_RANGE`
這道關卡就是專門防這個。

**信任層級低於人工填寫**，跟其他來源一樣：見 `pipeline.load_all_prices`
的合併邏輯，同一天、同一品牌、同一平台，人工填的資料永遠蓋過這裡抓到的。
"""
from __future__ import annotations

import logging
from typing import Callable, Optional
from urllib.parse import quote

from ._browser import fetch_intercepted_json, new_browser
from ._common import (
    extract_piece_count,
    first,
    looks_like_a_plausible_pack_price,
    looks_like_size_m,
    looks_unreliable,
)

log = logging.getLogger(__name__)

PLATFORM_NAME = "蝦皮"
# 頁面路由，不是 API 端點——headless browser 載入這個網址，讓蝦皮自己的
# 前端 JS 去呼叫下面的搜尋 API，不是直接用程式打 API。
SEARCH_PAGE_URL = "https://shopee.tw/search?keyword={query}"
# 之前直接用 requests 打的 API 端點，現在只留著給 docstring／註解對照，
# 也是 headless browser 要攔截的回應網址關鍵字。
SEARCH_URL = "https://shopee.tw/api/v4/search/search_items"
API_URL_SUBSTRING = "search_items"
MAX_RESULTS_PER_BRAND = 5

# 蝦皮價格欄位的換算係數（未經驗證，見本檔開頭說明）。
PRICE_SCALE = 100000.0

# 跟 pchome.py 同樣的理由：品牌全名直接搜尋常常搜不到，這裡用比較接近
# 賣場實際下標題習慣的關鍵字。
SEARCH_QUERIES = {
    "滿意寶寶日本境內版": "滿意寶寶 日本境內版 M",
    "Aiwibi": "Aiwibi 愛薇彼 M",
    "奢寵幫": "奢寵幫 M",
}

# 測試用替身的簽名：(頁面網址, 要攔截的 API 網址關鍵字) -> JSON 或 None。
BrowserFetcher = Callable[[str, str], Optional[dict]]


def _parse_payload(brand: str, query: str, payload) -> list[dict]:
    """把蝦皮搜尋 API 的回應（不管是從真的請求還是從 headless browser
    攔截來的）轉成跟 manual_prices.csv 相同欄位的 dict 列表。純函式，
    不碰網路、不開瀏覽器，方便測試直接餵合成的 payload 進來。"""
    if not isinstance(payload, dict):
        log.warning("蝦皮來源：回應格式跟預期不符（不是物件），可能是被擋下改回傳了其他內容")
        return []

    error = payload.get("error")
    if error:
        log.warning(
            "蝦皮來源：品牌 %s（關鍵字「%s」）回應包含 error 欄位（%s：%s），"
            "很可能是防爬機制擋下這次請求，不是真的沒有商品",
            brand, query, error, payload.get("error_msg", ""),
        )
        return []

    items = first(payload, "items")
    if not isinstance(items, list):
        log.warning(
            "蝦皮來源：回應格式跟預期不符（找不到 items 陣列），"
            "可能是 API 改版了；回應最外層欄位＝%s",
            list(payload.keys()),
        )
        return []

    quotes: list[dict] = []
    skipped = 0
    for entry in items:
        if not isinstance(entry, dict):
            skipped += 1
            continue
        # 舊版 API 把商品欄位包在 item_basic 底下，新版本有時直接攤平在
        # 最外層——兩種都試，不確定哪一種才是現在會拿到的格式
        item = entry.get("item_basic") if isinstance(entry.get("item_basic"), dict) else entry

        name = first(item, "name")
        raw_price = first(item, "price", "price_min")
        item_id = first(item, "itemid")
        shop_id = first(item, "shopid")
        if not name or raw_price is None:
            skipped += 1
            continue
        if not looks_like_size_m(str(name)):
            skipped += 1
            continue
        if looks_unreliable(str(name)):
            skipped += 1
            continue
        piece_count = extract_piece_count(str(name))
        if piece_count is None:
            skipped += 1
            continue
        try:
            pack_price = float(raw_price) / PRICE_SCALE
        except (TypeError, ValueError):
            skipped += 1
            continue
        if not looks_like_a_plausible_pack_price(pack_price):
            skipped += 1
            continue

        url = f"https://shopee.tw/-i.{shop_id}.{item_id}" if shop_id and item_id else ""
        quotes.append({
            "brand": brand,
            "platform": PLATFORM_NAME,
            "product_name": str(name),
            "pack_price": round(pack_price, 2),
            "piece_count": piece_count,
            "url": url,
            "note": f"自動爬蟲（蝦皮搜尋 API，headless browser 攔截，關鍵字：{query}）；"
                    f"未經人工核對，正確性以商品頁面為準",
        })
        if len(quotes) >= MAX_RESULTS_PER_BRAND:
            break

    if not quotes:
        log.warning(
            "蝦皮來源：品牌 %s（關鍵字「%s」）查到 %d 筆結果，"
            "但沒有一筆同時符合「M 號」「標題找得到片數」與「售價合理」，整批略過（跳過 %d 筆）",
            brand, query, len(items), skipped,
        )
    else:
        log.info(
            "蝦皮來源：品牌 %s 取得 %d 筆報價（原始 %d 筆，過濾掉 %d 筆）",
            brand, len(quotes), len(items), skipped,
        )
    return quotes


def fetch_brand(brand: str, query: Optional[str] = None,
                 browser=None, browser_fetcher: Optional[BrowserFetcher] = None) -> list[dict]:
    """查一個品牌在蝦皮的報價，回傳與 manual_prices.csv 相同欄位的
    dict 列表（date 欄位由呼叫端補上，這裡不知道「今天」是哪一天）。

    `browser` 是呼叫端（通常是 `fetch_all`）已經開好的 headless browser
    執行個體，用來實際載入頁面、攔截 API 回應；`browser_fetcher` 是測試
    用的替身，簽名是 `(頁面網址, API 網址關鍵字) -> Optional[dict]`，
    有給就直接用它、不會真的去開瀏覽器。任何一種失敗（瀏覽器啟動失敗、
    頁面載入逾時、攔截不到目標回應、回應格式不對、找不到符合條件的
    商品）都回傳空 list 並記錄警告，絕不拋例外。"""
    query = query or SEARCH_QUERIES.get(brand)
    if not query:
        log.warning("蝦皮來源：品牌 %s 沒有設定搜尋關鍵字，略過", brand)
        return []

    fetch = browser_fetcher or (lambda page_url, api_substr: fetch_intercepted_json(browser, page_url, api_substr))
    page_url = SEARCH_PAGE_URL.format(query=quote(query))
    payload = fetch(page_url, API_URL_SUBSTRING)
    if payload is None:
        log.warning(
            "蝦皮來源：品牌 %s（關鍵字「%s」）headless browser 沒能攔截到搜尋 API 的回應"
            "（頁面載入失敗，或是蝦皮的 API 網址／呼叫時機跟預期不同）",
            brand, query,
        )
        return []
    return _parse_payload(brand, query, payload)


def fetch_all(brands: list[str], browser_fetcher: Optional[BrowserFetcher] = None) -> list[dict]:
    """對每個品牌各呼叫一次 `fetch_brand`，共用同一個 headless browser
    執行個體（省掉每個品牌重複啟動瀏覽器的時間與開銷），單一品牌出錯
    不影響其他品牌。`browser_fetcher` 有給的話（測試場景）完全不會去開
    真正的瀏覽器。"""
    quotes: list[dict] = []

    if browser_fetcher is not None:
        for brand in brands:
            try:
                quotes.extend(fetch_brand(brand, browser_fetcher=browser_fetcher))
            except Exception as e:  # noqa: BLE001
                log.warning("蝦皮來源：品牌 %s 發生未預期的例外（%s: %s），跳過這個品牌",
                            brand, type(e).__name__, e)
        return quotes

    browser_handle = new_browser()
    if browser_handle is None:
        log.warning("蝦皮來源：headless browser 啟動失敗，這次整批略過")
        return []
    try:
        for brand in brands:
            try:
                quotes.extend(fetch_brand(brand, browser=browser_handle.browser))
            except Exception as e:  # noqa: BLE001
                log.warning("蝦皮來源：品牌 %s 發生未預期的例外（%s: %s），跳過這個品牌",
                            brand, type(e).__name__, e)
    finally:
        browser_handle.close()
    return quotes
