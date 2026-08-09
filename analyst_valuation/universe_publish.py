"""把逐檔爬取的結果庫彙整成儀表板可讀的報告。

爬蟲把每一檔的原始查詢結果逐筆存進 `data/results/*.jsonl`，但那是「原料」：
一行一檔、沒有算上漲空間、也沒有類股彙總。這個模組負責把原料轉成
`docs/data/valuation/universe_latest.json`，也就是儀表板真正讀的那份。

刻意重用 `aggregate.build_row`（模組二既有的估值邏輯）而不是另寫一套：
上漲空間、離散度、信心度的定義只能有一個實作，否則全市場儀表板與
S&P 500 儀表板會慢慢算出不一樣的數字，而且沒人會發現。

爬取是跨多次執行完成的，所以報告一定要帶上進度：讀者必須能分辨
「這檔沒有分析師覆蓋」與「這檔今天還沒輪到」。
"""
from __future__ import annotations

import collections
import csv
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .aggregate import ValuationRow, build_row, sector_summary
from .firms import TargetRecord
from .ledger import Ledger
from .result_store import ResultStore
from .sources.prices import PriceSnapshot
from .sources.yahoo_targets import TargetQuote

log = logging.getLogger(__name__)


def load_universe_meta(path: str) -> dict[str, dict]:
    """讀公司清單，作為名稱與交易所的後備來源。

    類股優先用抓取時記下的（來自 Yahoo），清單本身沒有類股欄位。
    """
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, dict] = {}
    with p.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ticker = (row.get("ticker") or "").strip()
            if ticker:
                out[ticker] = {
                    "name": (row.get("name") or "").strip(),
                    "exchange": (row.get("exchange") or "").strip(),
                    "is_etf": (row.get("is_etf") or "").strip().lower() == "true",
                }
    return out


def _quotes_from(record: dict) -> list[TargetQuote]:
    return [
        TargetQuote(
            ticker=record.get("ticker", ""),
            source=q.get("source", "unknown"),
            mean=q.get("mean"),
            median=q.get("median"),
            high=q.get("high"),
            low=q.get("low"),
            analyst_count=q.get("analyst_count"),
            recommendation_key=q.get("recommendation_key"),
            responded=True,
        )
        for q in record.get("quotes") or []
    ]


def _firms_from(record: dict) -> list[TargetRecord]:
    return [
        TargetRecord(
            ticker=record.get("ticker", ""),
            firm=f.get("firm") or "",
            target=f.get("target"),
            source=f.get("source") or "",
            published=f.get("published"),
        )
        for f in record.get("firm_targets") or []
        if f.get("target")
    ]


def _price_from(record: dict) -> Optional[PriceSnapshot]:
    price = record.get("price") or {}
    if not price:
        return None
    return PriceSnapshot(
        ticker=record.get("ticker", ""),
        close=price.get("close"),
        close_date=price.get("close_date"),
        change_1w_pct=price.get("change_1w_pct"),
        change_1m_pct=price.get("change_1m_pct"),
        error=price.get("error"),
    )


def row_from_record(record: dict, meta: dict) -> ValuationRow:
    """把一筆爬取結果轉成估值列。類股以抓取時記下的為準，名稱則以清單為準。

    名稱優先用交易所清單：它寫的是法定證券名稱（含「Common Stock」等後綴），
    在全市場情境下比 Yahoo 的簡稱更能區分同一家公司的不同證券。
    """
    ticker = record.get("ticker", "")
    row = build_row(
        meta={
            "ticker": ticker,
            "name": meta.get("name") or record.get("name") or ticker,
            "sector": record.get("sector") or "Unknown",
            "sub_industry": record.get("industry") or "",
        },
        price=_price_from(record),
        quotes=_quotes_from(record),
        firm_records=_firms_from(record),
    )
    if record.get("covered") is False:
        row.notes.append(record.get("note") or "無分析師目標價覆蓋")
    return row


# 儀表板實際會用到的欄位。這份 JSON 是「畫面的資料來源」而不是保存用的檔案——
# 每一檔完整的原始查詢結果都留在 data/results/ 裡，這裡只需要畫得出表格的部分。
# 7,498 列的規模下，多留一個沒人讀的欄位就是多幾百 KB 的下載量與每日 commit。
_UI_FIELDS = (
    "ticker", "name", "sector", "close", "change_1w_pct", "change_1m_pct",
    "consensus_target", "target_low", "target_high", "target_dispersion_pct",
    "range_source", "analyst_count", "basis", "firm_targets", "duplicates_removed",
    "upside_pct", "implausible", "sources_used", "confidence", "notes",
)

