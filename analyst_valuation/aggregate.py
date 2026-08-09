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
