"""模組二設定：股票池來源、目標價資料源、門檻與輸出路徑。"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 股票池（universe）
# ---------------------------------------------------------------------------
# S&P 500 成分股表同時提供 GICS 類股欄位，正好滿足「各類股篩選」需求。
SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX_WIKI_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"

UNIVERSE_CACHE_DAYS = 7  # 成分股清單快取天數（季度調整，一週更新一次綽綽有餘）

# ---------------------------------------------------------------------------
# 目標價資料源
# ---------------------------------------------------------------------------
# 「蒐集各財金網站分析師建議價並計算平均」的實作方式說明：
#
# 分析師目標價本身就是券商研究報告的產物，各大財金網站（Yahoo Finance、
# MarketWatch、TipRanks、Finviz…）呈現的都是「同一批券商報告的彙總共識價」，
# 而非各自獨立的估值。真正能合法、穩定、免金鑰取得的只有 Yahoo Finance 的
# 共識資料（mean/median/high/low + 分析師家數）。
#
# 因此本模組採「可插拔多資料源」架構：
#   1. Yahoo Finance  — 預設啟用，免金鑰。
#   2. Finnhub        — 選用，需免費 API 金鑰 (環境變數 FINNHUB_API_KEY)，
#                       提供另一組獨立彙總的共識目標價。
# 兩者皆取得時，consensus_target 為兩者的平均；只有一個時就用該來源，
# 並在輸出中明確標示 sources_used，絕不假裝有多來源平均。
#
# 未納入 Finviz / TipRanks 等站：其服務條款明文禁止自動化擷取，且無公開API。
# 資料源分兩層：
#   A. 逐機構層（per-firm）：能拿到「每一家券商各自的目標價」，可依機構去重後
#      自行計算平均。目前僅 FMP 提供，需 API 金鑰。
#   B. 共識層（consensus）：只提供彙總後的平均／最高／最低，無法做機構層級去重。
#      Yahoo（免金鑰）與 Finnhub（需金鑰）屬於此層。
# 有逐機構資料時一律優先採用並自行計算；沒有時才退回共識層，
# 並在輸出的 basis 欄位誠實標明是哪一種。
YAHOO_SOURCE = "yahoo"
FINNHUB_SOURCE = "finnhub"
FINNHUB_API_URL = "https://finnhub.io/api/v1/price-target"
FINNHUB_ENV_KEY = "FINNHUB_API_KEY"

FMP_SOURCE = "fmp"
FMP_PRICE_TARGET_URL = "https://financialmodelingprep.com/stable/price-target-news"
FMP_ENV_KEY = "FMP_API_KEY"

# 免費方案的速率上限（calls/min）。抓取是多執行緒並發，不節流會直接被 429。
# 取略低於官方上限的值當安全邊際。
FINNHUB_CALLS_PER_MIN = 55   # 官方免費方案 60/min
FMP_CALLS_PER_MIN = 55       # FMP 另有每日總量限制，見 README

# Yahoo 沒有公開的速率上限，數字來自實測：全市場掃描時 300 檔那批安然通過，
# 1500 檔那批在大約第 465 檔之後整批回 YFRateLimitError，1190 檔全數失敗。
# 可見它是「滾動視窗內的總量」而非單純的每秒上限，因此要壓低到能長時間維持
# 的水準。每檔會發兩個請求（analyst_price_targets 與 info），所以每分鐘
# 120 次呼叫約等於 60 檔／分；配合每天多班次執行，一天足以掃完 7500 檔。
YAHOO_CALLS_PER_MIN = 120

# 逐機構記錄超過這個天數就不採計：目標價會隨基本面調整，過舊的報告沒有代表性
FIRM_TARGET_MAX_AGE_DAYS = 180

# ---------------------------------------------------------------------------
# 資料品質門檻
# ---------------------------------------------------------------------------
# 分析師家數過少時，共識價的代表性低，於UI標示為低信心（不剔除，交由使用者判斷）。
MIN_ANALYSTS_FOR_HIGH_CONFIDENCE = 5
MIN_ANALYSTS_TO_INCLUDE = 1  # 至少要有1位分析師才納入，否則視為無目標價

# 明顯不合理的目標價（相對現價）視為資料異常，標記而非直接採用
IMPLAUSIBLE_UPSIDE_PCT = 300.0
IMPLAUSIBLE_DOWNSIDE_PCT = -90.0

# ---------------------------------------------------------------------------
# 價格變化視窗（交易日）
# ---------------------------------------------------------------------------
WEEK_TRADING_DAYS = 5
MONTH_TRADING_DAYS = 21
PRICE_HISTORY_PERIOD = "3mo"  # 批次抓取區間，需涵蓋 21 個交易日 + 緩衝

# ---------------------------------------------------------------------------
# 抓取行為
# ---------------------------------------------------------------------------
MAX_WORKERS = 8        # 併發抓取目標價的執行緒數（過高會被 Yahoo 限流）
PER_TICKER_TIMEOUT = 20
FETCH_RETRIES = 2

DATA_ROOT = "docs/data/valuation"
