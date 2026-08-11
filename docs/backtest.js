// 模組三：回測結果。資料由 scripts/run_backtest.py 產生。
const DATA_PATH = "data/backtest/latest.json";

const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escapeHtml = (v) =>
  v === null || v === undefined ? "" : String(v).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);

const state = { report: null, charts: {} };

function fmt(v, digits = 2, suffix = "") {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return Number(v).toFixed(digits) + suffix;
}

// 十五年的總報酬動輒四位數百分比，%.2f 會排出一整片數字牆
function fmtPct(v) {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  return (n >= 0 ? "+" : "") + (Math.abs(n) >= 1000 ? n.toFixed(0) : n.toFixed(2)) + "%";
}

function showError(msg) {
  document.getElementById("error-slot").innerHTML = `<div class="error-banner">${msg}</div>`;
}

/**
 * 這份報告最重要的一段：**它不是模組一線上分數的重現**。
 * ⑥ 政策方向依要求整類排除、另有兩項沒有可回溯的歷史，其餘五類的權重
 * 因此被按比例放大。不講清楚的話，讀者會直接把回測績效當成「照著儀表板
 * 做會有的結果」，而那兩者是不同的東西。
 */
function renderCaveats(report) {
  const c = report.coverage;
  const a = report.assumptions;
  const list = (arr) => arr.map((i) => `${escapeHtml(i.label)}`).join("、");

  // 覆蓋率偏低的指標要單獨講：名列「已採用」但多數時間沒有值的指標，
  // 對結果的影響遠小於它在名單上看起來的樣子。
  const low = c.low_coverage_items || [];
  const lowHtml = low.length ? `<div class="warning-box">
    <strong>${low.length} 項指標在回測期間多數時候沒有資料。</strong>
    它們列在「已採用」裡，但實際只在部分期間參與計分——
    名單長度與實際運作的指標數不是同一回事。
    <div style="margin-top:8px">${low.map((i) =>
      `<div>· ${escapeHtml(i.label)}：僅涵蓋 <strong>${i.coverage_pct}%</strong> 的交易日</div>`
    ).join("")}</div>
  </div>` : "";

  const html = `<div class="warning-box">
    <strong>這個回測不是模組一線上分數的重現。</strong>
    綜合分數由 ${c.categories_used.length} 個類別、${c.items_used.length} 項指標算出，
    而線上是 6 個類別、18 項。缺少的類別會讓其餘類別的權重<strong>按比例放大</strong>，
    因此同一天的回測分數與線上分數不會相同。
    <div style="margin-top:8px">
      <div>· 依要求排除（${c.items_excluded_by_request.length} 項）：${list(c.items_excluded_by_request)}</div>
      <div>· 無歷史可回溯（${c.items_unavailable.length} 項）：${
        c.items_unavailable.map((i) => `${escapeHtml(i.label)}<span class="subtitle">（${escapeHtml(i.reason)}）</span>`).join("、")
      }</div>
    </div>
  </div>`;
  document.getElementById("caveat-slot").innerHTML = html + lowHtml + coverageTable(c);

  const splice = a.cash_splice || {};
  const spliceText = splice.annual_diff_pct !== undefined && splice.annual_diff_pct !== null
    ? `現金部位：${escapeHtml(splice.basis)}（SGOV 2020-05 才成立，更早期間以 BIL 銜接；`
      + `重疊 ${splice.overlap_days} 個交易日的年化報酬差異 ${splice.annual_diff_pct}%，已驗證可互換）`
    : `現金部位：${escapeHtml(splice.basis || "—")}${splice.note ? "（" + escapeHtml(splice.note) + "）" : ""}`;

  document.getElementById("assumption-note").innerHTML =
    `<strong>交易假設：</strong>訊號以當日收盤資料算出、<strong>次一交易日</strong>成交`
    + `（用當日收盤價成交等於用當天才知道的資訊，回測會漂亮但無法複製）；`
    + `單邊換手成本 ${a.cost_bps_per_side} 基點；僅在階梯分級改變時換倉；`
    + `${a.gates_applied ? "已" : "未"}套用價格與脆弱度閘門。${spliceText}。`;
}

