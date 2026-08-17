"""股票池：S&P 500 / Nasdaq-100 成分股 + GICS 類股別
（供儀表板的「各類股篩選」使用）。

維基百科的 S&P 500 列表同時提供 Symbol / Security / GICS Sector /
GICS Sub-Industry，是免金鑰又穩定的來源。解析失敗時直接拋出錯誤，
不用過期或臆測的清單頂替（沿用模組一的誠實原則）。

Nasdaq-100 改用 TradingView：維基百科的 Nasdaq-100 頁面自 2026-08 起已無法
解析出成分股表格（見 liquidity_monitor/config.py 的說明，模組一的 NDX 廣度
指標已因此改用 TradingView），沒必要在這裡再踩一次同一個坑，直接沿用
liquidity_monitor 已有的抓取。
"""
from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from ..config import SP500_WIKI_URL, UNIVERSE_CACHE_DAYS
from liquidity_monitor.sources.tradingview_ndx import fetch_ndx_components

log = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (stock-valuation-monitor scraper)"}


def _pick_column(df: pd.DataFrame, *candidates: str) -> Optional[str]:
    for col in df.columns:
        name = str(col).strip().lower()
        for cand in candidates:
            if cand in name:
                return col
    return None


def _parse_sp500_table(html: str) -> list[dict]:
    tables = pd.read_html(io.StringIO(html))  # pandas>=2.1 需要 file-like，不可傳純字串
    for t in tables:
        sym_col = _pick_column(t, "symbol", "ticker")
        sector_col = _pick_column(t, "gics sector", "sector")
        if sym_col is None or sector_col is None:
            continue
        name_col = _pick_column(t, "security", "company")
        sub_col = _pick_column(t, "sub-industry", "sub industry")

        rows = []
        for _, r in t.iterrows():
            symbol = str(r[sym_col]).strip().upper()
            if not symbol or symbol.lower() == "nan":
                continue
            rows.append({
                # 維基百科用 BRK.B，Yahoo Finance 用 BRK-B
                "ticker": symbol.replace(".", "-"),
                "name": str(r[name_col]).strip() if name_col else symbol,
                "sector": str(r[sector_col]).strip() if sector_col else "Unknown",
                "sub_industry": str(r[sub_col]).strip() if sub_col else "",
            })
        if rows:
            return rows
    raise ValueError(
        "無法從維基百科 S&P 500 頁面解析出含 Symbol 與 GICS Sector 的表格；"
        "網站結構可能已變更，請人工檢查並更新 universe._parse_sp500_table。"
    )


def fetch_sp500_universe(cache_path: Optional[str] = None, timeout: int = 30) -> list[dict]:
    """回傳 [{ticker, name, sector, sub_industry}, ...]，優先使用未過期的本地快取。"""
    cache = Path(cache_path) if cache_path else None
    if cache and cache.exists():
        try:
            payload = json.loads(cache.read_text())
            as_of = pd.Timestamp(payload.get("as_of"))
            if (pd.Timestamp.today().normalize() - as_of).days < UNIVERSE_CACHE_DAYS:
                return payload["constituents"]
        except (ValueError, KeyError, TypeError):
            pass  # 快取毀損就重抓

    resp = requests.get(SP500_WIKI_URL, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    constituents = _parse_sp500_table(resp.text)

    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(
            {"as_of": str(pd.Timestamp.today().date()), "constituents": constituents},
            ensure_ascii=False, indent=2,
        ))
    return constituents


def fetch_ndx100_universe(
    cache_path: Optional[str] = None,
    sp500_cache_path: Optional[str] = None,
    timeout: int = 30,
) -> list[dict]:
    """回傳 [{ticker, name, sector, sub_industry}, ...]，優先使用未過期的本地快取。

    TradingView 的 screener 端點不回傳 GICS 類股，改用 S&P 500 清單的類股對照
    補上——NDX-100 與 S&P 500 成分股高度重疊。補值失敗（例如連不上 S&P 500 來源）
    或成分股本來就不在 S&P 500 裡，一律標為 Unknown，不臆測填補；這不影響
    成分股清單本身，只影響 UI 的類股篩選欄位。
    """
    cache = Path(cache_path) if cache_path else None
    if cache and cache.exists():
        try:
            payload = json.loads(cache.read_text())
            as_of = pd.Timestamp(payload.get("as_of"))
            if (pd.Timestamp.today().normalize() - as_of).days < UNIVERSE_CACHE_DAYS:
                return payload["constituents"]
        except (ValueError, KeyError, TypeError):
            pass  # 快取毀損就重抓

    components = fetch_ndx_components(timeout=timeout)

    sp500_by_ticker: dict[str, dict] = {}
    try:
        sp500 = fetch_sp500_universe(cache_path=sp500_cache_path, timeout=timeout)
        sp500_by_ticker = {c["ticker"]: c for c in sp500}
    except Exception as e:  # noqa: BLE001 — 類股只是加值資訊，不該讓整份成分股清單失敗
        log.warning("補 Nasdaq-100 類股時無法取得 S&P 500 對照：%s: %s", type(e).__name__, e)

    constituents = []
    for c in components:
        sp = sp500_by_ticker.get(c.symbol)
        constituents.append({
            "ticker": c.symbol,
            "name": (sp["name"] if sp else None) or c.name or c.symbol,
            "sector": sp["sector"] if sp else "Unknown",
            "sub_industry": sp["sub_industry"] if sp else "",
        })

    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(
            {"as_of": str(pd.Timestamp.today().date()), "constituents": constituents},
            ensure_ascii=False, indent=2,
        ))
    return constituents
