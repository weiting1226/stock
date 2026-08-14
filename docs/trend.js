// 模組七：類股主要 ETF 趨勢。資料由 scripts/run_sector_trend.py 產生。
//
// 與模組四的分工：那裡看 1 日～3 月的輪動與資金流；這裡看 1 月～3 年的趨勢，
// 而且每個類股列多檔 ETF——「科技類股漲多少」取決於你拿哪一檔。
const DATA_PATH = "data/trend/latest.json";

const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escapeHtml = (v) =>
  v === null || v === undefined ? "" : String(v).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);

const state = { report: null, charts: {}, window: "1y", sort: "return", primaryOnly: false };

// 缺值一律顯示破折號。顯示 0.00% 會讓「歷史不夠長」看起來像「剛好沒動」
function pct(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  return (n > 0 ? "+" : "") + n.toFixed(digits) + "%";
}
const cls = (v) => (v === null || v === undefined ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "");

// 類股固定配色，讓折線圖與表格對得起來
const SERIES = ["--series-1", "--series-2", "--series-3", "--series-4",
                "--series-5", "--series-6", "--good", "--warning",
                "--serious", "--critical", "--text-muted"];

function showError(msg) {
  document.getElementById("error-slot").innerHTML = `<div class="error-banner">${msg}</div>`;
}

function draw(id, config) {
  if (state.charts[id]) state.charts[id].destroy();
  state.charts[id] = new Chart(document.getElementById(id), config);
}

function maCell(above) {
  if (above === null || above === undefined) {
    return `<td class="subtitle">歷史不足</td>`;
  }
  const color = cssVar(above ? "--good" : "--critical");
  return `<td style="color:${color}">${above ? "之上" : "之下"}</td>`;
}

// --- 統計列 ------------------------------------------------------------------

function renderStats(r) {
  const bd = r.breadth || {};
  const b = r.benchmark || {};
  const sectors = r.sectors || [];
  const withRet = sectors
    .map((s) => {
      const p = s.etfs.find((e) => e.is_primary) || s.etfs[0];
      return { sector: s.sector, v: p.returns?.[state.window] };
    })
    .filter((x) => x.v !== null && x.v !== undefined);
  const best = withRet.length ? withRet.reduce((a, x) => (x.v > a.v ? x : a)) : null;
  const worst = withRet.length ? withRet.reduce((a, x) => (x.v < a.v ? x : a)) : null;

  const tiles = [
    { label: `站上 200 日線`, value: `${bd.above_ma200 ?? "—"} / ${bd.total ?? "—"}`,
      color: (bd.pct_above_ma200 ?? 50) >= 50 ? "--good" : "--critical" },
    { label: `基準 ${b.ticker || "SPY"}（${r.window_labels?.[state.window] || ""}）`,
      value: pct(b.returns?.[state.window]) },
    { label: `最強類股（${r.window_labels?.[state.window] || ""}）`,
      value: best ? `${best.sector} ${pct(best.v, 1)}` : "—", color: "--good" },
    { label: `最弱類股（${r.window_labels?.[state.window] || ""}）`,
      value: worst ? `${worst.sector} ${pct(worst.v, 1)}` : "—", color: "--critical" },
  ];
  document.getElementById("stat-row").innerHTML = tiles.map((t) =>
    `<div class="stat-tile">
      <div class="stat-value" style="${t.color ? `color:${cssVar(t.color)}` : ""}">${escapeHtml(t.value)}</div>
      <div class="stat-label">${escapeHtml(t.label)}</div></div>`).join("");

  document.getElementById("breadth-note").innerHTML =
    `資料基準為收盤日 <strong>${escapeHtml(r.close_date)}</strong>（不是執行日）。`
    + (bd.above_ma50 !== undefined
        ? ` 另有 ${bd.above_ma50} / ${bd.total} 檔站上 50 日線。` : "")
    + (r.missing?.length
        ? ` <strong>未取得：${r.missing.map(escapeHtml).join("、")}</strong>——`
          + `缺的 ETF 不會出現在表上，這裡標出來才不會靜靜消失。` : "");
}

// --- 趨勢圖 ------------------------------------------------------------------

