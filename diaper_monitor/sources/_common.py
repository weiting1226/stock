"""所有自動查價來源共用的標題解析邏輯。

抽出來是因為第二個來源（`shopee.py`）要重用跟 `pchome.py` 完全一樣的
「M 號 / 片數 / 不可靠標題」判斷——這些規則裡有 2026-08-18 那次真實抓錯過
才補上的過濾條件（見 `config.UNRELIABLE_TITLE_PATTERN`），兩邊各複製一份
邏輯，日後修規則很容易漏改其中一邊。
"""
from __future__ import annotations

import re
from typing import Optional

from ..config import (
    PIECE_COUNT_PATTERN,
    PLAUSIBLE_PACK_PRICE_RANGE,
    SIZE_M_PATTERN,
    UNRELIABLE_TITLE_PATTERN,
)


def first(item: dict, *keys: str):
    """依序試幾種常見的欄位命名（大小寫、新舊版本不一致是這類 API 的常態），
    避免因為猜錯一種命名就整批漏抓。"""
    for k in keys:
        v = item.get(k)
        if v not in (None, ""):
            return v
    return None


def extract_piece_count(name: str) -> Optional[int]:
    m = re.search(PIECE_COUNT_PATTERN, name)
    return int(m.group(1)) if m else None


def looks_like_size_m(name: str) -> bool:
    return re.search(SIZE_M_PATTERN, name) is not None


def looks_unreliable(name: str) -> bool:
    """擋掉試用包、多尺寸片數擠在一起、箱購倍數這三種標題——
    三個都是 `config.UNRELIABLE_TITLE_PATTERN` 開頭註解裡實際抓錯過的真實案例。"""
    return re.search(UNRELIABLE_TITLE_PATTERN, name) is not None


def looks_like_a_plausible_pack_price(pack_price: float) -> bool:
    """換算出來的售價如果離譜到不像一包尿布的價格，寧可整批跳過——
    這關專門防「單位／換算係數猜錯」，見 `config.PLAUSIBLE_PACK_PRICE_RANGE`。"""
    low, high = PLAUSIBLE_PACK_PRICE_RANGE
    return low <= pack_price <= high
