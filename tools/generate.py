#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, urlparse
from xml.sax.saxutils import escape as xml_escape

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "errors.json"
SEARCH_INDEX_FILE = ROOT / "data" / "search-index.json"
OUTPUT_DIR = ROOT / "errors"
CODE_OUTPUT_DIR = ROOT / "code"
SITEMAP_FILE = ROOT / "sitemap.xml"
ROBOTS_FILE = ROOT / "robots.txt"

DEFAULT_SITE_URL = "https://nano-tani.github.io/kaden-error-checker"
SITE_URL = os.environ.get("SITE_URL", DEFAULT_SITE_URL).rstrip("/")

REQUIRED_FIELDS = {
    "manufacturer",
    "appliance",
    "code",
    "summary",
    "actions",
    "source",
    "verified",
}


def e(value: object) -> str:
    return html.escape(str(value), quote=True)


def normalize_code(code: object) -> str:
    return str(code or "").strip().upper().replace(" ", "")


def safe_code(code: str) -> str:
    value = normalize_code(code).lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "code"


def page_path(record: dict) -> str:
    identity = "\0".join(
        [
            str(record["manufacturer"]).strip(),
            str(record["appliance"]).strip(),
            normalize_code(record["code"]),
        ]
    )
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:10]
    return f"errors/{safe_code(str(record['code']))}-{digest}.html"


def code_page_path(code: str) -> str:
    return f"code/{safe_code(code)}/index.html"


def code_page_url_path(code: str) -> str:
    return f"code/{safe_code(code)}/"


def absolute_url(relative_path: str) -> str:
    return f"{SITE_URL}/{quote(relative_path, safe='/-._~')}"


def validate(records: object) -> list[dict]:
    if not isinstance(records, list):
        raise ValueError("data/errors.json の最上位は配列にしてください。")

    seen: set[tuple[str, str, str]] = set()
    validated: list[dict] = []

    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"{index}件目がオブジェクトではありません。")

        missing = REQUIRED_FIELDS - record.keys()
        if missing:
            raise ValueError(f"{index}件目に不足項目があります: {', '.join(sorted(missing))}")

        if not isinstance(record["actions"], list) or not record["actions"]:
            raise ValueError(f"{index}件目の actions は1件以上の配列にしてください。")

        source = str(record["source"]).strip()
        parsed = urlparse(source)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{index}件目の source URL が不正です。")

        key = (
            str(record["manufacturer"]).strip(),
            str(record["appliance"]).strip(),
            normalize_code(record["code"]),
        )
        if key in seen:
            raise ValueError(f"重複データがあります: {' / '.join(key)}")
        seen.add(key)
        validated.append(record)

    return validated


def record_codes(record: dict) -> list[str]:
    values = [record.get("code"), *(record.get("aliases") or [])]
    result: list[str] = []
    for value in values:
        code = normalize_code(value)
        if code and code not in result:
            result.append(code)
    return result


def render_page(record: dict, relative_path: str) -> str:
    manufacturer = e(record["manufacturer"])
    appliance = e(record["appliance"])
    code_raw = normalize_code(record["code"])
    code = e(code_raw)
    summary = e(record["summary"])
    source = e(record["source"])
    verified = e(record["verified"])
    canonical = e(absolute_url(relative_path))
    code_group_href = e(f"../{code_page_url_path(code_raw)}")
    title = f"{manufacturer} {appliance} エラーコード {code}｜原因と対処"
    description = e(
        f"{record['manufacturer']}の{record['appliance']}に表示される"
        f"エラーコード{code_raw}の原因と対処方法を、メーカー公式情報をもとに整理しています。"
    )
    actions = "\n".join(f"          <li>{e(action)}</li>" for action in record["actions"])

    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="description" content="{description}">
  <link rel="canonical" href="{canonical}">
  <link rel="stylesheet" href="../styles.css">
</head>
<body>
  <main class="wrap">
    <nav class="back"><a href="../index.html">← エラーコード検索へ戻る</a></nav>

    <article class="card detail">
      <div class="meta">{manufacturer} / {appliance}</div>
      <h1 class="detail-title">エラーコード {code}</h1>

      <p><a class="detail-link" href="{code_group_href}">同じコードの他メーカー・家電候補を見る →</a></p>

      <h2>原因・状態</h2>
      <p class="summary">{summary}</p>

      <h2>確認すること</h2>
      <ol class="actions">
{actions}
      </ol>

      <h2>公式情報</h2>
      <p><a class="source" href="{source}" target="_blank" rel="noopener noreferrer">メーカー公式情報を確認 →</a></p>
      <p class="meta">情報確認日: {verified}</p>
    </article>

    <section class="about">
      <h2>注意</h2>
      <p>同じエラーコードでも、メーカー、製品、型番や製造時期によって意味や対処方法が異なる場合があります。最終的にはメーカー公式情報と製品の取扱説明書を確認してください。</p>
      <p class="danger">発煙・焦げ臭い・異常発熱・漏電の疑いがある場合は、操作を続けず使用を中止してください。</p>
    </section>

    <footer>
      <span>非公式の家電エラーコード検索ツール</span>
    </footer>
  </main>
