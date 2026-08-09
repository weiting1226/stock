"""資料落地：把 pipeline.run() 的報告寫成儀表板可讀取的檔案。

輸出全部放在 `docs/`，這樣 GitHub Pages 可以直接把 `docs/` 當成網站根目錄，
資料檔案（JSON/CSV）與前端頁面放在一起、不需要另外的後端伺服器。

- `docs/data/latest.json`      當日完整報告（給儀表板「今日診斷」畫面）
- `docs/data/scores_history.csv` 逐日綜合分數/燈號/類別分數（給趨勢圖）
- `docs/data/raw_indicators.csv` 逐日18項原始值與分數（給單一指標歷史圖）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import CATEGORY_LABELS


def _upsert_csv(path: Path, row: dict, key_col: str = "as_of") -> None:
    row_df = pd.DataFrame([row])
    if path.exists():
        existing = pd.read_csv(path)
        existing = existing[existing[key_col] != row[key_col]]
        combined = pd.concat([existing, row_df], ignore_index=True)
    else:
        combined = row_df
    combined = combined.sort_values(key_col)
    path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(path, index=False)


def save_report(report: dict, data_root: str = "docs/data") -> None:
    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)

    (root / "latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str)
    )

    # 附加到「歷史快照」清單，供儀表板列出可回顧的過去日期
    history_index_path = root / "history_index.json"
    history_index = []
    if history_index_path.exists():
        history_index = json.loads(history_index_path.read_text())
    history_index = [d for d in history_index if d != report["as_of"]]
    history_index.append(report["as_of"])
    history_index = sorted(history_index)[-400:]  # 保留最近400筆快照索引
    history_index_path.write_text(json.dumps(history_index, ensure_ascii=False, indent=2))

    snapshots_dir = root / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    (snapshots_dir / f"{report['as_of']}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str)
    )

    score_row = {
        "as_of": report["as_of"],
        "composite_score": report["composite_score"],
        "light": report["light"],
        "position_final": report["position"]["final"],
        "leverage_ladder": report["position"]["leverage_ladder"],
        "gate_a_cap": report["gate_a"].get("cap"),
        "gate_b_cap": report["gate_b"].get("cap"),
        "gate_c_cap": report["gate_c"].get("cap"),
        "missing_or_low_confidence_count": report["missing_or_low_confidence_count"],
    }
    for label, val in report["category_scores"].items():
        score_row[label] = val
    _upsert_csv(root / "scores_history.csv", score_row)

    raw_row = {"as_of": report["as_of"]}
    for key, item in report["items"].items():
        raw_row[f"{key}__value"] = item["raw_value"]
        raw_row[f"{key}__score"] = item["score"]
        raw_row[f"{key}__confidence"] = item["confidence"]
    _upsert_csv(root / "raw_indicators.csv", raw_row)


ETF_FLOW_HISTORY = "docs/data/etf_flows.csv"


def append_etf_flow(observation, path: str = ETF_FLOW_HISTORY) -> None:
    """把每日觀測到的 ETF 資金流累積下來。

    來源只給「當前值」，但要判斷這個數字算不算「大幅」，必須有它自己的
    歷史分布可以比。因此逐日累積成序列——這份檔案就是那個分布的來源。
    以 as_of 為鍵覆寫，同一天重跑不會產生重複列。
    """
    _upsert_csv(Path(path), {
        "as_of": observation.as_of,
        "net_flow_musd": observation.net_flow_musd,
        "scope": observation.scope,
        "period": observation.period,
        "source_url": observation.source_url,
    })


def load_etf_flow_history(path: str = ETF_FLOW_HISTORY, before: Optional[str] = None) -> list[float]:
    """讀出歷史淨流金額。

    `before` 用來排除當日自己那筆：拿今天的值去跟「含今天的分布」比，
    等於部分拿自己當基準，極端值會被自己稀釋掉。
    """
    p = Path(path)
    if not p.exists():
        return []
    df = pd.read_csv(p)
    if "net_flow_musd" not in df.columns:
        return []
    if before is not None and "as_of" in df.columns:
        df = df[df["as_of"].astype(str) < str(before)]
    return [float(v) for v in df["net_flow_musd"].dropna()]


ETF_SNAPSHOT_HISTORY = "docs/data/etf_snapshots.csv"

_SNAPSHOT_FIELDS = ["as_of", "ticker", "shares_outstanding", "total_assets", "close"]


def save_etf_snapshots(snapshots, path: str = ETF_SNAPSHOT_HISTORY, keep_days: int = 400) -> None:
    """保存各 ETF 的規模快照，供下次執行計算差額。

    以 (as_of, ticker) 為鍵覆寫，同一天重跑不會產生重複列。只保留近期若干天：
    計算差額只需要前一次觀測，留著幾年份的逐檔快照只會讓 repo 一直長大。
    """
    rows = [
        {
            "as_of": s.as_of, "ticker": s.ticker,
            "shares_outstanding": s.shares_outstanding,
            "total_assets": s.total_assets, "close": s.close,
        }
        for s in snapshots if s.ok
    ]
    if not rows:
        return

    p = Path(path)
    new = pd.DataFrame(rows, columns=_SNAPSHOT_FIELDS)
    if p.exists():
        existing = pd.read_csv(p)
        stamps = {(r["as_of"], r["ticker"]) for r in rows}
        mask = existing.apply(lambda r: (str(r["as_of"]), str(r["ticker"])) in stamps, axis=1)
        existing = existing[~mask]
        new = pd.concat([existing, new], ignore_index=True)

    new = new.sort_values(["as_of", "ticker"])
    kept_dates = sorted(new["as_of"].astype(str).unique())[-keep_days:]
    new = new[new["as_of"].astype(str).isin(kept_dates)]
    p.parent.mkdir(parents=True, exist_ok=True)
    new.to_csv(p, index=False)


def load_previous_etf_snapshots(path: str = ETF_SNAPSHOT_HISTORY,
                                before: Optional[str] = None) -> dict[str, dict]:
    """取出每檔 ETF「最近一次、且早於 before」的快照。

    必須排除當日自己那筆，否則差額會變成零——同一天重跑時尤其明顯。
    """
    p = Path(path)
    if not p.exists():
        return {}
    df = pd.read_csv(p)
    if df.empty or "ticker" not in df.columns:
        return {}
    if before is not None:
        df = df[df["as_of"].astype(str) < str(before)]
    if df.empty:
        return {}
    df = df.sort_values("as_of")
    latest = df.groupby("ticker").tail(1)
    return {str(r["ticker"]): r.to_dict() for _, r in latest.iterrows()}


def ensure_manual_overrides_template(path: str = "docs/data/manual_overrides.json") -> None:
    p = Path(path)
    if p.exists():
        return
    template = {
        "etf_fund_flow": {
            "score": None,
            "as_of": None,
            "note": "股票型ETF資金流：-2(大幅淨流出)/-1/0(持平)/+1/+2(大幅淨流入)。**備援用**——正常情況由 ETF.com 自動抓取，只有抓取失敗時才會採用這裡的值。",
        },
        "fedwatch_path": {
            "score": None,
            "as_of": None,
            "note": "CME FedWatch市場隱含12個月路徑：降息>=2碼:+2/降息1碼:+1/持平:0/升息1碼:-1/升息>=2碼:-2。請至 CME FedWatch 網站人工查證後填入。",
        },
        "fomc_decision": {
            "score": None,
            "as_of": None,
            "note": "FOMC決議偏向：降息無異議:+2/降息有異議:+1/持平無異議:0/持平但有升息異議:-1/升息:-2。"
                    "GitHub Actions 連不到 federalreserve.gov，故每次FOMC會後請人工查閱聲明並填入，"
                    "as_of 填會議日期（逾60天未更新會自動改標暫缺）。",
        },
        "ndx_fwd_pe": {
            "value": None,
            "as_of": None,
            "note": "NDX前瞻本益比，供 Gate B 脆弱度檢查使用（>85百分位/約>27x 觸發）。本專案無自動資料源，僅供人工填入參考。",
        },
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(template, ensure_ascii=False, indent=2))


def category_label_columns() -> list[str]:
    return list(CATEGORY_LABELS.values())
