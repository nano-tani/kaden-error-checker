#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from collect_candidates import clean_context, fetch_page, normalize_code

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "review" / "candidates.json"
SOURCES = ROOT / "review" / "discovered_sources.json"
METHOD = "dedicated:misc-official"

TOYOTOMI_ROOT = "https://www.toyotomi.jp/support/kerosene_heater/error"
DAINICHI_ROOTS = [
    ("石油暖房機器", "https://www.dainichi-net.co.jp/support/troublecheck/oil-heating/error/"),
    ("加湿器", "https://www.dainichi-net.co.jp/support/troublecheck/humidifier/error/"),
]
TIGER_SECTIONS = [
    ("ジャー炊飯器", "https://support.tiger-corporation.com/hc/ja/sections/7004711609231-%E3%82%A8%E3%83%A9%E3%83%BC%E8%A1%A8%E7%A4%BA"),
    ("電気ポット", "https://support.tiger-corporation.com/hc/ja/sections/7004696049167-%E3%82%A8%E3%83%A9%E3%83%BC%E8%A1%A8%E7%A4%BA"),
]

CODE_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{1,3}-?\d{1,3}|C-?\d{1,3}|F-?\d{1,3}|E-?\d{1,3}|HHH\d?|HHH)(?![A-Z0-9])", re.IGNORECASE)
TIGER_QUOTED_RE = re.compile(r"[「\"]([A-Z]{1,3}-?\d{1,3}|Err)[」\"]", re.IGNORECASE)
NON_ERROR = ("エラー（故障）ではありません", "エラー(故障)ではありません", "故障ではありません")
CAUSE_WORDS = ("作動", "異常", "故障", "原因", "検知", "不足", "過熱", "停電", "温度", "水", "ノイズ", "塞", "詰", "燃焼", "転倒", "停止")
ACTION_WORDS = ("確認", "掃除", "清掃", "取り除", "抜", "差し", "再点火", "再度", "修理", "連絡", "移動", "換気", "待", "停止", "入れ直", "依頼")


def clean(value: object) -> str:
    return clean_context(str(value or ""))


def domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def sentences(text: str) -> list[str]:
    return [clean(x).strip("・※■ ") for x in re.split(r"(?<=[。！？!?])\s*", clean(text)) if clean(x)]


def codes_from_text(text: str) -> list[str]:
    value = clean(text).upper()
    result = []
    for m in CODE_RE.finditer(value):
        code = normalize_code(m.group(1))
        if code and code not in result and len(code) <= 7:
            result.append(code)
    return result


def action_sentences(text: str, summary: str = "") -> list[str]:
    body = clean(text)
    if summary and summary in body:
        body = clean(body.split(summary, 1)[1])
    out = []
    for sentence in sentences(body[:3500]):
        if not 5 <= len(sentence) <= 190:
            continue
        if any(k in sentence for k in ACTION_WORDS):
            if sentence not in out:
                out.append(sentence)
        if len(out) >= 5:
            break
    return out


def specific_summary(title: str, text: str, code: str) -> str:
    t = clean(title)
    # Parenthetical diagnostic cause in titles is the strongest structured summary.
    parens = re.findall(r"[（(]([^()（）]{5,180})[）)]", t)
    for value in parens:
        if any(k in value for k in CAUSE_WORDS):
            return clean(value)
    body = clean(text)
    if t and t in body:
        body = clean(body.split(t, 1)[1])
    for sentence in sentences(body[:2800]):
        if not 7 <= len(sentence) <= 250:
            continue
        if any(skip in sentence for skip in ("修理をご希望", "お客様のお役", "こちらへ", "この記事")):
            continue
        if any(k in sentence for k in CAUSE_WORDS):
            # Avoid a generic 'if it repeats, repair is needed' as a cause.
            if sentence.startswith(("以上を確認", "改善しない", "この方法を", "それでも")):
                continue
            return sentence[:250]
    return ""


def make_candidate(manufacturer: str, appliance: str, code: str, source: str, title: str, summary: str, actions: list[str]) -> dict:
    return {
        "manufacturer": manufacturer,
        "appliance": appliance,
        "code": normalize_code(code),
        "source": source,
        "detail_url": source,
        "page_title": title,
        "evidence": clean(f"エラーコード {code} {summary} {' '.join(actions)}")[:800],
        "confidence": "high" if summary else "medium",
        "status": "needs_review",
        "extraction_method": METHOD,
        "summary_hint": summary,
        "action_hint": clean(" ".join(actions)),
        "already_published": False,
    }


