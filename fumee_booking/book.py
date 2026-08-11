"""
fumée 搶位排程主程式。

三種執行方式：
  python -m fumee_booking.book --inspect
      不搶位，只是打開頁面、存截圖+HTML到 artifacts/，用來核對/調整
      locators.py 裡的選擇器。任何時候都能安全執行。

  python -m fumee_booking.book --dry-run
      跑完整流程（含等到目標時間、依序嘗試 attempts）但『不按下最後送出』，
      用來驗證時序與前面幾步是否找得到元素。

  python -m fumee_booking.book
      正式模式。GitHub Actions 排程會用這個。預設只有在「明天是每月1號」
      時才會真的等到 00:00 去搶位，其餘日子直接结束（避免每天空等）；
      加 --force 可以忽略這個日期檢查（手動測試用）。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

from . import locators
from .config import BookingConfig, load_config
from .notify import send_email

TAIPEI = ZoneInfo("Asia/Taipei")
ARTIFACT_DIR = Path(__file__).parent / "artifacts"
DEFAULT_STORAGE_STATE = Path(__file__).parent / "storage_state.json"

log = logging.getLogger("fumee_booking")


def _storage_state_path() -> str | None:
    env_path = os.environ.get("STORAGE_STATE_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    if DEFAULT_STORAGE_STATE.exists():
        return str(DEFAULT_STORAGE_STATE)
    return None


def _tomorrow_is_first_of_month(now: datetime) -> bool:
    return (now + timedelta(days=1)).day == 1


def _next_midnight(now: datetime) -> datetime:
    tomorrow = (now + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=TAIPEI)


def _wait_until(target: datetime) -> None:
    while True:
        remaining = (target - datetime.now(TAIPEI)).total_seconds()
        if remaining <= 0:
            return
        log.info("距離開放時間還有 %.1f 秒...", remaining)
        time.sleep(min(remaining, 5))


def _check_result(page) -> str:
    """回傳 'success' / 'failure' / 'unknown'，用文字比對，不保證100%準確。"""
    success_words = ["訂位成功", "預約成功", "已完成", "confirmed", "success", "感謝您的預約"]
    failure_words = ["已被訂", "客滿", "無法預約", "unavailable", "sold out", "額滿"]
    try:
        body_text = page.locator("body").inner_text(timeout=2000)
    except Exception:  # noqa: BLE001
        return "unknown"
    lowered = body_text.lower()
    if any(w.lower() in lowered for w in success_words):
        return "success"
    if any(w.lower() in lowered for w in failure_words):
        return "failure"
    return "unknown"


def attempt_booking(page, cfg: BookingConfig, dry_run: bool) -> tuple[bool, str]:
    deadline = time.monotonic() + cfg.max_attempt_seconds
    for i, attempt in enumerate(cfg.attempts):
        if time.monotonic() > deadline:
            log.warning("已超過 max_attempt_seconds (%ds)，停止嘗試", cfg.max_attempt_seconds)
            break

        log.info("=== 嘗試 %d/%d：%s ===", i + 1, len(cfg.attempts), attempt.label)
        page.goto(cfg.booking_url, wait_until="domcontentloaded")

        locators.select_date(page, attempt.date)
        locators.select_time(page, attempt.time)
        locators.select_party_size(page, attempt.party_size)
        locators.fill_contact(page, cfg.contact.name, cfg.contact.phone, cfg.contact.email)

        locators.dump_state(page, ARTIFACT_DIR, f"before_submit_{i}")

        if dry_run:
            log.info("dry-run 模式，跳過送出")
            continue

        if not locators.submit(page):
            locators.dump_state(page, ARTIFACT_DIR, f"submit_failed_{i}")
            time.sleep(cfg.retry_interval_ms / 1000)
            continue

        page.wait_for_timeout(1500)
        result = _check_result(page)
        locators.dump_state(page, ARTIFACT_DIR, f"result_{result}_{i}")

        if result == "success":
            return True, attempt.label
        if result == "failure":
            log.info("這組額滿/失敗，換下一組")
        else:
            log.warning("送出後結果無法自動判斷，視為失敗並換下一組（請檢查 artifacts/）")

        time.sleep(cfg.retry_interval_ms / 1000)

    return False, ""


def run(inspect: bool, dry_run: bool, force: bool) -> int:
    cfg = load_config()
    now = datetime.now(TAIPEI)

    if not inspect and not force and not _tomorrow_is_first_of_month(now):
        log.info("今天不是月底最後一天（明天不是1號），無需搶位，結束。")
        return 0

    storage_state = _storage_state_path()
    if not storage_state and not inspect:
        log.error("找不到登入狀態（storage_state.json / STORAGE_STATE_PATH），無法送出訂位。")
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=storage_state)
        page = context.new_page()

        if inspect:
            page.goto(cfg.booking_url, wait_until="domcontentloaded")
            locators.dump_state(page, ARTIFACT_DIR, "inspect")
            browser.close()
            log.info("已將截圖/HTML存到 %s，請核對後調整 locators.py", ARTIFACT_DIR)
            return 0

        if not force:
            target = _next_midnight(now)
            _wait_until(target)

        success, label = attempt_booking(page, cfg, dry_run=dry_run)
        browser.close()

    if dry_run:
        log.info("dry-run 完成，未實際送出訂位")
        return 0

    if success:
        subject = f"[fumée搶位] 成功：{label}"
        body = f"已成功送出訂位：{label}\n\n聯絡人：{cfg.contact.name} / {cfg.contact.phone}"
    else:
        subject = "[fumée搶位] 這次沒搶到"
        tried = "\n".join(f"- {a.label}" for a in cfg.attempts)
        body = f"所有預設組合都嘗試過了，這次沒有成功：\n{tried}\n\n詳細截圖見本次 GitHub Actions 的 artifacts。"

    send_email(cfg.notify_email_to, subject, body)
    return 0 if success else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="fumée 搶位排程主程式")
    parser.add_argument("--inspect", action="store_true", help="只打開頁面存截圖/HTML，不搶位")
    parser.add_argument("--dry-run", action="store_true", help="跑完整流程但不按下最後送出")
    parser.add_argument("--force", action="store_true", help="忽略日期檢查與等待，立刻嘗試")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(run(inspect=args.inspect, dry_run=args.dry_run, force=args.force))


if __name__ == "__main__":
    main()
