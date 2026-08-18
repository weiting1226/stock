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

    跟 `pipeline._append_history` 同樣的鍵值邏輯：以 (date, brand, platform)
    為鍵，同一天重跑要覆蓋掉舊的爬蟲結果，不是無限疊加。"""
    p = Path(path)
    new_rows = pd.DataFrame(
        [{**q, "date": as_of} for q in quotes],
        columns=MANUAL_PRICES_COLUMNS,
    )
    if p.exists():
        existing = pd.read_csv(p, dtype={"date": str})
        if not new_rows.empty:
            keys = set(zip(new_rows["date"], new_rows["brand"], new_rows["platform"]))
            existing = existing[
                ~existing.apply(lambda r: (str(r["date"]), r["brand"], r["platform"]) in keys, axis=1)
            ]
        combined = pd.concat([existing, new_rows], ignore_index=True)
    else:
        combined = new_rows
    p.parent.mkdir(parents=True, exist_ok=True)
    combined.sort_values(["brand", "date"]).to_csv(p, index=False)
    return p
