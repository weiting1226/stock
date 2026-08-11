// fumée 搶位設定頁：純前端，直接呼叫 GitHub Contents API 讀寫
// fumee_booking/config.json。Token 只存在這台瀏覽器的 localStorage，
// 所有請求都是瀏覽器 -> api.github.com，不經過任何第三方伺服器。
(() => {
  const CONFIG_PATH = "fumee_booking/config.json";
  const LS_KEY = "fumee_booking_settings_v1";

  const $ = (id) => document.getElementById(id);

  const els = {
    owner: $("f-owner"),
    repo: $("f-repo"),
    branch: $("f-branch"),
    token: $("f-token"),
    url: $("f-url"),
    maxsec: $("f-maxsec"),
    interval: $("f-interval"),
    body: $("attempts-body"),
    status: $("status-slot"),
    secretsLink: $("secrets-link"),
  };

  function showStatus(msg, kind) {
    const cls = kind === "error" ? "error-banner" : "success-banner";
    els.status.innerHTML = `<div class="${cls}">${msg}</div>`;
  }

  function clearStatus() {
    els.status.innerHTML = "";
  }

  // ---- 偵測目前 repo（GitHub Pages 專案頁網址格式：<owner>.github.io/<repo>/...）----
  function detectOwnerRepo() {
    const host = location.hostname; // e.g. weiting1226.github.io
    const owner = host.endsWith(".github.io") ? host.split(".")[0] : "";
    const seg = location.pathname.split("/").filter(Boolean)[0] || "";
    return { owner, repo: seg };
  }

  function loadLocalSettings() {
    try {
      return JSON.parse(localStorage.getItem(LS_KEY) || "{}");
    } catch {
      return {};
    }
  }

  function saveLocalSettings() {
    const data = {
      owner: els.owner.value.trim(),
      repo: els.repo.value.trim(),
      branch: els.branch.value.trim() || "main",
      token: els.token.value.trim(),
    };
    localStorage.setItem(LS_KEY, JSON.stringify(data));
  }

  function updateSecretsLink() {
    const owner = els.owner.value.trim();
    const repo = els.repo.value.trim();
    if (owner && repo) {
      els.secretsLink.href = `https://github.com/${owner}/${repo}/settings/secrets/actions`;
    }
  }

  function initFields() {
    const detected = detectOwnerRepo();
    const saved = loadLocalSettings();
    els.owner.value = saved.owner || detected.owner || "";
    els.repo.value = saved.repo || detected.repo || "";
    els.branch.value = saved.branch || "main";
    els.token.value = saved.token || "";
    updateSecretsLink();
  }

  // ---- GitHub Contents API ----
  function apiBase() {
    const owner = els.owner.value.trim();
    const repo = els.repo.value.trim();
    return `https://api.github.com/repos/${owner}/${repo}/contents/${CONFIG_PATH}`;
  }

  async function ghRequest(method, body) {
    const token = els.token.value.trim();
    if (!token) throw new Error("請先貼上 GitHub token");
    const branch = els.branch.value.trim() || "main";
    const url = method === "GET" ? `${apiBase()}?ref=${encodeURIComponent(branch)}` : apiBase();
    const res = await fetch(url, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    return res;
  }

  function b64EncodeUtf8(str) {
    return btoa(unescape(encodeURIComponent(str)));
  }

  function b64DecodeUtf8(str) {
    return decodeURIComponent(escape(atob(str)));
  }

  // ---- attempts 表格 ----
  function addRow(attempt) {
    const a = attempt || { label: "", date: "", time: "17:30", party_size: 2 };
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="rownum"></td>
      <td><input type="text" class="a-label" value="${escapeAttr(a.label)}" placeholder="給自己看的備註" /></td>
      <td><input type="date" class="a-date" value="${escapeAttr(a.date)}" /></td>
      <td><input type="time" class="a-time" value="${escapeAttr(a.time)}" /></td>
      <td><input type="number" class="a-party" min="1" max="20" value="${a.party_size || 2}" /></td>
      <td class="btn-row">
        <button type="button" class="btn move-up" title="往上移">↑</button>
        <button type="button" class="btn move-down" title="往下移">↓</button>
        <button type="button" class="btn danger remove" title="刪除">✕</button>
      </td>`;
    tr.querySelector(".move-up").onclick = () => {
      const prev = tr.previousElementSibling;
      if (prev) tr.parentNode.insertBefore(tr, prev);
      renumberRows();
    };
    tr.querySelector(".move-down").onclick = () => {
      const next = tr.nextElementSibling;
      if (next) tr.parentNode.insertBefore(next, tr);
      renumberRows();
    };
    tr.querySelector(".remove").onclick = () => {
      tr.remove();
      renumberRows();
    };
    els.body.appendChild(tr);
    renumberRows();
  }

  function renumberRows() {
    [...els.body.children].forEach((tr, i) => {
      tr.querySelector(".rownum").textContent = i + 1;
    });
  }

  function escapeAttr(s) {
    return String(s || "").replace(/"/g, "&quot;");
  }

  function readAttemptsFromForm() {
    return [...els.body.children].map((tr) => ({
      label: tr.querySelector(".a-label").value.trim(),
      date: tr.querySelector(".a-date").value,
      time: tr.querySelector(".a-time").value,
      party_size: parseInt(tr.querySelector(".a-party").value, 10) || 1,
    }));
  }

  function populateForm(cfg) {
    els.url.value = cfg.booking_url || "";
    els.maxsec.value = cfg.max_attempt_seconds || 90;
    els.interval.value = cfg.retry_interval_ms || 500;
    els.body.innerHTML = "";
    (cfg.attempts || []).forEach(addRow);
    if (!cfg.attempts || cfg.attempts.length === 0) addRow();
  }

  // ---- Load ----
  let currentSha = null;

  async function onLoad() {
    clearStatus();
    saveLocalSettings();
    updateSecretsLink();
    if (!els.owner.value.trim() || !els.repo.value.trim()) {
      showStatus("請先填 repo owner / repo name", "error");
      return;
    }
    try {
      const res = await ghRequest("GET");
      if (res.status === 404) {
        currentSha = null;
        populateForm({});
        showStatus("目前repo裡還沒有 config.json，填好後按「儲存」會建立這個檔案。", "success");
        return;
      }
      if (!res.ok) {
        showStatus(`載入失敗（HTTP ${res.status}）：${await res.text()}`, "error");
        return;
      }
      const data = await res.json();
      currentSha = data.sha;
      const cfg = JSON.parse(b64DecodeUtf8(data.content.replace(/\n/g, "")));
      populateForm(cfg);
      showStatus("已載入目前設定。", "success");
    } catch (err) {
      showStatus(`載入失敗：${err.message}`, "error");
    }
  }

  // ---- Save ----
  async function onSave() {
    clearStatus();
    saveLocalSettings();
    const attempts = readAttemptsFromForm();
    if (!els.url.value.trim()) {
      showStatus("請填 inline 訂位頁面網址", "error");
      return;
    }
    if (attempts.length === 0 || attempts.some((a) => !a.date || !a.time)) {
      showStatus("每一組搶位優先順序都要填日期與時段", "error");
      return;
    }

    const cfg = {
      _說明:
        "複製這個檔案為 config.json 後填入資料，或直接用 docs/fumee-booking.html 網頁表單編輯（表單會透過GitHub API直接把改動送進repo）。這個檔案『不含』姓名/電話/Email——那些屬於個資，改用GitHub Secrets存放，不會進git歷史紀錄（見 fumee_booking/README.md）。",
      booking_url: els.url.value.trim(),
      _attempts說明:
        "依優先順序列出要嘗試的（日期、時段、人數）組合，程式會由上而下依序嘗試，第一個成功就停止。fumée吧檯座1-4人只開17:30與20:30；5人以上包廂只開19:00。每月1號00:00開放次月整月，所以date要填『下個月』的日期，每次搶位前記得更新這份清單。",
      attempts,
      _timing說明:
        "max_attempt_seconds：從00:00:00起最多嘗試搶位幾秒（超過就放棄，寄失敗通知）。retry_interval_ms：同一組合失敗後，隔多久再試下一個，避免對伺服器連續灌爆請求。",
      max_attempt_seconds: parseInt(els.maxsec.value, 10) || 90,
      retry_interval_ms: parseInt(els.interval.value, 10) || 500,
    };

    const content = b64EncodeUtf8(JSON.stringify(cfg, null, 2) + "\n");
    const branch = els.branch.value.trim() || "main";
    const body = {
      message: "chore: 更新 fumée 搶位設定 (docs/fumee-booking.html)",
      content,
      branch,
      ...(currentSha ? { sha: currentSha } : {}),
    };

    try {
      const res = await ghRequest("PUT", body);
      if (res.status === 409) {
        showStatus("儲存衝突：repo上的檔案在你載入之後被改過了，請重新「載入目前設定」再存一次。", "error");
        return;
      }
      if (!res.ok) {
        showStatus(`儲存失敗（HTTP ${res.status}）：${await res.text()}`, "error");
        return;
      }
      const data = await res.json();
      currentSha = data.content.sha;
      const commitUrl = data.commit && data.commit.html_url;
      showStatus(
        `已儲存並commit到 ${branch}。${commitUrl ? `<a href="${commitUrl}" target="_blank" rel="noopener">查看commit</a>` : ""}`,
        "success"
      );
    } catch (err) {
      showStatus(`儲存失敗：${err.message}`, "error");
    }
  }

  function onForgetToken() {
    els.token.value = "";
    saveLocalSettings();
    showStatus("已清除本機儲存的token。", "success");
  }

  document.addEventListener("DOMContentLoaded", () => {
    initFields();
    $("btn-add-row").onclick = () => addRow();
    $("btn-load").onclick = onLoad;
    $("btn-save").onclick = onSave;
    $("btn-forget").onclick = onForgetToken;
    [els.owner, els.repo, els.branch].forEach((el) => el.addEventListener("change", updateSecretsLink));
  });
})();
