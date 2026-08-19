# 模組八：尿布單價監控 — 人工報價填寫說明

`manual_prices.csv` 是模組八最主要、信任層級最高的資料輸入。每天要監控的
平台各查一次「M 號」的包裝售價與片數，填成一列。`scripts/run_diaper_monitor.py`
只認得下面這幾個欄位，多寫沒關係，少寫會直接報錯。

**自動爬蟲只是補人工的空檔，不是取代它。** `scripts/run_diaper_monitor.py`
每次執行會先跑三個自動查價來源——PChome（`sources/pchome.py`）、蝦皮
（`sources/shopee.py`）、momo（`sources/momo.py`）——結果寫進格式相同的
`scraped_prices.csv`；同一天、同一品牌、同一平台，`manual_prices.csv` 的
資料永遠蓋過爬蟲抓到的。這份檔案不需要、也不建議手動編輯——直接改
`manual_prices.csv` 就會蓋過它。爬蟲的原理、限制、各自的風險，見三支
檔案開頭的說明；目前實測狀況見下一段。

**比價範圍至少要涵蓋 `diaper_monitor/config.py` 的 `PLATFORMS`：蝦皮、酷澎、
momo購物網。** 這三個平台每天都要查；其他通路（藥妝連鎖、品牌官網等）查到的話
可以一起填進去當補充對照，但不能只填這些之外的平台卻漏掉這三個。

| 欄位 | 說明 |
|---|---|
| `date` | 查價日期，`YYYY-MM-DD` |
| `brand` | 監控品牌／通路，須完全match `diaper_monitor/config.py` 的 `BRANDS`：目前是「滿意寶寶日本境內版」「Aiwibi」「奢寵幫」。打錯字的話那一列不會出現在報表裡，也不會報錯——先核對拼字 |
| `platform` | 該筆報價所在的電商平台，例如「蝦皮」「momo購物網」「PChome24h購物」「Yahoo奇摩購物中心」，自由文字 |
| `product_name` | 賣場上的商品標題，用來核對是不是同一件商品、同一個尺寸 |
| `pack_price` | 該包裝的實付售價（新台幣，已含當下適用的折扣） |
| `piece_count` | 該包裝的片數。單片價 = `pack_price / piece_count`，由程式自動換算，**不要自己先除好再填** |
| `url` | 選填，商品連結，方便之後複查 |
| `note` | 選填，任何備註（例如「限量特價」「含運」） |

## 範例

```csv
date,brand,platform,product_name,pack_price,piece_count,url,note
2026-08-14,滿意寶寶日本境內版,蝦皮,滿意寶寶 日本境內版 M 64片,899,64,https://example.com/a,
2026-08-14,滿意寶寶日本境內版,momo購物網,滿意寶寶 境內版 M號 62片,950,62,https://example.com/b,
2026-08-14,Aiwibi,酷澎,Aiwibi 褲型 M 66片,1080,66,,
2026-08-14,奢寵幫,蝦皮,奢寵幫代購 M 60片,780,60,,限時特價
```

同一天、同一品牌可以填多個平台的報價（如上例），程式會自動取其中單片價最低的
一筆當作「今日最便宜」，其餘作為對照顯示在儀表板的展開明細裡。

## 為什麼自動爬蟲的信任層級比較低

蝦皮、momo 等平台對非登入的自動化請求普遍有防爬機制，貿然爬取容易撞到服務
條款；就算能爬到，頁面改版時爬蟲最危險的失敗方式不是報錯，是**安靜地**抓到
過期或錯誤的數字，而錯誤的價格資料會直接誤導「顯著下跌」的判定。人工填寫
雖然多一道手續，但每一筆都是查價當下親眼確認過的——這也是為什麼兩邊都有
資料時一律以人工為準，而不是「誰的日期新就用誰」。

