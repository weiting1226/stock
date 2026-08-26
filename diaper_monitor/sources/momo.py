"""momo購物網 的自動查價來源。

**目前策略：headless browser。** 2026-08-18 實測證實：純用 `requests`
打 `m.momoshop.com.tw/search.momo` 會被 302 導向桌機版的
`www.momoshop.com.tw/search/<關鍵字>`——一個 Next.js 網站，伺服器只回
應一個幾乎空的 HTML 殼（帶 `_next/static` 開頭的資源連結），完全沒有
商品連結；真正的商品清單要瀏覽器執行 JS 之後才會被塞進 DOM（詳見下方
「先前的失敗記錄」與 `_looks_like_client_rendered_shell`）。2026-08-19
改用 Playwright 開一個真正的 headless Chromium 分頁載入搜尋頁、等 JS
執行完，再把「JS 跑完之後」的 HTML（`page.content()`，不是原始回應）
拿去餵給原本就寫好的 DOM 解析邏輯（`_parse_html`：商品詳情連結＋NT$
價格文字這套結構特徵，見 `_candidate_blocks`）。

**這個策略本身還沒有機會驗證過。** 「JS 執行完之後 `page.content()`
裡看不看得到商品連結」只是一個合理的猜測，不是已驗證的結論——開發用的
沙盒環境連 headless browser 都連不到 momoshop.com.tw（在 CONNECT 階段
被沙盒自己的網路政策擋下，不是網站擋的：2026-08-19 實測一個真的
Chromium 打這個網域直接收到 `net::ERR_TUNNEL_CONNECTION_FAILED`），要
等下一次真實的 GitHub Actions 執行才會知道行不行。

**先前的失敗記錄（2026-08-18，純 requests 抓靜態 HTML）：** 三個品牌的
查詢都拿到 `HTTP 200`、標題也正確對應搜尋關鍵字（代表搜尋本身、重新
導向都正常），但回應的原始 HTML 裡總共 0 個 `<a href>` 連結，開頭片段
看得出是 Next.js 的殼頁面。這證實了「純用 requests 抓靜態 HTML 這條
路線，對現在的 momo 搜尋頁架構上就走不通」，不是 CSS 選擇器或網址猜錯
——headless browser 是唯一可能修好這個問題的方向。

一個商品區塊裡常常不只一個價格（例如劃線的原價＋促銷價），這裡只取
區塊文字裡第一個符合 `$數字` 樣式的價格，哪一個排在前面同樣沒有驗證過。

**信任層級低於人工填寫**，跟另外兩個來源一樣：見 `pipeline.load_all_prices`
的合併邏輯，同一天、同一品牌、同一平台，人工填的資料永遠蓋過這裡抓到的。
"""
from __future__ import annotations

import logging
from collections import Counter
import re
from typing import Callable, Optional
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from ._browser import fetch_rendered_html, new_browser
from ._common import format_rejects, screen_listing

log = logging.getLogger(__name__)

PLATFORM_NAME = "momo購物網"
SEARCH_URL = "https://m.momoshop.com.tw/search.momo"
BASE_URL = "https://m.momoshop.com.tw"
MAX_RESULTS_PER_BRAND = 5

PRICE_PATTERN = re.compile(r"\$\s*([\d,]+)")

NEXTJS_SHELL_MARKER = "_next/static"

# 跟 pchome.py／shopee.py 同樣的理由：品牌全名直接搜尋常常搜不到，這裡用
# 比較接近賣場實際下標題習慣的關鍵字。
SEARCH_QUERIES = {
    "滿意寶寶日本境內版": "滿意寶寶 日本境內版 M",
    "Aiwibi": "Aiwibi 愛薇彼 M",
    "奢寵幫": "奢寵幫 M",
}

# 測試用替身的簽名：(網址) -> (html, 最終網址) 或 None。
HtmlFetcher = Callable[[str], Optional[tuple[str, str]]]


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


def _looks_like_client_rendered_shell(html: str) -> bool:
    """2026-08-18 實跑後證實：`m.momoshop.com.tw/search.momo` 會被 302
    重新導向到 `www.momoshop.com.tw/search/<關鍵字>`——一個 Next.js 網站。
    伺服器直接回應的 HTML 只有殼（帶 `_next/static` 開頭的 CSS／JS 資源
    連結），完全沒有商品連結；真正的商品清單要等瀏覽器執行 JS 之後才會
    被塞進 DOM。這代表拿到 0 個候選區塊不是「頁面結構猜錯」，而是「純
    HTTP GET 解析靜態 HTML 這條路線，對現在的 momo 搜尋頁根本走不通」。
    現在改用 headless browser 之後，`html` 應該已經是 JS 跑完的結果，
    理論上不該再看到這個標記——如果還看到，代表 headless browser 這條
    路線也沒能讓 JS 真的執行完就擷取內容。"""
    return NEXTJS_SHELL_MARKER in html


def _diagnose(final_url: str, soup: BeautifulSoup, html: str) -> str:
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
        f"最終網址={final_url}｜頁面標題={title!r}｜"
        f"HTML 長度={len(html)}｜總連結數={total_links}｜開頭片段={snippet!r}"
    )


