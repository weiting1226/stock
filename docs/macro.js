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
  if (r.updated_today && r.updated_today.length) {
    html += `<div class="warning-box"><strong>今日新增 ${r.updated_today.length} 期資料。</strong>
      <div style="margin-top:8px">${r.updated_today.map((s) =>
        `<div>· ${escapeHtml(s.label)}：新增參考期 ${escapeHtml(s.reference_date)}</div>`
      ).join("")}</div></div>`;
  }
  if (r.discontinued_series && r.discontinued_series.length) {
    html += `<div class="warning-box"><strong>${r.discontinued_series.length} 條指標研判已停止發布。</strong>
      落後超過過期門檻四倍且至少一年——這與「延遲」不同，等下去不會有新資料，應從清單移除。
      <div style="margin-top:8px">${r.discontinued_series.map((s) =>
        `<div>· ${escapeHtml(s.label)}：最後一期 ${escapeHtml(s.reference_date)}，落後 <strong>${s.lag_days}</strong> 天</div>`
      ).join("")}</div></div>`;
  }
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
      <td>${x.news ? '<span class="badge ok" title="有官方發布摘要">摘要</span>' : ""}</td>
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

  renderAnalysis(r, row);
}

const AN = {
  zscore_full: ["z 分數（全期）", 2],
  zscore_10y: ["z 分數（10年）", 2],
  acceleration_3m: ["三個月加速度", 3],
};

/**
 * 深入分析面板。全部是可查證的量，不壓成單一分數——
 * 「總經健康度 72 分」那種數字沒有人查得出它是怎麼來的。
 */
function renderAnalysis(r, row) {
  const a = row.analysis || {};
  const stats = [];
  Object.entries(AN).forEach(([k, [label, digits]]) => {
    if (a[k] === undefined || a[k] === null) return;
    stats.push({ label, value: Number(a[k]).toFixed(digits), signed: Number(a[k]) });
  });
  const ex = a.extremes || {};
  if (ex.position_in_range_pct !== undefined) {
    stats.push({ label: "歷史區間位置", value: `${ex.position_in_range_pct.toFixed(0)}%`, plain: true });
  }
  const vol = a.volatility || {};
  if (vol.vol_ratio !== undefined) {
    stats.push({ label: "近期／長期波動", value: vol.vol_ratio.toFixed(2), plain: true });
  }
  if (a.observations) stats.push({ label: "觀測期數", value: String(a.observations), plain: true });

  document.getElementById("analysis-stats").innerHTML = stats.map((t) => {
    const cls = [t.plain ? "" : t.signed > 0 ? "pos" : t.signed < 0 ? "neg" : "",
                 t.text ? "is-text" : ""].filter(Boolean).join(" ");
    return `<div class="stat-tile"><div class="stat-value ${cls}">${escapeHtml(t.value)}</div>
      <div class="stat-label">${escapeHtml(t.label)}</div></div>`;
  }).join("");

  const parts = [];
  if (ex.high !== undefined) {
    parts.push(`<div><strong>歷史極值</strong>：最高 ${ex.high}（${escapeHtml(ex.high_date)}）、`
      + `最低 ${ex.low}（${escapeHtml(ex.low_date)}）；目前落在區間的 ${ex.position_in_range_pct.toFixed(0)}%。</div>`);
  }
  const tp = a.turning_point || {};
  if (tp.last_peak_date || tp.last_trough_date) {
    parts.push(`<div><strong>上一次轉折</strong>：`
      + (tp.last_peak_date ? `高點 ${escapeHtml(tp.last_peak_date)}（${tp.periods_since_peak} 期前）` : "")
      + (tp.last_peak_date && tp.last_trough_date ? "、" : "")
      + (tp.last_trough_date ? `低點 ${escapeHtml(tp.last_trough_date)}（${tp.periods_since_trough} 期前）` : "")
      + `。<span class="subtitle">最近 ${tp.confirmation_lag_periods} 期無法判定——`
      + `轉折需要後續資料才確認得了，硬判會產生一堆事後被推翻的假轉折。</span></div>`);
  }
  const rc = a.recession_contrast || {};
  if (rc.toward_recession_pct !== undefined && rc.toward_recession_pct !== null) {
    parts.push(`<div><strong>衰退期對照</strong>：擴張期平均 ${rc.expansion_mean}、`
      + `衰退期平均 ${rc.recession_mean}（${rc.recession_months} 個月的衰退期樣本）。`
      + `目前值位於兩者之間的 <strong>${rc.toward_recession_pct.toFixed(0)}%</strong>。`
      + `<br /><span class="subtitle">${escapeHtml(rc.note || "")}</span></div>`);
  }
  const orl = row.observed_release;
  if (orl) {
    parts.push(`<div><strong>觀測發布時滯</strong>：中位數 ${orl.median_lag_days} 天`
      + `（${orl.min_lag_days}~${orl.max_lag_days} 天，${orl.observations} 期觀測）。`
      + `<span class="subtitle">${escapeHtml(orl.note)}</span></div>`);
  }
  document.getElementById("analysis-detail").innerHTML =
    parts.length ? `<div class="warning-box" style="margin-top:16px">${parts.join("")}</div>` : "";

  renderNews(row);
}

