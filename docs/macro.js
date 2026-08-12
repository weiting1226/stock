// 模組五：總體經濟觀察。資料由 scripts/run_macro.py 產生。
const DATA_PATH = "data/macro/latest.json";

const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escapeHtml = (v) =>
  v === null || v === undefined ? "" : String(v).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);

const state = { report: null, charts: {} };

// 缺值一律顯示破折號。顯示 0 會讓「還沒公布」看起來像「這期剛好沒變」
function num(v, digits = 2, suffix = "") {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return Number(v).toFixed(digits) + suffix;
}

function signed(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  return (n > 0 ? "+" : "") + n.toFixed(digits);
}

const DIRECTION_CLASS = { "改善": "pos", "惡化": "neg", "—": "" };

function draw(id, config) {
  if (state.charts[id]) state.charts[id].destroy();
  state.charts[id] = new Chart(document.getElementById(id), config);
}

function showError(msg) {
  document.getElementById("error-slot").innerHTML = `<div class="error-banner">${msg}</div>`;
}

function renderStats(r) {
  const c = r.counts;
  const totals = Object.values(r.summary).reduce(
    (a, s) => ({ imp: a.imp + s.improving, wor: a.wor + s.worsening }), { imp: 0, wor: 0 });
  const worstLag = r.rows.filter((x) => x.available)
    .reduce((a, x) => (x.lag_days > (a?.lag_days ?? -1) ? x : a), null);

  const tiles = [
    { label: "資料基準日", value: r.as_of, text: true },
    { label: "可用指標", value: `${c.available} / ${c.total}`, plain: true },
    { label: "三個月改善", value: String(totals.imp), signed: 1 },
    { label: "三個月惡化", value: String(totals.wor), signed: -1 },
    { label: "過期未更新", value: String(c.stale), signed: c.stale ? -1 : 0 },
    { label: "時滯最長", value: worstLag ? `${worstLag.lag_days} 天` : "—", plain: true },
  ];
  document.getElementById("stat-row").innerHTML = tiles.map((t) => {
    const cls = [t.plain ? "" : t.signed > 0 ? "pos" : t.signed < 0 ? "neg" : "",
                 t.text ? "is-text" : ""].filter(Boolean).join(" ");
    return `<div class="stat-tile"><div class="stat-value ${cls}">${escapeHtml(t.value)}</div>
      <div class="stat-label">${escapeHtml(t.label)}</div></div>`;
  }).join("");

  document.getElementById("asof-note").textContent =
    `${worstLag ? `時滯最長的是「${worstLag.label}」（參考期 ${worstLag.reference_date}）。` : ""}`
    + `產生時間 ${r.generated_at}。`;
  document.getElementById("method-note").textContent = (r.notes || []).join(" ");
}

/**
 * 過期與缺失要分開講。兩者在畫面上都是「沒有新數字」，但處置完全不同：
 * 過期是「這一期還沒公布」（等就好），缺失是「抓不到」（要去修）。
 */
function renderWarnings(r) {
  let html = "";
  if (r.stale_series && r.stale_series.length) {
    html += `<div class="warning-box"><strong>${r.stale_series.length} 條指標超過預期的發布間隔仍未更新。</strong>
      每條指標的判定門檻依它自己的發布節奏設定（週頻與季頻不能用同一把尺量）。
      <div style="margin-top:8px">${r.stale_series.map((s) =>
        `<div>· ${escapeHtml(s.label)}：參考期 ${escapeHtml(s.reference_date)}，落後 <strong>${s.lag_days}</strong> 天</div>`
      ).join("")}</div></div>`;
  }
  if (r.missing_series && r.missing_series.length) {
    html += `<div class="warning-box"><strong>${r.missing_series.length} 條指標抓不到。</strong>
      這與「還沒公布」不同——需要檢查來源。
      <div style="margin-top:8px">${r.missing_series.map((s) =>
        `<div>· ${escapeHtml(s.label)}（${escapeHtml(s.fred_id)}）：${escapeHtml(s.error || "")}</div>`
      ).join("")}</div></div>`;
  }
  document.getElementById("warning-slot").innerHTML = html;
}