/** 逐項覆蓋率表：讓「採用 13 項」與「這 13 項各自涵蓋多少期間」擺在一起看。 */
function coverageTable(c) {
  const rows = (c.items_used || [])
    .slice()
    .sort((a, b) => (a.coverage_pct ?? 0) - (b.coverage_pct ?? 0))
    .map((i) => {
      const v = i.coverage_pct;
      const cls = v === null || v === undefined ? "" : v < 50 ? "neg" : v >= 99 ? "pos" : "";
      return `<tr><td>${escapeHtml(i.label)}</td>
        <td class="num ${cls}">${v === null || v === undefined ? "—" : v.toFixed(1) + "%"}</td></tr>`;
    }).join("");
  return `<section class="card"><h2>各指標實際涵蓋期間</h2>
    <div class="table-scroll"><table>
      <colgroup><col style="width:70%" /><col style="width:30%" /></colgroup>
      <thead><tr><th>指標</th><th class="num">涵蓋交易日比例</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <p class="subtitle">來源給得出多少歷史就是多少，不足的期間該指標不參與計分
    （類別平均只對有值的項目取平均）。比例低的指標對回測結果的影響，遠小於它在名單上看起來的樣子。</p>
  </section>`;
}

function renderStats(report) {
  const s = report.metrics.strategy;
  const q = report.metrics.qqq;
  const tiles = [
    { label: "回測期間", value: `${report.period.start.slice(0, 4)}–${report.period.end.slice(0, 4)}`, text: true },
    { label: "策略年化報酬", value: fmt(s.cagr_pct, 2, "%"), signed: s.cagr_pct },
    { label: "QQQ 年化報酬", value: fmt(q.cagr_pct, 2, "%"), signed: q.cagr_pct },
    { label: "策略最大回撤", value: fmt(s.max_drawdown_pct, 1, "%"), signed: s.max_drawdown_pct },
    { label: "QQQ 最大回撤", value: fmt(q.max_drawdown_pct, 1, "%"), signed: q.max_drawdown_pct },
    { label: "策略 Sharpe", value: fmt(s.sharpe, 2), plain: true },
    { label: "換倉次數", value: String(report.exposure.rebalances), plain: true },
  ];
  document.getElementById("stat-row").innerHTML = tiles.map((t) => {
    const cls = [
      t.plain ? "" : t.signed > 0 ? "pos" : t.signed < 0 ? "neg" : "",
      t.text ? "is-text" : "",
    ].filter(Boolean).join(" ");
    return `<div class="stat-tile"><div class="stat-value ${cls}">${escapeHtml(t.value)}</div>
      <div class="stat-label">${escapeHtml(t.label)}</div></div>`;
  }).join("");

  document.getElementById("period-note").textContent =
    `${report.period.start} ~ ${report.period.end}，共 ${report.period.trading_days.toLocaleString()} 個交易日；`
    + `產生時間 ${report.generated_at}。`;
}

function renderMetricsTable(report) {
  const order = ["strategy", "strategy_gross", "qqq", "tqqq"];
  document.querySelector("#metrics-table tbody").innerHTML = order.map((k) => {
    const m = report.metrics[k];
    if (!m) return "";
    const cls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
    return `<tr>
      <td>${escapeHtml(m.label)}</td>
      <td class="num ${cls(m.total_return_pct)}">${fmtPct(m.total_return_pct)}</td>
      <td class="num ${cls(m.cagr_pct)}">${fmt(m.cagr_pct, 2, "%")}</td>
      <td class="num">${fmt(m.volatility_pct, 1, "%")}</td>
      <td class="num">${fmt(m.sharpe, 2)}</td>
      <td class="num neg">${fmt(m.max_drawdown_pct, 1, "%")}</td>
      <td class="num">${fmt(m.win_rate_pct, 1, "%")}</td>
    </tr>`;
  }).join("");
}

const LINE = { borderWidth: 1.6, pointRadius: 0, tension: 0.1, backgroundColor: "transparent" };

function baseOptions(extra = {}) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { display: false }, ticks: { color: cssVar("--text-muted"), maxTicksLimit: 10, font: { size: 11 } } },
      y: { grid: { color: cssVar("--hairline") }, ticks: { color: cssVar("--text-muted"), font: { size: 11 } } },
    },
    ...extra,
  };
}

