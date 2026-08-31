#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import time
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse

from collect_candidates import clean_context, fetch_page, normalize_code

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "review" / "candidates.json"
SOURCES = ROOT / "review" / "discovered_sources.json"
METHOD = "dedicated:sharp-category"
DOMAIN = "cs.sharp.co.jp"

CATEGORY_ROOTS = [
    ("冷蔵庫", "https://cs.sharp.co.jp/trouble_check/div/refrigerator/navi/sj_diag13.html", "/trouble_check/div/refrigerator/", 120),
    ("加湿空気清浄機", "https://cs.sharp.co.jp/trouble_check/div/air_purifier/navi/diag04.html", "/trouble_check/div/air_purifier/", 160),
    ("ウォーターオーブン ヘルシオ", "https://cs.sharp.co.jp/trouble_check/div/healsio/diag04.html", "/trouble_check/div/healsio/", 80),
    ("電子レンジ・オーブンレンジ", "https://cs.sharp.co.jp/trouble_check/div/oven/diag04.html", "/trouble_check/div/oven/", 80),
    ("除湿機", "https://cs.sharp.co.jp/trouble_check/div/dehumid_con/navi/diag03.html", "/trouble_check/div/dehumid_con/", 150),
    ("扇風機", "https://cs.sharp.co.jp/trouble_check/div/e_fan/navi/diag06.html", "/trouble_check/div/e_fan/", 80),
    ("ホットクック", "https://cs.sharp.co.jp/trouble_check/div/hotcook/diag01.html", "/trouble_check/div/hotcook/", 80),
    ("炊飯器", "https://cs.sharp.co.jp/trouble_check/div/ricecooker/diag03.html", "/trouble_check/div/ricecooker/", 80),
]

# Code anchors on Sharp pages appear as 〖C01〗, 「C1」, C8エラー, etc.
CODE_ANCHOR_RE = re.compile(
    r"(?:〖|「|\[)?\s*((?:[CEUFHPA]\d{1,3}|U\d{1,3}|C\d{1,3}|E\d{1,3}|888))\s*(?:〗|」|\])?",
    re.IGNORECASE,
)

SUMMARY_KEYWORDS = (
    "異常", "不具合", "検知", "故障", "停止", "エラー", "温度", "センサー", "センサ", "通信",
    "水位", "排水", "給水", "高温", "低温", "電圧", "回転", "取り付け", "装着", "開いて",
    "詰まり", "つまり", "不足", "多すぎ", "少なすぎ", "できません", "できない", "運転中",
)
ACTION_KEYWORDS = (
    "確認", "抜", "差し", "入れ直", "再度", "再運転", "閉", "開", "掃除", "清掃", "取り除",
    "修理", "点検", "連絡", "相談", "待", "停止", "取り付け", "セット", "減ら", "増や", "解除",
)


def official(url: str, prefix: str) -> bool:
    parsed = urlparse(url)
    return (parsed.hostname or "").lower() == DOMAIN and parsed.path.startswith(prefix)


def sentence_chunks(text: str) -> list[str]:
    value = clean_context(text)
    return [clean_context(x).strip("・※■ ") for x in re.split(r"(?<=[。！？!?])\s*", value) if clean_context(x)]


def codes_from_anchor(anchor: str) -> list[str]:
    text = clean_context(anchor).upper()
    if not any(mark in text for mark in ("表示", "点滅", "エラー", "ERROR")):
        return []
    codes = []
    for match in CODE_ANCHOR_RE.finditer(text):
        code = normalize_code(match.group(1))
        if code and code not in codes:
            codes.append(code)
    return codes


def detail_summary(code: str, title: str, text: str) -> str:
    body = clean_context(text)
    # Drop navigation noise up to the detail title when possible.
    for marker in (title, f"{code}エラー", f"〖{code}〗", f"「{code}」"):
        if marker and marker in body:
            tail = clean_context(body.split(marker, 1)[1])
            if len(tail) > 20:
                body = tail
                break
    for stop in ("修理を申し込", "修理概算", "取扱説明書", "チャットで質問"):
        pos = body.find(stop)
        if 0 <= pos < 3000:
            body = body[:pos]
    for sentence in sentence_chunks(body[:3000]):
        if not 7 <= len(sentence) <= 260:
            continue
        if sentence.startswith(("確認事項", "以下を", "次の", "ご使用の", "該当する", "上記")):
            continue
        if any(k in sentence for k in SUMMARY_KEYWORDS):
            return sentence[:260]
    return ""


def detail_actions(text: str, summary: str) -> list[str]:
    body = clean_context(text)
    if summary and summary in body:
        body = clean_context(body.split(summary, 1)[1])
    result = []
    for sentence in sentence_chunks(body[:3600]):
        if not 6 <= len(sentence) <= 190:
            continue
        if any(k in sentence for k in ACTION_KEYWORDS):
            if sentence not in result:
                result.append(sentence)
        if len(result) >= 5:
            break
    return result


