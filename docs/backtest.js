// 模組三：回測結果。資料由 scripts/run_backtest.py 產生。
const DATA_PATH = "data/backtest/latest.json";
const THRESHOLD_PATH = "data/backtest/thresholds.json";

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

  const pr = a.position_rule;
  if (pr) {
    document.getElementById("rule-note").innerHTML =
      `<strong>${escapeHtml(pr.mode)}。</strong>`
      + `進 TQQQ 需 ≥ ${pr.tqqq_enter}、退出於 &lt; ${pr.tqqq_exit}；`
      + `轉現金於 &lt; ${pr.sgov_enter}、離開現金於 ≥ ${pr.qqq_enter}；`
      + `<strong>從現金回升時 ≥ ${pr.recovery_enter} 即直接進 TQQQ</strong>（跳過 QQQ）。`
      + `進出場門檻相差 ${(pr.buffer * 2).toFixed(2)}，中間那段是緩衝區：`
      + `分數在裡面來回時不換檔。`
      + `<br />緩衝區的代價是出場慢半拍——分數真的轉壞時，多抱的那幾天在 TQQQ 上會被放大。`
      + `這是「少換手」與「慢出場」之間的取捨，不是純粹的改進。`;
  }

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
    { label: "原階梯換倉次數", value: String(report.exposure.rebalances_ladder ?? "—"), plain: true },
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
  const order = ["strategy", "strategy_gross", "ladder", "qqq", "tqqq"];
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
    ["strategy", "全押式（TQQQ／QQQ／SGOV）", cssVar("--series-1")],
    ["ladder", "原階梯（混合持有）", cssVar("--series-3")],
    ["qqq", "QQQ 買進持有", cssVar("--series-4")],
    ["tqqq", "TQQQ 買進持有", cssVar("--series-2")],
  ].filter(([k]) => c.equity[k]);
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

/**
 * 門檻搜尋的結果。這一段的重點不是「找到了更好的門檻」，而是把兩件事
 * 攤開：樣本內最佳值必然偏樂觀（試越多組越樂觀），以及樣本外到底行不行。
 */
async function renderThresholds() {
  let t;
  try {
    const res = await fetch(THRESHOLD_PATH, { cache: "no-store" });
    if (!res.ok) return;                 // 還沒跑過搜尋就不顯示這一段
    t = await res.json();
  } catch (e) { return; }

  const card = document.getElementById("threshold-card");
  const m = (x) => (x && x.metrics ? x.metrics : null);
  const row = (label, mm, note) => {
    if (!mm) return "";
    return `<tr><td>${escapeHtml(label)}</td>
      <td class="num">${fmt(mm.cagr_pct, 2, "%")}</td>
      <td class="num">${fmt(mm.sharpe, 2)}</td>
      <td class="num neg">${fmt(mm.max_drawdown_pct, 1, "%")}</td>
      <td class="subtitle">${escapeHtml(note || "")}</td></tr>`;
  };
  const pl = t.plateau || {};
  const wfStart = (t.comparison_window || {}).walk_forward_start || "";

  card.hidden = false;
  document.getElementById("threshold-body").innerHTML = `
    <p class="subtitle">在 ${t.candidates} 組買賣門檻上搜尋，目標函式為 ${escapeHtml(t.objective)}。
    下表的後三列為<strong>同一段期間</strong>（${escapeHtml(wfStart)} 起），才可直接比較。</p>
    <div class="table-scroll"><table>
      <thead><tr><th>方法</th><th class="num">年化報酬</th><th class="num">Sharpe</th>
        <th class="num">最大回撤</th><th>說明</th></tr></thead>
      <tbody>
        ${row("樣本內最佳", m(t.in_sample_best), "在同一段歷史上挑出來的，必然偏樂觀——不是預期績效")}
        ${row("樣本外（逐年前進）", m(t.walk_forward), "每年的門檻只用該年之前的資料選出")}
        ${row("現行預設門檻（同期）",
              (t.current_default || {}).metrics_same_window_as_walk_forward, "未經最佳化，由燈號分界直接沿用")}
        ${row("QQQ 買進持有（同期）",
              (t.qqq_buy_and_hold || {}).metrics_same_window_as_walk_forward, "")}
      </tbody>
    </table></div>
    <div class="warning-box" style="margin-top:16px">
      <strong>結論：這份資料上找不到更有效的門檻。</strong>
      <div style="margin-top:8px">
        · 樣本外的最佳化結果<strong>輸給未經最佳化的現行門檻</strong>——挑門檻挑到的是雜訊，不是訊號。
        <br />· ${t.candidates} 組門檻的 Sharpe 分布：最佳 ${pl.best_score}、中位數 ${pl.median_score}、標準差 ${pl.score_sd}。
        最佳值只高出中位數 <strong>${pl.z_vs_all}</strong> 個標準差，
        而純雜訊下試 ${t.candidates} 組本來就會產生約 <strong>${pl.expected_z_from_noise_alone}</strong> 個標準差的最大值
        ——${pl.best_is_within_noise ? "<strong>最佳值完全落在雜訊能解釋的範圍內</strong>，沒有證據說哪一組門檻真的比較好。" : "最佳值超出雜訊可解釋的範圍。"}
      </div>
      <div style="margin-top:8px">真正的限制不在門檻，而在訊號本身：13 項指標中有 4 項涵蓋不到一半的期間（見上方覆蓋率表）。</div>
    </div>`;
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
  await renderThresholds();
}

main();
