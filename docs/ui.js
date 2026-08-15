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

  function start() {
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
