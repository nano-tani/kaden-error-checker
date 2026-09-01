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
METHOD = "dedicated:iris-siroca"

IRIS_PAGES = [
    ("洗濯機・洗濯乾燥機", "https://www.irisohyama.co.jp/support/faq/categori.php?ID=44"),
    ("ルームエアコン", "https://www.irisohyama.co.jp/support/faq/categori.php?ID=34"),
    ("炊飯器", "https://www.irisohyama.co.jp/support/faq/categori.php?ID=7&detail=1"),
    ("シェフドラム", "https://www.irisohyama.co.jp/support/faq/detail.php?ID=7098"),
]
SIROCA_ROOTS = [
    "https://www.siroca.co.jp/support/%E3%82%AD%E3%83%83%E3%83%81%E3%83%B3%E5%AE%B6%E9%9B%BB",
    "https://www.siroca.co.jp/support/%E6%95%85%E9%9A%9C%E3%83%BB%E4%BF%AE%E7%90%86%E3%81%AE%E3%81%8A%E5%95%8F%E3%81%84%E5%90%88%E3%82%8F%E3%81%9B-5f4b55bf1bf4b1001ef67b30",
]

IRIS_CODE_RE = re.compile(r"〖\s*([A-Za-z0-9-]{1,7}(?:\s*[、,]\s*[A-Za-z0-9-]{1,7})*)\s*〗(?:表示)?", re.IGNORECASE)
QUOTED_CODE_RE = re.compile(r"[「『]\s*([A-Za-z0-9-]{1,7})\s*[」』]", re.IGNORECASE)
GENERAL_CODE_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{1,4}-?\d{1,3}|E\d{1,3}|C\d{1,3}|U\d{1,3}|Ed)(?![A-Z0-9])", re.IGNORECASE)

NON_ERROR_TERMS = (
    "チャイルドロック", "停電が発生", "お手入れに関するお知らせ", "クールダウン運転中",
    "故障ではありません", "エラーではありません",
)
CAUSE_WORDS = (
    "異常", "不具合", "検知", "お知らせ", "できない", "できません", "閉まって", "給水", "排水",
    "モーター", "温度", "センサー", "センサ", "通信", "高温", "過熱", "電圧", "停電", "圧力",
    "取り付け", "入って", "挟ま", "空焚", "空だき", "水位", "偏って", "つまり", "詰まり",
)
ACTION_WORDS = (
    "確認", "抜", "差し", "再度", "入れ直", "閉", "掃除", "清掃", "取り除", "減ら", "修理",
    "点検", "連絡", "お問い合わせ", "冷ま", "待", "停止", "取り付け", "入れ", "操作", "お試し",
)


def clean(value: object) -> str:
    return clean_context(str(value or ""))


def sentences(text: str) -> list[str]:
    return [clean(x).strip("・■※ ") for x in re.split(r"(?<=[。！？!?])\s*", clean(text)) if clean(x)]


def codes(raw: str) -> list[str]:
    result = []
    for token in re.split(r"[、,\s]+", clean(raw)):
        code = normalize_code(token)
        if code and re.fullmatch(r"[A-Z0-9-]{1,7}", code) and code not in result:
            result.append(code)
    return result


def polish_summary(value: str) -> str:
    text = clean(value)
    text = re.sub(r"^シロカ\s+お客様サポート\s*[-–—]\s*", "", text)
    text = re.sub(r"^.+?[：:]エラーメッセージ\([^)]*\)が表示されます\s*", "", text)
    text = re.sub(r"^(?:が表示される|が表示されます)\s*", "", text)
    text = re.sub(r"^(?:トップに戻る|お問い合わせ)\s*", "", text)
    return clean(text)[:250]


