"""⑥ FOMC 決議偏向 — 自動判定升降息方向與是否有異議票。

聯準會新聞稿頁面格式多年來高度一致，適合規則式解析：
1. 從當年（必要時含前一年）的新聞稿索引頁找出最新一則貨幣政策聲明連結。
2. 用該次會議日期前後的聯邦資金目標區間上緣 (DFEDTARU) 判斷升息/降息/持平。
3. 在聲明全文中搜尋 "voting against" 字樣判斷是否有異議票。

這是規則式的最佳努力解析，不是語意理解；若聯準會改版新聞稿頁面格式，
本函式會拋出錯誤而非猜測結果，符合文件「不得臆測」原則。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import requests

from ..config import FED_PRESSRELEASE_INDEX

# 送出一般瀏覽器會送的標頭。原本的 UA 字串裡帶著 "scraper" 字樣，很多網站
# 會直接以此拒絕；這裡只是用常規的 UA 取一份公開的政府新聞稿，
# 與繞過驗證機制（例如 Cloudflare 的挑戰）不是同一回事。
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
# 聯準會的聲明連結歷史上是 monetary20260729a.htm，但也出現過 a1/b 等後綴，
# 且可能是絕對網址，所以放寬比對而不是只認單一形式。
_LINK_RE = re.compile(
    r'href="((?:https?://www\.federalreserve\.gov)?/newsevents/pressreleases/monetary\d{8}[a-z]\d*\.htm)"',
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"monetary(\d{4})(\d{2})(\d{2})[a-z]", re.IGNORECASE)
_ANY_MONETARY_RE = re.compile(r'href="([^"]*monetary[^"]*)"', re.IGNORECASE)


@dataclass
class FomcMeeting:
    date: pd.Timestamp
    statement_url: str
    statement_text: str
    has_dissent: bool


def _fetch_index(year: int) -> str:
    url = FED_PRESSRELEASE_INDEX.format(year=year)
    resp = requests.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def latest_meeting_on_or_before(as_of: str) -> Optional[FomcMeeting]:
    """回傳 `as_of` 當天已知的最近一次 FOMC 聲明；查無則拋出帶診斷資訊的錯誤。

    查無聲明時不能只回傳 None —— ⑥ 政策方向佔 30% 權重，靜默失效會讓
    整份報告的權重重分配悄悄改變，必須讓失敗原因浮上檯面。
    """
    as_of_ts = pd.Timestamp(as_of)
    candidates: list[tuple[pd.Timestamp, str]] = []
    seen_any: list[str] = []
    fetched_years: list[int] = []

    fetch_errors: list[str] = []
    for year in sorted({as_of_ts.year, as_of_ts.year - 1}, reverse=True):
        try:
            html = _fetch_index(year)
        except requests.RequestException as e:
            # 一定要留下實際原因。原本只是 continue，於是 403、逾時、DNS 失敗
            # 在畫面上全都變成同一句「網路或網站問題」，完全無從判斷該修什麼。
            status = getattr(getattr(e, "response", None), "status_code", None)
            detail = f"HTTP {status}" if status else f"{type(e).__name__}: {e}"
            fetch_errors.append(f"{year}年 {detail}")
            continue
        fetched_years.append(year)
        seen_any.extend(m.group(1) for m in _ANY_MONETARY_RE.finditer(html))
        for m in _LINK_RE.finditer(html):
            path = m.group(1)
            dm = _DATE_RE.search(path)
            if not dm:
                continue
            dt = pd.Timestamp(f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}")
            if dt <= as_of_ts:
                if path.startswith("http"):
                    candidates.append((dt, path))
                else:
                    candidates.append((dt, "https://www.federalreserve.gov" + path))

    if not candidates:
        if not fetched_years:
            raise ValueError(
                "聯準會新聞稿索引頁全部抓取失敗；各年度實際錯誤："
                + "｜".join(fetch_errors or ["無"])
            )
        raise ValueError(
            f"已取得 {fetched_years} 年索引頁，但找不到符合格式的 FOMC 聲明連結；"
            f"頁面上含 'monetary' 的連結範例：{seen_any[:3] or '無'}"
        )
    dt, url = max(candidates, key=lambda x: x[0])
    resp = requests.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    text = re.sub(r"<[^>]+>", " ", resp.text)
    text = re.sub(r"\s+", " ", text)
    has_dissent = bool(re.search(r"voting against", text, re.IGNORECASE))
    return FomcMeeting(date=dt, statement_url=url, statement_text=text, has_dissent=has_dissent)


def score_fomc_decision(meeting: FomcMeeting, target_upper_before: float, target_upper_after: float) -> int:
    """依文件第135行的表格計分：
    降息無異議:+2／降息有異議:+1／持平無異議:0／持平但有異議:-1／升息:-2
    """
    diff = round(target_upper_after - target_upper_before, 4)
    if diff < 0:
        return 1 if meeting.has_dissent else 2
    if diff > 0:
        return -2
    return -1 if meeting.has_dissent else 0
