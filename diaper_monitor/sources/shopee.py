"""蝦皮購物 的自動查價來源。

**風險比 PChome 高得多，而且同樣沒機會在真實回應上驗證過。** 開發環境連
`shopee.tw` 也連不到（跟 PChome 一樣在 CONNECT 階段被沙盒的網路出口 403），
但蝦皮本身的防爬機制又公認比 PChome 積極得多：非瀏覽器的請求常常直接被
擋、回傳 CAPTCHA 頁面，或是回傳 HTTP 200 但夾帶 `error` 欄位的「偵測到異常
活動」訊息，而不是單純的連線失敗。`fetch_brand()` 因此多一關 PChome 沒有的
檢查——回應裡有 `error` 欄位就直接當失敗處理，不嘗試從裡面硬解資料。

**價格單位是另一個沒被驗證過的假設。** 蝦皮這個搜尋端點的價格欄位，公開資料
普遍記載是以「元的十萬分之一」為單位（也就是要除以 100000 才是實際售價），
但這個換算係數本身也只是抄公開資料、沒有實測過。如果係數是錯的，換算出來
的售價會離譜到不像一包尿布（例如趨近於 0 或暴衝到幾百萬元）——
`config.PLAUSIBLE_PACK_PRICE_RANGE` 這道關卡就是專門防這個。

**信任層級低於人工填寫**，跟 PChome 一樣：見 `pipeline.load_all_prices`
的合併邏輯，同一天、同一品牌、同一平台，人工填的資料永遠蓋過這裡抓到的。

**第一次真的在 GitHub Actions 上跑起來時，務必看 log**：如果每個品牌都印出
「回應包含 error 欄位」的警告，代表被蝦皮的防爬機制擋下來了，不是程式碼的
邏輯錯——這支爬蟲能不能實際派上用場，要看那次的 log 才知道。
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

from ._common import (
    extract_piece_count,
    first,
    looks_like_a_plausible_pack_price,
    looks_like_size_m,
    looks_unreliable,
)

log = logging.getLogger(__name__)

PLATFORM_NAME = "蝦皮"
SEARCH_URL = "https://shopee.tw/api/v4/search/search_items"
TIMEOUT_SECONDS = 10
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


def fetch_brand(brand: str, query: Optional[str] = None,
                 session: Optional[requests.Session] = None) -> list[dict]:
    """查一個品牌在蝦皮的報價，回傳與 manual_prices.csv 相同欄位的
    dict 列表（date 欄位由呼叫端補上，這裡不知道「今天」是哪一天）。

    任何一種失敗（連線、逾時、被防爬機制擋下、回應格式不對、找不到符合
    條件的商品）都回傳空 list 並記錄警告，絕不拋例外——一個品牌抓失敗不該
    讓其他品牌也抓不到，整批失敗也不該讓 scripts/run_diaper_monitor.py
    中斷。"""
    query = query or SEARCH_QUERIES.get(brand)
    if not query:
        log.warning("蝦皮來源：品牌 %s 沒有設定搜尋關鍵字，略過", brand)
        return []

    http = session or requests
    try:
        resp = http.get(
            SEARCH_URL,
            params={
                "by": "relevancy",
                "keyword": query,
                "limit": 20,
                "newest": 0,
                "order": "desc",
                "page_type": "search",
                "scenario": "PAGE_GLOBAL_SEARCH",
                "version": 2,
            },
            timeout=TIMEOUT_SECONDS,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://shopee.tw/",
            },
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:  # noqa: BLE001
        log.warning("蝦皮來源：查詢「%s」失敗（%s: %s）", query, type(e).__name__, e)
        return []

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
            "note": f"自動爬蟲（蝦皮搜尋 API，關鍵字：{query}）；未經人工核對，正確性以商品頁面為準",
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


def fetch_all(brands: list[str], session: Optional[requests.Session] = None) -> list[dict]:
    """對每個品牌各呼叫一次 `fetch_brand`，單一品牌出錯不影響其他品牌
    （理由跟 `pchome.fetch_all` 完全一樣，見那邊的說明）。"""
    quotes: list[dict] = []
    for brand in brands:
        try:
            quotes.extend(fetch_brand(brand, session=session))
        except Exception as e:  # noqa: BLE001
            log.warning("蝦皮來源：品牌 %s 發生未預期的例外（%s: %s），跳過這個品牌",
                        brand, type(e).__name__, e)
    return quotes
