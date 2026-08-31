#!/usr/bin/env python3
"""Strict promotion extension for additional dedicated manufacturer collectors.

This wrapper keeps the core promotion policy in promote_verified.py and adds
scope/status/noise handling for Haier and AQUA before running the same
production rebuild. Generic candidates remain ineligible for publication.
"""
from __future__ import annotations

import re

import promote_verified as core

core.DEDICATED.update({"dedicated:haier", "dedicated:aqua"})

_original_candidate_valid = core.candidate_valid
_original_infer_scope = core.infer_scope
_original_scoped_appliance = core.scoped_appliance

STATUS_TERMS = (
    "故障ではありません",
    "運転終了のお知らせ",
    "洗剤補充のお知らせ",
    "チャイルドロック",
    "凍結防止",
    "残水排水",
    "クールダウン",
    "交互に表示",
    "ドラムの回転中",
    "乾燥中に一時停止",
    "水位が高い",
    "運転していない(一時停止)",
)

AQUA_NOISE_TERMS = (
    "ALL rights reserved",
    "【一覧表】",
    "エラーコードと原因・対処法",
    "どうしても直らない場合",
    "電源リセットと修理依頼",
    "まとめ",
    "エラー表示には",
    "縦型とドラム式に分けて",
    "エラーコードを紹介します",
)


def candidate_valid(item: dict, domains: list[str]) -> tuple[bool, str]:
    valid, reason = _original_candidate_valid(item, domains)
    if not valid:
        return valid, reason

    method = core.clean(item.get("extraction_method"))
    code = core.norm_code(item.get("code"))
    summary = core.clean(item.get("summary_hint"))
    evidence = core.clean(item.get("evidence"))
    text = core.clean(f"{summary} {item.get('action_hint', '')} {evidence}")

    if method in {"dedicated:haier", "dedicated:aqua"}:
        if any(term in text for term in STATUS_TERMS):
            return False, "status_not_error"
        # These manufacturers' official washer error tables use E/F/U families
        # for actionable error codes. This also rejects footer/prose tokens.
        if not re.fullmatch(r"[EFU][A-Z0-9]{0,3}", code):
            return False, "unexpected_code_family"
        if not 5 <= len(summary) <= 180:
            return False, "weak_structured_summary"

    if method == "dedicated:aqua":
        if any(term in text for term in AQUA_NOISE_TERMS):
            return False, "aqua_table_noise"
        if summary[0] in ")]}】。、,.・:-" or evidence.startswith(("ALL ", "CO ")):
            return False, "aqua_table_noise"

    return True, "ok"


def infer_scope(item: dict) -> str:
    hint = core.clean(item.get("scope_hint"))
    if hint in {"ドラム式", "タテ型"}:
        return hint
    return _original_infer_scope(item)


def scoped_appliance(manufacturer: str, appliance: str, scope: str) -> str:
    if manufacturer in {"日立", "ハイアール", "AQUA"}:
        if scope == "ドラム式":
            return "ドラム式洗濯機・洗濯乾燥機"
        if scope == "タテ型":
            return "タテ型洗濯機・洗濯乾燥機"
    return _original_scoped_appliance(manufacturer, appliance, scope)


core.candidate_valid = candidate_valid
core.infer_scope = infer_scope
core.scoped_appliance = scoped_appliance

if __name__ == "__main__":
    core.main()
