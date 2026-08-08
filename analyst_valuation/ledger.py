"""抓取進度帳本：記錄每一檔的狀態、嘗試次數與失敗原因，讓整批作業可續跑、可重試。

為什麼需要：全美股約 8000+ 檔，逐檔抓取無法在單次 GitHub Actions 執行中跑完
（有 6 小時上限，且外部來源會限流）。帳本讓每次執行只處理「還沒做完的部分」，
下一次接著跑；失敗的檔案會依重試次數上限自動排入下一輪，而不是被默默跳過。

格式刻意用 CSV 而非 SQLite：這個檔案要 commit 進 repo 才能跨執行保存進度，
文字格式的 diff 看得懂、衝突也能處理，二進位檔則否。

狀態：
  pending — 尚未嘗試
  ok      — 成功
  failed  — 失敗但仍可重試（attempts < max_attempts）
  dead    — 失敗且已達重試上限，不再自動重試（需人工處理或 --reset-dead）
"""
from __future__ import annotations

import csv
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

STATUS_PENDING = "pending"
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_DEAD = "dead"

DEFAULT_MAX_ATTEMPTS = 3

_FIELDS = ["ticker", "status", "attempts", "last_attempt_at", "last_ok_at", "last_error"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class LedgerEntry:
    ticker: str
    status: str = STATUS_PENDING
    attempts: int = 0
    last_attempt_at: str = ""
    last_ok_at: str = ""
    last_error: str = ""

    def as_row(self) -> dict:
        return asdict(self)


class Ledger:
    """以 ticker 為鍵的進度帳本。整份載入記憶體、原子性寫回。

    8000 筆規模下整份載入完全沒問題，換來的是簡單且不會有部分寫入的風險。
    """

    def __init__(self, path: str, max_attempts: int = DEFAULT_MAX_ATTEMPTS):
        self.path = Path(path)
        self.max_attempts = max_attempts
        self.entries: dict[str, LedgerEntry] = {}

    # --- 載入／儲存 -------------------------------------------------------

    def load(self) -> "Ledger":
        self.entries = {}
        if not self.path.exists():
            return self
        with self.path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                ticker = (row.get("ticker") or "").strip()
                if not ticker:
                    continue
                try:
                    attempts = int(row.get("attempts") or 0)
                except ValueError:
                    attempts = 0
                self.entries[ticker] = LedgerEntry(
                    ticker=ticker,
                    status=(row.get("status") or STATUS_PENDING).strip(),
                    attempts=attempts,
                    last_attempt_at=(row.get("last_attempt_at") or "").strip(),
                    last_ok_at=(row.get("last_ok_at") or "").strip(),
                    last_error=(row.get("last_error") or "").strip(),
                )
        return self

    def save(self) -> None:
        """先寫暫存檔再 rename：避免執行中途被中斷而留下半份帳本。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=_FIELDS)
                writer.writeheader()
                for ticker in sorted(self.entries):
                    writer.writerow(self.entries[ticker].as_row())
            os.replace(tmp, self.path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise

    # --- 維護 -------------------------------------------------------------

    def sync_universe(self, tickers: Iterable[str]) -> dict:
        """讓帳本與最新的公司清單對齊：新上市的加入、已下市的移除。"""
        wanted = {t for t in tickers if t}
        added = wanted - set(self.entries)
        removed = set(self.entries) - wanted
        for t in added:
            self.entries[t] = LedgerEntry(ticker=t)
        for t in removed:
            self.entries.pop(t, None)
        return {"added": len(added), "removed": len(removed), "total": len(self.entries)}

    def reset_for_new_cycle(self, keep_ok: bool = False) -> int:
        """開始新一輪抓取：把狀態歸零。

        `keep_ok=True` 時只重跑失敗的，適合「補跑」而非「重抓當日全量」。
        """
        n = 0
        for e in self.entries.values():
            if keep_ok and e.status == STATUS_OK:
                continue
            e.status = STATUS_PENDING
            e.attempts = 0
            e.last_error = ""
            n += 1
        return n

    def reset_dead(self) -> int:
        """把已達重試上限的項目放回佇列（人工確認問題已排除後使用）。"""
        n = 0
        for e in self.entries.values():
            if e.status == STATUS_DEAD:
                e.status = STATUS_PENDING
                e.attempts = 0
                e.last_error = ""
                n += 1
        return n

    # --- 取件與回報 -------------------------------------------------------

    def select_work(self, limit: Optional[int] = None) -> list[str]:
        """挑出這次要處理的標的：先做沒試過的，再做可重試的失敗項。

        失敗項依 attempts 由少到多排序，避免某幾檔一直卡在前面重試，
        讓其餘還沒試過的標的餓死。
        """
        pending = [e for e in self.entries.values() if e.status == STATUS_PENDING]
        retryable = [
            e for e in self.entries.values()
            if e.status == STATUS_FAILED and e.attempts < self.max_attempts
        ]
        pending.sort(key=lambda e: e.ticker)
        retryable.sort(key=lambda e: (e.attempts, e.ticker))
        work = [e.ticker for e in pending] + [e.ticker for e in retryable]
        return work[:limit] if limit else work

    def record_success(self, ticker: str) -> None:
        e = self.entries.setdefault(ticker, LedgerEntry(ticker=ticker))
        e.attempts += 1
        e.status = STATUS_OK
        e.last_attempt_at = e.last_ok_at = _now()
        e.last_error = ""

    def record_failure(self, ticker: str, error: str) -> None:
        e = self.entries.setdefault(ticker, LedgerEntry(ticker=ticker))
        e.attempts += 1
        e.last_attempt_at = _now()
        # 錯誤訊息壓成單行並截斷，否則整份 CSV 會被一則堆疊追蹤撐爆
        e.last_error = " ".join(str(error).split())[:300]
        e.status = STATUS_DEAD if e.attempts >= self.max_attempts else STATUS_FAILED

    # --- 統計 -------------------------------------------------------------

    def summary(self) -> dict:
        counts = {STATUS_PENDING: 0, STATUS_OK: 0, STATUS_FAILED: 0, STATUS_DEAD: 0}
        for e in self.entries.values():
            counts[e.status] = counts.get(e.status, 0) + 1
        counts["total"] = len(self.entries)
        return counts

    def dead_entries(self, limit: int = 20) -> list[dict]:
        """列出放棄重試的項目，供人工檢視原因。"""
        dead = [e for e in self.entries.values() if e.status == STATUS_DEAD]
        dead.sort(key=lambda e: e.ticker)
        return [{"ticker": e.ticker, "attempts": e.attempts, "error": e.last_error}
                for e in dead[:limit]]
