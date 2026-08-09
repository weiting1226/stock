"""每日執行的主流程：抓資料 -> 建立每日對齊面板 -> 算特徵 -> 計分 -> 套用閘門 -> 組裝報告。

對應文件「一、資料抓取規範」到「五、部位配置階梯」的機械化實作。
⑥ 的 FOMC 判定與 Gate B 百分位使用抓取到的歷史資料；⑤ 股票型ETF資金流由
ETF 流通股數變化自行計算（失敗才退回人工值）；CME FedWatch 與 NDX前瞻本益比因無可靠
免費資料源，讀取 `manual_overrides.json`，沒有填寫就標記「暫缺」，
绝不臆測（文件第287行）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import pandas as pd

from . import gates, scoring, storage
from .config import (
    CATEGORY_ITEMS,
    CATEGORY_LABELS,
    FINRA_PUBLISH_LAG_DAYS,
    FRED_SERIES,
    HISTORY_START,
    ITEM_LABELS,
    LIGHT_BANDS,
    MANUAL_OVERRIDE_DEFAULT_MAX_AGE_DAYS,
    MANUAL_OVERRIDE_ITEMS,
    MANUAL_OVERRIDE_MAX_AGE_DAYS,
    POSITION_LADDER,
    YAHOO_TICKERS,
)
from .sources import (
    etf_creation_flows,
    etf_flows,
    finra_margin,
    fomc,
    fred,
    ndx_breadth,
    tradingview_ndx,
    yahoo,
)
from .timeseries import (
    apply_publish_lag,
    is_trailing_max,
    n_trading_day_change,
    nyse_trading_days,
    to_daily_panel,
    yoy,
)

log = logging.getLogger(__name__)


def _lagged_yoy_daily(raw: pd.Series, lag_days: int, trading_days) -> pd.Series:
    """先算年增率，**再套用發布時滯**，最後才對齊到每個交易日。

    順序很重要：年增率必須從原始（未位移）序列計算，否則等於拿位移後的
    日期去對位移後的日期；但算完之後一定要套用時滯，否則當日就看得到
    尚未公布的參考月數值 —— 這正是文件第38-48行禁止的前視偏誤。
    """
    if raw is None or raw.empty:
        return pd.Series(dtype=float)
    return to_daily_panel(apply_publish_lag(yoy(raw), lag_days), trading_days)


def _short_error(msg: str, limit: int = 160) -> str:
    """把例外訊息壓成單行、限制長度，避免抓取失敗時把整頁HTML/JS塞進UI的note欄位。"""
    flat = " ".join(str(msg).split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


class Item:
    __slots__ = ("key", "raw_value", "score", "data_date", "source", "confidence", "note")

    def __init__(self, key, raw_value=None, score=None, data_date=None, source="", confidence="暫缺", note=""):
        self.key = key
        self.raw_value = raw_value
        self.score = score
        self.data_date = data_date
        self.source = source
        self.confidence = confidence
        self.note = note

    def as_dict(self):
        return {
            "key": self.key,
            "label": ITEM_LABELS.get(self.key, self.key),
            "raw_value": self.raw_value,
            "score": self.score,
            "data_date": str(self.data_date) if self.data_date is not None else None,
            "source": self.source,
            "confidence": self.confidence,
            "note": self.note,
        }


def _fetch_fred_panel(as_of: str) -> dict[str, pd.Series]:
    trading_days = nyse_trading_days(HISTORY_START, as_of)
    panel = {}
    for series_id, lag in FRED_SERIES.items():
        try:
            raw = fred.fetch_fred_series(series_id, HISTORY_START, as_of)
            lagged = apply_publish_lag(raw, lag)
            panel[series_id] = {"raw": raw, "daily": to_daily_panel(lagged, trading_days)}
        except Exception as e:  # noqa: BLE001 - 缺一項不能讓整批失敗
            log.warning("FRED %s 抓取失敗: %s", series_id, e)
            panel[series_id] = {"raw": pd.Series(dtype=float), "daily": pd.Series(dtype=float, index=trading_days)}
    return panel


def _fetch_yahoo_panel(as_of: str) -> dict[str, pd.Series]:
    panel = {}
    for ticker, name in YAHOO_TICKERS.items():
        try:
            panel[name] = yahoo.fetch_yahoo_close(ticker, HISTORY_START, as_of)
        except Exception as e:  # noqa: BLE001
            log.warning("Yahoo %s 抓取失敗: %s", ticker, e)
            panel[name] = pd.Series(dtype=float)
    return panel


def _fetch_margin_debt(as_of: str, override_csv: Optional[str] = None) -> dict:
    trading_days = nyse_trading_days(HISTORY_START, as_of)
    try:
        raw = finra_margin.fetch_margin_debt(HISTORY_START, as_of, override_csv=override_csv)
        lagged = apply_publish_lag(raw, FINRA_PUBLISH_LAG_DAYS)
        return {"raw": raw, "daily": to_daily_panel(lagged, trading_days), "ok": True}
    except Exception as e:  # noqa: BLE001
        log.warning("FINRA 融資餘額抓取失敗: %s", e)
        return {"raw": pd.Series(dtype=float), "daily": pd.Series(dtype=float, index=trading_days), "ok": False, "error": str(e)}


def _fetch_ndx_breadth(as_of: str, cache_path: str) -> tuple[Optional[float], Optional[str], dict]:
    """回傳 (廣度百分比, 錯誤訊息, 明細)。錯誤訊息會一路帶到報告的 note 欄位，
    否則抓取失敗在儀表板上看起來跟「正常但沒值」一模一樣。

    先用 TradingView：它一個請求就給出成分股與各條均線，取代「清單來源 +
    約100次逐檔下載」那整段。失敗才退回原本的路徑（Invesco QQQ 持股 →
    維基百科 → 過期快取，再自行以 yfinance 計算），並把兩邊的原因都帶出來。
    """
    try:
        breadth, components = tradingview_ndx.fetch_ndx_breadth()
        breadth["source"] = "tradingview"
        breadth["as_of"] = as_of
        return breadth["pct_above_200dma"], None, breadth
    except Exception as e:  # noqa: BLE001 — 主來源失敗要能退回備援
        primary_error = f"TradingView: {type(e).__name__}: {e}"
        log.warning("TradingView NDX 廣度失敗，改用備援來源: %s", e)

    try:
        tickers = ndx_breadth.fetch_constituents(cache_path=cache_path)
        value = ndx_breadth.compute_breadth_200d(tickers, as_of)
        if value is None:
            return None, (f"{primary_error}｜備援：取得 {len(tickers)} 檔成分股，"
                          "但收盤價歷史不足以計算200日均線"), {}
        return value, None, {"source": "fallback", "as_of": as_of,
                             "constituents": len(tickers), "pct_above_200dma": value}
    except Exception as e:  # noqa: BLE001
        log.warning("NDX 廣度計算失敗: %s", e)
        return None, f"{primary_error}｜備援 {type(e).__name__}: {e}", {}


def _flow_observation(result) -> "etf_flows.FlowObservation":
    """把計算結果轉成歷史檔用的觀測記錄（沿用既有的累積格式）。"""
    return etf_flows.FlowObservation(
        as_of=result.as_of,
        # 歷史檔存的是計分用的量（佔樣本淨資產比例），否則歷史與當日不同單位
        net_flow_musd=(result.flow_pct_of_aum if result.flow_pct_of_aum is not None
                       else result.net_flow_musd),
        scope=f"主要股票型ETF×{result.etfs_used}（{result.basis}）",
        period=f"{result.days_spanned}日",
        source_url=f"computed:{result.basis}",
    )


def _build_etf_flow_item(as_of: str, manual: dict) -> "Item":
    """⑤ 股票型 ETF 資金流：由 ETF 流通股數變化自行計算，人工填寫值為備援。

    ETF 的流通股數只會因申購／贖回而變動，因此「Δ股數 × 價格」就是資金流的
    定義本身，不是代理指標。原本規劃的 ETF.com 從 Actions 存取時三個位址
    全部回 403（Cloudflare 擋資料中心 IP），故改為自行計算。

    算不出來才退回 manual_overrides.json，並在 note 裡把原因講清楚，
    否則使用者只會看到「暫缺」而不知道要去修什麼。
    """
    snapshots: list = []
    try:
        snapshots = etf_creation_flows.fetch_snapshots(as_of=as_of)
        previous = storage.load_previous_etf_snapshots(before=as_of)
        result = etf_creation_flows.compute_flow(snapshots, previous)
    except Exception as e:  # noqa: BLE001 — 算不出來要能退回人工值
        log.warning("ETF 資金流計算失敗: %s", e)
        # 就算算不出流量，抓到的快照仍要存下來——第一次執行必然沒有前一次觀測，
        # 這時若不落地，明天一樣沒得比，會永遠卡在「無前一次觀測」
        storage.save_etf_snapshots(snapshots)
        fallback = manual.get("etf_fund_flow")
        if fallback is not None and fallback.score is not None:
            fallback.note = (f"（自動計算失敗，採用人工填寫值）{fallback.note}"
                             f"｜失敗原因：{_short_error(e, 300)}")
            return fallback
        return Item("etf_fund_flow", None, None, None, "ETF 申贖流量（計算失敗）", "暫缺",
                    note=f"自動計算失敗：{_short_error(e, 400)}"
                         "｜可於 docs/data/manual_overrides.json 手動填入 -2..2 分")

    storage.save_etf_snapshots(snapshots)
    # 歷史要排除今天自己那筆，否則等於部分拿自己當比較基準
    history = storage.load_etf_flow_history(before=as_of)
    storage.append_etf_flow(_flow_observation(result))
    # 計分用「佔樣本淨資產的比例」而不是絕對金額：樣本組成有增減時金額會跳動，
    # 比例不會——而計分完全依賴跟自己的歷史比
    scored_value = (result.flow_pct_of_aum if result.flow_pct_of_aum is not None
                    else result.net_flow_musd)
    score = scoring.score_etf_fund_flow(scored_value, history)

    enough = len(history) >= scoring.MIN_FLOW_HISTORY_FOR_MAGNITUDE
    basis_label = "流通股數變化" if result.basis == "shares" else "淨資產變動扣除市場漲跌（估計）"
    note = (f"{result.etfs_used} 檔主要股票型 ETF 以{basis_label}計算："
            f"淨{'流入' if result.net_flow_musd >= 0 else '流出'} "
            f"{abs(result.net_flow_musd):,.0f} 百萬美元")
    if result.flow_pct_of_aum is not None:
        note += f"（佔樣本淨資產 {result.flow_pct_of_aum:+.3f}%，計分以此為準）"
    if result.days_spanned > 1:
        note += f"（跨 {result.days_spanned} 天，已攤平為每日）"
    if enough:
        note += f"；以自身 {len(history)} 筆歷史規模判定強度"
    else:
        # 少說一級也不要硬給 ±2：方向是確定的，「大幅與否」還沒有依據
        note += (f"；歷史僅 {len(history)} 筆（需 {scoring.MIN_FLOW_HISTORY_FOR_MAGNITUDE} 筆），"
                 "目前只判方向不判強度")
    if result.etfs_skipped:
        note += f"｜略過 {len(result.etfs_skipped)} 檔：{result.etfs_skipped[:3]}"

    return Item("etf_fund_flow", result.net_flow_musd, score, result.as_of,
                "ETF 申贖流量（Yahoo 流通股數／淨資產自行計算）",
                "高" if enough and result.basis == "shares" else "中", note=note)


def _fetch_fomc(as_of: str, dfedtaru_daily: pd.Series) -> tuple[Optional[int], dict]:
    try:
        meeting = fomc.latest_meeting_on_or_before(as_of)
        if meeting is None:
            return None, {"note": "查無FOMC聲明", "data_date": None}
        before = dfedtaru_daily.loc[:meeting.date - pd.Timedelta(days=1)]
        after = dfedtaru_daily.loc[:meeting.date]
        if before.empty or after.empty:
            return None, {"note": "缺聯邦資金目標區間資料", "data_date": str(meeting.date.date())}
        target_before = float(before.iloc[-1])
        target_after = float(after.iloc[-1])
        score = fomc.score_fomc_decision(meeting, target_before, target_after)
        return score, {
            "note": f"{'有異議' if meeting.has_dissent else '無異議'}；目標區間上緣 {target_before}%→{target_after}%",
            "data_date": str(meeting.date.date()),
            "url": meeting.statement_url,
        }
    except Exception as e:  # noqa: BLE001
        log.warning("FOMC 判定失敗: %s", e)
        return None, {"note": f"抓取失敗: {e}", "data_date": None}


def _load_manual_overrides(path: str, as_of: str) -> dict[str, Item]:
    import json
    from pathlib import Path

    items = {}
    p = Path(path)
    if not p.exists():
        return items
    data = json.loads(p.read_text())
    for key, entry in data.items():
        if key not in MANUAL_OVERRIDE_ITEMS and key != "ndx_fwd_pe":
            continue
        value = entry.get("score" if key != "ndx_fwd_pe" else "value")
        asof_date = entry.get("as_of")
        if value is None:
            continue
        max_age = MANUAL_OVERRIDE_MAX_AGE_DAYS.get(key, MANUAL_OVERRIDE_DEFAULT_MAX_AGE_DAYS)
        stale = False
        if asof_date:
            try:
                age = (pd.Timestamp(as_of) - pd.Timestamp(asof_date)).days
                stale = age > max_age
            except Exception:  # noqa: BLE001
                pass
        items[key] = Item(
            key=key,
            raw_value=value,
            # 過期就不採計分數，否則等於拿陳舊資料當今日訊號
            score=(int(value) if key != "ndx_fwd_pe" else None) if not stale else None,
            data_date=asof_date,
            source="人工手動輸入 (manual_overrides.json)",
            confidence="低" if not stale else f"暫缺(逾{max_age}天未更新)",
            note=entry.get("note", ""),
        )
    return items


def run(as_of: Optional[str] = None, margin_override_csv: Optional[str] = None,
        overrides_path: str = "docs/data/manual_overrides.json",
        ndx_cache_path: str = "docs/data/ndx100_constituents.json") -> dict:
    as_of = as_of or datetime.utcnow().strftime("%Y-%m-%d")

    fred_panel = _fetch_fred_panel(as_of)
    yahoo_panel = _fetch_yahoo_panel(as_of)
    margin = _fetch_margin_debt(as_of, override_csv=margin_override_csv)
    ndx_breadth_pct, ndx_breadth_error, ndx_breadth_detail = _fetch_ndx_breadth(as_of, ndx_cache_path)
    manual = _load_manual_overrides(overrides_path, as_of)

    def fred_daily(sid):
        return fred_panel.get(sid, {}).get("daily", pd.Series(dtype=float))

    def fred_raw(sid):
        return fred_panel.get(sid, {}).get("raw", pd.Series(dtype=float))

    items: dict[str, Item] = {}

    # ① 總體貨幣流動性 ------------------------------------------------------
    walcl_yoy_daily = _lagged_yoy_daily(fred_raw("WALCL"), FRED_SERIES["WALCL"], fred_daily("WALCL").index)
    fed_bs_3m_chg = n_trading_day_change(walcl_yoy_daily, 63)
    items["fed_bs_3m_chg"] = Item("fed_bs_3m_chg", fed_bs_3m_chg, scoring.score_fed_bs_3m_chg(fed_bs_3m_chg),
                                   as_of, "FRED WALCL", "高" if fed_bs_3m_chg is not None else "暫缺")

    rrp_daily = fred_daily("RRPONTSYD")
    rrp_latest = rrp_daily.dropna().iloc[-1] if not rrp_daily.dropna().empty else None
    rrp_12m = rrp_daily.dropna().tail(252)
    dormant = bool(len(rrp_12m) > 0 and rrp_12m.max() < 100e9)
    rrp_pctile = None
    if rrp_latest is not None and not dormant and len(rrp_12m) >= 60:
        rrp_pctile = round(100.0 * (rrp_12m < rrp_latest).sum() / len(rrp_12m), 2)
    items["on_rrp_pctile"] = Item("on_rrp_pctile", rrp_pctile, scoring.score_on_rrp_pctile(rrp_pctile, dormant),
                                   as_of, "FRED RRPONTSYD", "高" if (rrp_pctile is not None or dormant) else "暫缺",
                                   note="結構性休眠(12個月皆<$100B)" if dormant else "")

    m2_yoy_daily = _lagged_yoy_daily(fred_raw("M2SL"), FRED_SERIES["M2SL"], fred_daily("M2SL").index)
    m2_3m_chg = n_trading_day_change(m2_yoy_daily, 63)
    items["m2_yoy_3m_chg"] = Item("m2_yoy_3m_chg", m2_3m_chg, scoring.score_m2_yoy_3m_chg(m2_3m_chg),
                                   as_of, "FRED M2SL", "中" if m2_3m_chg is not None else "暫缺",
                                   note="月頻，已套用約4週發布時滯")

    # ② 資金成本與信用 ------------------------------------------------------
    hy_oas_daily = fred_daily("BAMLH0A0HYM2")
    hy_oas_level = hy_oas_daily.dropna().iloc[-1] if not hy_oas_daily.dropna().empty else None
    items["hy_oas_level"] = Item("hy_oas_level", hy_oas_level, scoring.score_hy_oas_level(hy_oas_level),
                                  as_of, "FRED BAMLH0A0HYM2", "高" if hy_oas_level is not None else "暫缺")

    hy_oas_20d_chg = n_trading_day_change(hy_oas_daily, 20)
    hy_oas_20d_chg_bp = hy_oas_20d_chg * 100 if hy_oas_20d_chg is not None else None
    items["hy_oas_20d_chg"] = Item("hy_oas_20d_chg", hy_oas_20d_chg_bp, scoring.score_hy_oas_20d_chg(hy_oas_20d_chg_bp),
                                    as_of, "FRED BAMLH0A0HYM2", "高" if hy_oas_20d_chg_bp is not None else "暫缺")

    dgs2, dgs10, dgs30 = fred_daily("DGS2"), fred_daily("DGS10"), fred_daily("DGS30")
    t2s10s_bp = None
    if not dgs2.dropna().empty and not dgs10.dropna().empty:
        t2s10s_bp = (dgs10.dropna().iloc[-1] - dgs2.dropna().iloc[-1]) * 100
    items["t2s10s_bp"] = Item("t2s10s_bp", t2s10s_bp, scoring.score_t2s10s_bp(t2s10s_bp),
                               as_of, "FRED DGS10/DGS2", "高" if t2s10s_bp is not None else "暫缺")

    sofr, iorb = fred_daily("SOFR"), fred_daily("IORB")
    sofr_iorb_bp = None
    if not sofr.dropna().empty and not iorb.dropna().empty:
        sofr_iorb_bp = (sofr.dropna().iloc[-1] - iorb.dropna().iloc[-1]) * 100
    items["sofr_iorb_bp"] = Item("sofr_iorb_bp", sofr_iorb_bp, scoring.score_sofr_iorb_bp(sofr_iorb_bp),
                                  as_of, "FRED SOFR/IORB", "高" if sofr_iorb_bp is not None else "暫缺")

    # ③ 市場微觀結構 ---------------------------------------------------------
    move_latest = yahoo_panel.get("move", pd.Series(dtype=float)).dropna()
    move_level = float(move_latest.iloc[-1]) if not move_latest.empty else None
    items["move_index"] = Item("move_index", move_level, scoring.score_move_index(move_level),
                                as_of, "Yahoo Finance ^MOVE", "高" if move_level is not None else "暫缺")

    _breadth_source = ndx_breadth_detail.get("source")
    items["ndx_breadth_200d"] = Item("ndx_breadth_200d", ndx_breadth_pct, scoring.score_ndx_breadth_200d(ndx_breadth_pct),
                                      as_of,
                                      "TradingView NDX 成分股" if _breadth_source == "tradingview"
                                      else "Invesco QQQ 持股／Wikipedia 成分股 + Yahoo Finance",
                                      "中" if ndx_breadth_pct is not None else "暫缺",
                                      note=(
                                          f"{ndx_breadth_detail.get('constituents', '?')} 檔成分股；"
                                          f"站上50日線 {ndx_breadth_detail.get('pct_above_50dma')}%、"
                                          f"20日線 {ndx_breadth_detail.get('pct_above_20dma')}%；"
                                          "以目前成分股清單回推，非時點正確(look-back bias)"
                                      ) if _breadth_source == "tradingview"
                                      else "以目前成分股清單回推，非時點正確(look-back bias)"
                                      if ndx_breadth_pct is not None
                                      # 這裡的失敗訊息帶有定位問題所需的欄位/樣本資訊，
                                      # 截太短會把證據切掉，故放寬長度上限
                                      else f"計算失敗: {_short_error(ndx_breadth_error or '未知原因', 400)}")

    # ④ 風險偏好情緒 ---------------------------------------------------------
    vix_series = yahoo_panel.get("vix", pd.Series(dtype=float)).dropna()
    vix3m_series = yahoo_panel.get("vix3m", pd.Series(dtype=float)).dropna()
    vix_level = float(vix_series.iloc[-1]) if not vix_series.empty else None
    items["vix_level"] = Item("vix_level", vix_level, scoring.score_vix_level(vix_level),
                               as_of, "Yahoo Finance ^VIX", "高" if vix_level is not None else "暫缺")

    ivts = None
    if not vix_series.empty and not vix3m_series.empty:
        ivts = float(vix_series.iloc[-1] / vix3m_series.iloc[-1])
    items["ivts"] = Item("ivts", ivts, scoring.score_ivts(ivts),
                          as_of, "Yahoo Finance ^VIX / ^VIX3M", "高" if ivts is not None else "暫缺")

    margin_yoy_daily = _lagged_yoy_daily(margin["raw"], FINRA_PUBLISH_LAG_DAYS, margin["daily"].index)
    margin_yoy_latest = margin_yoy_daily.dropna().iloc[-1] if not margin_yoy_daily.dropna().empty else None
    if not margin.get("ok"):
        margin_note = f"抓取失敗: {_short_error(margin.get('error', ''))}"
    elif margin_yoy_latest is None:
        # 抓到資料卻算不出年增率，最常見的原因是頁面只提供不到13個月的歷史
        n_months = len(margin["raw"].dropna())
        margin_note = (
            f"僅取得 {n_months} 個月資料，不足以計算年增率（需涵蓋13個月以上）"
            if n_months else "FINRA 未取得任何月份資料"
        )
    else:
        margin_note = "月頻，已套用文件規定之2個月發布時滯"
    items["margin_debt_yoy"] = Item("margin_debt_yoy", margin_yoy_latest, scoring.score_margin_debt_yoy(margin_yoy_latest),
                                     as_of, "FINRA margin statistics",
                                     "中" if margin_yoy_latest is not None else "暫缺",
                                     note=margin_note)

    # ⑤ 跨資產資金流向 -------------------------------------------------------
    dxy_series = yahoo_panel.get("dxy", pd.Series(dtype=float)).dropna()
    dxy_source = "Yahoo Finance DX-Y.NYB"
    if dxy_series.empty:
        dtwexbgs = fred_daily("DTWEXBGS").dropna()
        if not dtwexbgs.empty:
            dxy_series = dtwexbgs
            dxy_source = "FRED DTWEXBGS (DX-Y.NYB 抓取失敗之備援，編製方式不同僅供參考)"
    dxy_1m_chg = n_trading_day_change(dxy_series, 21, pct=True)
    items["dxy_1m_chg"] = Item("dxy_1m_chg", dxy_1m_chg, scoring.score_dxy_1m_chg(dxy_1m_chg),
                                as_of, dxy_source, "高" if dxy_1m_chg is not None else "暫缺")

    usdjpy_series = yahoo_panel.get("usdjpy", pd.Series(dtype=float)).dropna()
    usdjpy_20d_chg = n_trading_day_change(usdjpy_series, 20, pct=True)
    items["usdjpy_20d_chg"] = Item("usdjpy_20d_chg", usdjpy_20d_chg, scoring.score_usdjpy_20d_chg(usdjpy_20d_chg),
                                    as_of, "Yahoo Finance JPY=X", "高" if usdjpy_20d_chg is not None else "暫缺")

    items["etf_fund_flow"] = _build_etf_flow_item(as_of, manual)

    # ⑥ 政策方向 --------------------------------------------------------------
    dfedtaru_daily = fred_daily("DFEDTARU")
    fomc_score, fomc_meta = _fetch_fomc(as_of, dfedtaru_daily)
    if fomc_score is not None:
        items["fomc_decision"] = Item("fomc_decision", fomc_meta.get("note"), fomc_score,
                                       fomc_meta.get("data_date"),
                                       "federalreserve.gov 新聞稿 + FRED DFEDTARU", "中",
                                       note=fomc_meta.get("url", ""))
    elif "fomc_decision" in manual:
        # 自動解析失敗時退回人工填入（GitHub Actions 連不到 federalreserve.gov）
        item = manual["fomc_decision"]
        item.note = f"{item.note}（自動抓取失敗：{_short_error(fomc_meta.get('note') or '未知原因', 80)}）"
        items["fomc_decision"] = item
    else:
        items["fomc_decision"] = Item("fomc_decision", fomc_meta.get("note"), None,
                                       fomc_meta.get("data_date"),
                                       "federalreserve.gov 新聞稿 + FRED DFEDTARU", "暫缺",
                                       note=f"抓取失敗: {_short_error(fomc_meta.get('note') or '未知原因')}"
                                            "。可於 docs/data/manual_overrides.json 手動填入 -2..2 分")

    if "fedwatch_path" in manual:
        items["fedwatch_path"] = manual["fedwatch_path"]
    else:
        items["fedwatch_path"] = Item("fedwatch_path", None, None, None,
                                       "CME FedWatch (無穩定可程式化擷取來源)", "暫缺",
                                       note="請至 CME FedWatch 網站人工查證後填入 docs/data/manual_overrides.json")

    yield30y_chg_bp = n_trading_day_change(dgs30, 60)
    yield30y_chg_bp = yield30y_chg_bp * 100 if yield30y_chg_bp is not None else None
    is_high = is_trailing_max(dgs30, gates.TEN_YEAR_TRADING_DAYS)
    items["yield30y_60d_momentum"] = Item("yield30y_60d_momentum", yield30y_chg_bp,
                                           scoring.score_yield30y_60d_momentum(yield30y_chg_bp, is_high),
                                           as_of, "FRED DGS30", "高" if yield30y_chg_bp is not None else "暫缺",
                                           note="創10年新高" if is_high else "")

    # --- 彙總 -----------------------------------------------------------
    item_scores = {k: v.score for k, v in items.items()}
    composite, cat_avgs, eff_weights = scoring.composite_score(item_scores)
    light = scoring.light_for_score(composite)
    dominance = scoring.dominance_check(item_scores, cat_avgs, eff_weights)
    divergence = scoring.divergence_check(cat_avgs)

    # --- Gate A/B/C -------------------------------------------------------
    ndx_series = yahoo_panel.get("ndx", pd.Series(dtype=float))
    gate_a_result = gates.gate_a(ndx_series, as_of)

    skew_series = yahoo_panel.get("skew", pd.Series(dtype=float))
    gdp_daily = fred_daily("GDP")
    margin_gdp_daily = None
    if not margin["daily"].dropna().empty and not gdp_daily.dropna().empty:
        common = margin["daily"].dropna().index.intersection(gdp_daily.dropna().index)
        if len(common) > 0:
            # GDP (FRED "GDP") 單位為十億美元；margin_debt 已換算為美元。
            margin_gdp_daily = (margin["daily"].loc[common] / (gdp_daily.loc[common] * 1e9) * 100).rename("margin_debt_gdp")
    gate_b_result = gates.gate_b(
        margin_yoy_daily, hy_oas_daily, margin_gdp_daily if margin_gdp_daily is not None else pd.Series(dtype=float),
        vix_series, skew_series, None, as_of,
    )

    missing_low_conf = sum(1 for it in items.values() if it.confidence in ("暫缺", "低") or it.confidence.startswith("暫缺"))
    policy_missing = any(items[k].score is None for k in CATEGORY_ITEMS["policy_direction"])
    gate_c_result = gates.gate_c(missing_low_conf, composite, policy_missing, LIGHT_BANDS)

    alloc, leverage = scoring.position_for_score(composite)
    if gate_c_result["downgrade_one_level"]:
        idx = next((i for i, (lo, hi, a, lv) in enumerate(POSITION_LADDER) if a == alloc), None)
        if idx is not None and idx < len(POSITION_LADDER) - 1:
            alloc, leverage = POSITION_LADDER[idx + 1][2], POSITION_LADDER[idx + 1][3]

    final_cap = None
    for g in (gate_a_result, gate_b_result, gate_c_result):
        cap = g.get("cap")
        if cap == "SGOV":
            final_cap = "SGOV"
        elif cap == "QQQ" and final_cap != "SGOV":
            final_cap = "QQQ"

    final_alloc = alloc
    if final_cap == "SGOV":
        final_alloc = "100% SGOV"
    elif final_cap == "QQQ" and alloc not in ("100% SGOV",) and "TQQQ" in alloc:
        final_alloc = "100% QQQ"

    # --- 軌道二（簡化每日近似）---------------------------------------------
    vix9d_series = yahoo_panel.get("vix9d", pd.Series(dtype=float)).dropna()
    gold_series = yahoo_panel.get("gold", pd.Series(dtype=float)).dropna()
    tlt_series = yahoo_panel.get("tlt", pd.Series(dtype=float)).dropna()

    def last_1d_chg(s, pct=True):
        return n_trading_day_change(s, 1, pct=pct)

    track2 = gates.track2_layer1_daily_approx(
        vix=vix_level,
        vix9d=float(vix9d_series.iloc[-1]) if not vix9d_series.empty else None,
        ndx_1d_chg_pct=last_1d_chg(ndx_series),
        dgs10_1d_chg_bp=(n_trading_day_change(dgs10, 1) * 100 if n_trading_day_change(dgs10, 1) is not None else None),
        dxy_1d_chg_pct=last_1d_chg(dxy_series),
        gold_1d_chg_pct=last_1d_chg(gold_series),
        tlt_1d_chg_pct=last_1d_chg(tlt_series),
    )

    report = {
        "as_of": as_of,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "items": {k: v.as_dict() for k, v in items.items()},
        "category_scores": {CATEGORY_LABELS[c]: (round(v, 4) if v is not None else None) for c, v in cat_avgs.items()},
        "category_weights_effective": {CATEGORY_LABELS[c]: round(w, 4) for c, w in eff_weights.items()},
        "composite_score": composite,
        "light": light,
        "structural_warnings": {
            "dominance": dominance,
            "divergence": divergence,
        },
        "gate_a": gate_a_result,
        "gate_b": gate_b_result,
        "gate_c": gate_c_result,
        "position": {
            "ladder_result": alloc,
            "final": final_alloc,
            "leverage_ladder": leverage,
            "final_cap_applied": final_cap,
        },
        "track2_daily_approx": track2,
        # ③ 只採計「站上200日線比例」計分，但同一次抓取還算出 20／50 日線廣度、
        # 漲跌家數與 RSI 分布。單看 200 日線分不出「短線鬆動」與「中期轉弱」，
        # 因此把完整明細一併輸出供儀表板呈現。
        "ndx_breadth_detail": ndx_breadth_detail,
        "missing_or_low_confidence_count": missing_low_conf,
    }
    return report