</body>
</html>
"""


def render_code_page(query_code: str, matches: list[dict]) -> str:
    code = e(query_code)
    count = len(matches)
    canonical = e(absolute_url(code_page_url_path(query_code)))
    title = f"エラーコード {code}｜メーカー・家電別の原因と対処候補"
    description = e(
        f"エラーコード{query_code}に該当するメーカー・家電を横断検索。"
        f"確認済みの公式情報をもとに{count}件の候補を表示します。"
    )

    cards: list[str] = []
    for record in matches:
        manufacturer = e(record["manufacturer"])
        appliance = e(record["appliance"])
        record_code = e(normalize_code(record["code"]))
        summary = e(record["summary"])
        verified = e(record["verified"])
        source = e(record["source"])
        detail_href = e(f"../../{record['_page']}")
        cards.append(
            f"""    <article class="card">
      <div class="meta">{manufacturer} / {appliance}</div>
      <h2 class="code">{record_code}</h2>
      <p class="summary">{summary}</p>
      <div class="meta verified">メーカー公式情報を確認済み / 確認日: {verified}</div>
      <a class="detail-link" href="{detail_href}">原因・対処の詳細を見る →</a>
      <a class="source" href="{source}" target="_blank" rel="noopener noreferrer">メーカー公式情報を確認 →</a>
    </article>"""
        )

    cards_html = "\n".join(cards)
    plural_note = (
        f"同じ「{code}」でもメーカーや製品によって意味が異なります。該当する製品を選んでください。"
        if count > 1
        else f"「{code}」に一致する確認済みデータは現在{count}件です。"
    )

    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="description" content="{description}">
  <link rel="canonical" href="{canonical}">
  <link rel="stylesheet" href="../../styles.css">
</head>
<body>
  <main class="wrap">
    <nav class="back"><a href="../../index.html?code={e(query_code)}">← 検索画面へ戻る</a></nav>

    <header class="hero">
      <div class="meta">メーカー横断検索</div>
      <h1>エラーコード {code}</h1>
      <p>{plural_note}</p>
      <p class="meta">確認済み候補: {count}件</p>
    </header>

    <section aria-label="検索結果">
{cards_html}
    </section>

    <section class="about">
      <h2>このページについて</h2>
      <p>メーカー名が分からない状態でも、表示されたエラーコードから候補を探せるよう、同じコードを使う家電を横断して掲載しています。</p>
      <p>掲載内容はメーカー公式情報を確認したデータだけを使用しています。型番や製造時期によって内容が異なる場合があるため、最終確認は各メーカー公式情報と取扱説明書で行ってください。</p>
      <p class="danger">発煙・焦げ臭い・異常発熱・漏電の疑いがある場合は、操作を続けず使用を中止してください。</p>
    </section>

    <footer>
      <span>非公式の家電エラーコード検索ツール</span>
    </footer>
  </main>
</body>
</html>
"""


def generate() -> None:
    records = validate(json.loads(DATA_FILE.read_text(encoding="utf-8")))

    for directory in (OUTPUT_DIR, CODE_OUTPUT_DIR):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)

    enriched_records: list[dict] = []
    code_groups: dict[str, list[dict]] = defaultdict(list)

    for record in records:
        enriched = dict(record)
        enriched["_page"] = page_path(record)
        enriched_records.append(enriched)
        for query_code in record_codes(record):
            code_groups[query_code].append(enriched)

    search_records: list[dict] = []
    sitemap_entries: list[tuple[str, str | None]] = [("index.html", None)]

    for record in enriched_records:
        relative_path = record["_page"]
        output_file = ROOT / relative_path
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(render_page(record, relative_path), encoding="utf-8")

        indexed = {key: value for key, value in record.items() if key != "_page"}
        indexed["page"] = relative_path
        indexed["code_pages"] = {
            query_code: code_page_url_path(query_code)
            for query_code in record_codes(record)
        }
        search_records.append(indexed)
        sitemap_entries.append((relative_path, str(record.get("verified", "")).strip() or None))

    for query_code, matches in sorted(code_groups.items()):
        relative_path = code_page_path(query_code)
        output_file = ROOT / relative_path
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(render_code_page(query_code, matches), encoding="utf-8")
        verified_dates = [str(item.get("verified", "")).strip() for item in matches if str(item.get("verified", "")).strip()]
        lastmod = max(verified_dates) if verified_dates else None
        sitemap_entries.append((code_page_url_path(query_code), lastmod))

    SEARCH_INDEX_FILE.write_text(
        json.dumps(search_records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    urls = []
    for relative_path, lastmod in sitemap_entries:
        lastmod_xml = f"\n    <lastmod>{xml_escape(lastmod)}</lastmod>" if lastmod else ""
        urls.append(
            "  <url>\n"
            f"    <loc>{xml_escape(absolute_url(relative_path))}</loc>"
            f"{lastmod_xml}\n"
            "  </url>"
        )

    SITEMAP_FILE.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n",
        encoding="utf-8",
    )

    ROBOTS_FILE.write_text(
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n",
        encoding="utf-8",
    )

    print(f"Generated {len(records)} detail pages.")
    print(f"Generated {len(code_groups)} cross-manufacturer code pages.")


if __name__ == "__main__":
    generate()
