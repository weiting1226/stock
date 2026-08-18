# 模組八：尿布單價監控 — 人工報價填寫說明

`manual_prices.csv` 是模組八最主要、信任層級最高的資料輸入。每天要監控的
平台各查一次「M 號」的包裝售價與片數，填成一列。`scripts/run_diaper_monitor.py`
只認得下面這幾個欄位，多寫沒關係，少寫會直接報錯。

**自動爬蟲只是補人工的空檔，不是取代它。** `scripts/run_diaper_monitor.py`
每次執行會先跑一輪 PChome 自動查價（`diaper_monitor/sources/pchome.py`），
結果寫進格式相同的 `scraped_prices.csv`；同一天、同一品牌、同一平台，
`manual_prices.csv` 的資料永遠蓋過爬蟲抓到的。這份檔案不需要、也不建議
手動編輯——直接改 `manual_prices.csv` 就會蓋過它。爬蟲的原理、限制、
為什麼先選 PChome，見 `diaper_monitor/sources/pchome.py` 開頭的說明。

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

目前唯一接上的自動來源是 PChome（有公開回傳 JSON 的搜尋端點，相對不需要
瀏覽器引擎渲染）。**這支爬蟲程式碼開發時所在的環境連不到
`ecshweb.pchome.com.tw`（網路出口直接擋掉），所以從未在真實回應上驗證過**，
對回應格式的假設全憑公開資料。如果儀表板上一直看不到 PChome 來源的報價，
先查 `python3 scripts/run_diaper_monitor.py -v` 的警告訊息——是完全連不上
（環境網路限制、平台真的擋了爬蟲），還是抓到資料但解析不出來（API 回應格式
跟程式碼的假設不一樣，需要照 log 裡的原始回應調整 `sources/pchome.py`）。

新增其他平台的爬蟲時，一樣在 `diaper_monitor/sources/` 底下新增模組、
輸出跟這份 CSV 相同的欄位（`diaper_monitor.sources.pchome.fetch_all` 是
現成的參考實作），`pipeline.py` 的合併邏輯不需要更動。
