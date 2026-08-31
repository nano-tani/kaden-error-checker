#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from collect_candidates import clean_context, fetch_page, normalize_code

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "review" / "candidates.json"
SOURCES = ROOT / "review" / "discovered_sources.json"

SHARP_DOMAIN = "cs.sharp.co.jp"
INDEXES = [
    ("タテ型洗濯機・洗濯乾燥機", "https://cs.sharp.co.jp/trouble_check/div/washer/navi_ge/ge_diag09_07.html"),
    ("ドラム式洗濯機・洗濯乾燥機", "https://cs.sharp.co.jp/trouble_check/div/washer/navi_z100/diag17.html"),
    ("ドラム式洗濯機・洗濯乾燥機", "https://cs.sharp.co.jp/trouble_check/div/washer/navi_w/diag17.html"),
    ("ドラム式洗濯機・洗濯乾燥機", "https://cs.sharp.co.jp/trouble_check/div/washer/navi_s/diag17.html"),
    ("ドラム式洗濯機・洗濯乾燥機", "https://cs.sharp.co.jp/trouble_check/div/washer/navi_v520/diag_error.html"),
    ("ドラム式洗濯機・洗濯乾燥機", "https://cs.sharp.co.jp/trouble_check/div/washer/navi_v510/v_diag10.html"),
    ("ドラム式洗濯機・洗濯乾燥機", "https://cs.sharp.co.jp/trouble_check/div/washer/navi/hg_diag10.html"),
]

CODE_RE = re.compile(r"(?<![A-Z0-9])((?:E|U|C)[A-Z0-9]{1,3}|UF)(?=\s*(?:エラー|表示|$))", re.IGNORECASE)
ACTION_WORDS = (
    "確認", "抜", "差", "入れ", "押", "取り外", "取り付", "お手入れ", "清掃", "減ら",
    "直", "移動", "調整", "閉", "開", "再", "スタート", "依頼", "相談", "停止",
)
SKIP_CODES = {"UCL"}  # lock/notice-style displays are not published as faults automatically