# 絕大多數列都是這些值，逐列重複等於把同一個字抄幾千遍。前端已用 `?? / ||`
# 處理缺鍵，因此「省略」與「等於預設值」在畫面上完全等價。
_ROW_DEFAULTS = {"basis": "consensus", "duplicates_removed": 0, "implausible": False,
                 "range_source": "yahoo", "sources_used": ["yahoo"]}

# 即使是空值也要保留：前端拿它們做篩選與排序，缺鍵會讓「未知」與「不符合條件」
# 無法區分。
_ALWAYS_KEEP = {"ticker", "name", "sector", "close", "consensus_target",
                "upside_pct", "confidence", "analyst_count"}


def _is_empty(v) -> bool:
    if v is None or v is False:
        return True
    return isinstance(v, (str, list, dict)) and len(v) == 0


def compact_row(row: dict) -> dict:
    """只留下畫得出表格的欄位，並略過空值與預設值。"""
    out = {}
    _MISSING = object()
    for key in _UI_FIELDS:
        if key not in row:
            continue
        value = row[key]
        if key in _ALWAYS_KEEP:
            out[key] = value
        elif not _is_empty(value) and value != _ROW_DEFAULTS.get(key, _MISSING):
            out[key] = value
    return out


def build_report(
    results_root: str = "data/results",
    universe_path: str = "data/universe.csv",
    ledger_path: str = "data/ledger.csv",
    as_of: Optional[str] = None,
) -> dict:
    meta_map = load_universe_meta(universe_path)
    store = ResultStore(results_root)

    rows: list[ValuationRow] = []
    covered = uncovered = 0
    # 來源層級的錯誤（例如 FMP 免費方案回 402）每一檔都會帶一份，逐列存等於
    # 把同一句話抄 7,498 遍——佔掉整份 JSON 兩成。改成整份報告只說一次。
    source_errors: dict[str, int] = {}
    for record in store.iter_all():
        ticker = record.get("ticker")
        if not ticker:
            continue
        if err := record.get("firm_source_error"):
            source_errors[err] = source_errors.get(err, 0) + 1
        row = row_from_record(record, meta_map.get(ticker, {}))
        rows.append(row)
        if row.upside_pct is not None:
            covered += 1
        else:
            uncovered += 1

    # 預設排序：可比較的在前、異常的沉底。全市場尺度下若單純按上漲空間排，
    # 榜首會清一色是「現價 0.27 美元、目標價 4.8 美元」這類單一分析師的雞蛋
    # 水餃股，真正被低估的中大型股會被埋在幾百列之後，儀表板等於失去用途。
    rows.sort(key=lambda r: (r.upside_pct is None, r.implausible, -(r.upside_pct or 0)))
    implausible = sum(1 for r in rows if r.implausible)

    # 收盤日全部相同，逐列存等於重複幾千次同一個日期
    close_dates = collections.Counter(r.close_date for r in rows if r.close_date)
    progress = Ledger(ledger_path).load().summary() if Path(ledger_path).exists() else {}
    universe_total = progress.get("total") or len(meta_map) or len(rows)

    return {
        "as_of": as_of or date.today().isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "close_date": close_dates.most_common(1)[0][0] if close_dates else None,
        "universe": "全美股上市公司（NASDAQ／NYSE／NYSE American／Arca／CBOE）",
        "counts": {
            "universe": universe_total,
            "fetched": len(rows),
            "with_target_and_price": covered,
            "missing": uncovered,
            "not_yet_fetched": max(0, universe_total - len(rows)),
            "implausible": implausible,
        },
        # 爬取跨多次執行完成，沒有這段就無法分辨「沒人覆蓋」與「還沒輪到」
        "crawl_progress": progress,
        "coverage_note": (
            f"全市場 {universe_total} 檔逐檔查詢，已取得 {len(rows)} 檔；"
            f"其中 {covered} 檔有分析師目標價可比較，"
            f"{uncovered} 檔查得到但無分析師覆蓋（多為 SPAC 單位、認股權證等）；"
            f"另有 {implausible} 檔的目標價相對現價偏離過大，已標記為疑似異常並排到最後。"
        ),
        "source_errors": [
            {"error": e, "affected_tickers": n}
            for e, n in sorted(source_errors.items(), key=lambda kv: -kv[1])
        ],
        "sector_summary": sector_summary(rows),
        "rows": [compact_row(r.as_dict()) for r in rows],
    }


def sector_counts(rows: Iterable[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r.get("sector") or "Unknown"] = out.get(r.get("sector") or "Unknown", 0) + 1
    return out
