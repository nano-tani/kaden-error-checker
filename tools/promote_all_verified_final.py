#!/usr/bin/env python3
from __future__ import annotations

import re

import promote_all_verified as promoter
import promote_verified as core

_original_summary_is_specific = core.summary_is_specific

GENERIC_SUMMARIES = {
    "点検または修理が必要です",
    "点検・修理が必要です",
    "修理が必要です",
    "故障の可能性があります",
    "異常を検知しました",
    "エラーが発生しました",
    "エラーです",
}


def strict_summary_is_specific(summary: str, code: str) -> bool:
    if not _original_summary_is_specific(summary, code):
        return False
    text = core.clean(summary).strip("。.!！ ")
    if text in GENERIC_SUMMARIES:
        return False
    # Navigation/page-title contamination is not a usable cause description.
    if text.startswith("シロカ お客様サポート"):
        return False
    if any(noise in text for noise in ("トップに戻る お問い合わせ", "トップに戻る", "お問い合わせ おうち")):
        return False
    # A long string that mostly repeats the article title is evidence extraction noise.
    if len(text) > 180 and re.search(r"エラーメッセージ[（(].+?[）)]が表示", text):
        return False
    return True


core.summary_is_specific = strict_summary_is_specific

if __name__ == "__main__":
    promoter.main()
