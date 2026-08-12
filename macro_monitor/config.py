"""模組五（美股總體經濟觀察）設定。

全部取自 FRED 的免金鑰端點，沿用模組一的抓取器（含多端點退回與診斷）。

**每一條序列都必須明確標出它的「轉換方式」**，這是這個模組最容易出錯的地方：
CPI 的原始序列是指數（例如 314.5），要看的是年增率；失業率的原始序列本身
就是百分比，再算一次年增率就毫無意義。把兩者混在一起不會報錯，只會產出
一整排看起來很正常的錯誤數字。

「方向好壞」也一併寫死在設定裡：失業率上升是壞事、GDP 上升是好事，
而這件事程式無從推論。不標的話，畫面只能顯示漲跌，讀者得自己記住
哪一個方向代表什麼。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Series:
    fred_id: str
    label: str                 # 中文名
    category: str
    unit: str
    transform: str             # "level" | "yoy" | "mom" | "diff"
    freq: str                  # "D" | "W" | "M" | "Q"
    higher_is_better: Optional[bool]   # None = 沒有單一方向的好壞
    note: str = ""
    # 這條序列多久沒更新就算異常（天）。用來分辨「這個月還沒公布」與
    # 「來源壞了」——兩者在畫面上長得一模一樣，但處置完全不同。
    stale_after_days: int = 0


CATEGORIES = {
    "growth": "① 成長動能",
    "labor": "② 就業市場",
    "inflation": "③ 通膨",
    "rates": "④ 利率與殖利率曲線",
    "financial": "⑤ 金融條件",
    "consumer": "⑥ 消費與房市",
}

SERIES = (
    # ① 成長動能 --------------------------------------------------------
    Series("GDPC1", "實質 GDP", "growth", "%", "yoy", "Q", True,
           "季頻，約季後一個月公布首次估計，之後還會修正兩次", 150),
    Series("INDPRO", "工業生產指數", "growth", "%", "yoy", "M", True, "", 70),
    Series("RSAFS", "零售銷售", "growth", "%", "yoy", "M", True, "含汽車與加油站", 70),
    Series("USSLIND", "領先指標（州級加總）", "growth", "%", "level", "M", True, "", 90),

    # ② 就業市場 --------------------------------------------------------
    Series("UNRATE", "失業率", "labor", "%", "level", "M", False, "", 70),
    Series("PAYEMS", "非農就業人數", "labor", "千人", "diff", "M", True,
           "顯示月增人數（非水準），這才是每月公布時市場在看的數字", 70),
    Series("ICSA", "初領失業金人數", "labor", "人", "level", "W", False,
           "週頻，是就業市場最即時的指標", 21),
    Series("CIVPART", "勞動參與率", "labor", "%", "level", "M", True, "", 70),
    Series("JTSJOL", "職缺數", "labor", "千個", "level", "M", True, "JOLTS，比就業數據多落後一個月", 100),
    # 薩姆規則：已發表的具名規則，門檻 0.50 由原作者定義，不是本專案自訂
    Series("SAHMREALTIME", "薩姆規則衰退指標", "labor", "pp", "level", "M", False,
           "失業率三個月均值相對前 12 個月低點的升幅。原作者定義 ≥0.50 代表衰退已經開始", 70),

    # ③ 通膨 ------------------------------------------------------------
    Series("CPIAUCSL", "CPI 消費者物價", "inflation", "%", "yoy", "M", None, "", 70),
    Series("CPILFESL", "核心 CPI（不含食物能源）", "inflation", "%", "yoy", "M", None, "", 70),
    Series("PCEPI", "PCE 物價", "inflation", "%", "yoy", "M", None, "", 80),
    Series("PCEPILFE", "核心 PCE（Fed 的政策目標）", "inflation", "%", "yoy", "M", None,
           "Fed 的 2% 目標指的是這一條", 80),
    Series("T5YIFR", "5年後5年通膨預期", "inflation", "%", "level", "D", None,
           "市場對長期通膨的定價，比實際通膨更能反映預期是否脫錨", 10),
    Series("T10YIE", "10年期損益兩平通膨率", "inflation", "%", "level", "D", None, "", 10),

    # ④ 利率與殖利率曲線 ------------------------------------------------
    Series("FEDFUNDS", "聯邦資金有效利率", "rates", "%", "level", "M", None, "", 70),
    Series("DGS2", "2年期公債殖利率", "rates", "%", "level", "D", None, "", 10),
    Series("DGS10", "10年期公債殖利率", "rates", "%", "level", "D", None, "", 10),
    Series("T10Y2Y", "10年−2年利差", "rates", "%", "level", "D", True,
           "倒掛（負值）在過去每一次衰退前都出現過，但領先時間從半年到兩年不等", 10),
    Series("T10Y3M", "10年−3個月利差", "rates", "%", "level", "D", True,
           "紐約聯準銀行的衰退模型用的是這一條，不是 10年−2年", 10),

    # ⑤ 金融條件 --------------------------------------------------------
    Series("NFCI", "全國金融條件指數", "financial", "", "level", "W", False,
           "芝加哥聯準銀行編製。0 = 歷史平均，正值代表條件緊縮", 21),
    Series("BAMLH0A0HYM2", "高收益債利差", "financial", "%", "level", "D", False,
           "FRED 對這條序列只提供近三年歷史（ICE BofA 為授權資料），百分位僅供參考", 10),

    # ⑥ 消費與房市 ------------------------------------------------------
    Series("UMCSENT", "密大消費者信心", "consumer", "", "level", "M", True, "", 70),
    Series("HOUST", "新屋開工", "consumer", "千戶", "level", "M", True, "年化", 70),
    Series("MORTGAGE30US", "30年期房貸利率", "consumer", "%", "level", "W", False, "", 21),
    Series("CSUSHPINSA", "Case-Shiller 房價指數", "consumer", "%", "yoy", "M", None,
           "落後約兩個月，是所有指標中時滯最長的", 120),
)

# 衰退期陰影用。USREC 由 NBER 認定，1 = 衰退期間。
# NBER 的認定往往在衰退開始後一年才公布，因此最近期不會有標記——
# 那是「還沒認定」，不是「沒有衰退」。
RECESSION_SERIES = "USREC"

# 百分位的比較期間（年）。太短會讓「歷史高低」失去意義，
# 太長則會把結構已經改變的年代也算進來。
PERCENTILE_YEARS = 20

# 抓多久的歷史
HISTORY_YEARS = 30

DATA_ROOT = "docs/data/macro"
