"""用 AI 摘要各指標對應的官方發布說明。

**為什麼用官方統計發布，而不是「新聞」**：CPI 的變動原因，最準確的來源就是
BLS 那份新聞稿本身——它逐項說明哪個分類推升了指數。二手新聞只是在轉述它，
而且會混入評論。官方發布同時也是穩定、免費、可引用的。

**反幻覺的三道防線**（沿用 FOMC 判讀那一套，這裡更重要）：

1. **抓不到文件就不呼叫模型。** 讓模型「回想」上個月 CPI 為什麼上漲，它會
   產出一段完全通順、細節具體、而且是編造的說明。散文比數字更危險——
   數字錯了看得出來，一段流暢的解釋不會有人懷疑。
2. **每個論點都要附原文引句**，且引句必須逐字出現在抓到的文件裡，否則整份
   摘要作廢。
3. **只摘要，不預測、不給投資意見。** 提示詞明確禁止，輸出也不留那種欄位。

**只在指標真的更新時才呼叫**：發布日誌已經知道今天有哪幾期是新的，
沒更新的指標重跑一次只會得到同樣的摘要，白花成本。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import requests

from liquidity_monitor.sources.fomc_ai import (
    DEFAULT_MODEL,
    _normalise_quote,
    call_model,
    is_enabled,
)

log = logging.getLogger(__name__)

# 各指標對應的官方統計發布。用發布機構的原始新聞稿，不用二手媒體。
# 一個指標可以有多個候選；依序嘗試，取第一個抓得到內容的。
# 已確認取不到、且決定不再嘗試的來源。
#
# 實測 2026-08（GitHub Actions）：**bls.gov 全站對資料中心 IP 回 403**，
# 三個不同路徑皆然，是 host 層級的封鎖，換路徑沒有用（同 ETF.com、CME）。
# 經確認後決定接受「這幾條沒有摘要」。
#
# 因此把它們列成「已知無法取得」而不是留在 RELEASE_SOURCES 裡：留著的話，
# 每天會多打六次註定失敗的請求，並且在失敗清單裡佔六個位置——把一個
# 「已經決定接受的狀態」每天當成新問題報一次。
#
# 但也不是靜靜刪掉：畫面仍會說明這幾條為什麼沒有摘要，讓「刻意不做」與
# 「壞掉了」分得出來。日後若改由可存取的環境執行，把項目移回上面即可。
UNAVAILABLE_SOURCES = {
    "CPIAUCSL": "bls.gov 對資料中心 IP 回 403（已確認接受無摘要）",
    "CPILFESL": "bls.gov 對資料中心 IP 回 403（已確認接受無摘要）",
    "UNRATE": "bls.gov 對資料中心 IP 回 403（已確認接受無摘要）",
    "PAYEMS": "bls.gov 對資料中心 IP 回 403（已確認接受無摘要）",
    "CIVPART": "bls.gov 對資料中心 IP 回 403（已確認接受無摘要）",
    "SAHMREALTIME": "bls.gov 對資料中心 IP 回 403（已確認接受無摘要）",
    "JTSJOL": "bls.gov 對資料中心 IP 回 403（已確認接受無摘要）",
}

RELEASE_SOURCES = {
    "INDPRO": ["https://www.federalreserve.gov/releases/g17/current/default.htm"],
    "HOUST": ["https://www.census.gov/construction/nrc/current/index.html"],
    "RSAFS": ["https://www.census.gov/retail/index.html"],
    "NFCI": ["https://www.chicagofed.org/research/data/nfci/current-data"],
    "UMCSENT": ["http://www.sca.isr.umich.edu/"],
    "MORTGAGE30US": ["https://www.freddiemac.com/pmms"],
    "GDPC1": ["https://www.bea.gov/data/gdp/gross-domestic-product"],
    "PCEPI": ["https://www.bea.gov/data/personal-consumption-expenditures-price-index"],
    "PCEPILFE": ["https://www.bea.gov/data/personal-consumption-expenditures-price-index"],
    # FEDFUNDS 不列靜態網址：正確的文件是「最近一次 FOMC 聲明」，網址每次會議
    # 都不同。改用 RESOLVERS 裡的解析器（見下方）。
    #
    # 原本指向 press_monetary.xml，那是**發布索引的 RSS**——去標籤之後只剩一串
    # 標題，看不出這一期的利率決定。模型於是正確地拒絕摘要，被歸為「內容不符」。
    # 這一條的病灶從頭到尾都是我們挑錯文件，不是來源不給。
    # 市場價格類（殖利率、利差、通膨預期、高收益債利差）沒有對應的統計發布。
    # 硬要配一個來源，只會讓模型對著不相干的文件生出一段像樣的解釋。
    #
    # 以下兩項確實有官方發布，但**刻意不列入**，理由記在這裡以免日後被盲目加回：
    #   ICSA        勞工部週報只有 PDF 版（dol.gov/ui/data.pdf），本模組的
    #               去標籤器處理不了 PDF。與其放一個必然失敗的網址，不如不放。
    #   CSUSHPINSA  S&P 的發布需登入，公開頁面只有摘要圖表。
    #
    # 上一次我憑印象加了一個沒驗證過的端點（FRED 的 /data/<id>.txt），
    # 實測回傳 HTML、根本不存在，卻在報告裡佔了一個「已嘗試」的位置。
    # 沒把握的網址不要放。
}

# 抓回來的頁面要有足夠內容才值得送給模型。太短通常代表拿到的是
# JavaScript 外殼或錯誤頁——那正是幻覺的溫床。
# 同一個原因只能有一種說法。實測分類器漏判，正是因為我在兩處把同一件事
# 寫成「沒有對應的官方統計發布」與「無對應的官方統計發布」。
NO_SOURCE_REASON = "無對應的官方統計發布（市場價格類指標）"
UNAVAILABLE_REASON = "來源已確認無法取得，依決定不再嘗試"

MIN_DOCUMENT_CHARS = 800
MAX_DOCUMENT_CHARS = 14000

_HEADERS = {"User-Agent": "Mozilla/5.0 (macro-monitor dashboard)"}

_PROMPT = """你是總體經濟統計發布的摘要器。以下是「{label}」這項指標的官方發布全文。

