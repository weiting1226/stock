"""模組八每日流程：讀取人工填入的各平台報價 -> 換算單片價 -> 取當日最低價
-> 與近期平均比較 -> 標示顯著下跌。

沒有計分、沒有燈號——這裡只有兩個數字（今天最便宜多少、跟近期比變動多少）
和一個門檻判定，出錯的方式主要是**算錯平均或抓錯視窗**，不是模型假設錯誤。
"""
from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import (
    BASELINE_WINDOW_DAYS,
    BRANDS,
    DROP_THRESHOLD_PCT,
    HISTORY_PATH,
    MANUAL_PRICES_PATH,
    MIN_BASELINE_POINTS,
    SIZE,
)

log = logging.getLogger(__name__)

REQUIRED_COLUMNS = ["date", "brand", "platform", "product_name", "pack_price", "piece_count"]
HISTORY_COLUMNS = ["date", "brand", "cheapest_unit_price", "cheapest_platform", "avg_unit_price", "offer_count"]


def load_manual_prices(path: str = MANUAL_PRICES_PATH) -> pd.DataFrame:
    """讀取人工填入的報價表，換算單片價。片數 <=0 或缺售價的列直接捨棄——
    那種列算不出單片價，留著只會在後面除以零。"""
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=REQUIRED_COLUMNS + ["unit_price", "url", "note"])
    df = pd.read_csv(p, dtype={"date": str})
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} 缺少欄位：{missing}")
    if "url" not in df.columns:
        df["url"] = ""
    if "note" not in df.columns:
        df["note"] = ""
    df[["url", "note"]] = df[["url", "note"]].fillna("")
    df = df.dropna(subset=["pack_price", "piece_count", "date", "brand", "platform"])
    df = df[df["piece_count"] > 0]
    df["unit_price"] = df["pack_price"] / df["piece_count"]
    return df


def _load_history(path: str = HISTORY_PATH) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    return pd.read_csv(p, dtype={"date": str})


def _append_history(today_rows: pd.DataFrame, path: str = HISTORY_PATH) -> None:
    if today_rows.empty:
        return
    hist = _load_history(path)
    # 以 (date, brand) 為鍵：同一天重跑要覆蓋，不是疊加
    keys = set(zip(today_rows["date"], today_rows["brand"]))
    if not hist.empty:
        hist = hist[~hist.apply(lambda r: (r["date"], r["brand"]) in keys, axis=1)]
    combined = pd.concat([hist, today_rows], ignore_index=True).sort_values(["brand", "date"])
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(p, index=False)


def _brand_daily_summary(day_rows: pd.DataFrame) -> dict:
    cheapest = day_rows.loc[day_rows["unit_price"].idxmin()]
    return {
        "cheapest_unit_price": round(float(cheapest["unit_price"]), 2),
        "cheapest_platform": str(cheapest["platform"]),
        "cheapest_product_name": str(cheapest.get("product_name", "")),
        "avg_unit_price": round(float(day_rows["unit_price"].mean()), 2),
        "offer_count": int(len(day_rows)),
    }


def _baseline(history: pd.DataFrame, brand: str, as_of: str,
              window_days: int = BASELINE_WINDOW_DAYS,
              min_points: int = MIN_BASELINE_POINTS) -> tuple[Optional[float], int]:
    """近期平均：取 as_of 之前（不含當天）、視窗天數內的「當日最便宜單價」序列平均。
    不含當天，否則「今天 vs 今天」的比較會被自己稀釋。"""
    if history.empty:
        return None, 0
    window_start = (date_cls.fromisoformat(as_of) - timedelta(days=window_days)).isoformat()
    rows = history[
        (history["brand"] == brand)
        & (history["date"].astype(str) < str(as_of))
        & (history["date"].astype(str) >= window_start)
    ]
    if len(rows) < min_points:
        return None, len(rows)
    return round(float(rows["cheapest_unit_price"].mean()), 2), len(rows)


