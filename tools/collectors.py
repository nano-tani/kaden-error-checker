#!/usr/bin/env python3
import re
import unicodedata
from urllib.parse import urljoin

DEDICATED_MANUFACTURERS = {"ダイキン", "パナソニック", "日立", "シャープ", "ハイアール", "AQUA"}


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
    token_re = re.compile(rf"(?<![A-Z0-9]){re.escape(target)}(?![A-Z0-9])", re.IGNORECASE)
    for href, anchor in links or []:
        anchor_n = normalize(anchor).upper()
        if not (target and token_re.search(anchor_n)):
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
    scope_hint=None,
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
    if scope_hint:
        item["scope_hint"] = clean(scope_hint)
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
        result.append(make_item(
            manufacturer, appliance, code, source_url, title,
            f"エラー内容: {error_text} 対処方法: {action_text}",
            "dedicated:daikin", confidence="high",
            summary_hint=first_sentence(error_text), action_hint=action_text[:450],
        ))
    return result


def hitachi_codes(raw):
    codes = []
    for token in re.split(r"[/・,\s]+", normalize(raw)):
        code = normalize_code(token)
        if re.fullmatch(r"[CD][A-Z0-9]{0,3}", code):
            codes.append(code)
    return list(dict.fromkeys(codes))


def extract_hitachi(manufacturer, appliance, text, title, source_url, parts, links):
    text = clean(text)
    title_n = clean(title)
    entry_re = re.compile(r"「([^」]{1,30})」\s*\(([^)]{1,100})\)")
    result = []
    seen = set()
    for match in entry_re.finditer(text):
        codes = hitachi_codes(match.group(1))
        if not codes:
            continue
        primary = codes[0]
        if primary in seen:
            continue
        seen.add(primary)
        label = clean(match.group(2))
        tail = clean(text[match.end(): match.end() + 260])
        detail_url = detail_for(primary, links, source_url)
        if not detail_url:
            for alias in codes[1:]:
                detail_url = detail_for(alias, links, source_url)
                if detail_url:
                    break
        result.append(make_item(
            manufacturer, appliance, primary, source_url, title,
            clean(f"{match.group(0)} {tail}"), "dedicated:hitachi",
            confidence="high", aliases=codes[1:], detail_url=detail_url,
            summary_hint=label if label else first_sentence(tail),
        ))
    if not result:
        quoted = re.findall(r"「([^」]{1,30})」", title_n)
        title_codes = []
        for raw in quoted:
            title_codes.extend(hitachi_codes(raw))
        title_codes = list(dict.fromkeys(title_codes))
        if title_codes:
            result.append(make_item(
                manufacturer, appliance, title_codes[0], source_url, title,
                clean(text[:700]), "dedicated:hitachi", confidence="high",
                aliases=title_codes[1:], detail_url=source_url,
                summary_hint=first_sentence(title_n),
            ))
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
        is_detail = bool(re.search(rf"エラー表示[「『\"]?{re.escape(code)}[」』\"]?", normalize(title_n), re.IGNORECASE))
        detail_url = source_url if is_detail else detail_for(code, links, source_url)
        summary_hint = ""
        if is_detail and position >= 0:
            summary_hint = first_sentence(clean(text[position + len(code): position + len(code) + 300]))
        result.append(make_item(
            manufacturer, appliance, code, source_url, title, evidence,
            "dedicated:panasonic", confidence="high" if is_detail else "medium",
            detail_url=detail_url, summary_hint=summary_hint or None,
        ))
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
        is_detail = bool(re.search(rf"(?<![A-Z0-9-]){re.escape(code)}を表示する", normalize(title_n), re.IGNORECASE))
        result.append(make_item(
            manufacturer, appliance, code, source_url, title, evidence,
            "dedicated:sharp", confidence="high",
            detail_url=source_url if is_detail else detail_for(code, links, source_url),
            summary_hint=first_sentence(evidence) if is_detail else None,
        ))
    return result


def scope_from_title(title):
    t = clean(title)
    if "ドラム" in t:
        return "ドラム式"
    if "縦型" in t or "タテ型" in t:
        return "タテ型"
    return ""


def simple_codes(value):
    prefix = clean(value).split("および", 1)[0]
    found = []
    for match in re.finditer(r"(?<![A-Za-z0-9])([A-Za-z]{1,3}\d{0,3})(?![A-Za-z0-9])", prefix):
        code = normalize_code(match.group(1))
        if len(code) == 1 and not re.search(r"\d", code):
            continue
        if code not in found:
            found.append(code)
    return found