def sharp_url(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() == SHARP_DOMAIN


def code_from_text(value: str) -> str:
    match = CODE_RE.search(clean_context(value).upper())
    if not match:
        return ""
    code = normalize_code(match.group(1))
    if code in SKIP_CODES:
        return ""
    return code


def sentence_chunks(text: str) -> list[str]:
    text = clean_context(text)
    return [clean_context(x) for x in re.split(r"(?<=[。！？!?])\s*", text) if clean_context(x)]


def summary_from_page(text: str, code: str) -> str:
    text = clean_context(text)
    patterns = [
        rf"{re.escape(code)}\s*エラー表示とは\s*(.*)",
        rf"{re.escape(code)}\s*エラー(?:が)?表示される\s*(.*)",
        rf"{re.escape(code)}\s*エラー表示\s*(.*)",
    ]
    tails = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            tails.append(match.group(1)[:800])

    keywords = ("異常", "検知", "排水", "給水", "脱水", "乾燥", "回転", "温度", "水位", "ロック", "故障", "止ま", "できな", "詰ま", "かたより", "傾き")
    for tail in reversed(tails):
        for sentence in sentence_chunks(tail):
            if not 12 <= len(sentence) <= 260:
                continue
            if "故障診断ナビ" in sentence or "エラーやお知らせ表示" in sentence:
                continue
            if any(word in sentence for word in keywords):
                return sentence

    # Fallback: choose a nearby factual sentence containing a diagnostic keyword.
    for sentence in sentence_chunks(text):
        if 12 <= len(sentence) <= 260 and any(word in sentence for word in keywords):
            if code in sentence or "エラー" in sentence:
                return sentence
    return ""


def actions_from_page(text: str) -> list[str]:
    text = clean_context(text)
    start = -1
    for marker in (
        "下記の操作をお試しください",
        "以下の点をご確認ください",
        "以下の内容をご確認ください",
        "以下をご確認ください",
        "確認して下さい",
        "確認してください",
    ):
        pos = text.find(marker)
        if pos >= 0 and (start < 0 or pos < start):
            start = pos + len(marker)
    if start < 0:
        return ["メーカー公式の故障診断ナビで対象機種の確認事項を確認する"]

    tail = text[start:start + 1800]
    for stop in ("改善しましたか", "上記をお試し", "修理を申し込", "故障診断ナビトップ"):
        pos = tail.find(stop)
        if pos >= 0:
            tail = tail[:pos]
    chunks = sentence_chunks(tail)
    result = []
    for chunk in chunks:
        chunk = re.sub(r"^\d+[.．、]\s*", "", chunk).strip("・ ")
        if not 8 <= len(chunk) <= 180:
            continue
        if not any(word in chunk for word in ACTION_WORDS):
            continue
        if chunk not in result:
            result.append(chunk)
        if len(result) >= 5:
            break
    return result or ["メーカー公式の故障診断ナビで対象機種の確認事項を確認する"]


def main() -> None:
    existing = json.loads(CANDIDATES.read_text(encoding="utf-8")) if CANDIDATES.exists() else []
    source_rows = json.loads(SOURCES.read_text(encoding="utf-8")) if SOURCES.exists() else []
    added = []
    visited = set()

    for appliance, index_url in INDEXES:
        try:
            parser, error = fetch_page(index_url)
        except Exception as exc:
            parser, error = None, f"{type(exc).__name__}: {exc}"
        if error or parser is None:
            source_rows.append({
                "manufacturer": "シャープ",
                "appliance": appliance,
                "url": index_url,
                "status": "fetch_error",
                "detail": str(error)[:240],
                "candidate_count": 0,
                "collector": "dedicated-extra",
            })
            continue

        detail_links = []
        for href, anchor in parser.links:
            absolute = urljoin(index_url, href)
            code = code_from_text(anchor)
            if not code or not sharp_url(absolute):
                continue
            if "/trouble_check/div/washer/" not in absolute:
                continue
            detail_links.append((code, absolute))

        source_rows.append({
            "manufacturer": "シャープ",
            "appliance": appliance,
            "url": index_url,
            "title": parser.title,
            "status": "ok",
            "candidate_count": len(detail_links),
            "collector": "dedicated-extra",
        })

        for link_code, detail_url in detail_links:
            key = (appliance, detail_url)
            if key in visited:
                continue
            visited.add(key)
            try:
                detail, error = fetch_page(detail_url)
            except Exception as exc:
                detail, error = None, f"{type(exc).__name__}: {exc}"
            if error or detail is None:
                source_rows.append({
                    "manufacturer": "シャープ",
                    "appliance": appliance,
                    "url": detail_url,
                    "status": "fetch_error",
                    "detail": str(error)[:240],
                    "candidate_count": 0,
                    "collector": "dedicated-extra",
                })
                continue

            code = code_from_text(detail.title) or link_code
            if not code:
                continue
            summary = summary_from_page(detail.text, code)
            if not summary:
                source_rows.append({
                    "manufacturer": "シャープ",
                    "appliance": appliance,
                    "url": detail_url,
                    "title": detail.title,
                    "status": "ok_no_structured_summary",
                    "candidate_count": 0,
                    "collector": "dedicated-extra",
                })
                continue
            actions = actions_from_page(detail.text)
            evidence = clean_context(f"{summary} {' '.join(actions)}")[:700]
            added.append({
                "manufacturer": "シャープ",
                "appliance": appliance,
                "code": code,
                "source": detail_url,
                "page_title": detail.title,
                "evidence": evidence,
                "confidence": "high",
                "status": "needs_review",
                "extraction_method": "dedicated:sharp",
                "detail_url": detail_url,
                "summary_hint": summary,
                "action_hint": " ".join(actions),
                "scope_hint": "ドラム式" if appliance.startswith("ドラム") else "タテ型",
                "already_published": False,
            })
            source_rows.append({
                "manufacturer": "シャープ",
                "appliance": appliance,
                "url": detail_url,
                "title": detail.title,
                "status": "ok",
                "candidate_count": 1,
                "collector": "dedicated-extra",
            })

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
    source_rows.sort(key=lambda x: (str(x.get("manufacturer", "")), str(x.get("url", ""))))
    CANDIDATES.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SOURCES.write_text(json.dumps(source_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Sharp washer dedicated candidates added: {len(added)}")
    print(f"candidate records after extra sources: {len(result)}")


if __name__ == "__main__":
    main()
