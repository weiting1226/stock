"""模組八的輸出入：寫 docs/data/diaper/latest.json，
在人工報價表不存在時建立帶欄位說明的範本，以及落地自動爬蟲抓到的報價。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .config import DATA_ROOT, MANUAL_PRICES_PATH, SCRAPED_PRICES_PATH

MANUAL_PRICES_COLUMNS = [
    "date", "brand", "platform", "product_name", "pack_price", "piece_count", "url", "note",
]


def write_report(report: dict, data_root: str = DATA_ROOT) -> Path:
    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)
    out = root / "latest.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def ensure_manual_prices_template(path: str = MANUAL_PRICES_PATH) -> None:
    """建立空白範本（僅表頭），不覆寫已存在的檔案。
    欄位說明見同資料夾的 README.md，CSV 本身不適合塞註解。"""
    p = Path(path)
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(",".join(MANUAL_PRICES_COLUMNS) + "\n", encoding="utf-8")


def write_scraped_prices(quotes: list[dict], as_of: str, path: str = SCRAPED_PRICES_PATH) -> Path:
    """把自動爬蟲抓到的報價（`diaper_monitor/sources/*.fetch_all` 的回傳值）
    落地成跟 `manual_prices.csv` 同樣格式的 CSV，補上 `date` 欄位。

    這次執行對 `as_of` 這天來說是**唯一且完整**的權威結果，所以先把舊檔裡
    `date == as_of` 的列全部丟掉，再整批換成這次抓到的（可能是空的）——
    不能只挑「這次也抓到的品牌/平台」去覆蓋，那樣的話某個品牌這次因為
    過濾規則收緊而查不到東西時，上次抓到的舊資料反而會被誤以為還有效、
    留在檔案裡沒人清（這正是 2026-08-18 那次修過濾規則時發生的狀況）。"""
    p = Path(path)
    new_rows = pd.DataFrame(
        [{**q, "date": as_of} for q in quotes],
        columns=MANUAL_PRICES_COLUMNS,
    )
    if p.exists():
        existing = pd.read_csv(p, dtype={"date": str})
        existing = existing[existing["date"].astype(str) != str(as_of)]
        combined = pd.concat([existing, new_rows], ignore_index=True)
    else:
        combined = new_rows
    p.parent.mkdir(parents=True, exist_ok=True)
    combined.sort_values(["brand", "date"]).to_csv(p, index=False)
    return p
