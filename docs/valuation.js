// 三份資料源：S&P 500 與 Nasdaq-100 都是每日全量重抓的完整快照；全美股是跨
// 多班次逐檔累積的掃描結果，覆蓋率會隨當天進度成長。共用同一套渲染與篩選
// 程式碼，避免三邊的上漲空間／離散度定義各自演化。
const DATA_SOURCES = {
  sp500: { path: "data/valuation/latest.json", label: "S&P 500" },
  ndx100: { path: "data/valuation/ndx100_latest.json", label: "Nasdaq-100" },
  universe: { path: "data/valuation/universe_latest.json", label: "全美股" },
};

// 7,498 列一次全畫進 DOM 會讓瀏覽器卡住好幾秒，而且沒有人會捲到第 500 列。
// 篩選與排序仍在全部資料上進行，只是渲染截斷，並在表尾說明還有多少列。
const MAX_RENDERED_ROWS = 300;

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escapeHtml = (v) =>
  v === null || v === undefined ? "" : String(v).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);

function fmtNum(v, digits = 2, suffix = "") {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return Number(v).toFixed(digits) + suffix;
}

// 市值分級：採美股慣用的分界（巨型 $200B、大型 $10B、中型 $2B、小型 $300M、
// 微型 $50M），不是自訂的數字。門檻只定義在這裡一處——後端只負責送出市值原始
// 值與「這個數字能不能用」，分級留在畫面層，兩邊各存一份遲早會不一致。
const CAP_TIERS = [
  { key: "mega",  label: "巨型股（≥ $200B）",   min: 200e9, max: Infinity },
  { key: "large", label: "大型股（$10B–200B）", min: 10e9,  max: 200e9 },
  { key: "mid",   label: "中型股（$2B–10B）",   min: 2e9,   max: 10e9 },
  { key: "small", label: "小型股（$300M–2B）",  min: 300e6, max: 2e9 },
  { key: "micro", label: "微型股（$50M–300M）", min: 50e6,  max: 300e6 },
  { key: "nano",  label: "奈米股（< $50M）",    min: 0,     max: 50e6 },
];

const capTier = (v) =>
  v === null || v === undefined ? null : CAP_TIERS.find((t) => v >= t.min && v < t.max);

/**
 * 市值以 $1.23T／$45.60B／$789.00M 呈現：7,500 列的表格裡沒有人讀得動 12 位數字。
 *
 * 小數位固定兩位，不因數字變大而縮減：$199.9B 與 $200.0B 分屬大型股與巨型股，
 * 若四捨五入成同樣的「$200B」，畫面上就看不出它們為什麼被分到不同級距。
 */
function fmtCap(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  for (const [unit, scale] of [["T", 1e12], ["B", 1e9], ["M", 1e6]]) {
    if (n >= scale) return `$${(n / scale).toFixed(2)}${unit}`;
  }
  return `$${(n / 1e6).toFixed(2)}M`;
}

/**
 * 市值欄。非美元計價的市值不顯示數字——它跟其他列不同單位，並排比較毫無意義，
 * 而一個看起來正常的數字會讓人以為可以比。留白則會跟「查無資料」混在一起。
 */
function capCell(r) {
  if (r.market_cap_issue === "non_usd") {
    return '<td class="num pb-issue" title="市值以非美元計價，與其他標的不可比">非美元</td>';
  }
  const v = r.market_cap;
  if (v === null || v === undefined) return '<td class="num">—</td>';
  const tier = capTier(v);
  return `<td class="num"${tier ? ` title="${escapeHtml(tier.label)}"` : ""}>${fmtCap(v)}</td>`;
}

function pctCell(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '<td class="num">—</td>';
  const n = Number(v);
  const cls = n > 0 ? "pos" : n < 0 ? "neg" : "";
  const sign = n > 0 ? "+" : "";
  return `<td class="num ${cls}">${sign}${n.toFixed(2)}%</td>`;
}

const state = { report: null, rows: [], sortKey: "upside_pct", sortDir: -1, source: "sp500" };

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/**
 * 目標價區間視覺化：一條軌道代表 [最低目標價, 最高目標價]，
 * ● 為目前收盤價的位置，▲ 為共識目標價的位置。
 * 軌道越寬（離散度越大）代表各機構看法分歧越大。
 */
