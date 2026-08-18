"""PChome 24h購物 的自動查價來源。

選 PChome 而不是蝦皮／momo 當第一個自動來源，是因為它有一個公開、回傳 JSON
（而非需要瀏覽器引擎渲染的動態頁面）的搜尋端點，理論上比較不容易被防爬機制
攔下。開發這個模組的沙盒環境連不到 `ecshweb.pchome.com.tw`（連 curl 都在
CONNECT 階段被 403），所以最初的實作完全沒驗證過；**2026-08-18 第一次在
GitHub Actions 上跑，證實連得到、JSON 結構（`prods` 陣列、`name`/`price`
欄位）的假設也是對的**——但同一次也抓到三種會**算錯單片價而不是抓不到**的
標題（試用包、多尺寸片數擠在一起、箱購倍數），修正後的過濾規則見
`config.UNRELIABLE_TITLE_PATTERN` 開頭的說明與那三個真實案例。

`fetch_brand()` 對抓不到的欄位、格式對不上的回應、片數／M 號／上述三種
不可靠標題比對不到的商品，一律記錄警告而不是拋例外或安靜略過，方便從
workflow log 判斷到底是「PChome 真的沒有這個商品」還是程式碼的假設錯了。

**信任層級低於人工填寫**：見 `pipeline.load_all_prices` 的合併邏輯，
同一天、同一品牌、同一平台，人工填的資料永遠蓋過這裡抓到的。
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import requests

from ..config import PIECE_COUNT_PATTERN, SIZE_M_PATTERN, UNRELIABLE_TITLE_PATTERN

log = logging.getLogger(__name__)

PLATFORM_NAME = "PChome24h購物"
SEARCH_URL = "https://ecshweb.pchome.com.tw/search/v3.3/all/results"
TIMEOUT_SECONDS = 10
MAX_RESULTS_PER_BRAND = 5

# 品牌全名直接拿去搜尋常常搜不到——賣場標題不會逐字複製監控用的品牌名稱，
# 這裡用的是比較接近賣場實際下標題習慣的關鍵字。之後如果某個品牌長期搜不到
# 結果，先懷疑是這裡的關鍵字不對，而不是 PChome 真的沒賣。
SEARCH_QUERIES = {
    "滿意寶寶日本境內版": "滿意寶寶 日本境內版 M",
    "Aiwibi": "Aiwibi 愛薇彼 M",
    "奢寵幫": "奢寵幫 M",
}


def _first(item: dict, *keys: str):
    """依序試幾種常見的欄位命名（大小寫不一致是這類舊版 API 的常態），
    避免因為猜錯一種大小寫就整批漏抓。"""
    for k in keys:
        v = item.get(k)
        if v not in (None, ""):
            return v
    return None


def _extract_piece_count(name: str) -> Optional[int]:
    m = re.search(PIECE_COUNT_PATTERN, name)
    return int(m.group(1)) if m else None


def _looks_like_size_m(name: str) -> bool:
    return re.search(SIZE_M_PATTERN, name) is not None


def _looks_unreliable(name: str) -> bool:
    """擋掉試用包、多尺寸片數擠在一起、箱購倍數這三種標題——
    三個都是 UNRELIABLE_TITLE_PATTERN 開頭註解裡實際抓錯過的真實案例。"""
    return re.search(UNRELIABLE_TITLE_PATTERN, name) is not None


def fetch_brand(brand: str, query: Optional[str] = None,
                 session: Optional[requests.Session] = None) -> list[dict]:
    """查一個品牌在 PChome 的報價，回傳與 manual_prices.csv 相同欄位的
    dict 列表（date 欄位由呼叫端補上，這裡不知道「今天」是哪一天）。

    任何一種失敗（連線、逾時、回應格式不對、找不到符合條件的商品）都回傳
    空 list 並記錄警告，絕不拋例外——一個品牌抓失敗不該讓其他品牌也抓不到，
    整批失敗也不該讓 scripts/run_diaper_monitor.py 中斷。"""
    query = query or SEARCH_QUERIES.get(brand)
    if not query:
        log.warning("PChome 來源：品牌 %s 沒有設定搜尋關鍵字，略過", brand)
        return []

    http = session or requests
    try:
        resp = http.get(
            SEARCH_URL,
            params={"q": query, "page": 1, "sort": "sale/dc"},
            timeout=TIMEOUT_SECONDS,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:  # noqa: BLE001
        log.warning("PChome 來源：查詢「%s」失敗（%s: %s）", query, type(e).__name__, e)
        return []

    items = _first(payload, "prods", "Prods")
    if not isinstance(items, list):
        log.warning(
            "PChome 來源：回應格式跟預期不符（找不到 prods 陣列），"
            "可能是 API 改版了；回應最外層欄位＝%s",
            list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__,
        )
        return []

    quotes: list[dict] = []
    skipped = 0
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        name = _first(item, "name", "Name", "describe", "Describe")
        price = _first(item, "price", "Price")
        item_id = _first(item, "Id", "id")
        if not name or price is None:
            skipped += 1
            continue
        if not _looks_like_size_m(str(name)):
            skipped += 1
            continue
        if _looks_unreliable(str(name)):
            skipped += 1
            continue
        piece_count = _extract_piece_count(str(name))
        if piece_count is None:
            skipped += 1
            continue
        try:
            pack_price = float(price)
        except (TypeError, ValueError):
            skipped += 1
            continue

        quotes.append({
            "brand": brand,
            "platform": PLATFORM_NAME,
            "product_name": str(name),
            "pack_price": pack_price,
            "piece_count": piece_count,
            "url": f"https://24h.pchome.com.tw/prod/{item_id}" if item_id else "",
            "note": f"自動爬蟲（PChome 搜尋 API，關鍵字：{query}）；未經人工核對，正確性以商品頁面為準",
        })
        if len(quotes) >= MAX_RESULTS_PER_BRAND:
            break

    if not quotes:
        log.warning(
            "PChome 來源：品牌 %s（關鍵字「%s」）查到 %d 筆結果，"
            "但沒有一筆同時符合「M 號」與「標題找得到片數」，整批略過（跳過 %d 筆）",
            brand, query, len(items), skipped,
        )
    else:
        log.info(
            "PChome 來源：品牌 %s 取得 %d 筆報價（原始 %d 筆，過濾掉 %d 筆）",
            brand, len(quotes), len(items), skipped,
        )
    return quotes


def fetch_all(brands: list[str], session: Optional[requests.Session] = None) -> list[dict]:
    """對每個品牌各呼叫一次 `fetch_brand`，單一品牌出錯不影響其他品牌。

    `fetch_brand` 內部已經把網路／解析錯誤都攔下來、回傳空 list 而不是拋例外，
    但這裡再包一層 try/except 是防「萬一那層防呆本身有漏洞」——單一品牌的
    bug 不該讓整批查價（也就是這次執行唯一能拿到的資料）全部中斷。"""
    quotes: list[dict] = []
    for brand in brands:
        try:
            quotes.extend(fetch_brand(brand, session=session))
        except Exception as e:  # noqa: BLE001
            log.warning("PChome 來源：品牌 %s 發生未預期的例外（%s: %s），跳過這個品牌",
                        brand, type(e).__name__, e)
    return quotes
