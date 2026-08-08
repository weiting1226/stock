"""FINRA 融資餘額 (margin debt) 抓取器。

FINRA 於
https://www.finra.org/investors/learn-to-invest/advanced-investing/margin-statistics
公布月底融資借方餘額統計，歷史上以 HTML 表格呈現，但版面曾多次調整。
若頁面結構已變更導致解析失敗，本函式會拋出清楚的錯誤訊息而非回傳錯誤數字；
此時可用 `override_csv` 參數改用人工下載的 CSV（欄位：date, margin_debt）。
"""
from __future__ import annotations

import io
import re
from typing import Optional

import pandas as pd
import requests

FINRA_URL = "https://www.finra.org/investors/learn-to-invest/advanced-investing/margin-statistics"

_HEADERS = {"User-Agent": "Mozilla/5.0 (liquidity-monitor-v3 scraper)"}

_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_NAME_RE = re.compile(r"^([A-Za-z]{3,9})[\s\-/.]+(\d{2,4})$")
_YEAR_FIRST_RE = re.compile(r"^(\d{4})[\-/](\d{1,2})$")
_MONTH_FIRST_RE = re.compile(r"^(\d{1,2})[\-/](\d{4})$")


def _month_end(year: int, month: int) -> Optional[pd.Timestamp]:
    if not 1 <= month <= 12 or not 1900 <= year <= 2200:
        return None
    return pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)


def _parse_period(value) -> Optional[pd.Timestamp]:
    """把 FINRA 的參考月份字串解析成該月月底日期。

    必須自己解析、不能直接丟給 pd.to_datetime：FINRA 慣用的 "Jun-25" 會被
    pandas 解讀成「公元 1 年 6 月 25 日」，接著被日期區間過濾掉，整個序列
    變成空的卻不報錯 —— 這正是這個指標長期顯示「暫缺」的原因。
    """
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", ""}:
        return None

    m = _MONTH_NAME_RE.match(s)
    if m:
        month = _MONTH_ABBR.get(m.group(1)[:3].lower())
        year = int(m.group(2))
        if year < 100:  # "Jun-25" -> 2025（FINRA 只發布近代資料）
            year += 2000
        if month:
            return _month_end(year, month)

    m = _YEAR_FIRST_RE.match(s)
    if m:
        return _month_end(int(m.group(1)), int(m.group(2)))

    m = _MONTH_FIRST_RE.match(s)
    if m:
        return _month_end(int(m.group(2)), int(m.group(1)))

    # 其餘格式（如 "June 30, 2025"）交給 pandas，但拒收被誤判成古代年份的結果
    parsed = pd.to_datetime(s, errors="coerce")
    if parsed is not pd.NaT and not pd.isna(parsed) and parsed.year >= 1900:
        return _month_end(parsed.year, parsed.month)
    return None


def _find_margin_table(tables: list[pd.DataFrame]) -> pd.DataFrame:
    for t in tables:
        cols = [str(c).strip().lower() for c in t.columns]
        has_debit = any("debit" in c for c in cols)
        has_period = any(("month" in c) or ("year" in c) or ("date" in c) for c in cols)
        if has_debit and has_period:
            return t
    raise ValueError(
        "無法從 FINRA 頁面找到含 'Debit' 與 'Month/Year' 欄位的表格；"
        "網站結構可能已變更，請人工檢查頁面並更新 finra_margin._find_margin_table，"
        "或改用 override_csv 參數提供手動下載的資料。"
    )


def fetch_margin_debt(
    start: str,
    end: str,
    timeout: int = 30,
    override_csv: Optional[str] = None,
) -> pd.Series:
    """回傳以月底參考日期為索引的融資餘額 Series（單位：美元）。

    FINRA 原始單位為百萬美元，此處換算為美元以利跟其他金額類指標統一。
    索引日期是「參考月份」，*不是*發布日期 —— 發布時滯的處理在 pipeline.py
    依 config.FINRA_PUBLISH_LAG_DAYS 統一套用。
    """
    if override_csv:
        df = pd.read_csv(override_csv)
        df.columns = [c.strip().lower() for c in df.columns]
        s = df.set_index(pd.to_datetime(df["date"]))["margin_debt"].sort_index()
        return s.loc[start:end].astype(float).rename("MARGIN_DEBT")

    resp = requests.get(FINRA_URL, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))  # pandas>=2.1 不再接受純字串
    table = _find_margin_table(tables)

    period_col = next(c for c in table.columns if any(k in str(c).lower() for k in ("month", "year", "date")))
    debit_col = next(c for c in table.columns if "debit" in str(c).lower())

    out = table[[period_col, debit_col]].copy()
    out.columns = ["date", "value"]
    raw_periods = out["date"].astype(str).tolist()
    out["date"] = out["date"].map(_parse_period)
    out["value"] = (
        out["value"].astype(str).str.replace(",", "", regex=False).str.replace("$", "", regex=False)
    )
    out["value"] = pd.to_numeric(out["value"], errors="coerce") * 1_000_000  # 百萬美元 -> 美元
    out = out.dropna().set_index("date").sort_index()["value"]

    if out.empty:
        raise ValueError(
            "FINRA 表格解析後沒有任何有效資料列；參考月份欄位樣本："
            f"{raw_periods[:3]}。請更新 finra_margin._parse_period。"
        )

    sliced = out.loc[start:end]
    if sliced.empty:
        raise ValueError(
            f"FINRA 資料解析成功但全部落在 {start}~{end} 之外"
            f"（實際範圍 {out.index.min().date()}~{out.index.max().date()}）。"
        )
    return sliced.rename("MARGIN_DEBT")
