// 模組九：買評缺口雷達。資料每週更新一次（見 .github/workflows/gap-radar.yml），
// 讀 docs/data/gap_radar/latest.json——那份資料本身是模組二報告的篩選結果，
// 兩邊共用「上漲空間」「分歧度」的定義，見 gap_radar/pipeline.py 的說明。
//
// 兩份資料源（S&P 500／Nasdaq-100）各自獨立跑一次候選股池與風險分層——
// 不合併成一份，因為分歧度的「高」是相對各自候選股池的分布而言，混在一起
// 算會讓兩邊互相污染彼此的門檻。

const DATA_SOURCES = {
  sp500: { path: "data/gap_radar/latest.json", label: "S&P 500" },
  ndx100: { path: "data/gap_radar/ndx100_latest.json", label: "Nasdaq-100" },
};

const state = { source: "sp500" };

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escapeHtml = (v) =>
  v === null || v === undefined ? "" : String(v).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);

const TIER_LABEL = { extreme: "極端", high: "高", moderate: "中", low: "低" };
const TIER_CLASS = { extreme: "tier-extreme", high: "tier-high", moderate: "tier-moderate", low: "tier-low" };

function fmtCap(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return null;
  const n = Number(v);
  for (const [unit, scale] of [["T", 1e12], ["B", 1e9], ["M", 1e6]]) {
    if (n >= scale) return `$${(n / scale).toFixed(2)}${unit}`;
  }
  return `$${(n / 1e6).toFixed(2)}M`;
}

function showError(msg) {
  document.getElementById("error-slot").innerHTML = `<div class="error-banner">${msg}</div>`;
}

function renderStats(report) {
  const c = report.counts;
  const ds = report.dispersion_stats || {};
  const tiles = [
    { label: "資料基準日", value: report.as_of, text: true },
    { label: `獲買評（全樣本 ${c.universe} 檔）`, value: `${c.candidates}`, plain: true },
    { label: "其中：買入 / 強力買入", value: `${c.buy} / ${c.strong_buy}`, text: true },
    { label: "買評股中位數上漲空間", value: `${report.median_upside_pct.toFixed(1)}%`, plain: true },
    { label: "買評股分歧度中位數", value: `${ds.median.toFixed(1)}%`, plain: true },
    { label: "分歧度前10%高點", value: `${ds.p90.toFixed(1)}%`, plain: true },
  ];
  document.getElementById("stat-row").innerHTML = tiles
    .map((t) => `<div class="stat-tile">
        <div class="stat-value${t.text ? " is-text" : ""}">${escapeHtml(t.value)}</div>
        <div class="stat-label">${escapeHtml(t.label)}</div>
      </div>`)
    .join("");

  document.getElementById("header-subtitle").textContent =
    `股票池：${report.universe || DATA_SOURCES[state.source].label}　|　`
    + `產生時間：${report.generated_at || "—"}　|　來源：模組二 ${report.source_generated_at || "—"} 的估值報告`;

  document.getElementById("basis-note").textContent =
    "風險分層以本次候選股池（獲買入／強力買入評等且資料完整的個股）的分歧度分布為基準：" +
    `中位數 ${ds.median.toFixed(1)}%、前25%高點 ${ds.p75.toFixed(1)}%、前10%高點 ${ds.p90.toFixed(1)}%。`;
}

let scatterChart;
function renderScatterChart(report) {
  const ctx = document.getElementById("scatter-chart");
  if (scatterChart) scatterChart.destroy();

  const top10Tickers = new Set(report.top10.map((r) => r.ticker));
  const bg = report.background.filter((r) => !top10Tickers.has(r.ticker));

  const mcVals = report.background.map((r) => r.market_cap).filter((v) => v);
  const mcMin = Math.min(...mcVals), mcMax = Math.max(...mcVals);
  const radius = (mc) => {
    if (!mc) return 3;
    const t = (Math.sqrt(mc) - Math.sqrt(mcMin)) / (Math.sqrt(mcMax) - Math.sqrt(mcMin) || 1);
    return 3 + t * 11;
  };

  const toPoint = (r) => ({ x: r.upside_pct, y: r.target_dispersion_pct, r: radius(r.market_cap), _t: r });

  scatterChart = new Chart(ctx, {
    type: "bubble",
    data: {
      datasets: [
        {
          label: "其餘買評股",
          data: bg.map(toPoint),
          backgroundColor: cssVar("--hairline-strong"),
          borderWidth: 0,
        },
        {
          label: "本次入選前10名",
          data: report.top10.map((r) => ({
            x: r.upside_pct, y: r.target_dispersion_pct, r: radius(r.market_cap), _t: r,
          })),
          backgroundColor: cssVar("--series-1"),
          borderColor: cssVar("--surface-1"),
          borderWidth: 1.5,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: "top",
          align: "start",
          labels: { color: cssVar("--text-secondary"), boxWidth: 8, boxHeight: 8, font: { size: 12 } },
        },
        tooltip: {
          callbacks: {
            label: (c) => {
              const t = c.raw._t;
              return `${t.ticker}　上漲空間 ${t.upside_pct.toFixed(1)}%　分歧度 ${t.target_dispersion_pct.toFixed(1)}%`;
            },
          },
        },
      },
      scales: {
        x: {
          title: { display: true, text: "上漲空間", color: cssVar("--text-muted") },
          grid: { color: cssVar("--hairline") },
          ticks: { color: cssVar("--text-muted"), callback: (v) => `${v}%` },
        },
        y: {
          title: { display: true, text: "目標價分歧度", color: cssVar("--text-muted") },
          grid: { color: cssVar("--hairline") },
          ticks: { color: cssVar("--text-muted"), callback: (v) => `${v}%` },
        },
      },
    },
  });
}

