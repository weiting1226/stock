"""模組七（類股 ETF 趨勢）設定。

**與模組四的分工**（先講清楚，否則兩頁看起來像同一件事）：

  模組四  11 檔 SPDR 類股 ETF，1 日～3 月，相對強弱象限與資金流
          回答「資金正在往哪一格走」
  模組七  每個類股的**多檔主要 ETF**，1 月～3 年，均線位置與趨勢狀態
          回答「這個類股處在什麼趨勢裡，而且換一檔 ETF 結果會不會不一樣」

兩者刻意不同的三件事：

1. **視窗長度。** 模組四最長三個月，看的是輪動；趨勢要看得更長。
2. **ETF 廣度。** 模組四每個類股只取一檔（XLK 代表科技）。但「科技類股漲多少」
   其實取決於你拿哪一檔——XLK、VGT、IYW 追的指數不同，成分與權重都不一樣。
   這個差異本身就值得看見。
3. **除息調整。** 模組四用未調整價（`fetch_yahoo_close_bulk` 的 `auto_adjust=False`），
   三個月內影響有限；但拉到一年、三年，公用事業與必需消費那種 3% 殖利率的類股
   會被系統性低估。本模組一律用**調整後**價格，見 `metrics.py`。

**沒有自創的綜合分數。** 這一頁只呈現可查證的事實：收在均線之上或之下、
距離 52 週高點多遠、各視窗報酬。不做「趨勢強度 0-100」那種東西——
那需要校準，而校準過的東西這個專案裡已經有前車之鑑（模組六的 VVIX 門檻）。
"""
from __future__ import annotations

BENCHMARK = "SPY"

# 每個 GICS 類股的主要 ETF。
#
# **`kind` 這一欄是後來補的，因為第一版把兩件事混在一起量了。**
# 原本只算「同類股所有 ETF 的報酬差距」，實測醫療保健 50.7pp、原物料 29.1pp，
# 看起來像「選哪一檔差很多」。但拆開來看，差距**全部**來自次產業 ETF：
#
#   醫療保健  XLV 30.6 / VHT 32.1  → 真正的差距 1.6pp；XBI（生技）81.3
#   原物料    XLB 19.3 / VAW 19.5  → 真正的差距 0.2pp；XME（金屬礦業）48.4
#   金融      XLF 12.8 / VFH 15.8  → 真正的差距 3.0pp；KRE（區域銀行）34.1
#
# 沒有人會拿 XBI 代表醫療保健類股。把它算進「同類股離散度」，那個數字就在
# 回答一個沒有人問的問題，而標籤卻說它在回答「選哪一檔會不會不一樣」。
#
#   broad     追整個類股，只差在指數編製者 → 這才是「選哪一檔」的問題
#   industry  次產業，本來就該不一樣 → 有參考價值，但不併入離散度
#
# (ticker, 發行商, 追蹤範圍簡述, kind)
SECTOR_FAMILIES = {
    "資訊科技": [("XLK", "SPDR", "S&P 500 資訊科技", "broad"),
                 ("VGT", "Vanguard", "MSCI 美國 IMI 資訊科技（含中小型）", "broad"),
                 ("IYW", "iShares", "羅素 1000 科技", "broad")],
    "金融": [("XLF", "SPDR", "S&P 500 金融", "broad"),
             ("VFH", "Vanguard", "MSCI 美國 IMI 金融", "broad"),
             ("KRE", "SPDR", "區域銀行（產業別，非全類股）", "industry")],
    "醫療保健": [("XLV", "SPDR", "S&P 500 醫療保健", "broad"),
                 ("VHT", "Vanguard", "MSCI 美國 IMI 醫療保健", "broad"),
                 ("XBI", "SPDR", "生技（等權重，波動遠高於全類股）", "industry")],
    "非必需消費": [("XLY", "SPDR", "S&P 500 非必需消費", "broad"),
                   ("VCR", "Vanguard", "MSCI 美國 IMI 非必需消費", "broad"),
                   ("XRT", "SPDR", "零售（等權重）", "industry")],
    "通訊服務": [("XLC", "SPDR", "S&P 500 通訊服務", "broad"),
                 ("VOX", "Vanguard", "MSCI 美國 IMI 通訊服務", "broad")],
    "工業": [("XLI", "SPDR", "S&P 500 工業", "broad"),
             ("VIS", "Vanguard", "MSCI 美國 IMI 工業", "broad"),
             ("XAR", "SPDR", "航太國防", "industry")],
    "必需消費": [("XLP", "SPDR", "S&P 500 必需消費", "broad"),
                 ("VDC", "Vanguard", "MSCI 美國 IMI 必需消費", "broad")],
    "能源": [("XLE", "SPDR", "S&P 500 能源", "broad"),
             ("VDE", "Vanguard", "MSCI 美國 IMI 能源", "broad"),
             ("XOP", "SPDR", "油氣探勘生產（等權重）", "industry")],
    "公用事業": [("XLU", "SPDR", "S&P 500 公用事業", "broad"),
                 ("VPU", "Vanguard", "MSCI 美國 IMI 公用事業", "broad")],
    "房地產": [("XLRE", "SPDR", "S&P 500 房地產", "broad"),
               ("VNQ", "Vanguard", "MSCI 美國 REIT", "broad")],
    "原物料": [("XLB", "SPDR", "S&P 500 原物料", "broad"),
               ("VAW", "Vanguard", "MSCI 美國 IMI 原物料", "broad"),
               ("XME", "SPDR", "金屬礦業（等權重）", "industry")],
}

# 跨類股的產業型 ETF：不屬於單一 GICS 類股，但市場當成獨立主題在看
THEMATIC = [
    ("SMH", "半導體", "資訊科技"),
    ("IGV", "軟體", "資訊科技"),
    ("ITB", "住宅營建", "非必需消費"),
    ("JETS", "航空", "工業"),
]

# 每個類股的「代表 ETF」——廣度統計用哪一檔要固定下來，
# 否則同一個「11 檔裡有幾檔在 200 日線上」會隨取樣而變
PRIMARY = {sector: etfs[0][0] for sector, etfs in SECTOR_FAMILIES.items()}


def all_tickers() -> list:
    seen = [BENCHMARK]
    for etfs in SECTOR_FAMILIES.values():
        for t, _, _, _ in etfs:
            if t not in seen:
                seen.append(t)
    for t, _, _ in THEMATIC:
        if t not in seen:
            seen.append(t)
    return seen


# 均線。50/200 是市場慣用的，不是本專案挑的——這一點重要：
# 挑一組「回測最好看」的均線長度就是在配參數。
MA_WINDOWS = (50, 200)

# 報酬視窗（交易日）。與模組四刻意不重疊太多：那裡看 1 日到 3 月。
RETURN_WINDOWS = {
    "1m": 21,
    "3m": 63,
    "6m": 126,
    "1y": 252,
    "3y": 756,
}
WINDOW_LABELS = {"1m": "近一月", "3m": "近三月", "6m": "近半年",
                 "1y": "近一年", "3y": "近三年"}

HISTORY_DAYS = 1200          # 需涵蓋三年報酬 + 200 日均線暖機
CHART_POINTS = 260           # 趨勢圖取樣點數

DATA_ROOT = "docs/data/trend"
HISTORY_PATH = "data/trend/breadth_history.csv"