def extract_haier(manufacturer, appliance, text, title, source_url, parts, links):
    text_n = clean(text)
    scope = scope_from_title(title)
    result = []
    for block in re.split(r"\s*・", text_n):
        block = clean(block)
        if "：" not in block and ":" not in block:
            continue
        header, body = re.split(r"[：:]", block, maxsplit=1)
        if len(header) > 120:
            continue
        codes = simple_codes(header)
        if not codes:
            continue
        body = clean(body)
        if "⇒" in body:
            summary, action = body.split("⇒", 1)
        else:
            summary, action = body, ""
        summary = clean(summary)
        action = clean(action)
        if "故障ではありません" in body or any(x in summary for x in ("運転終了のお知らせ", "チャイルドロック中")):
            continue
        for code in codes:
            result.append(make_item(
                manufacturer, appliance, code, source_url, title, block,
                "dedicated:haier", confidence="high",
                summary_hint=summary, action_hint=action, scope_hint=scope,
            ))
    return result


def table_tokens(section):
    token_re = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,3}\d{0,3})(?![A-Za-z0-9])")
    return list(token_re.finditer(section))


def extract_aqua_section(manufacturer, appliance, section, title, source_url, scope):
    section = clean(section)
    matches = table_tokens(section)
    result = []
    i = 0
    delimiter_re = re.compile(r"^[\s、，,/・]*(?:など)?[\s、，,/・]*$")
    while i < len(matches):
        group = [matches[i]]
        j = i
        while j + 1 < len(matches):
            between = clean(section[matches[j].end():matches[j + 1].start()])
            if delimiter_re.fullmatch(between):
                group.append(matches[j + 1])
                j += 1
            else:
                break
        desc_end = matches[j + 1].start() if j + 1 < len(matches) else len(section)
        desc = clean(section[group[-1].end():desc_end])
        codes = [normalize_code(m.group(1)) for m in group]
        codes = [c for c in codes if c and not (len(c) == 1 and c.isalpha())]
        if desc and codes:
            summary = first_sentence(desc)
            action = clean(desc[len(summary):]) if desc.startswith(summary) else desc
            if not any(x in summary for x in ("運転していない", "チャイルドロック", "凍結防止", "クールダウン", "水位が高い")):
                for code in codes:
                    result.append(make_item(
                        manufacturer, appliance, code, source_url, title,
                        f"{code} {desc}", "dedicated:aqua", confidence="high",
                        summary_hint=summary, action_hint=action, scope_hint=scope,
                    ))
        i = j + 1
    return result


def extract_aqua(manufacturer, appliance, text, title, source_url, parts, links):
    text_n = clean(text)
    vertical_marker = "全自動(縦型)洗濯機のエラーコード一覧表"
    drum_marker = "ドラム型洗濯乾燥機のエラーコード一覧表"
    end_marker = "2.自分で試せる"
    vpos = text_n.find(vertical_marker)
    dpos = text_n.find(drum_marker)
    epos = text_n.find(end_marker)
    result = []
    if vpos >= 0 and dpos > vpos:
        result.extend(extract_aqua_section(manufacturer, appliance, text_n[vpos + len(vertical_marker):dpos], title, source_url, "タテ型"))
    if dpos >= 0:
        end = epos if epos > dpos else len(text_n)
        result.extend(extract_aqua_section(manufacturer, appliance, text_n[dpos + len(drum_marker):end], title, source_url, "ドラム式"))
    return result


def extract_for(manufacturer, appliance, text, title, source_url, parts=None, links=None):
    extractors = {
        "ダイキン": extract_daikin,
        "パナソニック": extract_panasonic,
        "日立": extract_hitachi,
        "シャープ": extract_sharp,
        "ハイアール": extract_haier,
        "AQUA": extract_aqua,
    }
    extractor = extractors.get(manufacturer)
    if not extractor:
        return None
    return extractor(manufacturer, appliance, text, title, source_url, parts or [], links or [])


def should_follow(manufacturer, href, anchor):
    anchor_n = clean(anchor)
    if manufacturer in {"ダイキン", "ハイアール", "AQUA"}:
        return False
    if manufacturer == "パナソニック":
        if "/app/answers/detail/" not in href:
            return False
        return bool("エラー" in anchor_n or re.search(r"(?<![A-Z0-9])(?:U\d{2}|H(?:A0|\d{2}))(?![A-Z0-9])", normalize(anchor_n), re.IGNORECASE))
    if manufacturer == "日立":
        if "/support/wash/q_a/" not in href:
            return False
        return bool("お知らせ" in anchor_n or re.search(r"(?<![A-Z0-9])[CD][A-Z0-9]{1,3}(?![A-Z0-9])", normalize(anchor_n), re.IGNORECASE))
    if manufacturer == "シャープ":
        if "/trouble_check/div/air_con/navi/" not in href:
            return False
        return bool("英数字" in anchor_n or "エラー" in anchor_n or re.search(r"\b(?:[A-Z]{1,2}[0-9]?|\d{1,2}-\d)\b", normalize(anchor_n), re.IGNORECASE))
    return None
