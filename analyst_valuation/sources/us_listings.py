"""全美股上市公司清單。

主來源：**NASDAQ Trader Symbol Directory**（交易所官方每日更新，免金鑰）
  - nasdaqlisted.txt：NASDAQ 上市證券
  - otherlisted.txt ：NYSE / NYSE American / CBOE 等其他交易所
  兩檔皆為 pipe 分隔，且帶有 Test Issue 與 ETF 旗標，可直接濾掉測試代碼與 ETF。

備援：**SEC company_tickers.json**（含 CIK，但只涵蓋有申報的登記公司，
  且不含交易所與 ETF 旗標）。SEC 要求 User-Agent 帶聯絡資訊。

兩者都失敗才拋錯，錯誤訊息同時帶出兩邊原因，不用殘缺清單頂替。
"""
from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import asdict, dataclass
from typing import Optional

import requests

from ..security_type import classify_security

log = logging.getLogger(__name__)

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# SEC 的存取政策要求 User-Agent 表明身分與聯絡方式
_HEADERS = {"User-Agent": "stock-monitor/1.0 (github.com/weiting1226/stock)"}

MIN_EXPECTED_LISTINGS = 3000   # 全美股上市證券約 8000+；低於此數視為抓到殘缺檔案


@dataclass
class Listing:
    ticker: str
    name: str
    exchange: str
    is_etf: bool
    source: str
    # 證券種類（common／preferred／warrant／unit／right／note）。
    # 分類保留在清單裡，過濾在使用端做——這樣看得出當初濾掉了什麼、也隨時可改。
    security_type: str = "common"

    def as_dict(self) -> dict:
        return asdict(self)


def _clean(value: Optional[str]) -> str:
    return (value or "").strip()


def _parse_symbol_directory(text: str, symbol_col: str, exchange_label: str,
                            source: str) -> list[Listing]:
    """解析 NASDAQ Trader 的 pipe 分隔檔。

    檔尾有一行 "File Creation Time: ..."，欄位數不同，必須濾掉，
    否則會被當成一筆代碼為 "File Creation Time..." 的證券。
    """
    rows = list(csv.DictReader(io.StringIO(text), delimiter="|"))
    listings: list[Listing] = []
    for row in rows:
        symbol = _clean(row.get(symbol_col))
        if not symbol or symbol.lower().startswith("file creation time"):
            continue
        # Test Issue = Y 是交易所的測試代碼，不是真實證券
        if _clean(row.get("Test Issue")).upper() == "Y":
            continue
        listings.append(Listing(
            # 交易所用 BRK.B / 優先股用 XXX$Y，Yahoo Finance 用 BRK-B / XXX-PY
            ticker=symbol.replace(".", "-").replace("$", "-P"),
            name=_clean(row.get("Security Name")),
            exchange=_clean(row.get("Exchange")) or exchange_label,
            is_etf=_clean(row.get("ETF")).upper() == "Y",
            source=source,
            security_type=classify_security(
                symbol.replace(".", "-").replace("$", "-P"),
                _clean(row.get("Security Name")),
            ),
        ))
    return listings


def fetch_nasdaq_trader_listings(timeout: int = 60) -> list[Listing]:
    """抓取 NASDAQ Trader 的兩份清單並合併。"""
    out: list[Listing] = []
    for url, symbol_col, label in (
        (NASDAQ_LISTED_URL, "Symbol", "NASDAQ"),
        (OTHER_LISTED_URL, "ACT Symbol", "OTHER"),
    ):
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        parsed = _parse_symbol_directory(resp.text, symbol_col, label, "nasdaqtrader")
        log.info("%s -> %d 筆", url, len(parsed))
        out.extend(parsed)
    return out


def fetch_sec_listings(timeout: int = 60) -> list[Listing]:
    """備援：SEC 的 company_tickers.json（無交易所／ETF 資訊）。"""
    resp = requests.get(SEC_TICKERS_URL, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, dict):
        rows = list(payload.values())
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError(f"SEC 回傳非預期格式：{str(payload)[:120]}")

    listings = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = _clean(row.get("ticker")).replace(".", "-")
        if not ticker:
            continue
        listings.append(Listing(
            ticker=ticker,
            name=_clean(row.get("title")),
            exchange=_clean(row.get("exchange")) or "UNKNOWN",
            is_etf=False,       # SEC 這份資料沒有 ETF 旗標
            source="sec",
        ))
    return listings


def _dedupe(listings: list[Listing]) -> list[Listing]:
    """同一代碼可能在兩份檔案都出現，保留先出現者（NASDAQ 檔優先）。"""
    seen: dict[str, Listing] = {}
    for item in listings:
        if item.ticker and item.ticker not in seen:
            seen[item.ticker] = item
    return sorted(seen.values(), key=lambda x: x.ticker)


def fetch_us_listings(include_etf: bool = False, timeout: int = 60) -> list[Listing]:
    """回傳全美股上市證券清單（預設排除 ETF）。"""
    errors: list[str] = []
    listings: list[Listing] = []
    try:
        listings = fetch_nasdaq_trader_listings(timeout=timeout)
    except Exception as e:  # noqa: BLE001 — 主來源失敗要能退回備援
        errors.append(f"NASDAQ Trader: {type(e).__name__}: {e}")
        try:
            listings = fetch_sec_listings(timeout=timeout)
            log.warning("NASDAQ Trader 失敗，改用 SEC 備援清單（無ETF旗標）")
        except Exception as e2:  # noqa: BLE001
            errors.append(f"SEC: {type(e2).__name__}: {e2}")
            raise ValueError("美股上市清單所有來源皆失敗 -> " + " | ".join(errors)) from e2

    listings = _dedupe(listings)
    if not include_etf:
        listings = [x for x in listings if not x.is_etf]

    if len(listings) < MIN_EXPECTED_LISTINGS:
        raise ValueError(
            f"只解析出 {len(listings)} 檔（預期至少 {MIN_EXPECTED_LISTINGS} 檔），"
            "來源檔案可能不完整，拒絕以殘缺清單覆蓋既有資料"
        )
    return listings