function renderTrendChart(r) {
  const charts = r.charts || {};
  const keys = Object.keys(charts).filter((k) => k !== r.benchmark?.ticker);
  const labels = charts[keys[0]]?.dates || [];
  const datasets = keys.map((k, i) => ({
    label: k,
    data: charts[k].values,
    borderColor: cssVar(SERIES[i % SERIES.length]),
    borderWidth: 1.4,
    pointRadius: 0,
  }));
  if (charts[r.benchmark?.ticker]) {
    datasets.push({
      label: r.benchmark.ticker,
      data: charts[r.benchmark.ticker].values,
      borderColor: cssVar("--text-primary"),
      borderWidth: 2.2,
      borderDash: [5, 3],
      pointRadius: 0,
    });
  }
  draw("trend-chart", {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: { y: { title: { display: true, text: "起點 = 100" } } },
      plugins: { legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } } },
    },
  });
}

function renderBreadthChart(r) {
  const h = r.breadth_history || { dates: [] };
  const note = document.getElementById("breadth-chart").closest(".card").querySelector(".subtitle");
  const MIN_POINTS = 5;
  if (h.dates.length < MIN_POINTS) {
    // 一兩個點畫成折線圖，看起來像圖表壞了。與其如此，不如直說還沒累積夠。
    document.getElementById("breadth-chart").closest(".chart-wrap").style.display = "none";
    note.textContent = h.dates.length
      ? `廣度歷史累積中：目前 ${h.dates.length} 個交易日，滿 ${MIN_POINTS} 日才畫圖`
        + `（今日 ${h.above_ma200[h.above_ma200.length - 1]} / ${h.total[h.total.length - 1]} 檔站上 200 日線）。`
      : "廣度歷史尚未累積（本模組剛上線，每個交易日會累加一筆）。";
    return;
  }
  draw("breadth-chart", {
    type: "line",
    data: {
      labels: h.dates,
      datasets: [{
        label: "站上 200 日線的類股數",
        data: h.above_ma200,
        borderColor: cssVar("--series-1"),
        backgroundColor: "rgba(31,111,178,0.12)",
        fill: true, stepped: true, pointRadius: 0, borderWidth: 1.5,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: { y: { min: 0, max: (h.total?.[0] ?? 11), ticks: { stepSize: 1 } } },
      plugins: { legend: { display: false } },
    },
  });
}

// --- 主表 --------------------------------------------------------------------

function renderTable(r) {
  const w = state.window;
  document.getElementById("ret-head").textContent = r.window_labels?.[w] || "報酬";

  const sectors = [...(r.sectors || [])];
  const keyOf = (s) => {
    const p = s.etfs.find((e) => e.is_primary) || s.etfs[0];
    if (state.sort === "from_high") return p.from_high_pct ?? -999;
    if (state.sort === "dispersion") return s.provider_dispersion_1y ?? -1;
    return p.returns?.[w] ?? -999;
  };
  sectors.sort((a, b) => keyOf(b) - keyOf(a));

  const rows = [];
  for (const s of sectors) {
    const etfs = state.primaryOnly ? s.etfs.filter((e) => e.is_primary) : s.etfs;
    etfs.forEach((e, i) => {
      const first = i === 0;
      const disp = s.provider_dispersion_1y;
      const dispTag = first && disp !== null && disp !== undefined && disp >= 3
        ? `<div class="subtitle" title="同類股、不同發行商的近一年報酬差距（不含次產業）">
             發行商間差 ${disp.toFixed(1)}pp</div>` : "";
      // 次產業標的要標出來。XBI 是生技、不是醫療保健類股，
      // 把它跟 XLV 並排而不標，讀者會以為那是「同一個東西的另一種買法」
      const kindTag = e.kind === "industry"
        ? `<span class="subtitle" style="color:${cssVar("--warning")}" title="次產業，不代表整個類股">
             ・次產業</span>` : "";
      rows.push(`<tr>
        <td>${first ? `<strong>${escapeHtml(s.sector)}</strong><br>` : ""}
          <span class="ticker-cell">${escapeHtml(e.ticker)}</span>
          <span class="subtitle"> ${escapeHtml(e.issuer)}${e.is_primary ? "・代表" : ""}</span>${kindTag}
          ${dispTag}</td>
        <td class="num">${e.close?.toFixed(2) ?? "—"}</td>
        <td class="num ${cls(e.returns?.[w])}">${pct(e.returns?.[w])}</td>
        <td class="num ${cls(e.excess?.[w])}">${pct(e.excess?.[w])}</td>
        <td class="num ${cls(e.from_high_pct)}">${pct(e.from_high_pct)}</td>
        ${maCell(e.above_ma50)}
        ${maCell(e.above_ma200)}
        <td class="subtitle raw-text">${escapeHtml(e.tracks || "")}</td>
      </tr>`);
    });
  }
  document.querySelector("#trend-table tbody").innerHTML = rows.join("");

  const spread = sectors
    .map((s) => ({ sector: s.sector, d: s.provider_dispersion_1y }))
    .filter((x) => x.d !== null && x.d !== undefined)
    .sort((a, b) => b.d - a.d)
    .slice(0, 3);
  document.getElementById("dispersion-note").innerHTML = spread.length
    ? `<strong>同一類股、不同發行商的近一年報酬差距最大的三個：</strong>`
      + spread.map((x) => `${escapeHtml(x.sector)} ${x.d.toFixed(1)}pp`).join("、")
      + `。它們追的指數不同，成分與權重就不同（SPDR 只取 S&P 500 成分，
         Vanguard 含中小型，iShares 用羅素 1000）。<br>
         <strong>這個數字刻意不含標為「次產業」的 ETF。</strong>
         XBI 是生技、KRE 是區域銀行、XME 是金屬礦業——它們本來就該與整個類股不同，
         算進來會得到一個看起來很大、但標籤與內容不符的數字
         （實測醫療保健含 XBI 是 50.7pp，只看全類股 ETF 則是 1.6pp）。`
    : "";
}

function renderThematic(r) {
  document.querySelector("#thematic-table tbody").innerHTML = (r.thematic || []).map((t) =>
    `<tr>
      <td><span class="ticker-cell">${escapeHtml(t.ticker)}</span>
        <span class="subtitle"> ${escapeHtml(t.name)}</span></td>
      <td class="subtitle">${escapeHtml(t.parent_sector)}</td>
      <td class="num ${cls(t.returns?.["1m"])}">${pct(t.returns?.["1m"])}</td>
      <td class="num ${cls(t.returns?.["3m"])}">${pct(t.returns?.["3m"])}</td>
      <td class="num ${cls(t.returns?.["6m"])}">${pct(t.returns?.["6m"])}</td>
      <td class="num ${cls(t.returns?.["1y"])}">${pct(t.returns?.["1y"])}</td>
      <td class="num ${cls(t.from_high_pct)}">${pct(t.from_high_pct)}</td>
      ${maCell(t.above_ma200)}
    </tr>`).join("");
}

function renderNotes(r) {
  document.getElementById("notes-slot").innerHTML =
    (r.notes || []).map((n) => escapeHtml(n)).join("<br>");
}

function renderAll() {
  const r = state.report;
  renderStats(r);
  renderTable(r);
}

function initControls(r) {
  const sel = document.getElementById("window-select");
  sel.innerHTML = Object.entries(r.window_labels || {})
    .map(([k, v]) => `<option value="${k}"${k === state.window ? " selected" : ""}>${escapeHtml(v)}</option>`)
    .join("");
  sel.addEventListener("change", (e) => { state.window = e.target.value; renderAll(); });
  document.getElementById("sort-select").addEventListener("change", (e) => {
    state.sort = e.target.value; renderTable(r);
  });
  document.getElementById("primary-only").addEventListener("change", (e) => {
    state.primaryOnly = e.target.checked; renderTable(r);
  });
}

async function load() {
  try {
    const res = await fetch(DATA_PATH, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const r = await res.json();
    state.report = r;
    document.getElementById("asof-label").textContent =
      `收盤日 ${r.close_date}｜1 月～3 年的趨勢視角（短期輪動見模組四）；本頁純資料呈現，非投資建議`;
    initControls(r);
    renderAll();
    renderTrendChart(r);
    renderBreadthChart(r);
    renderThematic(r);
    renderNotes(r);
  } catch (e) {
    showError(`載入 ${DATA_PATH} 失敗：${escapeHtml(e.message)}。模組七可能尚未執行過。`);
  }
}

load();
