"""模組八（尿布單價監控）的測試。

沒有計分模型，出錯的方式主要是**算錯兩個數字**：把「最便宜」算成「平均」，
或是近期平均的視窗/門檻抓錯，導致顯著下跌被漏報或誤報。
"""
from __future__ import annotations

import datetime

import pandas as pd
import pytest

from diaper_monitor import pipeline
from diaper_monitor.config import BASELINE_WINDOW_DAYS, BRANDS, MIN_BASELINE_POINTS


def _write_manual_csv(path, rows):
    header = "date,brand,platform,product_name,pack_price,piece_count,url,note\n"
    body = "\n".join(
        f"{d},{brand},{platform},測試商品 M {pieces}片,{price},{pieces},,"
        for d, brand, platform, price, pieces in rows
    )
    path.write_text(header + body + "\n", encoding="utf-8")


def _seed_history(manual_path, history_path, brand, daily_price, n_days,
                   start=datetime.date(2026, 6, 1), platform="蝦皮"):
    """跑 n_days 天的每日流程，讓 history.csv 累積出一段基準期間。"""
    rows = []
    for i in range(n_days):
        d = (start + datetime.timedelta(days=i)).isoformat()
        rows.append((d, brand, platform, daily_price * 64, 64))
    _write_manual_csv(manual_path, rows)
    for i in range(n_days):
        d = (start + datetime.timedelta(days=i)).isoformat()
        pipeline.build_report(as_of=d, manual_path=str(manual_path), history_path=str(history_path))
    return start + datetime.timedelta(days=n_days)  # 下一個可用日期


# --- 單片價換算 ---------------------------------------------------------------

def test_unit_price_is_pack_price_divided_by_piece_count(tmp_path):
    manual = tmp_path / "manual.csv"
    _write_manual_csv(manual, [("2026-07-01", BRANDS[0], "蝦皮", 960, 64)])
    df = pipeline.load_manual_prices(str(manual))
    assert df.iloc[0]["unit_price"] == pytest.approx(15.0)


def test_rows_with_zero_piece_count_are_dropped_not_divided_by_zero(tmp_path):
    manual = tmp_path / "manual.csv"
    _write_manual_csv(manual, [
        ("2026-07-01", BRANDS[0], "蝦皮", 960, 0),
        ("2026-07-01", BRANDS[0], "momo購物網", 960, 64),
    ])
    df = pipeline.load_manual_prices(str(manual))
    assert len(df) == 1
    assert df.iloc[0]["platform"] == "momo購物網"


def test_missing_required_column_raises(tmp_path):
    manual = tmp_path / "manual.csv"
    manual.write_text("date,brand,platform\n2026-07-01,x,y\n", encoding="utf-8")
    with pytest.raises(ValueError):
        pipeline.load_manual_prices(str(manual))


# --- 當日代表值：取最便宜，不是平均 -------------------------------------------

def test_daily_summary_uses_the_cheapest_offer_not_the_average(tmp_path):
    manual = tmp_path / "manual.csv"
    history = tmp_path / "history.csv"
    brand = BRANDS[0]
    _write_manual_csv(manual, [
        ("2026-07-01", brand, "蝦皮", 100 * 64, 64),   # 100/片
        ("2026-07-01", brand, "momo購物網", 80 * 64, 64),  # 80/片，最便宜
        ("2026-07-01", brand, "PChome24h購物", 120 * 64, 64),  # 120/片
    ])
    report = pipeline.build_report(as_of="2026-07-01", manual_path=str(manual), history_path=str(history))
    b = next(x for x in report["brands"] if x["brand"] == brand)
    assert b["cheapest_unit_price"] == pytest.approx(80.0)
    assert b["cheapest_platform"] == "momo購物網"
    assert b["avg_unit_price"] == pytest.approx(100.0)  # (100+80+120)/3
    assert b["offer_count"] == 3
    # 明細依單片價由低到高排序，第一筆就是最便宜的那筆
    assert b["offers"][0]["platform"] == "momo購物網"


def test_brand_with_no_offers_today_is_reported_as_missing_not_zero(tmp_path):
    manual = tmp_path / "manual.csv"
    history = tmp_path / "history.csv"
    _write_manual_csv(manual, [("2026-07-01", BRANDS[0], "蝦皮", 6400, 64)])
    report = pipeline.build_report(as_of="2026-07-01", manual_path=str(manual), history_path=str(history))
    missing = next(x for x in report["brands"] if x["brand"] == BRANDS[1])
    assert missing["has_data"] is False
    assert missing["cheapest_unit_price"] is None
    assert missing["significant_drop"] is False


# --- 近期平均與顯著下跌判定 ----------------------------------------------------

