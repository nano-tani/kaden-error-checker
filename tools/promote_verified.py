#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "review" / "candidates.json"
PUBLISHED = ROOT / "data" / "errors.json"
REGISTRY = ROOT / "data" / "source_registry.json"
REPORT = ROOT / "review" / "promotion-report.json"

JST = timezone(timedelta(hours=9))
AUTO_PROMOTE_LIMIT = int(os.environ.get("AUTO_PROMOTE_LIMIT", "10000"))
DEDICATED = {
    "dedicated:daikin",
    "dedicated:hitachi",
    "dedicated:panasonic",
    "dedicated:sharp",
}


def norm(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or ""))


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", norm(value)).strip()


def norm_code(value: object) -> str:
    return clean(value).upper().replace(" ", "")


def host_allowed(url: str, domains: list[str]) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower()
    return bool(host) and any(host == d.lower() or host.endswith("." + d.lower()) for d in domains)


def registry_rules() -> dict[tuple[str, str], list[str]]:
    rows = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return {
        (clean(row.get("manufacturer")), clean(row.get("appliance"))): list(row.get("allowed_domains") or [])
        for row in rows
        if row.get("enabled", True) is not False
    }


def published_keys(records: list[dict]) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for record in records:
        base = (clean(record.get("manufacturer")), clean(record.get("appliance")))
        for code in [record.get("code"), *(record.get("aliases") or [])]:
            code_n = norm_code(code)
            if code_n:
                keys.add((*base, code_n))
    return keys


def sentence_chunks(value: str) -> list[str]:
    text = clean(value)
    if not text:
        return []
    chunks = re.split(r"(?<=[。！？!?])\s*|[●■◆]\s*", text)
    return [clean(x).strip("・") for x in chunks if clean(x).strip("・")]


def daikin_summary(item: dict) -> str:
    evidence = clean(item.get("evidence"))
    match = re.search(r"エラー内容:\s*(.*?)\s*対処方法:", evidence)
    if not match:
        return ""
    text = clean(match.group(1))
    if "エラーが確定していない" in text or "正しく診断できません" in text:
        return ""
    # The first sentence is often only "室内機のエラーです。"; preserve the
    # following concrete cause instead of publishing a thin generic sentence.
    text = re.sub(r"^(?:室内機|室外機)のエラーです[。.]\s*", "", text)
    chunks = sentence_chunks(text)
    summary = " ".join(chunks[:2]) if chunks else text
    return clean(summary)[:260]


def hitachi_summary(group: list[dict]) -> str:
    hints = []
    for item in group:
        hint = clean(item.get("summary_hint"))
        if not hint:
            continue
        if "表示部に" in hint and ("表示され" in hint or "表示します" in hint):
            continue
        if hint.count("(") != hint.count(")"):
            continue
        if 3 <= len(hint) <= 160:
            hints.append(hint)
    if hints:
        return min(hints, key=len)

    for item in group:
        evidence = clean(item.get("evidence"))
        match = re.search(r"「[^」]+」\s*\(([^)]{3,120})\)", evidence)
        if match:
            value = clean(match.group(1))
            if value.count("(") == value.count(")"):
                return value[:220]
    return ""


def generic_dedicated_summary(item: dict) -> str:
    hint = clean(item.get("summary_hint"))
    if not hint or len(hint) < 5:
        return ""
    if "表示部に" in hint and ("表示され" in hint or "表示します" in hint):
        return ""
    if hint.count("(") != hint.count(")"):
        return ""
    return hint[:260]


def choose_summary(group: list[dict]) -> str:
    method = clean(group[0].get("extraction_method"))
    if method == "dedicated:daikin":
        return daikin_summary(group[0])
    if method == "dedicated:hitachi":
        return hitachi_summary(group)

    candidates = [generic_dedicated_summary(x) for x in group]
    candidates = [x for x in candidates if x]
    return min(candidates, key=len) if candidates else ""


def summary_is_specific(summary: str, code: str) -> bool:
    text = clean(summary)
    if not 5 <= len(text) <= 260:
        return False
    if norm_code(text) == code:
        return False
    noisy = ("HTTP", "HTML", "FAQ", "KB", "PDF")
    if any(token in text.upper() for token in noisy):
        return False
    generic_only = {
        "室内機のエラーです。",
        "室外機のエラーです。",
        "エラーが表示されます。",
        "異常が発生しています。",
    }
    return text not in generic_only


def extract_actions_from_text(value: str) -> list[str]:
    text = clean(value)
    if not text:
        return []
    text = re.sub(r"当社へのお申し込みはこちら.*$", "", text)
    text = re.sub(r"取扱説明書はこちらから検索できます[。.]?", "", text)
    chunks = sentence_chunks(text)
    useful_words = (
        "確認", "掃除", "清掃", "停止", "抜", "入れ", "再運転", "調整", "閉", "外", "取り除",
        "相談", "依頼", "点検", "修理", "お手入れ", "試", "交換", "水栓", "ホース", "フィルター",
    )
    result = []
    for chunk in chunks:
        chunk = clean(chunk).strip("・")
        if not 5 <= len(chunk) <= 180:
            continue
        if not any(word in chunk for word in useful_words):
            continue
        if chunk not in result:
            result.append(chunk)
        if len(result) >= 5:
            break
    return result


