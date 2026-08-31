#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from collect_candidates import clean_context, fetch_page, normalize_code

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "category_source_registry.json"
CANDIDATES = ROOT / "review" / "candidates.json"
SOURCES = ROOT / "review" / "discovered_sources.json"
METHOD = "dedicated:rinnai-faq"
TITLE_RE = re.compile(r"^(.+?)｜\s*エラーコード\s*([A-Za-z0-9-]{1,7})\s*が表示", re.IGNORECASE)


def first_sentence(text: str) -> str:
    value = clean_context(text).strip("・ ")
    if not value:
        return ""
    match = re.match(r"(.{5,260}?[。！？!?])(?:\s|$)", value)
    return clean_context(match.group(1) if match else value[:260])


def explicit_summary(code: str, snippet: str) -> str:
    text = clean_context(snippet)
    code_re = re.escape(normalize_code(code))
    patterns = (
        rf"(エラー\s*{code_re}\s*は、?.{{3,240}}?[。！？!?])",
        rf"(エラーコード\s*{code_re}\s*は、?.{{3,240}}?[。！？!?])",
        rf"({code_re}\s*は、?.{{3,240}}?[。！？!?])",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return clean_context(match.group(1))[:260]
    return ""


def action_hint(summary: str, snippet: str) -> str:
    text = clean_context(snippet)
    if summary and summary in text:
        text = clean_context(text.split(summary, 1)[1])
    text = re.sub(r"修理料金の目安.*$", "", text)
    text = re.sub(r"考えられる故障箇所.*$", "", text)
    chunks = [clean_context(x) for x in re.split(r"(?<=[。！？!?])\s*", text) if clean_context(x)]
    result = []
    for chunk in chunks:
        chunk = re.sub(r"^[（(]?\d+[）)]?\s*", "", chunk).strip("・ 〖〗")
        if not 5 <= len(chunk) <= 180:
            continue
        if any(k in chunk for k in ("確認", "閉", "開", "抜", "差し", "再操作", "再度", "掃除", "清掃", "交換", "修理", "点検", "連絡", "待", "停止", "水位", "フィルタ")):
            if chunk not in result:
                result.append(chunk)
        if len(result) >= 4:
            break
    return " ".join(result)[:500]


def appliance_name(raw: str) -> str:
    value = clean_context(raw).strip("・ ")
    aliases = {
        "給湯器": "給湯器",
        "ガス給湯器": "給湯器",
        "給湯暖房機": "給湯暖房機",
        "ガスコンロ": "ガスコンロ",
        "ビルトインコンロ": "ビルトインガスコンロ",
        "ガステーブル": "ガステーブル",
        "ガスオーブン": "ガスオーブン",
        "食器洗い乾燥機": "食器洗い乾燥機",
        "ガス衣類乾燥機": "ガス衣類乾燥機",
        "浴室暖房乾燥機": "浴室暖房乾燥機",
        "ガスファンヒーター": "ガスファンヒーター",
        "ガス炊飯器": "ガス炊飯器",
        "レンジフード": "レンジフード",
    }
    return aliases.get(value, value[:60])


def page_url(base: str, page: int) -> str:
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}page={page}"


def collect_page(base: str, page: int) -> tuple[list[dict], dict]:
    url = page_url(base, page)
    parser, error = fetch_page(url)
    if error or parser is None:
        return [], {"url": url, "status": "fetch_error", "detail": error, "candidate_count": 0}

    text = clean_context(parser.text)
    rows = []
    seen_titles = set()
    for href, anchor in parser.links:
        title = clean_context(anchor)
        match = TITLE_RE.match(title)
        if not match or title in seen_titles:
            continue
        seen_titles.add(title)
        product = appliance_name(match.group(1))
        code = normalize_code(match.group(2))
        if not product or not code or len(code) > 7:
            continue
        pos = text.find(title)
        tail = text[pos + len(title):] if pos >= 0 else ""
        end_positions = [p for p in (tail.find("No："), tail.find("No:")) if p >= 0]
        if end_positions:
            tail = tail[:min(end_positions)]
        tail = clean_context(tail.replace("詳細表示", " "))[:1200]
        summary = explicit_summary(code, tail)
        confidence = "high" if summary else "medium"
        action = action_hint(summary, tail) if summary else ""
        detail_url = urljoin(url, href)
        rows.append({
            "manufacturer": "リンナイ",
            "appliance": product,
            "code": code,
            "source": detail_url,
            "detail_url": detail_url,
            "page_title": title,
            "evidence": clean_context(f"{title} {tail}")[:700],
            "confidence": confidence,
            "status": "needs_review",
            "extraction_method": METHOD,
            "summary_hint": summary or first_sentence(tail),
            "action_hint": action,
            "already_published": False,
        })
    return rows, {
        "manufacturer": "リンナイ",
        "appliance": "FAQエラーコードカテゴリ",
        "url": url,
        "title": parser.title,
        "status": "ok",
        "candidate_count": len(rows),
        "collector": METHOD,
    }


def main() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    cfg = next((x for x in registry if x.get("manufacturer") == "リンナイ" and x.get("collector") == "rinnai_faq" and x.get("enabled", True) is not False), None)
    if not cfg:
        print("Rinnai collector disabled or not configured")
        return

    base = str(cfg["source_url"])
    existing = json.loads(CANDIDATES.read_text(encoding="utf-8")) if CANDIDATES.exists() else []
    source_rows = json.loads(SOURCES.read_text(encoding="utf-8")) if SOURCES.exists() else []
    added = []
    empty_streak = 0

    for page in range(1, int(cfg.get("max_pages", 50)) + 1):
        rows, source_row = collect_page(base, page)
        source_rows.append(source_row)
        if not rows:
            empty_streak += 1
            if empty_streak >= 2:
                break
        else:
            empty_streak = 0
            added.extend(rows)
        time.sleep(0.15)

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
    source_rows.sort(key=lambda x: (str(x.get("manufacturer", "")), str(x.get("appliance", "")), str(x.get("url", ""))))
    CANDIDATES.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SOURCES.write_text(json.dumps(source_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    high = sum(x.get("confidence") == "high" for x in added)
    products = sorted({x.get("appliance") for x in added})
    print(f"Rinnai FAQ candidates added: {len(added)}")
    print(f"Rinnai high-confidence candidates: {high}")
    print(f"Rinnai product categories discovered: {len(products)}")
    print(f"candidate records after Rinnai: {len(result)}")


if __name__ == "__main__":
    main()
