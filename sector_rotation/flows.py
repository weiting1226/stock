"""各類股 ETF 的申贖流量＝資金動能。

沿用模組一 `etf_creation_flows` 的做法與防線：ETF 的流通股數只會因授權
參與者的申購／贖回而變動，因此「Δ股數 × 價格」就是流入／流出該類股的
金額本身，不是代理指標。

模組一算的是整體股票型 ETF 的**加總**，這裡要的是**逐檔**——輪動要看的
正是「錢從哪個類股出來、進到哪個類股」。因此重用它的資料結構與可信度
檢查，另寫逐檔的計算。

**陳舊資料的防線一併沿用**：實測 Yahoo 的 totalAssets 對 ETF 不是每日更新，
連兩天數值可能完全相同。回報 0 會被讀成「資金持平」——那是假訊號，
而且看起來完全正常。
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from liquidity_monitor.sources.etf_creation_flows import (
    EtfSnapshot,
    _as_float,
    fetch_snapshots,
    shares_are_consistent,
)

from .config import MAX_FLOW_GAP_DAYS

log = logging.getLogger(__name__)


def _days_between(a: str, b: str) -> int:
    try:
        return abs((pd.Timestamp(b) - pd.Timestamp(a)).days)
    except (ValueError, TypeError):
        return 0


def compute_sector_flows(
    today: list[EtfSnapshot],
    previous: dict[str, dict],
) -> tuple[dict, list]:
    """逐檔算資金流。回傳 ({ticker: 明細}, [(ticker, 略過原因)])。

    優先用流通股數（精確），股數不可信時退回淨資產分解（估計），
    並在每一筆標明用的是哪一種——兩種基礎的數字不可直接相比。
    """
    out, skipped = {}, []
    for snap in today:
        prev = previous.get(snap.ticker)
        if not snap.ok:
            skipped.append((snap.ticker, snap.error or "本日資料不完整"))
            continue
        if not prev:
            skipped.append((snap.ticker, "無前一次觀測"))
            continue

        gap = _days_between(prev.get("as_of", snap.as_of), snap.as_of)
        if gap == 0:
            skipped.append((snap.ticker, "與前一次觀測同日"))
            continue
        if gap > MAX_FLOW_GAP_DAYS:
            skipped.append((snap.ticker, f"距前次觀測 {gap} 天，過久不採計"))
            continue

        prev_shares = _as_float(prev.get("shares_outstanding"))
        prev_aum = _as_float(prev.get("total_assets"))
        prev_close = _as_float(prev.get("close"))

        flow_usd, basis = None, None
        if (prev_shares and snap.shares_outstanding
                and shares_are_consistent(snap.shares_outstanding, snap.close, snap.total_assets)
                and shares_are_consistent(prev_shares, prev_close, prev_aum)):
            flow_usd = (snap.shares_outstanding - prev_shares) * snap.close
            basis = "shares"
        elif prev_aum and prev_close and snap.total_assets:
            # 把市值變動中「漲跌造成的部分」扣掉，剩下的才是資金進出
            flow_usd = snap.total_assets - prev_aum * (snap.close / prev_close)
            basis = "aum"
        else:
            skipped.append((snap.ticker, "股數與淨資產都不足以計算"))
            continue

        out[snap.ticker] = {
            "flow_musd": round(flow_usd / 1e6, 2),
            # 佔自身淨資產的比例才是跨類股可比的量：XLK 規模是 XLRE 的十幾倍，
            # 比絕對金額等於在比誰的規模大，不是在比誰的資金動能強
            "flow_pct_of_aum": (round(100 * flow_usd / snap.total_assets, 4)
                                if snap.total_assets else None),
            "basis": basis,
            "days_spanned": gap,
            "aum_musd": round(snap.total_assets / 1e6, 0) if snap.total_assets else None,
            # 兩天的原始欄位，供陳舊判定使用
            "_raw": ((snap.shares_outstanding, snap.total_assets, snap.close),
                     (prev_shares, prev_aum, prev_close)),
        }
    return out, skipped


def stale_reason(flows: dict) -> Optional[str]:
    """整批來源沒更新時回報原因；正常則回 None。

    判斷依據放在原始欄位，不在算出來的差額——差額為零時，「真的沒有資金
    進出」與「來源那一欄沒重新整理」長得一模一樣（同模組一的教訓）。
    """
    keys = [t for t, d in flows.items() if d.get("_raw")]
    if not keys:
        return None

    if all(flows[t]["_raw"][0] == flows[t]["_raw"][1] for t in keys):
        return (f"{len(keys)} 檔的股數、淨資產與收盤價三欄全部與前一次觀測完全相同，"
                "研判為來源資料未更新，不採計為「資金持平」")

    aum_frozen_price_moved = [
        t for t in keys
        if flows[t]["basis"] == "aum"
        and flows[t]["_raw"][0][1] == flows[t]["_raw"][1][1]
        and flows[t]["_raw"][0][2] != flows[t]["_raw"][1][2]
    ]
    aum_based = [t for t in keys if flows[t]["basis"] == "aum"]
    if aum_based and len(aum_frozen_price_moved) == len(aum_based):
        return (f"{len(aum_based)} 檔的淨資產與前一次觀測完全相同、但收盤價已變動——"
                "持有股票的基金不可能如此，研判為來源的淨資產欄位未更新")
    return None


def strip_internals(flows: dict) -> dict:
    """把只給陳舊判定用的原始欄位拿掉，不送進報告。"""
    return {t: {k: v for k, v in d.items() if not k.startswith("_")} for t, d in flows.items()}
