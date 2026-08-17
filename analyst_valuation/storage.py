"""模組二資料落地：寫入 docs/data/valuation/ 供靜態儀表板讀取。

- latest.json            當日完整報告（含每檔明細，前端主要資料源）
- valuation_history.csv  每日彙總指標（可追蹤整體低估程度隨時間變化）
- snapshots/<date>.json  當日快照，供歷史查詢

`prefix` 讓同一組檔名可以在同一個 data_root 底下放好幾個股票池（S&P 500 用
預設空字串、Nasdaq-100 用 "ndx100_"），不必為每個股票池各開一個資料夾。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .secrets_redaction import redact_secrets


def _upsert_csv(path: Path, row: dict, key_col: str = "as_of") -> None:
    row_df = pd.DataFrame([row])
    if path.exists():
        existing = pd.read_csv(path)
        existing = existing[existing[key_col] != row[key_col]]
        combined = pd.concat([existing, row_df], ignore_index=True)
    else:
        combined = row_df
    path.parent.mkdir(parents=True, exist_ok=True)
    combined.sort_values(key_col).to_csv(path, index=False)


def save_report(report: dict, data_root: str = "docs/data/valuation", prefix: str = "") -> None:
    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)

    # 最後一道防線：任何序列化後的內容都再過一次遮蔽。單一來源的疏忽
    # （例外訊息夾帶 query string 裡的金鑰）不該有機會被 commit 進公開 repo。
    payload = redact_secrets(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    (root / f"{prefix}latest.json").write_text(payload)

    snapshots = root / f"{prefix}snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    (snapshots / f"{report['as_of']}.json").write_text(payload)

    index_path = root / f"{prefix}history_index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else []
    index = sorted({*[d for d in index if d != report["as_of"]], report["as_of"]})[-400:]
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2))

    upsides = [r["upside_pct"] for r in report["rows"] if r.get("upside_pct") is not None]
    upsides_sorted = sorted(upsides)
    n = len(upsides_sorted)
    median = None
    if n:
        median = upsides_sorted[n // 2] if n % 2 else (
            upsides_sorted[n // 2 - 1] + upsides_sorted[n // 2]
        ) / 2

    _upsert_csv(root / f"{prefix}valuation_history.csv", {
        "as_of": report["as_of"],
        "universe_count": report["counts"]["universe"],
        "covered_count": report["counts"]["with_target_and_price"],
        "median_upside_pct": round(median, 2) if median is not None else None,
        "mean_upside_pct": round(sum(upsides) / n, 2) if n else None,
        "pct_undervalued": round(100 * sum(1 for u in upsides if u > 0) / n, 2) if n else None,
        "sources": "|".join(report.get("sources_available", [])),
    })
