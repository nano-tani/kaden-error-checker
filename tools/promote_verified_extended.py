#!/usr/bin/env python3
"""Strict promotion extension for additional dedicated manufacturer collectors.

This wrapper keeps the core promotion policy in promote_verified.py and adds
scope/status/noise handling for extra consumer-facing official sources.
Generic candidates remain ineligible for publication.
"""
from __future__ import annotations

import re

import promote_verified as core

core.DEDICATED.update({"dedicated:haier", "dedicated:aqua", "dedicated:fujitsu-app"})

_original_candidate_valid = core.candidate_valid
_original_infer_scope = core.infer_scope
_original_scoped_appliance = core.scoped_appliance
_original_choose_summary = core.choose_summary
_original_item_summary = core.item_summary
_original_choose_actions = core.choose_actions
_original_registry_rules = core.registry_rules

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

ACTION_WORDS = (
    "確認", "してください", "下さい", "依頼", "取り除", "閉", "開", "掃除", "清掃",
    "入れ", "抜", "取り付け", "セット", "再運転", "減ら", "調整", "連絡", "交換",
    "水洗い", "差し", "直し", "相談", "停止",
)


def registry_rules() -> dict[tuple[str, str], list[str]]:
    rules = _original_registry_rules()
    sharp_domains = ["cs.sharp.co.jp"]
    rules[("シャープ", "タテ型洗濯機・洗濯乾燥機")] = sharp_domains
    rules[("シャープ", "ドラム式洗濯機・洗濯乾燥機")] = sharp_domains
    rules[("富士通ゼネラル", "ノクリアアプリ")] = ["www.fujitsu-general.com", "fujitsu-general.com", "www.generalww.com", "generalww.com"]
    return rules


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
        if not re.fullmatch(r"[EFU][A-Z0-9]{0,3}", code):
            return False, "unexpected_code_family"
        if not 5 <= len(summary) <= 180:
            return False, "weak_structured_summary"

    if method == "dedicated:aqua":
        if any(term in text for term in AQUA_NOISE_TERMS):
            return False, "aqua_table_noise"
        if summary[0] in ")]}】。、,.・:-" or evidence.startswith(("ALL ", "CO ")):
            return False, "aqua_table_noise"

    if method == "dedicated:sharp" and item.get("appliance") in {
        "タテ型洗濯機・洗濯乾燥機", "ドラム式洗濯機・洗濯乾燥機"
    }:
        if not re.fullmatch(r"(?:E|U|C)[A-Z0-9]{1,3}|UF", code):
            return False, "unexpected_code_family"
        if not 12 <= len(summary) <= 260:
            return False, "weak_structured_summary"
        if any(phrase in summary for phrase in (
            "以下の状況が考えられます",
            "下記の状況が考えられます",
            "以下をご確認ください",
            "下記をご確認ください",
        )):
            return False, "weak_structured_summary"

    if method == "dedicated:fujitsu-app":
        if not re.fullmatch(r"\d{4}", code):
            return False, "unexpected_code_family"
        if not 5 <= len(summary) <= 260:
            return False, "weak_structured_summary"

    return True, "ok"


def _polish_summary(value: str, method: str = "") -> str:
    text = core.clean(value)
    text = re.sub(r"^(?:など|等)\s*", "", text)
    if method == "dedicated:fujitsu-app" and "※" in text:
        text = core.clean(text.split("※", 1)[0])
    return text


def choose_summary(group: list[dict]) -> str:
    value = _original_choose_summary(group)
    method = core.clean(group[0].get("extraction_method")) if group else ""
    if method in {"dedicated:haier", "dedicated:aqua", "dedicated:fujitsu-app"}:
        return _polish_summary(value, method)
    return value


def item_summary(item: dict) -> str:
    value = _original_item_summary(item)
    method = core.clean(item.get("extraction_method"))
    if method in {"dedicated:haier", "dedicated:aqua", "dedicated:fujitsu-app"}:
        return _polish_summary(value, method)
    return value


def _fujitsu_actions(group: list[dict]) -> list[str]:
    result = []
    for item in group:
        raw = core.clean(item.get("action_hint"))
        for chunk in re.split(r"(?<=[。！？!?])\s*", raw):
            text = core.clean(chunk).strip("・※ ")
            text = re.sub(r"^\d+(?:-\d+)?[.．]\s*", "", text)
            if not 6 <= len(text) <= 180:
                continue
            if text not in result:
                result.append(text)
            if len(result) >= 5:
                return result
    return result or ["富士通ゼネラル公式のエラーコード表で確認内容を確認する"]


def choose_actions(group: list[dict]) -> list[str]:
    method = core.clean(group[0].get("extraction_method")) if group else ""
    if method == "dedicated:fujitsu-app":
        return _fujitsu_actions(group)

    actions = _original_choose_actions(group)
    if method not in {"dedicated:haier", "dedicated:aqua"}:
        return actions

    cleaned = []
    for action in actions:
        text = core.clean(action).lstrip("・※ ")
        if re.match(r"^\d+[.．]", text):
            continue
        if "エラーを解消する方法" in text or "焦って修理" in text:
            continue
        if not any(word in text for word in ACTION_WORDS):
            continue
        if text not in cleaned:
            cleaned.append(text)
    return cleaned or ["メーカー公式の案内で対象機種と対処方法を確認する"]


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


core.registry_rules = registry_rules
core.candidate_valid = candidate_valid
core.choose_summary = choose_summary
core.item_summary = item_summary
core.choose_actions = choose_actions
core.infer_scope = infer_scope
core.scoped_appliance = scoped_appliance

if __name__ == "__main__":
    core.main()
