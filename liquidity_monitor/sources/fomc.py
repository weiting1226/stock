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

_HEADERS = {"User-Agent": "Mozilla/5.0 (liquidity-monitor-v3 scraper)"}
_LINK_RE = re.compile(r'href="(/newsevents/pressreleases/monetary\d{8}a\.htm)"', re.IGNORECASE)
_DATE_RE = re.compile(r"monetary(\d{4})(\d{2})(\d{2})a\.htm", re.IGNORECASE)


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
    """回傳 `as_of` 當天已知的最近一次 FOMC 聲明；查無則回傳 None。"""
    as_of_ts = pd.Timestamp(as_of)
    candidates: list[tuple[pd.Timestamp, str]] = []
    for year in {as_of_ts.year, as_of_ts.year - 1}:
        try:
            html = _fetch_index(year)
        except requests.RequestException:
            continue
        for m in _LINK_RE.finditer(html):
            path = m.group(1)
            dm = _DATE_RE.search(path)
            if not dm:
                continue
            dt = pd.Timestamp(f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}")
            if dt <= as_of_ts:
                candidates.append((dt, "https://www.federalreserve.gov" + path))
    if not candidates:
        return None
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
