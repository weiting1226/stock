"""FRED (Federal Reserve Economic Data) 抓取器。

使用免金鑰的公開端點，不需申請 API key。

**為什麼要試多個端點**：實測 2026-08，同一次請求裡 DGS10 正確回傳 2008 年
起的完整歷史，BAMLH0A0HYM2（ICE BofA 高收益債利差）卻只回傳近三年——
明明帶了同樣的 `cosd=2008-08-12`。回測因此有 80% 的期間缺這條線，而畫面上
完全看不出來：最新值是對的，計分也照常運作，只是歷史短得離譜。

因此改成依序嘗試數個端點，**取回傳歷史最長的那一份**，並把每個端點的結果
記錄下來。少了這層，「來源只給得出這麼多」與「我們少帶了一個參數」在畫面上
長得一模一樣。
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
FRED_TXT_URL = "https://fred.stlouisfed.org/data/{series_id}.txt"

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


def _parse_txt(text: str, series_id: str) -> pd.Series:
    """解析 FRED 的純文字資料檔。

    格式是一段中繼資料、一行 `DATE  VALUE` 標頭，然後是空白分隔的資料列。
    只收「開頭看起來像日期」的行，其餘（標題、說明、來源註記）一律略過。
    """
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or len(parts[0]) != 10 or parts[0][4] != "-":
            continue
        date = pd.to_datetime(parts[0], errors="coerce")
        if date is pd.NaT or pd.isna(date):
            continue
        value = pd.to_numeric(parts[1], errors="coerce")
        if pd.isna(value):
            continue                      # FRED 用 "." 表示缺值
        rows.append((date, float(value)))
    if not rows:
        raise ValueError(f"FRED 純文字端點沒有解析出任何資料列（{series_id}）")
    s = pd.Series(dict(rows)).sort_index()
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

    依序嘗試三個端點，取歷史最長的一份；全部失敗才拋出。
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

    # 3) 純文字資料檔：格式最原始，也最不容易被繪圖端點的參數影響
    if _is_truncated(best, start):
        try:
            r = _SESSION.get(FRED_TXT_URL.format(series_id=series_id), timeout=timeout)
            r.raise_for_status()
            consider("txt", _parse_txt(r.text, series_id))
        except Exception as e:  # noqa: BLE001
            errors.append(f"txt: {type(e).__name__}: {e}")
            diag.note("txt", None, str(e))

    if best is None:
        raise ValueError(f"FRED 系列 '{series_id}' 三個端點都取不到資料：{'；'.join(errors)}")

    diag.chosen = best_name
    if best_name != "csv_with_range":
        log.info("FRED %s 改用 %s 端點（歷史自 %s 起，較長）",
                 series_id, best_name, best.index.min().date())

    return best.loc[(best.index >= pd.Timestamp(start)) & (best.index <= pd.Timestamp(end))]
