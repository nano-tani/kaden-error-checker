#!/usr/bin/env python3
"""Strict promotion extension for additional dedicated manufacturer collectors.

This wrapper keeps the core promotion policy in promote_verified.py and adds
scope/status handling for Haier and AQUA before running the same production
rebuild. Generic candidates remain ineligible for automatic publication.
"""
from __future__ import annotations

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


def candidate_valid(item: dict, domains: list[str]) -> tuple[bool, str]:
    valid, reason = _original_candidate_valid(item, domains)
    if not valid:
        return valid, reason

    method = core.clean(item.get("extraction_method"))
    if method in {"dedicated:haier", "dedicated:aqua"}:
        text = core.clean(
            f"{item.get('summary_hint', '')} {item.get('action_hint', '')} "
            f"{item.get('evidence', '')}"
        )
        if any(term in text for term in STATUS_TERMS):
            return False, "status_not_error"
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
