"""共用的 headless browser 抓取工具。

蝦皮的搜尋 API 直接用 requests 打會被 403 擋下（見 `shopee.py` 開頭），
momo 的搜尋頁是 Next.js 用戶端渲染、純 HTTP GET 抓到的靜態 HTML 完全看
不到商品（見 `momo.py` 開頭）——兩個問題的共同解法都是「不要自己組
請求，而是像瀏覽器一樣把整個頁面真的載入、跑完 JS」。這裡用 Playwright
開一個真正的 headless Chromium 分頁。

**這支程式碼完全沒機會在真實網站上驗證過。** 開發用的沙盒環境連 headless
browser 都連不到這兩個網域（跟純 requests 一樣，在 CONNECT 階段就被沙盒
本身的網路政策擋下，不是網站擋的——見 2026-08-19 的測試記錄：一個真的
Chromium 打 momoshop.com.tw 直接收到 `net::ERR_TUNNEL_CONNECTION_FAILED`），
所以連「瀏覽器打不打得開、能不能繞過蝦皮的 403」這種最基本的問題都要
留到 GitHub Actions 上才第一次見真章。CI 的 workflow 需要多一個
`playwright install chromium` 的步驟才有瀏覽器可用，這裡不假設它已經
裝好。

兩個 fetch 函式都遵守跟其他來源一樣的防呆原則：任何失敗（套件沒裝、
瀏覽器啟動失敗、頁面載入逾時、攔截不到目標回應、回應不是合法 JSON）
一律回傳 `None` 並記錄警告，絕不拋例外——呼叫端不需要另外包 try/except。
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
NAVIGATION_TIMEOUT_MS = 20000
# 等頁面「看起來穩定」之後，再多等一下讓 client-side render 把商品塞進
# DOM——networkidle 只保證沒有新的網路請求，不保證 React/Next.js 已經把
# 資料畫完。這個等待時間本身也是沒驗證過的猜測。
SETTLE_WAIT_MS = 2000


class BrowserHandle:
    """包住 playwright 物件與 browser 的生命週期，讓呼叫端只需要呼叫一次
    `close()` 就能把兩層都關掉，不用個別記得 playwright.stop()。"""

    def __init__(self, playwright, browser):
        self._playwright = playwright
        self.browser = browser

    def close(self) -> None:
        try:
            self.browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._playwright.stop()
        except Exception:  # noqa: BLE001
            pass


def new_browser() -> Optional[BrowserHandle]:
    """開一個新的 headless Chromium 執行個體；呼叫端用完後要呼叫
    `.close()`。啟動失敗（套件沒裝、CI 忘了跑 `playwright install`、
    沙盒環境的網路政策擋下）時回傳 None，而不是讓整支查價腳本在啟動期
    就整個中斷。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        log.warning("headless browser：playwright 套件沒裝好（%s: %s）", type(e).__name__, e)
        return None
    try:
        playwright = sync_playwright().start()
    except Exception as e:  # noqa: BLE001
        log.warning("headless browser：啟動 Playwright 失敗（%s: %s）", type(e).__name__, e)
        return None
    try:
        browser = playwright.chromium.launch()
    except Exception as e:  # noqa: BLE001
        log.warning(
            "headless browser：啟動 Chromium 失敗（%s: %s）——"
            "CI 環境是不是忘了執行 `playwright install chromium`？",
            type(e).__name__, e,
        )
        try:
            playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        return None
    return BrowserHandle(playwright, browser)


def fetch_rendered_html(browser, url: str) -> Optional[tuple[str, str]]:
    """打開一個新分頁載入 url、等頁面穩定，回傳 `(html, 最終網址)` 供
    純 HTML 解析邏輯使用（例如 momo 的用戶端渲染搜尋頁，`page.content()`
    拿到的是「JS 跑完之後」的 DOM，不是原始回應）。任何失敗都回傳
    None，不拋例外。"""
    try:
        page = browser.new_page(user_agent=DEFAULT_USER_AGENT)
    except Exception as e:  # noqa: BLE001
        log.warning("headless browser：開新分頁失敗（%s: %s）", type(e).__name__, e)
        return None
    try:
        page.goto(url, timeout=NAVIGATION_TIMEOUT_MS, wait_until="networkidle")
        page.wait_for_timeout(SETTLE_WAIT_MS)
        html = page.content()
        final_url = page.url
        return html, final_url
    except Exception as e:  # noqa: BLE001
        log.warning("headless browser：載入 %s 失敗（%s: %s）", url, type(e).__name__, e)
        return None
    finally:
        try:
            page.close()
        except Exception:  # noqa: BLE001
            pass


def fetch_intercepted_json(browser, url: str, api_url_substring: str) -> Optional[dict]:
    """打開一個新分頁載入 url，攔截網址包含 `api_url_substring` 的第一個
    回應、把它的 JSON 內容回傳（例如蝦皮搜尋頁本身在背景呼叫的搜尋
    API）——這樣可以直接重用既有的 JSON 解析邏輯，不用另外寫一套 DOM
    解析。攔截不到、回應不是合法 JSON、頁面載入失敗都回傳 None，不拋
    例外。"""
    captured: dict = {}

    def _on_response(response) -> None:
        if "payload" in captured:
            return
        if api_url_substring not in response.url:
            return
        try:
            captured["payload"] = response.json()
        except Exception:  # noqa: BLE001
            pass

    try:
        page = browser.new_page(user_agent=DEFAULT_USER_AGENT)
    except Exception as e:  # noqa: BLE001
        log.warning("headless browser：開新分頁失敗（%s: %s）", type(e).__name__, e)
        return None
    try:
        page.on("response", _on_response)
        page.goto(url, timeout=NAVIGATION_TIMEOUT_MS, wait_until="networkidle")
        page.wait_for_timeout(SETTLE_WAIT_MS)
    except Exception as e:  # noqa: BLE001
        log.warning("headless browser：載入 %s 失敗（%s: %s）", url, type(e).__name__, e)
        return None
    finally:
        try:
            page.close()
        except Exception:  # noqa: BLE001
            pass

    return captured.get("payload")