**PChome** 有公開回傳 JSON 的搜尋端點，相對不需要瀏覽器引擎渲染，是第一個
接上的來源，**目前三個裡唯一實測有效的**。2026-08-18 第一次在 GitHub
Actions 上實跑，證實連線與回應格式的假設可用，但同一次也抓到三種標題會
**算錯而非抓不到**單片價（試用包、多尺寸片數擠在一起、箱購倍數）——過濾
規則（`sources/_common.py` 的 `looks_unreliable`）就是照那三個真實案例
補上的，之後再抓到新的錯誤案例，一樣照這個模式：把真實標題寫成測試，
再補規則。目前查到的商品多半是箱購／組合包，還沒有一筆真的通過篩選，
但至少證實了連線跟解析邏輯是對的。

**蝦皮**是第二個接上的來源，2026-08-18 用 `requests` 直接打搜尋 API
**實測被擋下來了**：三個品牌的查詢全部收到 `HTTP 403 Forbidden`，是連線
層級被拒，不是原本猜測的「HTTP 200 但回應包含 `error` 欄位」那種擋法。
2026-08-19 改用 headless browser（Playwright）：不再直接打 API，而是開一個
真正的 Chromium 分頁載入蝦皮的搜尋頁，讓頁面自己的前端 JS 去呼叫這支
API，再攔截那個回應的 JSON——原本的 JSON 解析邏輯完全沒動，只換掉「怎麼
拿到這包 JSON」這一層。**這個新策略還沒有機會驗證過**，要等下一次真實的
GitHub Actions 執行才知道行不行。價格欄位公開資料記載要除以 100000 才是
實際售價，這個換算係數本身也還沒機會驗證過。

**momo** 是第三個接上的來源。2026-08-18 用 `requests` 抓靜態 HTML
**實測證實這條路線走不通**：請求會被 302 導向桌機版的
`www.momoshop.com.tw/search/<關鍵字>`，回應雖然是 `HTTP 200`、標題也正確
對應搜尋關鍵字（代表搜尋本身沒問題），但原始 HTML 裡總共 0 個 `<a href>`
連結，開頭片段看得出是 Next.js 的殼頁面（`_next/static/css` 這類資源
連結）——商品清單要瀏覽器執行 JS 之後才會出現，伺服器直接回應的只是
空殼。2026-08-19 改用 headless browser：開一個真正的 Chromium 分頁載入
搜尋頁、等 JS 執行完，把渲染後的 HTML 餵給原本就寫好的 DOM 解析邏輯。
**這個新策略還沒有機會驗證過。**

**兩支 headless browser 策略共同的限制：** 開發用的沙盒環境連 headless
browser 都連不到 shopee.tw／momoshop.com.tw（2026-08-19 實測一個真的
Chromium 直接收到 `net::ERR_TUNNEL_CONNECTION_FAILED`，在 CONNECT 階段
被沙盒自己的網路政策擋下，不是網站擋的）——跟純 requests 的狀況一樣，
唯一能驗證這條路線行不行的地方是真實的 GitHub Actions 執行。CI 的
workflow 多了一個 `playwright install --with-deps chromium` 的步驟，
沒裝瀏覽器 `new_browser()` 會回傳 None、整批查價當作沒抓到，不會讓腳本
中斷。

三支爬蟲共用的 `config.PLAUSIBLE_PACK_PRICE_RANGE`（10～10000 元）是防
「換算係數／解析邏輯整個猜錯、算出離譜價格」的保底防呆，不是精確驗證。

如果儀表板上一直看不到某個來源的報價，先查
`python3 scripts/run_diaper_monitor.py -v` 的警告訊息判斷卡在哪一層：
完全連不上／headless browser 啟動失敗、攔截不到目標回應（蝦皮的 API 或
momo 渲染後的商品連結）、還是抓到資料但解析不出來或被過濾規則擋下
（需要照 log 裡的原始回應調整對應的 `sources/*.py`）。`sources/pchome.py`
額外把每一筆被過濾掉的商品標題與確切原因都記到 log 裡，不用再猜。

新增其他平台的爬蟲時，一樣在 `diaper_monitor/sources/` 底下新增模組、
輸出跟這份 CSV 相同的欄位（`pchome.fetch_all`／`shopee.fetch_all`／
`momo.fetch_all` 是現成的參考實作，M 號／片數／不可靠標題的判斷可以
直接重用 `sources/_common.py`），`pipeline.py` 的合併邏輯不需要更動。
