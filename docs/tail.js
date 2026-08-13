// 模組六：尾部脆弱度雙閘門。資料由 scripts/run_tail_gate.py 產生。
//
// 這一頁的設計重點與其他五頁不同：其他頁在呈現資料，這一頁還要呈現
// **這些資料有多可信**。一個紅色的 STRESS 看起來跟已驗證過的訊號一模一樣，
// 而這個模組的門檻沒有一個校準過——那件事必須在畫面上，不能只在 README。
const DATA_PATH = "data/tail/latest.json";

const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escapeHtml = (v) =>
  v === null || v === undefined ? "" : String(v).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);

const state = { charts: {} };

function pct(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return (Number(v) * 100).toFixed(digits) + "%";
}
function num(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return Number(v).toFixed(digits);
}

const REGIME_COLOR = {
  EXPANSION: "--good", WATCH: "--warning",
  CONTRACTION: "--serious", STRESS: "--critical",
};
const REGIME_ZH = {
  EXPANSION: "擴張", WATCH: "觀察", CONTRACTION: "收縮", STRESS: "壓力",
};

function showError(msg) {
  document.getElementById("error-slot").innerHTML = `<div class="error-banner">${msg}</div>`;
}

function draw(id, config) {
  if (state.charts[id]) state.charts[id].destroy();
  state.charts[id] = new Chart(document.getElementById(id), config);
}

function tiles(list) {
  return list.map((t) => `<div class="stat-tile">
    <div class="stat-value" style="${t.color ? `color:${cssVar(t.color)}` : ""}">${escapeHtml(t.value)}</div>
    <div class="stat-label">${escapeHtml(t.label)}</div></div>`).join("");
}

// --- 免責與可信度 ------------------------------------------------------------

function renderCaveats(r) {
  const ev = r.ev || {};
  const unval = ev.unvalidated_count || 0;
  document.getElementById("caveat-body").innerHTML = `
    <p><strong>這個模組是影子運行：只記錄，不下單。</strong>${escapeHtml(r.shadow_note || "")}</p>
    <ul class="checklist">
      <li><strong>期望值約 +1.2%/年</strong>，且大部分來自約七年一遇的危機型事件。
          它不是黑天鵝防護罩，是<strong>薄利的尾部保險</strong>。決定要不要照著做之前，
          先確認你接受這個報酬／複雜度比。</li>
      <li><strong>慢閘的門檻未經真實資料校準。</strong>下方的分數與 regime 結構正確，
          但閾值沒有驗證過——它可能系統性過鬆或過緊，而畫面上看不出來。</li>
      <li><strong>快閘的統計基礎是 n=1。</strong>樣本裡真正的危機型事件只有 2020 一個。
          等級 2 與等級 3 的差別，背後沒有足以支撐的樣本數。</li>
      <li>期望值模型的 <strong>${unval} 項假設全部未實測</strong>，
          其中 <code>whipsaw_cost</code> 是最大的不確定來源。</li>
    </ul>`;
}

// --- 配置 --------------------------------------------------------------------

