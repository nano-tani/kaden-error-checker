#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "category_source_registry.json"
CANDIDATES = ROOT / "review" / "candidates.json"
SOURCES = ROOT / "review" / "discovered_sources.json"
UA = "kaden-error-checker/1.2 (+https://github.com/nano-tani/kaden-error-checker)"
METHOD = "dedicated:official-table"

BLOCKED_CODES = {
    "FAQ", "HTML", "HTTP", "HTTPS", "PDF", "LED", "ON", "OFF", "HP", "ECU", "WEB", "ID"
}
CODE_TOKEN_RE = re.compile(
    r"(?<![A-Z0-9])(?:[A-Z]{1,3}\d{1,4}[A-Z]?|\d{2,4}|[A-Z]{2,3})(?![A-Z0-9])",
    re.IGNORECASE,
)


def norm(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or ""))


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", norm(value)).strip()


def norm_code(value: object) -> str:
    return clean(value).upper().replace(" ", "")


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.title_parts: list[str] = []
        self.in_title = False
        self.skip_depth = 0
        self.table_depth = 0
        self.current_table: list[list[str]] | None = None
        self.current_row: list[str] | None = None
        self.current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "title":
            self.in_title = True
        elif tag == "table":
            self.table_depth += 1
            if self.table_depth == 1:
                self.current_table = []
        elif self.table_depth == 1 and tag == "tr":
            self.current_row = []
        elif self.table_depth == 1 and tag in {"th", "td"} and self.current_row is not None:
            self.current_cell = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == "title":
            self.in_title = False
        elif self.table_depth == 1 and tag in {"th", "td"} and self.current_cell is not None:
            assert self.current_row is not None
            self.current_row.append(clean(" ".join(self.current_cell)))
            self.current_cell = None
        elif self.table_depth == 1 and tag == "tr" and self.current_row is not None:
            row = [cell for cell in self.current_row if cell]
            if row and self.current_table is not None:
                self.current_table.append(row)
            self.current_row = None
        elif tag == "table" and self.table_depth:
            if self.table_depth == 1 and self.current_table:
                self.tables.append(self.current_table)
                self.current_table = None
            self.table_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = clean(data)
        if not text:
            return
        if self.in_title:
            self.title_parts.append(text)
        if self.current_cell is not None:
            self.current_cell.append(text)

    @property
    def title(self) -> str:
        return clean(" ".join(self.title_parts))


def fetch(url: str) -> tuple[TableParser | None, str | None]:
    req = Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ja,en;q=0.8",
    })
    try:
        with urlopen(req, timeout=25) as res:
            raw = res.read(3_000_000)
            charset = res.headers.get_content_charset() or "utf-8"
            if charset.lower() in {"shift_jis", "shift-jis", "sjis", "windows-31j", "x-sjis"}:
                charset = "cp932"
            html = raw.decode(charset, errors="replace")
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    parser = TableParser()
    parser.feed(html)
    return parser, None


def header_indices(row: list[str]) -> tuple[int, int, int | None] | None:
    joined = " ".join(row)
    if not any(word in joined for word in ("エラー", "故障", "表示", "サイン", "コード")):
        return None
    code_idx = next((i for i, x in enumerate(row) if any(k in x for k in ("エラーコード", "エラーサイン", "コード", "表示"))), 0)
    summary_idx = next((i for i, x in enumerate(row) if i != code_idx and any(k in x for k in ("内容", "原因", "意味", "故障", "状態"))), None)
    action_idx = next((i for i, x in enumerate(row) if i != code_idx and any(k in x for k in ("処置", "対処", "確認", "対応"))), None)
    if summary_idx is None:
        summary_idx = 1 if len(row) > 1 and code_idx != 1 else None
    if summary_idx is None:
        return None
    return code_idx, summary_idx, action_idx


def extract_codes(cell: str, explicit_table: bool) -> list[str]:
    value = norm(cell).upper()
    if any(mark in value for mark in ("～", "〜", "~")):
        return []
    if len(value) > 80 or any(word in value for word in ("ランプ", "点滅", "機種", "型式", "シリーズ")):
        return []
    codes: list[str] = []
    for match in CODE_TOKEN_RE.finditer(value):
        code = norm_code(match.group(0))
        if code in BLOCKED_CODES or len(code) > 7:
            continue
        if code.isdigit() and not explicit_table:
            continue
        if code not in codes:
            codes.append(code)
    return codes[:12]


def summary_text(value: str) -> str:
    text = clean(value).strip("・:：")
    text = re.sub(r"^(?:エラーコードの内容|内容|原因|故障内容)\s*[:：]?\s*", "", text)
    if not text:
        return ""
    chunks = re.split(r"(?<=[。！？!?])\s*", text)
    first = clean(chunks[0]) if chunks else text
    return first[:240]


