"""模組四的歷史累積：ETF 快照與名次。

名次要存下來才算得出「輪動」——沒有前一次的名次，就只有今天的排行榜，
看不出誰在往上爬。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from .config import HISTORY_PATH, SNAPSHOT_PATH


def _upsert(path: Path, df: pd.DataFrame, key: str = "as_of") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = pd.read_csv(path)
        # 同一天重跑要覆蓋而不是追加，否則歷史會出現重複的當日紀錄
        old = old[~old[key].astype(str).isin(df[key].astype(str))]
        df = pd.concat([old, df], ignore_index=True)
    df.sort_values(key).to_csv(path, index=False)


def save_snapshots(snapshots, path: str = SNAPSHOT_PATH, keep_days: int = 90) -> None:
    rows = [{"as_of": s.as_of, "ticker": s.ticker,
             "shares_outstanding": s.shares_outstanding,
             "total_assets": s.total_assets, "close": s.close} for s in snapshots]
    if not rows:
        return
    df = pd.DataFrame(rows)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        old = pd.read_csv(p)
        old = old[~old["as_of"].astype(str).isin(df["as_of"].astype(str))]
        df = pd.concat([old, df], ignore_index=True)
    keep = sorted(df["as_of"].astype(str).unique())[-keep_days:]
    df[df["as_of"].astype(str).isin(keep)].sort_values(["as_of", "ticker"]).to_csv(p, index=False)


def load_previous_snapshots(path: str = SNAPSHOT_PATH,
                            before: Optional[str] = None) -> dict[str, dict]:
    """取得最近一次（早於 `before`）的快照，以 ticker 為鍵。"""
    p = Path(path)
    if not p.exists():
        return {}
    df = pd.read_csv(p)
    if df.empty:
        return {}
    df["as_of"] = df["as_of"].astype(str)
    if before:
        df = df[df["as_of"] < str(before)]
    if df.empty:
        return {}
    latest = df[df["as_of"] == df["as_of"].max()]
    return {r["ticker"]: r.to_dict() for _, r in latest.iterrows()}


def append_ranks(as_of: str, ranks: dict, path: str = HISTORY_PATH) -> None:
    row = {"as_of": str(as_of)}
    row.update({t: ("" if v is None else int(v)) for t, v in ranks.items()})
    _upsert(Path(path), pd.DataFrame([row]))


def load_previous_ranks(path: str = HISTORY_PATH, before: Optional[str] = None) -> dict:
    """最近一次（早於 `before`）的名次。沒有就回空 dict——不假裝有變動。"""
    p = Path(path)
    if not p.exists():
        return {}
    df = pd.read_csv(p)
    if df.empty:
        return {}
    df["as_of"] = df["as_of"].astype(str)
    if before:
        df = df[df["as_of"] < str(before)]
    if df.empty:
        return {}
    last = df.sort_values("as_of").iloc[-1].drop("as_of")
    return {k: v for k, v in last.items() if pd.notna(v) and v != ""}
