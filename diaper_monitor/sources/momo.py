"""momo購物網 的自動查價來源。

**風險評估：三個來源裡最不確定的一個。** PChome 用的是公開 JSON 搜尋 API
（已在 2026-08-18 驗證過連線與欄位假設可用，見 `pchome.py` 開頭）；蝦皮的
API／欄位假設是對的，只是被邊緣防護擋在門外（HTTP 403，見 `shopee.py`
開頭）——但 momo 沒有已知、公開、穩定的 JSON 搜尋 API，這裡改用直接解析
HTML 搜尋結果頁。這代表多了一層前兩個來源都沒有的不確定性：不只是
「連不連得到」，還有「頁面的 DOM 結構猜得對不對」，而 DOM 結構通常比
JSON API 的欄位名稱更容易在改版時跑掉，而且完全沒有公開文件可以參考。

搜尋頁用行動版 `m.momoshop.com.tw/search.momo`，不是桌機版——這個網址格式
不是憑空猜的：2026-08 稍早研究這幾個品牌的市售通路時，Web Search 實際
索引到、真的存在這個頁面（見同一次對話的查價記錄）。但「搜尋引擎索引到
這個網址存在」不等於「用程式直接 GET 也能拿到一樣的內容」——行動版是否
需要額外標頭、會不會被導去別的頁面、內容是不是伺服器端直接算好的 HTML
（而非需要執行 JS 才會出現），一樣完全沒驗證過。

解析邏輯因此寫得比另外兩個來源更寬鬆：不依賴猜測的 CSS class 名稱（那種
名稱完全沒公開文件可查，改版就跑掉），改用結構特徵——商品詳情連結
（href 帶 `i_code=` 或走 `/goods.momo`）加上同一個區塊裡的 NT$ 價格文字
——去找候選商品區塊。找到 0 個候選區塊時會特別記錄「可能是頁面結構不對
或需要 JS 才能看到內容」，跟「找到候選但沒有一個通過 M 號／片數篩選」的
警告分開，方便之後從 log 判斷問題出在哪一層。**另外，一個商品區塊裡常常
不只一個價格（例如劃線的原價＋促銷價），這裡只取區塊文字裡第一個符合
`$數字` 樣式的價格，哪一個排在前面同樣沒有驗證過。**

**信任層級低於人工填寫**，跟另外兩個來源一樣：見 `pipeline.load_all_prices`
的合併邏輯，同一天、同一品牌、同一平台，人工填的資料永遠蓋過這裡抓到的。
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ._common import (
    extract_piece_count,
    looks_like_a_plausible_pack_price,
    looks_like_size_m,
    looks_unreliable,
)

log = logging.getLogger(__name__)

PLATFORM_NAME = "momo購物網"
SEARCH_URL = "https://m.momoshop.com.tw/search.momo"
BASE_URL = "https://m.momoshop.com.tw"
TIMEOUT_SECONDS = 10
MAX_RESULTS_PER_BRAND = 5

PRICE_PATTERN = re.compile(r"\$\s*([\d,]+)")

# 跟 pchome.py／shopee.py 同樣的理由：品牌全名直接搜尋常常搜不到，這裡用
# 比較接近賣場實際下標題習慣的關鍵字。
SEARCH_QUERIES = {
    "滿意寶寶日本境內版": "滿意寶寶 日本境內版 M",
    "Aiwibi": "Aiwibi 愛薇彼 M",
    "奢寵幫": "奢寵幫 M",
}


def _candidate_blocks(soup: BeautifulSoup) -> list[tuple]:
    """找可能是「一個商品」的區塊：包含商品詳情連結的最小容器（li 或 div）。
    不猜 class 名稱，只靠連結的網址形狀——momo 商品詳情頁慣用 `i_code`
    當商品代碼參數，這點比 CSS class 更不容易隨改版跑掉。"""
    blocks = []
    seen_ids = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "i_code=" not in href and "/goods.momo" not in href:
            continue
        block = a.find_parent(["li", "div"])
        if block is None or id(block) in seen_ids:
            continue
        seen_ids.add(id(block))
        blocks.append((a, block))
    return blocks


def fetch_brand(brand: str, query: Optional[str] = None,
                 session: Optional[requests.Session] = None) -> list[dict]:
    """查一個品牌在 momo 的報價，回傳與 manual_prices.csv 相同欄位的
    dict 列表（date 欄位由呼叫端補上，這裡不知道「今天」是哪一天）。

    任何一種失敗（連線、逾時、HTML 解析失敗、找不到符合條件的商品）都
    回傳空 list 並記錄警告，絕不拋例外——一個品牌抓失敗不該讓其他品牌也
    抓不到，整批失敗也不該讓 scripts/run_diaper_monitor.py 中斷。"""
    query = query or SEARCH_QUERIES.get(brand)
    if not query:
        log.warning("momo 來源：品牌 %s 沒有設定搜尋關鍵字，略過", brand)
        return []

    http = session or requests
    try:
        resp = http.get(
            SEARCH_URL,
            params={"searchKeyword": query, "searchType": 1, "curPage": 1},
            timeout=TIMEOUT_SECONDS,
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"},
        )
        resp.raise_for_status()
        html = resp.text
    except Exception as e:  # noqa: BLE001
        log.warning("momo 來源：查詢「%s」失敗（%s: %s）", query, type(e).__name__, e)
        return []

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:  # noqa: BLE001
        log.warning("momo 來源：查詢「%s」的回應無法解析為 HTML（%s: %s）", query, type(e).__name__, e)
        return []

    candidates = _candidate_blocks(soup)
    if not candidates:
        log.warning(
            "momo 來源：品牌 %s（關鍵字「%s」）在回應裡找不到任何商品連結——"
            "可能是頁面結構跟預期不同、需要執行 JS 才看得到內容，或是被導去了別的頁面",
            brand, query,
        )
        return []

    quotes: list[dict] = []
    skipped = 0
    for link, block in candidates:
        name = link.get("title") or link.get_text(strip=True)
        if not name:
            skipped += 1
            continue
        price_match = PRICE_PATTERN.search(block.get_text(" ", strip=True))
        if not price_match:
            skipped += 1
            continue
        if not looks_like_size_m(name):
            skipped += 1
            continue
        if looks_unreliable(name):
            skipped += 1
            continue
        piece_count = extract_piece_count(name)
        if piece_count is None:
            skipped += 1
            continue
        try:
            pack_price = float(price_match.group(1).replace(",", ""))
        except ValueError:
            skipped += 1
            continue
        if not looks_like_a_plausible_pack_price(pack_price):
            skipped += 1
            continue

        quotes.append({
            "brand": brand,
            "platform": PLATFORM_NAME,
            "product_name": name,
            "pack_price": pack_price,
            "piece_count": piece_count,
            "url": urljoin(BASE_URL, link["href"]),
            "note": f"自動爬蟲（momo 搜尋頁 HTML 解析，關鍵字：{query}）；未經人工核對，正確性以商品頁面為準",
        })
        if len(quotes) >= MAX_RESULTS_PER_BRAND:
            break

    if not quotes:
        log.warning(
            "momo 來源：品牌 %s（關鍵字「%s」）找到 %d 個候選商品區塊，"
            "但沒有一個同時符合「M 號」「標題找得到片數」「找得到價格」與「售價合理」，"
            "整批略過（跳過 %d 個）",
            brand, query, len(candidates), skipped,
        )
    else:
        log.info(
            "momo 來源：品牌 %s 取得 %d 筆報價（候選 %d 個，過濾掉 %d 個）",
            brand, len(quotes), len(candidates), skipped,
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
            log.warning("momo 來源：品牌 %s 發生未預期的例外（%s: %s），跳過這個品牌",
                        brand, type(e).__name__, e)
    return quotes