function renderAlloc(r) {
  const b = r.base_alloc || {}, o = r.out_alloc || {};
  document.getElementById("alloc-row").innerHTML = tiles([
    { label: "權益上限", value: pct(r.equity_ceiling, 0) },
    { label: "慢閘 regime", value: REGIME_ZH[r.slow?.regime] || "—",
      color: REGIME_COLOR[r.slow?.regime] },
    { label: "否決等級", value: `${r.fast?.gated_veto ?? "—"} / 3`,
      color: (r.fast?.gated_veto ?? 0) > 0 ? "--critical" : "--good" },
    { label: "本模組是否調整", value: r.changed ? "有" : "無",
      color: r.changed ? "--serious" : "--text-muted" },
  ]);

  draw("alloc-chart", {
    type: "bar",
    data: {
      labels: ["TQQQ", "QQQ", "SGOV"],
      datasets: [
        { label: "主評分（模組一）", data: [b.tqqq, b.qqq, b.sgov],
          backgroundColor: cssVar("--series-1") },
        { label: "經模組六", data: [o.tqqq, o.qqq, o.sgov],
          backgroundColor: cssVar("--series-2") },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, indexAxis: "y",
      scales: { x: { max: 1, ticks: { callback: (v) => (v * 100) + "%" } } },
      plugins: { legend: { position: "bottom" } },
    },
  });

  const src = r.base_alloc?.source || {};
  document.getElementById("alloc-note").innerHTML =
    `主評分來自模組一（${escapeHtml(src.as_of || "—")}：${escapeHtml(src.text || "—")}）。
     <strong>本模組是純否決式</strong>——它只會把權益部位往下壓，釋出的權重一律進 SGOV，
     不可能把你從 SGOV 拉回 TQQQ。TQQQ 另有 ${pct(r.tqqq_hard_cap, 0)} 的事前硬上限。`;
}

// --- 兩道閘門 ----------------------------------------------------------------

function renderGates(r) {
  const s = r.slow || {}, f = r.fast || {}, c = r.crowding || {};
  const sd = s.detail || {}, fd = f.detail || {}, cd = c.detail || {};

  const triggered = (fd.triggered || []);
  const missing = (fd.missing || []);
  const CAT_ZH = { term_structure: "期限結構倒掛", vol_of_vol: "波動的波動", credit_shock: "信用急變" };

  document.getElementById("gates-grid").innerHTML = `
    <div class="gate-card">
      <h3>慢閘 · CreditProxyGate</h3>
      <div class="light-badge" style="color:${cssVar(REGIME_COLOR[s.regime] || "--text-muted")}">
        ${escapeHtml(REGIME_ZH[s.regime] || "—")} ${escapeHtml(s.regime || "")}</div>
      <div class="subtitle">分數 ${num(s.score)} / 100 → 權益上限 ${pct(s.equity_ceiling, 0)}</div>
      <ul class="checklist">
        <li>壓力水準 ${num(sd.stress_level, 4)}（500 日回撤與低於 200MA 取大者）</li>
        <li>250 日變化百分位 ${num(sd.chg250_pctile)}</li>
        <li>HY vs IG 60 日相對變化 ${sd.hyg_lqd_chg60 === null || sd.hyg_lqd_chg60 === undefined
            ? "—（LQD 缺漏，該項不計分）" : num(sd.hyg_lqd_chg60 * 100, 2) + "%"}</li>
        <li>久期對沖 beta ${num(sd.beta, 3)}（${escapeHtml(sd.beta_source || "")}）</li>
      </ul>
      <p class="subtitle">用 HYG／LQD／IEF 合成，並把久期洗掉——否則升息會被誤讀成信用惡化。
      價格必須是<strong>除息調整價</strong>：HYG 年配息約 6~7%，用未調整價會讓這一格長期偏紅。</p>
    </div>

    <div class="gate-card">
      <h3>快閘 · 波動與信用</h3>
      <div class="light-badge" style="color:${cssVar((f.gated_veto ?? 0) > 0 ? "--critical" : "--good")}">
        等級 ${f.gated_veto ?? "—"} / 3</div>
      <div class="subtitle">原始 ${f.raw_veto ?? "—"} → 經持續期閘門 ${f.gated_veto ?? "—"}</div>
      <ul class="checklist">
        <li>VIX9D ${num(f.vix9d)} ／ VIX ${num(f.vix)} ／ VIX3M ${num(f.vix3m)}</li>
        <li>曲線倒掛：<strong>${f.curve_inverted ? "是" : "否"}</strong>（需 VIX9D &gt; VIX &gt; VIX3M）</li>
        <li>VVIX 百分位 ${num(fd.vvix_pctile)}（門檻 ${num(fd.vvix_trigger)}）</li>
        <li>HY OAS 20 日變化 ${num(fd.hy_chg20_bp, 1)} bp（門檻 ${num(fd.hy_trigger_bp, 1)}）</li>
      </ul>
      <p class="subtitle">觸發：${triggered.length
          ? triggered.map((t) => escapeHtml(CAT_ZH[t] || t)).join("、") : "無"}${
          missing.length ? `；<strong>缺資料</strong>：${missing.map((t) => escapeHtml(CAT_ZH[t] || t)).join("、")}` : ""}。
      缺資料的類別不計分——那與「沒有訊號」是兩件事。</p>
    </div>

    <div class="gate-card">
      <h3>擁擠度</h3>
      <div class="light-badge">${cd.known ? num(c.score, 0) : "未知"}</div>
      <div class="subtitle">門檻位移 ${num(c.threshold_shift, 2)}
        ${cd.coverage ? `（涵蓋 ${escapeHtml(cd.coverage)}）` : ""}</div>
      <p class="subtitle">擁擠本身不預測下跌，它決定<strong>下跌時會有多痛</strong>——
      部位擁擠時同一個衝擊會引發更多強制平倉。因此它不產生否決，只讓快閘的門檻更容易觸發。</p>
      ${cd.known ? "" : `<p class="subtitle"><strong>目前四項輸入全缺，回中性、不位移。</strong>
      這是「不知道」，不是「不擁擠」。</p>`}
      ${(cd.missing || []).length ? `<p class="subtitle">缺：${cd.missing.map(escapeHtml).join("、")}</p>` : ""}
    </div>`;
}

// --- 跨日狀態 ----------------------------------------------------------------

function renderState(r) {
  const st = r.state || {}, meta = r.state_meta || {}, ctl = r.controller || {};
  document.getElementById("state-row").innerHTML = tiles([
    { label: "否決等級（生效中）", value: st.active_veto ?? "—" },
    { label: `倒掛連續日數 / 需 ${ctl.persist_days ?? "—"}`, value: st.consec_inversion ?? "—",
      color: (st.consec_inversion ?? 0) >= (ctl.persist_days ?? 99) ? "--critical" : "" },
    { label: "無訊號連續日數", value: st.clean_days ?? "—" },
  ]);

  const staleness = meta.staleness_days;
  let warn = "";
  if (!meta.found) {
    warn = `<strong style="color:${cssVar("--critical")}">狀態檔不存在或無法讀取（${escapeHtml(meta.reason || "")}）。</strong>`;
  } else if (staleness !== null && staleness !== undefined && staleness > 5) {
    warn = `<strong style="color:${cssVar("--critical")}">狀態檔停在 ${staleness} 天前——排程可能中斷過。</strong>`;
  }

  document.getElementById("state-note").innerHTML = `${warn}
    這三個數字必須<strong>跨日保存</strong>。漏存的話，倒掛計數器每天都從 0 重數，
    永遠到不了 ${escapeHtml(String(ctl.persist_days ?? "—"))} 天——
    <strong>快閘等於已經關閉，而畫面上一切正常、不會報錯</strong>。
    這是這個模組最常見、也最看不出來的失效方式，因此每日執行後會斷言：
    若曲線倒掛為真但計數器是 0，直接失敗。`;
}

// --- 歷史 --------------------------------------------------------------------

function renderHistory(r) {
  const h = r.history || { dates: [], series: {} };
  if (!h.dates.length) {
    document.getElementById("history-chart").closest(".card").querySelector(".subtitle")
      .textContent = "日誌尚未累積（本模組剛上線）。";
    return;
  }
  draw("history-chart", {
    type: "line",
    data: {
      labels: h.dates,
      datasets: [
        { label: "慢閘分數", data: h.series.cp_score || [], yAxisID: "y",
          borderColor: cssVar("--series-1"), pointRadius: 0, borderWidth: 1.5 },
        { label: "否決等級", data: h.series.state_active_veto || [], yAxisID: "y1",
          borderColor: cssVar("--critical"), pointRadius: 0, borderWidth: 1.5, stepped: true },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        y: { min: 0, max: 100, title: { display: true, text: "慢閘分數" } },
        y1: { min: 0, max: 3, position: "right", grid: { drawOnChartArea: false },
              ticks: { stepSize: 1 }, title: { display: true, text: "否決等級" } },
      },
      plugins: { legend: { position: "bottom" } },
    },
  });
}

// --- 期望值 ------------------------------------------------------------------

const ASSUMPTION_ZH = {
  crisis_period_years: "危機型事件重現期（年）",
  crisis_avoided_drawdown: "危機中避開的相對損失",
  moderate_gain_annual: "非危機事件的年化效益",
  false_positive_per_year: "誤觸次數／年",
  whipsaw_cost: "每次誤觸的來回成本",
};

function renderEV(r) {
  const ev = r.ev || {};
  document.getElementById("ev-row").innerHTML = tiles([
    { label: "年化期望值", value: pct(ev.expected_annual, 2),
      color: (ev.expected_annual ?? 0) > 0 ? "--good" : "--critical" },
    { label: "危機型事件佔比", value: pct(ev.crisis_share, 0) },
    { label: "誤觸年成本", value: pct(ev.false_positive_cost_annual, 2), color: "--critical" },
    { label: "未實測的假設", value: `${ev.unvalidated_count ?? "—"} / 5`, color: "--warning" },
  ]);

  const a = ev.assumptions || {}, v = ev.validated || {};
  document.querySelector("#ev-table tbody").innerHTML = Object.keys(a).map((k) =>
    `<tr><td>${escapeHtml(ASSUMPTION_ZH[k] || k)}<span class="subtitle"> ${escapeHtml(k)}</span></td>
      <td class="num">${escapeHtml(a[k])}</td>
      <td style="color:${cssVar(v[k] ? "--good" : "--critical")}">${v[k] ? "已實測" : "未實測"}</td></tr>`
  ).join("");

  document.getElementById("ev-note").innerHTML = `${escapeHtml(ev.note || "")}
    ${ev.reproduces_doc
      ? `本模組算出的期望值與接線說明的 ${pct(ev.doc_expected_annual, 1)}／${pct(ev.doc_crisis_share, 0)} 一致。`
      : `<strong style="color:${cssVar("--critical")}">本模組算出的期望值與接線說明的
         ${pct(ev.doc_expected_annual, 1)} 不一致——代表這裡的假設已經與原始模型脫節。</strong>`}`;

  const sens = r.ev_sensitivity || [];
  draw("ev-chart", {
    type: "line",
    data: {
      labels: sens.map((x) => (x.whipsaw_cost * 100).toFixed(2) + "%"),
      datasets: [{
        label: "年化期望值",
        data: sens.map((x) => x.expected_annual),
        borderColor: cssVar("--series-2"),
        pointBackgroundColor: sens.map((x) =>
          cssVar(x.is_assumed ? "--series-1" : x.expected_annual > 0 ? "--good" : "--critical")),
        pointRadius: sens.map((x) => (x.is_assumed || x.is_breakeven ? 5 : 3)),
        borderWidth: 1.5,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        y: { ticks: { callback: (v) => (v * 100).toFixed(1) + "%" },
             title: { display: true, text: "年化期望值" } },
        x: { title: { display: true, text: "whipsaw_cost（每次誤觸的來回成本）" } },
      },
      plugins: { legend: { display: false } },
    },
  });

  const be = sens.find((x) => x.is_breakeven);
  const assumed = sens.find((x) => x.is_assumed);
  if (be && assumed) {
    const ratio = be.whipsaw_cost / assumed.whipsaw_cost;
    document.getElementById("ev-sens-note").innerHTML =
      `假設值 ${pct(assumed.whipsaw_cost, 2)}，損益兩平點 ${pct(be.whipsaw_cost, 2)}——
       <strong>誤觸成本只要比假設高約 ${((ratio - 1) * 100).toFixed(0)}%，整個模組的期望值就歸零。</strong>
       而這個參數目前完全沒有實測過。日誌已在記錄 TQQQ 與 QQQ 收盤價，
       等快閘真的觸發並回場後就能算出實際值。`;
  }
}

// --- 代理驗證 ----------------------------------------------------------------

const CAL_ZH = {
  corr_level_dd_vs_oas: "代理指數回撤 vs OAS 水準（需 &gt; 0.6）",
  corr_change_60d: "60 日變化的反向對應（需 &lt; −0.5）",
  regime_agreement: "分級一致率 · 排序基準（需 &gt; 0.70）",
};

function renderCalibration(r) {
  const c = r.calibration;
  const slot = document.getElementById("calibration-body");
  if (!c) {
    slot.innerHTML = `<p class="subtitle"><strong>尚未執行。</strong>
      需要 HY OAS 序列（FRED <code>BAMLH0A0HYM2</code>）才能比對，本次執行沒有取得。</p>`;
    return;
  }
  if (c.error) {
    slot.innerHTML = `<p class="subtitle"><strong>無法判讀：</strong>${escapeHtml(c.error)}</p>`;
    return;
  }
  const targets = c.targets || {};
  const rows = Object.keys(CAL_ZH).map((k) => {
    const val = c[k];
    let ok = null;
    if (val !== null && val !== undefined) {
      ok = k === "corr_change_60d" ? val < targets[k] : val > targets[k];
    }
    return `<tr><td>${CAL_ZH[k]}</td><td class="num">${num(val, 3)}</td>
      <td style="color:${cssVar(ok === null ? "--text-muted" : ok ? "--good" : "--critical")}">
        ${ok === null ? "無值" : ok ? "達標" : "未達標"}</td></tr>`;
  }).join("");

  const benchNote = c.regime_agreement_expected === undefined ? "" : `
    <p class="subtitle"><strong>分級一致率要放在基準旁邊讀。</strong>
    四分位一致率本來就比相關係數難達標得多：實測相關 ${num(c.corr_level_dd_vs_oas, 3)}
    在雙變量常態下的期望一致率是 <strong>${num(c.regime_agreement_expected, 3)}</strong>，
    而實測是 ${num(c.regime_agreement_rank, 3)}
    （落差 ${num(c.regime_agreement_shortfall, 3)}）。<br>
    更要緊的是：<strong>一致率門檻 0.70 需要相關係數約
    ${num(c.corr_needed_for_agreement_target, 3)}</strong>，
    而說明自己的相關係數門檻只有 ${num((c.targets || {}).corr_level_dd_vs_oas, 2)}。
    ${c.targets_are_mutually_consistent === false
      ? `<strong style="color:${cssVar("--critical")}">也就是說，說明的三個門檻彼此不相容——
         一個剛好通過前兩項的代理，數學上不可能通過第三項。</strong>
         這是門檻的問題，不是代理的問題；但落差為負，代表代理本身也確實不足。`
      : ""}</p>`;

  const verdict = c.passed === null || c.passed === undefined
    ? `<strong>還不知道</strong>——有項目算不出值。「還不知道」與「沒通過」是兩件事，
       混在一起會讓人以為驗證做過了。`
    : c.passed
      ? `<strong style="color:${cssVar("--good")}">三項全數達標。</strong>
         方向是對的；但三年樣本仍不足以定百分位門檻。`
      : `<strong style="color:${cssVar("--critical")}">未達標。</strong>
         先調 <code>hedge_beta</code>（固定 0.47 vs 滾動估計），仍不行則用 JNK 取代 HYG。`;

  slot.innerHTML = `
    <div class="table-scroll"><table>
      <thead><tr><th>判讀項目</th><th class="num">實測</th><th>結果</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="subtitle">重疊樣本 ${escapeHtml(c.n_obs ?? "—")} 筆，久期對沖 beta ${num(c.beta, 3)}。${verdict}</p>
    ${benchNote}
    ${c.regime_agreement_absolute === undefined ? "" : `<p class="subtitle">
      <strong>另一個分級一致率：${num(c.regime_agreement_absolute, 3)}</strong>（代理的<em>正式門檻</em>
      vs OAS 四分位）。這一項預期不會達標——<strong>門檻本來就沒校準過</strong>，
      而四分位在結構上強制各 25%，平靜期裡它會把最近三年相對最差的四分之一天標成 STRESS。
      上表用的是排序基準（兩邊都取四分位），問的是「代理排序日子的方式跟 OAS 一樣嗎」。
      兩個都列出來，是因為它們回答的是不同問題，任何一個單獨看都會誤導。</p>`}
    <p class="subtitle">三年樣本不足以定百分位門檻，但足以判斷<strong>方向對不對</strong>。
    方向錯了，門檻調再多也沒用。（FRED 的 HY OAS 只給得出近三年，這是 ICE 授權限制，
    本專案已實測確認。）</p>`;
}

// --- 其餘 --------------------------------------------------------------------

const LAG_ZH = {
  vix_family: ["VIX / VIX3M / VIX9D / VVIX", "CBOE／Yahoo，收盤後可得"],
  credit_proxy: ["HYG / LQD / IEF 調整價", "T+0"],
  funding: ["SOFR / IORB", "FRED，無授權限制"],
  cftc_jpy: ["CFTC 日圓淨部位", "週頻，且延遲三日——別誤以為是即時值"],
  margin_debt: ["FINRA 融資餘額", "月頻，約 T+30"],
};

function renderTail(r) {
  document.querySelector("#limit-table tbody").innerHTML = (r.limitations || []).map((x) =>
    `<tr><td>${escapeHtml(x.limit)}</td><td>${escapeHtml(x.impact)}</td>
      <td>${escapeHtml(x.solvable)}</td></tr>`).join("");

  document.querySelector("#lag-table tbody").innerHTML =
    Object.entries(r.input_lags || {}).map(([k, v]) => {
      const [label, note] = LAG_ZH[k] || [k, ""];
      return `<tr><td>${escapeHtml(label)}</td><td class="num">${escapeHtml(v)}</td>
        <td class="subtitle">${escapeHtml(note)}</td></tr>`;
    }).join("");

  const problems = r.problems || [];
  document.getElementById("problems-slot").innerHTML = problems.length
    ? `<p class="subtitle"><strong>本次執行的取得問題（${problems.length}）：</strong></p>`
      + problems.map((p) => `<div class="subtitle">· ${escapeHtml(p)}</div>`).join("")
    : `<p class="subtitle">本次執行沒有取得問題。</p>`;
}

async function load() {
  try {
    const res = await fetch(DATA_PATH, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const r = await res.json();
    document.getElementById("asof-label").textContent =
      `資料基準日 ${r.as_of}｜影子運行：只記錄、不下單；本頁純資料呈現，非投資建議`;
    renderCaveats(r);
    renderAlloc(r);
    renderGates(r);
    renderState(r);
    renderHistory(r);
    renderEV(r);
    renderCalibration(r);
    renderTail(r);
  } catch (e) {
    showError(`載入 ${DATA_PATH} 失敗：${escapeHtml(e.message)}。模組六可能尚未執行過。`);
  }
}

load();
