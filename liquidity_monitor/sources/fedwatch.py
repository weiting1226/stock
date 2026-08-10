"""⑥ 市場隱含 12 個月路徑（CME FedWatch）— 以 AI 解讀機率表，並附硬資料備援。

規格第136行：降息≥2碼 +2 ／降息1碼 +1 ／持平 0 ／升息1碼 −1 ／升息≥2碼 −2。

FedWatch 的機率表是一張「各次會議 × 各目標區間」的二維表，要從它讀出
「12 個月後的隱含利率」需要理解表格語意——這正是模型比正則式解析有優勢的
地方。但**模型只負責讀畫面上的數字，不負責回憶市場預期**：

- 沒抓到頁面就不呼叫模型。絕不讓它憑記憶說「市場預期降兩碼」——那是編造。
- 抽取結果必須附上頁面中出現的原文片段，且該片段要真的存在於抓到的內容裡。
- 抽出的隱含利率必須落在合理區間（0%–10%），否則視為誤讀。

抓不到 FedWatch 時退回一個**明確標示為近似**的算法：一年期公債殖利率與
目前聯邦資金目標區間的差距。它含期限溢酬、不等於純粹的市場隱含路徑，
因此信心只給「低」，並在備註寫明是近似值——不會假裝它是 FedWatch。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import requests

from . import fomc_ai

log = logging.getLogger(__name__)

CANDIDATE_URLS = (
    "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html",
    "https://www.cmegroup.com/trading/interest-rates/countdown-to-fomc.html",
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# 一碼 = 25 個基點。規格的五個等級就是以「碼」為單位切的。
BASIS_POINTS_PER_STEP = 25
# 隱含利率的合理範圍。落在範圍外代表模型讀錯了欄位（例如把機率當成利率）。
MIN_PLAUSIBLE_RATE = 0.0
MAX_PLAUSIBLE_RATE = 10.0

MAX_PAGE_CHARS = 20000

_PROMPT = """以下是 CME FedWatch 工具頁面的內容。請只根據這份內容回答，
**不要引用你記憶中的市場預期或任何頁面上沒有的數字**。

請找出「約 12 個月後的那次 FOMC 會議」市場隱含機率最高的聯邦資金目標區間，
並輸出**僅限**一個 JSON 物件，不要有其他文字：

{{
  "found": true | false,
  "implied_rate_pct": <該區間的中點，以百分比表示，例如 3.625>,
  "meeting_label": "<那次會議在頁面上的標示，逐字照抄>",
  "evidence": "<頁面中支持上述數字的原文片段，逐字照抄>",
  "summary_zh": "<繁體中文摘要，一到兩句>"
}}

若頁面內容不足以判讀（例如資料由 JavaScript 動態載入、頁面只有框架），
請把 found 設為 false，其餘欄位留空字串或 0，**不要猜測**。

evidence 必須是頁面內容中真實存在的片段，逐字照抄。

