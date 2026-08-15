"""模組九資料落地：寫入 docs/data/gap_radar/ 供靜態儀表板讀取。

- latest.json           本次快照的完整報告（top10 + 全買評股背景散布，前端主要資料源）
- history.csv           每次快照的 top10 名單（一批一批累積），用來算「較上次」的名次變化
- snapshots/<date>.json 當次快照，供歷史查詢
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def _load_previous_ranks(history_path: Path, before_as_of: str) -> dict[str, int]:
    """取「本次之前最近一次」快照裡，每檔股票的名次，用來算變化。"""
    if not history_path.exists():
        return {}
    df = pd.read_csv(history_path)
    df = df[df["as_of"] < before_as_of]
    if df.empty:
        return {}
    last_as_of = df["as_of"].max()
    last = df[df["as_of"] == last_as_of]
    return dict(zip(last["ticker"], last["rank"]))


def save_report(report: dict, data_root: str = "docs/data/gap_radar") -> dict:
    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)

    history_path = root / "history.csv"
    prev_ranks = _load_previous_ranks(history_path, report["as_of"])
    for row in report["top10"]:
        prev = prev_ranks.get(row["ticker"])
        row["prev_rank"] = int(prev) if prev is not None else None
        row["is_new"] = prev is None

    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    (root / "latest.json").write_text(payload)

    snapshots = root / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    (snapshots / f"{report['as_of']}.json").write_text(payload)

    index_path = root / "history_index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else []
    index = sorted({*[d for d in index if d != report["as_of"]], report["as_of"]})[-104:]
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2))

    new_rows = pd.DataFrame([
        {
            "as_of": report["as_of"],
            "rank": r["rank"],
            "ticker": r["ticker"],
            "name": r["name"],
            "upside_pct": r["upside_pct"],
            "target_dispersion_pct": r["target_dispersion_pct"],
            "risk_tier": r["risk_tier"],
        }
        for r in report["top10"]
    ])
    if history_path.exists():
        existing = pd.read_csv(history_path)
        existing = existing[existing["as_of"] != report["as_of"]]
        combined = pd.concat([existing, new_rows], ignore_index=True)
    else:
        combined = new_rows
    combined.sort_values(["as_of", "rank"]).to_csv(history_path, index=False)

    return report
