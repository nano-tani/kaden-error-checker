#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

from collect_candidates import clean_context, fetch_page, normalize_code
from collect_category_tables import collect_from_table, fetch as fetch_table

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "review" / "candidates.json"
SOURCES = ROOT / "review" / "discovered_sources.json"
METHOD = "dedicated:haier-extra"

RICE_URL = "https://www.haier.com/jp/service-support/self-service/20200403_144512.shtml"
MICROWAVE_URL = "https://www.haier.com/jp/service-support/self-service/20200323_144503.shtml"
WINE_URL = "https://www.haier.com/jp/service-support/self-service/20241213_253884.shtml"


def candidate(appliance: str, code: str, summary: str, actions: list[str], source: str, title: str) -> dict:
    return {
        "manufacturer": "ハイアール",
        "appliance": appliance,
        "code": normalize_code(code),
        "source": source,
        "page_title": title,
        "evidence": clean_context(f"エラーコード {code} {summary} {' '.join(actions)}")[:700],
        "confidence": "high",
        "status": "needs_review",
        "extraction_method": METHOD,
        "summary_hint": clean_context(summary),
        "action_hint": clean_context(" ".join(actions)),
        "already_published": False,
    }


def collect_rice() -> tuple[list[dict], dict]:
    parser, error = fetch_table(RICE_URL)
    if error or parser is None:
        return [], {"manufacturer": "ハイアール", "appliance": "炊飯器", "url": RICE_URL, "status": "fetch_error", "detail": error, "candidate_count": 0, "collector": "dedicated:official-table"}
    rows = collect_from_table({
        "manufacturer": "ハイアール",
        "appliance": "炊飯器",
        "source_url": RICE_URL,
    }, parser)
    return rows, {"manufacturer": "ハイアール", "appliance": "炊飯器", "url": RICE_URL, "title": parser.title, "status": "ok", "candidate_count": len(rows), "collector": "dedicated:official-table", "table_count": len(parser.tables)}


def collect_microwave() -> tuple[list[dict], dict]:
    parser, error = fetch_page(MICROWAVE_URL)
    if error or parser is None:
        return [], {"manufacturer": "ハイアール", "appliance": "電子レンジ（一部ターンテーブル機種）", "url": MICROWAVE_URL, "status": "fetch_error", "detail": error, "candidate_count": 0, "collector": METHOD}
    text = clean_context(parser.text)
    pattern = re.compile(r"(E[0-9]{1,2})エラー[「\"]([^」\"]+)[」\"]\s*(.*?)(?=\s*・E[0-9]{1,2}エラー|\s*不具合が改善|$)", re.IGNORECASE)
    rows = []
    for code, meaning, tail in pattern.findall(text):
        action = clean_context(re.split(r"(?<=[。！？!?])\s*", clean_context(tail))[0])
        if not action:
            continue
        rows.append(candidate(
            "電子レンジ（一部ターンテーブル機種）",
            code,
            clean_context(meaning),
            [action],
            MICROWAVE_URL,
            parser.title,
        ))
    return rows, {"manufacturer": "ハイアール", "appliance": "電子レンジ（一部ターンテーブル機種）", "url": MICROWAVE_URL, "title": parser.title, "status": "ok", "candidate_count": len(rows), "collector": METHOD}


def collect_wine() -> tuple[list[dict], dict]:
    parser, error = fetch_page(WINE_URL)
    if error or parser is None:
        return [], {"manufacturer": "ハイアール", "appliance": "ワインセラー", "url": WINE_URL, "status": "fetch_error", "detail": error, "candidate_count": 0, "collector": METHOD}
    text = clean_context(parser.text)
    required = ("H1", "H2", "L2", "L3", "25℃以上", "0℃以下")
    if not all(token in text for token in required):
        return [], {"manufacturer": "ハイアール", "appliance": "ワインセラー", "url": WINE_URL, "title": parser.title, "status": "structure_changed", "candidate_count": 0, "collector": METHOD}
    hot_action = "ドアが閉まっているか、開閉が多くなっていないか確認し、いずれかのキーを押して表示を解除する"
    cold_action = "いずれかのキーを押して表示を解除し、庫内温度の状態を確認する"
    rows = [
        candidate("ワインセラー", "H1", "上室の庫内温度が25℃以上の状態が続いています。", [hot_action], WINE_URL, parser.title),
        candidate("ワインセラー", "H2", "下室の庫内温度が25℃以上の状態が続いています。", [hot_action], WINE_URL, parser.title),
        candidate("ワインセラー", "L2", "上室の庫内温度が0℃以下の状態が続いています。", [cold_action], WINE_URL, parser.title),
        candidate("ワインセラー", "L3", "下室の庫内温度が0℃以下の状態が続いています。", [cold_action], WINE_URL, parser.title),
    ]
    return rows, {"manufacturer": "ハイアール", "appliance": "ワインセラー", "url": WINE_URL, "title": parser.title, "status": "ok", "candidate_count": len(rows), "collector": METHOD}


def main() -> None:
    existing = json.loads(CANDIDATES.read_text(encoding="utf-8")) if CANDIDATES.exists() else []
    source_rows = json.loads(SOURCES.read_text(encoding="utf-8")) if SOURCES.exists() else []
    # These exact categories are rebuilt from official source on every run.
    existing = [x for x in existing if not (
        x.get("manufacturer") == "ハイアール"
        and x.get("appliance") in {"炊飯器", "電子レンジ（一部ターンテーブル機種）", "ワインセラー"}
        and x.get("extraction_method") in {METHOD, "dedicated:official-table"}
    )]
    source_rows = [x for x in source_rows if not (
        x.get("manufacturer") == "ハイアール"
        and x.get("appliance") in {"炊飯器", "電子レンジ（一部ターンテーブル機種）", "ワインセラー"}
        and x.get("collector") in {METHOD, "dedicated:official-table"}
    )]

    added = []
    for fn in (collect_rice, collect_microwave, collect_wine):
        rows, source_row = fn()
        added.extend(rows)
        source_rows.append(source_row)

    rank = {"low": 1, "medium": 2, "high": 3}
    merged = {}
    for item in [*existing, *added]:
        key = (item.get("manufacturer"), item.get("appliance"), item.get("code"), item.get("detail_url") or item.get("source"))
        old = merged.get(key)
        if old is None or rank.get(item.get("confidence"), 0) > rank.get(old.get("confidence"), 0):
            merged[key] = item
    result = sorted(merged.values(), key=lambda x: (bool(x.get("already_published")), str(x.get("manufacturer", "")), str(x.get("appliance", "")), str(x.get("code", "")), str(x.get("detail_url") or x.get("source") or "")))
    source_rows.sort(key=lambda x: (str(x.get("manufacturer", "")), str(x.get("appliance", "")), str(x.get("url", ""))))
    CANDIDATES.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SOURCES.write_text(json.dumps(source_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Haier additional category candidates added: {len(added)}")
    print(f"candidate records after Haier categories: {len(result)}")


if __name__ == "__main__":
    main()
