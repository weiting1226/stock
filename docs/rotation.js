// 模組四：類股資金輪動。資料由 scripts/run_sector_rotation.py 產生。
const DATA_PATH = "data/rotation/latest.json";

const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escapeHtml = (v) =>
  v === null || v === undefined ? "" : String(v).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);

const state = { report: null, charts: {} };

// 缺值一律顯示破折號。顯示 0.00% 會讓「還沒成立的類股」看起來像「今天剛好沒動」
function pct(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  return (n > 0 ? "+" : "") + n.toFixed(digits) + "%";
}

function cls(v) {
  if (v === null || v === undefined) return "";
  return v > 0 ? "pos" : v < 0 ? "neg" : "";
}

const QUADRANT_COLOR = {
  "領漲": "--good", "改善": "--series-1", "轉弱": "--warning", "落後": "--critical",
};

function showError(msg) {
  document.getElementById("error-slot").innerHTML = `<div class="error-banner">${msg}</div>`;
}

function draw(id, config) {
  if (state.charts[id]) state.charts[id].destroy();
  state.charts[id] = new Chart(document.getElementById(id), config);
}

function renderStats(r) {
  const b = r.breadth["1d"] || {};
  const bench = r.benchmark.returns || {};
  const counts = r.quadrants || {};
  const tiles = [
    { label: "資料基準日", value: r.as_of, text: true },
    { label: `大盤 ${r.benchmark.ticker} 當日`, value: pct(bench["1d"]), signed: bench["1d"] },
    { label: "上漲類股", value: `${b.up ?? "—"} / ${b.total ?? "—"}`, plain: true },
    { label: "類股中位數當日", value: pct(b.median_pct), signed: b.median_pct },
    { label: "領漲", value: String((counts["領漲"] || []).length), plain: true },
    { label: "改善", value: String((counts["改善"] || []).length), plain: true },
    { label: "轉弱", value: String((counts["轉弱"] || []).length), plain: true },
  ];
  document.getElementById("stat-row").innerHTML = tiles.map((t) => {
    const c = [t.plain ? "" : t.signed > 0 ? "pos" : t.signed < 0 ? "neg" : "",
               t.text ? "is-text" : ""].filter(Boolean).join(" ");
    return `<div class="stat-tile"><div class="stat-value ${c}">${escapeHtml(t.value)}</div>
      <div class="stat-label">${escapeHtml(t.label)}</div></div>`;
  }).join("");

  document.getElementById("asof-note").textContent =
    `基準 ${r.benchmark.ticker}：當日 ${pct(bench["1d"])}、近一週 ${pct(bench["1w"])}、`
    + `近一月 ${pct(bench["1m"])}、近三月 ${pct(bench["3m"])}。產生時間 ${r.generated_at}。`;
  document.getElementById("method-note").textContent = (r.notes || []).join(" ");
}

/**
 * 四象限散點圖。這是這一頁的核心：漲幅排行只說得出過去誰強，
 * 象限說得出資金正在往哪走。
 */
