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


def test_brand_with_no_offers_ever_is_reported_as_missing_not_zero(tmp_path):
    manual = tmp_path / "manual.csv"
    history = tmp_path / "history.csv"
    _write_manual_csv(manual, [("2026-07-01", BRANDS[0], "蝦皮", 6400, 64)])
    report = pipeline.build_report(as_of="2026-07-01", manual_path=str(manual), history_path=str(history))
    missing = next(x for x in report["brands"] if x["brand"] == BRANDS[1])
    assert missing["has_data"] is False
    assert missing["data_date"] is None
    assert missing["is_stale"] is False


# --- 沒填今日資料時退回最近一筆 --------------------------------------------

def test_falls_back_to_the_most_recent_data_when_today_is_missing(tmp_path):
    manual = tmp_path / "manual.csv"
    history = tmp_path / "history.csv"
    brand = BRANDS[0]
    # 只有 07-01 有資料，07-03 完全沒填（忘記查價）
    _write_manual_csv(manual, [("2026-07-01", brand, "蝦皮", 100 * 64, 64)])
    pipeline.build_report(as_of="2026-07-01", manual_path=str(manual), history_path=str(history))

    report = pipeline.build_report(as_of="2026-07-03", manual_path=str(manual), history_path=str(history))
    b = next(x for x in report["brands"] if x["brand"] == brand)
    assert b["has_data"] is True
    assert b["is_stale"] is True
    assert b["data_date"] == "2026-07-01"
    assert b["cheapest_unit_price"] == pytest.approx(100.0)
    assert b["cheapest_platform"] == "蝦皮"


def test_stale_fallback_does_not_pollute_history(tmp_path):
    """墊檔用的舊資料不能被當成「今天」寫進歷史，否則同一個價格會重複計入
    近期平均，變相拉高「顯著下跌」的判定門檻。"""
    manual = tmp_path / "manual.csv"
    history = tmp_path / "history.csv"
    brand = BRANDS[0]
    _write_manual_csv(manual, [("2026-07-01", brand, "蝦皮", 100 * 64, 64)])
    pipeline.build_report(as_of="2026-07-01", manual_path=str(manual), history_path=str(history))

    pipeline.build_report(as_of="2026-07-03", manual_path=str(manual), history_path=str(history))
    pipeline.build_report(as_of="2026-07-05", manual_path=str(manual), history_path=str(history))

    hist = pd.read_csv(history)
    brand_hist = hist[hist["brand"] == brand]
    assert len(brand_hist) == 1
    assert list(brand_hist["date"]) == ["2026-07-01"]


def test_baseline_for_stale_data_is_relative_to_its_own_date_not_as_of(tmp_path):
    manual = tmp_path / "manual.csv"
    history = tmp_path / "history.csv"
    brand = BRANDS[0]
    # 建立 14 天穩定在 100 的歷史，最後一天（第 15 天）大跌到 80，之後就沒再填資料
    last_data_day = _seed_history(manual, history, brand, daily_price=100, n_days=BASELINE_WINDOW_DAYS)
    _write_manual_csv(manual, [(last_data_day.isoformat(), brand, "蝦皮", 80 * 64, 64)])
    pipeline.build_report(as_of=last_data_day.isoformat(), manual_path=str(manual), history_path=str(history))

    much_later = last_data_day + datetime.timedelta(days=10)
    report = pipeline.build_report(as_of=much_later.isoformat(), manual_path=str(manual), history_path=str(history))
    b = next(x for x in report["brands"] if x["brand"] == brand)
    assert b["is_stale"] is True
    assert b["data_date"] == last_data_day.isoformat()
    assert b["baseline_avg"] == pytest.approx(100.0)
    assert b["significant_drop"] is True


def test_fresh_data_today_is_used_instead_of_falling_back(tmp_path):
    manual = tmp_path / "manual.csv"
    history = tmp_path / "history.csv"
    brand = BRANDS[0]
    _write_manual_csv(manual, [
        ("2026-07-01", brand, "蝦皮", 100 * 64, 64),
        ("2026-07-02", brand, "蝦皮", 90 * 64, 64),
    ])
    pipeline.build_report(as_of="2026-07-01", manual_path=str(manual), history_path=str(history))
    report = pipeline.build_report(as_of="2026-07-02", manual_path=str(manual), history_path=str(history))
    b = next(x for x in report["brands"] if x["brand"] == brand)
    assert b["is_stale"] is False
    assert b["data_date"] == "2026-07-02"
    assert b["cheapest_unit_price"] == pytest.approx(90.0)


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


