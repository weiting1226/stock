"""模組二主流程：股票池 → 目標價（多來源、併發）+ 收盤價 → 彙整 → 報告。"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Optional

from .aggregate import ValuationRow, build_row, sector_summary
from .config import FIRM_TARGET_MAX_AGE_DAYS, MAX_WORKERS
from .firms import TargetRecord
from .secrets_redaction import redact_secrets
from .throttle import reset_all, tripped_circuits
from .sources import finnhub_targets, fmp_targets, prices, universe
from .sources.yahoo_targets import TargetQuote, fetch_yahoo_target

log = logging.getLogger(__name__)


def _fetch_all_targets_for(
    ticker: str, use_finnhub: bool, finnhub_key: Optional[str],
    use_fmp: bool = False, fmp_key: Optional[str] = None,
) -> tuple[list[TargetQuote], list[TargetRecord], Optional[str]]:
    """回傳 (共識層報價, 逐機構層記錄, 逐機構層錯誤訊息)。"""
    quotes = [fetch_yahoo_target(ticker)]
    if use_finnhub:
        quotes.append(finnhub_targets.fetch_finnhub_target(ticker, api_key=finnhub_key))

    firm_records: list[TargetRecord] = []
    firm_error: Optional[str] = None
    if use_fmp:
        records, firm_error = fmp_targets.fetch_fmp_firm_targets(ticker, api_key=fmp_key)
        fresh = _drop_stale(records)
        if records and not fresh:
            firm_error = f"取得 {len(records)} 筆機構目標價，但全部超過 {FIRM_TARGET_MAX_AGE_DAYS} 天"
        firm_records.extend(fresh)
    return quotes, firm_records, firm_error


def _drop_stale(records: list[TargetRecord]) -> list[TargetRecord]:
    """濾掉過舊的機構目標價：報告會隨基本面調整，半年前的估值沒有代表性。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=FIRM_TARGET_MAX_AGE_DAYS)).strftime("%Y-%m-%d")
    return [r for r in records if not r.published or r.published >= cutoff]


def run(
    as_of: Optional[str] = None,
    universe_cache_path: str = "docs/data/valuation/sp500_universe.json",
    limit: Optional[int] = None,
    max_workers: int = MAX_WORKERS,
    finnhub_key: Optional[str] = None,
    fmp_key: Optional[str] = None,
) -> dict:
    as_of = as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    constituents = universe.fetch_sp500_universe(cache_path=universe_cache_path)
    if limit:
        constituents = constituents[:limit]
    tickers = [c["ticker"] for c in constituents]
    log.info("股票池共 %d 檔", len(tickers))

    reset_all()   # 同一行程重複執行時，節流與熔斷狀態不應延續
    price_snapshots = prices.fetch_price_snapshots(tickers)

    use_finnhub = finnhub_targets.is_enabled(finnhub_key)
    use_fmp = fmp_targets.is_enabled(fmp_key)
    log.info("Finnhub 共識來源：%s", "啟用" if use_finnhub else "停用（未設金鑰）")
    log.info("FMP 逐機構來源：%s", "啟用" if use_fmp else "停用（未設金鑰）")

    quotes_by_ticker: dict[str, list[TargetQuote]] = {}
    firms_by_ticker: dict[str, list[TargetRecord]] = {}
    firm_errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_fetch_all_targets_for, t, use_finnhub, finnhub_key, use_fmp, fmp_key): t
            for t in tickers
        }
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                quotes_by_ticker[ticker], firms_by_ticker[ticker], err = fut.result()
                if err:
                    firm_errors[ticker] = err
            except Exception as e:  # noqa: BLE001
                msg = redact_secrets(f"{type(e).__name__}: {e}")
                log.warning("目標價抓取整體失敗 %s: %s", ticker, msg)
                quotes_by_ticker[ticker], firms_by_ticker[ticker] = [], []
                firm_errors[ticker] = msg

    disabled = tripped_circuits()
    for name, reason in disabled.items():
        log.warning("來源 %s 已於本次執行熔斷：%s", name, reason)
    if firm_errors and not disabled:
        # 逐機構來源啟用卻拿不到資料時，要能一眼看出原因，不能靜默退回共識模式
        sample = next(iter(firm_errors.values()))
        log.warning("逐機構來源有 %d 檔失敗，例：%s", len(firm_errors), sample)

    rows: list[ValuationRow] = []
    for meta in constituents:
        t = meta["ticker"]
        row = build_row(meta, price_snapshots.get(t), quotes_by_ticker.get(t, []),
                        firms_by_ticker.get(t, []))
        if t in firm_errors:
            row.notes.append(f"逐機構來源：{firm_errors[t]}")
        rows.append(row)

    with_upside = [r for r in rows if r.upside_pct is not None]
    sources_available = sorted({s for r in rows for s in r.sources_used})
    per_firm_rows = [r for r in rows if r.basis == "per_firm"]
    total_dupes = sum(r.duplicates_removed for r in rows)

    return {
        "as_of": as_of,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "universe": "S&P 500 (Wikipedia constituents)",
        "sources_available": sources_available,
        "finnhub_enabled": use_finnhub,
        "fmp_enabled": use_fmp,
        "firm_source_error_count": len(firm_errors),
        "firm_source_error_sample": next(iter(firm_errors.values()), None),
        # 因方案／授權限制被熔斷的來源：一次講清楚，不是每檔重複一遍
        "disabled_sources": disabled,
        "counts": {
            "universe": len(rows),
            "with_target_and_price": len(with_upside),
            "missing": len(rows) - len(with_upside),
            "per_firm_basis": len(per_firm_rows),
            "duplicate_firms_removed": total_dupes,
        },
        "coverage_note": (
            f"{len(with_upside)}/{len(rows)} 檔同時取得共識目標價與收盤價；"
            "其餘標記暫缺，未以任何方式填補。"
        ),
        "multi_source_note": (
            (f"逐機構模式：{len(per_firm_rows)} 檔的目標價由各分析機構的個別報告"
             f"依機構去重後自行平均（共合併 {total_dupes} 筆重複機構）。"
             if per_firm_rows else
             "共識模式：資料源僅提供彙總後的共識值，無法依機構去重，"
             "consensus_target 為各啟用來源之共識價平均。")
            + "　啟用來源："
            + "Yahoo Finance"
            + ("、Finnhub" if use_finnhub else "")
            + ("、FMP（逐機構）" if use_fmp else "")
            + ("。設定 FMP_API_KEY 可取得逐機構目標價並依機構去重自行計算平均。"
               if not use_fmp else "。")
            + ("".join(f"　⚠️ {n}：{r}" for n, r in disabled.items()) if disabled else "")
        ),
        "sector_summary": sector_summary(rows),
        "rows": [r.as_dict() for r in rows],
    }
