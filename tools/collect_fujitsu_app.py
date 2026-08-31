#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

from collect_candidates import clean_context, fetch_page, normalize_code

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "review" / "candidates.json"
SOURCES = ROOT / "review" / "discovered_sources.json"
SOURCE_URL = "https://www.fujitsu-general.com/jp/support/faq/nocria-app/error-code/ja/index.html"
MANUFACTURER = "富士通ゼネラル"
APPLIANCE = "ノクリアアプリ"
METHOD = "dedicated:fujitsu-app"

GROUP_RE = re.compile(
    r"エラーコード\s*[:：]\s*([0-9０-９、,\s]+?)\s*原因\s*(.*?)\s*確認内容\s*(.*?)(?=\s*(?:\d+[.．]\s*)?エラーコード\s*[:：]|$)",
    re.IGNORECASE,
)


def parse_codes(raw: str) -> list[str]:
    values = []
    for token in re.split(r"[、,\s]+", raw):
        code = normalize_code(token)
        if re.fullmatch(r"\d{4}", code) and code not in values:
            values.append(code)
    return values


def clean_summary(value: str) -> str:
    text = clean_context(value)
    text = re.sub(r"^※\s*", "", text)
    return text[:260]


def clean_actions(value: str) -> list[str]:
    text = clean_context(value)
    stop_markers = ("Scroll", "エラーコード表", "ページの先頭へ")
    for marker in stop_markers:
        pos = text.find(marker)
        if pos >= 0:
            text = text[:pos]
    chunks = [clean_context(x) for x in re.split(r"(?<=[。！？!?])\s*", text) if clean_context(x)]
    useful = ("確認", "操作", "設定", "接続", "再起動", "有効", "無効", "お問い合わせ", "待", "近付", "減ら", "押", "参照", "更新", "入力", "選択", "許可")
    result = []
    for chunk in chunks:
        chunk = re.sub(r"^\d+(?:-\d+)?[.．]\s*", "", chunk).strip("・※ ")
        if not 6 <= len(chunk) <= 180:
            continue
        if not any(word in chunk for word in useful):
            continue
        if chunk not in result:
            result.append(chunk)
        if len(result) >= 5:
            break
    return result or ["富士通ゼネラル公式のエラーコード表で確認内容を確認する"]


def main() -> None:
    existing = json.loads(CANDIDATES.read_text(encoding="utf-8")) if CANDIDATES.exists() else []
    source_rows = json.loads(SOURCES.read_text(encoding="utf-8")) if SOURCES.exists() else []

    try:
        parser, error = fetch_page(SOURCE_URL)
    except Exception as exc:
        parser, error = None, f"{type(exc).__name__}: {exc}"
    if error or parser is None:
        raise RuntimeError(f"Fujitsu app official page fetch failed: {error}")

    text = clean_context(parser.text)
    added = []
    groups = 0
    for match in GROUP_RE.finditer(text):
        codes = parse_codes(match.group(1))
        summary = clean_summary(match.group(2))
        actions = clean_actions(match.group(3))
        if not codes or not 5 <= len(summary) <= 260:
            continue
        groups += 1
        for code in codes:
            added.append({
                "manufacturer": MANUFACTURER,
                "appliance": APPLIANCE,
                "code": code,
                "source": SOURCE_URL,
                "page_title": parser.title,
                "evidence": clean_context(f"エラーコード {code} 原因 {summary} 確認内容 {' '.join(actions)}")[:700],
                "confidence": "high",
                "status": "needs_review",
                "extraction_method": METHOD,
                "summary_hint": summary,
                "action_hint": " ".join(actions),
                "already_published": False,
            })

    if len(added) < 50:
        raise RuntimeError(f"Fujitsu app safety threshold failed: only {len(added)} codes parsed")

    rank = {"low": 1, "medium": 2, "high": 3}
    merged = {}
    for item in [*existing, *added]:
        key = (
            item.get("manufacturer"),
            item.get("appliance"),
            item.get("code"),
            item.get("detail_url") or item.get("source"),
        )
        old = merged.get(key)
        if old is None or rank.get(item.get("confidence"), 0) > rank.get(old.get("confidence"), 0):
            merged[key] = item

    result = sorted(
        merged.values(),
        key=lambda x: (
            bool(x.get("already_published")),
            str(x.get("manufacturer", "")),
            str(x.get("appliance", "")),
            str(x.get("code", "")),
            str(x.get("detail_url") or x.get("source") or ""),
        ),
    )
    source_rows.append({
        "manufacturer": MANUFACTURER,
        "appliance": APPLIANCE,
        "url": SOURCE_URL,
        "title": parser.title,
        "status": "ok",
        "candidate_count": len(added),
        "collector": "dedicated-extra",
        "structured_groups": groups,
    })
    source_rows.sort(key=lambda x: (str(x.get("manufacturer", "")), str(x.get("url", ""))))
    CANDIDATES.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SOURCES.write_text(json.dumps(source_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Fujitsu Nocria app structured groups: {groups}")
    print(f"Fujitsu Nocria app dedicated candidates added: {len(added)}")
    print(f"candidate records after Fujitsu app: {len(result)}")


if __name__ == "__main__":
    main()
