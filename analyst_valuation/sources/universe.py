"""股票池：S&P 500 成分股 + GICS 類股別（供儀表板的「各類股篩選」使用）。

維基百科的 S&P 500 列表同時提供 Symbol / Security / GICS Sector /
GICS Sub-Industry，是免金鑰又穩定的來源。解析失敗時直接拋出錯誤，
不用過期或臆測的清單頂替（沿用模組一的誠實原則）。
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from ..config import SP500_WIKI_URL, UNIVERSE_CACHE_DAYS

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
