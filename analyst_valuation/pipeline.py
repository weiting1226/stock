"""模組二主流程：股票池 → 目標價（多來源、併發）+ 收盤價 → 彙整 → 報告。"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

from .aggregate import ValuationRow, build_row, sector_summary
from .config import FINNHUB_SOURCE, MAX_WORKERS, YAHOO_SOURCE
from .sources import finnhub_targets, prices, universe
from .sources.yahoo_targets import TargetQuote, fetch_yahoo_target

log = logging.getLogger(__name__)


def _fetch_all_targets_for(ticker: str, use_finnhub: bool, finnhub_key: Optional[str]) -> list[TargetQuote]:
    quotes = [fetch_yahoo_target(ticker)]
    if use_finnhub:
        quotes.append(finnhub_targets.fetch_finnhub_target(ticker, api_key=finnhub_key))
    return quotes


def run(
    as_of: Optional[str] = None,
    universe_cache_path: str = "docs/data/valuation/sp500_universe.json",
    limit: Optional[int] = None,
    max_workers: int = MAX_WORKERS,
    finnhub_key: Optional[str] = None,
) -> dict:
    as_of = as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    constituents = universe.fetch_sp500_universe(cache_path=universe_cache_path)
    if limit:
        constituents = constituents[:limit]
    tickers = [c["ticker"] for c in constituents]
    log.info("股票池共 %d 檔", len(tickers))

    price_snapshots = prices.fetch_price_snapshots(tickers)

    use_finnhub = finnhub_targets.is_enabled(finnhub_key)
    log.info("Finnhub 來源：%s", "啟用" if use_finnhub else "停用（未設金鑰）")

    quotes_by_ticker: dict[str, list[TargetQuote]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_fetch_all_targets_for, t, use_finnhub, finnhub_key): t
            for t in tickers
        }
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                quotes_by_ticker[ticker] = fut.result()
            except Exception as e:  # noqa: BLE001
                log.warning("目標價抓取整體失敗 %s: %s", ticker, e)
                quotes_by_ticker[ticker] = []

    rows: list[ValuationRow] = [
        build_row(meta, price_snapshots.get(meta["ticker"]), quotes_by_ticker.get(meta["ticker"], []))
        for meta in constituents
    ]

    with_upside = [r for r in rows if r.upside_pct is not None]
    sources_available = sorted({s for r in rows for s in r.sources_used})

    return {
        "as_of": as_of,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "universe": "S&P 500 (Wikipedia constituents)",
        "sources_available": sources_available,
        "finnhub_enabled": use_finnhub,
        "counts": {
            "universe": len(rows),
            "with_target_and_price": len(with_upside),
            "missing": len(rows) - len(with_upside),
        },
        "coverage_note": (
            f"{len(with_upside)}/{len(rows)} 檔同時取得共識目標價與收盤價；"
            "其餘標記暫缺，未以任何方式填補。"
        ),
        "multi_source_note": (
            "consensus_target 為各啟用來源之共識目標價平均；"
            + ("目前啟用 Yahoo Finance 與 Finnhub 兩個獨立來源。"
               if use_finnhub else
               "目前僅啟用 Yahoo Finance 單一來源（設定 FINNHUB_API_KEY 可加入第二來源）。")
        ),
        "sector_summary": sector_summary(rows),
        "rows": [r.as_dict() for r in rows],
    }
