"""發布日誌：每日掃描，記錄每一期數字**實際是哪一天出現的**。

FRED 的資料本身只帶「參考期」（例如 2026-06-01），不帶「這個數字是哪天公布的」。
`publish_lag_days` 目前是我依各機構的慣例手填的估計值——而手填的東西已經
出過一次錯（過期門檻誤報 7 條）。

因此改成**觀測**：每天掃一次，當某條序列的最新參考期往前推進時，就把
「今天」記成那一期的觀測發布日。累積幾個月之後，就有了這個專案自己量到的
發布節奏，可以拿來驗證（甚至取代）手填的估計。

記錄兩件事，缺一不可：
  observed_at   這一期第一次被我們看到的日期  -> 推導實際發布時滯
  last_scan     最後一次掃描的日期            -> 分辨「掃了但沒變」與「根本沒掃」

少了 last_scan，資料停止更新時看起來會跟排程壞掉一模一樣。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

RELEASE_LOG_PATH = "data/macro/release_log.csv"
SCAN_LOG_PATH = "data/macro/scan_log.csv"

_COLUMNS = ["fred_id", "reference_date", "observed_at", "value"]


def load_log(path: str = RELEASE_LOG_PATH) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=_COLUMNS)
    df = pd.read_csv(p)
    for c in _COLUMNS:
        if c not in df.columns:
            df[c] = None
    return df[_COLUMNS]


def latest_recorded(df: pd.DataFrame) -> dict[str, str]:
    """每條序列目前記錄到的最新參考期。"""
    if df.empty:
        return {}
    idx = df.groupby("fred_id")["reference_date"].max()
    return {k: str(v) for k, v in idx.items()}


def record_scan(
    observations: dict[str, tuple],
    scan_date: str,
    path: str = RELEASE_LOG_PATH,
) -> list[dict]:
    """比對本次掃描與既有記錄，把新出現的期別寫進日誌。

    `observations` 以 fred_id 為鍵，值為 (參考期, 數值)。
    回傳這次新記錄到的項目（供報告顯示「今天有哪些指標更新了」）。
    """
    df = load_log(path)
    known = latest_recorded(df)

    new_rows = []
    for fred_id, (ref, value) in observations.items():
        if ref is None:
            continue
        ref = str(ref)
        # 只在參考期真的往前推進時記錄。數值被事後修正而參考期不變的情況
        # 不算新發布——那是修正，不是發布。
        if known.get(fred_id) is not None and ref <= known[fred_id]:
            continue
        new_rows.append({"fred_id": fred_id, "reference_date": ref,
                         "observed_at": str(scan_date),
                         "value": None if value is None else float(value)})

    if new_rows:
        out = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
        out = out.drop_duplicates(subset=["fred_id", "reference_date"], keep="first")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        out.sort_values(["fred_id", "reference_date"]).to_csv(path, index=False)
    return new_rows


def record_scan_time(scan_date: str, series_ids, path: str = SCAN_LOG_PATH) -> None:
    """記錄「這一天確實掃過這些序列」。

    沒有這一筆的話，「資料停止更新」與「排程壞掉沒跑」在日誌上長得一模一樣。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = pd.DataFrame([{"scan_date": str(scan_date), "series_scanned": len(list(series_ids))}])
    if p.exists():
        old = pd.read_csv(p)
        old = old[old["scan_date"].astype(str) != str(scan_date)]
        row = pd.concat([old, row], ignore_index=True)
    row.sort_values("scan_date").to_csv(p, index=False)


def observed_lag_stats(df: pd.DataFrame, fred_id: str) -> Optional[dict]:
    """由日誌算出這條序列**實際觀測到**的發布時滯。

    只有累積兩期以上才回報——一期算不出節奏，硬給一個數字會讓人以為
    那是量出來的結果。
    """
    sub = df[df["fred_id"] == fred_id]
    if len(sub) < 2:
        return None
    ref = pd.to_datetime(sub["reference_date"])
    obs = pd.to_datetime(sub["observed_at"])
    lags = (obs - ref).dt.days
    return {
        "observations": int(len(sub)),
        "median_lag_days": int(lags.median()),
        "min_lag_days": int(lags.min()),
        "max_lag_days": int(lags.max()),
        # 掃描是每日一次，因此觀測到的發布日最多晚真實發布日一天。
        # 這個誤差方向是固定的（只會高估），要講清楚而不是假裝沒有。
        "note": "由每日掃描觀測而得，最多高估一天（掃描頻率所限）",
    }


def days_since_last_change(df: pd.DataFrame, fred_id: str, as_of: str) -> Optional[int]:
    """距離這條序列上一次「參考期往前推進」過了幾天。"""
    sub = df[df["fred_id"] == fred_id]
    if sub.empty:
        return None
    last = pd.to_datetime(sub["observed_at"]).max()
    return int((pd.Timestamp(as_of) - last).days)
