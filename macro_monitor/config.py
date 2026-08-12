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


# 兩次發布之間的間隔（天）。判定「過期」時必須把這段算進去，理由見下。
RELEASE_INTERVAL_DAYS = {"D": 4, "W": 7, "M": 31, "Q": 92}

# 再多給幾天緩衝，避免發布延後一兩天就跳警告
STALE_SLACK_DAYS = 7


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
    # 從「參考期起始日」到「該期數字公布」約幾天。
    # 例：2026-06 的 CPI 參考期記為 2026-06-01，實際在 2026-07-13 公布，約 43 天。
    publish_lag_days: int = 0

    @property
    def stale_after_days(self) -> int:
        """多久沒更新才算異常。

        **關鍵是要加上發布間隔，而不是只用發布時滯。** 第一版直接把發布時滯
        當門檻（月頻設 70 天），結果 7 條指標被誤標為過期——因為月頻資料的
        落後天數本來就會在兩次發布之間持續增加：

          2026-06 的 CPI 於 07-13 公布 -> 當下落後 42 天
          下一次發布是 08-13（7月資料）
          因此在 08-12 這天，最新的仍是 6 月資料，落後 72 天，而這完全正常

        落後天數在「發布時滯」與「發布時滯＋發布間隔」之間來回，
        門檻必須大於後者，否則每個發布週期的尾聲都會誤報一次。
        """
        if not self.publish_lag_days:
            return 0
        return self.publish_lag_days + RELEASE_INTERVAL_DAYS[self.freq] + STALE_SLACK_DAYS


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
           "季頻，約季後一個月公布首次估計，之後還會修正兩次", 120),
    Series("INDPRO", "工業生產指數", "growth", "%", "yoy", "M", True, "", 45),
    Series("RSAFS", "零售銷售", "growth", "%", "yoy", "M", True, "含汽車與加油站", 45),
    # 原本列有 USSLIND（費城聯準銀行的美國領先指標）。實測最後一期停在
    # 2020-02，落後 2384 天——那條序列已經停止更新，不是「還沒公布」。
    # 留著只會每天多一則假警告，因此移除。

    # ② 就業市場 --------------------------------------------------------
    Series("UNRATE", "失業率", "labor", "%", "level", "M", False, "", 37),
    Series("PAYEMS", "非農就業人數", "labor", "千人", "diff", "M", True,
           "顯示月增人數（非水準），這才是每月公布時市場在看的數字", 37),
    Series("ICSA", "初領失業金人數", "labor", "人", "level", "W", False,
           "週頻，是就業市場最即時的指標", 6),
    Series("CIVPART", "勞動參與率", "labor", "%", "level", "M", True, "", 37),
    Series("JTSJOL", "職缺數", "labor", "千個", "level", "M", True, "JOLTS，比就業數據多落後一個月", 70),
    # 薩姆規則：已發表的具名規則，門檻 0.50 由原作者定義，不是本專案自訂
    Series("SAHMREALTIME", "薩姆規則衰退指標", "labor", "pp", "level", "M", False,
           "失業率三個月均值相對前 12 個月低點的升幅。原作者定義 ≥0.50 代表衰退已經開始", 40),

    # ③ 通膨 ------------------------------------------------------------
    Series("CPIAUCSL", "CPI 消費者物價", "inflation", "%", "yoy", "M", None, "", 43),
    Series("CPILFESL", "核心 CPI（不含食物能源）", "inflation", "%", "yoy", "M", None, "", 43),
    Series("PCEPI", "PCE 物價", "inflation", "%", "yoy", "M", None, "", 60),
    Series("PCEPILFE", "核心 PCE（Fed 的政策目標）", "inflation", "%", "yoy", "M", None,
           "Fed 的 2% 目標指的是這一條", 60),
    Series("T5YIFR", "5年後5年通膨預期", "inflation", "%", "level", "D", None,
           "市場對長期通膨的定價，比實際通膨更能反映預期是否脫錨", 3),
    Series("T10YIE", "10年期損益兩平通膨率", "inflation", "%", "level", "D", None, "", 3),

    # ④ 利率與殖利率曲線 ------------------------------------------------
    Series("FEDFUNDS", "聯邦資金有效利率", "rates", "%", "level", "M", None, "", 35),
    Series("DGS2", "2年期公債殖利率", "rates", "%", "level", "D", None, "", 3),
    Series("DGS10", "10年期公債殖利率", "rates", "%", "level", "D", None, "", 3),
    Series("T10Y2Y", "10年−2年利差", "rates", "%", "level", "D", True,
           "倒掛（負值）在過去每一次衰退前都出現過，但領先時間從半年到兩年不等", 3),
    Series("T10Y3M", "10年−3個月利差", "rates", "%", "level", "D", True,
           "紐約聯準銀行的衰退模型用的是這一條，不是 10年−2年", 3),

    # ⑤ 金融條件 --------------------------------------------------------
    Series("NFCI", "全國金融條件指數", "financial", "", "level", "W", False,
           "芝加哥聯準銀行編製。0 = 歷史平均，正值代表條件緊縮", 6),
    Series("BAMLH0A0HYM2", "高收益債利差", "financial", "%", "level", "D", False,
           "FRED 對這條序列只提供近三年歷史（ICE BofA 為授權資料），百分位僅供參考", 3),

    # ⑥ 消費與房市 ------------------------------------------------------
    Series("UMCSENT", "密大消費者信心", "consumer", "", "level", "M", True, "", 45),
    Series("HOUST", "新屋開工", "consumer", "千戶", "level", "M", True, "年化", 48),
    Series("MORTGAGE30US", "30年期房貸利率", "consumer", "%", "level", "W", False, "", 4),
    Series("CSUSHPINSA", "Case-Shiller 房價指數", "consumer", "%", "yoy", "M", None,
           "落後約兩個月，是所有指標中時滯最長的", 85),
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
