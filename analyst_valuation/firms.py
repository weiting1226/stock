"""分析機構名稱正規化與逐機構目標價去重。

「按照分析機構作為價格的主要來源分類去除重複」的核心：同一家券商可能透過
不同資料源以不同寫法出現（J.P. Morgan / JPMorgan / JP Morgan Securities），
若不正規化就會被當成三家，平均值被重複計算的機構拉偏。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# 券商名稱常見的尾綴／修飾詞，對「是哪一家機構」沒有辨識意義
_NOISE_WORDS = {
    "inc", "llc", "lp", "ltd", "plc", "co", "corp", "corporation", "company",
    "securities", "research", "capital", "markets", "market", "group", "partners",
    "equity", "equities", "investment", "investments", "bank", "banc", "financial",
    "brokerage", "advisors", "advisers", "and", "the", "us", "usa", "global",
    "international", "intl", "sa", "ag", "nv", "asset", "management",
}

# 已知的別名對應（正規化後仍不同、但實為同一家）
_ALIASES = {
    "jpmorgan": "jp morgan",
    "jpmorganchase": "jp morgan",
    "bofa": "bank of america",
    "bofamerrilllynch": "bank of america",
    "merrilllynch": "bank of america",
    "morganstanleyco": "morgan stanley",
    "deutschebk": "deutsche",
    "rbccm": "rbc",
    "ubsgroup": "ubs",
    "wellsfargogroup": "wells fargo",
    "goldmansachsgroup": "goldman sachs",
}

_PUNCT_RE = re.compile(r"[^\w\s]")
_SPACE_RE = re.compile(r"\s+")


def normalize_firm(name: Optional[str]) -> str:
    """把券商名稱正規化成可比對的鍵值。

    'J.P. Morgan Securities LLC' -> 'jp morgan'
    'JPMorgan Chase & Co.'       -> 'jp morgan'
    """
    if not name:
        return ""
    s = _PUNCT_RE.sub(" ", str(name).lower())
    s = _SPACE_RE.sub(" ", s).strip()
    if not s:
        return ""

    # 先查別名表（用去掉空白的形式比對，避免斷詞差異）
    compact = s.replace(" ", "")
    if compact in _ALIASES:
        return _ALIASES[compact]

    tokens = [t for t in s.split(" ") if t and t not in _NOISE_WORDS]
    if not tokens:                      # 整串都是修飾詞時保留原字串，別把它變成空鍵
        tokens = s.split(" ")
    normalized = " ".join(tokens)

    compact = normalized.replace(" ", "")
    return _ALIASES.get(compact, normalized)


@dataclass
class TargetRecord:
    """單一機構對單一標的的目標價。"""
    ticker: str
    firm: str                      # 原始機構名稱（顯示用）
    target: float
    source: str                    # 這筆記錄由哪個資料源提供
    published: Optional[str] = None  # ISO 日期，越新越優先

    @property
    def firm_key(self) -> str:
        return normalize_firm(self.firm)


@dataclass
class FirmConsensus:
    """由逐機構記錄自行計算出來的共識統計。"""
    mean: Optional[float] = None
    median: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    firm_count: int = 0
    firms: list = field(default_factory=list)      # [{firm, target, source, published}]
    sources: list = field(default_factory=list)    # 這些記錄來自哪些資料源
    duplicates_removed: int = 0


def dedupe_by_firm(records: list[TargetRecord]) -> list[TargetRecord]:
    """同一機構只保留一筆：優先取最新發布日，其次取先出現的資料源。

    這是「以分析機構為主要分類去除重複」的實作：跨資料源蒐集到的同一家券商
    目標價只會被計入一次，避免平均值被覆蓋率高的資料源重複灌票。
    """
    best: dict[str, TargetRecord] = {}
    for rec in records:
        key = rec.firm_key
        if not key or rec.target is None or rec.target <= 0:
            continue
        current = best.get(key)
        if current is None:
            best[key] = rec
            continue
        # 有發布日的優先；都有就取比較新的
        if (rec.published or "") > (current.published or ""):
            best[key] = rec
    return sorted(best.values(), key=lambda r: r.firm_key)


def build_firm_consensus(records: list[TargetRecord]) -> FirmConsensus:
    """去重後自行計算平均／中位數／高低，而不是沿用資料源給的共識值。"""
    unique = dedupe_by_firm(records)
    if not unique:
        return FirmConsensus()

    values = sorted(r.target for r in unique)
    n = len(values)
    median = values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2

    return FirmConsensus(
        mean=round(sum(values) / n, 4),
        median=round(median, 4),
        high=round(values[-1], 4),
        low=round(values[0], 4),
        firm_count=n,
        firms=[
            {"firm": r.firm, "target": round(r.target, 4), "source": r.source,
             "published": r.published}
            for r in unique
        ],
        sources=sorted({r.source for r in unique}),
        duplicates_removed=max(0, len([r for r in records if r.firm_key and r.target]) - n),
    )
