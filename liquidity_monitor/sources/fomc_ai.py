"""用 AI 摘要並分類 FOMC 聲明，取代單純的字串比對。

原本的判斷是 `re.search("voting against")`——只知道「有沒有人投反對票」，
不知道**反對的方向**。但規格第135行寫的是「持平但有**升息**異議：−1」：
反對者主張升息才是鷹派訊號；若他主張的是降息，那反而偏鴿。同一個正則
把兩者算成同一件事，等於把一個訊號和它的反面混為一談。

因此改由模型讀完整聲明，輸出結構化結果：升／降／持平、異議人數與方向、
以及一段中文摘要。三道防線確保它不會亂編：

1. **輸出限定為 JSON schema**，欄位不合就當作失敗，不做寬鬆解析。
2. **要求引用原文**：異議判定必須附上聲明中的原句，且該句要真的出現在
   全文裡，否則視為幻覺並丟棄。
3. **利率方向以 FRED 為準**：升降息幅度是硬資料，模型只在「異議方向」與
   「摘要」上有裁量權。模型若與 FRED 不一致，採用 FRED 並記錄這個矛盾。

沒有 API 金鑰就拋錯，由呼叫端退回原本的正則判斷，不會讓流程中斷。
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import requests

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
API_KEY_ENV = "ANTHROPIC_API_KEY"
# 分類任務不需要最大的模型；Sonnet 在結構化抽取上足夠且便宜
DEFAULT_MODEL = "claude-sonnet-5"
API_VERSION = "2023-06-01"

DECISION_CUT = "cut"
DECISION_HOLD = "hold"
DECISION_HIKE = "hike"
_DECISIONS = {DECISION_CUT, DECISION_HOLD, DECISION_HIKE}

DISSENT_NONE = "none"
DISSENT_HAWKISH = "hawkish"     # 主張升息／主張更緊縮
DISSENT_DOVISH = "dovish"       # 主張降息／主張更寬鬆
_DISSENTS = {DISSENT_NONE, DISSENT_HAWKISH, DISSENT_DOVISH, "mixed"}

# 聲明全文動輒上萬字，只取前段（決議與投票結果都在前面），省 token 也降低離題
MAX_STATEMENT_CHARS = 12000

_PROMPT = """你是總體經濟資料的結構化抽取器。以下是一份 FOMC 會後聲明全文。

請只根據這份文字回答，不要引用你記憶中的其他會議內容。

輸出**僅限**一個 JSON 物件，不要有任何其他文字或程式碼區塊標記：

{{
  "decision": "cut" | "hold" | "hike",
  "rate_change_bp": <整數，聯邦資金利率目標區間的變動基點，持平為 0，降息為負>,
  "dissent_count": <整數，投反對票的委員人數，沒有就填 0>,
  "dissent_direction": "none" | "hawkish" | "dovish" | "mixed",
  "dissent_quote": "<聲明中描述投票結果或反對意見的原句，逐字照抄；沒有異議則填空字串>",
  "summary_zh": "<繁體中文摘要，兩到三句，說明決議內容與聲明語氣的重點>"
}}

dissent_direction 的判斷方式：
- 反對者主張**升息或更緊縮**（higher target range／tighter policy）-> "hawkish"
- 反對者主張**降息或更寬鬆**（lower target range／easier policy）-> "dovish"
- 同時有兩種方向的反對票 -> "mixed"
- 沒有反對票 -> "none"

dissent_quote 必須是原文中真實存在的句子，逐字照抄，不可改寫或翻譯。

