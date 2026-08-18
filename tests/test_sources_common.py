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


def test_looks_unreliable_flags_trial_packs_multi_size_lists_and_box_multipliers():
    assert _common.looks_unreliable("2片/包 M-2XL(體驗包)") is True
    assert _common.looks_unreliable("M-XXL(單包入48/44/40/36片)") is True
    assert _common.looks_unreliable("奢寵幫 M 38片入*3包入(箱購)") is True
    assert _common.looks_unreliable("滿意寶寶 極上呵護紙尿褲 M號 62片") is False


def test_looks_like_a_plausible_pack_price_rejects_out_of_range_values():
    assert _common.looks_like_a_plausible_pack_price(500.0) is True
    assert _common.looks_like_a_plausible_pack_price(0.005) is False
    assert _common.looks_like_a_plausible_pack_price(5_000_000.0) is False
