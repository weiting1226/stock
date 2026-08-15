"""模組七（類股 ETF 趨勢）的測試。

這個模組沒有狀態機、沒有門檻，出錯的方式主要是**安靜地算錯一個數字**：
用錯價格序列、均線穿越判定差一天、廣度隨取樣而變。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sector_trend import config, metrics


def _series(values, start="2021-01-04"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


# --- 除息調整：這個模組與模組四分開存在的主要理由 ---------------------------

def test_the_fetcher_asks_for_dividend_adjusted_prices():
    """公用事業殖利率約 3%、資訊科技不到 1%。用未調整價比較一年報酬，
    等於系統性低估防禦類股約兩個百分點——而畫面上完全看不出來，
    只會得出「防禦類股長期就是比較弱」這種被股利吃掉的結論。"""
    from unittest import mock

    captured = {}

    def fake_download(tickers, **kw):
        captured.update(kw)
        idx = pd.bdate_range("2024-01-01", periods=30)
        cols = pd.MultiIndex.from_product([["XLK"], ["Close"]])
        return pd.DataFrame(np.linspace(100, 110, 30).reshape(-1, 1),
                            index=idx, columns=cols)

    with mock.patch.object(metrics.yf, "download", side_effect=fake_download):
        metrics.fetch_adjusted(["XLK"], "2024-01-01", "2024-02-01")
    assert captured.get("auto_adjust") is True


def test_a_three_percent_yield_changes_the_one_year_ranking():
    """具體演一次為什麼這件事要緊：兩檔價格漲幅相同、股利差 3%，
    未調整價會說它們一樣強。"""
    n = 300
    tech_price = _series(np.linspace(100, 112, n))          # 價格 +12%，幾乎不配息
    util_total = _series(np.linspace(100, 115, n))          # 總報酬 +15%（含 3% 股利）
    tech = metrics.build_row("XLK", tech_price)
    util = metrics.build_row("XLU", util_total)
    assert util["returns"]["1y"] > tech["returns"]["1y"]


# --- 均線與穿越 -------------------------------------------------------------

def test_price_above_and_below_the_moving_averages_is_reported():
    up = _series(np.linspace(50, 150, 400))
    st = metrics.moving_average_state(up)
    assert st["above_ma50"] is True and st["above_ma200"] is True
    down = _series(np.linspace(150, 50, 400))
    st2 = metrics.moving_average_state(down)
    assert st2["above_ma50"] is False and st2["above_ma200"] is False


def test_days_since_cross_is_reported_not_just_the_current_side():
    """「剛穿越」與「站上兩年」在畫面上長得一樣，但意義完全不同。"""
    n = 500
    values = np.concatenate([np.linspace(150, 60, 300), np.linspace(60, 200, n - 300)])
    st = metrics.moving_average_state(_series(values))
    assert st["golden_cross"] is True
    assert isinstance(st["days_since_cross"], int) and st["days_since_cross"] > 0


def test_a_short_series_reports_none_rather_than_a_wrong_average():
    """不足 200 天就算 200 日均線，算出來的是「歷史還不夠長」，不是趨勢。"""
    st = metrics.moving_average_state(_series(np.linspace(100, 110, 60)))
    assert st["ma200"] is None and st["above_ma200"] is None
    assert st["ma50"] is not None


# --- 回撤與 52 週高低 -------------------------------------------------------

def test_distance_from_high_and_low_are_both_reported():
    """離高點 −18% 可能是「剛開始跌」，也可能是「從谷底漲了 40% 但還沒回前高」。
    只報其中一個看不出差別。"""
    values = np.concatenate([np.linspace(100, 140, 120), np.linspace(140, 115, 132)])
    d = metrics.drawdown_stats(_series(values))
    assert d["from_high_pct"] < 0 and d["from_low_pct"] > 0


# --- 廣度 -------------------------------------------------------------------

def test_breadth_uses_a_fixed_set_so_it_does_not_move_with_sampling():
    """把所有 ETF 都算進去的話，「11 檔裡有幾檔」會隨我今天列了幾檔科技 ETF
    而變動——那不是廣度，是取樣。"""
    rows = [{"ticker": "XLK", "above_ma200": True, "above_ma50": True},
            {"ticker": "VGT", "above_ma200": True, "above_ma50": True},
            {"ticker": "IYW", "above_ma200": True, "above_ma50": True},
            {"ticker": "XLU", "above_ma200": False, "above_ma50": False}]
    bd = metrics.breadth(rows, ["XLK", "XLU"])
    assert bd["total"] == 2 and bd["above_ma200"] == 1


def test_breadth_skips_tickers_without_enough_history():
    rows = [{"ticker": "XLK", "above_ma200": True},
            {"ticker": "XLC", "above_ma200": None}]
    bd = metrics.breadth(rows, ["XLK", "XLC"])
    assert bd["total"] == 1


# --- 同類股不同 ETF 的離散度 ------------------------------------------------

def _broad(v):
    return {"kind": "broad", "returns": {"1y": v}}


def test_provider_dispersion_shows_that_the_choice_of_etf_matters():
    """這是本模組相對模組四多出來的那件事：「科技類股今年漲多少」
    其實取決於你拿哪一檔。"""
    fam = [_broad(22.0), _broad(31.5), _broad(18.0)]
    assert metrics.provider_dispersion(fam, "1y") == pytest.approx(13.5)


def test_sub_industry_etfs_are_excluded_from_provider_dispersion():
    """**第一版把這兩件事混在一起量了。** 實測醫療保健 50.7pp，看起來像
    「選哪一檔差很多」——但差距全部來自 XBI（生技）。拆開之後只有 1.55pp。
    沒有人會拿 XBI 代表醫療保健類股。"""
    fam = [_broad(30.59), _broad(32.14),
           {"kind": "industry", "returns": {"1y": 81.29}}]     # XBI 實測值
    assert metrics.provider_dispersion(fam, "1y") == pytest.approx(1.55)


def test_dispersion_needs_at_least_two_broad_etfs():
    assert metrics.provider_dispersion([_broad(10.0)], "1y") is None
    # 一檔全類股 + 一檔次產業，仍然算不出「發行商之間」的差距
    assert metrics.provider_dispersion(
        [_broad(10.0), {"kind": "industry", "returns": {"1y": 50.0}}], "1y") is None


def test_dispersion_ignores_missing_values_rather_than_treating_them_as_zero():
    """把缺值當成 0 會憑空造出一個巨大的離散度。"""
    fam = [_broad(20.0), _broad(None), _broad(24.0)]
    assert metrics.provider_dispersion(fam, "1y") == pytest.approx(4.0)


def test_every_etf_is_labelled_broad_or_industry():
    """沒標到的會被當成 industry 而從離散度裡消失——那是靜默的漏算。"""
    for sector, etfs in config.SECTOR_FAMILIES.items():
        for t, _, _, kind in etfs:
            assert kind in ("broad", "industry"), f"{sector} {t}"
        assert sum(1 for *_, k in etfs if k == "broad") >= 2, f"{sector} 全類股 ETF 不足兩檔"


# --- 設定的一致性 -----------------------------------------------------------

def test_every_sector_has_a_primary_etf_that_is_in_its_family():
    """廣度用固定的代表 ETF。代表若不在該類股清單裡，廣度就會算到別的東西。"""
    for sector, etfs in config.SECTOR_FAMILIES.items():
        tickers = [t for t, _, _, _ in etfs]
        assert config.PRIMARY[sector] in tickers, sector


def test_the_primary_etfs_match_module_four_so_the_two_pages_agree():
    """兩頁若各用不同 ETF 代表同一個類股，同一天會顯示不同的漲跌，
    而讀者沒有辦法知道是哪一邊的問題。"""
    from sector_rotation.config import SECTOR_ETFS
    module_four = set(SECTOR_ETFS)
    for sector, ticker in config.PRIMARY.items():
        assert ticker in module_four, f"{sector} 的代表 {ticker} 不在模組四的清單裡"


def test_ticker_list_has_no_duplicates():
    t = config.all_tickers()
    assert len(t) == len(set(t))
    assert config.BENCHMARK in t


def test_return_windows_are_longer_than_module_fours():
    """兩個模組的分工：模組四看 1 日～3 月，本模組看 1 月～3 年。
    完全重疊的話，第七頁就沒有存在的必要。"""
    from sector_rotation.config import WINDOWS as M4
    assert max(config.RETURN_WINDOWS.values()) > max(M4.values()) * 3


# --- 圖表 -------------------------------------------------------------------

def test_rebased_series_starts_at_one_hundred():
    r = metrics.rebased_series(_series(np.linspace(80, 160, 300)), 260)
    assert r["values"][0] == pytest.approx(100.0)
    assert len(r["dates"]) == len(r["values"])


def test_rebased_series_handles_a_short_history():
    r = metrics.rebased_series(_series([100.0, 101.0]), 260)
    assert len(r["values"]) >= 1


def test_every_dashboard_page_links_to_all_seven_modules():
    """新增模組時最容易漏掉的就是「其他六頁」。少一個連結，那一頁就成了死路。"""
    from pathlib import Path
    docs = Path(__file__).resolve().parents[1] / "docs"
    pages = ["index.html", "valuation.html", "backtest.html", "rotation.html",
             "macro.html", "tail.html", "trend.html"]
    for page in pages:
        html = (docs / page).read_text(encoding="utf-8")
        for target in pages:
            assert f'href="{target}"' in html, f"{page} 缺少往 {target} 的連結"
        assert 'class="active"' in html, f"{page} 沒有標出目前頁面"


def test_the_page_states_it_has_no_invented_score():
    """這個專案的原則之一：不把一堆指標壓成一個自創分數。
    寫在 README 不夠，畫面上要看得到。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root / "docs" / "trend.html").read_text(encoding="utf-8")
    assert "沒有自創的綜合分數" in html


