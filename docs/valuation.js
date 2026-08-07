const DATA_PATH = "data/valuation/latest.json";

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escapeHtml = (v) =>
  v === null || v === undefined ? "" : String(v).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);

function fmtNum(v, digits = 2, suffix = "") {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return Number(v).toFixed(digits) + suffix;
}

function pctCell(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '<td class="num">—</td>';
  const n = Number(v);
  const cls = n > 0 ? "pos" : n < 0 ? "neg" : "";
  const sign = n > 0 ? "+" : "";
  return `<td class="num ${cls}">${sign}${n.toFixed(2)}%</td>`;
}

const state = { report: null, rows: [], sortKey: "upside_pct", sortDir: -1 };

function showError(msg) {
  document.getElementById("error-slot").innerHTML = `<div class="error-banner">⚠️ ${msg}</div>`;
}

function renderStats(report) {
  const rows = report.rows.filter((r) => r.upside_pct !== null && r.upside_pct !== undefined);
  const upsides = rows.map((r) => r.upside_pct).sort((a, b) => a - b);
  const n = upsides.length;
  const median = n ? (n % 2 ? upsides[(n - 1) / 2] : (upsides[n / 2 - 1] + upsides[n / 2]) / 2) : null;
  const undervalued = upsides.filter((u) => u > 0).length;

  const tiles = [
    { label: "資料基準日", value: report.as_of, plain: true },
    { label: "已涵蓋標的", value: `${report.counts.with_target_and_price} / ${report.counts.universe}`, plain: true },
    { label: "中位數上漲空間", value: median === null ? "—" : `${median.toFixed(1)}%`, signed: median },
    { label: "共識價高於現價", value: n ? `${((100 * undervalued) / n).toFixed(0)}%` : "—", plain: true },
  ];

  document.getElementById("stat-row").innerHTML = tiles
    .map((t) => {
      const cls = t.plain ? "" : t.signed > 0 ? "pos" : t.signed < 0 ? "neg" : "";
      return `<div class="stat-tile">
        <div class="stat-value ${cls}">${escapeHtml(t.value)}</div>
        <div class="stat-label">${escapeHtml(t.label)}</div>
      </div>`;
    })
    .join("");

  document.getElementById("coverage-note").textContent = report.coverage_note || "";
  document.getElementById("source-note").textContent = report.multi_source_note || "";
  document.getElementById("header-subtitle").textContent =
    `股票池：${report.universe}　|　產生時間：${report.generated_at || "—"}`;
}

let sectorChart;
function renderSectorChart(report) {
  const data = (report.sector_summary || []).slice();
  if (!data.length) return;
  const ctx = document.getElementById("sector-chart");
  if (sectorChart) sectorChart.destroy();
  sectorChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: data.map((d) => d.sector),
      datasets: [{
        data: data.map((d) => d.median_upside_pct),
        // 與表格的漲跌色一致：正=綠、負=紅，避免同一頁對同一指標用兩套語意
        backgroundColor: data.map((d) =>
          d.median_upside_pct >= 0 ? cssVar("--good") : cssVar("--critical")),
        borderRadius: 4,
      }],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (c) => {
              const d = data[c.dataIndex];
              return `中位數 ${d.median_upside_pct}%　平均 ${d.mean_upside_pct}%　(${d.count} 檔)`;
            },
          },
        },
      },
      scales: {
        x: {
          grid: { color: cssVar("--gridline") },
          ticks: { color: cssVar("--text-muted"), callback: (v) => `${v}%` },
        },
        y: { grid: { display: false }, ticks: { color: cssVar("--text-muted"), font: { size: 11 } } },
      },
    },
  });
}

function populateSectorFilter(report) {
  const sectors = [...new Set(report.rows.map((r) => r.sector).filter(Boolean))].sort();
  const sel = document.getElementById("sector-filter");
  sectors.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s;
    opt.textContent = s;
    sel.appendChild(opt);
  });
}

function currentFilters() {
  return {
    sector: document.getElementById("sector-filter").value,
    confidence: document.getElementById("confidence-filter").value,
    minUpside: document.getElementById("upside-filter").value,
    query: document.getElementById("search-box").value.trim().toLowerCase(),
    hideMissing: document.getElementById("hide-missing").checked,
  };
}

