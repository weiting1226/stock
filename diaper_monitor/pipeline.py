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
    SCRAPED_PRICES_PATH,
    SIZE,
)

log = logging.getLogger(__name__)

REQUIRED_COLUMNS = ["date", "brand", "platform", "product_name", "pack_price", "piece_count"]
HISTORY_COLUMNS = ["date", "brand", "cheapest_unit_price", "cheapest_platform", "avg_unit_price", "offer_count"]


def _load_price_csv(path: str) -> pd.DataFrame:
    """共用的報價表讀取／換算邏輯，人工填的與自動爬蟲抓的共用同一份欄位定義。
    片數 <=0 或缺售價的列直接捨棄——那種列算不出單片價，留著只會在後面除以零。"""
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


def load_manual_prices(path: str = MANUAL_PRICES_PATH) -> pd.DataFrame:
    """讀取人工填入的報價表。人工填的資料一律信任，見 `load_all_prices`
    的合併規則——同一天、同一品牌、同一平台，這份資料永遠蓋過自動爬蟲。"""
    df = _load_price_csv(path)
    df["source"] = "人工"
    return df


def load_scraped_prices(path: str = SCRAPED_PRICES_PATH) -> pd.DataFrame:
    """讀取自動爬蟲（見 `diaper_monitor/sources/`）寫入的報價表。信任層級
    低於人工填寫，只用來補人工沒空查、或忘記查的空檔——見 `load_all_prices`。"""
    df = _load_price_csv(path)
    df["source"] = "自動爬蟲"
    return df


def load_all_prices(manual_path: str = MANUAL_PRICES_PATH,
                     scraped_path: Optional[str] = None) -> pd.DataFrame:
    """合併人工與自動爬蟲的報價。`scraped_path` 給 None 時完全不碰爬蟲資料——
    這是刻意的預設值，讓既有只傳 `manual_path` 的呼叫端（包含測試）行為不變。

    同一天、同一品牌、同一平台，人工的資料蓋過爬蟲的：爬蟲的角色是補人工的
    空檔，不是取代查證，兩邊都有資料時沒有理由採信沒人核對過的那一筆。"""
    manual = load_manual_prices(manual_path)
    if not scraped_path:
        return manual
    scraped = load_scraped_prices(scraped_path)
    if scraped.empty:
        return manual
    if manual.empty:
        return scraped
    manual_keys = set(zip(manual["date"], manual["brand"], manual["platform"]))
    scraped = scraped[
        ~scraped.apply(lambda r: (r["date"], r["brand"], r["platform"]) in manual_keys, axis=1)
    ]
    return pd.concat([manual, scraped], ignore_index=True)


def _load_history(path: str = HISTORY_PATH) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    return pd.read_csv(p, dtype={"date": str})


