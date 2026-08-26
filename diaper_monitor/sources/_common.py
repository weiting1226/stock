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
    PACK_MULTIPLIER_PATTERN,
    PIECE_COUNT_PATTERN,
    PLAUSIBLE_PACK_PRICE_RANGE,
    PLAUSIBLE_UNIT_PRICE_RANGE,
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
    """標題裡的片數。箱購（「38片入*3包入」）回傳乘開後的**總片數**。

    倍數只在明確寫出來時才乘。乘錯了不會停在這裡——最終單片價還要過
    `looks_like_a_plausible_unit_price`，忘了乘（偏高）與乘過頭（偏低）
    在那一關都會被擋下。
    """
    m = re.search(PIECE_COUNT_PATTERN, name)
    if not m:
        return None
    count = int(m.group(1))
    mult = re.search(PACK_MULTIPLIER_PATTERN, name)
    return count * int(mult.group(1)) if mult else count


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


def looks_like_a_plausible_unit_price(unit_price: float) -> bool:
    """單片價的合理範圍。**這一關防的是片數算錯，不是售價異常。**

    `looks_like_a_plausible_pack_price` 看的是「一包多少錢」，擋不掉這種錯：
    一包 509 元完全正常，但片數若少算三倍，單片價會從 13 變成 40——而 509
    這個數字本身沒有任何異狀。會誤導「顯著下跌」判定的是單片價，
    所以要在那個數字上再設一道。
    """
    low, high = PLAUSIBLE_UNIT_PRICE_RANGE
    return low <= unit_price <= high


# 每一關各自的代號。**逐關計數是這次加的。**
#
# 原本三個爬蟲都只有一個 `skipped` 總數，於是日誌只能說「147 個候選、
# 0 個通過」，說不出是哪一關擋的——momo 連續十天回 0 筆，光看日誌完全無從查起。
REJECT_REASONS = ("沒有標題", "找不到價格", "判定不是 M 號", "標題不可靠",
                  "標題找不到片數", "售價不合理", "單片價不合理")


def screen_listing(name: Optional[str], pack_price: Optional[float]) -> tuple:
    """把一筆商品跑過所有過濾關卡。

    回傳 `(piece_count, reason)`：通過時 reason 是 None，被擋時 piece_count
    是 None 而 reason 說明是哪一關。三個來源共用同一套順序，日誌才能互相比較。
    """
    if not name:
        return None, "沒有標題"
    if pack_price is None:
        return None, "找不到價格"
    if not looks_like_size_m(name):
        return None, "判定不是 M 號"
    if looks_unreliable(name):
        return None, "標題不可靠"
    piece_count = extract_piece_count(name)
    if piece_count is None:
        return None, "標題找不到片數"
    if not looks_like_a_plausible_pack_price(pack_price):
        return None, "售價不合理"
    if not looks_like_a_plausible_unit_price(pack_price / piece_count):
        return None, "單片價不合理"
    return piece_count, None


def format_rejects(rejects) -> str:
    """把逐關計數印成一行。沒有任何一筆被擋時說「無」，
    而不是印一個空字串——空字串在日誌裡跟「這行壞了」長得一樣。"""
    if not rejects:
        return "無"
    return "、".join(f"{reason} {n}" for reason, n in
                     sorted(rejects.items(), key=lambda kv: -kv[1]))
