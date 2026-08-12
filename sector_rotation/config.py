"""模組四（資金輪動觀察）設定。

用 SPDR 類股 ETF 代表美股十一個 GICS 類股。用 ETF 而不是自行加總成分股：
ETF 有現成的價格與流通股數，而流通股數的變化正好就是資金進出的定義本身
（申購／贖回），不需要另找資金流資料源。

類股名稱沿用 GICS 那一套，與模組二的 `normalize_sector` 一致——三個模組
用同一份詞彙，切換頁面時才不會像在看三種不同的分類。
"""
from __future__ import annotations

BENCHMARK = "SPY"

# ticker -> (GICS 名稱, 中文名)
SECTOR_ETFS = {
    "XLK":  ("Information Technology", "資訊科技"),
    "XLF":  ("Financials", "金融"),
    "XLV":  ("Health Care", "醫療保健"),
    "XLY":  ("Consumer Discretionary", "非必需消費"),
    "XLC":  ("Communication Services", "通訊服務"),
    "XLI":  ("Industrials", "工業"),
    "XLP":  ("Consumer Staples", "必需消費"),
    "XLE":  ("Energy", "能源"),
    "XLU":  ("Utilities", "公用事業"),
    "XLRE": ("Real Estate", "房地產"),
    "XLB":  ("Materials", "原物料"),
}

ALL_TICKERS = (BENCHMARK, *SECTOR_ETFS)

# 觀察視窗一律以**交易日**計，不是日曆天。
# 用日曆天的話，遇到長假就會少算幾天，而「這一週漲多少」在連假前後會是
# 不同長度的區間——跨日期比較就沒有意義了。
WINDOWS = {
    "1d": 1,
    "1w": 5,
    "1m": 21,
    "3m": 63,
}
WINDOW_LABELS = {"1d": "當日", "1w": "近一週", "1m": "近一月", "3m": "近三月"}

# 相對強弱動能的回看長度（交易日）：RS 比率本身相對它自己 N 天前的變化。
# 用 10 天而不是 1 天：單日的 RS 變化幾乎全是雜訊，看不出輪動方向。
RS_MOMENTUM_WINDOW = 10

# 相對強弱的比較基準長度（交易日）。RS 是「比值相對它自己這麼多天的移動平均」，
# 不是「相對抓取視窗的第一天」——後者會讓 RS 數值取決於設定檔裡的 HISTORY_DAYS，
# 改一個常數就讓所有類股的象限歸類整批位移。約一季，與市場慣用的輪動週期相當。
RS_TREND_WINDOW = 63

# 抓多久的歷史。要涵蓋最長視窗（63 天）加上 RS 動能的回看，再留緩衝。
HISTORY_DAYS = 400

# 資金流：連續觀測的最大間隔。超過就不採計——中間可能跨了長假或排程中斷，
# 把多日累積的申贖當成單日訊號會嚴重高估強度（同模組一的處理）。
MAX_FLOW_GAP_DAYS = 7

DATA_ROOT = "docs/data/rotation"
HISTORY_PATH = "data/rotation/history.csv"
SNAPSHOT_PATH = "data/rotation/etf_snapshots.csv"