def _reconcile_history_for_date(as_of: str, today_rows: pd.DataFrame, path: str = HISTORY_PATH) -> None:
    """把 `history.csv` 裡 `as_of` 這天的所有列，換成這次執行算出來的結果
    （可能是空的）。跟 `storage.write_scraped_prices` 同一個理由：這次執行
    對 `as_of` 這天來說是唯一權威結果，不能只挑「這次批次裡也有的品牌」去
    覆蓋——某個品牌這次判定沒有貨真價實的今日資料時（例如上次是爬蟲抓錯、
    這次過濾規則收緊後正確判定「沒有」），history 裡舊的那筆也要跟著清掉，
    不能留著一筆已經知道是錯的資料繼續影響之後的近期平均
    （2026-08-18 那次修正 PChome 過濾規則後才發現這個洞：舊的覆蓋邏輯只在
    `today_rows` 非空時才動作，等於「重跑後沒抓到東西」永遠清不掉上次的錯誤資料）。"""
    hist = _load_history(path)
    hist = hist[hist["date"].astype(str) != str(as_of)]
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
                  history_path: str = HISTORY_PATH,
                  scraped_path: Optional[str] = None) -> dict:
    as_of = as_of or date_cls.today().isoformat()
    raw = load_all_prices(manual_path, scraped_path)
    today = raw[raw["date"].astype(str) == str(as_of)]
    history_before = _load_history(history_path)

    brands_out = []
    today_rows_for_history = []
    for brand in BRANDS:
        day_rows = today[today["brand"] == brand]
        data_date = as_of
        stale = False

        if day_rows.empty:
            # 今天沒填 = 忘記查價，不代表沒有價格可看。退回這個品牌有紀錄以來
            # 最近一筆（不含未來日期），並在報表上清楚標出那不是今天的資料——
            # 靜靜顯示「今日無報價」對每天都要看一眼的儀表板來說沒有幫助，
            # 但假裝那是今天的價格會誤導「顯著下跌」的判定，所以兩者都要避免。
            brand_rows = raw[(raw["brand"] == brand) & (raw["date"].astype(str) <= str(as_of))]
            if brand_rows.empty:
                brands_out.append({
                    "brand": brand,
                    "has_data": False,
                    "data_date": None,
                    "is_stale": False,
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
            data_date = brand_rows["date"].astype(str).max()
            day_rows = brand_rows[brand_rows["date"].astype(str) == data_date]
            stale = True

        summary = _brand_daily_summary(day_rows)
        if not stale:
            # 只有貨真價實「今天」的資料才寫進歷史——用來墊檔的舊資料重複寫入
            # 會讓近期平均被同一筆價格灌水，變相拉高判定「顯著下跌」的門檻
            today_rows_for_history.append({
                "date": as_of,
                "brand": brand,
                "cheapest_unit_price": summary["cheapest_unit_price"],
                "cheapest_platform": summary["cheapest_platform"],
                "avg_unit_price": summary["avg_unit_price"],
                "offer_count": summary["offer_count"],
            })

        baseline_avg, baseline_points = _baseline(history_before, brand, data_date)
        pct_change = None
        significant = False
        if baseline_avg:
            pct_change = round((summary["cheapest_unit_price"] - baseline_avg) / baseline_avg * 100, 1)
            significant = pct_change <= -DROP_THRESHOLD_PCT

        offers = (
            day_rows[["platform", "product_name", "pack_price", "piece_count", "unit_price", "url", "source"]]
            .sort_values("unit_price")
            .copy()
        )
        offers["unit_price"] = offers["unit_price"].round(2)

        brands_out.append({
            "brand": brand,
            "has_data": True,
            "data_date": data_date,
            "is_stale": stale,
            "offers": offers.to_dict("records"),
            "baseline_avg": baseline_avg,
            "baseline_points": baseline_points,
            "pct_change_vs_baseline": pct_change,
            "significant_drop": bool(significant),
            **summary,
        })

    _reconcile_history_for_date(
        as_of, pd.DataFrame(today_rows_for_history, columns=HISTORY_COLUMNS), history_path)

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
        "如果某個品牌當天沒填報價，畫面會退回顯示該品牌最近一次有資料的日期"
        "（並標示「非今日」），不是顯示成「今日無報價」——但那筆退回顯示的資料"
        "不會被當成「今天」寫進歷史，否則同一個價格會重複墊高近期平均。",
        f"「近期平均」為前 {BASELINE_WINDOW_DAYS} 天（不含當天）的「當日最便宜單價」平均，"
        f"歷史點數不足 {MIN_BASELINE_POINTS} 筆時不判定，避免用一兩筆資料誤判顯著下跌。",
        "資料以人工每日填入為主（見 data/diaper_monitor/manual_prices.csv），"
        "目前另有 PChome 的自動爬蟲補人工沒查到的空檔（見 data/diaper_monitor/scraped_prices.csv）——"
        "同一天、同一品牌、同一平台，人工填的資料永遠蓋過爬蟲抓到的；"
        "明細裡每一筆都標了「人工」或「自動爬蟲」的來源。",
        f"三個品牌／通路彼此不比較——「滿意寶寶日本境內版」「Aiwibi」「奢寵幫」不是同一件商品，"
        f"價格高低本來就不該放在一起看，各自只跟自己的近期平均比。",
    ]
