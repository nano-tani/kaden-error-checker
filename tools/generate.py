#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import quote, urlparse
from xml.sax.saxutils import escape as xml_escape

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "errors.json"
SEARCH_INDEX_FILE = ROOT / "data" / "search-index.json"
OUTPUT_DIR = ROOT / "errors"
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


def safe_code(code: str) -> str:
    value = code.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "code"


def page_path(record: dict) -> str:
    identity = "\0".join(
        [
            str(record["manufacturer"]).strip(),
            str(record["appliance"]).strip(),
            str(record["code"]).strip().upper(),
        ]
    )
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:10]
    return f"errors/{safe_code(str(record['code']))}-{digest}.html"


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
            str(record["code"]).strip().upper(),
        )
        if key in seen:
            raise ValueError(f"重複データがあります: {' / '.join(key)}")
        seen.add(key)
        validated.append(record)

    return validated


def render_page(record: dict, relative_path: str) -> str:
    manufacturer = e(record["manufacturer"])
    appliance = e(record["appliance"])
    code = e(str(record["code"]).upper())
    summary = e(record["summary"])
    source = e(record["source"])
    verified = e(record["verified"])
    canonical = e(absolute_url(relative_path))
    title = f"{manufacturer} {appliance} エラーコード {code}｜原因と対処"
    description = e(
        f"{record['manufacturer']}の{record['appliance']}に表示される"
        f"エラーコード{str(record['code']).upper()}の原因と対処方法を、メーカー公式情報をもとに整理しています。"
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
      <p>同じエラーコードでも、型番や製造時期によって意味や対処方法が異なる場合があります。最終的にはメーカー公式情報と製品の取扱説明書を確認してください。</p>
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

    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    search_records: list[dict] = []
    sitemap_entries: list[tuple[str, str | None]] = [("index.html", None)]

    for record in records:
        relative_path = page_path(record)
        output_file = ROOT / relative_path
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(render_page(record, relative_path), encoding="utf-8")

        indexed = dict(record)
        indexed["page"] = relative_path
        search_records.append(indexed)
        sitemap_entries.append((relative_path, str(record.get("verified", "")).strip() or None))

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


if __name__ == "__main__":
    generate()
