"""讀寫公司清單檔，並在單一處決定「哪些證券要納入抓取」。

抓取端與發布端必須套用**同一個**過濾條件：兩邊各寫一份的話，儀表板會出現
「有結果但不在清單裡」或「在清單裡卻永遠沒資料」的列，而且沒人會發現是
兩份定義不一致造成的。因此過濾只在這裡實作一次。

清單檔本身保留全部證券與其分類，過濾發生在讀取時——這樣看得出當初濾掉了
什麼，也不必重建清單就能改變範圍。
"""
from __future__ import annotations

import csv
import logging
from collections import Counter
from pathlib import Path
from typing import Optional

from .security_type import COMMON, classify_security, is_operating_company

log = logging.getLogger(__name__)

FIELDS = ["ticker", "name", "exchange", "is_etf", "security_type", "source"]


def _row_security_type(row: dict) -> str:
    """取得證券種類；舊版清單沒有這個欄位，就地由名稱推定。"""
    existing = (row.get("security_type") or "").strip()
    if existing:
        return existing
    return classify_security(row.get("ticker") or "", row.get("name") or "")


def load_universe(path: str, include_all: bool = False) -> tuple[list[dict], dict]:
    """讀取清單並套用過濾。回傳 (可用列, 統計)。

    統計會帶出各種類的檔數，讓「這次抓取涵蓋什麼」在日誌與報告裡是明說的，
    而不是要去比對兩個數字才推得出來。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"找不到公司清單 {path}；請先執行 scripts/build_universe.py 建立。")

    rows: list[dict] = []
    with p.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ticker = (row.get("ticker") or "").strip()
            if not ticker:
                continue
            row["ticker"] = ticker
            row["security_type"] = _row_security_type(row)
            rows.append(row)

    by_type = Counter(r["security_type"] for r in rows)
    if include_all:
        kept = rows
    else:
        kept = [r for r in rows if is_operating_company(r["security_type"])]

    stats = {
        "total": len(rows),
        "kept": len(kept),
        "excluded": len(rows) - len(kept),
        "by_type": dict(by_type),
        "include_all": include_all,
    }
    log.info("公司清單 %d 檔，納入抓取 %d 檔（排除 %d 檔非普通股：%s）",
             stats["total"], stats["kept"], stats["excluded"],
             {k: v for k, v in sorted(by_type.items()) if k != COMMON})
    return kept, stats


def load_tickers(path: str, include_all: bool = False) -> tuple[list[str], dict]:
    rows, stats = load_universe(path, include_all=include_all)
    return [r["ticker"] for r in rows], stats


def save_universe(rows: list[dict], path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: r["ticker"]):
            writer.writerow({k: row.get(k, "") for k in FIELDS})
