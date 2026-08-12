#!/usr/bin/env python3
"""探測新聞摘要的替代來源，回報每個候選網址的實際結果。

    python3 scripts/probe_sources.py -v

**只在有對外網路的環境（GitHub Actions）跑得出有意義的結果。**開發環境沒有
對外網路，在那裡跑只會得到一整排 error，那不是證據。

這支程式**不會修改任何設定**。它產出報告，由人看過真實結果之後，才決定
要不要把通過的網址寫進 `macro_monitor/news_ai.RELEASE_SOURCES`。
自動採用等於把「沒驗證」換個地方藏起來。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macro_monitor import source_probe  # noqa: E402
from macro_monitor.config import DATA_ROOT  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", default=DATA_ROOT)
    ap.add_argument("--only", default=None, help="只探測這幾條（逗號分隔的 FRED 代碼）")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cands = source_probe.CANDIDATES
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        cands = {k: v for k, v in cands.items() if k in wanted}
        if not cands:
            print(f"沒有這些指標的候選來源：{sorted(wanted)}", file=sys.stderr)
            return 2

    results = source_probe.probe_all(cands)

    out_dir = Path(args.data_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "source_probe.md").write_text(source_probe.to_markdown(results), encoding="utf-8")
    (out_dir / "source_probe.json").write_text(
        json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2), encoding="utf-8")

    usable = [r for r in results if r.usable]
    by_verdict: dict = {}
    for r in results:
        by_verdict[r.verdict] = by_verdict.get(r.verdict, 0) + 1

    print(f"探測 {len(results)} 個網址，可用 {len(usable)} 個")
    for verdict, n in sorted(by_verdict.items(), key=lambda x: -x[1]):
        print(f"  {verdict:<12} {n}")
    for r in usable:
        print(f"  ✅ {r.fred_id:<14} [{r.kind}] {r.url}\n     {r.reason}")
    print(f"報告：{out_dir / 'source_probe.md'}")
    # 一個都不可用不是執行失敗——那也是一個結論，而且要能被看見。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
