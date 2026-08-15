"""七頁共用的介面規則。

這些不是「好不好看」的主觀判斷，是**量測出來的問題**對應的修正：

  手機導覽 144px 且最後一項被切掉  ← 七個完整名稱各分到約 40px，
                                     「模組一：市場流動性監控」被拆成一行一個字
  捲到中段看不到欄位標題            ← 估值表約 20,000px 高，滿螢幕都是沒有
                                     標題的數字，175.58 是收盤價還是目標價看不出來

兩者都會隨著日後新增模組或欄位而復發，所以寫成測試。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[1] / "docs"
PAGES = ["index.html", "valuation.html", "backtest.html", "rotation.html",
         "macro.html", "tail.html", "trend.html"]


def _nav(html: str) -> str:
    start = html.index('<nav class="module-nav">')
    return html[start:html.index("</nav>", start)]


@pytest.mark.parametrize("page", PAGES)
def test_every_nav_link_has_both_a_full_and_a_short_label(page):
    """手機用短名、桌機用全名。少一個 nav-short，那一項在手機上會塌回
    一行一個字，而且只有那一項壞掉——最難發現的樣子。"""
    nav = _nav((DOCS / page).read_text(encoding="utf-8"))
    links = re.findall(r"<a [^>]*>(.*?)</a>", nav, re.S)
    assert len(links) == len(PAGES)
    for inner in links:
        assert 'class="nav-full"' in inner, page
        assert 'class="nav-short"' in inner, page


@pytest.mark.parametrize("page", PAGES)
def test_short_labels_are_actually_short(page):
    """短名要能在 390px 寬裡排成一行。放一個 12 字的「短名」等於沒改。"""
    nav = _nav((DOCS / page).read_text(encoding="utf-8"))
    for short in re.findall(r'class="nav-short">([^<]+)<', nav):
        assert len(short.strip()) <= 6, f"{page}: 「{short}」太長"


@pytest.mark.parametrize("page", PAGES)
def test_every_page_loads_the_shared_ui_script(page):
    """表頭跟隨是共用行為。漏載的那一頁不會報錯，只是安靜地沒有這個功能。"""
    html = (DOCS / page).read_text(encoding="utf-8")
    assert '<script src="ui.js"></script>' in html, page


def test_the_nav_is_sticky_and_paints_its_own_background():
    """頁面高 3,600～20,000px，導覽若跟著捲走，換模組就得先捲回最上面。
    固定住就必須自己畫底色，否則內容會從底下透出來。"""
    css = (DOCS / "style.css").read_text(encoding="utf-8")
    block = re.search(r"\.module-nav \{(.*?)\}", css, re.S).group(1)
    assert "position: sticky" in block
    assert "background:" in block
    assert "z-index:" in block


def test_mobile_swaps_to_short_labels_in_a_single_row():
    css = (DOCS / "style.css").read_text(encoding="utf-8")
    mobile = css[css.index("@media (max-width: 720px)"):]
    assert ".module-nav .nav-full { display: none; }" in mobile
    assert "nav-short { display: inline; }" in mobile
    assert "white-space: nowrap" in mobile


def test_sticky_headers_are_opt_in_via_a_measured_class():
    """不能無條件開啟：`overflow-x: auto` 會讓另一軸也變成捲動盒，
    把 sticky 綁在裡面（實測捲動 4000px 後表頭 top = −2745）。
    因此要嘛橫向捲動、要嘛表頭跟隨，由 ui.js 量測後決定。"""
    css = (DOCS / "style.css").read_text(encoding="utf-8")
    assert ".table-scroll.sticky-head { overflow: visible; }" in css
    assert "position: sticky" in css[css.index(".table-scroll.sticky-head thead th"):]


def test_the_sticky_header_uses_a_shadow_not_a_border():
    """border-collapse 之下，儲存格的 border 不會跟著 sticky 元素移動——
    捲起來會看到一條留在原地的線。"""
    css = (DOCS / "style.css").read_text(encoding="utf-8")
    block = css[css.index(".table-scroll.sticky-head thead th"):]
    block = block[:block.index("}")]
    assert "box-shadow" in block
    assert re.search(r"\bborder-bottom\s*:", block) is None


def test_ui_script_measures_rather_than_assuming_a_breakpoint():
    """用斷點猜「這個寬度應該放得下」，遇到欄位增減就會失準。
    量 scrollWidth 是準的，而且會自己跟著 resize 修正。"""
    js = (DOCS / "ui.js").read_text(encoding="utf-8")
    assert "scrollWidth" in js and "clientWidth" in js
    assert "resize" in js
    # 表格是抓完資料才填的，只在載入時量一次會量到空表
    assert "MutationObserver" in js


# --- 模組選單 ---------------------------------------------------------------
#
# 標籤列適合「快速切換 + 看出我在哪一頁」，但它說不出每一頁在做什麼，
# 而且手機上是橫向捲動的——排在後面的模組可能從來沒被發現。

def _ui_js() -> str:
    return (DOCS / "ui.js").read_text(encoding="utf-8")


def test_every_page_has_a_menu_entry_with_a_group_and_a_description():
    """漏一頁不會報錯，那一項只是安靜地從選單消失——而標籤列上還在，
    於是兩個地方顯示的模組數量不一致。"""
    js = _ui_js()
    block = js[js.index("const MODULE_INFO"):js.index("const GROUP_ORDER")]
    for page in PAGES:
        assert f'"{page}"' in block, f"選單缺少 {page}"
        entry = block[block.index(f'"{page}"'):]
        entry = entry[:entry.index("}")]
        assert "group:" in entry and "desc:" in entry, page
        desc = re.search(r'desc:\s*"([^"]+)"', entry).group(1)
        assert len(desc.strip()) >= 8, f"{page} 的說明太短，等於沒說"


def test_menu_groups_are_all_declared_in_the_order_list():
    """用到但沒宣告的分組會整組不顯示——那一組的模組就消失了。"""
    js = _ui_js()
    info = js[js.index("const MODULE_INFO"):js.index("const GROUP_ORDER")]
    order = re.search(r"const GROUP_ORDER = \[(.*?)\]", js, re.S).group(1)
    declared = set(re.findall(r'"([^"]+)"', order))
    used = set(re.findall(r'group:\s*"([^"]+)"', info))
    assert used <= declared, f"未宣告的分組：{used - declared}"
    assert declared == used, f"宣告了卻沒人用的分組：{declared - used}"


def test_the_menu_is_generated_from_the_nav_not_a_second_hardcoded_list():
    """七頁的導覽列已經有測試守著彼此一致。再手寫一份選單連結，
    就等於多一個會走鐘的來源。"""
    js = _ui_js()
    fn = js[js.index("function buildMenu"):]
    assert 'querySelectorAll("a")' in fn or "querySelectorAll('a')" in fn
    assert ".module-nav" in fn
    assert "nav-full" in fn


def test_the_menu_toggle_is_accessible():
    """展開式浮層的最低要求：狀態要能被輔助技術讀到，且要有退路。"""
    js = _ui_js()
    fn = js[js.index("function buildMenu"):]
    assert "aria-expanded" in fn
    assert "aria-controls" in fn
    assert "aria-current" in fn          # 目前頁面要標出來
    assert "Escape" in fn                # 鍵盤退路
    assert "contains(e.target)" in fn    # 點外面關閉


def test_the_menu_degrades_without_javascript():
    """按鈕是 JS 建立的，所以 CSS 預設不能把標籤列藏起來——
    否則停用 JS 的人會完全沒有導覽。手機規則必須以按鈕存在為前提。"""
    css = (DOCS / "style.css").read_text(encoding="utf-8")
    mobile = css[css.index("@media (max-width: 720px)"):]
    assert ".menu-toggle ~ .module-nav { display: none; }" in mobile
    assert re.search(r"^\s*\.module-nav \{ display: none; \}", mobile, re.M) is None


def test_the_menu_panel_paints_a_background_and_sits_above_content():
    css = (DOCS / "style.css").read_text(encoding="utf-8")
    block = css[css.index(".module-menu {"):]
    block = block[:block.index("}")]
    assert "background:" in block and "z-index:" in block
