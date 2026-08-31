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
MIN_CANDIDATES_FOR_REBUILD = int(os.environ.get("MIN_CANDIDATES_FOR_REBUILD", "300"))
MIN_DEDICATED_FOR_REBUILD = int(os.environ.get("MIN_DEDICATED_FOR_REBUILD", "180"))
AUTO_METHOD = "automatic_strict_official"
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
    raw = [clean(x).strip("・") for x in re.split(r"(?<=[。！？!?])\s*|[●■◆]\s*", text)]
    raw = [x for x in raw if x]
    merged: list[str] = []
    buffer = ""
    for chunk in raw:
        if buffer:
            buffer = clean(f"{buffer} {chunk}")
        else:
            buffer = chunk
        if buffer.count("(") > buffer.count(")"):
            continue
        merged.append(buffer)
        buffer = ""
    if buffer:
        merged.append(buffer)
    return merged


def daikin_summary(item: dict) -> str:
    evidence = clean(item.get("evidence"))
    match = re.search(r"エラー内容:\s*(.*?)\s*対処方法:", evidence)
    if not match:
        return ""
    text = clean(match.group(1))
    if "エラーが確定していない" in text or "正しく診断できません" in text:
        return ""
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


def item_summary(item: dict) -> str:
    method = clean(item.get("extraction_method"))
    if method == "dedicated:daikin":
        return daikin_summary(item)
    if method == "dedicated:hitachi":
        return hitachi_summary([item])
    return generic_dedicated_summary(item)


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


def summaries_conflict(group: list[dict]) -> bool:
    values = []
    for item in group:
        value = clean(item_summary(item)).rstrip("。.")
        if not value:
            continue
        canonical = re.sub(r"[\s・、。,:：()（）\-]", "", value)
        if canonical and canonical not in values:
            values.append(canonical)
    if len(values) <= 1:
        return False
    for i, left in enumerate(values):
        for right in values[i + 1:]:
            if left in right or right in left:
                continue
            return True
    return False


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
        if chunk.startswith(("を", "について", "対処方法")):
            continue
        if not 5 <= len(chunk) <= 140:
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


def infer_scope(item: dict) -> str:
    manufacturer = clean(item.get("manufacturer"))
    text = clean(f"{item.get('page_title', '')} {item.get('evidence', '')}")
    if manufacturer == "日立":
        if "ドラム式" in text:
            return "ドラム式"
        if "タテ型" in text or "縦型" in text:
            return "タテ型"
    return ""


def scoped_appliance(manufacturer: str, appliance: str, scope: str) -> str:
    if manufacturer == "日立" and scope == "ドラム式":
        return "ドラム式洗濯機・洗濯乾燥機"
    if manufacturer == "日立" and scope == "タテ型":
        return "タテ型洗濯機・洗濯乾燥機"
    return appliance


def main() -> None:
    candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    previous_records = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    dedicated_count = sum(clean(x.get("extraction_method")) in DEDICATED for x in candidates)
    if len(candidates) < MIN_CANDIDATES_FOR_REBUILD or dedicated_count < MIN_DEDICATED_FOR_REBUILD:
        raise RuntimeError(
            f"candidate safety threshold failed: total={len(candidates)} dedicated={dedicated_count}; refusing to rebuild production DB"
        )

    manual_records = [x for x in previous_records if clean(x.get("verification_method")) != AUTO_METHOD]
    previous_auto = [x for x in previous_records if clean(x.get("verification_method")) == AUTO_METHOD]
    records = list(manual_records)
    rules = registry_rules()
    existing = published_keys(manual_records)
    rejected = Counter()

    by_base_code: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
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
        by_base_code[key].append(item)

    eligible: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for (manufacturer, appliance, code), items in by_base_code.items():
        scopes = {infer_scope(item) for item in items if infer_scope(item)}
        for item in items:
            scope = infer_scope(item)
            if scopes and not scope:
                rejected["unscoped_shadowed_by_specific_scope"] += 1
                continue
            eligible[(manufacturer, appliance, code, scope)].append(item)

    promoted = []
    today = datetime.now(JST).date().isoformat()
    manufacturer_counts = Counter()

    for key in sorted(eligible):
        if len(promoted) >= AUTO_PROMOTE_LIMIT:
            rejected["run_limit"] += 1
            continue
        manufacturer, base_appliance, code, scope = key
        group = eligible[key]
        domains = rules[(manufacturer, base_appliance)]
        methods = {clean(x.get("extraction_method")) for x in group}
        if len(methods) != 1:
            rejected["mixed_methods"] += 1
            continue
        if summaries_conflict(group):
            rejected["conflicting_summaries"] += 1
            continue

        summary = choose_summary(group)
        if not summary_is_specific(summary, code):
            rejected["summary_not_specific"] += 1
            continue

        source = choose_source(group, domains)
        if not source:
            rejected["no_official_source"] += 1
            continue

        appliance = scoped_appliance(manufacturer, base_appliance, scope)
        output_key = (manufacturer, appliance, code)
        if output_key in existing:
            rejected["manual_published_key_collision"] += 1
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
            "verification_method": AUTO_METHOD,
        })
        if scope:
            record["scope"] = scope
        records.append(record)
        existing.add(output_key)
        for alias in aliases:
            existing.add((manufacturer, appliance, alias))
        manufacturer_counts[manufacturer] += 1
        promoted.append({
            "manufacturer": manufacturer,
            "appliance": appliance,
            "code": code,
            "scope": scope or None,
            "source": source,
            "method": next(iter(methods)),
        })

    records.sort(key=lambda x: (clean(x.get("manufacturer")), clean(x.get("appliance")), norm_code(x.get("code"))))
    PUBLISHED.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "verified_date": today,
        "policy": "strict dedicated official sources only; generic candidates are never auto-promoted; scoped conflicts are separated or rejected",
        "candidate_count": len(candidates),
        "dedicated_candidate_count": dedicated_count,
        "previous_published_count": len(previous_records),
        "manual_retained_count": len(manual_records),
        "previous_auto_count": len(previous_auto),
        "auto_rebuilt_count": len(promoted),
        "published_after": len(records),
        "auto_promote_limit": AUTO_PROMOTE_LIMIT,
        "promoted_by_manufacturer": dict(sorted(manufacturer_counts.items())),
        "rejected": dict(sorted(rejected.items())),
        "promoted": promoted,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"previous published: {report['previous_published_count']}")
    print(f"manual retained: {report['manual_retained_count']}")
    print(f"previous auto: {report['previous_auto_count']}")
    print(f"strict auto rebuilt: {report['auto_rebuilt_count']}")
    print(f"published after: {report['published_after']}")
    print("promoted by manufacturer:", json.dumps(report["promoted_by_manufacturer"], ensure_ascii=False, sort_keys=True))
    print("rejected:", json.dumps(report["rejected"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