function draw(id, config) {
  if (state.charts[id]) state.charts[id].destroy();
  state.charts[id] = new Chart(document.getElementById(id), config);
}

function renderEquityChart(report) {
  const c = report.chart;
  const series = [
    ["strategy", "模組一策略", cssVar("--series-1")],
    ["qqq", "QQQ 買進持有", cssVar("--series-4")],
    ["tqqq", "TQQQ 買進持有", cssVar("--series-2")],
  ];
  draw("equity-chart", {
    type: "line",
    data: {
      labels: c.dates,
      datasets: series.map(([k, label, color]) => ({
        label, data: c.equity[k], borderColor: color, ...LINE,
      })),
    },
    // 對數刻度：十五年跨越兩個數量級，線性刻度會把前十年壓成一條平線
    options: baseOptions({
      scales: {
        x: { grid: { display: false }, ticks: { color: cssVar("--text-muted"), maxTicksLimit: 10, font: { size: 11 } } },
        y: {
          type: "logarithmic",
          grid: { color: cssVar("--hairline") },
          ticks: { color: cssVar("--text-muted"), font: { size: 11 } },
        },
      },
    }),
  });
  document.getElementById("equity-legend").innerHTML = series
    .map(([, label, color]) => `<span><span class="dot" style="background:${color}"></span>${label}</span>`)
    .join("");
}

// 綜合分數的燈號區間背景，與模組一首頁同一組色帶
const scoreBandPlugin = {
  id: "scoreBands",
  beforeDraw(chart) {
    const { ctx, chartArea, scales } = chart;
    if (!chartArea || !scales.score) return;
    const bands = [
      [1.2, 2.0, "rgba(30,122,79,0.10)"], [0.4, 1.2, "rgba(30,122,79,0.05)"],
      [-0.4, 0.4, "rgba(184,134,11,0.06)"], [-1.2, -0.4, "rgba(194,102,47,0.07)"],
      [-2.0, -1.2, "rgba(168,58,50,0.09)"],
    ];
    bands.forEach(([lo, hi, color]) => {
      const yTop = scales.score.getPixelForValue(hi);
      const yBot = scales.score.getPixelForValue(lo);
      ctx.save();
      ctx.fillStyle = color;
      ctx.fillRect(chartArea.left, yTop, chartArea.right - chartArea.left, yBot - yTop);
      ctx.restore();
    });
  },
};

function renderScoreChart(report) {
  const c = report.chart;
  draw("score-chart", {
    type: "line",
    data: {
      labels: c.dates,
      datasets: [
        { label: "QQQ", data: c.qqq_price, borderColor: cssVar("--text-muted"),
          yAxisID: "price", ...LINE, borderWidth: 1.2 },
        { label: "綜合分數", data: c.composite_score, borderColor: cssVar("--series-1"),
          yAxisID: "score", ...LINE },
      ],
    },
    options: baseOptions({
      scales: {
        x: { grid: { display: false }, ticks: { color: cssVar("--text-muted"), maxTicksLimit: 10, font: { size: 11 } } },
        price: { type: "logarithmic", position: "right", grid: { display: false },
                 ticks: { color: cssVar("--text-muted"), font: { size: 11 } } },
        score: { position: "left", min: -2, max: 2, grid: { color: cssVar("--hairline") },
                 ticks: { color: cssVar("--text-muted"), font: { size: 11 } } },
      },
    }),
    plugins: [scoreBandPlugin],
  });
}

