"""v3.0 文件中定義的資料來源代碼、發布時滯、權重與計分門檻表。

所有門檻數字直接抄錄自
「市場流動性雙軌監控與交易策略系統 v3.0」文件的表格，不做任何調整。
若文件更新，只需要改這個檔案。
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 資料來源
# ---------------------------------------------------------------------------

# FRED 免金鑰 CSV 端點的 series id -> 假設的「發布時滯」(天)。
# 時滯用於把「原始資料時間軸」轉換成「該日期實際可取得的最新值」，
# 對齊文件第38-48行的「資料時滯規則」。數字為保守估計（寧可晚一點才採用新值，
# 不做前視偏誤），非官方公告時間表。
FRED_SERIES = {
    "WALCL": 7,          # Fed 資產負債表 H.4.1，週三資料週四公布
    "RRPONTSYD": 1,       # ON RRP 每日
    "M2SL": 28,           # M2 月增率，約 4 週落後
    "BAMLH0A0HYM2": 2,    # ICE BofA HY OAS，每日（含假日緩衝）
    "DGS2": 2,            # 2 年期公債殖利率
    "DGS1": 2,            # 1 年期公債殖利率（FedWatch 抓不到時的隱含路徑近似）
    "DGS10": 2,           # 10 年期公債殖利率
    "DGS30": 2,           # 30 年期公債殖利率
    "SOFR": 2,            # SOFR
    "IORB": 2,            # 準備金利率 (IOER 已由 IORB 取代)
    "DTWEXBGS": 2,        # 貿易加權美元指數（DXY 抓取失敗時的備援）
    "DFEDTARU": 2,        # 聯邦資金目標區間上緣
    "DFEDTARL": 2,        # 聯邦資金目標區間下緣
    # 投資等級信用利差（穆迪 Baa − 10 年期公債）。1986 年起、無授權限制，
    # 用來對照被 FRED 限制在近三年的 BAMLH0A0HYM2。目前只做量測，不參與計分。
    "BAA10Y": 2,
    "GDP": 35,            # 名目GDP，季資料，約1個月後公布（估）
}

# yfinance ticker -> 內部欄位名稱
YAHOO_TICKERS = {
    "^VIX": "vix",
    "^VIX3M": "vix3m",
    "^SKEW": "skew",
    "^MOVE": "move",
    "^NDX": "ndx",
    "DX-Y.NYB": "dxy",
    "JPY=X": "usdjpy",
    # 軌道二簡化版每日近似訊號用
    "^VIX9D": "vix9d",
    "GC=F": "gold",
    "TLT": "tlt",
}

FINRA_URL = "https://www.finra.org/investors/learn-to-invest/advanced-investing/margin-statistics"
FINRA_PUBLISH_LAG_DAYS = 35  # 文件規定：當月只能使用兩個月前的參考月數據

FED_PRESSRELEASE_INDEX = "https://www.federalreserve.gov/newsevents/pressreleases/{year}-monetary.htm"

# NASDAQ-100 成分股清單來源（用於計算③ NDX 200日線廣度）
#
# 主來源改用 Invesco QQQ 的公開持股 CSV：QQQ 完整複製 NASDAQ-100，
# 其持股清單就是成分股清單，且由發行商每日更新，比維基百科可靠。
# 2026-08 維基百科 Nasdaq-100 頁面已無法解析出成分股表格（18個表格全不足50列），
# 因此把維基百科降為備援。
QQQ_HOLDINGS_URL = (
    "https://www.invesco.com/us/financial-products/etfs/holdings/main/holdings/0"
    "?audienceType=Investor&action=download&ticker=QQQ"
)
NDX_CONSTITUENTS_WIKI_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"

# ---------------------------------------------------------------------------
# 軌道一權重表（文件第53-64行）
# ---------------------------------------------------------------------------
CATEGORY_WEIGHTS = {
    "monetary_liquidity": 0.10,   # ① 總體貨幣流動性
    "credit_funding": 0.20,       # ② 資金成本與信用
    "microstructure": 0.10,       # ③ 市場微觀結構
    "risk_sentiment": 0.15,       # ④ 風險偏好情緒
    "cross_asset_flow": 0.15,     # ⑤ 跨資產資金流向
    "policy_direction": 0.30,     # ⑥ 政策方向
}

CATEGORY_LABELS = {
    "monetary_liquidity": "① 總體貨幣流動性",
    "credit_funding": "② 資金成本與信用",
    "microstructure": "③ 市場微觀結構",
    "risk_sentiment": "④ 風險偏好情緒",
    "cross_asset_flow": "⑤ 跨資產資金流向",
    "policy_direction": "⑥ 政策方向",
}

# 每個類別底下的子項，用於「類別平均分」與計算過程展示。
CATEGORY_ITEMS = {
    "monetary_liquidity": ["fed_bs_3m_chg", "on_rrp_pctile", "m2_yoy_3m_chg"],
    "credit_funding": ["hy_oas_level", "hy_oas_20d_chg", "t2s10s_bp", "sofr_iorb_bp"],
    "microstructure": ["move_index", "ndx_breadth_200d"],
    "risk_sentiment": ["vix_level", "ivts", "margin_debt_yoy"],
    "cross_asset_flow": ["dxy_1m_chg", "etf_fund_flow", "usdjpy_20d_chg"],
    "policy_direction": ["fomc_decision", "fedwatch_path", "yield30y_60d_momentum"],
}

ITEM_LABELS = {
    "fed_bs_3m_chg": "Fed資產負債表年增率之3個月變化(pp)",
    "on_rrp_pctile": "ON RRP 12個月百分位",
    "m2_yoy_3m_chg": "M2年增率之3個月變化(pp)",
    "hy_oas_level": "HY OAS 水準(%)",
    "hy_oas_20d_chg": "HY OAS 20日變化(bp)",
    "t2s10s_bp": "2Y-10Y公債利差(bp)",
    "sofr_iorb_bp": "SOFR-IORB利差(bp)",
    "move_index": "MOVE指數",
    "ndx_breadth_200d": "NDX站上200日線比例(%)",
    "vix_level": "VIX",
    "ivts": "VIX/VIX3M期限結構",
    "margin_debt_yoy": "融資餘額年增率(%)",
    "dxy_1m_chg": "DXY美元指數月變動(%)",
    "etf_fund_flow": "股票型ETF資金流",
    "usdjpy_20d_chg": "USD/JPY 20日變化(%)",
    "fomc_decision": "FOMC決議偏向",
    "fedwatch_path": "市場隱含12個月路徑(CME FedWatch)",
    "yield30y_60d_momentum": "30Y殖利率60日動能",
}

# 沒有可靠免費資料源、需由使用者於 manual_overrides.json 手動填入的欄位。
# 對應文件第103、124、136行的說明。
# fomc_decision 原本可從聯準會新聞稿自動解析，但 GitHub Actions 的執行環境
# 連不到 federalreserve.gov（2026-08 實測），因此改為「自動抓取失敗時退回人工填入」。
MANUAL_OVERRIDE_ITEMS = {"etf_fund_flow", "fedwatch_path", "fomc_decision"}

# 人工填入的資料多久算過期（超過即改標「暫缺」，避免拿陳舊資料當今日訊號）。
# 依各項目的實際更新頻率設定：資金流與利率路徑每週都在變，FOMC 約6週才開一次會。
MANUAL_OVERRIDE_MAX_AGE_DAYS = {
    "etf_fund_flow": 14,
    "fedwatch_path": 14,
    "fomc_decision": 60,   # FOMC 一年8次、平均間隔約46天，須大於此值
    "ndx_fwd_pe": 45,
}
MANUAL_OVERRIDE_DEFAULT_MAX_AGE_DAYS = 45

# ---------------------------------------------------------------------------
# 燈號區間（文件第148-154行）
# ---------------------------------------------------------------------------
LIGHT_BANDS = [
    (1.2, float("inf"), "🟢🟢 深綠"),
    (0.4, 1.2, "🟢 綠"),
    (-0.4, 0.4, "🟡 黃"),
    (-1.2, -0.4, "🔴 紅"),
    (float("-inf"), -1.2, "🔴🔴 深紅"),
]

# 部位配置階梯（文件第234-249行）
POSITION_LADDER = [
    (1.2, float("inf"), "50% TQQQ + 50% QQQ", 2.0),
    (0.4, 1.2, "25% TQQQ + 75% QQQ", 1.5),
    (-0.4, 0.4, "100% QQQ", 1.0),
    (-1.2, -0.4, "100% SGOV", 0.0),
    (float("-inf"), -1.2, "100% SGOV", 0.0),
]

# Gate B 2026年參考門檻（文件第181-187行；無法取得真實10年百分位時的近似值）
GATE_B_FALLBACK_THRESHOLDS = {
    "margin_debt_yoy": 22.0,     # > 則觸發 (近似 85 百分位)
    "hy_oas_level": 3.1,          # < 則觸發 (近似 15 百分位)
    "margin_debt_gdp": 3.6,       # > 則觸發 (近似 85 百分位)
    "ndx_fwd_pe": 27.0,           # > 則觸發 (近似 85 百分位；本專案無資料源，恆暫缺)
}

DATA_ROOT = "docs/data"
HISTORY_START = "2016-01-01"  # 預設回補起點（約10年，供 Gate B 滾動百分位使用）
