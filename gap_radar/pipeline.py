"""模組九主流程：從模組二（analyst_valuation）已產出的估值報告中，
篩出機構評等為「買入」或「強力買入」、且現價與共識目標價落差最大的股票，
並用目標價分歧度衡量這份樂觀共識背後的風險。

本模組不重新抓資料——直接讀模組二的 docs/data/valuation/latest.json，
避免「上漲空間」「分歧度」這兩個定義在兩個模組裡各自演化出不同答案。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BUY_KEYS = {"buy", "strong_buy"}
TOP_N = 10

# 風險分層門檻取自「全部買評股」的分歧度分布本身（中位數／前25%高點／前10%高點），
# 而不是一組寫死的百分比——分歧度的「高」是相對這份買評清單而言，不是絕對數字。
RISK_TIERS = ("low", "moderate", "high", "extreme")


def _median(sorted_vals: list[float]) -> Optional[float]:
    n = len(sorted_vals)
    if not n:
        return None
    mid = n // 2
    return sorted_vals[mid] if n % 2 else (sorted_vals[mid - 1] + sorted_vals[mid]) / 2


def _percentile(sorted_vals: list[float], p: float) -> Optional[float]:
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * p))
    return sorted_vals[idx]


def _risk_tier(dispersion: float, median: float, p75: float, p90: float) -> str:
    if dispersion > p90:
        return "extreme"
    if dispersion > p75:
        return "high"
    if dispersion > median:
        return "moderate"
    return "low"


def _eligible(row: dict) -> bool:
    """買評 + 上漲空間與分歧度都算得出來 + 未被標記為異常。"""
    return (
        row.get("recommendation_key") in BUY_KEYS
        and row.get("upside_pct") is not None
        and row.get("target_dispersion_pct") is not None
        and not row.get("implausible")
    )


def source_staleness(valuation_as_of: Optional[str], run_date: Optional[str]) -> dict:
    """模組九讀到的估值報告，比執行當天舊多少天。

    本模組整份輸出都是模組二那份 latest.json 的再加工，自己不抓任何資料。
    因此「模組二還沒跑」與「模組二跑完了」在這裡長得**一模一樣**：名單照樣
    排得出來、風險分層照樣算得出來、頁面照樣顯示，只是整份是昨天的。
    沒有例外、沒有空值，唯一的差別是 as_of 少了一天——所以那一天要被算出來。

    不在這裡丟例外：估值報告本來就會在週末與假日落後，把它變成失敗只會讓
    排程在每個週一以外的日子紅一片。判斷留給讀的人，數字則一定要在。
    """
    if not valuation_as_of or not run_date:
        return {"source_as_of": valuation_as_of, "days": None}
    days = (datetime.fromisoformat(run_date).date()
            - datetime.fromisoformat(valuation_as_of).date()).days
    return {"source_as_of": valuation_as_of, "days": days}


def build_report(valuation_report: dict, top_n: int = TOP_N,
                 run_date: Optional[str] = None) -> dict:
    run_date = run_date or datetime.now(timezone.utc).date().isoformat()
    rows = valuation_report.get("rows", [])
    candidates = [r for r in rows if _eligible(r)]

    disp_sorted = sorted(r["target_dispersion_pct"] for r in candidates)
    upside_sorted = sorted(r["upside_pct"] for r in candidates)
    median_disp = _median(disp_sorted)
    p75_disp = _percentile(disp_sorted, 0.75)
    p90_disp = _percentile(disp_sorted, 0.90)

    ranked = sorted(candidates, key=lambda r: r["upside_pct"], reverse=True)[:top_n]

    top10 = []
    for i, r in enumerate(ranked):
        disp = r["target_dispersion_pct"]
        top10.append({
            "rank": i + 1,
            "ticker": r["ticker"],
            "name": r.get("name"),
            "sector": r.get("sector"),
            "close": r.get("close"),
            "consensus_target": r.get("consensus_target"),
            "target_low": r.get("target_low"),
            "target_high": r.get("target_high"),
            "upside_pct": r["upside_pct"],
            "target_dispersion_pct": disp,
            "risk_tier": _risk_tier(disp, median_disp, p75_disp, p90_disp),
            "recommendation_key": r.get("recommendation_key"),
            "recommendation_mean": r.get("recommendation_mean"),
            "analyst_count": r.get("analyst_count"),
            "market_cap": r.get("market_cap"),
            "confidence": r.get("confidence"),
        })

    background = [
        {
            "ticker": r["ticker"],
            "upside_pct": r["upside_pct"],
            "target_dispersion_pct": r["target_dispersion_pct"],
            "market_cap": r.get("market_cap"),
        }
        for r in candidates
    ]

    return {
        "as_of": valuation_report.get("as_of"),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "universe": valuation_report.get("universe"),
        "source_generated_at": valuation_report.get("generated_at"),
        "source_staleness": source_staleness(valuation_report.get("as_of"), run_date),
        "counts": {
            "universe": valuation_report.get("counts", {}).get("universe"),
            "candidates": len(candidates),
            "buy": sum(1 for r in candidates if r.get("recommendation_key") == "buy"),
            "strong_buy": sum(1 for r in candidates if r.get("recommendation_key") == "strong_buy"),
        },
        "dispersion_stats": {
            "median": median_disp,
            "p75": p75_disp,
            "p90": p90_disp,
        },
        "median_upside_pct": _median(upside_sorted),
        "top10": top10,
        "background": background,
    }


def load_valuation_report(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"找不到模組二的估值報告：{path}。請先執行 scripts/run_valuation.py 產生資料。"
        )
    return json.loads(p.read_text())


def run(valuation_path: str = "docs/data/valuation/latest.json", top_n: int = TOP_N,
        run_date: Optional[str] = None) -> dict:
    valuation_report = load_valuation_report(valuation_path)
    return build_report(valuation_report, top_n=top_n, run_date=run_date)