def test_baseline_not_judged_when_history_has_too_few_points(tmp_path):
    manual = tmp_path / "manual.csv"
    history = tmp_path / "history.csv"
    brand = BRANDS[0]
    next_day = _seed_history(manual, history, brand, daily_price=100, n_days=MIN_BASELINE_POINTS - 1)

    rows = [(d, brand, "蝦皮", 100 * 64, 64)
            for d in [(next_day - datetime.timedelta(days=i)).isoformat()
                      for i in range(1, MIN_BASELINE_POINTS)]]
    rows.append((next_day.isoformat(), brand, "蝦皮", 50 * 64, 64))  # 大跌但基準不足
    _write_manual_csv(manual, rows)

    report = pipeline.build_report(as_of=next_day.isoformat(), manual_path=str(manual), history_path=str(history))
    b = next(x for x in report["brands"] if x["brand"] == brand)
    assert b["baseline_avg"] is None
    assert b["significant_drop"] is False


def test_a_twenty_percent_drop_is_flagged_significant(tmp_path):
    manual = tmp_path / "manual.csv"
    history = tmp_path / "history.csv"
    brand = BRANDS[0]
    next_day = _seed_history(manual, history, brand, daily_price=100, n_days=BASELINE_WINDOW_DAYS)

    _write_manual_csv(manual, [(next_day.isoformat(), brand, "蝦皮", 80 * 64, 64)])  # 100 -> 80，-20%
    report = pipeline.build_report(as_of=next_day.isoformat(), manual_path=str(manual), history_path=str(history))
    b = next(x for x in report["brands"] if x["brand"] == brand)
    assert b["baseline_avg"] == pytest.approx(100.0)
    assert b["pct_change_vs_baseline"] == pytest.approx(-20.0)
    assert b["significant_drop"] is True


def test_a_drop_just_under_the_threshold_is_not_flagged(tmp_path):
    manual = tmp_path / "manual.csv"
    history = tmp_path / "history.csv"
    brand = BRANDS[0]
    next_day = _seed_history(manual, history, brand, daily_price=100, n_days=BASELINE_WINDOW_DAYS)

    _write_manual_csv(manual, [(next_day.isoformat(), brand, "蝦皮", 81 * 64, 64)])  # -19%
    report = pipeline.build_report(as_of=next_day.isoformat(), manual_path=str(manual), history_path=str(history))
    b = next(x for x in report["brands"] if x["brand"] == brand)
    assert b["pct_change_vs_baseline"] == pytest.approx(-19.0)
    assert b["significant_drop"] is False


def test_a_price_rise_is_never_flagged_as_a_drop(tmp_path):
    manual = tmp_path / "manual.csv"
    history = tmp_path / "history.csv"
    brand = BRANDS[0]
    next_day = _seed_history(manual, history, brand, daily_price=100, n_days=BASELINE_WINDOW_DAYS)

    _write_manual_csv(manual, [(next_day.isoformat(), brand, "蝦皮", 150 * 64, 64)])  # +50%
    report = pipeline.build_report(as_of=next_day.isoformat(), manual_path=str(manual), history_path=str(history))
    b = next(x for x in report["brands"] if x["brand"] == brand)
    assert b["pct_change_vs_baseline"] == pytest.approx(50.0)
    assert b["significant_drop"] is False


def test_baseline_window_excludes_days_outside_the_window(tmp_path):
    """視窗外（超過 BASELINE_WINDOW_DAYS 天前）的舊資料不該拉低/推高基準——
    否則一個月前的價格會一直被當成「近期」，跌價後很久都還顯示顯著下跌。"""
    manual = tmp_path / "manual.csv"
    history = tmp_path / "history.csv"
    brand = BRANDS[0]

    # 視窗外：45 天前，價格很高（200），不該被算進基準
    far_start = datetime.date(2026, 5, 1)
    _seed_history(manual, history, brand, daily_price=200, n_days=1, start=far_start)

    # 視窗內：緊接著的 BASELINE_WINDOW_DAYS 天，價格穩定在 100
    near_start = far_start + datetime.timedelta(days=45)
    next_day = _seed_history(manual, history, brand, daily_price=100,
                              n_days=BASELINE_WINDOW_DAYS, start=near_start)

    _write_manual_csv(manual, [(next_day.isoformat(), brand, "蝦皮", 80 * 64, 64)])
    report = pipeline.build_report(as_of=next_day.isoformat(), manual_path=str(manual), history_path=str(history))
    b = next(x for x in report["brands"] if x["brand"] == brand)
    assert b["baseline_avg"] == pytest.approx(100.0)  # 不是被 200 拉高後的值


# --- 歷史資料落地 --------------------------------------------------------------

def test_rerunning_the_same_date_overwrites_history_instead_of_duplicating(tmp_path):
    manual = tmp_path / "manual.csv"
    history = tmp_path / "history.csv"
    brand = BRANDS[0]
    _write_manual_csv(manual, [("2026-07-01", brand, "蝦皮", 100 * 64, 64)])
    pipeline.build_report(as_of="2026-07-01", manual_path=str(manual), history_path=str(history))

    _write_manual_csv(manual, [("2026-07-01", brand, "蝦皮", 90 * 64, 64)])
    pipeline.build_report(as_of="2026-07-01", manual_path=str(manual), history_path=str(history))

    hist = pd.read_csv(history)
    same_day = hist[(hist["date"] == "2026-07-01") & (hist["brand"] == brand)]
    assert len(same_day) == 1
    assert same_day.iloc[0]["cheapest_unit_price"] == pytest.approx(90.0)
