// 模組八：尿布（M 號）各平台電商單片價格監控。資料由 scripts/run_diaper_monitor.py 產生。
//
// 只有兩個數字：今天最便宜多少、跟近期平均比變動多少。跌幅達門檻（預設 20%）
// 顯著標示，讓「這是正常波動」跟「這次真的便宜很多」一眼分得出來。
const DATA_PATH = "data/diaper/latest.json";

const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escapeHtml = (v) =>
  v === null || v === undefined ? "" : String(v).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);

const state = { report: null, chart: null };

const money = (v) => (v === null || v === undefined ? "—" : `${Number(v).toFixed(2)} 元/片`);
function pct(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  return (n > 0 ? "+" : "") + n.toFixed(1) + "%";
}

const SERIES = ["--series-1", "--series-2", "--series-3"];

function showError(msg) {
  document.getElementById("error-slot").innerHTML = `<div class="error-banner">${msg}</div>`;
}

// --- 今日總覽 ------------------------------------------------------------

// 沒填今天的報價時，畫面退回顯示該品牌最近一次有資料的日期，而不是空白——
// 但一定要讓看的人知道那不是今天的價格，所以每個用到的地方都帶上這個標籤
function staleBadge(b) {
  return b.is_stale
    ? ` <span class="badge muted" title="今天沒有新報價，顯示最近一次查到的價格">${escapeHtml(b.data_date)}・非今日</span>`
    : "";
}

function renderStats(r) {
  const tiles = (r.brands || []).map((b) => {
    if (!b.has_data) {
      return `<div class="stat-tile">
        <div class="stat-value is-text" style="color:${cssVar("--text-muted")}">尚無報價</div>
        <div class="stat-label">${escapeHtml(b.brand)}</div></div>`;
    }
    const changeColor = b.significant_drop ? "--good-deep" : b.pct_change_vs_baseline < 0 ? "--good" : "--text-secondary";
    const changeLine = b.baseline_avg !== null
      ? `<span style="color:${cssVar(changeColor)}">${pct(b.pct_change_vs_baseline)}</span> vs 近${r.baseline_window_days}日均 ${money(b.baseline_avg)}`
      : `近期資料不足（${b.baseline_points}/${r.min_baseline_points} 筆），未判定`;
    return `<div class="stat-tile">
      <div class="stat-value">${money(b.cheapest_unit_price)}${b.significant_drop ? ` <span class="badge ok">顯著下跌</span>` : ""}${staleBadge(b)}</div>
      <div class="stat-label">${escapeHtml(b.brand)}・${escapeHtml(b.cheapest_platform)}</div>
      <div class="subtitle">${changeLine}</div>
    </div>`;
  });
  document.getElementById("brand-stats").innerHTML = tiles.join("");

  const alerts = (r.brands || []).filter((b) => b.significant_drop);
  document.getElementById("drop-alerts").innerHTML = alerts.length
    ? alerts.map((b) => `<div class="bargain-box">
        <strong>${escapeHtml(b.brand)}</strong> ${b.is_stale ? `${escapeHtml(b.data_date)}（非今日）最便宜` : "今日最便宜"}
        ${money(b.cheapest_unit_price)}
        （${escapeHtml(b.cheapest_platform)}），較近 ${r.baseline_window_days} 日平均
        <strong>下跌 ${Math.abs(b.pct_change_vs_baseline).toFixed(1)}%</strong>，
        達顯著下跌門檻（${r.drop_threshold_pct}%）。
      </div>`).join("")
    : "";
}

// --- 走勢圖 ----------------------------------------------------------------

function renderChart(r) {
  const hist = r.history || {};
  const brands = (r.brands || []).map((b) => b.brand).filter((b) => hist[b]);
  if (!brands.length) {
    document.getElementById("price-chart").closest(".chart-wrap").style.display = "none";
    return;
  }
  document.getElementById("price-chart").closest(".chart-wrap").style.display = "";
  const datasets = brands.map((b, i) => ({
    label: b,
    data: hist[b].dates.map((d, j) => ({ x: d, y: hist[b].cheapest_unit_price[j] })),
    borderColor: cssVar(SERIES[i % SERIES.length]),
    borderWidth: 1.8,
    pointRadius: 2,
    spanGaps: true,
  }));
  if (state.chart) state.chart.destroy();
  state.chart = new Chart(document.getElementById("price-chart"), {
    type: "line",
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { type: "category" },
        y: { title: { display: true, text: "元/片" } },
      },
      plugins: { legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } } },
    },
  });
}

// --- 明細 --------------------------------------------------------------------

function renderOffers(r) {
  const blocks = (r.brands || []).map((b) => {
    if (!b.has_data) {
      return `<div class="card" style="padding-top:8px">
        <h3 style="font-size:var(--fs-3);margin:0 0 8px">${escapeHtml(b.brand)}</h3>
        <p class="subtitle">尚無任何報價紀錄。</p></div>`;
    }
    const rows = (b.offers || []).map((o, i) => `<tr${i === 0 ? ' style="font-weight:500"' : ""}>
        <td>${escapeHtml(o.platform)}</td>
        <td class="subtitle raw-text">${escapeHtml(o.product_name)}</td>
        <td class="num">${Number(o.pack_price).toLocaleString()}</td>
        <td class="num">${o.piece_count}</td>
        <td class="num">${Number(o.unit_price).toFixed(2)}</td>
        <td><span class="badge ${o.source === "自動爬蟲" ? "warn" : "muted"}"
              title="${o.source === "自動爬蟲" ? "自動爬蟲抓到、未經人工核對；同一天若人工也填了同一平台，一律以人工為準" : "人工查價填入"}">${escapeHtml(o.source)}</span></td>
        <td>${o.url ? `<a href="${escapeHtml(o.url)}" target="_blank" rel="noopener">連結</a>` : ""}</td>
      </tr>`).join("");
    return `<div class="card" style="padding-top:8px">
      <h3 style="font-size:var(--fs-3);margin:0 0 8px">${escapeHtml(b.brand)}
        <span class="subtitle">（${b.is_stale ? `${escapeHtml(b.data_date)} 資料，非今日；` : ""}${b.offer_count} 個平台報價，最便宜標粗體）</span></h3>
      <div class="table-scroll">
        <table>
          <thead><tr><th>平台</th><th>商品</th><th class="num">售價</th>
            <th class="num">片數</th><th class="num">單片價</th><th>來源</th><th>連結</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>`;
  });
  document.getElementById("offers-slot").innerHTML = blocks.join("");
}

function renderNotes(r) {
  document.getElementById("notes-slot").innerHTML = (r.notes || []).map(escapeHtml).join("<br>");
}

async function load() {
  try {
    const res = await fetch(DATA_PATH, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const r = await res.json();
    state.report = r;
    document.getElementById("asof-label").textContent =
      `資料基準日 ${r.as_of}｜尺寸 ${r.size} 號｜跌幅達近${r.baseline_window_days}日均${r.drop_threshold_pct}%即顯著標示；本頁純資料呈現`;
    renderStats(r);
    renderChart(r);
    renderOffers(r);
    renderNotes(r);
  } catch (e) {
    showError(`載入 ${DATA_PATH} 失敗：${escapeHtml(e.message)}。模組八可能尚未執行過。`);
  }
}

load();
