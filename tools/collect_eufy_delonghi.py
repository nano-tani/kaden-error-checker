#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

from collect_candidates import clean_context, fetch_page, normalize_code

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "review" / "candidates.json"
SOURCES = ROOT / "review" / "discovered_sources.json"
METHOD = "dedicated:eufy-delonghi"

EUFY_URL = "https://www.ankerjapan.com/blogs/faq/eufy-robovac-%E3%82%A8%E3%83%A9%E3%83%BC%E3%82%B3%E3%83%BC%E3%83%89%E3%81%AB%E3%81%A4%E3%81%84%E3%81%A6"
DELONGHI_URL = "https://www.delonghi.com/ja-jp/faqs/%E3%80%90%E3%83%9E%E3%83%AB%E3%83%81%E3%83%80%E3%82%A4%E3%83%8A%E3%83%9F%E3%83%83%E3%82%AF%E3%83%92%E3%83%BC%E3%82%BF%E3%83%BC%E3%80%91%EF%BC%88MDHAA%EF%BC%89FAQ%EF%BC%88%E3%82%88%E3%81%8F%E3%81%82%E3%82%8B%E3%81%94%E8%B3%AA%E5%95%8F%EF%BC%89/a/266541"


def clean(value: object) -> str:
    return clean_context(str(value or ""))


def sentence_chunks(text: str) -> list[str]:
    return [clean(x).strip("・•※ ") for x in re.split(r"(?<=[。！？!?])\s*", clean(text)) if clean(x)]


def make(manufacturer: str, appliance: str, code: str, source: str, title: str, summary: str, actions: list[str]) -> dict:
    code_n = normalize_code(code)
    return {
        "manufacturer": manufacturer,
        "appliance": appliance,
        "code": code_n,
        "source": source,
        "detail_url": source,
        "page_title": title,
        "evidence": clean(f"エラーコード {code_n} {summary} {' '.join(actions)}")[:850],
        "confidence": "high",
        "status": "needs_review",
        "extraction_method": METHOD,
        "summary_hint": clean(summary),
        "action_hint": clean(" ".join(actions)),
        "already_published": False,
    }


def collect_eufy() -> tuple[list[dict], dict]:
    page, error = fetch_page(EUFY_URL)
    if error or page is None:
        return [], {"manufacturer": "Anker/Eufy", "appliance": "ロボット掃除機 RoboVac", "url": EUFY_URL, "status": "fetch_error", "detail": error, "candidate_count": 0, "collector": METHOD}
    text = clean(page.text)
    marker_re = re.compile(r"エラー\s*(\d{1,3})\s*[:：]\s*", re.IGNORECASE)
    markers = list(marker_re.finditer(text))
    rows = []
    seen = set()
    for i, marker in enumerate(markers):
        code = normalize_code(marker.group(1))
        end = markers[i + 1].start() if i + 1 < len(markers) else min(len(text), marker.end() + 1300)
        block = clean(text[marker.end():end])
        chunks = sentence_chunks(block)
        if not chunks:
            continue
        summary = chunks[0]
        if not 5 <= len(summary) <= 240:
            continue
        actions = []
        for chunk in chunks[1:]:
            if not 5 <= len(chunk) <= 190:
                continue
            if any(k in chunk for k in ("確認", "取り除", "清掃", "掃除", "移動", "再開", "再起動", "取り付け", "落と", "試して", "交換")):
                actions.append(chunk)
            if len(actions) >= 4:
                break
        if not actions and len(chunks) > 1:
            actions = [chunks[1][:190]]
        if code in seen:
            continue
        seen.add(code)
        rows.append(make("Anker/Eufy", "ロボット掃除機 RoboVac", code, EUFY_URL, page.title, summary, actions))
    return rows, {"manufacturer": "Anker/Eufy", "appliance": "ロボット掃除機 RoboVac", "url": EUFY_URL, "title": page.title, "status": "ok", "candidate_count": len(rows), "collector": METHOD}


