"""模組八（尿布單價監控）設定。

**量測對象**：尿布 M 號，三個品牌／通路各自視為獨立的監控對象——
「滿意寶寶日本境內版」「Awibii」「奢寵幫」彼此不是同一件商品的不同賣場，
價格本來就不該放在一起比大小，只在各自的時間序列上比「今天 vs. 自己的近期」。

**資料來源是人工填入，不是自動爬蟲。** 蝦皮、momo、PChome 等平台對非登入的
自動化請求有防爬機制，貿然爬取容易撞到服務條款、也容易因頁面改版而悄悄爬錯
（爬蟲最危險的失敗方式不是報錯，是安靜地回傳一個過期或錯誤的數字）。
比照模組一 `manual_overrides.json` 的前例：由人工每日把各平台的報價填進
`data/diaper_monitor/manual_prices.csv`，本模組只負責換算與比較。
之後若要接自動爬蟲，在獨立的 `sources/` 子模組裡新增即可，只要輸出同樣的欄位
（date, brand, platform, product_name, pack_price, piece_count, url），
不需要動 `pipeline.py`。

**「今日最便宜」怎麼算**：同一天、同一品牌可能有好幾個平台的報價，
取單片價（= 包裝售價 / 片數）最低的那一筆，作為當日代表值。
用最低價而非平均價，是因為使用者實際能拿到的價格就是各平台裡最便宜的那個，
用平均會把「其實有更便宜的選擇」這件事平均掉。
"""
from __future__ import annotations

SIZE = "M"

# 監控品牌／通路。順序即為儀表板呈現順序。
BRANDS = ["滿意寶寶日本境內版", "Awibii", "奢寵幫"]

MANUAL_PRICES_PATH = "data/diaper_monitor/manual_prices.csv"
HISTORY_PATH = "data/diaper_monitor/history.csv"
DATA_ROOT = "docs/data/diaper"

# 「近期平均」的比較視窗（天數，非交易日）。
# 太短（例如 3 天）容易被單一天的異常報價牽著走；太長則會把一個月前的價格
# 也當成「近期」，跌價後很久都還顯示「顯著下跌」。14 天是折衷值，非回測校準。
BASELINE_WINDOW_DAYS = 14

# 視窗內至少要有幾個歷史資料點，才採信「近期平均」這個基準。
# 剛上線或某幾天忘記填資料時，1、2 筆資料算出的「平均」雜訊極大，
# 用來判定「跌 20%」會做出沒有根據的標示——寧可先顯示「資料不足」。
MIN_BASELINE_POINTS = 5

# 顯著下跌門檻：今日最便宜單價 vs. 近期平均，跌幅達此比例即標示。
DROP_THRESHOLD_PCT = 20.0
