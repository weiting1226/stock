"""把多來源目標價 + 收盤價彙整成單一標的的估值紀錄（純函式，無網路存取）。

核心輸出是 upside_pct =（共識目標價 − 收盤價）/ 收盤價 × 100，
即「分析師認為還有多少上漲空間」，正值越大代表相對越被低估。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Optional

from .config import (
    IMPLAUSIBLE_DOWNSIDE_PCT,
    IMPLAUSIBLE_PB_FLOOR,
    IMPLAUSIBLE_UPSIDE_PCT,
    MIN_ANALYSTS_FOR_HIGH_CONFIDENCE,
    MIN_ANALYSTS_TO_INCLUDE,
)
from .firms import TargetRecord, build_firm_consensus
from .sources.prices import PriceSnapshot
from .sources.yahoo_targets import TargetQuote


@dataclass
class ValuationRow:
    ticker: str
    name: str = ""
    sector: str = "Unknown"
    sub_industry: str = ""

    close: Optional[float] = None
    close_date: Optional[str] = None
    change_1w_pct: Optional[float] = None
    change_1m_pct: Optional[float] = None

    consensus_target: Optional[float] = None
    target_median: Optional[float] = None
    target_high: Optional[float] = None
    target_low: Optional[float] = None
    target_dispersion_pct: Optional[float] = None  # (最高-最低)/平均，越大代表分歧越大
    range_source: Optional[str] = None             # 高低價與離散度取自哪個來源
    analyst_count: Optional[int] = None

    # 逐機構層資訊（有 per-firm 資料源時才有值）
    basis: str = "consensus"        # "per_firm"=自行由各機構目標價平均；"consensus"=沿用資料源彙總值
    firm_targets: list = field(default_factory=list)  # [{firm, target, source, published}]
    duplicates_removed: int = 0     # 依機構去重時被合併掉的重複筆數
    recommendation_mean: Optional[float] = None
    recommendation_key: Optional[str] = None

    # 淨值面：每股淨值與股價淨值比。與分析師目標價互為對照——目標價是「別人
    # 怎麼看」，淨值是帳上實際有多少股東權益。
    book_value: Optional[float] = None
    price_to_book: Optional[float] = None
    # 有淨值卻算不出可用比值時的原因，None 表示比值可用。
    # 用原因字串而不是一堆布林旗標：這類「算得出來但不能用」的情況會持續增加，
    # 而畫面要說的是「為什麼沒有」，留白會跟「查無資料」混在一起。
    #   "negative"            股東權益為負
    #   "share_class_mismatch" 價格與淨值不是同一種股別
    book_value_issue: Optional[str] = None

    # 市值（美元）。用來把「跌破淨值」「上漲空間很大」這類結果按規模分層看：
    # 同一個篩選條件，在巨型股與奈米股身上的意義完全不同。
    market_cap: Optional[float] = None
    # 有市值數字卻不能採用時的原因，None 表示可用。理由同 book_value_issue：
    # 「查無資料」與「查得到但不可比」在篩選時的意義相反，不能都留白。
    #   "non_usd"  以非美元計價，與其他標的不可比
    market_cap_issue: Optional[str] = None

    upside_pct: Optional[float] = None
    # 目標價相對現價偏離到不合理的程度（多為雞蛋水餃股搭配單一分析師）。
    # 做成結構化欄位而不是只寫進 notes：排序與篩選要靠它，比對說明文字太脆弱。
    implausible: bool = False
    source_targets: dict = field(default_factory=dict)  # {source: mean_target}
    sources_used: list = field(default_factory=list)
    confidence: str = "暫缺"   # 高／中／低／暫缺
    notes: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _pick_primary(usable: list[TargetQuote]) -> TargetQuote:
    """挑出提供高低價區間的主來源。

    離散度的分子（最高−最低）與分母（平均）必須來自**同一批分析師**，
    否則等於拿 A 券商群的區間去除以 A、B 混合的平均，數字沒有意義。
    因此高低價、中位數、離散度一律取自同一個來源，優先 Yahoo。
    """
    complete = [q for q in usable if q.high and q.low]
    for pool in (complete, usable):
        if not pool:
            continue
        return next((q for q in pool if q.source == "yahoo"), pool[0])
    return usable[0]


def _confidence_for(analyst_count: Optional[int], n_sources: int, basis: str = "consensus") -> str:
    if not analyst_count:
        # 沒有家數資訊但有目標價時，仍算有資料，只是無法判斷代表性
        return "低" if n_sources else "暫缺"
    if analyst_count < MIN_ANALYSTS_FOR_HIGH_CONFIDENCE:
        return "低"
    # 逐機構資料可驗證每一家的貢獻、且已去重，本身就比彙總值可信，
    # 因此不像共識層那樣要求兩個來源才給「高」
    if basis == "per_firm" or n_sources >= 2:
        return "高"
    return "中"


def apply_book_value(row: ValuationRow, book_value: Optional[float]) -> None:
    """填入每股淨值並計算股價淨值比。

    股價淨值比刻意用**本專案自己的收盤價**去除，而不是沿用資料源的現成比值：
    否則分子（畫面上顯示的收盤價）與分母（資料源當時的價格快照）來自不同時點，
    使用者拿收盤價除以淨值會得到跟欄位不一樣的數字（比照離散度的處理原則）。

    另外抽成獨立函式是因為「沒有分析師覆蓋」的標的也要有這個欄位——
    「沒人追蹤但跌破淨值」正是這個篩選最有意義的情境。
    """
    row.book_value = book_value
    if book_value is None or not row.close:
        return
    if book_value <= 0:
        # 負淨值算得出比值卻毫無意義（負除以負還會看起來很便宜），必須擋掉
        row.book_value_issue = "negative"
        row.notes.append(f"每股淨值為負（{book_value:.2f}），股價淨值比不適用")
        return

    ratio = row.close / book_value
    if ratio < IMPLAUSIBLE_PB_FLOOR:
        # 比值低到這種程度不是便宜，是價格與淨值不同股別（BRK-B 對到 A 股淨值）。
        # 不猜換算比例：猜錯會產生一個看起來很合理、實際上錯誤的數字。
        row.book_value_issue = "share_class_mismatch"
        row.notes.append(
            f"每股淨值 {book_value:,.2f} 與收盤價 {row.close:,.2f} 落差過大，"
            "研判為不同股別的數值，股價淨值比不採用"
        )
        return

    row.price_to_book = round(ratio, 3)


def apply_market_cap(
    row: ValuationRow,
    market_cap: Optional[float],
    currency: Optional[str] = None,
) -> None:
    """填入市值，並擋掉不是以美元計價的數字。

    市值篩選是拿一個標的的數字去跟門檻比大小，因此**單位必須一致**。
    資料源給的市值以該檔的計價幣別為準，若混進非美元的數字，一檔日圓計價的
    中型企業會被算成「巨型股」——那不會報錯，只會靜靜地排在名單最上面。
    幣別不是美元就不採用，並記下原因（留白會跟「查無市值」混在一起）。

    與 apply_book_value 一樣抽成獨立函式：沒有分析師覆蓋的標的也要有市值，
    「沒人追蹤的小型股」正是這個篩選最有意義的情境。
    """
    if market_cap is None or market_cap <= 0:
        return
    if currency and str(currency).upper() != "USD":
        row.market_cap_issue = "non_usd"
        row.notes.append(f"市值以 {currency} 計價，與其他標的不可比，未採用")
        return
    row.market_cap = market_cap


def build_row(
    meta: dict,
    price: Optional[PriceSnapshot],
    quotes: list[TargetQuote],
    firm_records: Optional[list[TargetRecord]] = None,
) -> ValuationRow:
    """合併單一標的的股票池資訊、收盤價與目標價。

    `firm_records` 為逐機構目標價。有的話一律優先：先依機構去重（同一家券商
    只算一次），再**自行計算**平均／中位數／高低，而不是沿用資料源給的共識值。
    沒有時才退回各共識來源的平均，並把 basis 標為 "consensus"。
    """
    row = ValuationRow(
        ticker=meta.get("ticker", ""),
        name=meta.get("name", ""),
        sector=meta.get("sector") or "Unknown",
        sub_industry=meta.get("sub_industry", ""),
    )

    if price is not None:
        row.close = price.close
        row.close_date = price.close_date
        row.change_1w_pct = price.change_1w_pct
        row.change_1m_pct = price.change_1m_pct
        if price.error:
            row.notes.append(f"價格：{price.error}")

    usable = [q for q in quotes if q.ok]
    for q in usable:
        row.source_targets[q.source] = round(q.mean, 4)
        row.sources_used.append(q.source)
    for q in quotes:
        if not q.ok and q.error:
            row.notes.append(f"{q.source}：{q.error}")

    firm_consensus = build_firm_consensus(firm_records or [])
    if firm_consensus.firm_count:
        # --- 逐機構層：依機構去重後自行計算，不沿用任何資料源的彙總值 ---
        row.basis = "per_firm"
        row.consensus_target = firm_consensus.mean
        row.target_median = firm_consensus.median
        row.target_high = firm_consensus.high
        row.target_low = firm_consensus.low
        row.analyst_count = firm_consensus.firm_count
        row.firm_targets = firm_consensus.firms
        row.duplicates_removed = firm_consensus.duplicates_removed
        row.range_source = "+".join(firm_consensus.sources)
        # sources_used 只列出「真的產生了這個數字」的來源；source_targets 保留
        # 共識來源的值供對照（可看出自算平均與資料源共識差多少）
        row.sources_used = list(firm_consensus.sources)
        if firm_consensus.high and firm_consensus.low and firm_consensus.mean:
            row.target_dispersion_pct = round(
                (firm_consensus.high - firm_consensus.low) / firm_consensus.mean * 100, 2
            )
        # 評級沿用共識來源（逐機構來源不提供）
        row.recommendation_mean = next((q.recommendation_mean for q in usable if q.recommendation_mean), None)
        row.recommendation_key = next((q.recommendation_key for q in usable if q.recommendation_key), None)

    elif usable:
        # --- 共識層：只拿得到彙總值，取各來源共識價的平均 ---
        row.consensus_target = round(mean(q.mean for q in usable), 4)
        primary = _pick_primary(usable)
        row.range_source = primary.source
        row.target_median = primary.median
        row.target_high = primary.high
        row.target_low = primary.low
        if primary.high and primary.low and primary.mean and primary.high >= primary.low:
            row.target_dispersion_pct = round(
                (primary.high - primary.low) / primary.mean * 100, 2
            )
        row.analyst_count = next(
            (q.analyst_count for q in usable if q.analyst_count), None
        )
        row.recommendation_mean = next(
            (q.recommendation_mean for q in usable if q.recommendation_mean), None
        )
        row.recommendation_key = next(
            (q.recommendation_key for q in usable if q.recommendation_key), None
        )

    if row.analyst_count is not None and row.analyst_count < MIN_ANALYSTS_TO_INCLUDE:
        row.consensus_target = None
        row.notes.append("分析師家數不足，不採用共識目標價")

    apply_book_value(
        row, next((q.book_value for q in quotes if q.book_value is not None), None)
    )
    cap_quote = next((q for q in quotes if q.market_cap is not None), None)
    if cap_quote is not None:
        apply_market_cap(row, cap_quote.market_cap, cap_quote.currency)

    if row.consensus_target and row.close:
        upside = (row.consensus_target / row.close - 1) * 100
        if upside > IMPLAUSIBLE_UPSIDE_PCT or upside < IMPLAUSIBLE_DOWNSIDE_PCT:
            row.implausible = True
            row.notes.append(
                f"目標價相對現價偏離 {upside:.0f}%，疑似資料異常，已標記但未剔除"
            )
        row.upside_pct = round(upside, 2)

    row.confidence = (
        _confidence_for(row.analyst_count, len(row.sources_used), row.basis)
        if row.upside_pct is not None else "暫缺"
    )
    return row


def sector_summary(rows: list[ValuationRow]) -> list[dict]:
    """各類股的中位數上漲空間與樣本數，供儀表板的類股比較圖使用。

    排除已標記為異常的列：全市場範圍內有數百檔雞蛋水餃股帶著上千 % 的
    「上漲空間」，把它們算進去，類股中位數反映的是資料雜訊而不是行情。
    （S&P 500 範圍幾乎沒有這種列，因此原本的數字不受影響。）
    """
    buckets: dict[str, list[float]] = {}
    for r in rows:
        if r.upside_pct is None or r.implausible:
            continue
        buckets.setdefault(r.sector, []).append(r.upside_pct)

    out = []
    for sector, values in buckets.items():
        values.sort()
        n = len(values)
        median = values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2
        out.append({
            "sector": sector,
            "count": n,
            "median_upside_pct": round(median, 2),
            "mean_upside_pct": round(sum(values) / n, 2),
        })
    return sorted(out, key=lambda d: d["median_upside_pct"], reverse=True)