def useful_follow(anchor: str, href: str) -> bool:
    text = clean_context(anchor)
    href_l = href.lower()
    if codes_from_anchor(text):
        return True
    if any(k in text for k in ("エラー", "点滅", "表示", "形名", "シリーズ")):
        return True
    if re.search(r"diag\d", href_l):
        return True
    # Model selector pages often use model names only; allow nav paths but bound total pages.
    if "/navi" in href_l and re.search(r"[A-Z]{1,4}-[A-Z0-9]", text.upper()):
        return True
    return False


def collect_category(appliance: str, root_url: str, prefix: str, max_pages: int) -> tuple[list[dict], list[dict]]:
    queue = deque([root_url])
    seen = set()
    candidates = []
    source_rows = []
    candidate_keys = set()

    while queue and len(seen) < max_pages:
        url = queue.popleft()
        if url in seen or not official(url, prefix):
            continue
        seen.add(url)
        try:
            parser, error = fetch_page(url)
        except Exception as exc:
            parser, error = None, f"{type(exc).__name__}: {exc}"
        if error or parser is None:
            source_rows.append({
                "manufacturer": "シャープ", "appliance": appliance, "url": url,
                "status": "fetch_error", "detail": str(error)[:240], "candidate_count": 0,
                "collector": METHOD,
            })
            continue

        page_added = 0
        page_text = clean_context(parser.text)
        for href, anchor in parser.links:
            absolute = urljoin(url, href)
            if not official(absolute, prefix):
                continue
            codes = codes_from_anchor(anchor)
            if codes:
                # Fetch the linked diagnosis detail page once and reuse it for grouped code anchors.
                try:
                    detail, detail_error = fetch_page(absolute)
                except Exception as exc:
                    detail, detail_error = None, f"{type(exc).__name__}: {exc}"
                if detail is not None and not detail_error:
                    for code in codes:
                        summary = detail_summary(code, detail.title, detail.text)
                        actions = detail_actions(detail.text, summary) if summary else []
                        confidence = "high" if summary else "medium"
                        key = (appliance, code, absolute)
                        if key in candidate_keys:
                            continue
                        candidate_keys.add(key)
                        candidates.append({
                            "manufacturer": "シャープ",
                            "appliance": appliance,
                            "code": code,
                            "source": absolute,
                            "detail_url": absolute,
                            "page_title": detail.title,
                            "evidence": clean_context(f"エラーコード {code} {summary} {' '.join(actions)}")[:750],
                            "confidence": confidence,
                            "status": "needs_review",
                            "extraction_method": METHOD,
                            "summary_hint": summary,
                            "action_hint": " ".join(actions),
                            "already_published": False,
                        })
                        page_added += 1
                    time.sleep(0.04)
                continue
            if absolute not in seen and useful_follow(anchor, absolute):
                queue.append(absolute)

        # Some index pages render code labels as text without links. Record only as medium review candidates,
        # never auto-promote, so the source remains discoverable without inventing a cause.
        for code in sorted(set(normalize_code(x) for x in re.findall(r"[〖「](?:\s*)([CEU]\d{1,3}|888)(?:\s*)[〗」]", page_text, re.IGNORECASE))):
            if not code:
                continue
            key = (appliance, code, url)
            if key in candidate_keys:
                continue
            candidate_keys.add(key)
            candidates.append({
                "manufacturer": "シャープ", "appliance": appliance, "code": code,
                "source": url, "page_title": parser.title,
                "evidence": clean_context(f"エラーコード {code} {page_text[:500]}")[:700],
                "confidence": "medium", "status": "needs_review",
                "extraction_method": METHOD, "summary_hint": "", "action_hint": "",
                "already_published": False,
            })

        source_rows.append({
            "manufacturer": "シャープ", "appliance": appliance, "url": url,
            "title": parser.title, "status": "ok", "candidate_count": page_added,
            "collector": METHOD,
        })
        time.sleep(0.03)

    return candidates, source_rows


def main() -> None:
    existing = json.loads(CANDIDATES.read_text(encoding="utf-8")) if CANDIDATES.exists() else []
    source_rows = json.loads(SOURCES.read_text(encoding="utf-8")) if SOURCES.exists() else []
    existing = [x for x in existing if x.get("extraction_method") != METHOD]
    source_rows = [x for x in source_rows if x.get("collector") != METHOD]

    added = []
    for appliance, root_url, prefix, max_pages in CATEGORY_ROOTS:
        rows, sources = collect_category(appliance, root_url, prefix, max_pages)
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
    print(f"Sharp category candidates added: {len(added)}")
    print(f"Sharp category high-confidence candidates: {high}")
    print(f"candidate records after Sharp categories: {len(result)}")


if __name__ == "__main__":
    main()
