#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import collect_eufy_delonghi as src

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "review" / "candidates.json"
SOURCES = ROOT / "review" / "discovered_sources.json"
METHOD = src.METHOD


def main() -> None:
    existing = json.loads(CANDIDATES.read_text(encoding="utf-8")) if CANDIDATES.exists() else []
    source_rows = json.loads(SOURCES.read_text(encoding="utf-8")) if SOURCES.exists() else []
    existing = [x for x in existing if x.get("extraction_method") != METHOD]
    source_rows = [x for x in source_rows if x.get("collector") != METHOD]

    added = []
    for label, fn in (("Anker/Eufy", src.collect_eufy), ("デロンギ", src.collect_delonghi)):
        try:
            rows, source = fn()
        except Exception as exc:
            rows, source = [], {
                "manufacturer": label,
                "appliance": "*",
                "url": "",
                "status": "collector_error",
                "detail": f"{type(exc).__name__}: {exc}"[:240],
                "candidate_count": 0,
                "collector": METHOD,
            }
        added.extend(rows)
        source_rows.append(source)

    rank = {"low": 1, "medium": 2, "high": 3}
    merged = {}
    for item in [*existing, *added]:
        key = (
            item.get("manufacturer"), item.get("appliance"), item.get("code"),
            item.get("detail_url") or item.get("source"),
        )
        old = merged.get(key)
        if old is None or rank.get(item.get("confidence"), 0) > rank.get(old.get("confidence"), 0):
            merged[key] = item
    result = sorted(merged.values(), key=lambda x: (
        bool(x.get("already_published")), str(x.get("manufacturer", "")),
        str(x.get("appliance", "")), str(x.get("code", "")),
        str(x.get("detail_url") or x.get("source") or ""),
    ))
    source_rows.sort(key=lambda x: (
        str(x.get("manufacturer", "")), str(x.get("appliance", "")), str(x.get("url", ""))
    ))
    CANDIDATES.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SOURCES.write_text(json.dumps(source_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    by_mfr = {}
    for row in added:
        by_mfr[row["manufacturer"]] = by_mfr.get(row["manufacturer"], 0) + 1
    print(f"safe Eufy/DeLonghi candidates added: {len(added)}")
    print(f"safe Eufy/DeLonghi by manufacturer: {json.dumps(by_mfr, ensure_ascii=False)}")
    print(f"candidate records after safe Eufy/DeLonghi: {len(result)}")


if __name__ == "__main__":
    main()