# --- 人工與自動爬蟲資料合併 ----------------------------------------------------

def test_scraped_data_is_ignored_when_scraped_path_is_not_given(tmp_path):
    """既有只傳 manual_path 的呼叫端（包含其餘所有測試）行為不能變——
    scraped_path 預設 None 就是完全不去看爬蟲那份資料。"""
    manual = tmp_path / "manual.csv"
    scraped = tmp_path / "scraped.csv"
    history = tmp_path / "history.csv"
    brand = BRANDS[0]
    _write_manual_csv(manual, [("2026-07-01", brand, "蝦皮", 100 * 64, 64)])
    _write_manual_csv(scraped, [("2026-07-01", brand, "momo購物網", 50 * 64, 64)])  # 應該被忽略

    report = pipeline.build_report(as_of="2026-07-01", manual_path=str(manual), history_path=str(history))
    b = next(x for x in report["brands"] if x["brand"] == brand)
    assert b["offer_count"] == 1
    assert b["cheapest_unit_price"] == pytest.approx(100.0)


def test_scraped_data_fills_gaps_when_scraped_path_is_given(tmp_path):
    manual = tmp_path / "manual.csv"
    scraped = tmp_path / "scraped.csv"
    history = tmp_path / "history.csv"
    brand = BRANDS[0]
    _write_manual_csv(manual, [("2026-07-01", brand, "蝦皮", 100 * 64, 64)])
    _write_manual_csv(scraped, [("2026-07-01", brand, "PChome24h購物", 80 * 64, 64)])

    report = pipeline.build_report(as_of="2026-07-01", manual_path=str(manual), history_path=str(history),
                                    scraped_path=str(scraped))
    b = next(x for x in report["brands"] if x["brand"] == brand)
    assert b["offer_count"] == 2
    assert b["cheapest_unit_price"] == pytest.approx(80.0)
    assert b["cheapest_platform"] == "PChome24h購物"
    sources = {o["platform"]: o["source"] for o in b["offers"]}
    assert sources["蝦皮"] == "人工"
    assert sources["PChome24h購物"] == "自動爬蟲"


def test_manual_data_wins_over_scraped_data_on_the_same_platform_and_day(tmp_path):
    """同一天、同一品牌、同一平台，人工填的資料要蓋過爬蟲抓到的——
    爬蟲補的是空檔，不是取代查證，兩邊都有資料時不該採信沒人核對過的那一筆。"""
    manual = tmp_path / "manual.csv"
    scraped = tmp_path / "scraped.csv"
    history = tmp_path / "history.csv"
    brand = BRANDS[0]
    _write_manual_csv(manual, [("2026-07-01", brand, "蝦皮", 100 * 64, 64)])
    # 爬蟲對同一天、同一品牌、同一平台抓到一個離譜的低價（例如抓錯商品）
    _write_manual_csv(scraped, [("2026-07-01", brand, "蝦皮", 1 * 64, 64)])

    report = pipeline.build_report(as_of="2026-07-01", manual_path=str(manual), history_path=str(history),
                                    scraped_path=str(scraped))
    b = next(x for x in report["brands"] if x["brand"] == brand)
    assert b["offer_count"] == 1
    assert b["cheapest_unit_price"] == pytest.approx(100.0)
    assert b["offers"][0]["source"] == "人工"


def test_load_all_prices_returns_manual_only_when_scraped_file_is_missing(tmp_path):
    manual = tmp_path / "manual.csv"
    brand = BRANDS[0]
    _write_manual_csv(manual, [("2026-07-01", brand, "蝦皮", 100 * 64, 64)])
    df = pipeline.load_all_prices(str(manual), str(tmp_path / "does_not_exist.csv"))
    assert len(df) == 1
    assert df.iloc[0]["source"] == "人工"


