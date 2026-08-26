"""`diaper_monitor.sources._common` 共用解析邏輯的測試。

抽出來給 pchome.py 跟 shopee.py 共用，這裡直接測函式本身，
個別來源的測試（test_pchome_source.py、test_shopee_source.py）
則驗證這些函式有被正確接進各自的 fetch_brand 流程。
"""
from __future__ import annotations

from diaper_monitor.sources import _common


def test_first_tries_keys_in_order():
    assert _common.first({"b": 2}, "a", "b") == 2


def test_first_skips_none_and_empty_string():
    assert _common.first({"a": None, "b": "", "c": 3}, "a", "b", "c") == 3


def test_first_returns_none_when_nothing_matches():
    assert _common.first({"x": 1}, "a", "b") is None


def test_extract_piece_count_finds_the_number_before_pian():
    assert _common.extract_piece_count("滿意寶寶 M 62片") == 62


def test_extract_piece_count_returns_none_without_a_match():
    assert _common.extract_piece_count("滿意寶寶 M 箱購組合") is None


def test_looks_like_size_m_requires_a_standalone_m():
    assert _common.looks_like_size_m("滿意寶寶 M 62片") is True
    assert _common.looks_like_size_m("某商品 XML 認證 62片") is False
    assert _common.looks_like_size_m("滿意寶寶 L 54片") is False


def test_looks_unreliable_flags_trial_packs_and_multi_size_lists():
    """箱購**刻意不再列入**：它跟另外兩種不一樣，38 片、3 包兩個數字都寫在
    標題上，乘起來是算得出來的（見 extract_piece_count 與下面那則測試）。
    另外兩種則是「哪個數字對應 M 號」根本不確定，只能跳過。"""
    assert _common.looks_unreliable("2片/包 M-2XL(體驗包)") is True
    assert _common.looks_unreliable("M-XXL(單包入48/44/40/36片)") is True
    assert _common.looks_unreliable("奢寵幫 M 38片入*3包入(箱購)") is False
    assert _common.looks_unreliable("滿意寶寶 極上呵護紙尿褲 M號 62片") is False


def test_box_purchases_are_multiplied_into_a_total_piece_count():
    assert _common.extract_piece_count("奢寵幫 M 38片入*3包入(箱購)") == 114
    assert _common.extract_piece_count("零觸感瞬吸 M 48片 x2包") == 96
    # 沒寫倍數的就是單包，不能憑空乘
    assert _common.extract_piece_count("滿意寶寶 極上呵護紙尿褲 M號 62片") == 62


def test_the_unit_price_guard_catches_a_piece_count_that_is_wrong_either_way():
    """這一關防的是片數算錯，不是售價異常——一包 1520 元完全合理，
    但若少算三倍，單片價會從 13 變成 40，而 1520 這個數字本身毫無異狀。
    會誤導「顯著下跌」判定的是單片價，所以要在那個數字上再設一道。"""
    assert _common.looks_like_a_plausible_unit_price(1520 / 114) is True   # 13.33，正確
    assert _common.looks_like_a_plausible_unit_price(1520 / 38) is False   # 40.00，忘了乘
    assert _common.looks_like_a_plausible_unit_price(1520 / 1140) is False  # 1.33，乘過頭


def test_screen_listing_says_which_gate_rejected_a_listing():
    """三個爬蟲原本各有一個 skipped 總數，日誌只能說「147 個候選、0 個通過」，
    說不出是哪一關擋的——momo 連續十天回 0 筆，光看日誌完全無從查起。"""
    assert _common.screen_listing("滿意寶寶 M號 62片", 509.0) == (62, None)
    assert _common.screen_listing("滿意寶寶 L 54片", 509.0)[1] == "判定不是 M 號"
    assert _common.screen_listing("滿意寶寶 M號 箱購組合", 509.0)[1] == "標題找不到片數"
    assert _common.screen_listing("2片/包 M-2XL(體驗包)", 29.0)[1] == "標題不可靠"
    assert _common.screen_listing("", 509.0)[1] == "沒有標題"
    assert _common.screen_listing("滿意寶寶 M號 62片", None)[1] == "找不到價格"
    assert _common.screen_listing("滿意寶寶 M號 62片", 5_000_000.0)[1] == "售價不合理"
    assert _common.screen_listing("滿意寶寶 M號 62片", 3000.0)[1] == "單片價不合理"


def test_looks_like_a_plausible_pack_price_rejects_out_of_range_values():
    assert _common.looks_like_a_plausible_pack_price(500.0) is True
    assert _common.looks_like_a_plausible_pack_price(0.005) is False
    assert _common.looks_like_a_plausible_pack_price(5_000_000.0) is False
