#!/usr/bin/env python3
"""在 rebase 衝突時，以「記錄層級」合併爬取進度檔，而不是靠文字比對。

為什麼需要：爬一批要跑十幾分鐘到數小時，結果曾經因為 `git pull --rebase`
在 data/ledger.csv 與 data/results/*.jsonl 撞到衝突，整批成果被丟掉
（2026-08 實際發生，1500 檔的進度全部作廢）。

這些檔案都是**以 ticker 為鍵的記錄集合**，不是散文：
  - ledger.csv        一列一檔，鍵為 ticker
  - results/*.jsonl   一行一檔，鍵為 ticker

因此衝突有唯一正確的解法：取兩邊的聯集，同一個 ticker 取較新的那筆。
文字層級的 <<<< ==== >>>> 對這種檔案毫無意義，而放棄整批更是最糟的選擇。

用法（在 rebase 衝突當下執行）：
    python3 scripts/resolve_crawl_conflict.py

會處理所有處於衝突狀態的已知資料檔，寫回合併結果並 `git add`。
未知格式的檔案不碰，留給人工處理——猜錯比停下來更糟。
"""
from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from pathlib import Path


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def conflicted_files() -> list[str]:
    """列出目前處於衝突狀態的檔案。"""
    out = _git("diff", "--name-only", "--diff-filter=U")
    return [line.strip() for line in out.splitlines() if line.strip()]


def _stage(path: str, stage: int) -> str:
    """取出衝突檔案的某一方原始內容。

    直接讀工作區的檔案只會拿到夾著 <<<<<<< 標記的混合物；索引裡的
    stage 2／3 才是兩邊各自完整、乾淨的版本。
    """
    try:
        return _git("show", f":{stage}:{path}")
    except subprocess.CalledProcessError:
        return ""          # add/add 衝突時某一方可能不存在


def merge_jsonl(ours: str, theirs: str) -> str:
    """以 ticker 為鍵合併 JSONL 分片，同一檔取 as_of 較新的那筆。"""
    merged: dict[str, dict] = {}
    for text in (ours, theirs):
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue    # 毀損的單行不該讓整份合併失敗
            ticker = rec.get("ticker")
            if not ticker:
                continue
            existing = merged.get(ticker)
            if existing is None or str(rec.get("as_of") or "") >= str(existing.get("as_of") or ""):
                merged[ticker] = rec
    return "".join(
        json.dumps(merged[t], ensure_ascii=False) + "\n" for t in sorted(merged)
    )


def merge_ledger(ours: str, theirs: str) -> str:
    """以 ticker 為鍵合併帳本，同一檔取 last_attempt_at 較新的那筆。

    嘗試次數取兩邊較大值：兩邊都試過的話，實際消耗的次數是兩者之和的下界，
    取大值至少不會低估，也就不會讓一檔無限重試下去。
    """
    rows: dict[str, dict] = {}
    fields: list[str] = []
    for text in (ours, theirs):
        if not text.strip():
            continue
        reader = csv.DictReader(io.StringIO(text))
        fields = reader.fieldnames or fields
        for row in reader:
            ticker = (row.get("ticker") or "").strip()
            if not ticker:
                continue
            prev = rows.get(ticker)
            if prev is None:
                rows[ticker] = row
                continue
            newer = row if (row.get("last_attempt_at") or "") >= (prev.get("last_attempt_at") or "") else prev
            older = prev if newer is row else row
            try:
                newer["attempts"] = str(max(int(newer.get("attempts") or 0),
                                            int(older.get("attempts") or 0)))
            except ValueError:
                pass
            rows[ticker] = newer

    if not fields:
        return ours or theirs
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for ticker in sorted(rows):
        writer.writerow({k: rows[ticker].get(k, "") for k in fields})
    return buf.getvalue()


def resolve(path: str) -> bool:
    """合併單一衝突檔案。回傳是否已處理。"""
    ours, theirs = _stage(path, 2), _stage(path, 3)

    if path.endswith(".jsonl"):
        merged = merge_jsonl(ours, theirs)
    elif path.endswith("ledger.csv"):
        merged = merge_ledger(ours, theirs)
    else:
        return False       # 衍生檔（如 universe_latest.json）另行重算，不在這裡猜

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(merged, encoding="utf-8")
    subprocess.run(["git", "add", path], check=True)
    return True


def main() -> int:
    files = conflicted_files()
    if not files:
        print("沒有衝突檔案")
        return 0

    handled, skipped = [], []
    for path in files:
        (handled if resolve(path) else skipped).append(path)

    for path in handled:
        print(f"已以記錄層級合併：{path}")
    for path in skipped:
        print(f"未處理（需重新產生或人工處理）：{path}", file=sys.stderr)

    # 衍生檔沒有「合併」的意義，直接重算才是對的；交由呼叫端執行 publish
    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
