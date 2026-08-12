"""替代來源探測器：先用證據確認網址能用，再決定要不要採用。

**為什麼需要這支程式，而不是直接把網址加進 `news_ai.RELEASE_SOURCES`**

上一次我憑印象加了一個沒驗證過的端點（FRED 的 `/data/<id>.txt`），實測回傳
HTML、那個端點根本不存在，卻已經在報告裡佔了一個「已嘗試」的位置，還讓我在
說明裡寫下「試過三個端點」這句假話。開發環境沒有對外網路，**我在這裡永遠
驗證不了任何網址**——能驗證的只有 GitHub Actions。

所以流程分兩步：這支程式在 Actions 裡跑，把每個候選網址的實際結果記下來；
我看到真實結果之後，才把通過的網址寫進 `news_ai`。探測器**不會自動採用**
任何來源——自動採用等於把「沒驗證」換個地方藏起來。

**四道檢查，缺一不可**（只看 HTTP 200 會放進一堆入口頁）：

1. `status`      —— 200 以外直接淘汰
2. `final_url`   —— 追完轉址後仍在預期的網域。實測很多站台把不存在的頁面
                    302 回首頁再回 200，只看狀態碼會把首頁當成發布頁
3. `topic`       —— 內文要出現這項指標的關鍵詞。少了這關，一頁 5,000 字的
                    導覽選單也會過關
4. `recency`     —— 要提到最近三個月的月份名。靜態說明頁與當期發布頁的差別
                    就在這裡，而這正是目前那四條「內容不符」的病灶

四道都過才叫 `usable`，而且報告會列出每一道的結果——只寫「可用／不可用」的話，
「來源改版了」與「我們挑錯頁面」又會長得一模一樣。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional
from urllib.parse import urlparse

import requests

from macro_monitor.news_ai import MIN_DOCUMENT_CHARS, _strip_html

log = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (macro-monitor source probe)"}

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


@dataclass
class Candidate:
    """一個待驗證的候選來源。

    `kind` 決定它通過之後能不能用，而不只是描述性欄位：
      official  發布機構原文——最理想
      news      二手報導——可用，但摘要必然混入記者的解讀
      archive   官方原文的存檔副本——文字仍是官方的，代價是可能落後幾天
      probe     只是想知道這個主機通不通，不打算當來源用
    """

    url: str
    kind: str
    note: str = ""


@dataclass
class ProbeResult:
    fred_id: str
    url: str
    kind: str
    status: Optional[int] = None
    final_url: str = ""
    text_len: int = 0
    topic_hits: list = field(default_factory=list)
    recent_months: list = field(default_factory=list)
    verdict: str = ""
    reason: str = ""

    @property
    def usable(self) -> bool:
        return self.verdict == "usable"


# 每項指標的主題關鍵詞。內文必須出現其中至少兩個，否則就算抓到了 5,000 字，
# 也可能只是那個網站的導覽選單。
TOPIC_TERMS = {
    "CPIAUCSL": ["Consumer Price Index", "CPI", "inflation", "shelter"],
    "CPILFESL": ["Consumer Price Index", "core", "food and energy", "CPI"],
    "UNRATE": ["unemployment rate", "labor force", "jobless", "employment"],
    "PAYEMS": ["nonfarm payroll", "payroll employment", "jobs", "employment"],
    "CIVPART": ["labor force participation", "labor force", "participation rate"],
    "SAHMREALTIME": ["unemployment rate", "recession", "labor market", "Sahm"],
    "JTSJOL": ["job openings", "JOLTS", "labor turnover", "hires"],
    "RSAFS": ["retail sales", "retail trade", "food services", "advance"],
    "PCEPI": ["personal consumption expenditures", "PCE", "price index", "personal income"],
    "PCEPILFE": ["personal consumption expenditures", "core", "price index", "food and energy"],
    "FEDFUNDS": ["federal funds", "FOMC", "monetary policy", "target range"],
    "NFCI": ["financial conditions", "NFCI", "credit", "leverage"],
}

MIN_TOPIC_HITS = 2

# 候選來源。每一條都標明為什麼值得一試——沒有理由的網址就是下一個
# `/data/<id>.txt`。信心程度寫在 note 裡，探測結果會覆蓋它。
#
# 官方原文抓不到時，二手報導是**可以接受的替代**：使用者當初要的就是
# 「蒐集對應新聞並摘要」，是我自己把範圍收窄成官方發布的。收窄有它的道理
# （二手報導混入評論與預測），但在官方原文根本拿不到的七條上，
# 「有一段標明出處的二手摘要」顯然勝過「什麼都沒有」。
CANDIDATES: dict[str, list[Candidate]] = {}


def _bls_candidates(fred_id: str, release_path: str, hub: str) -> list[Candidate]:
    """BLS 系列的替代路徑。

    www.bls.gov 對資料中心 IP 回 403 已經確認。這裡要問的是三個**不同**的問題：
    封鎖是不是只在 www 這個主機？存檔副本拿不拿得到？二手報導行不行？
    三條路的答案彼此獨立，所以三條都要探。
    """
    return [
        # 1) 封鎖範圍：data.bls.gov 是不同主機，若通則代表 403 是 www 專屬規則
        Candidate(f"https://data.bls.gov/{release_path}", "probe",
                  "測 data.bls.gov 是否與 www 同樣被擋"),
        # 2) 存檔副本：文字仍是 BLS 原文，代價是快照可能落後幾天
        Candidate(f"https://web.archive.org/web/2/https://www.bls.gov/{release_path}",
                  "archive", "Wayback 最新快照，原文逐字保留"),
        # 3) 二手報導
        Candidate(hub, "news", "主流媒體報導頁"),
    ]


CANDIDATES.update({
    "CPIAUCSL": _bls_candidates("CPIAUCSL", "news.release/cpi.nr0.htm",
                                "https://apnews.com/hub/inflation"),
    "CPILFESL": _bls_candidates("CPILFESL", "news.release/cpi.nr0.htm",
                                "https://apnews.com/hub/inflation"),
    "UNRATE": _bls_candidates("UNRATE", "news.release/empsit.nr0.htm",
                              "https://apnews.com/hub/jobs"),
    "PAYEMS": _bls_candidates("PAYEMS", "news.release/empsit.nr0.htm",
                              "https://apnews.com/hub/jobs"),
    "CIVPART": _bls_candidates("CIVPART", "news.release/empsit.nr0.htm",
                               "https://apnews.com/hub/jobs"),
    "SAHMREALTIME": _bls_candidates("SAHMREALTIME", "news.release/empsit.nr0.htm",
                                    "https://apnews.com/hub/jobs"),
    "JTSJOL": _bls_candidates("JTSJOL", "news.release/jolts.nr0.htm",
                              "https://apnews.com/hub/jobs"),

    # --- 以下四條不是被封鎖，是我們指到了入口頁。要找的是「當期發布頁」-----
    #
    # 這四條目前每天各浪費一次模型呼叫：頁面抓得到，但模型看不出這一期的
    # 數字，於是正確地拒絕摘要。要修的是網址，不是模型。
    "RSAFS": [
        Candidate("https://www.census.gov/retail/marts/www/marts_current.html", "official",
                  "MARTS 當期發布的 HTML 版（目前指到 /retail/ 入口頁）"),
        Candidate("https://www.census.gov/retail/sales.html", "official",
                  "零售銷售當期頁"),
        Candidate("https://www.census.gov/economic-indicators/", "official",
                  "經濟指標總表，含當期發布連結"),
    ],
    "PCEPI": [
        Candidate("https://apps.bea.gov/newsreleases/national/pi/pinewsrelease.htm", "official",
                  "BEA 個人所得與支出新聞稿（固定網址，內容隨期別更新）"),
        Candidate("https://www.bea.gov/news/current-releases", "official",
                  "BEA 當期發布索引"),
    ],
    "PCEPILFE": [
        Candidate("https://apps.bea.gov/newsreleases/national/pi/pinewsrelease.htm", "official",
                  "同 PCEPI，核心數字在同一份新聞稿裡"),
        Candidate("https://www.bea.gov/news/current-releases", "official",
                  "BEA 當期發布索引"),
    ],
    "NFCI": [
        Candidate("https://www.chicagofed.org/publications/nfci/index", "official",
                  "NFCI 發布索引（目前指到 current-data，那頁多半是圖表外殼）"),
        Candidate("https://www.chicagofed.org/research/data/nfci/current-data", "official",
                  "現用網址，一併重測以確認診斷"),
    ],
})


def recent_month_names(as_of: Optional[date] = None, back: int = 3) -> list[str]:
    """最近幾個月的英文月份名。

    當期發布頁一定會提到參考月份；靜態說明頁不會。這是分辨兩者最便宜的方法。
    """
    today = as_of or date.today()
    out, cursor = [], today
    for _ in range(back):
        out.append(MONTHS[cursor.month - 1])
        cursor = cursor.replace(day=1) - timedelta(days=1)
    return out


def _same_site(requested: str, final: str) -> bool:
    """轉址之後還在同一個網域嗎。

    實測很多站台把不存在的頁面 302 回首頁再回 200——只看狀態碼，
    就會把首頁當成發布頁收下。
    """
    a, b = urlparse(requested).netloc.lower(), urlparse(final or requested).netloc.lower()
    a, b = a.removeprefix("www."), b.removeprefix("www.")
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def probe(fred_id: str, cand: Candidate, session=None, timeout: int = 30,
          as_of: Optional[date] = None) -> ProbeResult:
    """探測單一候選網址，回傳每一道檢查的實際結果。"""
    res = ProbeResult(fred_id=fred_id, url=cand.url, kind=cand.kind)
    http = session or requests
    try:
        resp = http.get(cand.url, headers=_HEADERS, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        res.verdict, res.reason = "error", f"{type(e).__name__}"
        return res

    res.status = getattr(resp, "status_code", None)
    res.final_url = str(getattr(resp, "url", "") or cand.url)
    if res.status != 200:
        res.verdict = "blocked" if res.status in (401, 403) else "http_error"
        res.reason = f"HTTP {res.status}"
        return res

    if not _same_site(cand.url, res.final_url):
        res.verdict, res.reason = "redirected", f"轉址到 {urlparse(res.final_url).netloc}"
        return res

    text = _strip_html(getattr(resp, "text", "") or "")
    res.text_len = len(text)
    if res.text_len < MIN_DOCUMENT_CHARS:
        res.verdict = "too_short"
        res.reason = f"內容 {res.text_len} 字，可能是 JS 外殼或錯誤頁"
        return res

    low = text.lower()
    res.topic_hits = [t for t in TOPIC_TERMS.get(fred_id, []) if t.lower() in low]
    if len(res.topic_hits) < MIN_TOPIC_HITS:
        res.verdict = "off_topic"
        res.reason = f"只命中 {len(res.topic_hits)} 個主題關鍵詞，內容可能與本指標無關"
        return res

    res.recent_months = [m for m in recent_month_names(as_of) if m in text]
    if not res.recent_months:
        res.verdict = "stale_page"
        res.reason = "未提到最近三個月，研判為靜態說明頁而非當期發布頁"
        return res

    res.verdict = "usable"
    res.reason = (f"{res.text_len} 字、主題命中 {len(res.topic_hits)}、"
                  f"提到 {'/'.join(res.recent_months)}")
    return res


def probe_all(candidates: Optional[dict] = None, session=None,
              as_of: Optional[date] = None) -> list[ProbeResult]:
    out = []
    for fred_id, cands in (candidates or CANDIDATES).items():
        for cand in cands:
            res = probe(fred_id, cand, session=session, as_of=as_of)
            log.info("%s %s -> %s（%s）", fred_id, cand.url, res.verdict, res.reason)
            out.append(res)
    return out


def to_markdown(results: list) -> str:
    """給人看的報告。按指標分組，每一列都要能回答「為什麼不能用」。"""
    lines = ["# 替代來源探測結果", "",
             "`usable` 才可以寫進 `news_ai.RELEASE_SOURCES`。",
             "本報告只陳述事實，不自動採用任何來源。", ""]
    by_id: dict = {}
    for r in results:
        by_id.setdefault(r.fred_id, []).append(r)

    ok = [r for r in results if r.usable]
    lines.append(f"共探測 {len(results)} 個網址，其中 **{len(ok)} 個可用**。")
    lines.append("")
    for fred_id, rows in by_id.items():
        good = [r for r in rows if r.usable]
        mark = "✅" if good else "❌"
        lines.append(f"## {mark} {fred_id}")
        lines.append("")
        lines.append("| 類型 | 網址 | 狀態 | 判定 | 說明 |")
        lines.append("|---|---|---:|---|---|")
        for r in rows:
            lines.append(f"| {r.kind} | `{r.url}` | {r.status or '—'} "
                         f"| **{r.verdict}** | {r.reason} |")
        lines.append("")
    return "\n".join(lines)