def test_rerunning_a_date_with_no_fresh_data_clears_its_stale_history_row(tmp_path):
    """真實案例（2026-08-18）：爬蟲第一次跑抓錯資料，寫進了 history.csv；
    修好過濾規則後重跑同一天，這次正確判定「沒有可信資料」——history.csv
    裡那筆已知錯誤的資料也要跟著消失，不能因為「這次批次是空的」就留著不管。"""
    manual = tmp_path / "manual.csv"
    scraped = tmp_path / "scraped.csv"
    history = tmp_path / "history.csv"
    brand = BRANDS[0]

    # 第一次執行：爬蟲抓到一筆（之後會被證實是錯的）資料
    _write_manual_csv(scraped, [("2026-08-18", brand, "PChome24h購物", 40 * 64, 64)])
    pipeline.build_report(as_of="2026-08-18", manual_path=str(manual), history_path=str(history),
                           scraped_path=str(scraped))
    hist = pd.read_csv(history, dtype={"date": str})
    assert len(hist[(hist["date"] == "2026-08-18") & (hist["brand"] == brand)]) == 1

    # 修好過濾規則後重跑同一天：這次爬蟲什麼都沒抓到（scraped.csv 清空）
    _write_manual_csv(scraped, [])
    pipeline.build_report(as_of="2026-08-18", manual_path=str(manual), history_path=str(history),
                           scraped_path=str(scraped))
    hist = pd.read_csv(history, dtype={"date": str})
    assert hist[(hist["date"] == "2026-08-18") & (hist["brand"] == brand)].empty, \
        f"上次的錯誤資料應該被清掉，但還在：{hist.to_dict('records')}"


# --- 斷料要被看見 -----------------------------------------------------------
#
# **這一段是補破網。** 2026-08-15 之後人工沒再填、三個自動爬蟲每天都回 0 筆，
# 儀表板連續十天顯示同一組 08-15 的價格。GitHub Actions 每天綠燈、每天 commit
# ——因為每天真的有東西變：latest.json 的 as_of 從昨天改成今天，其餘一字未動。
# 十天下來沒有任何一個地方叫過。
#
# 逐品牌的 is_stale 本來就有，但那是三個各自獨立的旗標，沒有任何地方把它們
# 加總成一句「這個模組現在是不是活的」。

def _brand(name, data_date, stale):
    return {"brand": name, "has_data": data_date is not None,
            "data_date": data_date, "is_stale": stale}


def test_all_brands_stale_is_reported_as_a_drought():
    health = pipeline.assess_data_health([
        _brand("甲", "2026-08-15", True),
        _brand("乙", "2026-08-15", True),
        _brand("丙", "2026-08-15", True),
    ], "2026-08-25")
    assert health["is_stale"] is True
    assert health["stale_days"] == 10
    assert health["latest_data_date"] == "2026-08-15"
    assert "2026-08-15" in health["message"]


def test_one_fresh_brand_means_the_pipeline_is_still_alive():
    """刻意看「最新的一筆」而不是「最舊的一筆」：只要還有一個品牌今天有資料，
    查價流程就還在運作。三個全舊了才是斷料——否則某個品牌長期缺報價
    會讓警告天天都在，然後被無視。"""
    health = pipeline.assess_data_health([
        _brand("甲", "2026-08-25", False),
        _brand("乙", "2026-08-15", True),
        _brand("丙", "2026-08-15", True),
    ], "2026-08-25")
    assert health["is_stale"] is False
    assert health["message"] is None
    assert health["fresh_brands"] == ["甲"]


def test_a_weekend_sized_gap_does_not_alert():
    """人工查價本來就可能週末跳過一兩天。天天紅燈的警告等於沒有警告。"""
    health = pipeline.assess_data_health(
        [_brand("甲", "2026-08-23", True)], "2026-08-25")
    assert health["stale_days"] == 2 and health["is_stale"] is False


def test_the_threshold_is_three_days_not_one():
    """門檻剛好落在 3 天：2 天不叫、3 天要叫。實際發生的斷料是十天，
    但把門檻放寬到「一週才算」就會重演一次同樣的沉默。"""
    assert pipeline.assess_data_health(
        [_brand("甲", "2026-08-22", True)], "2026-08-25")["is_stale"] is True


def test_no_price_records_at_all_is_also_a_drought():
    """完全沒有紀錄跟「有紀錄但很舊」都是斷料，不能因為算不出天數就當成正常。"""
    health = pipeline.assess_data_health([_brand("甲", None, False)], "2026-08-25")
    assert health["is_stale"] is True and health["stale_days"] is None


def test_the_report_carries_data_health_so_the_page_can_show_it(tmp_path):
    """後端算出來卻沒放進報表，等於前端拿不到——這一則守住那條線。"""
    manual = tmp_path / "m.csv"
    manual.write_text("date,brand,platform,product_name,pack_price,piece_count,url,note\n"
                      "2026-08-15,滿意寶寶日本境內版,蝦皮,測試 M 62片,509,62,,\n", encoding="utf-8")
    report = pipeline.build_report(as_of="2026-08-25", manual_path=str(manual),
                                    history_path=str(tmp_path / "h.csv"))
    assert report["data_health"]["is_stale"] is True
    assert report["data_health"]["stale_days"] == 10
