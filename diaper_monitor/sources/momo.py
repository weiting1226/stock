"""momo購物網 的自動查價來源。

**風險評估：三個來源裡最不確定的一個。** PChome 用的是公開 JSON 搜尋 API
（已在 2026-08-18 驗證過連線與欄位假設可用，見 `pchome.py` 開頭）；蝦皮的
API／欄位假設是對的，只是被邊緣防護擋在門外（HTTP 403，見 `shopee.py`
開頭）——但 momo 沒有已知、公開、穩定的 JSON 搜尋 API，這裡改用直接解析
HTML 搜尋結果頁。這代表多了一層前兩個來源都沒有的不確定性：不只是
「連不連得到」，還有「頁面的 DOM 結構猜得對不對」，而 DOM 結構通常比
JSON API 的欄位名稱更容易在改版時跑掉，而且完全沒有公開文件可以參考。

請求打的是行動版 `m.momoshop.com.tw/search.momo`，但**實測證實**（見下）
會被 302 導向桌機版的 `www.momoshop.com.tw/search/<關鍵字>`。

解析邏輯寫得比另外兩個來源更寬鬆：不依賴猜測的 CSS class 名稱（那種
名稱完全沒公開文件可查，改版就跑掉），改用結構特徵——商品詳情連結
（href 帶 `i_code=` 或走 `/goods.momo`）加上同一個區塊裡的 NT$ 價格文字
——去找候選商品區塊。**但這套邏輯目前實測完全派不上用場**（見下）。
另外，一個商品區塊裡常常不只一個價格（例如劃線的原價＋促銷價），這裡
只取區塊文字裡第一個符合 `$數字` 樣式的價格，哪一個排在前面同樣沒有
驗證過——不過這一段目前根本走不到，見下。

**實測結果：2026-08-18 在 GitHub Actions 上證實這條路線走不通。** 三個
品牌的查詢都拿到 `HTTP 200`、標題也正確對應搜尋關鍵字，代表請求本身、
重新導向都正常；但回應的原始 HTML 裡總共 0 個 `<a href>` 連結，開頭
片段看得出是 Next.js 的殼頁面（`_next/static/css`／`data-precedence=
"next"` 這類標記）。也就是說 momo 現在的搜尋頁是用戶端渲染
（client-side rendered）：伺服器只回一個幾乎空的 HTML 殼，商品清單要
瀏覽器執行 JS 之後才會被塞進 DOM。純用 `requests` 抓靜態 HTML 這個做法
對現在的 momo 搜尋頁**架構上就走不通**，不是 CSS 選擇器或 URL 猜錯（見
`_looks_like_client_rendered_shell`／`fetch_brand` 裡對這個情況的專門
判斷與 log 訊息）。要修好只有換成能執行 JS 的做法（例如 headless
browser）一途，而目前沒有做——這支爬蟲短期內大概率抓不到任何資料。

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


NEXTJS_SHELL_MARKER = "_next/static"


def _looks_like_client_rendered_shell(html: str) -> bool:
    """2026-08-18 實跑後證實：`m.momoshop.com.tw/search.momo` 會被 302
    重新導向到 `www.momoshop.com.tw/search/<關鍵字>`——一個 Next.js 網站。
    伺服器直接回應的 HTML 只有殼（帶 `_next/static` 開頭的 CSS／JS 資源
    連結），完全沒有商品連結；真正的商品清單要等瀏覽器執行 JS 之後才會
    被塞進 DOM。這代表拿到 0 個候選區塊不是「頁面結構猜錯」，而是「純
    HTTP GET 解析靜態 HTML 這條路線，對現在的 momo 搜尋頁根本走不通」。"""
    return NEXTJS_SHELL_MARKER in html


def _diagnose(resp: requests.Response, soup: BeautifulSoup, html: str) -> str:
    """在「找不到候選商品連結」時，把足夠診斷問題出在哪一層的線索塞進
    警告訊息：有沒有被重新導向、頁面標題（常常能看出是不是被導去搜尋結果
    以外的頁面，例如首頁或錯誤頁）、整份回應裡到底有沒有 <a href>（完全
    沒有的話多半是要靠 JS 才會出現內容），以及開頭一小段原始 HTML。
    這是 2026-08-18 第一次實跑「找不到任何商品連結」之後才加的——與其
    再猜一次 DOM 結構，不如直接把回應本身的線索記下來。"""
    title = soup.title.get_text(strip=True) if soup.title else "(無 <title>)"
    total_links = len(soup.find_all("a", href=True))
    snippet = re.sub(r"\s+", " ", html[:500]).strip()
    return (
        f"最終網址={resp.url}｜回應狀態={resp.status_code}｜頁面標題={title!r}｜"
        f"HTML 長度={len(html)}｜總連結數={total_links}｜開頭片段={snippet!r}"
    )


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
        if _looks_like_client_rendered_shell(html):
            log.warning(
                "momo 來源：品牌 %s（關鍵字「%s」）拿到的是 Next.js 用戶端渲染的空殼頁面"
                "（HTML 裡看得到 %s 開頭的資源連結，但完全沒有商品連結）——"
                "2026-08-18 實測證實這是目前 momo 搜尋頁的架構，商品清單要執行 JS 才會出現，"
                "純解析靜態 HTML 這條路線走不通，不是解析規則猜錯，需要換成能執行 JS 的方式"
                "（例如 headless browser）才有機會抓到資料。%s",
                brand, query, NEXTJS_SHELL_MARKER, _diagnose(resp, soup, html),
            )
        else:
            log.warning(
                "momo 來源：品牌 %s（關鍵字「%s」）在回應裡找不到任何商品連結——"
                "可能是頁面結構跟預期不同，或是被導去了別的頁面。%s",
                brand, query, _diagnose(resp, soup, html),
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
