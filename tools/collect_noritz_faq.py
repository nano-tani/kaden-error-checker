#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from collect_candidates import clean_context, fetch_page, normalize_code

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "category_source_registry.json"
CANDIDATES = ROOT / "review" / "candidates.json"
SOURCES = ROOT / "review" / "discovered_sources.json"
METHOD = "dedicated:noritz-faq"
DOMAIN = "faq.noritz.co.jp"

TITLE_RE = re.compile(
    r"^(.+?)[：:]\s*エラー表示\s*((?:〖[^〗]+〗)+)\s*が点滅",
    re.IGNORECASE,
)
CODE_RE = re.compile(r"〖([^〗]{1,12})〗")

SKIP_SUMMARY_PREFIXES = (
    "修理をするべきか迷われている",
    "以下をご確認",
    "以下より",
    "詳しくは",
    "参考:",
    "参考：",
    "製品名の調べ方",
)
SUMMARY_KEYWORDS = (
    "異常", "不具合", "検知", "故障", "考えられ", "エラー", "表示され", "表示します",
    "点検時期", "不足", "停止", "つまり", "詰まり", "凍結", "燃焼", "通信", "温度",
    "水量", "湯量", "排水", "給水", "圧力", "センサー", "センサ",
)
ACTION_KEYWORDS = (
    "確認", "閉め", "開け", "お試し", "試し", "掃除", "お手入れ", "取り除", "補給",
    "抜", "差し", "再度", "再操作", "停止", "修理", "点検", "連絡", "相談", "待って",
    "様子をみ", "交換", "リセット", "入れ直", "切って", "運転",
)