def _parse_html(brand: str, query: str, html: str, final_url: str) -> list[dict]:
    """把 momo 搜尋頁的 HTML（不管是從真的 headless browser 載入還是
    測試餵進來的合成 HTML）轉成跟 manual_prices.csv 相同欄位的 dict
    列表。純函式，不碰網路、不開瀏覽器，方便測試。"""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:  # noqa: BLE001
        log.warning("momo 來源：品牌 %s 的回應無法解析為 HTML（%s: %s）", brand, type(e).__name__, e)
        return []

    candidates = _candidate_blocks(soup)
    if not candidates:
        if _looks_like_client_rendered_shell(html):
            log.warning(
                "momo 來源：品牌 %s（關鍵字「%s」）拿到的還是 Next.js 用戶端渲染的空殼頁面"
                "（HTML 裡看得到 %s 開頭的資源連結，但完全沒有商品連結）——"
                "即使已經改用 headless browser，JS 似乎還是沒有真的執行完就被擷取內容，"
                "可能要拉長等待時間或換一個「頁面穩定」的判斷方式。%s",
                brand, query, NEXTJS_SHELL_MARKER, _diagnose(final_url, soup, html),
            )
        else:
            log.warning(
                "momo 來源：品牌 %s（關鍵字「%s」）在回應裡找不到任何商品連結——"
                "可能是頁面結構跟預期不同，或是被導去了別的頁面。%s",
                brand, query, _diagnose(final_url, soup, html),
            )
        return []

    quotes: list[dict] = []
    rejects: Counter = Counter()
    for link, block in candidates:
        name = link.get("title") or link.get_text(strip=True)
        name_str = str(name) if name else ""
        price_match = PRICE_PATTERN.search(block.get_text(" ", strip=True))
        pack_price = None
        if price_match:
            try:
                pack_price = float(price_match.group(1).replace(",", ""))
            except ValueError:
                pack_price = None

        piece_count, reason = screen_listing(name_str, pack_price)
        if reason:
            rejects[reason] += 1
            log.info("momo 來源：品牌 %s 略過「%s」（%s；抓到的價格字串＝%r）",
                     brand, name_str or "（無標題）", reason,
                     price_match.group(1) if price_match else None)
            continue

        quotes.append({
            "brand": brand,
            "platform": PLATFORM_NAME,
            "product_name": name_str,
            "pack_price": pack_price,
            "piece_count": piece_count,
            "url": urljoin(BASE_URL, link["href"]),
            "note": f"自動爬蟲（momo 搜尋頁，headless browser 渲染後解析 HTML，"
                    f"關鍵字：{query}）；未經人工核對，正確性以商品頁面為準",
        })
        if len(quotes) >= MAX_RESULTS_PER_BRAND:
            break

    if not quotes:
        log.warning(
            "momo 來源：品牌 %s（關鍵字「%s」）找到 %d 個候選商品區塊，"
            "但沒有一個通過過濾，整批略過。各關卡擋下的筆數：%s",
            brand, query, len(candidates), format_rejects(rejects),
        )
    else:
        log.info(
            "momo 來源：品牌 %s 取得 %d 筆報價（候選 %d 個，過濾掉 %d 個：%s）",
            brand, len(quotes), len(candidates), sum(rejects.values()), format_rejects(rejects),
        )
    return quotes


def fetch_brand(brand: str, query: Optional[str] = None,
                 browser=None, html_fetcher: Optional[HtmlFetcher] = None) -> list[dict]:
    """查一個品牌在 momo 的報價，回傳與 manual_prices.csv 相同欄位的
    dict 列表（date 欄位由呼叫端補上，這裡不知道「今天」是哪一天）。

    `browser` 是呼叫端（通常是 `fetch_all`）已經開好的 headless browser
    執行個體，用來實際載入頁面、等 JS 執行完；`html_fetcher` 是測試用的
    替身，簽名是 `(網址) -> Optional[(html, 最終網址)]`，有給就直接用它、
    不會真的去開瀏覽器。任何一種失敗（瀏覽器啟動失敗、頁面載入逾時、
    HTML 解析失敗、找不到符合條件的商品）都回傳空 list 並記錄警告，絕不
    拋例外。"""
    query = query or SEARCH_QUERIES.get(brand)
    if not query:
        log.warning("momo 來源：品牌 %s 沒有設定搜尋關鍵字，略過", brand)
        return []

    fetch = html_fetcher or (lambda url: fetch_rendered_html(browser, url))
    url = f"{SEARCH_URL}?searchKeyword={quote(query)}&searchType=1&curPage=1"
    result = fetch(url)
    if result is None:
        log.warning(
            "momo 來源：品牌 %s（關鍵字「%s」）headless browser 載入頁面失敗（逾時或例外）",
            brand, query,
        )
        return []
    html, final_url = result
    return _parse_html(brand, query, html, final_url)


def fetch_all(brands: list[str], html_fetcher: Optional[HtmlFetcher] = None) -> list[dict]:
    """對每個品牌各呼叫一次 `fetch_brand`，共用同一個 headless browser
    執行個體（省掉每個品牌重複啟動瀏覽器的時間與開銷），單一品牌出錯
    不影響其他品牌。`html_fetcher` 有給的話（測試場景）完全不會去開
    真正的瀏覽器。"""
    quotes: list[dict] = []

    if html_fetcher is not None:
        for brand in brands:
            try:
                quotes.extend(fetch_brand(brand, html_fetcher=html_fetcher))
            except Exception as e:  # noqa: BLE001
                log.warning("momo 來源：品牌 %s 發生未預期的例外（%s: %s），跳過這個品牌",
                            brand, type(e).__name__, e)
        return quotes

    browser_handle = new_browser()
    if browser_handle is None:
        log.warning("momo 來源：headless browser 啟動失敗，這次整批略過")
        return []
    try:
        for brand in brands:
            try:
                quotes.extend(fetch_brand(brand, browser=browser_handle.browser))
            except Exception as e:  # noqa: BLE001
                log.warning("momo 來源：品牌 %s 發生未預期的例外（%s: %s），跳過這個品牌",
                            brand, type(e).__name__, e)
    finally:
        browser_handle.close()
    return quotes