function rangeCell(r) {
  const { target_low: lo, target_high: hi, consensus_target: mid, close } = r;
  if (lo === null || lo === undefined || hi === null || hi === undefined) {
    return '<td class="range-cell">—</td>';
  }
  const span = hi - lo;
  const pos = (v) => (span > 0 ? clamp(((v - lo) / span) * 100, 0, 100) : 50);
  const tip = [
    `最低 ${lo.toFixed(2)}`,
    `共識 ${mid !== null && mid !== undefined ? mid.toFixed(2) : "—"}`,
    `最高 ${hi.toFixed(2)}`,
    close !== null && close !== undefined ? `收盤 ${close.toFixed(2)}` : null,
    r.range_source ? `區間來源：${r.range_source}` : null,
  ].filter(Boolean).join("　");

  const markers = [
    close !== null && close !== undefined
      ? `<span class="range-close" style="left:${pos(close).toFixed(1)}%"></span>` : "",
    mid !== null && mid !== undefined
      ? `<span class="range-target" style="left:${pos(mid).toFixed(1)}%"></span>` : "",
  ].join("");

  return `<td class="range-cell" title="${escapeHtml(tip)}">
    <div class="range-nums"><span>${lo.toFixed(2)}</span><span>${hi.toFixed(2)}</span></div>
    <div class="range-track">${markers}</div>
  </td>`;
}

/**
 * 股價淨值比。< 1 代表市價低於帳上股東權益，特別標示。
 * 淨值為負時比值沒有意義（負除以負會看起來很便宜），顯示「負淨值」而非留白——
 * 留白會讓它跟「查無資料」混在一起，但兩者在篩選時的意義完全相反。
 */
function pbCell(r) {
  // 「算得出來但不能用」要說明原因。留白會跟「查無資料」混在一起，
  // 而這兩者對使用者的意義完全不同。
  const issue = r.book_value_issue;
  if (issue === "negative") {
    return '<td class="num pb-issue" title="每股淨值為負，股價淨值比不適用">負淨值</td>';
  }
  if (issue === "share_class_mismatch") {
    return '<td class="num pb-issue" title="每股淨值與收盤價落差過大，研判為不同股別的數值">股別不符</td>';
  }
  const v = r.price_to_book;
  if (v === null || v === undefined) return '<td class="num">—</td>';
  const cls = v < 1 ? "pb-cheap" : "";
  const tip = r.book_value !== null && r.book_value !== undefined
    ? `每股淨值 ${Number(r.book_value).toFixed(2)}` : "";
  return `<td class="num ${cls}"${tip ? ` title="${escapeHtml(tip)}"` : ""}>${v.toFixed(2)}</td>`;
}

/** 離散度分級：區間寬度相對共識價越大，分歧越嚴重。 */
function dispersionCell(v) {
  if (v === null || v === undefined) return '<td class="num">—</td>';
  const cls = v >= 60 ? "disp-high" : v >= 30 ? "disp-mid" : "disp-low";
  return `<td class="num ${cls}">${v.toFixed(1)}%</td>`;
}

function showError(msg) {
  document.getElementById("error-slot").innerHTML = `<div class="error-banner">${msg}</div>`;
}

