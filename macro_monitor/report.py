"""組裝模組五的報告：抓 FRED -> 轉換 -> 分類 -> 產出儀表板 JSON。"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from liquidity_monitor.sources import fred

from . import analysis, indicators, news_ai, news_store, release_log
from .config import (
    CATEGORIES,
    HISTORY_YEARS,
    PERCENTILE_YEARS,
    RECESSION_SERIES,
    SERIES,
)

log = logging.getLogger(__name__)


def fetch_all(as_of: str, years: int = HISTORY_YEARS) -> tuple[dict, dict]:
    """抓所有序列。回傳 (資料, 抓取診斷)。

    單一序列失敗不中斷整批——少一條指標仍然是有用的儀表板，
    但失敗原因要留下來，否則畫面上「這個月還沒公布」與「來源壞了」
    長得一模一樣。
    """
    start = str((pd.Timestamp(as_of) - pd.DateOffset(years=years)).date())
    data, diag = {}, {}
    for spec in (*SERIES,):
        d = fred.FetchDiagnostics(spec.fred_id, start)
        try:
            data[spec.fred_id] = fred.fetch_fred_series(spec.fred_id, start, as_of, diagnostics=d)
        except Exception as e:  # noqa: BLE001
            log.warning("FRED %s 抓取失敗：%s", spec.fred_id, e)
            data[spec.fred_id] = pd.Series(dtype=float)
            diag[spec.fred_id] = {"error": f"{type(e).__name__}: {e}",
                                  "attempts": d.attempts}
            continue
        # 只留有疑點的診斷（試過一個以上端點），其餘附上只是噪音
        if len(d.attempts) > 1:
            diag[spec.fred_id] = {"chosen": d.chosen, "attempts": d.attempts}

    try:
        data[RECESSION_SERIES] = fred.fetch_fred_series(RECESSION_SERIES, start, as_of)
    except Exception as e:  # noqa: BLE001
        log.warning("衰退指標抓取失敗：%s", e)
        data[RECESSION_SERIES] = pd.Series(dtype=float)
    return data, diag


def build_report(as_of: str = None, data: dict = None, diagnostics: dict = None) -> dict:
    as_of = as_of or date.today().isoformat()
    if data is None:
        data, diagnostics = fetch_all(as_of)
    diagnostics = diagnostics or {}

    usrec = data.get(RECESSION_SERIES)
    rows, charts, observations = [], {}, {}
    for spec in SERIES:
        raw = data.get(spec.fred_id)
        item = indicators.build_indicator(raw, spec, as_of)
        if item.get("available"):
            values = indicators.transform(raw, spec)
            charts[spec.fred_id] = indicators.sparkline(values, freq=spec.freq)
            # 深入分析：z 分數、加速度、極值、轉折、波動、衰退期對照
            item["analysis"] = analysis.analyse(values, usrec, spec.freq)
            observations[spec.fred_id] = (item["reference_date"], item["value"])
        rows.append(item)

    # 每日掃描的發布日誌。記錄「這一期第一次被看到是哪一天」，
    # 累積之後就有本專案自己量到的發布節奏，可以驗證手填的估計值。
    try:
        newly = release_log.record_scan(observations, as_of)
        release_log.record_scan_time(as_of, observations)
        rlog = release_log.load_log()
        for r in rows:
            if not r.get("available"):
                continue
            stats = release_log.observed_lag_stats(rlog, r["fred_id"])
            if stats:
                r["observed_release"] = stats
        updated_today = [{"fred_id": n["fred_id"], "reference_date": n["reference_date"],
                          "label": next((x["label"] for x in rows if x["fred_id"] == n["fred_id"]), n["fred_id"])}
                         for n in newly]
    except Exception as e:  # noqa: BLE001 — 日誌寫不進去不該讓整份報告失敗
        log.warning("發布日誌記錄失敗：%s", e)
        updated_today, rlog = [], None

    available = [r for r in rows if r.get("available")]
    discontinued = [r for r in available if r.get("discontinued")]
    # 已停止更新的不算「延遲」——兩者的處置完全不同
    stale = [r for r in available if r.get("stale") and not r.get("discontinued")]
    missing = [r for r in rows if not r.get("available")]

    # --- AI 新聞摘要 -------------------------------------------------------
    # 只為「當期還沒有摘要」的指標呼叫模型：成功的留著（同一期重算沒有意義），
    # 失敗的下次自然重試（若以發布日誌為判準，今天失敗的就永遠不會再試）。
    news_notes = []
    try:
        store = news_store.load()
        pending = news_store.missing(rows, store)
        summaries, news_notes = news_ai.summarise_updated(rows, pending)
        store = news_store.save(store, summaries, rows)
        news_attached = news_store.attach(rows, store)
    except Exception as e:  # noqa: BLE001 — 摘要失敗不該讓整份報告失敗
        log.warning("新聞摘要處理失敗：%s", e)
        news_notes.append(f"{type(e).__name__}: {e}")
        news_attached = 0

    by_category = {}
    for key, label in CATEGORIES.items():
        items = [r for r in rows if r["category"] == key]
        improving = sum(1 for r in items if r.get("direction_3m") == "改善")
        worsening = sum(1 for r in items if r.get("direction_3m") == "惡化")
        by_category[key] = {
            "label": label,
            "count": len(items),
            "improving": improving,
            "worsening": worsening,
        }

    return {
        "as_of": as_of,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "categories": CATEGORIES,
        "summary": by_category,
        "rows": rows,
        "charts": charts,
        "recession_bands": indicators.recession_bands(data.get(RECESSION_SERIES)),
        "percentile_years": PERCENTILE_YEARS,
        "counts": {"total": len(rows), "available": len(available),
                   "stale": len(stale), "missing": len(missing),
                   "discontinued": len(discontinued),
                   "updated_today": len(updated_today)},
        "updated_today": updated_today,
        "news": {
            "attached": news_attached,
            "notes": news_notes[:24],
            # 失敗原因分類：三種的處置完全不同，混在一起就看不出該做什麼
            "failure_kinds": _count_failures(news_notes),
            "method": ("摘要取自各指標的**官方統計發布原文**（BLS／BEA／Census／Fed 等），"
                       "不是二手新聞。抓不到文件就不呼叫模型——讓模型憑記憶說明「上個月 CPI "
                       "為什麼上漲」，會得到一段完全通順而且是編造的解釋。"
                       "模型提供的原文佐證必須逐字出現在文件中，否則整份摘要作廢。"),
        },
        "discontinued_series": [
            {"fred_id": r["fred_id"], "label": r["label"],
             "reference_date": r["reference_date"], "lag_days": r["lag_days"]}
            for r in discontinued],
        "stale_series": [{"fred_id": r["fred_id"], "label": r["label"],
                          "reference_date": r["reference_date"], "lag_days": r["lag_days"]}
                         for r in stale],
        "missing_series": [{"fred_id": r["fred_id"], "label": r["label"],
                            "error": r.get("error")} for r in missing],
        "fetch_diagnostics": diagnostics,
        "notes": [
            "每一條指標顯示的是**參考期**的數字，不是抓取日期——CPI 的 7 月數字"
            "要到 8 月中才公布，畫面上的「資料落後天數」就是這段差距。",
            "「改善／惡化」的方向由設定檔明確指定（失業率上升是惡化、GDP 上升是改善）；"
            "通膨與殖利率這類沒有單一方向好壞的指標一律顯示「—」，不硬套。",
            f"百分位以最近 {PERCENTILE_YEARS} 年為比較期間，樣本不足時留空而不是硬給數字。",
            "衰退陰影取自 NBER 認定（FRED USREC）。NBER 往往在衰退開始一年後才公布認定，"
            "因此最近期不會有標記——那是「還沒認定」，不是「沒有衰退」。",
            "總體數據會被事後修正，本頁顯示的一律是目前最新的修正版本。",
            "「觀測發布時滯」由每日掃描累積而得——記錄每一期第一次被看到是哪一天。"
            "掃描為每日一次，因此最多高估一天；累積期數不足兩期時不顯示。",
            "「停止更新」與「延遲」分開判定：超過過期門檻四倍且至少一年，"
            "視為該序列已停止發布，應從清單移除而不是繼續等。",
            "AI 摘要只描述官方發布文件說了什麼，不預測、不給投資判斷；"
            "殖利率與利差等市場價格類指標沒有對應的統計發布，因此沒有摘要。",
        ],
    }


def _count_failures(notes: list) -> dict:
    """把失敗原因歸類。來源封鎖、沒有來源、內容不符、模型出錯——
    四種的處置完全不同，混成一份清單就看不出該做什麼。"""
    from . import news_ai
    kinds = {}
    for n in notes:
        kinds[news_ai.classify_failure(n)] = kinds.get(news_ai.classify_failure(n), 0) + 1
    return kinds


def write_report(report: dict, data_root: str) -> Path:
    import json
    out = Path(data_root) / "latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
