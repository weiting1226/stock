"""`diaper_monitor.storage.write_scraped_prices` 的測試。

沒有測試覆蓋這支函式，是 2026-08-18 那次真正在 GitHub Actions 上跑爬蟲時
才發現它的 dedupe 邏輯有洞：收緊過濾規則後某個品牌這次抓不到東西，舊的
（已經證實抓錯的）那一列卻留在檔案裡沒被清掉。這裡把那個情境寫成迴歸測試。
"""
from __future__ import annotations

import pandas as pd

from diaper_monitor import storage


def _quote(brand, platform, price=500, pieces=50):
    return {"brand": brand, "platform": platform, "product_name": f"{brand} 測試",
            "pack_price": price, "piece_count": pieces, "url": "", "note": ""}


def test_writes_a_fresh_file_when_none_exists(tmp_path):
    path = tmp_path / "scraped.csv"
    storage.write_scraped_prices([_quote("滿意寶寶日本境內版", "PChome24h購物")], "2026-08-18", str(path))
    df = pd.read_csv(path, dtype={"date": str})
    assert len(df) == 1
    assert df.iloc[0]["date"] == "2026-08-18"


def test_rerunning_the_same_day_replaces_all_of_that_days_rows_not_just_matching_keys(tmp_path):
    """關鍵迴歸案例：這次重跑某個品牌完全沒抓到東西（例如過濾規則收緊後
    識破了上次誤判的商品），上次那筆錯誤資料也要跟著被清掉，不能因為
    「這次的批次裡沒有同樣的 key」就被誤判成還有效、留在檔案裡。"""
    path = tmp_path / "scraped.csv"
    storage.write_scraped_prices(
        [_quote("Aiwibi", "PChome24h購物", price=529, pieces=36)], "2026-08-18", str(path))

    # 重跑同一天，這次過濾規則收緊，Aiwibi 什麼都沒抓到
    storage.write_scraped_prices([], "2026-08-18", str(path))

    df = pd.read_csv(path, dtype={"date": str})
    assert df.empty, f"上次的錯誤資料應該被清掉，但還在：{df.to_dict('records')}"


def test_rerunning_the_same_day_with_new_results_drops_the_old_ones(tmp_path):
    path = tmp_path / "scraped.csv"
    storage.write_scraped_prices(
        [_quote("奢寵幫", "PChome24h購物", price=1520, pieces=38)], "2026-08-18", str(path))
    storage.write_scraped_prices(
        [_quote("奢寵幫", "PChome24h購物", price=499, pieces=38)], "2026-08-18", str(path))

    df = pd.read_csv(path, dtype={"date": str})
    rows = df[(df["brand"] == "奢寵幫") & (df["date"] == "2026-08-18")]
    assert len(rows) == 1
    assert rows.iloc[0]["pack_price"] == 499


def test_rows_for_other_dates_are_left_untouched(tmp_path):
    path = tmp_path / "scraped.csv"
    storage.write_scraped_prices([_quote("奢寵幫", "PChome24h購物")], "2026-08-17", str(path))
    storage.write_scraped_prices([], "2026-08-18", str(path))

    df = pd.read_csv(path, dtype={"date": str})
    assert list(df["date"]) == ["2026-08-17"]
