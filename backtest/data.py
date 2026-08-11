"""回測用歷史資料的抓取與快取。

價格用 yfinance、總經用 FRED、融資餘額用 FINRA，全部沿用模組一既有的
來源模組。抓到的原始序列會快取成 parquet／csv，讓重跑不必再打一次網路
（也讓「資料變了」與「程式改了」造成的差異能分開檢查）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from liquidity_monitor.config import FRED_SERIES, YAHOO_TICKERS
from liquidity_monitor.sources import finra_margin, fred, yahoo

from .config import CACHE_ROOT, CASH_PROXY, MAX_CASH_SPLICE_ANNUAL_DIFF_PCT, PRICE_TICKERS, SGOV

log = logging.getLogger(__name__)

# 回測不需要 ⑥ 專用的序列，但 DGS30 等仍可能用於圖表；一併抓不增加成本
BACKTEST_FRED_SERIES = tuple(FRED_SERIES)


def _cache_path(name: str) -> Path:
    p = Path(CACHE_ROOT) / f"{name}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _read_cache(name: str) -> Optional[pd.Series]:
    p = _cache_path(name)
    if not p.exists():
        return None
    df = pd.read_csv(p, index_col=0, parse_dates=True)
    return df.iloc[:, 0]


def _write_cache(name: str, s: pd.Series) -> None:
    if s is None or s.empty:
        return
    s.to_frame(name="value").to_csv(_cache_path(name))


def fetch_series(name: str, fetcher, use_cache: bool = True) -> pd.Series:
    """抓一條序列，失敗時回退到快取；兩者都沒有就回空序列。

    回空序列而不是拋出：單一條線缺了，其他指標仍算得出來，而缺哪一條
    會一路顯示在報告的「可用指標」清單上——比整個回測掛掉更有用。
    """
    if use_cache:
        cached = _read_cache(name)
        if cached is not None and not cached.empty:
            log.info("使用快取 %s（%d 筆）", name, len(cached))
            return cached
    try:
        s = fetcher()
        if s is not None and not s.empty:
            _write_cache(name, s)
            return s
        log.warning("%s 抓取結果為空", name)
    except Exception as e:  # noqa: BLE001 — 缺一條不能讓整個回測失敗
        log.warning("%s 抓取失敗：%s", name, e)
    cached = _read_cache(name)
    return cached if cached is not None else pd.Series(dtype=float)


def fetch_prices(start: str, end: str, use_cache: bool = True) -> pd.DataFrame:
    """QQQ／TQQQ／SGOV／BIL 的收盤價。"""
    cols = {}
    for ticker in PRICE_TICKERS:
        s = fetch_series(f"px_{ticker}", lambda t=ticker: yahoo.fetch_yahoo_close(t, start, end), use_cache)
        if not s.empty:
            cols[ticker] = s
    return pd.DataFrame(cols).sort_index()


def fetch_market_panel(start: str, end: str, use_cache: bool = True) -> dict[str, pd.Series]:
    """模組一用到的市場指數（VIX／VIX3M／MOVE／NDX／DXY／USDJPY／SKEW）。"""
    out = {}
    for ticker, name in YAHOO_TICKERS.items():
        out[name] = fetch_series(f"idx_{name}", lambda t=ticker: yahoo.fetch_yahoo_close(t, start, end), use_cache)
    return out


def fetch_fred_panel(start: str, end: str, use_cache: bool = True) -> dict[str, pd.Series]:
    return {
        sid: fetch_series(f"fred_{sid}", lambda s=sid: fred.fetch_fred_series(s, start, end), use_cache)
        for sid in BACKTEST_FRED_SERIES
    }


def fetch_margin_debt(start: str, end: str, use_cache: bool = True) -> pd.Series:
    return fetch_series("finra_margin", lambda: finra_margin.fetch_margin_debt(start, end), use_cache)


def splice_cash(prices: pd.DataFrame) -> tuple[pd.Series, dict]:
    """把 SGOV 與 BIL 接成一條完整的現金部位價格序列。

    SGOV 2020-05 才成立，涵蓋不到十五年，因此更早的期間改用 BIL
    （同為短天期美國國庫券 ETF，2007 年成立）。

    **銜接前先量重疊期間的差異**，而不是假設它們可以互換：兩檔的年化報酬
    若差得夠多，這條現金線就會系統性地偏移整個回測的結果，而且完全看不
    出來——現金部位的曲線本來就近乎直線，肉眼分辨不出 0.1% 還是 1%。
    差異超過門檻就直接讓回測失敗。

    回傳 (價格序列, 銜接說明)。
    """
    if SGOV not in prices.columns and CASH_PROXY not in prices.columns:
        raise ValueError("SGOV 與 BIL 都抓不到，現金部位無法回測")
    if SGOV not in prices.columns:
        return prices[CASH_PROXY].dropna(), {"basis": CASH_PROXY, "note": "SGOV 無資料，全期使用 BIL"}
    if CASH_PROXY not in prices.columns:
        s = prices[SGOV].dropna()
        return s, {"basis": SGOV, "note": f"BIL 無資料，回測起點受限於 SGOV 成立日 {s.index[0].date()}"}

    sgov, bil = prices[SGOV].dropna(), prices[CASH_PROXY].dropna()
    overlap = sgov.index.intersection(bil.index)
    info: dict = {"basis": f"{CASH_PROXY}→{SGOV}", "switch_date": str(sgov.index[0].date())}

    if len(overlap) > 252:
        years = (overlap[-1] - overlap[0]).days / 365.25
        sgov_cagr = (sgov.loc[overlap[-1]] / sgov.loc[overlap[0]]) ** (1 / years) - 1
        bil_cagr = (bil.loc[overlap[-1]] / bil.loc[overlap[0]]) ** (1 / years) - 1
        diff = abs(sgov_cagr - bil_cagr) * 100
        info.update({
            "overlap_days": len(overlap),
            "sgov_cagr_pct": round(sgov_cagr * 100, 3),
            "bil_cagr_pct": round(bil_cagr * 100, 3),
            "annual_diff_pct": round(diff, 3),
        })
        if diff > MAX_CASH_SPLICE_ANNUAL_DIFF_PCT:
            raise ValueError(
                f"SGOV 與 BIL 重疊期間的年化報酬差異 {diff:.2f}%／年，"
                f"超過可接受的 {MAX_CASH_SPLICE_ANNUAL_DIFF_PCT}%——兩者不可互換，"
                "不能用 BIL 代表 2020 年之前的現金部位"
            )
    else:
        info["note"] = f"重疊天數僅 {len(overlap)}，不足以驗證銜接品質"

    # 接的是**報酬率**不是價格水準：兩檔的絕對價位不同（SGOV 約 100、BIL 約 91），
    # 直接把兩段價格拼起來會在交界日產生一個憑空出現的跳空報酬。
    # 做法是保留 BIL 的每日報酬，只把水準平移到與 SGOV 起點相接。
    before = bil.pct_change().loc[bil.index < sgov.index[0]]
    if before.empty:
        return sgov, info
    factor = (1 + before).cumprod()
    early = sgov.iloc[0] * factor / factor.iloc[-1]
    spliced = pd.concat([early, sgov]).sort_index()
    return spliced, info
