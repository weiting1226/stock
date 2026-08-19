"""`_browser.py` 共用 headless browser 工具的測試。

`fetch_rendered_html`／`fetch_intercepted_json` 只呼叫傳進來的 `browser`
物件身上的方法（`new_page`／`goto`／`content`／`on` 等等），所以這裡用
假的 Page／Browser 物件就能測完整套邏輯，不需要真的裝瀏覽器、也不用碰
網路。`new_browser()` 本身要不要真的啟動 Chromium 才是另一回事，這裡只
測它在啟動失敗時有沒有乖乖回傳 None（用 monkeypatch 讓啟動失敗，不用
真的裝壞掉的瀏覽器）。
"""
from __future__ import annotations

from diaper_monitor.sources import _browser


class _FakeResponse:
    def __init__(self, url, payload=None, json_exc=None):
        self.url = url
        self._payload = payload
        self._json_exc = json_exc

    def json(self):
        if self._json_exc:
            raise self._json_exc
        return self._payload


class _FakePage:
    def __init__(self, html="<html></html>", final_url="https://example.com/final",
                 goto_exc=None, responses=None):
        self._html = html
        self.url = final_url
        self._goto_exc = goto_exc
        self._responses = responses or []
        self._handlers = []
        self.closed = False

    def on(self, event, handler):
        self._handlers.append(handler)

    def goto(self, url, timeout=None, wait_until=None):
        if self._goto_exc:
            raise self._goto_exc
        for resp in self._responses:
            for handler in self._handlers:
                handler(resp)

    def wait_for_timeout(self, ms):
        pass

    def content(self):
        return self._html

    def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, page=None, new_page_exc=None):
        self._page = page
        self._new_page_exc = new_page_exc

    def new_page(self, user_agent=None):
        if self._new_page_exc:
            raise self._new_page_exc
        return self._page


# --- fetch_rendered_html --------------------------------------------------------

def test_fetch_rendered_html_returns_html_and_final_url():
    page = _FakePage(html="<html>hi</html>", final_url="https://www.momoshop.com.tw/search/foo")
    browser = _FakeBrowser(page=page)
    result = _browser.fetch_rendered_html(browser, "https://m.momoshop.com.tw/search.momo")
    assert result == ("<html>hi</html>", "https://www.momoshop.com.tw/search/foo")
    assert page.closed


def test_fetch_rendered_html_returns_none_on_navigation_failure():
    page = _FakePage(goto_exc=RuntimeError("timeout"))
    browser = _FakeBrowser(page=page)
    assert _browser.fetch_rendered_html(browser, "https://example.com") is None
    assert page.closed


def test_fetch_rendered_html_returns_none_when_new_page_fails():
    browser = _FakeBrowser(new_page_exc=RuntimeError("no more pages"))
    assert _browser.fetch_rendered_html(browser, "https://example.com") is None


# --- fetch_intercepted_json ------------------------------------------------------

def test_fetch_intercepted_json_returns_the_matching_response_payload():
    matching = _FakeResponse("https://shopee.tw/api/v4/search/search_items?x=1", payload={"items": []})
    other = _FakeResponse("https://shopee.tw/api/v4/other_endpoint", payload={"noise": True})
    page = _FakePage(responses=[other, matching])
    browser = _FakeBrowser(page=page)
    payload = _browser.fetch_intercepted_json(browser, "https://shopee.tw/search?keyword=x", "search_items")
    assert payload == {"items": []}
    assert page.closed


def test_fetch_intercepted_json_returns_none_when_nothing_matches():
    other = _FakeResponse("https://shopee.tw/api/v4/other_endpoint", payload={"noise": True})
    page = _FakePage(responses=[other])
    browser = _FakeBrowser(page=page)
    assert _browser.fetch_intercepted_json(browser, "https://shopee.tw/search?keyword=x", "search_items") is None


def test_fetch_intercepted_json_ignores_a_matching_response_with_invalid_json():
    bad = _FakeResponse("https://shopee.tw/api/v4/search/search_items", json_exc=ValueError("not json"))
    page = _FakePage(responses=[bad])
    browser = _FakeBrowser(page=page)
    assert _browser.fetch_intercepted_json(browser, "https://shopee.tw/search?keyword=x", "search_items") is None


def test_fetch_intercepted_json_returns_none_on_navigation_failure():
    page = _FakePage(goto_exc=RuntimeError("timeout"))
    browser = _FakeBrowser(page=page)
    assert _browser.fetch_intercepted_json(browser, "https://example.com", "search_items") is None


# --- new_browser：啟動失敗要回傳 None，不拋例外 -----------------------------------

def test_new_browser_returns_none_when_chromium_launch_fails(monkeypatch):
    class _FakeChromium:
        def launch(self):
            raise RuntimeError("executable doesn't exist")

    class _FakePlaywrightCtx:
        def __init__(self):
            self.chromium = _FakeChromium()

        def stop(self):
            pass

    class _FakeSyncPlaywright:
        def start(self):
            return _FakePlaywrightCtx()

    import playwright.sync_api as pw_sync_api
    monkeypatch.setattr(pw_sync_api, "sync_playwright", lambda: _FakeSyncPlaywright())

    assert _browser.new_browser() is None
