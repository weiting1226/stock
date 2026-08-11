"""
一次性、在你自己電腦上手動執行的小工具：
開一個有畫面的瀏覽器，讓你手動登入 inline，登入完按 Enter，
就把登入狀態（cookies/localStorage）存成 storage_state.json，
之後 GitHub Actions 的搶位排程會用這份狀態，不需要每次重新登入。

用法：
    pip install playwright
    playwright install chromium
    python -m fumee_booking.capture_session

這個檔案只在你本機執行、只產生 fumee_booking/storage_state.json（已在
.gitignore 排除，不會被提交），之後你要自己把它轉成 base64 存進
GitHub Secrets（步驟見 fumee_booking/README.md），不要把這個檔案傳給任何人。
"""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

from .config import load_config

OUT_PATH = Path(__file__).parent / "storage_state.json"


def main() -> None:
    cfg = load_config()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(cfg.booking_url)
        print("瀏覽器已開啟，請在裡面手動完成 inline 登入（含簡訊OTP等步驟）。")
        input("登入完成後，回到這裡按 Enter 繼續...")
        context.storage_state(path=str(OUT_PATH))
        browser.close()
    print(f"已儲存登入狀態到 {OUT_PATH}")
    print("接下來：")
    print(f"  base64 -w0 {OUT_PATH} > storage_state.b64.txt   # macOS 用 base64 -i 代替 -w0")
    print("  把 storage_state.b64.txt 的內容整包貼進 GitHub Secret：INLINE_STORAGE_STATE_B64")
    print("  貼完後刪掉本機的 storage_state.json 與 storage_state.b64.txt。")


if __name__ == "__main__":
    main()