def summarize_block(block: str) -> str:
    text = clean(block)
    for sentence in sentences(text[:1200]):
        sentence = polish_summary(sentence)
        if not 5 <= len(sentence) <= 250:
            continue
        if any(term in sentence for term in NON_ERROR_TERMS):
            return ""
        if sentence in {"故障の可能性があります。", "故障の可能性があります", "点検・修理が必要です。", "点検または修理が必要です。"}:
            continue
        if any(word in sentence for word in CAUSE_WORDS):
            return sentence
    return ""


def actions_block(block: str, summary: str) -> list[str]:
    text = clean(block)
    if summary and summary in text:
        text = clean(text.split(summary, 1)[1])
    out = []
    for sentence in sentences(text[:1800]):
        if not 5 <= len(sentence) <= 190:
            continue
        if any(word in sentence for word in ACTION_WORDS):
            if sentence not in out:
                out.append(sentence)
        if len(out) >= 5:
            break
    return out


def make(manufacturer: str, appliance: str, code: str, source: str, title: str, summary: str, actions: list[str]) -> dict:
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


def iris_inline_blocks(appliance: str, url: str, title: str, text: str) -> list[dict]:
    value = clean(text)
    matches = list(IRIS_CODE_RE.finditer(value))
    result = []
    for i, match in enumerate(matches):
        block = value[match.end(): matches[i + 1].start() if i + 1 < len(matches) else min(len(value), match.end() + 1800)]
        summary = summarize_block(block)
        if not summary:
            continue
        acts = actions_block(block, summary)
        for code in codes(match.group(1)):
            result.append(make("アイリスオーヤマ", appliance, code, url, title, summary, acts))
    return result


def collect_iris() -> tuple[list[dict], list[dict]]:
    added = []
    source_rows = []
    seen_detail = set()
    for appliance, url in IRIS_PAGES:
        page, error = fetch_page(url)
        if error or page is None:
            source_rows.append({"manufacturer": "アイリスオーヤマ", "appliance": appliance, "url": url, "status": "fetch_error", "detail": error, "candidate_count": 0, "collector": METHOD})
            continue
        rows = iris_inline_blocks(appliance, url, page.title, page.text)
        for href, anchor in page.links:
            anchor_text = clean(anchor)
            if "表示" not in anchor_text or not (QUOTED_CODE_RE.search(anchor_text) or GENERAL_CODE_RE.search(anchor_text)):
                continue
            detail_url = urljoin(url, href)
            if urlparse(detail_url).hostname not in {"www.irisohyama.co.jp", "irisohyama.co.jp"} or detail_url in seen_detail:
                continue
            seen_detail.add(detail_url)
            detail, err = fetch_page(detail_url)
            if err or detail is None:
                continue
            detail_rows = iris_inline_blocks(appliance, detail_url, detail.title, detail.text)
            if detail_rows:
                rows.extend(detail_rows)
            else:
                raw_codes = [normalize_code(x) for x in QUOTED_CODE_RE.findall(clean(detail.title))]
                raw_codes += [normalize_code(x) for x in GENERAL_CODE_RE.findall(clean(detail.title))]
                raw_codes = list(dict.fromkeys(x for x in raw_codes if x))
                summary = summarize_block(detail.text)
                acts = actions_block(detail.text, summary) if summary else []
                for code in raw_codes:
                    rows.append(make("アイリスオーヤマ", appliance, code, detail_url, detail.title, summary, acts))
            time.sleep(0.03)
        added.extend(rows)
        source_rows.append({"manufacturer": "アイリスオーヤマ", "appliance": appliance, "url": url, "title": page.title, "status": "ok", "candidate_count": len(rows), "collector": METHOD})
    return added, source_rows


