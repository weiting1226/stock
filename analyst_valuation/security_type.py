"""判斷一檔上市證券的種類，用來把非營運公司的證券排除在抓取範圍外。

實測依據（2026-08，掃描 4,965 檔的結果）：

| 種類 | 檔數 | 有分析師目標價 | 覆蓋率 |
|---|---:|---:|---:|
| 普通股（含 ADR）| 3,933 | 2,748 | 69.9% |
| 認股權證 | 329 | 0 | **0.0%** |
| 特別股 | 280 | 3 | 1.1% |
| SPAC 單位 | 222 | 18 | 8.1% |
| 債券 | 121 | 1 | 0.8% |
| 權利 | 80 | 1 | 1.2% |

非普通股共 1,032 檔（占 20.8%），只有 23 檔有目標價（2.2%），認股權證更是
一檔都沒有。它們佔掉五分之一的抓取預算，換來的幾乎只是永遠空白的列——
而且認股權證、債券本來就不是「公司」。

**分類結果會寫進 universe.csv 保留，只是預設不抓**；要全部納入時傳
`include_all=True` 即可，不必重建清單。判斷錯了也看得出來是錯在哪一檔。
"""
from __future__ import annotations

import re

COMMON = "common"
PREFERRED = "preferred"
WARRANT = "warrant"
UNIT = "unit"
RIGHT = "right"
NOTE = "note"

# 預設納入抓取的種類。普通股（含 ADR）才是「公司」，其餘是衍生或債務證券。
OPERATING_COMPANY_TYPES = frozenset({COMMON})

_WARRANT_RE = re.compile(r"\bwarrants?\b", re.IGNORECASE)
_UNIT_RE = re.compile(r"\bunits?\b", re.IGNORECASE)
_RIGHT_RE = re.compile(r"\brights?\b", re.IGNORECASE)
_NOTE_RE = re.compile(r"\bnotes?\b|\bdebentures?\b|\bbonds?\b", re.IGNORECASE)
_PREFERRED_RE = re.compile(r"\bpreferred\b|\bpfd\b", re.IGNORECASE)
# 「American Depositary Shares」是外國公司的普通權益（ABEV、ABVX 這些），
# 不是特別股。第一版分類把 depositary 一律當成特別股，於是 ADR 全被錯分——
# 那會誤刪 100 多檔有真實分析師覆蓋的外國公司。
_ADR_RE = re.compile(r"american\s+depositar|\bADRs?\b|\bADSs?\b", re.IGNORECASE)
_DEPOSITARY_RE = re.compile(r"\bdepositar", re.IGNORECASE)
# 合夥事業（MLP／LP）的權益就叫「Common Units」「Limited Partnership Units」，
# 它們是**真實營運公司**，不是 SPAC 的認購單位。AllianceBernstein、
# Brookfield Renewable、Alliance Resource 這些都屬於此類，共 49 檔——
# 只用「名稱含 Units」判斷會把它們一起濾掉。
_PARTNERSHIP_UNIT_RE = re.compile(
    r"common\s+units|limited\s+partner|limited\s+partnership|depositary\s+units"
    r"|\bL\.P\.|\bLP\b",
    re.IGNORECASE,
)
# 交易所的 XXX$Y 已被正規化成 XXX-PY，代碼形態本身就是特別股的證據
_PREFERRED_TICKER_RE = re.compile(r"-P[A-Z]?$")
# 明寫的證券形式。用來壓過公司名稱裡碰巧出現的 Bond／Right 等字
_COMMON_FORM_RE = re.compile(r"common\s+stock|ordinary\s+shares?|common\s+shares?",
                             re.IGNORECASE)


def _is_preferred(name: str) -> bool:
    if _PREFERRED_RE.search(name):
        return True
    # 沒寫 American 的「Depositary Shares」多半是特別股的存託憑證
    #（如 "…Depositary Shares each representing 1/1000th of a share of Series A"）
    return bool(_DEPOSITARY_RE.search(name) and not _ADR_RE.search(name))


def classify_security(ticker: str, name: str) -> str:
    """由證券名稱判斷種類。

    順序是這個函式的全部重點。證券名稱裡有兩種資訊，必須分開看：

      1. **這張證券本身是什麼**（Common Stock、Warrant、Notes…）
      2. **它代表什麼**（"each representing 500 Preferred shares"）

    第二種只是描述，不是這張證券的種類。第一版沒有分開，於是 ITUB（Itaú）、
    AMX（América Móvil）、KOF（可口可樂 FEMSA）這些大型 ADR 全被當成特別股
    或單位濾掉——它們是外國公司的普通權益，有真實分析師覆蓋。
    """
    n = name or ""

    # 交易所代碼的 $ 後綴（已正規化為 -PX）是最硬的證據，名稱怎麼寫都不會推翻
    if _PREFERRED_TICKER_RE.search(ticker or ""):
        return PREFERRED
    # 存託憑證一律是普通權益，它「代表」什麼與它自己的種類無關
    if _ADR_RE.search(n):
        return COMMON
    if _WARRANT_RE.search(n):
        return WARRANT
    if _UNIT_RE.search(n):
        return COMMON if _PARTNERSHIP_UNIT_RE.search(n) else UNIT
    if _RIGHT_RE.search(n):
        return RIGHT
    # 明寫證券形式者以它為準，勝過公司名稱裡碰巧出現的字
    #（"Our Bond, Inc. - Common Stock" 曾被當成債券）
    if _COMMON_FORM_RE.search(n):
        return COMMON
    if _NOTE_RE.search(n):
        return NOTE
    if _is_preferred(n):
        return PREFERRED
    return COMMON


def is_operating_company(security_type: str) -> bool:
    return security_type in OPERATING_COMPANY_TYPES