function rankChangeCell(r) {
  if (r.is_new) return '<span class="rank-change is-new">新進榜</span>';
  if (r.prev_rank === null || r.prev_rank === undefined) return '<span class="rank-change">—</span>';
  const delta = r.prev_rank - r.rank; // 正值代表名次進步（數字變小）
  if (delta > 0) return `<span class="rank-change pos">▲ ${delta}</span>`;
  if (delta < 0) return `<span class="rank-change neg">▼ ${-delta}</span>`;
  return '<span class="rank-change">持平</span>';
}

function renderTable(report) {
  const tbody = document.querySelector("#gap-table tbody");
  tbody.innerHTML = report.top10
    .map((r) => {
      const recLabel = r.recommendation_key === "strong_buy" ? "強力買入" : "買入";
      const recClass = r.recommendation_key === "strong_buy" ? "strong-buy" : "buy";
      const cap = fmtCap(r.market_cap);
      return `<tr>
        <td class="num">${r.rank}</td>
        <td class="ticker-cell">${escapeHtml(r.ticker)} <span class="badge ${recClass}">${recLabel}</span></td>
        <td>
          <div>${escapeHtml(r.name)}</div>
          <div class="subtitle" style="font-size:var(--fs-2)">${escapeHtml(r.sector)}${cap ? "　" + cap : ""}</div>
        </td>
        <td class="num">$${r.close.toFixed(2)} → $${r.consensus_target.toFixed(2)}</td>
        <td class="num pos">+${r.upside_pct.toFixed(1)}%</td>
        <td>
          <span class="${TIER_CLASS[r.risk_tier]}">${r.target_dispersion_pct.toFixed(1)}%（${TIER_LABEL[r.risk_tier]}）</span>
        </td>
        <td class="num">${r.analyst_count ?? "—"}</td>
        <td>${rankChangeCell(r)}</td>
      </tr>`;
    })
    .join("");

  document.getElementById("table-note").textContent =
    `候選股池共 ${report.counts.candidates} 檔（買入 ${report.counts.buy} / 強力買入 ${report.counts.strong_buy}），` +
    "「較上次」比較的是本頁上一次快照的名次，非投資建議。";
}

function renderCaveats(report) {
  const items = [
    "目標價落差不是保證報酬，分析師普遍存在偏多傾向，且共識價會隨股價事後調整。",
    "分歧度＝（最高目標價－最低目標價）／目標價中位數，反映機構之間認知的分散程度，不是統計上的標準差；機構數越少，這個數字越容易被單一極端值放大。",
    "資料以模組二取得的共識目標價來源為主（詳見 valuation.html 頁尾），屬單一資料源快照。",
    `資料基準日為 ${report.as_of}，每週更新一次，個股評等與目標價可能於財報季後快速變動。`,
  ];
  document.getElementById("caveats-list").innerHTML = items.map((t) => `<li>${escapeHtml(t)}</li>`).join("");
}

function wireSourceSwitch() {
  document.querySelectorAll("#source-switch button").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.source === state.source) return;
      state.source = btn.dataset.source;
      document.querySelectorAll("#source-switch button").forEach((b) =>
        b.classList.toggle("active", b.dataset.source === state.source)
      );
      loadAndRender();
    });
  });
}

async function loadAndRender() {
  const src = DATA_SOURCES[state.source];
  document.getElementById("error-slot").innerHTML = "";
  let report;
  try {
    const res = await fetch(src.path, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    report = await res.json();
  } catch (e) {
    showError(
      `讀取${src.label}買評缺口資料失敗：${escapeHtml(e.message)}。` +
      `可能是模組九的排程尚未執行過，${escapeHtml(src.path)} 還不存在。`
    );
    return;
  }

  renderStats(report);
  renderScatterChart(report);
  renderTable(report);
  renderCaveats(report);
}

wireSourceSwitch();
loadAndRender();
