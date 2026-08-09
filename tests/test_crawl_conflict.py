"""爬取進度檔的記錄層級合併。

2026-08 一批 1500 檔的爬取成果被 `git pull --rebase` 的衝突整批丟掉。
這些檔案都是以 ticker 為鍵的記錄集合，衝突有唯一正確解——取聯集、
同一檔取較新的那筆。這裡守的就是「不論衝突怎麼撞，一筆都不能少」。
"""
from __future__ import annotations

import csv
import io
import json

from scripts.resolve_crawl_conflict import merge_jsonl, merge_ledger


def _rows(text):
    return {r["ticker"]: r for r in csv.DictReader(io.StringIO(text))}


LEDGER_HEADER = "ticker,status,attempts,last_attempt_at,last_ok_at,last_error\n"


def test_jsonl_merge_keeps_records_from_both_sides():
    ours = ('{"ticker":"AAA","as_of":"2026-08-03T00:00:00Z"}\n'
            '{"ticker":"CCC","as_of":"2026-08-03T00:00:00Z"}\n')
    theirs = ('{"ticker":"AAA","as_of":"2026-08-01T00:00:00Z"}\n'
              '{"ticker":"BBB","as_of":"2026-08-02T00:00:00Z"}\n')

    merged = {json.loads(l)["ticker"]: json.loads(l)
              for l in merge_jsonl(ours, theirs).splitlines()}
    assert set(merged) == {"AAA", "BBB", "CCC"}      # 一筆都不能少
    assert merged["AAA"]["as_of"] == "2026-08-03T00:00:00Z"   # 同一檔取較新的


def test_jsonl_merge_is_sorted_and_one_record_per_line():
    ours = '{"ticker":"ZZZ","as_of":"1"}\n{"ticker":"AAA","as_of":"1"}\n'
    out = merge_jsonl(ours, "").splitlines()
    assert [json.loads(l)["ticker"] for l in out] == ["AAA", "ZZZ"]


def test_jsonl_merge_survives_a_corrupt_line():
    """毀損的單行不該讓整批合併失敗——那等於又把成果丟掉一次。"""
    ours = '{"ticker":"AAA","as_of":"1"}\n{not json\n{"ticker":"BBB","as_of":"1"}\n'
    merged = merge_jsonl(ours, "")
    assert len(merged.splitlines()) == 2


def test_jsonl_merge_handles_one_side_missing():
    """add/add 衝突時某一方可能根本不存在。"""
    theirs = '{"ticker":"BBB","as_of":"1"}\n'
    assert json.loads(merge_jsonl("", theirs).strip())["ticker"] == "BBB"


def test_ledger_merge_unions_tickers_and_takes_newer_attempt():
    ours = LEDGER_HEADER + "AAA,ok,2,2026-08-03T00:00:00Z,2026-08-03T00:00:00Z,\n" \
                           "CCC,ok,1,2026-08-03T00:00:00Z,2026-08-03T00:00:00Z,\n"
    theirs = LEDGER_HEADER + "AAA,failed,1,2026-08-01T00:00:00Z,,boom\n" \
                             "BBB,ok,1,2026-08-02T00:00:00Z,2026-08-02T00:00:00Z,\n"

    rows = _rows(merge_ledger(ours, theirs))
    assert set(rows) == {"AAA", "BBB", "CCC"}
    assert rows["AAA"]["status"] == "ok"          # 較新的那筆勝出
    assert rows["BBB"]["status"] == "ok"          # 另一邊的進度要保留


def test_ledger_merge_takes_the_larger_attempt_count():
    """兩邊都試過同一檔時，取較大值才不會低估——低估會讓它無限重試下去。"""
    ours = LEDGER_HEADER + "AAA,failed,1,2026-08-03T00:00:00Z,,e\n"
    theirs = LEDGER_HEADER + "AAA,failed,2,2026-08-01T00:00:00Z,,e\n"
    rows = _rows(merge_ledger(ours, theirs))
    assert rows["AAA"]["attempts"] == "2"


def test_ledger_merge_preserves_header_and_sorts():
    ours = LEDGER_HEADER + "ZZZ,ok,1,2026-08-03T00:00:00Z,2026-08-03T00:00:00Z,\n"
    theirs = LEDGER_HEADER + "AAA,ok,1,2026-08-01T00:00:00Z,2026-08-01T00:00:00Z,\n"
    out = merge_ledger(ours, theirs)
    assert out.splitlines()[0] == LEDGER_HEADER.strip()
    assert list(_rows(out)) == ["AAA", "ZZZ"]


def test_ledger_merge_handles_one_side_empty():
    ours = LEDGER_HEADER + "AAA,ok,1,2026-08-03T00:00:00Z,2026-08-03T00:00:00Z,\n"
    assert list(_rows(merge_ledger(ours, ""))) == ["AAA"]
