#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import collect_sharp_categories as sharp

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "review" / "candidates.json"
SOURCES = ROOT / "review" / "discovered_sources.json"
METHOD = sharp.METHOD

SERIES = [
    ("加湿空気清浄機（KI-GX/GSシリーズ）", "https://cs.sharp.co.jp/trouble_check/div/air_purifier/navi_ki_gx_gs/ki_diag04_01.html", "/trouble_check/div/air_purifier/navi_ki_gx_gs/", 60),
    ("加湿空気清浄機（KI-NS50）", "https://cs.sharp.co.jp/trouble_check/div/air_purifier/navi_kins50/ki_diag04.html", "/trouble_check/div/air_purifier/navi_kins50/", 50),
    ("加湿空気清浄機（KI-WF/FXシリーズ）", "https://cs.sharp.co.jp/trouble_check/div/air_purifier/navi_ki_wf_fx/ki_diag01_01.html", "/trouble_check/div/air_purifier/navi_ki_wf_fx/", 60),
    ("加湿空気清浄機（KC-Fシリーズ）", "https://cs.sharp.co.jp/trouble_check/div/air_purifier/navi_kcf/kc_diag01_01.html", "/trouble_check/div/air_purifier/navi_kcf/", 50),
    ("加湿空気清浄機（KC-GD70シリーズ）", "https://cs.sharp.co.jp/trouble_check/div/air_purifier/navi_kcgd70/kc_diag01.html", "/trouble_check/div/air_purifier/navi_kcgd70/", 50),
    ("加湿空気清浄機（KI-EXシリーズ）", "https://cs.sharp.co.jp/trouble_check/div/air_purifier/navi_kiex/ki_diag01_01.html", "/trouble_check/div/air_purifier/navi_kiex/", 50),
    ("加湿空気清浄機（KI-LX/NX75シリーズ）", "https://cs.sharp.co.jp/trouble_check/div/air_purifier/navi_kilx_nx75/ki_diag04_01.html", "/trouble_check/div/air_purifier/navi_kilx_nx75/", 60),
    ("加湿空気清浄機（KC-HD70シリーズ）", "https://cs.sharp.co.jp/trouble_check/div/air_purifier/navi_kchd70/kc_diag01.html", "/trouble_check/div/air_purifier/navi_kchd70/", 50),
    ("加湿空気清浄機（KC-Bシリーズ）", "https://cs.sharp.co.jp/trouble_check/div/air_purifier/navi_kcb/kc_diag01_01.html", "/trouble_check/div/air_purifier/navi_kcb/", 50),
    ("加湿空気清浄機（KI-BXシリーズ）", "https://cs.sharp.co.jp/trouble_check/div/air_purifier/navi_kibx/ki_diag01_02.html", "/trouble_check/div/air_purifier/navi_kibx/", 50),
]


def main() -> None:
    existing = json.loads(CANDIDATES.read_text(encoding="utf-8")) if CANDIDATES.exists() else []
    source_rows = json.loads(SOURCES.read_text(encoding="utf-8")) if SOURCES.exists() else []

    # Main Sharp collector rebuilds all dedicated:sharp-category records immediately before this step.
    # Keep those records and append series-scoped air-purifier results.
    added = []
    for appliance, root, prefix, max_pages in SERIES:
        rows, sources = sharp.collect_category(appliance, root, prefix, max_pages)
        added.extend(rows)
        source_rows.extend(sources)
        print(f"Sharp {appliance}: {len(rows)} candidates")

    rank = {"low": 1, "medium": 2, "high": 3}
    merged = {}
    for item in [*existing, *added]:
        key = (item.get("manufacturer"), item.get("appliance"), item.get("code"), item.get("detail_url") or item.get("source"))
        old = merged.get(key)
        if old is None or rank.get(item.get("confidence"), 0) > rank.get(old.get("confidence"), 0):
            merged[key] = item
    result = sorted(merged.values(), key=lambda x: (
        bool(x.get("already_published")), str(x.get("manufacturer", "")), str(x.get("appliance", "")),
        str(x.get("code", "")), str(x.get("detail_url") or x.get("source") or ""),
    ))
    source_rows.sort(key=lambda x: (str(x.get("manufacturer", "")), str(x.get("appliance", "")), str(x.get("url", ""))))
    CANDIDATES.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SOURCES.write_text(json.dumps(source_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    high = sum(x.get("confidence") == "high" for x in added)
    print(f"Sharp air purifier series candidates added: {len(added)}")
    print(f"Sharp air purifier series high-confidence candidates: {high}")
    print(f"candidate records after Sharp air purifier series: {len(result)}")


if __name__ == "__main__":
    main()
