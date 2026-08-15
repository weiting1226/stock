// 七頁共用的介面增強。純漸進式：不載入這支檔案，每一頁仍然完整可用。
//
// **表頭跟隨捲動**，但只在該表格真的不需要橫向捲動時才啟用。
//
// 為什麼要量測而不是設一個斷點：`.table-scroll` 用的是 `overflow-x: auto`，
// 而 CSS 規定另一軸的 `visible` 會被強制成 `auto`——於是那個容器變成一個
// 捲動盒，`position: sticky` 會被綁在裡面，跟著整頁一起捲走。實測捲動 4000px
// 後表頭的 top 是 −2745，完全沒有黏住。
//
// 所以要嘛橫向捲動、要嘛表頭跟隨，兩者無法並存。哪一個該讓步，取決於
// 這張表在這個視窗寬度下**到底有沒有溢出**——那是可以量的，不必用斷點猜。
// 實測七頁只有估值表會溢出（1280px 下 8px），其餘全部放得下。
//
// 這件事值得做，是因為估值表在桌機上約 20,000px 高：捲到中段時滿螢幕都是
// 沒有欄位標題的數字，175.58 是收盤價還是目標價完全看不出來。

(function () {
  "use strict";

  function stickyTop() {
    const nav = document.querySelector(".module-nav");
    if (!nav) return 0;
    // 只有真的固定住的導覽才需要讓位
    return getComputedStyle(nav).position === "sticky"
      ? Math.round(nav.getBoundingClientRect().height)
      : 0;
  }

  function apply() {
    const top = stickyTop();
    document.querySelectorAll(".table-scroll").forEach((sc) => {
      if (!sc.querySelector("thead")) return;
      // +1 容忍次像素捨入；差一兩個像素就關掉表頭並不划算
      const fits = sc.scrollWidth <= sc.clientWidth + 1;
      sc.classList.toggle("sticky-head", fits);
      if (fits) sc.style.setProperty("--sticky-top", top + "px");
      else sc.style.removeProperty("--sticky-top");
    });
  }

  let pending = null;
  function schedule() {
    if (pending) cancelAnimationFrame(pending);
    pending = requestAnimationFrame(() => { pending = null; apply(); });
  }


// ---------------------------------------------------------------------------
// 模組選單
//
// **為什麼不只留那條標籤列**：七項而且還在增加，標籤本身又不說明那一頁在做
// 什麼——「模組三：策略回測」回測了什麼、用什麼資料，看標籤是不知道的。
// 而手機上那條列是橫向捲動的，排在後面的模組使用者可能從來沒發現。
//
// 選單由**既有的 nav 連結生成**，不是另外寫一份：七頁的導覽列已經有測試守著
// 彼此一致，再手寫一份選單就等於多一個會走鐘的來源。描述放在這裡集中管理。

const MODULE_INFO = {
  "index.html": { group: "市場狀態", desc: "六大類別計分、三道閘門、部位建議" },
    "macro.html": { group: "市場狀態", desc: "26 條 FRED 總經指標、發布日誌與 AI 摘要" },
  "rotation.html": { group: "類股", desc: "相對強弱象限與資金流（1 日～3 月）" },
  "trend.html": { group: "類股", desc: "主要 ETF 的均線與趨勢（1 月～3 年）" },
  "valuation.html": { group: "個股", desc: "分析師共識目標價與市價的差距" },
  "backtest.html": { group: "策略", desc: "近十五年 QQQ／TQQQ／SGOV 實測模組一策略" },
  "tail.html": { group: "策略", desc: "尾部脆弱度雙閘門（影子運行，門檻未校準）" },
};
const GROUP_ORDER = ["市場狀態", "類股", "個股", "策略"];

function buildMenu() {
  const nav = document.querySelector(".module-nav");
  if (!nav || document.getElementById("module-menu")) return;

  const links = [...nav.querySelectorAll("a")].map((a) => ({
    href: a.getAttribute("href"),
    full: a.querySelector(".nav-full")?.textContent.trim() || a.textContent.trim(),
    short: a.querySelector(".nav-short")?.textContent.trim() || "",
    current: a.classList.contains("active"),
  }));
  const here = links.find((l) => l.current);

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "menu-toggle";
  toggle.setAttribute("aria-expanded", "false");
  toggle.setAttribute("aria-controls", "module-menu");
  toggle.innerHTML = `<span class="menu-icon" aria-hidden="true"></span>`
    + `<span class="menu-toggle-label">全部模組</span>`
    + `<span class="menu-toggle-current">${here ? here.short || here.full : ""}</span>`;

  const panel = document.createElement("div");
  panel.id = "module-menu";
  panel.className = "module-menu";
  panel.hidden = true;
  panel.innerHTML = GROUP_ORDER.map((g) => {
    const items = links.filter((l) => (MODULE_INFO[l.href] || {}).group === g);
    if (!items.length) return "";
    return `<div class="menu-group"><h3>${g}</h3>` + items.map((l) => `
      <a href="${l.href}"${l.current ? ' aria-current="page"' : ""}>
        <span class="menu-name">${l.full}</span>
        <span class="menu-desc">${(MODULE_INFO[l.href] || {}).desc || ""}</span>
      </a>`).join("") + "</div>";
  }).join("");

  nav.parentNode.insertBefore(toggle, nav);
  nav.parentNode.insertBefore(panel, nav.nextSibling);

  const close = () => {
    panel.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
  };
  const open = () => {
    panel.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
    panel.querySelector("a")?.focus();
  };
  toggle.addEventListener("click", () =>
    (toggle.getAttribute("aria-expanded") === "true" ? close() : open()));
  // Esc 關閉、點外面關閉：開著的浮層一定要有這兩條退路，
  // 否則在手機上會變成「只能按那顆按鈕才回得去」
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  document.addEventListener("click", (e) => {
    if (!panel.hidden && !panel.contains(e.target) && !toggle.contains(e.target)) close();
  });
  window.addEventListener("resize", close);
}

  function start() {
    buildMenu();
    apply();
    window.addEventListener("resize", schedule);
    // 表格是抓完資料才填的，所以要在內容變動後重算——只掛在 tbody 上，
    // 避免每次無關的 DOM 變動都觸發一次量測
    const mo = new MutationObserver(schedule);
    document.querySelectorAll("table tbody").forEach((tb) =>
      mo.observe(tb, { childList: true }));
    // 篩選器改變欄位可見性時也會影響寬度
    document.querySelectorAll(".filter-bar select, .filter-bar input")
      .forEach((el) => el.addEventListener("change", schedule));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
