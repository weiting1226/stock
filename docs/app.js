const DATA_ROOT = "data";
const REPO_URL = "https://github.com/weiting1226/stock";

const LIGHT_COLOR = {
  "🟢🟢 深綠": "var(--good-deep)",
  "🟢 綠": "var(--good)",
  "🟡 黃": "var(--warning)",
  "🔴 紅": "var(--serious)",
  "🔴🔴 深紅": "var(--critical)",
};

const CATEGORY_ORDER = [
  "① 總體貨幣流動性", "② 資金成本與信用", "③ 市場微觀結構",
  "④ 風險偏好情緒", "⑤ 跨資產資金流向", "⑥ 政策方向",
];

const ITEM_CATEGORY = {
  "fed_bs_3m_chg": "① 總體貨幣流動性", "on_rrp_pctile": "① 總體貨幣流動性", "m2_yoy_3m_chg": "① 總體貨幣流動性",
  "hy_oas_level": "② 資金成本與信用", "hy_oas_20d_chg": "② 資金成本與信用", "t2s10s_bp": "② 資金成本與信用", "sofr_iorb_bp": "② 資金成本與信用",
  "move_index": "③ 市場微觀結構", "ndx_breadth_200d": "③ 市場微觀結構",
  "vix_level": "④ 風險偏好情緒", "ivts": "④ 風險偏好情緒", "margin_debt_yoy": "④ 風險偏好情緒",
  "dxy_1m_chg": "⑤ 跨資產資金流向", "etf_fund_flow": "⑤ 跨資產資金流向", "usdjpy_20d_chg": "⑤ 跨資產資金流向",
  "fomc_decision": "⑥ 政策方向", "fedwatch_path": "⑥ 政策方向", "yield30y_60d_momentum": "⑥ 政策方向",
};

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

async function fetchJson(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return res.json();
}

async function fetchText(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return res.text();
}

function parseCsv(text) {
  const lines = text.trim().split("\n");
  const headers = lines[0].split(",");
  return lines.slice(1).map((line) => {
    const cells = line.split(",");
    const row = {};
    headers.forEach((h, i) => { row[h] = cells[i]; });
    return row;
  });
}

function showError(msg) {
  const slot = document.getElementById("error-slot");
  slot.innerHTML = `<div class="error-banner">⚠️ ${msg}</div>`;
}

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || v === "" || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  return n.toFixed(digits);
}

function renderHero(report) {
  document.getElementById("composite-score").textContent = fmtNum(report.composite_score, 3);
  document.getElementById("composite-score").style.color = LIGHT_COLOR[report.light] || "inherit";
  document.getElementById("light-badge").textContent = report.light;
  document.getElementById("asof-label").textContent = `資料基準日：${report.as_of}（產生時間 ${report.generated_at || "—"}）`;
  document.getElementById("hero-meta").textContent =
    `缺失/低信心項：${report.missing_or_low_confidence_count} 項`;

  const pos = report.position || {};
  const banner = document.getElementById("position-banner");
  let text = `🎯 建議部位：${pos.final || "—"}`;
  if (pos.final_cap_applied) {
    text += ` （閘門觸發上限：${pos.final_cap_applied}，計分階梯原始結果為 ${pos.ladder_result}）`;
  }
  banner.textContent = text;
}

function gateCardHtml(title, gate) {
  if (!gate) return `<div class="gate-card"><h3>${title}</h3><span class="badge muted">暫缺</span></div>`;
  const cap = gate.cap;
  const badgeClass = cap === "SGOV" ? "bad" : cap === "QQQ" ? "warn" : "ok";
  const badgeText = cap ? `上限 ${cap}` : "未觸發";
  const status = gate.status || (Array.isArray(gate.reasons) ? gate.reasons.join("；") : "");
  const detail = gate.detail || (gate.reasons ? gate.reasons.join("；") : "");
  return `
    <div class="gate-card">
      <h3>${title} <span class="badge ${badgeClass}">${badgeText}</span></h3>
      <div class="subtitle">${status || ""}</div>
      <div style="margin-top:6px;font-size:0.85rem;">${detail || ""}</div>
    </div>`;
}

