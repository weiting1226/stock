"""買賣門檻的搜尋與驗證。

**這個檔案存在的理由是防止自欺。** 在同一段十五年歷史上試 180 組門檻、
挑出最好的那組、再把它的績效當成成果報告——這不是最佳化，是把雜訊
記下來。試的組數越多，最好的那組看起來越漂亮，而它在未來的表現與
隨便挑一組沒有差別。

因此這裡的規則是：

1. **樣本外才算數。** 用逐年前進（walk-forward）：每一年的門檻只能用
   「那一年之前」的資料選出來，再套用到接下來那一年。把每年的樣本外
   報酬接起來，才是這套方法真正的成績。
2. **同時報告樣本內最佳值**，讓兩者的落差直接顯示出來。落差有多大，
   就是過度配適有多嚴重。
3. **檢查最佳解是尖峰還是高原。** 鄰近參數表現也好，代表訊號是真的；
   只有那一點特別好、旁邊都不行，那就是雜訊。這比最佳值本身更能說明
   一組參數能不能用。
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import allin, engine, metrics
from .config import COST_BPS_PER_SIDE, QQQ, REBALANCE_ON_CHANGE_ONLY, SGOV, TQQQ

log = logging.getLogger(__name__)

# 刻意用粗網格。格子切得越細，找到的「最佳點」越可能只是某一天的巧合；
# 而門檻本來就沒有精確到 0.01 的意義——分數本身是十幾個 -2..2 整數的平均。
TQQQ_CENTERS = (-0.2, 0.0, 0.2, 0.4, 0.6, 0.8)
SGOV_CENTERS = (-1.0, -0.8, -0.6, -0.4, -0.2, 0.0)
BUFFERS = (0.0, 0.10, 0.15, 0.20, 0.30)

# 訓練期至少要這麼多年才選參數，否則等於用兩三年的樣本決定十年的規則
MIN_TRAIN_YEARS = 4


@dataclass
class Candidate:
    rules: allin.AllInRules
    label: str


def candidate_rules() -> list[Candidate]:
    """產生所有合法的門檻組合。"""
    out = []
    for t, s, b in itertools.product(TQQQ_CENTERS, SGOV_CENTERS, BUFFERS):
        if s >= t:
            continue                      # 現金線必須低於槓桿線
        if s + b >= t - b:
            continue                      # 緩衝區重疊，狀態機沒有中間帶
        try:
            rules = allin.AllInRules(tqqq_center=t, sgov_center=s, buffer=b)
        except ValueError:
            continue                      # 加速門檻與出場線衝突的組合
        out.append(Candidate(rules, f"T{t:+.1f}/S{s:+.1f}/B{b:.2f}"))
    return out


def evaluate(
    weights: pd.DataFrame,
    prices: pd.DataFrame,
    rf: pd.Series,
    window: pd.DatetimeIndex = None,
    cost_bps: float = COST_BPS_PER_SIDE,
) -> dict:
    """在指定期間內模擬一組權重並回傳績效。"""
    px = prices if window is None else prices.loc[prices.index.isin(window)]
    if len(px) < 60:
        return {}
    sim = engine.simulate(weights, px, cost_bps_per_side=cost_bps,
                          rebalance_on_change_only=REBALANCE_ON_CHANGE_ONLY)
    if sim.empty:
        return {}
    m = metrics.summarize(sim["net_return"], sim["equity"], rf.reindex(sim.index).fillna(0.0))
    m["rebalances"] = int((sim["turnover"] > 0).sum())
    return m


def _score_of(m: dict, objective: str) -> float:
    """把績效換算成單一個可比較的分數。缺值一律視為最差。"""
    if not m:
        return float("-inf")
    if objective == "sharpe":
        v = m.get("sharpe")
    elif objective == "calmar":
        # 年化報酬除以最大回撤：槓桿策略最該看的比率
        dd = abs(m.get("max_drawdown_pct") or 0)
        v = (m.get("cagr_pct") / dd) if dd else None
    else:
        v = m.get("cagr_pct")
    return float("-inf") if v is None or np.isnan(v) else float(v)


def build_weights(
    composite: pd.DataFrame,
    gate_a: pd.DataFrame,
    gate_b: pd.Series,
    candidates: list[Candidate] = None,
) -> dict[str, pd.DataFrame]:
    """先把每組門檻的權重算好。

    狀態機是逐日的 Python 迴圈，而同一組門檻的權重與「評估哪一段期間」
    無關。逐年前進會反覆用到同樣的權重，先算好可以省下十幾倍的時間。
    """
    cands = candidates or candidate_rules()
    return {c.label: allin.allin_weights(composite, gate_a, gate_b, c.rules) for c in cands}


def search(
    weights_by_label: dict[str, pd.DataFrame],
    prices: pd.DataFrame,
    rf: pd.Series,
    window: pd.DatetimeIndex,
    objective: str = "sharpe",
    cost_bps: float = COST_BPS_PER_SIDE,
    candidates: list[Candidate] = None,
) -> list[tuple[Candidate, dict, float]]:
    """在 `window` 這段期間內評估所有候選門檻，依目標函式由好到差排序。"""
    cands = candidates or candidate_rules()
    scored = []
    for c in cands:
        w = weights_by_label.get(c.label)
        if w is None:
            continue
        m = evaluate(w, prices, rf, window, cost_bps)
        scored.append((c, m, _score_of(m, objective)))
    return sorted(scored, key=lambda x: x[2], reverse=True)


def walk_forward(
    composite: pd.DataFrame,
    gate_a: pd.DataFrame,
    gate_b: pd.Series,
    prices: pd.DataFrame,
    rf: pd.Series,
    objective: str = "sharpe",
    cost_bps: float = COST_BPS_PER_SIDE,
    min_train_years: int = MIN_TRAIN_YEARS,
) -> dict:
    """逐年前進驗證。

    對每一個測試年度 Y：只用 Y 之前的資料選門檻，再把選出來的門檻套用到 Y。
    把各年度的報酬接起來，就是這套「用歷史挑門檻」的方法真正能拿到的成績。

    狀態機是路徑相依的，因此權重仍以完整歷史計算——但**選參數時只看得到
    訓練期**，測試年度的報酬也只取自該年度。決策當下沒有用到任何未來資訊。
    """
    years = sorted({d.year for d in prices.index})
    test_years = years[min_train_years:]
    if not test_years:
        raise ValueError(f"資料不足 {min_train_years} 年，無法做逐年前進驗證")

    cands = candidate_rules()
    weights_by_label = build_weights(composite, gate_a, gate_b, cands)
    picks, oos_returns = [], []

    for y in test_years:
        train = prices.index[prices.index.year < y]
        test = prices.index[prices.index.year == y]
        ranked = search(weights_by_label, prices, rf, train, objective, cost_bps, cands)
        best, train_m, train_score = ranked[0]

        sim = engine.simulate(weights_by_label[best.label], prices.loc[prices.index.isin(test)],
                              cost_bps_per_side=cost_bps,
                              rebalance_on_change_only=REBALANCE_ON_CHANGE_ONLY)
        if sim.empty:
            continue
        oos_returns.append(sim["net_return"])
        picks.append({
            "year": int(y),
            "label": best.label,
            "tqqq_center": best.rules.tqqq_center,
            "sgov_center": best.rules.sgov_center,
            "buffer": best.rules.buffer,
            "train_score": round(train_score, 3),
            "train_cagr_pct": train_m.get("cagr_pct"),
            "test_return_pct": round(float(((1 + sim["net_return"]).prod() - 1) * 100), 2),
        })

    oos = pd.concat(oos_returns).sort_index()
    equity = (1 + oos).cumprod()
    return {
        "picks": picks,
        "metrics": metrics.summarize(oos, equity, rf.reindex(oos.index).fillna(0.0)),
        "equity": equity,
        "returns": oos,
    }


def plateau(ranked: list[tuple[Candidate, dict, float]], top_n: int = 10) -> dict:
    """最佳解是尖峰還是高原？

    只有那一點特別好、旁邊都不行，代表它抓到的是某幾天的巧合。
    鄰近參數也都不錯，訊號才可能是真的。這比最佳值本身更能說明能不能用。
    """
    if not ranked:
        return {}
    scores = [s for _c, _m, s in ranked if np.isfinite(s)]
    if not scores:
        return {}
    best = scores[0]
    top = ranked[:top_n]

    # 多重比較檢定：試 n 組參數，就算它們**完全沒有差別**，最好的那組也一定
    # 會比中位數高一截——純粹因為試得夠多。純雜訊下 n 組的最大值期望約在
    # 2.5~2.8 個標準差（n≈150），因此觀察到的最佳值若沒有明顯超過這個水準，
    # 就沒有證據說「這組門檻真的比較好」。
    n = len(scores)
    sd = float(np.std(scores))
    observed_z = (best - float(np.median(scores))) / sd if sd else None
    expected_noise_z = float(np.sqrt(2 * np.log(n))) if n > 1 else None
    return {
        "best_score": round(best, 3),
        "median_score": round(float(np.median(scores)), 3),
        # 最佳值比全體中位數高出多少個標準差。差距越大，越可能是運氣
        "z_vs_all": round(observed_z, 2) if observed_z is not None else None,
        "expected_z_from_noise_alone": round(expected_noise_z, 2) if expected_noise_z else None,
        "best_is_within_noise": (
            bool(observed_z < expected_noise_z)
            if observed_z is not None and expected_noise_z else None
        ),
        "score_spread": round(float(scores[0] - scores[-1]), 3),
        "score_sd": round(sd, 4),
        "top_n": top_n,
        "top_score_spread": round(best - top[-1][2], 3) if len(top) == top_n else None,
        "top_params": [
            {"label": c.label, "score": round(s, 3),
             "tqqq_center": c.rules.tqqq_center, "sgov_center": c.rules.sgov_center,
             "buffer": c.rules.buffer}
            for c, _m, s in top
        ],
    }