def choose_actions(group: list[dict]) -> list[str]:
    for item in group:
        actions = extract_actions_from_text(clean(item.get("action_hint")))
        if actions:
            return actions

    # Prefer evidence from an individual detail page when available.
    ordered = sorted(
        group,
        key=lambda x: (
            str(x.get("source")) != str(x.get("detail_url")),
            -len(clean(x.get("evidence"))),
        ),
    )
    for item in ordered:
        evidence = clean(item.get("evidence"))
        if "対処方法" in evidence:
            actions = extract_actions_from_text(evidence.split("対処方法", 1)[1])
            if actions:
                return actions

    return ["メーカー公式の案内で対象機種と対処方法を確認する"]


def choose_source(group: list[dict], domains: list[str]) -> str:
    for item in group:
        detail = str(item.get("detail_url") or "").strip()
        if detail and host_allowed(detail, domains):
            return detail
    for item in group:
        source = str(item.get("source") or "").strip()
        if source and host_allowed(source, domains):
            return source
    return ""


def candidate_valid(item: dict, domains: list[str]) -> tuple[bool, str]:
    if clean(item.get("extraction_method")) not in DEDICATED:
        return False, "not_dedicated"
    if clean(item.get("confidence")) != "high":
        return False, "not_high_confidence"
    if clean(item.get("status")) != "needs_review":
        return False, "unexpected_status"
    if item.get("already_published"):
        return False, "already_published"
    source = str(item.get("source") or "")
    detail = str(item.get("detail_url") or "")
    if not host_allowed(source, domains) and not host_allowed(detail, domains):
        return False, "non_official_domain"
    code = norm_code(item.get("code"))
    if not code or len(code) > 7:
        return False, "bad_code"
    haystack = norm_code(f"{item.get('page_title', '')} {item.get('evidence', '')}")
    if code not in haystack and clean(item.get("extraction_method")) != "dedicated:daikin":
        return False, "code_not_in_evidence"
    return True, "ok"


def main() -> None:
    candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    records = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    rules = registry_rules()
    existing = published_keys(records)
    rejected = Counter()
    eligible: dict[tuple[str, str, str], list[dict]] = defaultdict(list)

    for item in candidates:
        base = (clean(item.get("manufacturer")), clean(item.get("appliance")))
        domains = rules.get(base, [])
        if not domains:
            rejected["no_registry_rule"] += 1
            continue
        valid, reason = candidate_valid(item, domains)
        if not valid:
            rejected[reason] += 1
            continue
        key = (*base, norm_code(item.get("code")))
        if key in existing:
            rejected["published_key_collision"] += 1
            continue
        eligible[key].append(item)

    promoted = []
    today = datetime.now(JST).date().isoformat()

    for key in sorted(eligible):
        if len(promoted) >= AUTO_PROMOTE_LIMIT:
            rejected["run_limit"] += 1
            continue
        manufacturer, appliance, code = key
        group = eligible[key]
        domains = rules[(manufacturer, appliance)]
        methods = {clean(x.get("extraction_method")) for x in group}
        if len(methods) != 1:
            rejected["mixed_methods"] += 1
            continue

        summary = choose_summary(group)
        if not summary_is_specific(summary, code):
            rejected["summary_not_specific"] += 1
            continue

        source = choose_source(group, domains)
        if not source:
            rejected["no_official_source"] += 1
            continue

        aliases = sorted({norm_code(alias) for item in group for alias in (item.get("aliases") or []) if norm_code(alias)})
        aliases = [alias for alias in aliases if alias != code and (manufacturer, appliance, alias) not in existing]
        actions = choose_actions(group)

        record = {
            "manufacturer": manufacturer,
            "appliance": appliance,
            "code": code,
        }
        if aliases:
            record["aliases"] = aliases
        record.update({
            "summary": summary,
            "actions": actions,
            "source": source,
            "verified": today,
            "verification_method": "automatic_strict_official",
        })
        records.append(record)
        existing.add(key)
        for alias in aliases:
            existing.add((manufacturer, appliance, alias))
        promoted.append({
            "manufacturer": manufacturer,
            "appliance": appliance,
            "code": code,
            "source": source,
            "method": next(iter(methods)),
        })

    records.sort(key=lambda x: (clean(x.get("manufacturer")), clean(x.get("appliance")), norm_code(x.get("code"))))
    PUBLISHED.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "verified_date": today,
        "policy": "strict dedicated official sources only; generic candidates are never auto-promoted",
        "candidate_count": len(candidates),
        "published_before": len(records) - len(promoted),
        "promoted_count": len(promoted),
        "published_after": len(records),
        "auto_promote_limit": AUTO_PROMOTE_LIMIT,
        "rejected": dict(sorted(rejected.items())),
        "promoted": promoted,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"published before: {report['published_before']}")
    print(f"strict auto-promoted: {report['promoted_count']}")
    print(f"published after: {report['published_after']}")
    print("rejected:", json.dumps(report["rejected"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