def siroca_appliance(title: str) -> str:
    value = clean(title)
    value = re.sub(r"^シロカ\s+お客様サポート\s*[-–—]\s*", "", value)
    value = value.split("：", 1)[0].split(":", 1)[0].strip()
    mapping = [
        (("食器洗い乾燥機",), "食器洗い乾燥機"),
        (("おうちシェフクッカー", "電気圧力鍋", "おうちシェフ"), "電気圧力鍋・おうちシェフ"),
        (("すばやきトースター", "オーブントースター"), "オーブントースター"),
        (("コーン式全自動コーヒーメーカー",), "コーン式全自動コーヒーメーカー"),
        (("全自動コーヒーメーカー", "コーヒーメーカー全般"), "コーヒーメーカー"),
        (("ヒーター機能付きブレンダー",), "ヒーター機能付きブレンダー"),
    ]
    for needles, label in mapping:
        if any(needle in value for needle in needles):
            return label
    return value[:60] or "キッチン家電"


def siroca_code_blocks(text: str) -> list[tuple[list[str], str]]:
    value = clean(text)
    markers = list(re.finditer(r"(?:[「『]\s*([A-Za-z]{1,4}\d{1,3}|Ed)\s*[」』]|〖\s*([A-Za-z]{1,4}\d{1,3}|Ed)\s*〗)", value, re.IGNORECASE))
    out = []
    for i, marker in enumerate(markers):
        code = normalize_code(marker.group(1) or marker.group(2))
        block = value[marker.end(): markers[i + 1].start() if i + 1 < len(markers) else min(len(value), marker.end() + 1500)]
        out.append(([code], block))
    return out


def collect_siroca() -> tuple[list[dict], list[dict]]:
    added = []
    source_rows = []
    detail_links = {}
    for root_url in SIROCA_ROOTS:
        root, error = fetch_page(root_url)
        if error or root is None:
            source_rows.append({"manufacturer": "シロカ", "appliance": "サポート", "url": root_url, "status": "fetch_error", "detail": error, "candidate_count": 0, "collector": METHOD})
            continue
        for href, anchor in root.links:
            title = clean(anchor)
            if not any(term in title for term in ("表示され", "表示されます", "エラーメッセージ", "E01", "E1", "Er 01", "Ed")):
                continue
            url = urljoin(root_url, href)
            if (urlparse(url).hostname or "") not in {"www.siroca.co.jp", "siroca.co.jp"}:
                continue
            detail_links[url] = title

    for url, anchor in sorted(detail_links.items()):
        detail, error = fetch_page(url)
        if error or detail is None:
            continue
        appliance = siroca_appliance(detail.title or anchor)
        rows = []
        for code_list, block in siroca_code_blocks(detail.text):
            summary = summarize_block(block)
            if not summary:
                continue
            acts = actions_block(block, summary)
            for code in code_list:
                rows.append(make("シロカ", appliance, code, url, detail.title, summary, acts))
        if not rows:
            title_codes = [normalize_code(x) for x in GENERAL_CODE_RE.findall(clean(detail.title))]
            title_codes = list(dict.fromkeys(x for x in title_codes if x))
            summary = summarize_block(detail.text)
            acts = actions_block(detail.text, summary) if summary else []
            for code in title_codes:
                rows.append(make("シロカ", appliance, code, url, detail.title, summary, acts))
        added.extend(rows)
        source_rows.append({"manufacturer": "シロカ", "appliance": appliance, "url": url, "title": detail.title, "status": "ok", "candidate_count": len(rows), "collector": METHOD})
        time.sleep(0.03)
    return added, source_rows


def main() -> None:
    existing = json.loads(CANDIDATES.read_text(encoding="utf-8")) if CANDIDATES.exists() else []
    source_rows = json.loads(SOURCES.read_text(encoding="utf-8")) if SOURCES.exists() else []
    existing = [x for x in existing if x.get("extraction_method") != METHOD]
    source_rows = [x for x in source_rows if x.get("collector") != METHOD]

    added = []
    for fn in (collect_iris, collect_siroca):
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
    print(f"Iris/Siroca candidates added: {len(added)}")
    print(f"Iris/Siroca candidates by manufacturer: {json.dumps(by_mfr, ensure_ascii=False)}")
    print(f"candidate records after Iris/Siroca: {len(result)}")


if __name__ == "__main__":
    main()
