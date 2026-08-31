#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

from collect_candidates import clean_context, normalize_code

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "review" / "candidates.json"
SOURCES = ROOT / "review" / "discovered_sources.json"
SOURCE_URL = "https://www.chofu.co.jp/support/error-code/?cid=&mode=search"
METHOD = "dedicated:chofu"
UA = "kaden-error-checker/1.4 (+https://github.com/nano-tani/kaden-error-checker)"

CODE_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{0,3}-?\d{1,4}[A-Z]?|[A-Z]{1,3}\d{1,3})(?![A-Z0-9])", re.IGNORECASE)
BLOCKED = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"}
NON_ERROR_MARKERS = (
    "エラー表示ではありません",
    "動作表示であり",
    "試運転中です",
    "試運転完了です",
    "水張り中です",
    "水張りが終了",
    "履歴としてセット",
    "故障履歴呼び出し時にのみ",
)


def clean(value: object) -> str:
    return clean_context(str(value or ""))


class Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.in_h3 = False
        self.h3_parts: list[str] = []
        self.current_heading = ""
        self.in_table = False
        self.current_table_heading = ""
        self.current_table: list[list[str]] = []
        self.tables: list[tuple[str, list[list[str]]]] = []
        self.current_row: list[str] | None = None
        self.current_cell: list[str] | None = None
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "title":
            self.in_title = True
        elif tag == "h3":
            self.in_h3 = True
            self.h3_parts = []
        elif tag == "table" and not self.in_table:
            self.in_table = True
            self.current_table_heading = self.current_heading
            self.current_table = []
        elif self.in_table and tag == "tr":
            self.current_row = []
        elif self.in_table and tag in {"th", "td"} and self.current_row is not None:
            self.current_cell = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            if self.skip:
                self.skip -= 1
            return
        if self.skip:
            return
        if tag == "title":
            self.in_title = False
        elif tag == "h3" and self.in_h3:
            self.current_heading = clean(" ".join(self.h3_parts))
            self.in_h3 = False
        elif self.in_table and tag in {"th", "td"} and self.current_cell is not None:
            assert self.current_row is not None
            self.current_row.append(clean(" ".join(self.current_cell)))
            self.current_cell = None
        elif self.in_table and tag == "tr" and self.current_row is not None:
            row = [x for x in self.current_row if x]
            if row:
                self.current_table.append(row)
            self.current_row = None
        elif tag == "table" and self.in_table:
            if self.current_table:
                self.tables.append((self.current_table_heading, self.current_table))
            self.current_table = []
            self.current_table_heading = ""
            self.in_table = False

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        text = clean(data)
        if not text:
            return
        if self.in_title:
            self.title_parts.append(text)
        if self.in_h3:
            self.h3_parts.append(text)
        if self.current_cell is not None:
            self.current_cell.append(text)

    @property
    def title(self) -> str:
        return clean(" ".join(self.title_parts))


