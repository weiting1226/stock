"""逐筆保存每一檔的查詢結果。

儲存方式：依代碼首字分片的 JSONL（`results/A.jsonl`、`results/B.jsonl`…），
每一行是一檔的完整結果，以 ticker 為鍵覆寫更新。

為什麼不是「一檔一個檔案」：8000+ 檔會產生 8000+ 個小檔，而價格每天都變，
等於每天 commit 八千個檔案變更，repo 會迅速膨脹到難以使用。分片 JSONL 同樣
是「一筆結果一筆記錄」、可逐筆讀寫，但檔案數收斂到約 40 個，git diff 也還看得懂。

寫入一律先寫暫存檔再 rename，避免執行被中斷時留下半份分片。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .secrets_redaction import redact_secrets


def shard_for(ticker: str) -> str:
    """代碼首字為分片名；非 A-Z 開頭者集中到 _OTHER。"""
    first = (ticker or "").strip().upper()[:1]
    return first if first.isalpha() else "_OTHER"


class ResultStore:
    def __init__(self, root: str):
        self.root = Path(root)

    def _shard_path(self, shard: str) -> Path:
        return self.root / f"{shard}.jsonl"

    # --- 讀取 -------------------------------------------------------------

    def load_shard(self, shard: str) -> dict[str, dict]:
        path = self._shard_path(shard)
        if not path.exists():
            return {}
        out: dict[str, dict] = {}
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue          # 跳過毀損的單行，不讓整個分片讀不出來
                ticker = rec.get("ticker")
                if ticker:
                    out[ticker] = rec
        return out

    def iter_all(self) -> Iterator[dict]:
        if not self.root.exists():
            return
        for path in sorted(self.root.glob("*.jsonl")):
            for rec in self.load_shard(path.stem).values():
                yield rec

    def load_all(self) -> dict[str, dict]:
        return {r["ticker"]: r for r in self.iter_all() if r.get("ticker")}

    def get(self, ticker: str) -> Optional[dict]:
        return self.load_shard(shard_for(ticker)).get(ticker)

    # --- 寫入 -------------------------------------------------------------

    def write_many(self, records: Iterable[dict]) -> int:
        """以 ticker 為鍵覆寫更新；同一分片只重寫一次。"""
        grouped: dict[str, list[dict]] = {}
        for rec in records:
            ticker = rec.get("ticker")
            if not ticker:
                continue
            grouped.setdefault(shard_for(ticker), []).append(rec)

        written = 0
        self.root.mkdir(parents=True, exist_ok=True)
        for shard, recs in grouped.items():
            existing = self.load_shard(shard)
            for rec in recs:
                existing[rec["ticker"]] = rec
            self._write_shard(shard, existing)
            written += len(recs)
        return written

    def _write_shard(self, shard: str, records: dict[str, dict]) -> None:
        path = self._shard_path(shard)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for ticker in sorted(records):
                    # 落地前再遮蔽一次憑證，比照 storage.save_report
                    line = redact_secrets(json.dumps(records[ticker], ensure_ascii=False, default=str))
                    fh.write(line + "\n")
            os.replace(tmp, path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise

    def count(self) -> int:
        return sum(1 for _ in self.iter_all())
