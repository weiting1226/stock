"""模組六（尾部脆弱度雙閘門）設定。

**這個模組的定位，接線說明第 0 節講得很清楚，這裡照抄不打折：**
期望值約 +1.2%/年，而且 89% 來自約七年一遇的危機型事件。它不是黑天鵝防護罩，
是**薄利的尾部保險**。

兩項限制同樣照抄：

  慢閘的門檻**未經真實資料校準**。合成測試只驗證結構正確性，沒有驗證閾值。
  快閘的統計基礎是 **n=1**——樣本裡真正的危機型事件只有 2020 一個。

因此本模組在儀表板上**只做影子運行**：算出建議配置、記錄下來，不接任何下單
邏輯。這正是說明第 6 節第三步建議的做法，而儀表板天生就是那個形態。
這件事必須寫在畫面上，不能只寫在這裡——一個看起來很篤定的紅色警示，
使用者不會知道它的門檻沒校準過。
"""
from __future__ import annotations

# --- 資料來源 ---------------------------------------------------------------

# 波動率期限結構。VIX9D < VIX < VIX3M 是常態；倒過來代表近月恐慌溢價
VOL_TICKERS = {
    "vix9d": "^VIX9D",
    "vix": "^VIX",
    "vix3m": "^VIX3M",
    "vvix": "^VVIX",
}

# 信用代理閘所需。**必須用除息調整價**——見 fetch_adjusted_close 的說明，
# HYG 年配息約 6~7%，用未調整價會讓合成指數每年假性下跌數個百分點。
CREDIT_PROXY_TICKERS = {"hyg": "HYG", "lqd": "LQD", "ief": "IEF"}

# HY OAS：ICE 授權資料。本專案實測 FRED 只給得出近三年（見 sources/fred.py
# 的實測結論），這正是說明第 2 節建議「先用 CreditProxyGate」的理由——
# 那份限制在這個 repo 已經踩過一次了。
HY_OAS_SERIES = "BAMLH0A0HYM2"

# 融資壓力：說明第 2 節指出 CreditProxyGate 缺這一塊，可用免費的
# SOFR 與 IORB 自行補上，這兩個系列沒有授權限制。
FUNDING_SERIES = {"sofr": "SOFR", "iorb": "IORB"}

# 驗證專用。說明第 5 節：存這兩欄才能實測 whipsaw_cost，
# 那是整個 EV 模型最未經驗證的一環。
VALIDATION_TICKERS = {"tqqq": "TQQQ", "qqq": "QQQ"}

# --- 閘門參數 ---------------------------------------------------------------

# 曲線倒掛需連續幾日才降級。說明第 6 節第二步要求用真實資料重跑敏感度；
# 在那之前這是說明給的預設值，不是本專案校準出來的。
PERSIST_DAYS = 4

# 回場不延遲（說明第 3 節的校準結論）
REENTRY_CLEAN_DAYS = 0

# 久期對沖 beta 的固定值。說明第 6 節：代理驗證未達標時，
# 先試「固定 0.47」與「滾動估計」哪個好。
HEDGE_BETA_FIXED = 0.47
HEDGE_BETA_WINDOW = 250

REGIME_CEILING = {
    "EXPANSION": 1.00,
    "WATCH": 0.75,
    "CONTRACTION": 0.50,
    "STRESS": 0.25,
}
REGIME_ORDER = ["EXPANSION", "WATCH", "CONTRACTION", "STRESS"]

# 否決等級對應的權益上限（純扣減，見 controller 的三條規則）
VETO_CEILING = {0: 1.00, 1: 0.75, 2: 0.50, 3: 0.25}

# 事前的 TQQQ 硬上限。說明第 8 節的最後一句：
# 「50% TQQQ 的事前上限比任何訊號都重要——它不依賴任何一個需要校準的參數。」
# 放在這裡而不是埋在函式裡，是因為它是全模組唯一不需要校準就成立的保護。
TQQQ_HARD_CAP = 0.50

# --- 代理驗證的判讀門檻（說明第 6 節第一步）---------------------------------
CALIBRATION_TARGETS = {
    "corr_level_dd_vs_oas": 0.60,     # 大於
    "corr_change_60d": -0.50,         # 小於
    "regime_agreement": 0.70,         # 大於
}

# --- 資料落後（說明第 4 節）------------------------------------------------
# 記在設定裡而不是只寫在文件，是因為 CFTC 的三日延遲若被當成即時值，
# 畫面上看不出任何異常——那是最典型的靜默錯誤。
INPUT_LAG_DAYS = {
    "vix_family": 0,
    "credit_proxy": 0,
    "funding": 1,
    "cftc_jpy": 3,
    "margin_debt": 30,
}

HISTORY_DAYS = 900          # 需涵蓋 500 日回撤 + 250 日百分位 + 緩衝

DATA_ROOT = "docs/data/tail"
STATE_PATH = "data/tail/state.json"
DAILY_LOG_PATH = "data/tail/daily_log.csv"
