"""跨日狀態的持久化。

接線說明第 3 節規則 2、第 7 節第 5 步都在講同一件事：**這個檔案漏寫，
快閘就永遠不會觸發，而且不會報錯。**

因此這裡不只是存檔，還記錄 `saved_at` 與 `as_of`：狀態檔存在但停在
兩週前，與狀態檔不存在，症狀完全一樣（計數器都是 0），
但一個是排程壞了、一個是第一次執行。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import STATE_PATH
from .controller import GateState

log = logging.getLogger(__name__)


def load_state(path: str = STATE_PATH) -> tuple[GateState, dict]:
    """回傳 (狀態, 中繼資訊)。檔案不存在時回傳全新狀態並標明。"""
    p = Path(path)
    if not p.exists():
        return GateState(), {"found": False, "reason": "狀態檔不存在（首次執行）"}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        # 壞掉的狀態檔不能靜靜當成全新狀態——那會讓計數器歸零而沒有人知道
        return GateState(), {"found": False, "reason": f"狀態檔無法解析：{type(e).__name__}"}
    return GateState.from_dict(raw.get("state")), {
        "found": True,
        "as_of": raw.get("as_of"),
        "saved_at": raw.get("saved_at"),
    }


def save_state(state: GateState, as_of: str, path: str = STATE_PATH) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "as_of": as_of,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "state": state.to_dict(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def staleness_days(meta: dict, as_of: str) -> Optional[int]:
    """狀態檔停在幾天前。None 代表沒有可比對的日期。"""
    prev = meta.get("as_of")
    if not prev:
        return None
    try:
        a = datetime.fromisoformat(str(prev)).date()
        b = datetime.fromisoformat(str(as_of)).date()
    except (ValueError, TypeError):
        return None
    return (b - a).days