def test_the_runner_script_can_print_a_real_report(tmp_path, capsys):
    """**這一則是補破網。** 把 dispersion_1y 更名為 provider_dispersion_1y 時，
    config／metrics／report／前端／測試全都改了，唯獨漏掉 run_sector_trend.py，
    於是排程連兩天 KeyError 失敗——而單元測試全綠，因為沒有任何一則走過
    執行腳本的列印路徑。

    這裡拿**真的由 report 產生**的字典餵給 main()，兩邊的欄位名稱若再度不一致
    就會當場失敗。"""
    import runpy
    import sys
    from unittest import mock

    from sector_trend import report as R

    rng = np.random.default_rng(5)
    idx = pd.bdate_range("2022-01-03", periods=900)
    tickers = config.all_tickers()
    wide = pd.DataFrame({t: pd.Series(100 * np.exp(np.cumsum(
        rng.normal(0.0003, 0.011, len(idx)))), index=idx) for t in tickers})

    with mock.patch.object(R.metrics, "fetch_adjusted", return_value=(wide, [])), \
         mock.patch.object(R, "HISTORY_PATH", str(tmp_path / "b.csv")), \
         mock.patch("sector_trend.report.HISTORY_PATH", str(tmp_path / "b.csv")):
        rep = R.build_report(as_of="2026-08-13")

    argv = sys.argv
    sys.argv = ["run_sector_trend.py", "--data-root", str(tmp_path)]
    try:
        with mock.patch("sector_trend.report.build_report", return_value=rep):
            with pytest.raises(SystemExit) as exc:
                runpy.run_path("scripts/run_sector_trend.py", run_name="__main__")
    finally:
        sys.argv = argv

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "廣度" in out and "站上 200 日線" in out