function renderCategoryChart(report) {
  const c = report.chart;
  const cat = document.getElementById("category-picker").value;
  const ind = document.getElementById("indicator-picker").value;
  const datasets = [
    { label: "QQQ", data: c.qqq_price, borderColor: cssVar("--text-muted"),
      yAxisID: "price", ...LINE, borderWidth: 1.2 },
  ];
  if (cat && c.category_scores[cat]) {
    datasets.push({ label: catLabel(cat), data: c.category_scores[cat],
                    borderColor: cssVar("--series-3"), yAxisID: "score", ...LINE });
  }
  if (ind && c.indicator_scores[ind]) {
    // 個別指標是 -2..2 的整數階梯，用階梯線畫才不會有不存在的中間值
    datasets.push({ label: itemLabel(ind), data: c.indicator_scores[ind],
                    borderColor: cssVar("--series-5"), yAxisID: "score",
                    ...LINE, stepped: true });
  }
  draw("category-chart", {
    type: "line",
    data: { labels: c.dates, datasets },
    options: baseOptions({
      scales: {
        x: { grid: { display: false }, ticks: { color: cssVar("--text-muted"), maxTicksLimit: 10, font: { size: 11 } } },
        price: { type: "logarithmic", position: "right", grid: { display: false },
                 ticks: { color: cssVar("--text-muted"), font: { size: 11 } } },
        score: { position: "left", min: -2.2, max: 2.2, grid: { color: cssVar("--hairline") },
                 ticks: { color: cssVar("--text-muted"), stepSize: 1, font: { size: 11 } } },
      },
    }),
  });
  document.getElementById("relation-note").textContent =
    "左軸為分數（-2 ~ +2）、右軸為 QQQ 價格（對數）。"
    + "分數領先或落後於價格轉折，是判斷這個指標有沒有用的直接方式——"
    + "與價格同步的指標無法提供任何提前的資訊。";
}

let LABELS = { categories: {}, items: {} };
const catLabel = (k) => LABELS.categories[k] || k;
const itemLabel = (k) => LABELS.items[k] || k;

function populatePickers(report) {
  const c = report.coverage;
  c.categories_used.forEach((x) => { LABELS.categories[x.key] = x.label; });
  c.items_used.forEach((x) => { LABELS.items[x.key] = x.label; });

  const catSel = document.getElementById("category-picker");
  catSel.innerHTML = c.categories_used
    .filter((x) => report.chart.category_scores[x.key])
    .map((x) => `<option value="${escapeHtml(x.key)}">${escapeHtml(x.label)}</option>`).join("");

  const indSel = document.getElementById("indicator-picker");
  indSel.innerHTML = '<option value="">（不顯示）</option>'
    + c.items_used
      .filter((x) => report.chart.indicator_scores[x.key])
      .map((x) => `<option value="${escapeHtml(x.key)}">${escapeHtml(x.label)}</option>`).join("");

  catSel.addEventListener("change", () => renderCategoryChart(report));
  indSel.addEventListener("change", () => renderCategoryChart(report));
}

function renderWeights(report) {
  const c = report.chart;
  const stack = [
    ["SGOV", cssVar("--series-5")], ["QQQ", cssVar("--series-4")], ["TQQQ", cssVar("--series-2")],
  ].filter(([k]) => c.weights[k]);
  draw("weights-chart", {
    type: "line",
    data: {
      labels: c.dates,
      datasets: stack.map(([k, color]) => ({
        label: k, data: c.weights[k], borderColor: color, backgroundColor: color,
        borderWidth: 0, pointRadius: 0, fill: true, stepped: true,
      })),
    },
    options: baseOptions({
      scales: {
        x: { grid: { display: false }, ticks: { color: cssVar("--text-muted"), maxTicksLimit: 10, font: { size: 11 } } },
        y: { stacked: true, min: 0, max: 1, grid: { color: cssVar("--hairline") },
             ticks: { color: cssVar("--text-muted"), font: { size: 11 },
                      callback: (v) => `${(v * 100).toFixed(0)}%` } },
      },
    }),
  });

  const days = report.exposure.ladder_days;
  const total = Object.values(days).reduce((a, b) => a + b, 0) || 1;
  document.querySelector("#exposure-table tbody").innerHTML = Object.entries(days)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `<tr><td>${escapeHtml(k)}</td><td class="num">${v.toLocaleString()}</td>
      <td class="num">${((100 * v) / total).toFixed(1)}%</td></tr>`).join("");
}

async function main() {
  try {
    const res = await fetch(DATA_PATH, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.report = await res.json();
  } catch (e) {
    showError(`讀取回測結果失敗：${escapeHtml(e.message)}。`
      + `可能是回測尚未執行過，${escapeHtml(DATA_PATH)} 還不存在——`
      + `可執行 <code>python3 scripts/run_backtest.py -v</code> 產生。`);
    return;
  }
  const r = state.report;
  renderStats(r);
  renderCaveats(r);
  renderMetricsTable(r);
  renderEquityChart(r);
  renderScoreChart(r);
  populatePickers(r);
  renderCategoryChart(r);
  renderWeights(r);
}

main();