頁面內容：
---
{page}
---"""


@dataclass
class ImpliedPath:
    """市場隱含的 12 個月政策路徑。"""
    change_bp: float                 # 相對目前目標區間的變動，負值為降息
    implied_rate_pct: Optional[float] = None
    current_rate_pct: Optional[float] = None
    basis: str = ""                  # "fedwatch_ai" 或 "treasury_proxy"
    detail: str = ""
    warnings: list = field(default_factory=list)


def fetch_fedwatch_page(timeout: int = 30, session=None) -> str:
    """取得 FedWatch 頁面內容。全部失敗時拋出並附上各網址的實際結果。"""
    http = session or requests
    diagnostics: list[str] = []
    for url in CANDIDATE_URLS:
        try:
            resp = http.get(url, headers=_HEADERS, timeout=timeout)
        except Exception as e:  # noqa: BLE001 — 換下一個網址再試
            diagnostics.append(f"{url.rsplit('/', 1)[-1]}: {type(e).__name__}: {e}")
            continue
        if resp.status_code != 200:
            diagnostics.append(
                f"{url.rsplit('/', 1)[-1]}: HTTP {resp.status_code}"
                + ("（可能是 Cloudflare 或需登入）" if resp.status_code in (401, 403, 503) else "")
            )
            continue
        text = re.sub(r"<script.*?</script>", " ", resp.text or "", flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 500:
            diagnostics.append(f"{url.rsplit('/', 1)[-1]}: 取得頁面但內容過短（{len(text)} 字元）")
            continue
        return text
    raise ValueError("CME FedWatch 頁面無法取得；已嘗試 —— " + "｜".join(diagnostics))


def implied_path_from_page(
    page_text: str,
    current_rate_pct: float,
    api_key: Optional[str] = None,
    session=None,
) -> ImpliedPath:
    """讓模型從 FedWatch 頁面讀出隱含利率，再換算成相對目前利率的變動。"""
    payload = fomc_ai.call_model(
        _PROMPT.format(page=page_text[:MAX_PAGE_CHARS]),
        api_key=api_key, session=session,
    )

    if not payload.get("found"):
        raise ValueError(
            "模型判定 FedWatch 頁面內容不足以判讀（多半是機率表由 JavaScript 動態載入）"
        )

    try:
        implied = float(payload.get("implied_rate_pct"))
    except (TypeError, ValueError):
        raise ValueError(f"模型回傳的 implied_rate_pct 無法解析：{payload.get('implied_rate_pct')!r}") from None

    if not MIN_PLAUSIBLE_RATE <= implied <= MAX_PLAUSIBLE_RATE:
        # 落在合理區間外，通常代表把機率（%）當成利率讀了
        raise ValueError(f"模型讀出的隱含利率 {implied}% 不在合理範圍，研判為誤讀欄位")

    evidence = str(payload.get("evidence") or "").strip()
    warnings: list[str] = []
    if not evidence:
        raise ValueError("模型未提供頁面原文佐證，不採信其判讀")
    if _normalise(evidence) not in _normalise(page_text):
        raise ValueError("模型提供的佐證片段未出現在頁面內容中，研判為幻覺")

    return ImpliedPath(
        change_bp=round((implied - current_rate_pct) * 100, 1),
        implied_rate_pct=implied,
        current_rate_pct=current_rate_pct,
        basis="fedwatch_ai",
        detail=f"{payload.get('meeting_label', '')}｜{payload.get('summary_zh', '')}".strip("｜"),
        warnings=warnings,
    )


def implied_path_from_treasury(one_year_yield_pct: float, current_rate_pct: float) -> ImpliedPath:
    """備援：一年期公債殖利率與目前政策利率的差距。

    **這是近似值，不是 FedWatch。** 一年期殖利率約等於「未來一年政策利率的
    平均預期」加上期限溢酬，後者無法從這裡拆出來。因此呼叫端要把信心降級並
    在備註寫明是近似——不能讓它在畫面上看起來跟真的隱含路徑一樣。
    """
    return ImpliedPath(
        change_bp=round((one_year_yield_pct - current_rate_pct) * 100, 1),
        implied_rate_pct=one_year_yield_pct,
        current_rate_pct=current_rate_pct,
        basis="treasury_proxy",
        detail=f"1年期公債 {one_year_yield_pct:.2f}% vs 目前 {current_rate_pct:.2f}%",
        warnings=["以一年期公債殖利率近似，含期限溢酬，非 FedWatch 的市場隱含機率"],
    )


def score_implied_path(change_bp: Optional[float]) -> Optional[int]:
    """依規格第136行計分。

    連續的基點變動要先四捨五入到最接近的「碼」（25bp），再對應等級——
    規格是以碼為單位描述的，−37bp 這種數字必須先歸到「降息1碼還是2碼」
    才有對應的分數。
    """
    if change_bp is None:
        return None
    steps = round(float(change_bp) / BASIS_POINTS_PER_STEP)
    if steps <= -2:
        return 2
    if steps == -1:
        return 1
    if steps == 0:
        return 0
    if steps == 1:
        return -1
    return -2


def _normalise(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[‐-―]", "-", text)
    return re.sub(r"\s+", " ", text).strip()