def official(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() == DOMAIN


def sentence_chunks(value: str) -> list[str]:
    text = clean_context(value)
    return [clean_context(x).strip("・■※ ") for x in re.split(r"(?<=[。！？!?])\s*", text) if clean_context(x)]


def summary_from_detail(title: str, text: str) -> str:
    body = clean_context(text)
    if title and title in body:
        body = clean_context(body.split(title, 1)[1])
    stop = body.find("修理参考料金")
    if stop >= 0:
        body = body[:stop]
    for sentence in sentence_chunks(body[:2600]):
        if not 8 <= len(sentence) <= 260:
            continue
        if sentence.startswith(SKIP_SUMMARY_PREFIXES):
            continue
        if "修理か交換" in sentence or "フォーム" in sentence:
            continue
        if any(k in sentence for k in SUMMARY_KEYWORDS):
            return sentence[:260]
    return ""


def actions_from_detail(text: str, summary: str) -> list[str]:
    body = clean_context(text)
    if summary and summary in body:
        body = clean_context(body.split(summary, 1)[1])
    for marker in ("<修理参考料金>", "＜修理参考料金＞", "修理参考料金", "考えられる故障部品"):
        pos = body.find(marker)
        if pos >= 0:
            body = body[:pos]
    result = []
    for sentence in sentence_chunks(body[:3600]):
        if not 6 <= len(sentence) <= 190:
            continue
        if "修理をするべきか迷われている" in sentence or "フォーム" in sentence:
            continue
        if any(k in sentence for k in ACTION_KEYWORDS):
            if sentence not in result:
                result.append(sentence)
        if len(result) >= 5:
            break
    return result


def appliance_name(raw: str) -> str:
    value = clean_context(raw).strip("・ ")
    replacements = {
        "給湯器": "給湯機器",
        "給湯機器": "給湯機器",
        "暖房機器": "暖房機器",
        "浴室": "浴室設備",
        "おそうじ浴槽": "おそうじ浴槽",
        "エコウィル": "エコウィル",
    }
    return replacements.get(value, value[:70])


def main() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    cfg = next((
        x for x in registry
        if x.get("manufacturer") == "ノーリツ"
        and x.get("collector") == "noritz_faq"
        and x.get("enabled", True) is not False
    ), None)
    if not cfg:
        print("Noritz collector disabled or not configured")
        return

    hub_url = str(cfg["source_url"])
    hub, error = fetch_page(hub_url)
    if error or hub is None:
        raise RuntimeError(f"Noritz hub fetch failed: {error}")

    existing = json.loads(CANDIDATES.read_text(encoding="utf-8")) if CANDIDATES.exists() else []
    source_rows = json.loads(SOURCES.read_text(encoding="utf-8")) if SOURCES.exists() else []
    existing = [x for x in existing if x.get("extraction_method") != METHOD]
    source_rows = [x for x in source_rows if x.get("collector") != METHOD]

    links: list[tuple[str, list[str], str]] = []
    seen_urls = set()
    for href, anchor in hub.links:
        title = clean_context(anchor)
        match = TITLE_RE.match(title)
        if not match:
            continue
        detail_url = urljoin(hub_url, href)
        if not official(detail_url) or detail_url in seen_urls:
            continue
        codes = []
        for raw in CODE_RE.findall(match.group(2)):
            code = normalize_code(raw)
            if not code or len(code) > 7 or code in codes:
                continue
            codes.append(code)
        if not codes:
            continue
        seen_urls.add(detail_url)
        links.append((appliance_name(match.group(1)), codes, detail_url))

    added = []
    fetch_errors = 0
    for appliance, codes, detail_url in links:
        try:
            detail, error = fetch_page(detail_url)
        except Exception as exc:
            detail, error = None, f"{type(exc).__name__}: {exc}"
        if error or detail is None:
            fetch_errors += 1
            source_rows.append({
                "manufacturer": "ノーリツ",
                "appliance": appliance,
                "url": detail_url,
                "status": "fetch_error",
                "detail": str(error)[:240],
                "candidate_count": 0,
                "collector": METHOD,
            })
            continue

        summary = summary_from_detail(detail.title, detail.text)
        actions = actions_from_detail(detail.text, summary) if summary else []
        confidence = "high" if summary else "medium"
        for code in codes:
            added.append({
                "manufacturer": "ノーリツ",
                "appliance": appliance,
                "code": code,
                "source": detail_url,
                "detail_url": detail_url,
                "page_title": detail.title,
                "evidence": clean_context(f"エラーコード {code} {summary} {' '.join(actions)}")[:750],
                "confidence": confidence,
                "status": "needs_review",
                "extraction_method": METHOD,
                "summary_hint": summary,
                "action_hint": " ".join(actions),
                "already_published": False,
            })
        source_rows.append({
            "manufacturer": "ノーリツ",
            "appliance": appliance,
            "url": detail_url,
            "title": detail.title,
            "status": "ok" if summary else "ok_no_structured_summary",
            "candidate_count": len(codes) if summary else 0,
            "collector": METHOD,
        })
        time.sleep(0.08)

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
    source_rows.append({
        "manufacturer": "ノーリツ",
        "appliance": "公式FAQカテゴリ",
        "url": hub_url,
        "title": hub.title,
        "status": "ok",
        "candidate_count": len(added),
        "collector": METHOD,
        "detail_links": len(links),
        "fetch_errors": fetch_errors,
    })
    source_rows.sort(key=lambda x: (str(x.get("manufacturer", "")), str(x.get("appliance", "")), str(x.get("url", ""))))
    CANDIDATES.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SOURCES.write_text(json.dumps(source_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    high = sum(x.get("confidence") == "high" for x in added)
    appliances = sorted({x.get("appliance") for x in added})
    print(f"Noritz error detail links discovered: {len(links)}")
    print(f"Noritz candidates added: {len(added)}")
    print(f"Noritz high-confidence candidates: {high}")
    print(f"Noritz product scopes discovered: {len(appliances)}")
    print(f"Noritz detail fetch errors: {fetch_errors}")
    print(f"candidate records after Noritz: {len(result)}")


if __name__ == "__main__":
    main()