def collect_toyotomi() -> tuple[list[dict], list[dict]]:
    root, error = fetch_page(TOYOTOMI_ROOT)
    if error or root is None:
        return [], [{"manufacturer": "トヨトミ", "appliance": "石油ファンヒーター", "url": TOYOTOMI_ROOT, "status": "fetch_error", "detail": error, "candidate_count": 0, "collector": METHOD}]
    added = []
    sources = []
    seen = set()
    for href, anchor in root.links:
        url = urljoin(TOYOTOMI_ROOT, href)
        if domain(url) not in {"www.toyotomi.jp", "toyotomi.jp"} or "/support/kerosene_heater/trouble/error/" not in url:
            continue
        if url in seen:
            continue
        seen.add(url)
        detail, err = fetch_page(url)
        if err or detail is None:
            continue
        full = clean(detail.text)
        if any(marker in full for marker in NON_ERROR):
            sources.append({"manufacturer": "トヨトミ", "appliance": "石油ファンヒーター", "url": url, "title": detail.title, "status": "non_error_notice", "candidate_count": 0, "collector": METHOD})
            continue
        codes = codes_from_text(detail.title)
        # HHH1 HHH2 HHH3 can be grouped in one official detail page.
        if not codes:
            codes = codes_from_text(anchor)
        summary = ""
        for marker in ("「表示の意味」", "表示の意味"):
            pos = full.find(marker)
            if pos >= 0:
                tail = full[pos + len(marker):]
                for s in sentences(tail[:1000]):
                    if 5 <= len(s) <= 250 and not s.startswith("処置方法"):
                        summary = s
                        break
                if summary:
                    break
        if not summary:
            summary = specific_summary(detail.title, full, codes[0] if codes else "")
        actions = []
        pos = full.find("処置方法")
        if pos >= 0:
            actions = action_sentences(full[pos:])
        else:
            actions = action_sentences(full, summary)
        for code in codes:
            if code in {"3HR", "1HR"}:
                continue
            added.append(make_candidate("トヨトミ", "石油ファンヒーター", code, url, detail.title, summary, actions))
        sources.append({"manufacturer": "トヨトミ", "appliance": "石油ファンヒーター", "url": url, "title": detail.title, "status": "ok", "candidate_count": len(codes), "collector": METHOD})
        time.sleep(0.04)
    return added, sources


def collect_dainichi() -> tuple[list[dict], list[dict]]:
    added = []
    sources = []
    for appliance, root_url in DAINICHI_ROOTS:
        root, error = fetch_page(root_url)
        if error or root is None:
            sources.append({"manufacturer": "ダイニチ", "appliance": appliance, "url": root_url, "status": "fetch_error", "detail": error, "candidate_count": 0, "collector": METHOD})
            continue
        seen = set()
        for href, anchor in root.links:
            url = urljoin(root_url, href)
            if domain(url) not in {"www.dainichi-net.co.jp", "dainichi-net.co.jp"} or "/answer/" not in url or url in seen:
                continue
            codes = codes_from_text(anchor)
            if not codes:
                continue
            seen.add(url)
            detail, err = fetch_page(url)
            if err or detail is None:
                continue
            title_codes = codes_from_text(detail.title)
            if title_codes:
                codes = title_codes
            summary = specific_summary(detail.title, detail.text, codes[0])
            actions = action_sentences(detail.text, summary)
            for code in codes:
                added.append(make_candidate("ダイニチ", appliance, code, url, detail.title, summary, actions))
            sources.append({"manufacturer": "ダイニチ", "appliance": appliance, "url": url, "title": detail.title, "status": "ok", "candidate_count": len(codes) if summary else 0, "collector": METHOD})
            time.sleep(0.035)
    return added, sources


def tiger_codes(title: str) -> list[str]:
    value = clean(title)
    codes = []
    for raw in TIGER_QUOTED_RE.findall(value):
        code = normalize_code(raw)
        if code == "ERR":
            continue
        if code not in codes:
            codes.append(code)
    # Titles may say F2/C4 together without all being captured by quote syntax.
    for code in codes_from_text(value):
        if code not in codes:
            codes.append(code)
    return codes


def collect_tiger() -> tuple[list[dict], list[dict]]:
    added = []
    sources = []
    for appliance, section_url in TIGER_SECTIONS:
        section, error = fetch_page(section_url)
        if error or section is None:
            sources.append({"manufacturer": "タイガー魔法瓶", "appliance": appliance, "url": section_url, "status": "fetch_error", "detail": error, "candidate_count": 0, "collector": METHOD})
            continue
        seen = set()
        for href, anchor in section.links:
            url = urljoin(section_url, href)
            if domain(url) != "support.tiger-corporation.com" or "/articles/" not in url or url in seen:
                continue
            codes = tiger_codes(anchor)
            # Generic 'E:数字' and no-code articles are review only and intentionally skipped.
            if not codes:
                continue
            seen.add(url)
            detail, err = fetch_page(url)
            if err or detail is None:
                continue
            codes = tiger_codes(detail.title) or codes
            summary = specific_summary(detail.title, detail.text, codes[0])
            actions = action_sentences(detail.text, summary)
            for code in codes:
                added.append(make_candidate("タイガー魔法瓶", appliance, code, url, detail.title, summary, actions))
            sources.append({"manufacturer": "タイガー魔法瓶", "appliance": appliance, "url": url, "title": detail.title, "status": "ok", "candidate_count": len(codes) if summary else 0, "collector": METHOD})
            time.sleep(0.035)
    return added, sources


def main() -> None:
    existing = json.loads(CANDIDATES.read_text(encoding="utf-8")) if CANDIDATES.exists() else []
    source_rows = json.loads(SOURCES.read_text(encoding="utf-8")) if SOURCES.exists() else []
    existing = [x for x in existing if x.get("extraction_method") != METHOD]
    source_rows = [x for x in source_rows if x.get("collector") != METHOD]

    added = []
    for fn in (collect_toyotomi, collect_dainichi, collect_tiger):
        rows, sources = fn()
        added.extend(rows)
        source_rows.extend(sources)

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
    print(f"misc official candidates added: {len(added)}")
    print(f"misc official candidates by manufacturer: {json.dumps(by_mfr, ensure_ascii=False)}")
    print(f"candidate records after misc official: {len(result)}")


if __name__ == "__main__":
    main()