function renderGates(report) {
  const grid = document.getElementById("gates-grid");
  grid.innerHTML =
    gateCardHtml("Gate A — 價格閘門", report.gate_a) +
    gateCardHtml("Gate B — 脆弱度閘門", report.gate_b) +
    gateCardHtml("Gate C — 資料完整度閘門", report.gate_c);
}

function renderWarnings(report) {
  const slot = document.getElementById("warnings-slot");
  const w = report.structural_warnings || {};
  let html = "";
  if (w.dominance) {
    const d = w.dominance;
    html += `<div class="warning-box">⚠️ 本指令實質由單一類別決定：<strong>${d.label}</strong>
      （貢獻 ${d.contribution}，占同號貢獻 ${(d.share_of_same_sign * 100).toFixed(1)}%）。
      若排除該類別，綜合分數將變為 <strong>${d.score_if_excluded}</strong>。</div>`;
  }
  if (w.divergence && w.divergence.length) {
    w.divergence.forEach((d) => {
      html += `<div class="warning-box">⚠️ 結構性背離：${d.pair} 評分差距達 ${d.gap}
        （② = ${d.credit_funding}，對照類別 = ${d.other}）。</div>`;
    });
  }
  slot.innerHTML = html;
}

function renderCategoryTable(report) {
  const tbody = document.querySelector("#category-table tbody");
  tbody.innerHTML = "";
  CATEGORY_ORDER.forEach((cat) => {
    const score = report.category_scores ? report.category_scores[cat] : null;
    const weight = report.category_weights_effective ? report.category_weights_effective[cat] : null;
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${cat}</td><td class="num">${fmtNum(score, 3)}</td><td class="num">${weight != null ? (weight * 100).toFixed(1) + "%" : "—"}</td>`;
    tbody.appendChild(tr);
  });
}

function renderItemsTable(report) {
  const tbody = document.querySelector("#items-table tbody");
  tbody.innerHTML = "";
  CATEGORY_ORDER.forEach((cat) => {
    const catRow = document.createElement("tr");
    catRow.className = "category-row";
    catRow.innerHTML = `<td colspan="7">${cat}</td>`;
    tbody.appendChild(catRow);
    Object.entries(report.items || {}).forEach(([key, item]) => {
      if (ITEM_CATEGORY[key] !== cat) return;
      const tr = document.createElement("tr");
      const confClass = `confidence-${(item.confidence || "").replace(/\(.*\)/, "")}`;
      tr.innerHTML = `
        <td>${item.label || key}</td>
        <td class="num">${typeof item.raw_value === "number" ? fmtNum(item.raw_value, 3) : (item.raw_value ?? "—")}</td>
        <td class="num">${item.score ?? "—"}</td>
        <td class="${confClass}">${item.confidence || "—"}</td>
        <td>${item.data_date || "—"}</td>
        <td>${item.source || "—"}</td>
        <td>${item.note || ""}</td>`;
      tbody.appendChild(tr);
    });
  });
}

function renderTrack2(report) {
  const list = document.getElementById("track2-list");
  const t2 = report.track2_daily_approx || {};
  const labels = {
    vix9d_gt_vix: "VIX9D > VIX（短端倒掛）",
    treasury_1d_move_gt_15bp: "美債殖利率單日變動 > 15bp",
    ndx_1d_drop_gt_2pct: "NDX 單日下跌 > 2%",
    stocks_bonds_gold_dollar_all_down: "跨資產股債金幣齊跌",
  };
  list.innerHTML = "";
  Object.entries(t2.signals || {}).forEach(([key, val]) => {
    const badge = val === true ? '<span class="badge bad">是</span>' : val === false ? '<span class="badge ok">否</span>' : '<span class="badge muted">暫缺</span>';
    const li = document.createElement("li");
    li.innerHTML = `${badge} ${labels[key] || key}`;
    list.appendChild(li);
  });
  document.getElementById("track2-note").textContent =
    (t2.reliability_note || "") + `　觸發項數：${t2.triggered_count ?? "—"}　建議熔斷：${t2.circuit_breaker_suggested ? "是" : "否"}`;
}

let categoryChart, historyChart;

function renderCategoryChart(report) {
  const ctx = document.getElementById("category-chart");
  const colors = [cssVar("--series-1"), cssVar("--series-2"), cssVar("--series-3"), cssVar("--series-4"), cssVar("--series-5"), cssVar("--series-6")];
  const data = CATEGORY_ORDER.map((c) => (report.category_scores ? report.category_scores[c] : null));
  if (categoryChart) categoryChart.destroy();
  categoryChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: CATEGORY_ORDER,
      datasets: [{ data, backgroundColor: colors, borderRadius: 4, barThickness: 28 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { min: -2, max: 2, grid: { color: cssVar("--gridline") }, ticks: { color: cssVar("--text-muted") } },
        x: { grid: { display: false }, ticks: { color: cssVar("--text-muted"), font: { size: 10 } } },
      },
    },
  });
}

const lightBandPlugin = {
  id: "lightBands",
  beforeDraw(chart) {
    const { ctx, chartArea, scales } = chart;
    if (!chartArea) return;
    const bands = [
      [1.2, 2.0, "rgba(12,163,12,0.10)"],
      [0.4, 1.2, "rgba(12,163,12,0.05)"],
      [-0.4, 0.4, "rgba(250,178,25,0.08)"],
      [-1.2, -0.4, "rgba(236,131,90,0.08)"],
      [-2.0, -1.2, "rgba(208,59,59,0.10)"],
    ];
    bands.forEach(([lo, hi, color]) => {
      const yTop = scales.y.getPixelForValue(hi);
      const yBot = scales.y.getPixelForValue(lo);
      ctx.save();
      ctx.fillStyle = color;
      ctx.fillRect(chartArea.left, yTop, chartArea.right - chartArea.left, yBot - yTop);
      ctx.restore();
    });
  },
};

function renderHistoryChart(rows) {
  const ctx = document.getElementById("history-chart");
  const recent = rows.slice(-180);
  if (historyChart) historyChart.destroy();
  historyChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: recent.map((r) => r.as_of),
      datasets: [{
        data: recent.map((r) => Number(r.composite_score)),
        borderColor: cssVar("--series-1"),
        backgroundColor: "transparent",
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.15,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { min: -2, max: 2, grid: { color: cssVar("--gridline") }, ticks: { color: cssVar("--text-muted") } },
        x: { grid: { display: false }, ticks: { color: cssVar("--text-muted"), maxTicksLimit: 8, font: { size: 10 } } },
      },
    },
    plugins: [lightBandPlugin],
  });
}

async function populateDatePicker(currentAsOf) {
  const picker = document.getElementById("date-picker");
  try {
    const idx = await fetchJson(`${DATA_ROOT}/history_index.json`);
    idx.slice().reverse().forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d;
      opt.textContent = d;
      if (d === currentAsOf) opt.selected = true;
      picker.appendChild(opt);
    });
  } catch (e) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = currentAsOf || "—";
    picker.appendChild(opt);
  }
  picker.addEventListener("change", () => loadReport(picker.value));
}

async function loadReport(asOf) {
  try {
    const path = asOf ? `${DATA_ROOT}/snapshots/${asOf}.json` : `${DATA_ROOT}/latest.json`;
    const report = await fetchJson(path);
    document.getElementById("error-slot").innerHTML = "";
    renderHero(report);
    renderGates(report);
    renderWarnings(report);
    renderCategoryTable(report);
    renderItemsTable(report);
    renderTrack2(report);
    renderCategoryChart(report);
  } catch (e) {
    showError(`讀取報告失敗：${e.message}（可能是每日爬蟲尚未第一次執行，docs/data/ 底下還沒有資料檔）`);
  }
}

async function main() {
  document.getElementById("repo-link").href = REPO_URL;
  let latest = null;
  try {
    latest = await fetchJson(`${DATA_ROOT}/latest.json`);
  } catch (e) {
    showError(`尚未有任何資料（${e.message}）。請確認 GitHub Actions 每日排程已執行過至少一次，` +
      `或手動執行 <code>python3 scripts/run_daily.py</code> 產生 docs/data/latest.json。`);
  }
  await populateDatePicker(latest ? latest.as_of : null);
  if (latest) await loadReport(null);

  try {
    const csv = await fetchText(`${DATA_ROOT}/scores_history.csv`);
    renderHistoryChart(parseCsv(csv));
  } catch (e) {
    // 歷史檔案還不存在（第一次執行）時安靜略過折線圖
  }
}

main();