function renderQuadrant(r) {
  const pts = r.rows.filter((x) => x.rs_ratio !== null && x.rs_momentum !== null);
  const trails = (r.history || {}).trails || {};
  const trailDatasets = [];
  // 軌跡：每檔類股最近幾週的位置連成一條線。只看今天那個點，
  // 分不出「剛進入這一格」與「已經待在這裡很久」——而那是完全不同的訊息。
  pts.forEach((x) => {
    const tr = trails[x.ticker];
    if (!tr || tr.rs.length < 2) return;
    trailDatasets.push({
      label: `${x.sector_zh} 軌跡`,
      data: tr.rs.map((v, i) => ({ x: v, y: tr.momentum[i] })),
      borderColor: cssVar(QUADRANT_COLOR[x.quadrant] || "--text-muted"),
      borderWidth: 1,
      pointRadius: 1.5,
      showLine: true,
      fill: false,
      order: 2,
    });
  });
  draw("quadrant-chart", {
    type: "scatter",
    data: {
      datasets: [
        ...pts.map((x) => ({
          label: x.sector_zh,
          data: [{ x: x.rs_ratio, y: x.rs_momentum, label: x.sector_zh }],
          backgroundColor: cssVar(QUADRANT_COLOR[x.quadrant] || "--text-muted"),
          pointRadius: 7,
          pointHoverRadius: 9,
          order: 1,
        })),
        ...trailDatasets,
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (c) => {
              const row = pts[c.datasetIndex];
              if (!row) return c.dataset.label;    // 軌跡上的點
              return `${row.sector_zh}（${row.ticker}）　RS ${row.rs_ratio}　`
                + `動能 ${pct(row.rs_momentum)}　${row.quadrant}`;
            },
          },
        },
      },
      scales: {
        x: { title: { display: true, text: "相對強弱 RS（100 = 與自身 63 日均值持平）", color: cssVar("--text-muted") },
             grid: { color: cssVar("--hairline") },
             ticks: { color: cssVar("--text-muted"), font: { size: 11 } } },
        y: { title: { display: true, text: "RS 10 日動能 (%)", color: cssVar("--text-muted") },
             grid: { color: cssVar("--hairline") },
             ticks: { color: cssVar("--text-muted"), font: { size: 11 } } },
      },
    },
    plugins: [{
      // 點旁邊標名稱。輪動圖要看的是「哪個類股在哪一格」，
      // 只有一堆同色圓點的話，不把游標移上去就什麼也讀不出來。
      id: "pointLabels",
      afterDatasetsDraw(chart) {
        const { ctx } = chart;
        ctx.save();
        ctx.font = "11px system-ui, sans-serif";
        ctx.fillStyle = cssVar("--text-secondary");
        ctx.textBaseline = "middle";
        chart.data.datasets.forEach((ds, i) => {
          if (ds.showLine) return;                 // 軌跡不標字，否則整張圖都是字
          const el = chart.getDatasetMeta(i).data[0];
          if (!el) return;
          // 靠右邊界的點改標在左側，否則字會被切掉
          const nearRight = el.x > chart.chartArea.right - 60;
          ctx.textAlign = nearRight ? "right" : "left";
          ctx.fillText(ds.label, el.x + (nearRight ? -10 : 10), el.y);
        });
        ctx.restore();
      },
    }, {
      // 象限分隔線：RS=100 與動能=0。沒有這兩條線，散點只是一團點
      id: "quadrantLines",
      beforeDatasetsDraw(chart) {
        const { ctx, chartArea, scales } = chart;
        if (!chartArea) return;
        ctx.save();
        ctx.strokeStyle = cssVar("--hairline-strong");
        ctx.setLineDash([4, 4]);
        const x = scales.x.getPixelForValue(100);
        const y = scales.y.getPixelForValue(0);
        ctx.beginPath(); ctx.moveTo(x, chartArea.top); ctx.lineTo(x, chartArea.bottom); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(chartArea.left, y); ctx.lineTo(chartArea.right, y); ctx.stroke();

        ctx.setLineDash([]);
        ctx.font = "11px system-ui, sans-serif";
        ctx.fillStyle = cssVar("--text-muted");
        [["改善", chartArea.left + 8, chartArea.top + 14, "left"],
         ["領漲", chartArea.right - 8, chartArea.top + 14, "right"],
         ["落後", chartArea.left + 8, chartArea.bottom - 10, "left"],
         ["轉弱", chartArea.right - 8, chartArea.bottom - 10, "right"]]
          .forEach(([t, cx, cy, align]) => { ctx.textAlign = align; ctx.fillText(t, cx, cy); });
        ctx.restore();
      },
    }],
  });
}

function renderTimeline(r) {
  const tl = (r.history || {}).quadrant_timeline;
  const card = document.getElementById("timeline-card");
  if (!tl || !tl.dates || !tl.dates.length) { card.hidden = true; return; }
  card.hidden = false;
  const order = ["領漲", "改善", "轉弱", "落後"];
  draw("timeline-chart", {
    type: "line",
    data: {
      labels: tl.dates,
      datasets: order.filter((q) => tl.counts[q]).map((q) => ({
        label: q, data: tl.counts[q],
        borderColor: cssVar(QUADRANT_COLOR[q]), backgroundColor: cssVar(QUADRANT_COLOR[q]),
        borderWidth: 0, pointRadius: 0, fill: true, stepped: true,
      })),
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: true, labels: { color: cssVar("--text-secondary"),
                                                    boxWidth: 10, font: { size: 11 } } } },
      scales: {
        x: { grid: { display: false }, ticks: { color: cssVar("--text-muted"),
             maxTicksLimit: 10, font: { size: 11 } } },
        y: { stacked: true, beginAtZero: true,
             grid: { color: cssVar("--hairline") },
             ticks: { color: cssVar("--text-muted"), stepSize: 2, font: { size: 11 } } },
      },
    },
  });
}

function renderReturns(r) {
  const win = document.getElementById("sort-window").value || "1w";
  const useExcess = document.getElementById("show-excess").checked;
  const field = useExcess ? "excess" : "returns";
  const rows = r.rows
    .filter((x) => x[field] && x[field][win] !== null && x[field][win] !== undefined)
    .sort((a, b) => b[field][win] - a[field][win]);

  draw("returns-chart", {
    type: "bar",
    data: {
      labels: rows.map((x) => x.sector_zh),
      datasets: [{
        data: rows.map((x) => x[field][win]),
        backgroundColor: rows.map((x) =>
          cssVar(x[field][win] >= 0 ? "--series-4" : "--series-2")),
        borderRadius: 3,
      }],
    },
    options: {
      indexAxis: "y",
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (c) => pct(c.raw) } },
      },
      scales: {
        x: { grid: { color: cssVar("--hairline") },
             ticks: { color: cssVar("--text-muted"), font: { size: 11 },
                      callback: (v) => `${v}%` } },
        y: { grid: { display: false }, ticks: { color: cssVar("--text-muted"), font: { size: 11 } } },
      },
    },
  });
}