/**
 * 官方發布摘要。三件事一定要一起顯示：摘要、原文佐證、來源連結。
 * 只給摘要的話，讀者無從判斷那句話是文件裡真的有，還是模型補出來的。
 */
function renderNews(row) {
  const slot = document.getElementById("news-detail");
  const n = row.news;
  if (!n) {
    slot.innerHTML = `<p class="subtitle">這項指標沒有當期的官方發布摘要。`
      + `殖利率與利差等市場價格類指標沒有對應的統計發布；`
      + `其餘則可能是文件抓取失敗或尚未輪到（見下方註記）。</p>`;
    return;
  }
  const drivers = (n.drivers || []).length
    ? `<div style="margin-top:8px">主要項目：${n.drivers.map((d) =>
        `<span class="source-pill">${escapeHtml(d)}</span>`).join(" ")}</div>`
    : "";
  slot.innerHTML = `<div class="warning-box" style="margin-top:16px">
    <div>${escapeHtml(n.summary_zh)}</div>
    ${drivers}
    <div style="margin-top:10px" class="subtitle">
      原文佐證（已驗證逐字出現於文件中）：<em>「${escapeHtml(n.evidence)}」</em>
    </div>
    <div style="margin-top:6px" class="subtitle">
      來源：<a href="${escapeHtml(n.source_url)}" target="_blank" rel="noopener">${escapeHtml(n.source_url)}</a>
      　摘要模型：${escapeHtml(n.model)}
    </div>
  </div>`;
}

const FAILURE_LABEL = {
  blocked: ["來源封鎖", "該站台對資料中心 IP 回 403。換路徑沒有用，需要改來源或改執行環境"],
  no_source: ["本來就沒有來源", "市場價格類指標（殖利率、利差、通膨預期）沒有對應的統計發布。這是正常狀態，不是故障"],
  content: ["內容不符", "抓到了頁面但看不出這一期的數字，多半是網址指向入口頁而非發布頁——模型拒絕摘要是對的，該檢查的是網址"],
  model: ["模型或 API 出錯", "下次執行會自動重試"],
};

function renderNewsNotes(r) {
  const n = r.news || {};
  const slot = document.getElementById("news-notes");
  const kinds = n.failure_kinds || {};
  // 四種失敗的處置完全不同，混成一份清單就看不出該做什麼
  const kindHtml = Object.entries(kinds).length
    ? `<div class="table-scroll"><table>
        <thead><tr><th>原因</th><th class="num">項數</th><th>該怎麼辦</th></tr></thead>
        <tbody>${Object.entries(kinds).sort((a, b) => b[1] - a[1]).map(([k, v]) => {
          const [label, advice] = FAILURE_LABEL[k] || [k, ""];
          return `<tr><td>${escapeHtml(label)}</td><td class="num">${v}</td>
            <td class="subtitle">${escapeHtml(advice)}</td></tr>`;
        }).join("")}</tbody></table></div>`
    : "";
  const notes = (n.notes || []).length
    ? `<details class="longtext" style="margin-top:12px"><summary><span class="clamp">`
      + `逐項原因（${n.notes.length} 則）</span></summary>`
      + `<div style="margin-top:8px">${n.notes.map((x) =>
          `<div class="subtitle">· ${escapeHtml(x)}</div>`).join("")}</div></details>`
    : "";
  slot.innerHTML =
    (n.attached ? `<p class="subtitle">目前有 <strong>${n.attached}</strong> 條指標帶有當期摘要。</p>` : "")
    + kindHtml + notes;
}

function renderReleaseLog(r) {
  const rows = r.rows.filter((x) => x.observed_release);
  const card = document.getElementById("release-card");
  const specLag = {};
  r.rows.forEach((x) => { if (x.publish_lag_days) specLag[x.fred_id] = x.publish_lag_days; });

  document.getElementById("release-summary").textContent = rows.length
    ? `已累積 ${rows.length} 條指標的發布觀測。`
    : "尚未累積足夠的觀測——每條指標需要看到兩期以上的更新才算得出發布節奏，"
      + "每日掃描會逐步累積。";

  document.querySelector("#release-table tbody").innerHTML = rows.map((x) => {
    const o = x.observed_release;
    return `<tr><td>${escapeHtml(x.label)}<span class="subtitle"> ${escapeHtml(x.fred_id)}</span></td>
      <td class="date">${escapeHtml(x.reference_date)}</td>
      <td class="num">${o.observations}</td>
      <td class="num">${o.median_lag_days} 天</td>
      <td class="num">${o.min_lag_days}~${o.max_lag_days}</td>
      <td class="num">${x.publish_lag_days ? x.publish_lag_days + " 天" : "—"}</td></tr>`;
  }).join("");
  card.hidden = false;
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
  renderReleaseLog(r);
  renderNewsNotes(r);
}

main();