def build_report(as_of: Optional[str] = None, manual_path: str = MANUAL_PRICES_PATH,
                  history_path: str = HISTORY_PATH) -> dict:
    as_of = as_of or date_cls.today().isoformat()
    raw = load_manual_prices(manual_path)
    today = raw[raw["date"].astype(str) == str(as_of)]
    history_before = _load_history(history_path)

    brands_out = []
    today_rows_for_history = []
    for brand in BRANDS:
        day_rows = today[today["brand"] == brand]
        if day_rows.empty:
            brands_out.append({
                "brand": brand,
                "has_data": False,
                "offers": [],
                "cheapest_unit_price": None,
                "cheapest_platform": None,
                "avg_unit_price": None,
                "offer_count": 0,
                "baseline_avg": None,
                "baseline_points": 0,
                "pct_change_vs_baseline": None,
                "significant_drop": False,
            })
            continue

        summary = _brand_daily_summary(day_rows)
        today_rows_for_history.append({
            "date": as_of,
            "brand": brand,
            "cheapest_unit_price": summary["cheapest_unit_price"],
            "cheapest_platform": summary["cheapest_platform"],
            "avg_unit_price": summary["avg_unit_price"],
            "offer_count": summary["offer_count"],
        })

        baseline_avg, baseline_points = _baseline(history_before, brand, as_of)
        pct_change = None
        significant = False
        if baseline_avg:
            pct_change = round((summary["cheapest_unit_price"] - baseline_avg) / baseline_avg * 100, 1)
            significant = pct_change <= -DROP_THRESHOLD_PCT

        offers = (
            day_rows[["platform", "product_name", "pack_price", "piece_count", "unit_price", "url"]]
            .sort_values("unit_price")
            .copy()
        )
        offers["unit_price"] = offers["unit_price"].round(2)

        brands_out.append({
            "brand": brand,
            "has_data": True,
            "offers": offers.to_dict("records"),
            "baseline_avg": baseline_avg,
            "baseline_points": baseline_points,
            "pct_change_vs_baseline": pct_change,
            "significant_drop": bool(significant),
            **summary,
        })

    if today_rows_for_history:
        _append_history(pd.DataFrame(today_rows_for_history, columns=HISTORY_COLUMNS), history_path)

    return {
        "as_of": as_of,
        "size": SIZE,
        "baseline_window_days": BASELINE_WINDOW_DAYS,
        "min_baseline_points": MIN_BASELINE_POINTS,
        "drop_threshold_pct": DROP_THRESHOLD_PCT,
        "brands": brands_out,
        "history": _history_for_chart(history_path),
        "notes": _notes(),
    }


def _history_for_chart(path: str = HISTORY_PATH, keep_days: int = 120) -> dict:
    hist = _load_history(path)
    if hist.empty:
        return {}
    cutoff = (date_cls.today() - timedelta(days=keep_days)).isoformat()
    hist = hist[hist["date"].astype(str) >= cutoff]
    out: dict = {}
    for brand, g in hist.groupby("brand"):
        g = g.sort_values("date")
        out[brand] = {
            "dates": g["date"].astype(str).tolist(),
            "cheapest_unit_price": [round(float(v), 2) for v in g["cheapest_unit_price"]],
        }
    return out


def _notes() -> list:
    return [
        f"「今日最便宜」取當天各平台報價中單片價最低者，不是各平台的平均——"
        f"使用者實際能拿到的價格就是最便宜的那個，用平均會把「其實有更便宜的選擇」平均掉。",
        f"「近期平均」為前 {BASELINE_WINDOW_DAYS} 天（不含當天）的「當日最便宜單價」平均，"
        f"歷史點數不足 {MIN_BASELINE_POINTS} 筆時不判定，避免用一兩筆資料誤判顯著下跌。",
        "資料來源為人工每日填入（見 data/diaper_monitor/manual_prices.csv），"
        "非自動爬蟲：多數平台對非登入的自動化請求有防爬機制，貿然爬取容易撞到服務條款，"
        "也容易在頁面改版後安靜地爬到錯誤價格。",
        f"三個品牌／通路彼此不比較——「滿意寶寶日本境內版」「Aiwibi」「奢寵幫」不是同一件商品，"
        f"價格高低本來就不該放在一起看，各自只跟自己的近期平均比。",
    ]