聲明全文：
---
{statement}
---"""


@dataclass
class FomcClassification:
    decision: str
    rate_change_bp: int
    dissent_count: int
    dissent_direction: str
    dissent_quote: str = ""
    summary_zh: str = ""
    model: str = ""
    warnings: list = field(default_factory=list)

    @property
    def has_dissent(self) -> bool:
        return self.dissent_count > 0

    @property
    def has_hawkish_dissent(self) -> bool:
        return self.dissent_direction in (DISSENT_HAWKISH, "mixed")


def is_enabled(api_key: Optional[str] = None) -> bool:
    return bool(api_key or os.environ.get(API_KEY_ENV))


def _extract_json(text: str) -> dict:
    """從回應中取出 JSON。模型偶爾會包上程式碼區塊或加一句前言。"""
    stripped = (text or "").strip()
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.MULTILINE).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    # 退一步：抓出第一個看起來完整的物件
    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if not match:
        raise ValueError(f"模型回應中找不到 JSON；前 200 字：{stripped[:200]!r}")
    return json.loads(match.group(0))


def _normalise_quote(text: str) -> str:
    """比對引文用：去掉多餘空白與標點差異（連字號、引號的各種變體）。"""
    text = (text or "").lower()
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = re.sub(r"[‐-―]", "-", text)
    return re.sub(r"\s+", " ", text).strip()


def call_model(
    prompt: str,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    timeout: int = 60,
    session=None,
    max_tokens: int = 1024,
) -> dict:
    """送出提示並取回 JSON 物件。供各個需要 AI 抽取的指標共用。

    抽成共用函式是因為「呼叫 API、檢查狀態碼、從回應裡挖出 JSON」這幾件事
    每個指標都一樣；各寫一份的話，錯誤處理會逐漸長歪成不同的樣子。

    `max_tokens` 可調：中文摘要比 FOMC 那種短分類長得多，用同一個上限會被截斷。
    """
    key = api_key or os.environ.get(API_KEY_ENV)
    if not key:
        raise ValueError(f"未設定 {API_KEY_ENV}，無法使用 AI 分類")

    http = session or requests
    resp = http.post(
        API_URL,
        json={"model": model, "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout,
        headers={"x-api-key": key, "anthropic-version": API_VERSION,
                 "content-type": "application/json"},
    )
    if resp.status_code != 200:
        snippet = (resp.text or "")[:200].replace("\n", " ")
        raise ValueError(f"Anthropic API 回應 {resp.status_code}：{snippet}")

    payload = resp.json()
    blocks = payload.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    # 被 max_tokens 截斷時，回應是一段**沒寫完的 JSON**。若只交給 _extract_json，
    # 得到的錯誤是「模型回應中找不到 JSON」——那句話會把人引向完全錯誤的方向：
    # 實際上模型輸出得好好的，是我們給的額度不夠。實測 CPI 摘要就是這樣失敗的，
    # 錯誤訊息裡還附著一段明明是 JSON 的開頭。
    if payload.get("stop_reason") == "max_tokens":
        raise ValueError(
            f"模型回應在 max_tokens={max_tokens} 處被截斷，JSON 不完整——"
            f"這不是模型出錯，是額度不夠（已輸出 {len(text)} 字）"
        )
    return _extract_json(text)


def classify_statement(
    statement_text: str,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    timeout: int = 60,
    session=None,
) -> FomcClassification:
    """讓模型讀聲明並輸出結構化分類。抽取失敗一律拋錯，不回傳半套結果。"""
    if not (statement_text or "").strip():
        raise ValueError("聲明全文為空，無從分類")

    data = call_model(
        _PROMPT.format(statement=statement_text[:MAX_STATEMENT_CHARS]),
        api_key=api_key, model=model, timeout=timeout, session=session,
    )

    decision = str(data.get("decision", "")).strip().lower()
    direction = str(data.get("dissent_direction", "")).strip().lower()
    if decision not in _DECISIONS:
        raise ValueError(f"模型回傳的 decision 不在允許值內：{decision!r}")
    if direction not in _DISSENTS:
        raise ValueError(f"模型回傳的 dissent_direction 不在允許值內：{direction!r}")

    try:
        count = int(data.get("dissent_count") or 0)
        change_bp = int(data.get("rate_change_bp") or 0)
    except (TypeError, ValueError) as e:
        raise ValueError(f"模型回傳的數值欄位無法解析：{e}") from None

    result = FomcClassification(
        decision=decision,
        rate_change_bp=change_bp,
        dissent_count=max(0, count),
        dissent_direction=direction,
        dissent_quote=str(data.get("dissent_quote") or "").strip(),
        summary_zh=str(data.get("summary_zh") or "").strip(),
        model=model,
    )

    # 引文必須真的出現在聲明裡。這是防幻覺最有效的一道：模型可以編出一段
    # 合理的敘述，但編不出一段「剛好逐字出現在原文」的句子。
    if result.dissent_count and result.dissent_quote:
        haystack = _normalise_quote(statement_text)
        if _normalise_quote(result.dissent_quote) not in haystack:
            result.warnings.append(
                "模型提供的異議引文未出現在聲明全文中，已不採信其異議判定"
            )
            result.dissent_count = 0
            result.dissent_direction = DISSENT_NONE
    elif result.dissent_count and not result.dissent_quote:
        result.warnings.append("模型判定有異議票但未提供原文引用，已不採信")
        result.dissent_count = 0
        result.dissent_direction = DISSENT_NONE

    return result


def reconcile_with_rate_data(
    classification: FomcClassification,
    target_upper_before: Optional[float],
    target_upper_after: Optional[float],
) -> FomcClassification:
    """利率方向以 FRED 的實際目標區間為準，模型只負責異議方向與摘要。

    升降息是硬資料，沒有讓模型裁量的理由。兩者不一致時採用 FRED 並記下
    這個矛盾——那通常代表聲明抓錯了場次，值得人看一眼。
    """
    if target_upper_before is None or target_upper_after is None:
        classification.warnings.append("無聯邦資金目標區間資料可交叉驗證，方向僅採模型判斷")
        return classification

    diff = round(target_upper_after - target_upper_before, 4)
    actual = DECISION_CUT if diff < 0 else DECISION_HIKE if diff > 0 else DECISION_HOLD
    if actual != classification.decision:
        classification.warnings.append(
            f"模型判定為 {classification.decision}，但目標區間上緣 "
            f"{target_upper_before}%→{target_upper_after}% 顯示為 {actual}；以實際利率為準"
        )
        classification.decision = actual
    classification.rate_change_bp = int(round(diff * 100))
    return classification


def score_from_classification(classification: FomcClassification) -> int:
    """依規格第135行計分。

    降息無異議:+2／降息有異議:+1／持平無異議:0／持平但有升息異議:−1／升息:−2

    關鍵在「持平」那一列：規格寫的是**升息異議**。反對者主張升息才是鷹派
    訊號；若他主張降息，那反而偏鴿，不該記 −1。原本的正則無法分辨方向，
    把兩者都算成 −1——等於把一個訊號和它的反面混為一談。
    """
    if classification.decision == DECISION_HIKE:
        return -2
    if classification.decision == DECISION_CUT:
        return 1 if classification.has_dissent else 2
    return -1 if classification.has_hawkish_dissent else 0
