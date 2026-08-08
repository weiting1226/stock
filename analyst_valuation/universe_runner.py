"""全美股逐檔抓取的批次執行器：由帳本驅動、可續跑、逐筆保存。

流程：
1. 讀取公司清單（`universe.csv`），與帳本對齊（新上市加入、下市移除）
2. 從帳本挑出這次要做的標的（未處理的優先，其次是可重試的失敗項）
3. 價格用批次下載（一次 100 檔），目標價逐檔抓
4. 每一檔的結果**立刻寫入結果庫並更新帳本**，中途中斷也不會白做
5. 收尾輸出這一輪的統計與仍失敗的樣本

單次執行用 `--max-tickers` 限量，跑不完的部分留給下次執行接手。
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

from .config import MAX_WORKERS
from .ledger import Ledger
from .result_store import ResultStore
from .secrets_redaction import redact_secrets
from .sources import finnhub_targets, fmp_targets, prices
from .sources.yahoo_targets import fetch_yahoo_target
from .throttle import reset_all, tripped_circuits

log = logging.getLogger(__name__)

# 每處理這麼多檔就把帳本與結果落地一次。太小則 I/O 頻繁，太大則中斷時損失多。
CHECKPOINT_EVERY = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _fetch_one(ticker: str, use_finnhub: bool, use_fmp: bool) -> dict:
    """抓取單一標的的目標價（價格另由批次流程補上）。失敗時拋出，由呼叫端記錄。"""
    quotes = [fetch_yahoo_target(ticker)]
    if use_finnhub:
        quotes.append(finnhub_targets.fetch_finnhub_target(ticker))

    firm_records = []
    firm_error = None
    if use_fmp:
        records, firm_error = fmp_targets.fetch_fmp_firm_targets(ticker)
        firm_records = records

    usable = [q for q in quotes if q.ok]
    if not usable and not firm_records:
        # 所有來源都沒有資料：這是「查得到但沒有分析師覆蓋」還是「抓取失敗」？
        # 由各來源自己的 error 欄位判斷，避免把「無覆蓋」誤記成失敗而不斷重試。
        errors = [q.error for q in quotes if q.error]
        if all(e and "無此標的" in e for e in errors) or not errors:
            return {"ticker": ticker, "covered": False,
                    "note": "查詢成功但無分析師目標價覆蓋"}
        raise RuntimeError(redact_secrets("；".join(e for e in errors if e) or "未知錯誤"))

    primary = next((q for q in usable if q.source == "yahoo"), usable[0] if usable else None)
    return {
        "ticker": ticker,
        "covered": True,
        "quotes": [
            {"source": q.source, "mean": q.mean, "median": q.median,
             "high": q.high, "low": q.low, "analyst_count": q.analyst_count,
             "recommendation_key": q.recommendation_key}
            for q in usable
        ],
        "firm_targets": [
            {"firm": r.firm, "target": r.target, "source": r.source, "published": r.published}
            for r in firm_records
        ],
        "firm_source_error": redact_secrets(firm_error) if firm_error else None,
        "currency": primary.currency if primary else None,
    }


def run(
    universe_tickers: list[str],
    ledger_path: str,
    results_root: str,
    max_tickers: Optional[int] = None,
    max_workers: int = MAX_WORKERS,
    new_cycle: bool = False,
    keep_ok: bool = False,
    reset_dead: bool = False,
) -> dict:
    reset_all()
    ledger = Ledger(ledger_path).load()
    store = ResultStore(results_root)

    sync = ledger.sync_universe(universe_tickers)
    if new_cycle:
        reset_n = ledger.reset_for_new_cycle(keep_ok=keep_ok)
        log.info("開始新一輪：重置 %d 檔", reset_n)
    if reset_dead:
        log.info("放回重試佇列：%d 檔", ledger.reset_dead())
    ledger.save()

    work = ledger.select_work(limit=max_tickers)
    log.info("清單同步：%s；本次處理 %d 檔", sync, len(work))
    if not work:
        return {"processed": 0, "sync": sync, "summary": ledger.summary(),
                "disabled_sources": {}, "dead_sample": ledger.dead_entries()}

    # 價格用批次下載，比逐檔快得多
    price_map = prices.fetch_price_snapshots(work)

    use_finnhub = finnhub_targets.is_enabled()
    use_fmp = fmp_targets.is_enabled()
    log.info("Finnhub：%s／FMP：%s", "啟用" if use_finnhub else "停用",
             "啟用" if use_fmp else "停用")

    pending_records: list[dict] = []
    ok = failed = 0
    processed = 0

    def _flush() -> None:
        """把累積的結果與帳本一起落地——中途中斷也不會白做。"""
        if pending_records:
            store.write_many(pending_records)
            pending_records.clear()
        ledger.save()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, t, use_finnhub, use_fmp): t for t in work}
        for fut in as_completed(futures):
            ticker = futures[fut]
            snap = price_map.get(ticker)
            try:
                record = fut.result()
                record["as_of"] = _now()
                record["price"] = {
                    "close": snap.close if snap else None,
                    "close_date": snap.close_date if snap else None,
                    "change_1w_pct": snap.change_1w_pct if snap else None,
                    "change_1m_pct": snap.change_1m_pct if snap else None,
                    "error": snap.error if snap else "無價格資料",
                }
                pending_records.append(record)
                ledger.record_success(ticker)
                ok += 1
            except Exception as e:  # noqa: BLE001 — 單檔失敗不能中斷整批
                ledger.record_failure(ticker, redact_secrets(f"{type(e).__name__}: {e}"))
                failed += 1

            processed += 1
            if processed % CHECKPOINT_EVERY == 0:
                _flush()
                log.info("進度 %d/%d（成功 %d、失敗 %d）", processed, len(work), ok, failed)

    _flush()
    disabled = tripped_circuits()
    return {
        "processed": processed,
        "ok": ok,
        "failed": failed,
        "sync": sync,
        "summary": ledger.summary(),
        "stored_total": store.count(),
        "disabled_sources": disabled,
        "dead_sample": ledger.dead_entries(),
    }
