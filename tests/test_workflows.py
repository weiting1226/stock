"""排程的形狀。

這些不是「設定檔看起來對不對」，是**兩個實測出來的排序錯誤**對應的防線：

  模組六讀到昨天的 base_alloc   ← 註解寫「排在模組一之後」，cron 卻早一小時。
                                  2026-08-24 的報告 as_of=08-24、base_alloc
                                  來自 08-21，problems 是空的。

  模組九可能跑在模組二前面       ← 兩者 cron 只差 20 分鐘，但排程觸發的實際
                                  延遲實測是 19～24 分鐘。間隔比抖動還小，
                                  先後保證不了。

兩者的共同點是**都不會失敗**：閘門照跑、名單照排、頁面照顯示，只是資料是舊的。
沒有例外可以攔，所以只能在排程的形狀上守住。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(wf: dict) -> dict:
    """YAML 1.1 把沒加引號的 `on` 讀成布林 True，不是字串 "on"。"""
    return wf.get("on", wf.get(True)) or {}


def _crons(wf: dict) -> list[str]:
    sched = _triggers(wf).get("schedule") or []
    return [s["cron"] for s in sched]


def _minutes(cron: str) -> int:
    """把 "20 23 * * 1-5" 換成當日的第幾分鐘，用來比先後。"""
    minute, hour = cron.split()[0], cron.split()[1]
    return int(hour) * 60 + int(minute)


# --- 模組六 ← 模組一 --------------------------------------------------------

def test_tail_gate_runs_after_the_module_one_scrape_it_depends_on():
    """**這一則是補破網。** base_alloc 只能來自主評分的輸出 docs/data/latest.json，
    模組六自己不重算（重算等於讓兩邊有機會不一致）。但它原本排在 21:30，
    模組一排在 22:30——每天早一個小時，於是讀到的永遠是上一個交易日的部位。

    實測 docs/data/tail/latest.json：as_of 2026-08-24，
    base_alloc.source.as_of 2026-08-21，而 problems 是空的。
    """
    tail = _minutes(_crons(_load("tail-gate.yml"))[0])
    module_one = _minutes(_crons(_load("daily-scrape.yml"))[0])
    assert tail > module_one, "模組六排在模組一之前，base_alloc 會是舊的"


def test_tail_gate_leaves_more_margin_than_the_observed_schedule_jitter():
    """光是「晚一點」不夠：排程觸發的實際開始時間會延遲，而且每天不一樣。
    實測模組一延遲 16～21 分鐘、執行約 50 秒。間隔若跟抖動同量級，
    某天就會反過來——而且反過來的那天不會有任何錯誤訊息。"""
    gap = _minutes(_crons(_load("tail-gate.yml"))[0]) - _minutes(_crons(_load("daily-scrape.yml"))[0])
    assert gap >= 25, f"只差 {gap} 分鐘，比實測的排程延遲變動範圍還小"


def test_both_run_within_the_same_utc_day_so_their_as_of_match():
    """兩邊的 as_of 都取執行當下的 UTC 日期。跨過午夜就會差一天，
    看起來就跟排序錯誤一模一樣。"""
    for name in ("daily-scrape.yml", "tail-gate.yml"):
        for cron in _crons(_load(name)):
            assert _minutes(cron) < 24 * 60 - 30, f"{name} 排得太靠近 UTC 午夜"


# --- 模組九 ← 模組二 --------------------------------------------------------

def test_gap_radar_is_chained_to_the_valuation_run_not_merely_scheduled_after_it():
    """模組九整份輸出都是模組二 latest.json 的再加工。用「排在後面 N 分鐘」
    保證先後是行不通的：實測模組二的排程延遲是 19、22、24、21、20 分鐘，
    抖動範圍比原本設的 20 分鐘間隔還大。"""
    on = _triggers(_load("gap-radar.yml"))
    assert "workflow_run" in on, "模組九又改回用時刻排程了，先後保證不了"
    assert not on.get("schedule"), "同時掛排程會讓它有機會在模組二之前跑"


def test_the_chain_names_the_upstream_workflow_exactly():
    """workflow_run 是用 `name:` 字串比對的。上游改了名字，這條鏈會**安靜地**
    斷掉——不會有錯誤，模組九只是從此再也不觸發，而頁面上還是昨天那份資料。"""
    upstream = _triggers(_load("gap-radar.yml"))["workflow_run"]["workflows"]
    assert _load("daily-valuation.yml")["name"] in upstream, \
        "模組九指定的上游名稱對不上 daily-valuation.yml 的 name"


def test_gap_radar_checks_out_main_rather_than_the_triggering_commit():
    """workflow_run 事件的 github.sha 是**觸發它的那次執行的 head commit**，
    也就是模組二開始跑之前的 commit——不含模組二剛推上來的估值資料。
    不指定 ref 就等於保證讀到舊資料，正好是這條鏈要修掉的那件事。"""
    steps = _load("gap-radar.yml")["jobs"]["gap-radar"]["steps"]
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
    assert (checkout.get("with") or {}).get("ref") == "main"


def test_gap_radar_skips_a_failed_upstream_run():
    """模組二失敗代表 latest.json 沒更新。這時候照跑，會把昨天的名單重存一次
    並附上今天的 generated_at——看起來像是今天算的。"""
    job = _load("gap-radar.yml")["jobs"]["gap-radar"]
    assert "conclusion" in job.get("if", ""), "沒有擋掉模組二失敗的情況"


# --- 每日更新的整體覆蓋 -----------------------------------------------------

# 刻意只手動觸發的，各有理由；列在這裡是為了讓「沒排程」變成一個要說明的決定，
# 而不是漏掉了也沒人發現。
MANUAL_ONLY = {
    "probe-sources.yml": "探測資料來源設定要不要改，不是每天會變的資料",
    "tail-backfill.yml": "回補是一次性重建，排程它只會每天把同一段歷史重算一次",
}

# 每週一次即可的：只有在策略邏輯改變或多了一段新歷史時結果才會變。
WEEKLY_OK = {"backtest.yml": "回測結果不隨當日行情變動"}


def test_every_workflow_either_updates_regularly_or_says_why_not():
    """「這一頁的資料多久更新一次」不該靠翻 cron 才知道。
    新增模組時最容易發生的是**忘了排程**——它不會失敗，只是那一頁從此不動，
    而頁面上還是有數字。"""
    for path in sorted(WORKFLOWS.glob("*.yml")):
        wf = _load(path.name)
        on = _triggers(wf)
        if path.name in MANUAL_ONLY or path.name in WEEKLY_OK:
            continue
        assert on.get("schedule") or "workflow_run" in on or "push" in on, \
            f"{path.name} 只能手動觸發，卻不在 MANUAL_ONLY 名單裡"


@pytest.mark.parametrize("name", sorted(
    p.name for p in WORKFLOWS.glob("*.yml")
    if p.name not in MANUAL_ONLY and p.name not in WEEKLY_OK))
def test_scheduled_workflows_cover_every_trading_day(name):
    """每天更新的意思是每個交易日都要有一次。只排 `* * 1`（每週一次）
    或漏掉某幾天，畫面不會顯示「這是三天前的」，只會顯示那三天前的數字。"""
    wf = _load(name)
    on = _triggers(wf)
    if "workflow_run" in on:
        return          # 跟著上游跑，覆蓋範圍由上游決定（另有測試守住那條鏈）
    dows = {c.split()[4] for c in _crons(wf)}
    assert dows & {"*", "1-5", "2-6"}, f"{name} 的排程沒有涵蓋每個交易日：{dows}"
