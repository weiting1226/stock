# 模組三：fumée 搶位排程

自動化協助在 inline 訂位系統開放 fumée 次月訂位的瞬間（每月1號00:00台北時間）
依你預先設定的優先順序嘗試訂位，成功/失敗都會寄信通知。

**請先讀完這份文件的「風險與限制」再使用。**

## 風險與限制（誠實說明，不要跳過）

- **這類工具通常落在訂位平台使用條款的灰色地帶。** 多數訂位/售票平台明文禁止自動化或機器人訂位。
  本工具刻意只用你自己的單一帳號、以接近人手操作的速度嘗試，不做繞過驗證碼、偽造多帳號、
  高頻率灌爆伺服器等行為——但即使如此，帳號仍有被平台偵測、限制或停用的風險，請自行評估是否接受。
- **inline 頁面的實際 DOM 結構未經驗證。** 開發這個工具時的環境連不到 inline.app，
  `book.py` 裡的選擇器（`locators.py`）是依常見 SPA 訂位介面寫的合理猜測，
  **正式使用前必須先用 `inspect` 模式跑一次並核對**（見下方步驟5）。
- **GitHub Actions 的排程時間不保證絕對準時**，且執行環境用的是GitHub共用IP，
  在「開放瞬間僅個位數名額、成功率<1%」這種場景下，這些都是可能導致失敗、且無法完全消除的限制。
- 停止使用時，記得到 GitHub Settings → Secrets 刪除相關密鑰，並停用/刪除 `.github/workflows/fumee-booking.yml` 的排程。

## 架構

```
fumee_booking/
  config.example.json   範例設定（複製成 config.json）
  config.json            實際設定：booking_url / 搶位優先順序 / 逾時參數
                          —— 不含任何個資，可以安全commit進repo
                          —— 用 docs/fumee-booking.html 網頁表單編輯最方便
  config.py               讀取+驗證設定（個資改讀環境變數，見下方）
  locators.py             與 inline 頁面互動的操作（選日期/時段/人數/填資料/送出）
  book.py                 主程式：等到開放時刻、依序嘗試、寄通知信
  notify.py               寄信（SMTP）
  capture_session.py      一次性本機工具：手動登入inline、存登入狀態
.github/workflows/fumee-booking.yml   排程：每天23:50台北時間檢查，
                                        明天是1號才真的等到00:00搶位
docs/fumee-booking.html + .js + .css  網頁表單：編輯 config.json 裡的搶位優先順序
```

個資（姓名/電話/Email/登入狀態/SMTP密碼）刻意**不**放進 `config.json`，
改用 GitHub Actions Secrets，避免進入 git 歷史紀錄。

## 設定步驟

### 1. 複製設定檔

```bash
cp fumee_booking/config.example.json fumee_booking/config.json
```

之後改用 `docs/fumee-booking.html`（GitHub Pages 上線後即可用）編輯搶位優先順序更方便，
每次改動會直接 commit 回 `config.json`。

### 2. 設定 GitHub Secrets

Repo → Settings → Secrets and variables → Actions → New repository secret，新增：

| Secret 名稱 | 說明 |
|---|---|
| `BOOKING_NAME` | 訂位姓名 |
| `BOOKING_PHONE` | 訂位電話 |
| `BOOKING_EMAIL` | 訂位用Email |
| `NOTIFY_EMAIL_TO` | 搶位結果通知信要寄到哪個信箱（可跟BOOKING_EMAIL相同） |
| `INLINE_STORAGE_STATE_B64` | inline登入狀態（見步驟3） |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` | 選用：寄通知信的SMTP設定（例如Gmail應用程式密碼）。不設定的話結果只會印在Actions log裡，不會寄信。 |

### 3. 產生 inline 登入狀態

在**你自己的電腦**（不是CI）執行：

```bash
pip install playwright
playwright install chromium
python -m fumee_booking.capture_session
```

瀏覽器會打開 inline 的訂位頁面，手動完成登入（含簡訊OTP等步驟）後回到終端機按 Enter，
會產生 `fumee_booking/storage_state.json`（已加進 `.gitignore`，不會被commit）。

接著轉成 base64 存進 Secret：

```bash
base64 -w0 fumee_booking/storage_state.json > storage_state.b64.txt   # macOS 用 base64 -i 代替 -w0
```

把 `storage_state.b64.txt` 的內容整包貼進 GitHub Secret `INLINE_STORAGE_STATE_B64`，
貼完後**刪掉本機的這兩個檔案**，不要留著也不要傳給別人。

> inline的登入狀態可能會過期。如果某次搶位失敗、artifacts裡的截圖顯示回到登入頁，
> 代表需要重新跑一次這個步驟、更新Secret。

### 4. 用網頁表單填搶位優先順序

GitHub Pages 上線後開啟 `docs/fumee-booking.html`，貼上一組「只限這個repo、
只給Contents讀寫」的 fine-grained personal access token，載入目前設定、
填好下個月想搶的日期/時段/人數優先順序、儲存。

### 5. 正式使用前，先跑 inspect 模式核對頁面結構

Repo → Actions → fumée 搶位排程 → Run workflow → mode 選 `inspect`。
跑完後在該次執行的 Artifacts 下載截圖與HTML，對照 `locators.py`
裡各個函式的候選選擇器，是否真的有找到（log會印✅/❌），找不到的話照著實際DOM調整。

可以用 `dry_run` 模式驗證到「填完資料但不送出」，用 `force` 模式忽略日期檢查立刻測試整套流程。

### 6. 正式運作

不用手動做什麼——排程本來就是每天23:50檢查，遇到月底最後一天才會真的等到00:00搶位。
確保 `fumee_booking/config.json` 裡的日期在每次搶位前是「下個月」的日期即可。