function renderTable(r) {
  const flowCell = (x) => {
    if (!x.flow) return '<td class="num">—</td>';
    const v = x.flow.flow_pct_of_aum;
    // 估計值要標示出來：兩種基礎的數字不可直接相比
    const tag = x.flow.basis === "aum" ? "<span class=\"source-pill\">估計</span>" : "";
    const tip = `${x.flow.flow_musd >= 0 ? "淨流入" : "淨流出"} `
      + `${Math.abs(x.flow.flow_musd).toLocaleString()} 百萬美元`
      + `（基礎：${x.flow.basis === "shares" ? "流通股數變化" : "淨資產變動扣除市場漲跌（估計）"}）`;
    return `<td class="num ${cls(v)}" title="${escapeHtml(tip)}">${pct(v, 3)}${tag}</td>`;
  };

  document.querySelector("#sector-table tbody").innerHTML = r.rows
    .slice()
    .sort((a, b) => (b.returns?.["1w"] ?? -Infinity) - (a.returns?.["1w"] ?? -Infinity))
    .map((x) => `<tr>
      <td>${escapeHtml(x.sector_zh)}<span class="subtitle"> ${escapeHtml(x.sector)}</span></td>
      <td class="ticker-cell">${escapeHtml(x.ticker)}</td>
      <td class="num ${cls(x.returns?.["1d"])}">${pct(x.returns?.["1d"])}</td>
      <td class="num ${cls(x.returns?.["1w"])}">${pct(x.returns?.["1w"])}</td>
      <td class="num ${cls(x.returns?.["1m"])}">${pct(x.returns?.["1m"])}</td>
      <td class="num ${cls(x.returns?.["3m"])}">${pct(x.returns?.["3m"])}</td>
      <td class="num">${x.rs_ratio ?? "—"}</td>
      <td class="num ${cls(x.rs_momentum)}">${pct(x.rs_momentum)}</td>
      <td style="color:${cssVar(QUADRANT_COLOR[x.quadrant] || "--text-muted")}">${escapeHtml(x.quadrant)}</td>
      ${flowCell(x)}
    </tr>`).join("");

  const note = document.getElementById("flow-note");
  note.textContent = r.flow_note
    ? `資金流：${r.flow_note}`
    : "資金流為「佔自身淨資產的比例」，跨類股可比；滑鼠移到數字上可看金額與計算基礎。";
}

function renderChanges(r) {
  const rows = r.rank_changes || [];
  const card = document.getElementById("changes-card");
  if (!rows.length) { card.hidden = true; return; }
  card.hidden = false;
  document.querySelector("#changes-table tbody").innerHTML = rows.map((c) => `<tr>
    <td>${escapeHtml(c.sector_zh)}</td>
    <td class="num">${c.rank_before}</td>
    <td class="num">${c.rank_now}</td>
    <td class="num ${cls(c.change)}">${c.change > 0 ? "↑" : c.change < 0 ? "↓" : "—"} ${Math.abs(c.change)}</td>
  </tr>`).join("");
}

function wire(r) {
  const sel = document.getElementById("sort-window");
  sel.innerHTML = Object.entries(r.window_labels)
    .map(([k, v]) => `<option value="${k}"${k === "1w" ? " selected" : ""}>${escapeHtml(v)}</option>`)
    .join("");
  sel.addEventListener("change", () => renderReturns(r));
  document.getElementById("show-excess").addEventListener("change", () => renderReturns(r));
}

async function main() {
  try {
    const res = await fetch(DATA_PATH, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.report = await res.json();
  } catch (e) {
    showError(`讀取類股輪動資料失敗：${escapeHtml(e.message)}。`
      + `可能是尚未執行過，${escapeHtml(DATA_PATH)} 還不存在——`
      + `可執行 <code>python3 scripts/run_sector_rotation.py -v</code> 產生。`);
    return;
  }
  const r = state.report;
  renderStats(r);
  renderQuadrant(r);
  renderTimeline(r);
  const h = r.history || {};
  document.getElementById("trail-note").innerHTML = h.days
    ? `細線為各類股最近 8 週的<strong>移動軌跡</strong>（每週一點）——`
      + `只看今天那一個點，分不出「剛進入這一格」與「已經待在這裡很久」，而那是完全不同的訊息。`
      + `<br />價格面歷史已由價格回溯重建（${h.days} 個交易日）；`
      + `<strong>資金流不在其中</strong>，它需要當時的逐日股數快照，沒有任何來源提供回溯查詢。`
    : "";
  wire(r);
  renderReturns(r);
  renderTable(r);
  renderChanges(r);
}

main();
