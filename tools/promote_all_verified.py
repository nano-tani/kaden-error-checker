#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# Importing the extension applies its strict validation/action/scope patches to core.
import promote_verified_extended  # noqa: F401
import promote_verified as core

ROOT = Path(__file__).resolve().parents[1]
CATEGORY_REGISTRY = ROOT / "data" / "category_source_registry.json"

core.DEDICATED.update({
    "dedicated:official-table",
    "dedicated:rinnai-faq",
    "dedicated:noritz-faq",
    "dedicated:haier-extra",
})


def combined_rules() -> dict[tuple[str, str], list[str]]:
    rules = core.registry_rules()
    if CATEGORY_REGISTRY.exists():
        rows = json.loads(CATEGORY_REGISTRY.read_text(encoding="utf-8"))
        for row in rows:
            if row.get("enabled", True) is False:
                continue
            manufacturer = core.clean(row.get("manufacturer"))
            appliance = core.clean(row.get("appliance"))
            domains = list(row.get("allowed_domains") or [])
            if manufacturer and appliance and domains:
                rules[(manufacturer, appliance)] = domains
    # Consumer-facing FAQ/category sources use one official domain across multiple product scopes.
    rules[("ノーリツ", "*")] = ["faq.noritz.co.jp"]
    rules[("ハイアール", "*")] = ["www.haier.com", "haier.com"]
    return rules


def domains_for(rules: dict[tuple[str, str], list[str]], manufacturer: str, appliance: str) -> list[str]:
    return rules.get((manufacturer, appliance), rules.get((manufacturer, "*"), []))


def main() -> None:
    candidates = json.loads(core.CANDIDATES.read_text(encoding="utf-8"))
    previous_records = json.loads(core.PUBLISHED.read_text(encoding="utf-8"))
    dedicated_count = sum(core.clean(x.get("extraction_method")) in core.DEDICATED for x in candidates)
    if len(candidates) < core.MIN_CANDIDATES_FOR_REBUILD or dedicated_count < core.MIN_DEDICATED_FOR_REBUILD:
        raise RuntimeError(
            f"candidate safety threshold failed: total={len(candidates)} dedicated={dedicated_count}; refusing to rebuild production DB"
        )

    manual_records = [x for x in previous_records if core.clean(x.get("verification_method")) != core.AUTO_METHOD]
    previous_auto = [x for x in previous_records if core.clean(x.get("verification_method")) == core.AUTO_METHOD]
    records = list(manual_records)
    rules = combined_rules()
    existing = core.published_keys(manual_records)
    rejected = Counter()

    by_base_code: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for item in candidates:
        manufacturer = core.clean(item.get("manufacturer"))
        appliance = core.clean(item.get("appliance"))
        domains = domains_for(rules, manufacturer, appliance)
        if not domains:
            rejected["no_registry_rule"] += 1
            continue
        valid, reason = core.candidate_valid(item, domains)
        if not valid:
            rejected[reason] += 1
            continue
        key = (manufacturer, appliance, core.norm_code(item.get("code")))
        by_base_code[key].append(item)

    eligible: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for (manufacturer, appliance, code), items in by_base_code.items():
        scopes = {core.infer_scope(item) for item in items if core.infer_scope(item)}
        for item in items:
            scope = core.infer_scope(item)
            if scopes and not scope:
                rejected["unscoped_shadowed_by_specific_scope"] += 1
                continue
            eligible[(manufacturer, appliance, code, scope)].append(item)

    promoted = []
    today = datetime.now(core.JST).date().isoformat()
    manufacturer_counts = Counter()
    appliance_counts = Counter()

    # Intentionally no count ceiling: every candidate that passes strict validation is promoted.
    for key in sorted(eligible):
        manufacturer, base_appliance, code, scope = key
        group = eligible[key]
        domains = domains_for(rules, manufacturer, base_appliance)
        methods = {core.clean(x.get("extraction_method")) for x in group}
        if len(methods) != 1:
            rejected["mixed_methods"] += 1
            continue
        if core.summaries_conflict(group):
            rejected["conflicting_summaries"] += 1
            continue

        summary = core.choose_summary(group)
        if not core.summary_is_specific(summary, code):
            rejected["summary_not_specific"] += 1
            continue

        source = core.choose_source(group, domains)
        if not source:
            rejected["no_official_source"] += 1
            continue

        appliance = core.scoped_appliance(manufacturer, base_appliance, scope)
        output_key = (manufacturer, appliance, code)
        if output_key in existing:
            rejected["manual_published_key_collision"] += 1
            continue

        aliases = sorted({
            core.norm_code(alias)
            for item in group
            for alias in (item.get("aliases") or [])
            if core.norm_code(alias)
        })
        aliases = [
            alias for alias in aliases
            if alias != code and (manufacturer, appliance, alias) not in existing
        ]
        actions = core.choose_actions(group)

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
            "verification_method": core.AUTO_METHOD,
            "verification_source_method": next(iter(methods)),
        })
        if scope:
            record["scope"] = scope
        records.append(record)
        existing.add(output_key)
        for alias in aliases:
            existing.add((manufacturer, appliance, alias))
        manufacturer_counts[manufacturer] += 1
        appliance_counts[f"{manufacturer} / {appliance}"] += 1
        promoted.append({
            "manufacturer": manufacturer,
            "appliance": appliance,
            "code": code,
            "scope": scope or None,
            "source": source,
            "method": next(iter(methods)),
        })

    records.sort(key=lambda x: (
        core.clean(x.get("manufacturer")), core.clean(x.get("appliance")), core.norm_code(x.get("code"))
    ))
    core.PUBLISHED.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "verified_date": today,
        "policy": "strict dedicated official sources only; generic candidates are never auto-promoted; scoped conflicts are separated or rejected; no promotion count ceiling",
        "promotion_mode": "unlimited",
        "candidate_count": len(candidates),
        "dedicated_candidate_count": dedicated_count,
        "previous_published_count": len(previous_records),
        "manual_retained_count": len(manual_records),
        "previous_auto_count": len(previous_auto),
        "auto_rebuilt_count": len(promoted),
        "published_after": len(records),
        "promoted_by_manufacturer": dict(sorted(manufacturer_counts.items())),
        "promoted_by_category": dict(sorted(appliance_counts.items())),
        "rejected": dict(sorted(rejected.items())),
        "promoted": promoted,
    }
    core.REPORT.parent.mkdir(parents=True, exist_ok=True)
    core.REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"previous published: {report['previous_published_count']}")
    print(f"manual retained: {report['manual_retained_count']}")
    print(f"previous auto: {report['previous_auto_count']}")
    print(f"strict auto rebuilt: {report['auto_rebuilt_count']}")
    print(f"published after: {report['published_after']}")
    print("promotion mode: unlimited")
    print(f"promoted by manufacturer: {json.dumps(report['promoted_by_manufacturer'], ensure_ascii=False)}")
    print(f"rejected: {json.dumps(report['rejected'], ensure_ascii=False)}")


if __name__ == "__main__":
    main()