def collect_delonghi() -> tuple[list[dict], dict]:
    page, error = fetch_page(DELONGHI_URL)
    if error or page is None:
        return [], {"manufacturer": "デロンギ", "appliance": "Comfortアプリ（マルチダイナミックヒーター）", "url": DELONGHI_URL, "status": "fetch_error", "detail": error, "candidate_count": 0, "collector": METHOD}
    text = clean(page.text)
    # The official FAQ headings repeat: エラーコードG001が表示された場合の対処法 ... エラーG001は、...
    marker_re = re.compile(r"エラーコード\s*([A-Z]\d{3})\s*(?:と\s*([A-Z]\d{3})\s*)?が表示された場合の対処法", re.IGNORECASE)
    markers = list(marker_re.finditer(text))
    rows = []
    seen = set()
    for i, marker in enumerate(markers):
        codes = [normalize_code(marker.group(1))]
        if marker.group(2):
            codes.append(normalize_code(marker.group(2)))
        end = markers[i + 1].start() if i + 1 < len(markers) else min(len(text), marker.end() + 2200)
        block = clean(text[marker.end():end])
        chunks = sentence_chunks(block)
        for code in codes:
            summary = next((c for c in chunks if re.search(rf"エラー\s*{re.escape(code)}\s*は", c, re.IGNORECASE)), "")
            if not summary:
                summary = next((c for c in chunks if 8 <= len(c) <= 260 and any(k in c for k in ("示して", "表して", "エラーです", "無効", "接続"))), "")
            if not summary:
                continue
            actions = []
            after = False
            for chunk in chunks:
                if chunk == summary:
                    after = True
                    continue
                if not after or not 5 <= len(chunk) <= 190:
                    continue
                if any(k in chunk for k in ("確認", "再度", "再起動", "閉じ", "入力", "探", "有効", "押", "ログイン", "認証")):
                    actions.append(chunk)
                if len(actions) >= 5:
                    break
            if code not in seen:
                seen.add(code)
                rows.append(make("デロンギ", "Comfortアプリ（マルチダイナミックヒーター）", code, DELONGHI_URL, page.title, summary, actions))

    # B007 is described in the same official FAQ under a text-error heading rather than an error-code heading.
    b007 = re.search(r"エラーB007は、(.{5,220}?[。！？!?])", text, re.IGNORECASE)
    if b007 and "B007" not in seen:
        summary = clean("エラーB007は、" + b007.group(1))
        tail = text[b007.end(): b007.end() + 1000]
        actions = [c for c in sentence_chunks(tail) if any(k in c for k in ("確認", "再試行", "参照"))][:4]
        rows.append(make("デロンギ", "Comfortアプリ（マルチダイナミックヒーター）", "B007", DELONGHI_URL, page.title, summary, actions))
    return rows, {"manufacturer": "デロンギ", "appliance": "Comfortアプリ（マルチダイナミックヒーター）", "url": DELONGHI_URL, "title": page.title, "status": "ok", "candidate_count": len(rows), "collector": METHOD}


def main() -> None:
    existing = json.loads(CANDIDATES.read_text(encoding="utf-8")) if CANDIDATES.exists() else []
    source_rows = json.loads(SOURCES.read_text(encoding="utf-8")) if SOURCES.exists() else []
    existing = [x for x in existing if x.get("extraction_method") != METHOD]
    source_rows = [x for x in source_rows if x.get("collector") != METHOD]

    added = []
    for fn in (collect_eufy, collect_delonghi):
        rows, source = fn()
        added.extend(rows)
        source_rows.append(source)

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
    by_mfr = {}
    for row in added:
        by_mfr[row["manufacturer"]] = by_mfr.get(row["manufacturer"], 0) + 1
    print(f"Eufy/DeLonghi candidates added: {len(added)}")
    print(f"Eufy/DeLonghi by manufacturer: {json.dumps(by_mfr, ensure_ascii=False)}")
    print(f"candidate records after Eufy/DeLonghi: {len(result)}")


if __name__ == "__main__":
    main()
