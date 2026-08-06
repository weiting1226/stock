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


def ensure_manual_overrides_template(path: str = "docs/data/manual_overrides.json") -> None:
    p = Path(path)
    if p.exists():
        return
    template = {
        "etf_fund_flow": {
            "score": None,
            "as_of": None,
            "note": "股票型ETF資金流：-2(大幅淨流出)/-1/0(持平)/+1/+2(大幅淨流入)。請人工查證 ICI/State Street/Fidelity 資料後填入 score。",
        },
        "fedwatch_path": {
            "score": None,
            "as_of": None,
            "note": "CME FedWatch市場隱含12個月路徑：降息>=2碼:+2/降息1碼:+1/持平:0/升息1碼:-1/升息>=2碼:-2。請至 CME FedWatch 網站人工查證後填入。",
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
