"""FINRA 融資餘額 (margin debt) 抓取器。

FINRA 於
https://www.finra.org/investors/learn-to-invest/advanced-investing/margin-statistics
公布月底融資借方餘額統計，歷史上以 HTML 表格呈現，但版面曾多次調整。
若頁面結構已變更導致解析失敗，本函式會拋出清楚的錯誤訊息而非回傳錯誤數字；
此時可用 `override_csv` 參數改用人工下載的 CSV（欄位：date, margin_debt）。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
import requests

FINRA_URL = "https://www.finra.org/investors/learn-to-invest/advanced-investing/margin-statistics"

_HEADERS = {"User-Agent": "Mozilla/5.0 (liquidity-monitor-v3 scraper)"}


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
    tables = pd.read_html(resp.text)
    table = _find_margin_table(tables)

    period_col = next(c for c in table.columns if any(k in str(c).lower() for k in ("month", "year", "date")))
    debit_col = next(c for c in table.columns if "debit" in str(c).lower())

    out = table[[period_col, debit_col]].copy()
    out.columns = ["date", "value"]
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["value"] = (
        out["value"].astype(str).str.replace(",", "", regex=False).str.replace("$", "", regex=False)
    )
    out["value"] = pd.to_numeric(out["value"], errors="coerce") * 1_000_000  # 百萬美元 -> 美元
    out = out.dropna().set_index("date").sort_index()["value"]

    return out.loc[start:end].rename("MARGIN_DEBT")
