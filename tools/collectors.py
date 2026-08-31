#!/usr/bin/env python3
import re
import unicodedata
from urllib.parse import urljoin

DEDICATED_MANUFACTURERS = {"ダイキン", "パナソニック", "日立", "シャープ"}


def normalize(value):
    return unicodedata.normalize("NFKC", str(value or ""))


def clean(value):
    return re.sub(r"\s+", " ", normalize(value)).strip()


def normalize_code(value):
    return clean(value).upper().replace(" ", "")


def first_sentence(value, limit=220):
    text = clean(value)
    if not text:
        return ""
    for sep in ("。", "！", "!", "？", "?"):
        if sep in text:
            text = text.split(sep, 1)[0] + sep
            break
    return text[:limit]


def detail_for(code, links, base_url):
    target = normalize_code(code)
    for href, anchor in links or []:
        anchor_n = normalize_code(anchor)
        if not (target and target in anchor_n):
            continue
        href = str(href or "").strip()
        if href.lower().startswith("javascript:"):
            match = re.search(r"next\(\s*['\"]([^'\"]+)['\"]", href, re.IGNORECASE)
            if match:
                return urljoin(base_url, match.group(1))
            continue
        return urljoin(base_url, href)
    return None


def make_item(
    manufacturer,
    appliance,
    code,
    source,
    title,
    evidence,
    method,
    confidence="high",
    aliases=None,
    detail_url=None,
    summary_hint=None,
    action_hint=None,
):
    item = {
        "manufacturer": manufacturer,
        "appliance": appliance,
        "code": normalize_code(code),
        "source": source,
        "page_title": clean(title),
        "evidence": clean(evidence)[:700],
        "confidence": confidence,
        "status": "needs_review",
        "extraction_method": method,
    }
    aliases = [normalize_code(x) for x in (aliases or []) if normalize_code(x)]
    aliases = [x for x in aliases if x != item["code"]]
    if aliases:
        item["aliases"] = sorted(set(aliases))
    if detail_url:
        item["detail_url"] = detail_url
    if summary_hint:
        item["summary_hint"] = clean(summary_hint)[:300]
    if action_hint:
        item["action_hint"] = clean(action_hint)[:500]
    return item


def extract_daikin(manufacturer, appliance, text, title, source_url, parts, links):
    text = clean(text)
    block_re = re.compile(
        r"▼\s*([0-9A-Z]{2,3})\s*"
        r"≪エラー内容≫\s*(.*?)\s*"
        r"≪対処方法≫\s*(.*?)"
        r"(?=\s*▼\s*[0-9A-Z]{2,3}\s*(?:≪エラー内容≫)?|$)",
        re.IGNORECASE,
    )
    result = []
    for match in block_re.finditer(text):
        code = normalize_code(match.group(1))
        if not re.fullmatch(r"[0-9A-Z]{2,3}", code):
            continue
        error_text = clean(match.group(2))
        action_text = clean(match.group(3))
        evidence = f"エラー内容: {error_text} 対処方法: {action_text}"
        result.append(
            make_item(
                manufacturer,
                appliance,
                code,
                source_url,
                title,
                evidence,
                "dedicated:daikin",
                confidence="high",
                summary_hint=first_sentence(error_text),
                action_hint=action_text[:450],
            )
        )
    return result


def extract_hitachi(manufacturer, appliance, text, title, source_url, parts, links):
    text = clean(text)
    entry_re = re.compile(r"「([^」]{1,30})」\s*（([^）]{1,100})）")
    result = []
    seen = set()
    for match in entry_re.finditer(text):
        raw_codes = re.split(r"[／/・,\s]+", normalize(match.group(1)))
        codes = []
        for raw in raw_codes:
            code = normalize_code(raw)
            if re.fullmatch(r"[CD][A-Z0-9]{1,3}", code):
                codes.append(code)
        if not codes:
            continue
        primary = codes[0]
        if primary in seen:
            continue
        seen.add(primary)
        label = clean(match.group(2))
        tail = clean(text[match.end(): match.end() + 220])
        evidence = clean(f"{match.group(0)} {tail}")
        detail_url = detail_for(primary, links, source_url)
        if not detail_url:
            for alias in codes[1:]:
                detail_url = detail_for(alias, links, source_url)
                if detail_url:
                    break
        result.append(
            make_item(
                manufacturer,
                appliance,
                primary,
                source_url,
                title,
                evidence,
                "dedicated:hitachi",
                confidence="high",
                aliases=codes[1:],
                detail_url=detail_url,
                summary_hint=label if label else first_sentence(tail),
            )
        )
    return result


