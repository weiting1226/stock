"""模組八的輸出入：寫 docs/data/diaper/latest.json，
以及在人工報價表不存在時建立帶欄位說明的範本。"""
from __future__ import annotations

import json
from pathlib import Path

from .config import DATA_ROOT, MANUAL_PRICES_PATH

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
