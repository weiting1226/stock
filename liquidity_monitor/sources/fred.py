"""FRED (Federal Reserve Economic Data) 抓取器。

使用免金鑰的公開端點，不需申請 API key。

**歷史長度的實測結論**（2026-08，已由診斷確認）：

  BAMLH0A0HYM2  帶區間 785 筆／2023-08-11，不帶區間**完全相同**
  SOFR          2086 筆／2018-04-03  <- 該利率本身的起始日，非截斷
  IORB          1840 筆／2021-07-29  <- 同上

也就是說 CSV 端點並沒有通用的長度限制，是 **BAMLH0A0HYM2 這一條被來源限制
在近三年**（ICE BofA 為授權資料）。曾另外嘗試 `fred.stlouisfed.org/data/<ID>.txt`
作為第三條路，實測三條序列全部回傳 HTML 頁面——那個端點根本不存在，已移除。
留著只會讓「試過三個端點」變成一句假話，而且每次多打一次沒有用的請求。

仍保留「不帶區間再試一次」：它便宜，而且哪天某條序列真的是被參數截斷時
就會派上用場。每個端點的結果都記進 FetchDiagnostics——少了它，
「來源只給得出這麼多」與「我們少帶了一個參數」在畫面上長得一模一樣。
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import requests

log = logging.getLogger(__name__)

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0 (liquidity-monitor-v3 scraper)"})

# 抓回來的歷史比要求的區間短這麼多天以上，就認定「這個端點被截斷了」，
# 值得再試下一個。設一年是因為序列本身可能就是這幾年才開始（例如 IORB
# 2021 才有），那種情況不是截斷，重試也沒用。
TRUNCATION_TOLERANCE_DAYS = 365


@dataclass
class FetchDiagnostics:
    """每個端點各拿到什麼，供事後判斷是來源限制還是我們用錯方式。"""

    series_id: str
    requested_start: str
    attempts: list = field(default_factory=list)
    chosen: Optional[str] = None

    def note(self, endpoint: str, series: Optional[pd.Series], error: str = None) -> None:
        self.attempts.append({
            "endpoint": endpoint,
            "rows": 0 if series is None else int(len(series)),
            "start": None if series is None or series.empty else str(series.index.min().date()),
            "error": error,
        })


def _parse_csv(text: str, series_id: str) -> pd.Series:
    if text.strip().startswith("<") or "series does not exist" in text.lower():
        raise ValueError(f"FRED 系列 '{series_id}' 無法解析（代碼錯誤或已下架）")
    df = pd.read_csv(io.StringIO(text))
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    s = df.dropna().set_index("date")["value"].sort_index()
    s.name = series_id
    return s



def _is_truncated(series: pd.Series, start: str) -> bool:
    if series is None or series.empty:
        return True
    gap = (series.index.min() - pd.Timestamp(start)).days
    return gap > TRUNCATION_TOLERANCE_DAYS


def fetch_fred_series(
    series_id: str,
    start: str,
    end: str,
    timeout: int = 30,
    diagnostics: FetchDiagnostics = None,
) -> pd.Series:
    """抓取單一 FRED 系列，回傳以日期為索引的 Series（已去除缺值列）。

    先用帶區間的 CSV；若回傳的歷史明顯偏短，再試一次不帶區間的，取較長的一份。
    """
    diag = diagnostics if diagnostics is not None else FetchDiagnostics(series_id, start)
    best: Optional[pd.Series] = None
    best_name = None
    errors = []

    def consider(name: str, series: Optional[pd.Series]) -> None:
        nonlocal best, best_name
        diag.note(name, series)
        if series is None or series.empty:
            return
        if best is None or series.index.min() < best.index.min():
            best, best_name = series, name

    # 1) 帶區間的 CSV：正常情況下這個就夠了
    try:
        r = _SESSION.get(FRED_CSV_URL, params={"id": series_id, "cosd": start, "coed": end},
                         timeout=timeout)
        r.raise_for_status()
        consider("csv_with_range", _parse_csv(r.text, series_id))
    except Exception as e:  # noqa: BLE001
        errors.append(f"csv_with_range: {type(e).__name__}: {e}")
        diag.note("csv_with_range", None, str(e))

    # 2) 不帶區間的 CSV：實測有些系列帶了 cosd 反而只回傳近幾年
    if _is_truncated(best, start):
        try:
            r = _SESSION.get(FRED_CSV_URL, params={"id": series_id}, timeout=timeout)
            r.raise_for_status()
            consider("csv_full", _parse_csv(r.text, series_id))
        except Exception as e:  # noqa: BLE001
            errors.append(f"csv_full: {type(e).__name__}: {e}")
            diag.note("csv_full", None, str(e))

    if best is None:
        raise ValueError(f"FRED 系列 '{series_id}' 兩個端點都取不到資料：{'；'.join(errors)}")

    diag.chosen = best_name
    if best_name != "csv_with_range":
        log.info("FRED %s 改用 %s 端點（歷史自 %s 起，較長）",
                 series_id, best_name, best.index.min().date())

    return best.loc[(best.index >= pd.Timestamp(start)) & (best.index <= pd.Timestamp(end))]
