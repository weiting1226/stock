"""⑤ 股票型 ETF 資金流：由 ETF 流通股數變化自行計算。

**為什麼這是定義而不是代理指標**：ETF 的流通股數只會因為授權參與者的
申購（creation）與贖回（redemption）而變動——買賣既有股份不會改變股數。
因此「流通股數變化 × 價格」就是流入／流出該檔 ETF 的實際金額本身。

改用這個方法是因為 ETF.com 從 GitHub Actions 存取時三個位址全部回 403
（Cloudflare 擋資料中心 IP，2026-08 實測）。而 Yahoo 是本專案唯一實證能從
Actions 穩定取用的來源。

兩種資料都試，優先序有意義：

1. **流通股數**（精確）：Δ股數 × 收盤價 = 申贖金額
2. **淨資產**（備援）：flow ≈ AUM_t − AUM_{t−1} × (1 + 報酬率)
   ——把市值變動中「漲跌造成的部分」扣掉，剩下的才是資金進出。
   比第一種多一層估計，但 Yahoo 對某些 ETF 只給得出 AUM。

抓不到一律標記暫缺，絕不臆測填補。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import yfinance as yf

log = logging.getLogger(__name__)

# 美國股票型 ETF 中規模最大的幾檔，涵蓋大盤、成長／價值、中小型。
# 不求窮盡全市場——目的是取得一個「規模夠大、成分穩定」的樣本來觀察方向與
# 強度，而計分本來就是拿它跟自己的歷史比，樣本一致比涵蓋完整更重要。
EQUITY_ETFS = (
    "SPY", "IVV", "VOO", "VTI", "QQQ", "IWM", "VUG", "VTV",
    "IWF", "IWD", "SPLG", "RSP", "DIA", "MDY", "IJH", "IJR",
)

# 單日流通股數變動超過這個比例，研判為分割或資料異常而非申贖。
# 即使是大額申贖，單日也不會讓一檔大型 ETF 的股數變動三成；
# 但 ETF 分割會讓股數瞬間變成兩倍或一半——那不是資金流。
IMPLAUSIBLE_SHARE_CHANGE_RATIO = 0.30

# 觀測間隔超過這麼多天就不採計：中間可能跨了長假或排程中斷，
# 把多日累積的申贖當成單日訊號會嚴重高估強度。
MAX_OBSERVATION_GAP_DAYS = 7

# 股數可信度檢查：對 ETF 而言「流通股數 × 價格」依定義就等於淨資產，
# 兩者對不起來就代表其中一個數字是錯的。
#
# 2026-08 實測 16 檔，只有 4 檔通過（DIA 0.97、IWD 0.96、IWM 1.05、SPY 0.89），
# 其餘偏差高達 16 倍（VUG 0.06、VTI 0.12、IJH 0.16），另有 2 檔根本沒有股數。
# Yahoo 對 ETF 的 sharesOutstanding 顯然不可靠。少了這道檢查，程式會照樣拿
# 這些數字去算差額——算出來的「資金流」看起來完全正常，實際上毫無意義。
#
# 更關鍵的是它**時有時無**：同一天稍晚再抓一次，DIA 與 IWD 的股數就變成
# 空值，通過檢查的只剩 IWM 與 SPY。這代表股數法的樣本組成會逐日飄移，
# 而樣本一飄移，絕對金額就跟著跳動——這正是計分改用「佔樣本淨資產比例」
# 的原因，也是不改用「只取股數可信的那幾檔」的原因：那樣的樣本會由 SPY
# 主導（占九成），而 SPY 的申贖大量來自機構避險，不能代表整體股票型資金流。
SHARES_CONSISTENCY_MIN = 0.80
SHARES_CONSISTENCY_MAX = 1.25
# 通過檢查的檔數不到可用檔數的這個比例，就整批改用淨資產法，不混用兩種基礎。
# 用比例而非絕對數：樣本清單日後增減時，門檻才會跟著調整。
SHARES_BASIS_MIN_FRACTION = 0.5
SHARES_BASIS_MIN_COUNT = 3


def shares_are_consistent(shares, close, total_assets) -> bool:
    """股數 × 價格是否對得上淨資產。對不上就代表這檔的股數不可信。"""
    shares, close, total_assets = _as_float(shares), _as_float(close), _as_float(total_assets)
    if not (shares and close and total_assets):
        return False
    ratio = shares * close / total_assets
    return SHARES_CONSISTENCY_MIN <= ratio <= SHARES_CONSISTENCY_MAX


@dataclass
class EtfSnapshot:
    """單一 ETF 在某一天的規模快照。"""
    as_of: str
    ticker: str
    shares_outstanding: Optional[float] = None
    total_assets: Optional[float] = None      # 淨資產（美元）
    close: Optional[float] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.close is not None and (
            self.shares_outstanding is not None or self.total_assets is not None
        )


@dataclass
class FlowResult:
    """一次計算出來的整體資金流。"""
    as_of: str
    net_flow_musd: float                # 百萬美元，正值為淨流入
    basis: str                          # "shares"（精確）或 "aum"（估計）
    etfs_used: int
    etfs_skipped: list                  # [(ticker, 原因)]
    days_spanned: int
    # 佔樣本淨資產的比例（%）。樣本組成有增減時，絕對金額會跳動而比例不會，
    # 因此計分用這個而不是金額——計分完全依賴跟自己的歷史比。
    flow_pct_of_aum: Optional[float] = None
    tickers_used: list = field(default_factory=list)


def _as_float(value) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f or f <= 0 else f


def fetch_snapshots(tickers=EQUITY_ETFS, as_of: Optional[str] = None) -> list[EtfSnapshot]:
    """抓取各 ETF 目前的流通股數、淨資產與收盤價。"""
    stamp = as_of or date.today().isoformat()
    out: list[EtfSnapshot] = []
    for ticker in tickers:
        snap = EtfSnapshot(as_of=stamp, ticker=ticker)
        try:
            info = yf.Ticker(ticker).info or {}
            snap.shares_outstanding = _as_float(info.get("sharesOutstanding"))
            snap.total_assets = _as_float(info.get("totalAssets"))
            snap.close = _as_float(
                info.get("previousClose") or info.get("regularMarketPreviousClose")
                or info.get("navPrice")
            )
        except Exception as e:  # noqa: BLE001 — 單一 ETF 失敗不能中斷整批
            snap.error = f"{type(e).__name__}: {e}"
            log.debug("ETF 快照抓取失敗 %s: %s", ticker, e)
        out.append(snap)
    return out


def _days_between(a: str, b: str) -> int:
    try:
        return abs((date.fromisoformat(str(b)) - date.fromisoformat(str(a))).days)
    except ValueError:
        return 0


def compute_flow(
    today: list[EtfSnapshot],
    previous: dict[str, dict],
) -> FlowResult:
    """比較今昨兩份快照，算出整體淨資金流（純函式，無網路存取）。

    `previous` 以 ticker 為鍵，值需含 as_of / shares_outstanding / total_assets / close。
    有前一日股數的用股數算（精確），否則退回淨資產分解（估計）。
    兩種基礎不能混在同一個數字裡——混了之後這個序列跟自己的歷史就不可比，
    而計分完全依賴「跟自己比」。因此以能用股數計算的檔數為準，
    不足半數才整批改用淨資產法。
    """
    stamp = next((s.as_of for s in today), date.today().isoformat())
    skipped: list = []

    share_pairs, aum_pairs, spans, sample_aum = {}, {}, [], 0.0
    for snap in today:
        prev = previous.get(snap.ticker)
        if not snap.ok:
            skipped.append((snap.ticker, snap.error or "本日資料不完整"))
            continue
        if not prev:
            skipped.append((snap.ticker, "無前一次觀測"))
            continue

        gap = _days_between(prev.get("as_of", stamp), stamp)
        if gap == 0:
            skipped.append((snap.ticker, "與前一次觀測同日"))
            continue
        if gap > MAX_OBSERVATION_GAP_DAYS:
            skipped.append((snap.ticker, f"距前次觀測 {gap} 天，過久不採計"))
            continue
        spans.append(gap)
        if snap.total_assets:
            sample_aum += snap.total_assets

        prev_shares = _as_float(prev.get("shares_outstanding"))
        # 股數要先通過可信度檢查——今昨兩天都要通過，否則差額沒有意義
        if (prev_shares and snap.shares_outstanding
                and shares_are_consistent(snap.shares_outstanding, snap.close, snap.total_assets)
                and shares_are_consistent(prev_shares, prev.get("close"), prev.get("total_assets"))):
            change = (snap.shares_outstanding - prev_shares) / prev_shares
            if abs(change) > IMPLAUSIBLE_SHARE_CHANGE_RATIO:
                # 分割會讓股數瞬間倍增／減半，那不是申贖
                skipped.append((snap.ticker, f"股數單日變動 {change:+.0%}，研判為分割或資料異常"))
            else:
                share_pairs[snap.ticker] = (snap.shares_outstanding - prev_shares) * snap.close

        prev_aum, prev_close = _as_float(prev.get("total_assets")), _as_float(prev.get("close"))
        if prev_aum and prev_close and snap.total_assets:
            # 把市值變動中「漲跌造成的部分」扣掉，剩下的才是資金進出
            market_return = snap.close / prev_close
            aum_pairs[snap.ticker] = snap.total_assets - prev_aum * market_return

    usable = set(share_pairs) | set(aum_pairs)
    use_shares = bool(share_pairs) and len(share_pairs) >= max(
        SHARES_BASIS_MIN_COUNT, SHARES_BASIS_MIN_FRACTION * len(usable)
    )
    pairs = share_pairs if use_shares else aum_pairs
    basis = "shares" if use_shares else "aum"

    if not pairs:
        if not previous:
            # 這是第一天（或同一天重跑）的正常狀態，不是故障。訊息要講清楚差別，
            # 否則「等明天就好」跟「壞掉了」在畫面上長得一模一樣。
            raise ValueError(
                f"尚無前一日觀測可比較（今日已取得 {len(today)} 檔快照並存檔）；"
                "資金流需要相隔一天的兩次觀測，明日起即可計算"
            )
        raise ValueError(
            "無法計算 ETF 資金流：沒有任何一檔取得可用的前後兩次觀測；"
            f"共 {len(today)} 檔，略過原因={skipped[:6]}"
        )

    # 跨越多天時攤平成每日，否則長假後那一筆會被當成暴量單日訊號
    span = max(1, round(sum(spans) / len(spans))) if spans else 1
    total_usd = sum(pairs.values()) / span
    return FlowResult(
        as_of=stamp,
        net_flow_musd=round(total_usd / 1e6, 2),
        # 佔樣本淨資產的比例：這才是跨日可比的量。樣本組成若有增減，
        # 絕對金額會跟著跳動，比例則不會——而計分完全依賴跟自己的歷史比。
        flow_pct_of_aum=round(100 * total_usd / sample_aum, 5) if sample_aum else None,
        basis=basis,
        etfs_used=len(pairs),
        tickers_used=sorted(pairs),
        etfs_skipped=skipped,
        days_spanned=span,
    )