function applyFilters() {
  const f = currentFilters();
  let rows = state.report.rows.slice();

  if (f.hideMissing) rows = rows.filter((r) => r.upside_pct !== null && r.upside_pct !== undefined);
  if (f.sector) rows = rows.filter((r) => r.sector === f.sector);
  if (f.confidence) rows = rows.filter((r) => r.confidence === f.confidence);
  if (f.minUpside !== "") {
    const min = Number(f.minUpside);
    rows = rows.filter((r) => r.upside_pct !== null && r.upside_pct !== undefined && r.upside_pct > min);
  }
  if (f.query) {
    rows = rows.filter(
      (r) =>
        (r.ticker || "").toLowerCase().includes(f.query) ||
        (r.name || "").toLowerCase().includes(f.query)
    );
  }

  const key = state.sortKey;
  rows.sort((a, b) => {
    const av = a[key], bv = b[key];
    const aMissing = av === null || av === undefined;
    const bMissing = bv === null || bv === undefined;
    if (aMissing && bMissing) return 0;
    if (aMissing) return 1;   // 缺值一律排最後，不受排序方向影響
    if (bMissing) return -1;
    if (typeof av === "number" && typeof bv === "number") return (av - bv) * state.sortDir;
    return String(av).localeCompare(String(bv)) * state.sortDir;
  });

  state.rows = rows;
  renderTable();
}

function renderTable() {
  const tbody = document.querySelector("#valuation-table tbody");
  const rows = state.rows;

  tbody.innerHTML = rows
    .map((r) => {
      const upsideCell = pctCell(r.upside_pct);
      const confClass = `confidence-${(r.confidence || "").replace(/\(.*\)/, "")}`;
      const title = r.notes && r.notes.length ? escapeHtml(r.notes.join(" / ")) : "";
      const sourceBadges = (r.sources_used || [])
        .map((s) => `<span class="source-pill">${escapeHtml(s)}</span>`)
        .join("");
      return `<tr${title ? ` title="${title}"` : ""}>
        <td class="ticker-cell">${escapeHtml(r.ticker)}${sourceBadges}</td>
        <td>${escapeHtml(r.name)}</td>
        <td>${escapeHtml(r.sector)}</td>
        <td class="num">${fmtNum(r.close)}</td>
        <td class="num">${fmtNum(r.consensus_target)}</td>
        ${upsideCell}
        ${pctCell(r.change_1w_pct)}
        ${pctCell(r.change_1m_pct)}
        <td class="num">${r.analyst_count ?? "—"}</td>
        <td class="${confClass}">${escapeHtml(r.confidence || "—")}</td>
      </tr>`;
    })
    .join("");

  document.getElementById("table-footer").textContent =
    `顯示 ${rows.length} 檔（股票池共 ${state.report.counts.universe} 檔）。`
    + "滑鼠移到列上可看資料備註；上漲空間為正＝分析師共識價高於現價。";
}

function wireSorting() {
  document.querySelectorAll("#valuation-table thead th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (state.sortKey === key) {
        state.sortDir *= -1;
      } else {
        state.sortKey = key;
        // 數字欄預設由大到小，文字欄由 A 到 Z
        state.sortDir = ["ticker", "name", "sector", "confidence"].includes(key) ? 1 : -1;
      }
      document.querySelectorAll("#valuation-table thead th[data-sort]").forEach((el) => {
        el.textContent = el.textContent.replace(/ [▼▲]$/, "");
      });
      th.textContent += state.sortDir === -1 ? " ▼" : " ▲";
      applyFilters();
    });
  });
}

function wireFilters() {
  ["sector-filter", "confidence-filter", "upside-filter", "hide-missing"].forEach((id) =>
    document.getElementById(id).addEventListener("change", applyFilters)
  );
  document.getElementById("search-box").addEventListener("input", applyFilters);
}

async function main() {
  try {
    const res = await fetch(DATA_PATH, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.report = await res.json();
  } catch (e) {
    showError(
      `讀取估值資料失敗：${escapeHtml(e.message)}。` +
      "可能是模組二的每日爬蟲尚未執行過，docs/data/valuation/latest.json 還不存在。"
    );
    return;
  }

  renderStats(state.report);
  renderSectorChart(state.report);
  populateSectorFilter(state.report);
  wireSorting();
  wireFilters();
  applyFilters();
}

main();
