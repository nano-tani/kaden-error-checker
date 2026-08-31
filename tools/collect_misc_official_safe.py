#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import collect_misc_official as misc

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "review" / "candidates.json"
SOURCES = ROOT / "review" / "discovered_sources.json"
METHOD = misc.METHOD


def main() -> None:
    existing = json.loads(CANDIDATES.read_text(encoding="utf-8")) if CANDIDATES.exists() else []
    source_rows = json.loads(SOURCES.read_text(encoding="utf-8")) if SOURCES.exists() else []
    existing = [x for x in existing if x.get("extraction_method") != METHOD]
    source_rows = [x for x in source_rows if x.get("collector") != METHOD]

    added = []
    for label, fn in (("Toyotomi", misc.collect_toyotomi), ("Dainichi", misc.collect_dainichi)):
        try:
            rows, sources = fn()
        except Exception as exc:
            rows, sources = [], [{
                "manufacturer": label,
                "appliance": "*",
                "url": "",
                "status": "collector_error",
                "detail": f"{type(exc).__name__}: {exc}"[:240],
                "candidate_count": 0,
                "collector": METHOD,
            }]
        added.extend(rows)
        source_rows.extend(sources)

    # Tiger public support currently returns HTTP 403 to GitHub Actions. Keep the source visible
    # as blocked instead of failing the whole multi-manufacturer refresh.
    for appliance, section_url in misc.TIGER_SECTIONS:
        source_rows.append({
            "manufacturer": "タイガー魔法瓶",
            "appliance": appliance,
            "url": section_url,
            "status": "blocked_http_403",
            "detail": "Public support page is visible in browsers/search but blocks GitHub Actions fetches.",
            "candidate_count": 0,
            "collector": METHOD,
        })

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
    print(f"safe misc official candidates added: {len(added)}")
    print(f"safe misc official candidates by manufacturer: {json.dumps(by_mfr, ensure_ascii=False)}")
    print("Tiger: skipped because official support blocks GitHub Actions with HTTP 403")
    print(f"candidate records after safe misc official: {len(result)}")


if __name__ == "__main__":
    main()
