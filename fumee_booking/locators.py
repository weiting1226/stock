"""
與 inline.app 訂位頁面互動的操作層。

⚠️ 重要限制：這個環境目前連不到 inline.app（網路代理擋掉外部網域），
所以下面的選擇器沒有辦法對照真實頁面驗證過，是依「inline 是常見的
React SPA 訂位介面」寫的合理猜測（文字比對優先於精確 CSS/id，
盡量耐頁面小改版）。

**正式搶位前，你一定要先用 `python -m fumee_booking.book --inspect` 跑一次**
（在非搶位時段執行也可以，那時就算按不下去也沒關係），檢查
artifacts/ 底下存的截圖與 HTML，確認：
  1. 每個 STEP 有沒有找到對應元素（log 會印出 ✅/❌）
  2. 找錯元素或找不到的話，照下面每個函式裡的候選清單，
     比對實際 DOM 後修改 candidates。
"""
from __future__ import annotations

import logging
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

log = logging.getLogger("fumee_booking")

SHORT_TIMEOUT_MS = 1500


def _try_click(page: Page, candidates: list, step: str) -> bool:
    """依序嘗試多個 locator，第一個可點的就點下去。"""
    for get_locator in candidates:
        try:
            locator = get_locator(page)
            locator.first.wait_for(state="visible", timeout=SHORT_TIMEOUT_MS)
            locator.first.click(timeout=SHORT_TIMEOUT_MS)
            log.info("✅ [%s] 點擊成功", step)
            return True
        except (PWTimeoutError, Exception):  # noqa: BLE001 - 候選失敗就換下一個
            continue
    log.warning("❌ [%s] 所有候選元素都找不到/點不到", step)
    return False


def _try_fill(page: Page, candidates: list, value: str, step: str) -> bool:
    for get_locator in candidates:
        try:
            locator = get_locator(page)
            locator.first.wait_for(state="visible", timeout=SHORT_TIMEOUT_MS)
            locator.first.fill(value, timeout=SHORT_TIMEOUT_MS)
            log.info("✅ [%s] 已填入", step)
            return True
        except (PWTimeoutError, Exception):  # noqa: BLE001
            continue
    log.warning("❌ [%s] 找不到輸入框，未填入", step)
    return False


def select_date(page: Page, date_str: str) -> bool:
    """date_str 格式 YYYY-MM-DD。inline 的月曆通常是點日期格子，格子上只顯示『日』。"""
    day = str(int(date_str.split("-")[2]))  # 去掉前導0，例如 "06" -> "6"
    candidates = [
        lambda p: p.get_by_role("button", name=date_str, exact=False),
        lambda p: p.locator(f"[aria-label*='{date_str}']"),
        lambda p: p.get_by_role("gridcell", name=day, exact=True),
        lambda p: p.get_by_role("button", name=day, exact=True),
    ]
    return _try_click(page, candidates, f"選日期 {date_str}")


def select_time(page: Page, time_str: str) -> bool:
    candidates = [
        lambda p: p.get_by_role("button", name=time_str, exact=False),
        lambda p: p.get_by_text(time_str, exact=False),
    ]
    return _try_click(page, candidates, f"選時段 {time_str}")


def select_party_size(page: Page, n: int) -> bool:
    candidates_select = [
        lambda p: p.locator("select[name*='part' i], select[name*='guest' i], select[name*='people' i]"),
    ]
    for get_locator in candidates_select:
        try:
            locator = get_locator(page)
            locator.first.wait_for(state="visible", timeout=SHORT_TIMEOUT_MS)
            locator.first.select_option(str(n), timeout=SHORT_TIMEOUT_MS)
            log.info("✅ [選人數 %d] 已用下拉選單選取", n)
            return True
        except (PWTimeoutError, Exception):  # noqa: BLE001
            continue

    candidates_click = [
        lambda p: p.get_by_role("button", name=f"{n}人", exact=False),
        lambda p: p.get_by_role("button", name=str(n), exact=True),
        lambda p: p.get_by_text(f"{n}位", exact=False),
    ]
    return _try_click(page, candidates_click, f"選人數 {n}")


def fill_contact(page: Page, name: str, phone: str, email: str) -> None:
    _try_fill(
        page,
        [
            lambda p: p.get_by_label("姓名", exact=False),
            lambda p: p.get_by_placeholder("姓名", exact=False),
            lambda p: p.locator("input[name*='name' i]"),
        ],
        name,
        "填姓名",
    )
    _try_fill(
        page,
        [
            lambda p: p.get_by_label("電話", exact=False),
            lambda p: p.get_by_placeholder("電話", exact=False),
            lambda p: p.locator("input[type='tel'], input[name*='phone' i]"),
        ],
        phone,
        "填電話",
    )
    _try_fill(
        page,
        [
            lambda p: p.get_by_label("Email", exact=False),
            lambda p: p.get_by_placeholder("Email", exact=False),
            lambda p: p.locator("input[type='email']"),
        ],
        email,
        "填Email",
    )


def submit(page: Page) -> bool:
    candidates = [
        lambda p: p.get_by_role("button", name="確認", exact=False),
        lambda p: p.get_by_role("button", name="送出", exact=False),
        lambda p: p.get_by_role("button", name="預約", exact=False),
        lambda p: p.get_by_role("button", name="訂位", exact=False),
        lambda p: p.get_by_role("button", name="Confirm", exact=False),
        lambda p: p.get_by_role("button", name="Book", exact=False),
    ]
    return _try_click(page, candidates, "送出訂位")


def dump_state(page: Page, out_dir: Path, tag: str) -> None:
    """存截圖 + HTML，方便事後（或非搶位時段）核對頁面實際結構。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(out_dir / f"{tag}.png"), full_page=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("截圖失敗：%s", exc)
    try:
        (out_dir / f"{tag}.html").write_text(page.content(), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.warning("存HTML失敗：%s", exc)
