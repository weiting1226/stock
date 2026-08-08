"""端對端整合測試：用 monkeypatch 替換掉所有網路呼叫，驗證
pipeline.run() 在合成資料下能跑完全程、不拋例外，且回傳結構正確。
不驗證資料抓取邏輯本身是否能連上真實網站（沙盒無網路存取，見 README）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from liquidity_monitor import pipeline
from liquidity_monitor.sources import fomc as fomc_module


def _mock_fred_series(series_id, start, end):
    if series_id == "GDP":
        idx = pd.date_range(start, end, freq="QS")
        if len(idx) == 0:
            idx = pd.DatetimeIndex([pd.Timestamp(start)])
        values = np.linspace(27000, 29500, len(idx))
        return pd.Series(values, index=idx, name=series_id)

    idx = pd.bdate_range(start, end)
    if len(idx) == 0:
        idx = pd.DatetimeIndex([pd.Timestamp(start)])
    n = len(idx)
    generators = {
        "WALCL": 8_000_000 + np.linspace(0, 500_000, n),
        "RRPONTSYD": 2e11 + np.sin(np.linspace(0, 10, n)) * 5e9,
        "M2SL": 20000 + np.linspace(0, 1000, n),
        "BAMLH0A0HYM2": 3.5 + np.sin(np.linspace(0, 5, n)) * 0.4,
        "DGS2": 4.5 + np.linspace(0, -0.3, n),
        "DGS10": 4.2 + np.linspace(0, -0.1, n),
        "DGS30": 4.5 + np.linspace(0, 0.2, n),
        "SOFR": np.full(n, 5.33),
        "IORB": np.full(n, 5.40),
        "DTWEXBGS": 120 + np.linspace(0, -2, n),
        "DFEDTARU": np.where(np.arange(n) < n // 2, 5.50, 5.25),
        "DFEDTARL": np.where(np.arange(n) < n // 2, 5.25, 5.00),
    }
    values = generators.get(series_id)
    if values is None:
        raise AssertionError(f"unexpected series_id in test mock: {series_id}")
    return pd.Series(values, index=idx, name=series_id)


def _mock_yahoo_close(ticker, start, end):
    idx = pd.bdate_range(start, end)
    if len(idx) == 0:
        idx = pd.DatetimeIndex([pd.Timestamp(start)])
    n = len(idx)
    generators = {
        "^VIX": 16 + np.sin(np.linspace(0, 20, n)) * 3,
        "^VIX3M": 18 + np.sin(np.linspace(0, 20, n)) * 2,
        "^SKEW": 125 + np.sin(np.linspace(0, 8, n)) * 8,
        "^MOVE": 95 + np.sin(np.linspace(0, 8, n)) * 15,
        "^NDX": np.linspace(15000, 20000, n) + np.sin(np.linspace(0, 30, n)) * 200,
        "DX-Y.NYB": 104 + np.linspace(0, -1, n),
        "JPY=X": 150 + np.linspace(0, 5, n),
        "^VIX9D": 15 + np.sin(np.linspace(0, 20, n)) * 3,
        "GC=F": 2000 + np.linspace(0, 100, n),
        "TLT": 95 + np.linspace(0, -3, n),
    }
    values = generators.get(ticker)
    if values is None:
        raise AssertionError(f"unexpected ticker in test mock: {ticker}")
    return pd.Series(values, index=idx, name=ticker)


def _mock_margin_debt(start, end, timeout=30, override_csv=None):
    idx = pd.date_range(start, end, freq="ME")
    if len(idx) == 0:
        idx = pd.DatetimeIndex([pd.Timestamp(end)])
    values = np.linspace(700e9, 820e9, len(idx))
    return pd.Series(values, index=idx, name="MARGIN_DEBT")


def _mock_fomc_meeting(as_of):
    dt = pd.Timestamp(as_of) - pd.Timedelta(days=10)
    return fomc_module.FomcMeeting(
        date=dt, statement_url="https://example.com/statement", statement_text="voting for the action",
        has_dissent=False,
    )


@pytest.fixture(autouse=True)
def _patch_sources(monkeypatch, tmp_path):
    from liquidity_monitor.sources import fred, yahoo, finra_margin, ndx_breadth, fomc

    monkeypatch.setattr(fred, "fetch_fred_series", _mock_fred_series)
    monkeypatch.setattr(yahoo, "fetch_yahoo_close", _mock_yahoo_close)
    monkeypatch.setattr(finra_margin, "fetch_margin_debt", _mock_margin_debt)
    monkeypatch.setattr(ndx_breadth, "fetch_constituents", lambda cache_path=None, **kw: ["AAA", "BBB"])
    monkeypatch.setattr(ndx_breadth, "compute_breadth_200d", lambda tickers, as_of: 62.5)
    monkeypatch.setattr(fomc, "latest_meeting_on_or_before", _mock_fomc_meeting)
    monkeypatch.setattr(pipeline, "HISTORY_START", "2024-01-01")

    overrides = tmp_path / "manual_overrides.json"
    overrides.write_text("{}")
    yield {"overrides_path": str(overrides), "ndx_cache_path": str(tmp_path / "ndx.json")}


def test_pipeline_runs_end_to_end(_patch_sources):
    report = pipeline.run(
        as_of="2026-08-05",
        overrides_path=_patch_sources["overrides_path"],
        ndx_cache_path=_patch_sources["ndx_cache_path"],
    )

    assert report["as_of"] == "2026-08-05"
    assert isinstance(report["composite_score"], float)
    assert report["light"] in {"🟢🟢 深綠", "🟢 綠", "🟡 黃", "🔴 紅", "🔴🔴 深紅"}
    assert len(report["items"]) == 18
    assert report["position"]["final"]
    assert "gate_a" in report and "gate_b" in report and "gate_c" in report
    # 沒填 manual override，這兩項應標記暫缺，不得被臆測填補
    assert report["items"]["etf_fund_flow"]["score"] is None
    assert report["items"]["fedwatch_path"]["score"] is None


def test_pipeline_end_to_end_storage_roundtrip(_patch_sources, tmp_path):
    from liquidity_monitor import storage

    report = pipeline.run(
        as_of="2026-08-05",
        overrides_path=_patch_sources["overrides_path"],
        ndx_cache_path=_patch_sources["ndx_cache_path"],
    )
    data_root = tmp_path / "docs_data"
    storage.save_report(report, data_root=str(data_root))

    assert (data_root / "latest.json").exists()
    assert (data_root / "scores_history.csv").exists()
    assert (data_root / "raw_indicators.csv").exists()

    df = pd.read_csv(data_root / "scores_history.csv")
    assert len(df) == 1
    assert df.iloc[0]["as_of"] == "2026-08-05"


# --- FOMC 人工填入退回機制 -------------------------------------------------

def test_fomc_falls_back_to_manual_override_when_scrape_fails(_patch_sources, monkeypatch, tmp_path):
    """GitHub Actions 連不到 federalreserve.gov，需能退回人工填入的分數。"""
    import json as _json
    from liquidity_monitor.sources import fomc as fomc_src

    monkeypatch.setattr(fomc_src, "latest_meeting_on_or_before",
                        lambda as_of: (_ for _ in ()).throw(ValueError("網站連不到")))
    overrides = tmp_path / "ov.json"
    overrides.write_text(_json.dumps({
        "fomc_decision": {"score": 2, "as_of": "2026-07-29", "note": "降息一碼無異議"}
    }))

    report = pipeline.run(as_of="2026-08-05", overrides_path=str(overrides),
                          ndx_cache_path=str(tmp_path / "ndx.json"))
    item = report["items"]["fomc_decision"]
    assert item["score"] == 2
    assert item["confidence"] == "低"
    assert "自動抓取失敗" in item["note"]


def test_stale_manual_override_is_not_scored(_patch_sources, monkeypatch, tmp_path):
    """人工資料過期就不採計，避免拿陳舊資料當今日訊號。"""
    import json as _json
    from liquidity_monitor.sources import fomc as fomc_src

    monkeypatch.setattr(fomc_src, "latest_meeting_on_or_before",
                        lambda as_of: (_ for _ in ()).throw(ValueError("網站連不到")))
    overrides = tmp_path / "ov2.json"
    overrides.write_text(_json.dumps({
        "fomc_decision": {"score": 2, "as_of": "2026-01-01", "note": "很久以前"}
    }))

    report = pipeline.run(as_of="2026-08-05", overrides_path=str(overrides),
                          ndx_cache_path=str(tmp_path / "ndx2.json"))
    item = report["items"]["fomc_decision"]
    assert item["score"] is None
    assert "逾60天" in item["confidence"]
