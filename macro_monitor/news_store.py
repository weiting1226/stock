"""新聞摘要的持久化儲存。

以 (指標, 參考期) 為鍵，原因有二：

1. **成功的要留著。** 摘要每呼叫一次模型就是一次成本，而同一期的數字不會變，
   重算只會得到同樣的結果。
2. **失敗的要能重試。** 如果只憑「發布日誌記錄過這一期」就跳過，那麼今天
   API 出錯的指標，明天也不會再試——那一期就永遠沒有摘要了。
   以「摘要存不存在」為判準，失敗自然會在下一次執行重試。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

NEWS_STORE_PATH = "data/macro/news_summaries.json"

# 保留幾期。同一指標的舊摘要留著沒有壞處，但檔案會無限成長。
KEEP_PER_SERIES = 6


def load(path: str = NEWS_STORE_PATH) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("新聞摘要儲存讀取失敗，視為空的：%s", e)
        return {}


def key(fred_id: str, reference_date: str) -> str:
    return f"{fred_id}@{reference_date}"


def missing(rows: list, store: dict) -> list:
    """哪些指標的**當期**摘要還沒有。

    不看發布日誌而看摘要本身：失敗的指標下一次執行才會重試。
    """
    out = []
    for r in rows:
        if not r.get("available") or not r.get("reference_date"):
            continue
        if key(r["fred_id"], r["reference_date"]) not in store:
            out.append(r["fred_id"])
    return out


def save(store: dict, new_items: dict, rows: list, path: str = NEWS_STORE_PATH) -> dict:
    """把新摘要併入儲存，並修剪每條序列的舊期別。"""
    by_id = {r.get("fred_id"): r for r in rows}
    for fred_id, summary in new_items.items():
        ref = (by_id.get(fred_id) or {}).get("reference_date")
        if not ref:
            continue
        store[key(fred_id, ref)] = {**summary, "reference_date": ref, "fred_id": fred_id}

    # 每條序列只留最近幾期
    grouped: dict[str, list] = {}
    for k, v in store.items():
        grouped.setdefault(v.get("fred_id", k.split("@")[0]), []).append((k, v))
    trimmed = {}
    for fred_id, items in grouped.items():
        items.sort(key=lambda kv: kv[1].get("reference_date", ""), reverse=True)
        for k, v in items[:KEEP_PER_SERIES]:
            trimmed[k] = v

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(trimmed, ensure_ascii=False, indent=1), encoding="utf-8")
    return trimmed


def attach(rows: list, store: dict) -> int:
    """把當期摘要掛到各列上。回傳掛上幾筆。"""
    n = 0
    for r in rows:
        if not r.get("available"):
            continue
        item = store.get(key(r["fred_id"], r.get("reference_date", "")))
        if item:
            r["news"] = {k: v for k, v in item.items()
                         if k not in ("fred_id", "reference_date")}
            n += 1
    return n