function renderSummary(r) {
  const keys = Object.keys(r.categories);
  draw("summary-chart", {
    type: "bar",
    data: {
      labels: keys.map((k) => r.categories[k]),
      datasets: [
        { label: "改善", data: keys.map((k) => r.summary[k].improving),
          backgroundColor: cssVar("--good"), borderRadius: 3 },
        { label: "惡化", data: keys.map((k) => -r.summary[k].worsening),
          backgroundColor: cssVar("--critical"), borderRadius: 3 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: true, labels: { color: cssVar("--text-secondary"),
                                           boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label} ${Math.abs(c.raw)} 項` } },
      },
      scales: {
        x: { stacked: true, grid: { display: false },
             ticks: { color: cssVar("--text-muted"), font: { size: 11 } } },
        // 惡化畫成負值，零軸兩側一眼分得出方向
        y: { stacked: true, grid: { color: cssVar("--hairline") },
             ticks: { color: cssVar("--text-muted"), font: { size: 11 },
                      callback: (v) => Math.abs(v) } },
      },
    },
  });
}

// 表格內的迷你走勢圖：用 inline SVG，不另外開 canvas
function sparkSvg(chart, positive) {
  if (!chart || !chart.values || chart.values.length < 3) return "—";
  const v = chart.values;
  const min = Math.min(...v), max = Math.max(...v);
  const span = max - min || 1;
  const w = 80, h = 20;
  const pts = v.map((y, i) =>
    `${(i / (v.length - 1) * w).toFixed(1)},${(h - (y - min) / span * h).toFixed(1)}`).join(" ");
  const color = cssVar(positive ? "--good" : positive === false ? "--critical" : "--text-muted");
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.2" /></svg>`;
}

function filteredRows(r) {
  const cat = document.getElementById("category-filter").value;
  const sort = document.getElementById("sort-by").value;
  const onlyStale = document.getElementById("only-stale").checked;

  let rows = r.rows.slice();
  if (cat) rows = rows.filter((x) => x.category === cat);
  if (onlyStale) rows = rows.filter((x) => x.stale);

  const order = Object.keys(r.categories);
  if (sort === "lag") {
    rows.sort((a, b) => (b.lag_days ?? -1) - (a.lag_days ?? -1));
  } else if (sort === "percentile") {
    // 缺值排最後，不因排序方向而變動
    rows.sort((a, b) => (b.percentile_20y ?? -1) - (a.percentile_20y ?? -1));
  } else {
    rows.sort((a, b) => order.indexOf(a.category) - order.indexOf(b.category));
  }
  return rows;
}

function renderTable(r) {
  const rows = filteredRows(r);
  document.querySelector("#macro-table tbody").innerHTML = rows.map((x) => {
    if (!x.available) {
      return `<tr><td>${escapeHtml(x.label)}<span class="subtitle"> ${escapeHtml(x.fred_id)}</span></td>
        <td class="num pb-issue" colspan="8" title="${escapeHtml(x.error || "")}">抓不到資料</td></tr>`;
    }
    const dirCls = DIRECTION_CLASS[x.direction_3m] || "";
    const lagCls = x.stale ? "neg" : "";
    const tip = [x.note, `FRED ${x.fred_id}`,
                 x.transform === "yoy" ? "顯示年增率" : x.transform === "diff" ? "顯示期間變化" : ""]
      .filter(Boolean).join("　");
    return `<tr title="${escapeHtml(tip)}">
      <td>${escapeHtml(x.label)}<span class="subtitle"> ${escapeHtml(x.fred_id)}</span></td>
      <td class="num">${num(x.value, 2, x.unit === "%" ? "%" : "")}</td>
      <td class="date">${escapeHtml(x.reference_date)}</td>
      <td class="num ${lagCls}">${x.lag_days} 天${x.stale ? " ⚠" : ""}</td>
      <td class="num">${signed(x.change_3m)}</td>
      <td class="num">${signed(x.change_12m)}</td>
      <td class="num">${x.percentile_20y === null ? "—" : x.percentile_20y.toFixed(0) + "%"}</td>
      <td class="${dirCls}">${escapeHtml(x.direction_3m)}</td>
      <td>${sparkSvg(r.charts[x.fred_id],
                     x.direction_3m === "改善" ? true : x.direction_3m === "惡化" ? false : null)}</td>
    </tr>`;
  }).join("");

  document.getElementById("table-note").textContent =
    `顯示 ${rows.length} / ${r.rows.length} 條。百分位以最近 ${r.percentile_years} 年為比較期間；`
    + "「方向」只對有明確好壞的指標顯示，通膨與殖利率一律留「—」。"
    + "滑鼠移到列上可看該指標的說明與 FRED 代碼。";
}

function renderSeriesChart(r) {
  const id = document.getElementById("series-picker").value;
  const row = r.rows.find((x) => x.fred_id === id);
  const chart = r.charts[id];
  if (!row || !chart) return;

  draw("series-chart", {
    type: "line",
    data: {
      labels: chart.dates,
      datasets: [{
        label: row.label,
        data: chart.values,
        borderColor: cssVar("--series-1"),
        backgroundColor: "transparent",
        borderWidth: 1.8, pointRadius: 0, tension: 0.15,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { color: cssVar("--text-muted"),
             maxTicksLimit: 10, font: { size: 11 } } },
        y: { grid: { color: cssVar("--hairline") },
             ticks: { color: cssVar("--text-muted"), font: { size: 11 } } },
      },
    },
  });

  const what = row.transform === "yoy" ? "年增率" : row.transform === "diff" ? "期間變化" : "水準";
  document.getElementById("series-note").textContent =
    `${row.label}（FRED ${row.fred_id}）——顯示的是${what}，單位 ${row.unit || "指數"}。`
    + `參考期 ${row.reference_date}，落後 ${row.lag_days} 天。`
    + (row.note ? `　${row.note}` : "");
}

function wire(r) {
  const catSel = document.getElementById("category-filter");
  catSel.innerHTML = '<option value="">全部類別</option>'
    + Object.entries(r.categories)
        .map(([k, v]) => `<option value="${escapeHtml(k)}">${escapeHtml(v)}</option>`).join("");

  const picker = document.getElementById("series-picker");
  picker.innerHTML = r.rows.filter((x) => x.available && r.charts[x.fred_id])
    .map((x) => `<option value="${escapeHtml(x.fred_id)}">`
      + `${escapeHtml(r.categories[x.category])}｜${escapeHtml(x.label)}</option>`).join("");

  ["category-filter", "sort-by", "only-stale"].forEach((id) =>
    document.getElementById(id).addEventListener("change", () => renderTable(r)));
  picker.addEventListener("change", () => renderSeriesChart(r));
}

async function main() {
  try {
    const res = await fetch(DATA_PATH, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.report = await res.json();
  } catch (e) {
    showError(`讀取總經資料失敗：${escapeHtml(e.message)}。`
      + `可能是尚未執行過，${escapeHtml(DATA_PATH)} 還不存在——`
      + `可執行 <code>python3 scripts/run_macro.py -v</code> 產生。`);
    return;
  }
  const r = state.report;
  renderStats(r);
  renderWarnings(r);
  renderSummary(r);
  wire(r);
  renderTable(r);
  renderSeriesChart(r);
}

main();