function renderStats(report) {
  const rows = report.rows.filter((r) => r.upside_pct !== null && r.upside_pct !== undefined);
  const upsides = rows.map((r) => r.upside_pct).sort((a, b) => a - b);
  const n = upsides.length;
  const median = n ? (n % 2 ? upsides[(n - 1) / 2] : (upsides[n / 2 - 1] + upsides[n / 2]) / 2) : null;
  const undervalued = upsides.filter((u) => u > 0).length;

  const disps = report.rows
    .map((r) => r.target_dispersion_pct)
    .filter((v) => v !== null && v !== undefined)
    .sort((a, b) => a - b);
  const dn = disps.length;
  const medianDisp = dn ? (dn % 2 ? disps[(dn - 1) / 2] : (disps[dn / 2 - 1] + disps[dn / 2]) / 2) : null;

  const counts = report.rows
    .map((r) => r.analyst_count)
    .filter((v) => v !== null && v !== undefined);
  const medianCount = counts.length
    ? counts.slice().sort((a, b) => a - b)[Math.floor(counts.length / 2)]
    : null;

  const perFirm = report.counts.per_firm_basis || 0;
  const tiles = [
    { label: "資料基準日", value: report.as_of, plain: true, text: true },
    // 分母放進標籤而不是數值：全美股的「4197 / 5958」在 28px 字級下需要 164px，
    // 而七格並排時每格只有 135px——會直接壓到隔壁格的數字上（實測畫面顯示成
    // 「4197 / 595823.4%」）。分母本來就是脈絡而非主角，移到標籤才是它該在的位置。
    {
      label: `已涵蓋標的（共 ${report.counts.universe.toLocaleString()} 檔）`,
      value: report.counts.with_target_and_price.toLocaleString(),
      plain: true,
    },
    { label: "中位數上漲空間", value: median === null ? "—" : `${median.toFixed(1)}%`, signed: median },
    { label: "共識價高於現價", value: n ? `${((100 * undervalued) / n).toFixed(0)}%` : "—", plain: true },
    { label: "中位數機構數", value: medianCount === null ? "—" : `${medianCount}`, plain: true },
    { label: "中位數離散度", value: medianDisp === null ? "—" : `${medianDisp.toFixed(1)}%`, plain: true },
    {
      label: perFirm ? "逐機構計算檔數" : "計算基礎",
      value: perFirm ? `${perFirm}` : "資料源共識",
      plain: true,
      text: !perFirm,
    },
  ];

  document.getElementById("stat-row").innerHTML = tiles
    .map((t) => {
      const cls = [
        t.plain ? "" : t.signed > 0 ? "pos" : t.signed < 0 ? "neg" : "",
        t.text ? "is-text" : "",
      ].filter(Boolean).join(" ");
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
  const ctx = document.getElementById("sector-chart");
  if (sectorChart) sectorChart.destroy();
  // 只有 Unknown 一類時畫出來的是一根沒有意義的長條（類股要等下一輪掃描才填得上），
  // 與其顯示假的比較圖，不如整段收起來
  const meaningful = data.filter((d) => d.sector && d.sector !== "Unknown");
  // 切換資料源時若新的沒有類股資料，得把舊圖清掉，否則會留著上一份的長條
  if (!meaningful.length) {
    sectorChart = undefined;
    ctx.closest("section").hidden = true;
    return;
  }
  ctx.closest("section").hidden = false;
  sectorChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: data.map((d) => d.sector),
      datasets: [{
        data: data.map((d) => d.median_upside_pct),
        // 北歐色盤：正向用 spruce、負向用 rust；兩者已通過色盲辨識檢驗
        backgroundColor: data.map((d) =>
          d.median_upside_pct >= 0 ? cssVar("--series-4") : cssVar("--series-2")),
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
          grid: { color: cssVar("--hairline") },
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
  // 切換資料源時要重建；只用 append 會把兩份清單疊在一起
  sel.innerHTML = '<option value="">全部類股</option>';
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
    minAnalysts: document.getElementById("analyst-filter").value,
    maxDispersion: document.getElementById("dispersion-filter").value,
    maxPb: document.getElementById("pb-filter").value,
    cap: document.getElementById("cap-filter").value,
    query: document.getElementById("search-box").value.trim().toLowerCase(),
    hideMissing: document.getElementById("hide-missing").checked,
    hideImplausible: document.getElementById("hide-implausible").checked,
  };
}

function applyFilters() {
  const f = currentFilters();
  let rows = state.report.rows.slice();

  if (f.hideMissing) rows = rows.filter((r) => r.upside_pct !== null && r.upside_pct !== undefined);
  if (f.hideImplausible) rows = rows.filter((r) => !r.implausible);
  if (f.sector) rows = rows.filter((r) => r.sector === f.sector);
  if (f.confidence) rows = rows.filter((r) => r.confidence === f.confidence);
  if (f.minUpside !== "") {
    const min = Number(f.minUpside);
    rows = rows.filter((r) => r.upside_pct !== null && r.upside_pct !== undefined && r.upside_pct > min);
  }
  if (f.minAnalysts !== "") {
    const min = Number(f.minAnalysts);
    rows = rows.filter((r) => r.analyst_count !== null && r.analyst_count !== undefined && r.analyst_count >= min);
  }
  if (f.maxDispersion !== "") {
    const max = Number(f.maxDispersion);
    rows = rows.filter(
      (r) => r.target_dispersion_pct !== null && r.target_dispersion_pct !== undefined
        && r.target_dispersion_pct <= max
    );
  }
  if (f.maxPb !== "") {
    const max = Number(f.maxPb);
    // 有問題的列不得混進「便宜」的結果裡——它們算得出比值，但那個數字沒有意義
    rows = rows.filter(
      (r) => !r.book_value_issue && r.price_to_book !== null
        && r.price_to_book !== undefined && r.price_to_book < max
    );
  }
  if (f.cap) {
    // 兩種語意共用一個下拉：min:N 是「至少多大」，tier:X 是「就看這一級」。
    // 不論哪一種，沒有可用市值的列都不能留下——篩選條件是「市值如何」，
    // 而市值不明的標的無法判定符不符合，混進來等於讓條件失效。
    const [mode, arg] = f.cap.split(":");
    const usable = (r) => !r.market_cap_issue && r.market_cap !== null && r.market_cap !== undefined;
    if (mode === "min") {
      const min = Number(arg);
      rows = rows.filter((r) => usable(r) && r.market_cap >= min);
    } else {
      rows = rows.filter((r) => usable(r) && (capTier(r.market_cap) || {}).key === arg);
    }
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
  const rows = state.rows.slice(0, MAX_RENDERED_ROWS);
  const truncated = state.rows.length - rows.length;

  tbody.innerHTML = rows
    .map((r) => {
      const upsideCell = pctCell(r.upside_pct);
      const confClass = `confidence-${(r.confidence || "").replace(/\(.*\)/, "")}`;
      // 逐機構模式時，把每一家券商的目標價放進 tooltip，讓平均值可以被檢驗
      const firmDetail = (r.firm_targets || [])
        .map((f) => `${f.firm} ${Number(f.target).toFixed(2)}`)
        .join("　");
      const tipParts = [
        firmDetail ? `各機構目標價：${firmDetail}` : "",
        r.duplicates_removed ? `（已合併 ${r.duplicates_removed} 筆重複機構）` : "",
        ...(r.notes || []),
      ].filter(Boolean);
      const title = tipParts.length ? escapeHtml(tipParts.join(" / ")) : "";
      // 逐機構模式的列額外標示，讓使用者知道這個平均是我們自行算的
      const badges = (r.sources_used || []).slice();
      if (r.basis === "per_firm") badges.unshift("逐機構");
      const sourceBadges = badges
        .map((s) => `<span class="source-pill">${escapeHtml(s)}</span>`)
        .join("");
      return `<tr${title ? ` title="${title}"` : ""}>
        <td class="ticker-cell">${escapeHtml(r.ticker)}${sourceBadges}</td>
        <td>${escapeHtml(r.name)}</td>
        <td>${escapeHtml(r.sector)}</td>
        ${capCell(r)}
        <td class="num">${fmtNum(r.close)}</td>
        <td class="num">${fmtNum(r.consensus_target)}</td>
        ${upsideCell}
        <td class="num">${r.analyst_count ?? "—"}</td>
        ${rangeCell(r)}
        ${dispersionCell(r.target_dispersion_pct)}
        ${pbCell(r)}
        ${pctCell(r.change_1w_pct)}
        ${pctCell(r.change_1m_pct)}
        <td class="${confClass}">${escapeHtml(r.confidence || "—")}</td>
      </tr>`;
    })
    .join("");

  document.getElementById("table-footer").innerHTML =
    `顯示 ${rows.length} 檔（符合條件共 ${state.rows.length} 檔，`
    + `股票池共 ${state.report.counts.universe} 檔）。`
    + (truncated > 0
        ? `<strong>另有 ${truncated} 檔未顯示</strong>——表格最多列出 ${MAX_RENDERED_ROWS} 列，`
          + "請用上方篩選或點欄位標題排序來縮小範圍。"
        : "")
    + "「目標價區間」欄：軌道兩端為最低／最高目標價，"
    + '<span class="legend-close"></span> 為目前收盤價、'
    + '<span class="legend-target"></span> 為共識目標價；'
    + "離散度 =（最高 − 最低）／共識目標價，數值越大代表各機構看法越分歧。"
    + "滑鼠移到區間上可看完整數字，移到列上可看資料備註。";
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
  ["sector-filter", "confidence-filter", "upside-filter", "analyst-filter",
   "dispersion-filter", "pb-filter", "cap-filter", "hide-missing", "hide-implausible"].forEach((id) =>
    document.getElementById(id).addEventListener("change", applyFilters)
  );
  document.getElementById("search-box").addEventListener("input", applyFilters);
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

/** 全美股是跨班次累積的，覆蓋率會隨進度成長——沒說清楚會被誤讀成「資料不全」。 */
function renderProgressNote(report) {
  const slot = document.getElementById("progress-note");
  const p = report.crawl_progress;
  if (state.source !== "universe" || !p || !p.total) {
    slot.innerHTML = "";
    return;
  }
  const done = (p.ok || 0);
  const pct = ((done / p.total) * 100).toFixed(1);
  slot.innerHTML =
    `<strong>本輪掃描進度 ${pct}%</strong>（${done} / ${p.total} 檔）。`
    + `尚待處理 ${p.pending || 0} 檔、可重試 ${p.failed || 0} 檔、已放棄 ${p.dead || 0} 檔。`
    + "全美股逐檔查詢受外部來源限流，分成每天多個班次完成，因此未列出的標的"
    + "多半是<strong>今天還沒輪到</strong>，而不是查不到。";
}

async function loadAndRender() {
  const src = DATA_SOURCES[state.source];
  document.getElementById("error-slot").innerHTML = "";
  try {
    const res = await fetch(src.path, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.report = await res.json();
  } catch (e) {
    showError(
      `讀取${src.label}估值資料失敗：${escapeHtml(e.message)}。` +
      `可能是對應的爬蟲尚未執行過，${escapeHtml(src.path)} 還不存在。`
    );
    return;
  }

  renderStats(state.report);
  renderProgressNote(state.report);
  renderSectorChart(state.report);
  populateSectorFilter(state.report);
  applyFilters();
}

async function main() {
  wireSorting();
  wireFilters();
  wireSourceSwitch();
  await loadAndRender();
}

main();
