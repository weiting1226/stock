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
    analyst_count: Optional[int] = None
    recommendation_mean: Optional[float] = None
    recommendation_key: Optional[str] = None

    upside_pct: Optional[float] = None
    source_targets: dict = field(default_factory=dict)  # {source: mean_target}
    sources_used: list = field(default_factory=list)
    confidence: str = "暫缺"   # 高／中／低／暫缺
    notes: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _confidence_for(analyst_count: Optional[int], n_sources: int) -> str:
    if not analyst_count:
        # 沒有家數資訊但有目標價時，仍算有資料，只是無法判斷代表性
        return "低" if n_sources else "暫缺"
    if analyst_count >= MIN_ANALYSTS_FOR_HIGH_CONFIDENCE and n_sources >= 2:
        return "高"
    if analyst_count >= MIN_ANALYSTS_FOR_HIGH_CONFIDENCE:
        return "中"
    return "低"


def build_row(
    meta: dict,
    price: Optional[PriceSnapshot],
    quotes: list[TargetQuote],
) -> ValuationRow:
    """合併單一標的的股票池資訊、收盤價與各來源目標價。"""
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

    if usable:
        # 多來源時取各來源共識價的平均（文件需求：「計算平均」）
        row.consensus_target = round(mean(q.mean for q in usable), 4)
        # 中位數/高低/家數以 Yahoo 為主，其次任一有值的來源
        primary = next((q for q in usable if q.source == "yahoo"), usable[0])
        row.target_median = primary.median
        row.target_high = primary.high
        row.target_low = primary.low
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
            row.notes.append(
                f"目標價相對現價偏離 {upside:.0f}%，疑似資料異常，已標記但未剔除"
            )
        row.upside_pct = round(upside, 2)

    row.confidence = (
        _confidence_for(row.analyst_count, len(row.sources_used))
        if row.upside_pct is not None else "暫缺"
    )
    return row


def sector_summary(rows: list[ValuationRow]) -> list[dict]:
    """各類股的中位數上漲空間與樣本數，供儀表板的類股比較圖使用。"""
    buckets: dict[str, list[float]] = {}
    for r in rows:
        if r.upside_pct is None:
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
