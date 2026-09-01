#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import collect_iris_siroca as src

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "review" / "candidates.json"
SOURCES = ROOT / "review" / "discovered_sources.json"
METHOD = src.METHOD

IRIS_BLOCKED = [
    ("洗濯機・洗濯乾燥機", "https://www.irisohyama.co.jp/support/faq/categori.php?ID=44"),
    ("ルームエアコン", "https://www.irisohyama.co.jp/support/faq/categori.php?ID=34"),
    ("炊飯器", "https://www.irisohyama.co.jp/support/faq/categori.php?ID=7&detail=1"),
    ("シェフドラム", "https://www.irisohyama.co.jp/support/faq/detail.php?ID=7098"),
]


def main() -> None:
    existing = json.loads(CANDIDATES.read_text(encoding="utf-8")) if CANDIDATES.exists() else []
    source_rows = json.loads(SOURCES.read_text(encoding="utf-8")) if SOURCES.exists() else []
    existing = [x for x in existing if x.get("extraction_method") != METHOD]
    source_rows = [x for x in source_rows if x.get("collector") != METHOD]

    added = []
    for appliance, url in IRIS_BLOCKED:
        source_rows.append({
            "manufacturer": "アイリスオーヤマ",
            "appliance": appliance,
            "url": url,
            "status": "blocked_http_403",
            "detail": "Public official support page blocks GitHub Actions fetches; no bypass attempted.",
            "candidate_count": 0,
            "collector": METHOD,
        })

    try:
        rows, sources = src.collect_siroca()
    except Exception as exc:
        rows, sources = [], [{
            "manufacturer": "シロカ",
            "appliance": "*",
            "url": "",
            "status": "collector_error",
            "detail": f"{type(exc).__name__}: {exc}"[:240],
            "candidate_count": 0,
            "collector": METHOD,
        }]
    added.extend(rows)
    source_rows.extend(sources)

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

    print("Iris Ohyama: skipped because official support blocks GitHub Actions with HTTP 403")
    print(f"Siroca candidates added: {len(added)}")
    print(f"candidate records after Iris/Siroca safe step: {len(result)}")


if __name__ == "__main__":
    main()