def extract_panasonic(manufacturer, appliance, text, title, source_url, parts, links):
    text = clean(text)
    title_n = clean(title)
    codes = []
    for match in re.finditer(r"(?<![A-Z0-9])(?:U\d{2}|H(?:A0|\d{2}))(?![A-Z0-9])", normalize(text), re.IGNORECASE):
        code = normalize_code(match.group(0))
        if code not in codes:
            codes.append(code)

    result = []
    for code in codes:
        position = normalize(text).upper().find(code)
        left = max(0, position - 120) if position >= 0 else 0
        right = min(len(text), position + 360) if position >= 0 else 500
        evidence = clean(text[left:right])
        is_detail = bool(
            re.search(rf"エラー表示[「『\"]?{re.escape(code)}[」』\"]?", normalize(title_n), re.IGNORECASE)
        )
        detail_url = source_url if is_detail else detail_for(code, links, source_url)
        summary_hint = ""
        if is_detail and position >= 0:
            after = clean(text[position + len(code): position + len(code) + 300])
            summary_hint = first_sentence(after)

        result.append(
            make_item(
                manufacturer,
                appliance,
                code,
                source_url,
                title,
                evidence,
                "dedicated:panasonic",
                confidence="high" if is_detail else "medium",
                detail_url=detail_url,
                summary_hint=summary_hint or None,
            )
        )
    return result


def extract_sharp(manufacturer, appliance, text, title, source_url, parts, links):
    text_n = clean(text)
    title_n = clean(title)
    found = []

    def add(code):
        code_n = normalize_code(code)
        if code_n and code_n not in found:
            found.append(code_n)

    for match in re.finditer(r"「(\d{1,2}-\d)」\s*を表示", normalize(text), re.IGNORECASE):
        add(match.group(1))

    for match in re.finditer(r"(?<![A-Z0-9-])([A-Z]{1,2}[0-9]?|\d{1,2}-\d)を表示する", normalize(title_n), re.IGNORECASE):
        add(match.group(1))

    if "英数字エラー" in title_n or "英数字エラー" in text_n:
        for part in parts or []:
            candidate = normalize_code(part)
            if re.fullmatch(r"[A-Z]{1,2}[0-9]?", candidate) and len(candidate) <= 3:
                add(candidate)

    result = []
    for code in found:
        pos = normalize(text_n).upper().find(code)
        evidence = clean(text_n[max(0, pos - 100): min(len(text_n), pos + 380)]) if pos >= 0 else title_n
        is_detail = bool(
            re.search(rf"(?<![A-Z0-9-]){re.escape(code)}を表示する", normalize(title_n), re.IGNORECASE)
        )
        result.append(
            make_item(
                manufacturer,
                appliance,
                code,
                source_url,
                title,
                evidence,
                "dedicated:sharp",
                confidence="high",
                detail_url=source_url if is_detail else detail_for(code, links, source_url),
                summary_hint=first_sentence(evidence) if is_detail else None,
            )
        )
    return result


def extract_for(manufacturer, appliance, text, title, source_url, parts=None, links=None):
    extractors = {
        "ダイキン": extract_daikin,
        "パナソニック": extract_panasonic,
        "日立": extract_hitachi,
        "シャープ": extract_sharp,
    }
    extractor = extractors.get(manufacturer)
    if not extractor:
        return None
    return extractor(manufacturer, appliance, text, title, source_url, parts or [], links or [])


def should_follow(manufacturer, href, anchor):
    anchor_n = clean(anchor)

    if manufacturer == "ダイキン":
        return False

    if manufacturer == "パナソニック":
        if "/app/answers/detail/" not in href:
            return False
        return bool(
            "エラー" in anchor_n
            or re.search(r"(?<![A-Z0-9])(?:U\d{2}|H(?:A0|\d{2}))(?![A-Z0-9])", normalize(anchor_n), re.IGNORECASE)
        )

    if manufacturer == "日立":
        if "/support/wash/q_a/" not in href:
            return False
        return bool(
            "お知らせ" in anchor_n
            or re.search(r"(?<![A-Z0-9])[CD][A-Z0-9]{1,3}(?![A-Z0-9])", normalize(anchor_n), re.IGNORECASE)
        )

    if manufacturer == "シャープ":
        if "/trouble_check/div/air_con/navi/" not in href:
            return False
        return bool(
            "英数字" in anchor_n
            or "エラー" in anchor_n
            or re.search(r"\b(?:[A-Z]{1,2}[0-9]?|\d{1,2}-\d)\b", normalize(anchor_n), re.IGNORECASE)
        )

    return None