最新數據：參考期 {reference}，數值 {value}{unit}，三個月變化 {change_3m}。

請**只根據下面這份文件**回答。不要引用你記憶中的其他期別或其他來源的資訊。
文件裡沒有提到的事，就不要寫。

輸出**僅限**一個 JSON 物件，不要有任何其他文字或程式碼區塊標記：

{{
  "found": true 或 false,
  "summary_zh": "<繁體中文，2-4 句，說明這一期的變動與文件指出的原因>",
  "drivers": ["<推升或壓抑此指標的具體項目，取自文件，最多 4 個>"],
  "evidence": "<文件中最能支持上述摘要的一段原文，逐字複製，30-200 字元>"
}}

規則：
- 文件內容與這項指標無關、或看不出這一期的數字，`found` 填 false，其餘留空。
- `evidence` 必須是文件中**逐字存在**的片段，不得改寫、不得拼接。
- 只描述文件說了什麼，**不要預測未來、不要給投資建議、不要下多空判斷**。

文件全文：
---
{document}
---
"""


# 失敗原因分類。三種的處置完全不同：
#   blocked        來源擋住資料中心 IP —— 換來源或放棄，重試沒有用
#   no_source      本來就沒有對應的統計發布 —— 正常，不是故障
#   content        抓到了但內容不符 —— 網址可能指向入口頁而非發布頁，要檢查
#   model          模型或 API 出錯 —— 下次會自動重試
def classify_failure(message: str) -> str:
    if UNAVAILABLE_REASON in message:
        return "accepted"
    if "HTTP 403" in message or "HTTP 401" in message:
        return "blocked"
    if NO_SOURCE_REASON in message:
        return "no_source"
    if "不足以摘要" in message or "內容過短" in message:
        return "content"
    return "model"


@dataclass
class NewsSummary:
    fred_id: str
    source_url: str
    summary_zh: str
    drivers: list = field(default_factory=list)
    evidence: str = ""
    model: str = DEFAULT_MODEL


def _strip_html(html: str) -> str:
    """把頁面轉成純文字。保留段落間隔，讓模型看得出結構。"""
    text = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'"))
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _fomc_statement_document() -> tuple[str, str]:
    """取最近一次 FOMC 聲明全文。

    這個解析器不是新的來源，是**已經在用、而且已經證明可靠的**那一個：
    模組一每天靠它抓聲明來評分⑥，索引鏈（RSS 優先、四個候選）也早就修過了。
    與其為 FEDFUNDS 另外猜一個網址，不如用這個有實績的。
    """
    from datetime import date as _date

    from liquidity_monitor.sources.fomc import latest_meeting_on_or_before

    meeting = latest_meeting_on_or_before(_date.today().isoformat())
    if meeting is None or not (meeting.statement_text or "").strip():
        raise ValueError("FEDFUNDS：取不到最近一次 FOMC 聲明全文")
    return meeting.statement_text[:MAX_DOCUMENT_CHARS], meeting.statement_url


# 有些指標的正確文件沒有固定網址（FOMC 聲明每次會議一個網址）。
# 這種情況硬填一個靜態網址，就會像先前那樣指到索引頁而抓不到當期內容。
RESOLVERS = {
    "FEDFUNDS": _fomc_statement_document,
}


def fetch_release_document(fred_id: str, timeout: int = 30, session=None) -> tuple[str, str]:
    """抓官方發布全文。回傳 (純文字, 來源網址)；全部失敗則拋出。

    錯誤訊息要帶出每個候選網址的結果——抓不到時，「來源改版了」與
    「這個指標本來就沒有對應發布」必須分得出來。
    """
    if fred_id in UNAVAILABLE_SOURCES:
        raise ValueError(f"{fred_id}：{UNAVAILABLE_REASON}——{UNAVAILABLE_SOURCES[fred_id]}")
    if fred_id in RESOLVERS:
        return RESOLVERS[fred_id]()
    urls = RELEASE_SOURCES.get(fred_id)
    if not urls:
        raise ValueError(f"{fred_id}：{NO_SOURCE_REASON}")

    http = session or requests
    problems = []
    for url in urls:
        try:
            resp = http.get(url, headers=_HEADERS, timeout=timeout)
            if resp.status_code != 200:
                problems.append(f"{url}: HTTP {resp.status_code}")
                continue
            text = _strip_html(resp.text or "")
            if len(text) < MIN_DOCUMENT_CHARS:
                problems.append(f"{url}: 內容過短（{len(text)} 字），可能是 JS 外殼或錯誤頁")
                continue
            return text[:MAX_DOCUMENT_CHARS], url
        except Exception as e:  # noqa: BLE001
            problems.append(f"{url}: {type(e).__name__}")
    raise ValueError(f"{fred_id} 官方發布抓取失敗——{'；'.join(problems)}")


def summarise_release(
    row: dict,
    document: str,
    source_url: str,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    session=None,
) -> NewsSummary:
    """請模型摘要這份發布，並驗證它引用的原文真的存在。"""
    prompt = _PROMPT.format(
        label=row.get("label", row.get("fred_id", "")),
        reference=row.get("reference_date", "—"),
        value=row.get("value", "—"),
        unit=row.get("unit", ""),
        change_3m=row.get("change_3m", "—"),
        document=document,
    )
    data = call_model(prompt, api_key=api_key, model=model, session=session)

    if not data.get("found"):
        raise ValueError(f"{row.get('fred_id')}：模型判定文件內容不足以摘要這一期的數字")

    summary = (data.get("summary_zh") or "").strip()
    evidence = (data.get("evidence") or "").strip()
    if not summary:
        raise ValueError(f"{row.get('fred_id')}：模型未提供摘要")
    if not evidence:
        raise ValueError(f"{row.get('fred_id')}：模型未提供原文佐證")
    if _normalise_quote(evidence) not in _normalise_quote(document):
        # 引句不在文件裡，代表這段摘要有一部分是編出來的。
        # 只丟掉引句、留下摘要是不夠的——編得出引句的回應，摘要同樣不可信。
        raise ValueError(
            f"{row.get('fred_id')}：佐證片段未出現在原文中，研判為幻覺，整份摘要不採用"
        )

    drivers = [str(d).strip() for d in (data.get("drivers") or []) if str(d).strip()][:4]
    return NewsSummary(fred_id=row.get("fred_id", ""), source_url=source_url,
                       summary_zh=summary, drivers=drivers, evidence=evidence, model=model)


def summarise_updated(
    rows: list,
    updated_ids: list,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    session=None,
) -> tuple[dict, list]:
    """只為「今天真的更新了」的指標產生摘要。

    回傳 ({fred_id: 摘要}, [略過或失敗的原因])。
    沒有金鑰時整段跳過並說明原因，不讓流程中斷。
    """
    if not is_enabled(api_key):
        return {}, ["未設定 ANTHROPIC_API_KEY，略過新聞摘要"]

    by_id = {r.get("fred_id"): r for r in rows}
    out, notes = {}, []
    for fred_id in updated_ids:
        row = by_id.get(fred_id)
        if not row or not row.get("available"):
            continue
        if fred_id in UNAVAILABLE_SOURCES:
            # 已決定接受沒有摘要——不發請求、不呼叫模型，只留下說明
            notes.append(f"{fred_id}：{UNAVAILABLE_REASON}——{UNAVAILABLE_SOURCES[fred_id]}")
            continue
        if fred_id not in RELEASE_SOURCES and fred_id not in RESOLVERS:
            notes.append(f"{fred_id}：{NO_SOURCE_REASON}")
            continue
        try:
            document, url = fetch_release_document(fred_id, session=session)
        except Exception as e:  # noqa: BLE001
            notes.append(str(e))
            continue
        try:
            summary = summarise_release(row, document, url, api_key, model, session)
        except Exception as e:  # noqa: BLE001
            # 訊息一定要帶指標代碼。實測有一則是「模型回應中找不到 JSON」，
            # 因為那是共用的 call_model 拋出的、不知道自己在處理哪一條——
            # 於是報告上出現一個無主的錯誤，無從追查。
            msg = str(e)
            notes.append(msg if fred_id in msg else f"{fred_id}：{msg}")
            continue
        out[fred_id] = {
            "summary_zh": summary.summary_zh,
            "drivers": summary.drivers,
            "evidence": summary.evidence,
            "source_url": summary.source_url,
            "model": summary.model,
        }
    return out, notes