def fetch() -> Parser:
    req = Request(SOURCE_URL, headers={"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
    with urlopen(req, timeout=30) as res:
        raw = res.read(5_000_000)
        charset = res.headers.get_content_charset() or "utf-8"
        html = raw.decode(charset, errors="replace")
    parser = Parser()
    parser.feed(html)
    return parser


def normalize_appliance(heading: str) -> str:
    value = clean(heading)
    value = re.sub(r"^サンポットブランド\s*", "サンポット ", value)
    value = re.sub(r"\s*\([^)]*\)\s*$", "", value)
    return value[:80]


def extract_code(cell: str) -> str:
    value = clean(cell).upper()
    # Strip notes such as (※3) while preserving a real hyphen inside E-00.
    value = re.sub(r"[（(]※?\d+[）)]", "", value)
    match = CODE_RE.search(value)
    if not match:
        return ""
    code = normalize_code(match.group(1))
    if code in BLOCKED or len(code) > 7:
        return ""
    return code


def is_header(row: list[str]) -> bool:
    joined = " ".join(row)
    return "警報" in joined and "内容" in joined and ("処置" in joined or "原因" in joined)


def build_actions(reset: str, cause: str, action: str) -> list[str]:
    result = []
    for source in (cause, reset, action):
        for chunk in re.split(r"(?<=[。！？!?])\s*", clean(source)):
            text = clean(chunk).strip("・〖〗 ")
            if not 5 <= len(text) <= 190:
                continue
            if not any(k in text for k in ("確認", "解除", "切", "入", "閉", "開", "掃除", "補充", "給油", "修理", "点検", "連絡", "依頼", "再", "待", "停止", "交換")):
                continue
            if text not in result:
                result.append(text)
            if len(result) >= 5:
                return result
    return result


def main() -> None:
    parser = fetch()
    existing = json.loads(CANDIDATES.read_text(encoding="utf-8")) if CANDIDATES.exists() else []
    source_rows = json.loads(SOURCES.read_text(encoding="utf-8")) if SOURCES.exists() else []
    existing = [x for x in existing if x.get("extraction_method") != METHOD]
    source_rows = [x for x in source_rows if x.get("collector") != METHOD]

    added = []
    categories = {}
    seen = set()
    for heading, table in parser.tables:
        appliance = normalize_appliance(heading)
        if not appliance or appliance in {"エラーコード", ""}:
            continue
        header_idx = next((i for i, row in enumerate(table[:4]) if is_header(row)), None)
        if header_idx is None:
            continue
        count = 0
        for row in table[header_idx + 1:]:
            if len(row) < 2:
                continue
            code = extract_code(row[0])
            if not code:
                continue
            summary = clean(row[1]).strip("・〖〗 ")
            reset = clean(row[2]) if len(row) > 2 else ""
            cause = clean(row[3]) if len(row) > 3 else ""
            action = clean(row[4]) if len(row) > 4 else ""
            full = clean(" ".join(row))
            if any(marker in full for marker in NON_ERROR_MARKERS):
                continue
            if not 5 <= len(summary) <= 260:
                continue
            key = (appliance, code, summary)
            if key in seen:
                continue
            seen.add(key)
            actions = build_actions(reset, cause, action)
            added.append({
                "manufacturer": "長府製作所",
                "appliance": appliance,
                "code": code,
                "source": SOURCE_URL,
                "page_title": parser.title,
                "evidence": clean(f"エラーコード {code} 内容 {summary} 警報解除方法 {reset} 原因・確認事項 {cause} 処置 {action}")[:850],
                "confidence": "high",
                "status": "needs_review",
                "extraction_method": METHOD,
                "summary_hint": summary,
                "action_hint": clean(" ".join(actions)),
                "already_published": False,
            })
            count += 1
        if count:
            categories[appliance] = categories.get(appliance, 0) + count

    rank = {"low": 1, "medium": 2, "high": 3}
    merged = {}
    for item in [*existing, *added]:
        key = (item.get("manufacturer"), item.get("appliance"), item.get("code"), item.get("detail_url") or item.get("source"))
        old = merged.get(key)
        if old is None or rank.get(item.get("confidence"), 0) > rank.get(old.get("confidence"), 0):
            merged[key] = item
    result = sorted(merged.values(), key=lambda x: (
        bool(x.get("already_published")), str(x.get("manufacturer", "")), str(x.get("appliance", "")),
        str(x.get("code", "")), str(x.get("detail_url") or x.get("source") or ""),
    ))
    source_rows.append({
        "manufacturer": "長府製作所", "appliance": "公式エラーコード全カテゴリ",
        "url": SOURCE_URL, "title": parser.title, "status": "ok",
        "candidate_count": len(added), "collector": METHOD,
        "category_count": len(categories), "category_counts": dict(sorted(categories.items())),
    })
    source_rows.sort(key=lambda x: (str(x.get("manufacturer", "")), str(x.get("appliance", "")), str(x.get("url", ""))))
    CANDIDATES.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SOURCES.write_text(json.dumps(source_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"CHOFU official categories discovered: {len(categories)}")
    print(f"CHOFU candidates added: {len(added)}")
    print(f"CHOFU category counts: {json.dumps(dict(sorted(categories.items())), ensure_ascii=False)}")
    print(f"candidate records after CHOFU: {len(result)}")


if __name__ == "__main__":
    main()