def action_text(value: str) -> str:
    text = clean(value).strip("・:：")
    if not text:
        return ""
    if "点検または修理が必要" in text or "点検・修理が必要" in text:
        return "販売店またはメーカーへ点検・修理を依頼する"
    if "サービスセンター" in text and ("連絡" in text or "修理" in text):
        return "販売店またはメーカーへ点検・修理を依頼する"
    chunks = [clean(x) for x in re.split(r"(?<=[。！？!?])\s*", text) if clean(x)]
    useful = []
    for chunk in chunks:
        if not 5 <= len(chunk) <= 180:
            continue
        if any(k in chunk for k in ("確認", "停止", "抜", "入れ", "閉", "掃除", "清掃", "連絡", "修理", "点検", "解除", "再度", "交換", "待", "操作")):
            useful.append(chunk)
        if len(useful) >= 3:
            break
    return " ".join(useful)[:450]


def collect_from_table(row_cfg: dict, parser: TableParser) -> list[dict]:
    manufacturer = clean(row_cfg.get("manufacturer"))
    appliance = clean(row_cfg.get("appliance"))
    source = str(row_cfg.get("source_url") or "")
    result: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for table in parser.tables:
        indices = None
        header_row_index = -1
        for i, row in enumerate(table[:5]):
            indices = header_indices(row)
            if indices:
                header_row_index = i
                break
        explicit = indices is not None
        if indices is None:
            indices = (0, 1, 2)
        code_idx, summary_idx, action_idx = indices

        for row in table[header_row_index + 1:]:
            if len(row) <= max(code_idx, summary_idx):
                continue
            codes = extract_codes(row[code_idx], explicit_table=explicit)
            if not codes:
                continue
            summary = summary_text(row[summary_idx])
            if not 5 <= len(summary) <= 240:
                continue
            raw_action = row[action_idx] if action_idx is not None and action_idx < len(row) else (row[2] if len(row) > 2 and summary_idx != 2 else "")
            action = action_text(raw_action)
            for code in codes:
                key = (code, summary)
                if key in seen:
                    continue
                seen.add(key)
                evidence = clean(f"エラーコード {code} 内容 {summary} 処置 {raw_action}")[:650]
                result.append({
                    "manufacturer": manufacturer,
                    "appliance": appliance,
                    "code": code,
                    "source": source,
                    "page_title": parser.title,
                    "evidence": evidence,
                    "confidence": "high",
                    "status": "needs_review",
                    "extraction_method": METHOD,
                    "summary_hint": summary,
                    "action_hint": action,
                    "already_published": False,
                })
    return result


def main() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    existing = json.loads(CANDIDATES.read_text(encoding="utf-8")) if CANDIDATES.exists() else []
    source_rows = json.loads(SOURCES.read_text(encoding="utf-8")) if SOURCES.exists() else []
    added: list[dict] = []

    for cfg in registry:
        if cfg.get("enabled", True) is False or cfg.get("collector") != "official_table":
            continue
        url = str(cfg.get("source_url") or "")
        parser, error = fetch(url)
        if error or parser is None:
            source_rows.append({
                "manufacturer": cfg.get("manufacturer"),
                "appliance": cfg.get("appliance"),
                "url": url,
                "status": "fetch_error",
                "detail": error,
                "candidate_count": 0,
                "collector": METHOD,
            })
            continue
        rows = collect_from_table(cfg, parser)
        added.extend(rows)
        source_rows.append({
            "manufacturer": cfg.get("manufacturer"),
            "appliance": cfg.get("appliance"),
            "url": url,
            "title": parser.title,
            "status": "ok",
            "candidate_count": len(rows),
            "collector": METHOD,
            "table_count": len(parser.tables),
        })

    rank = {"low": 1, "medium": 2, "high": 3}
    merged: dict[tuple, dict] = {}
    for item in [*existing, *added]:
        key = (
            item.get("manufacturer"), item.get("appliance"), item.get("code"),
            item.get("detail_url") or item.get("source"),
        )
        old = merged.get(key)
        if old is None or rank.get(item.get("confidence"), 0) > rank.get(old.get("confidence"), 0):
            merged[key] = item

    result = sorted(merged.values(), key=lambda x: (
        bool(x.get("already_published")), str(x.get("manufacturer", "")),
        str(x.get("appliance", "")), str(x.get("code", "")),
        str(x.get("detail_url") or x.get("source") or ""),
    ))
    source_rows.sort(key=lambda x: (str(x.get("manufacturer", "")), str(x.get("appliance", "")), str(x.get("url", ""))))
    CANDIDATES.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SOURCES.write_text(json.dumps(source_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"official-table candidates added: {len(added)}")
    print(f"candidate records after official tables: {len(result)}")


if __name__ == "__main__":
    main()
