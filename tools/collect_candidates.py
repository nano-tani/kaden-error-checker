#!/usr/bin/env python3
import json
import re
import sys
import unicodedata
from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.request import Request, urlopen

from collectors import DEDICATED_MANUFACTURERS, extract_for, should_follow

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "source_registry.json"
PUBLISHED = ROOT / "data" / "errors.json"
REVIEW_DIR = ROOT / "review"
CANDIDATES = REVIEW_DIR / "candidates.json"
SOURCES = REVIEW_DIR / "discovered_sources.json"

UA = "kaden-error-checker/1.1 (+https://github.com/nano-tani/kaden-error-checker)"
LINK_KEYWORDS = ("エラー", "異常", "故障", "点検", "診断", "表示", "error", "trouble", "diagnosis")
CONTEXT_KEYWORDS = ("エラー", "診断コード", "エラーコード", "点検コード", "異常", "故障", "表示", "お知らせ")

CODE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]{1,3}[0-9]{1,4}[A-Za-z]?|[0-9]{1,4}[A-Za-z]{1,3})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
EXPLICIT_RE = re.compile(
    r"(?:エラー(?:コード)?|診断コード|点検コード|表示)\s*[:：]?\s*[「『\[]?([A-Za-z0-9]{1,7})[」』\]]?",
    re.IGNORECASE,
)
BLOCKED = {"HTML", "HTTP", "HTTPS", "UTF8", "UTF-8", "PDF", "FAQ", "WEB", "AI", "ID"}
UNIT_LIKE_RE = re.compile(r"^\d+(?:KG|KW|CM|MM|ML|HZ)$", re.IGNORECASE)


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []
        self.title_parts = []
        self.in_title = False
        self.skip_depth = 0
        self.current_link = None
        self.current_anchor = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        if tag == "title":
            self.in_title = True
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.current_link = href
                self.current_anchor = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
        if tag == "title":
            self.in_title = False
        if tag == "a" and self.current_link:
            self.links.append((self.current_link, " ".join(self.current_anchor)))
            self.current_link = None
            self.current_anchor = []

    def handle_data(self, data):
        if self.skip_depth:
            return
        text = " ".join(data.split())
        if not text:
            return
        self.parts.append(text)
        if self.in_title:
            self.title_parts.append(text)
        if self.current_link is not None:
            self.current_anchor.append(text)

    @property
    def text(self):
        return " ".join(self.parts)

    @property
    def title(self):
        return " ".join(self.title_parts).strip()


def normalize(text):
    return unicodedata.normalize("NFKC", str(text or ""))


def normalize_code(code):
    return normalize(code).strip().upper().replace(" ", "")


def clean_context(text):
    return re.sub(r"\s+", " ", normalize(text)).strip()


def canonicalize_url(url, manufacturer=None):
    url = urldefrag(str(url or ""))[0]
    if manufacturer == "パナソニック":
        parsed = urlparse(url)
        match = re.search(r"(/app/answers/detail/a_id/\d+)", parsed.path)
        if match:
            return f"{parsed.scheme}://{parsed.netloc}{match.group(1)}"
    return url


def fetch_page(url):
    req = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ja,en;q=0.8",
        },
    )
    with urlopen(req, timeout=20) as res:
        ctype = res.headers.get("Content-Type", "")
        if "text/html" not in ctype and "application/xhtml+xml" not in ctype:
            return None, f"unsupported content-type: {ctype}"
        raw = res.read(2_000_000)
        header_charset = res.headers.get_content_charset()
        meta_match = re.search(
            br"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)",
            raw[:8192],
            re.IGNORECASE,
        )
        meta_charset = meta_match.group(1).decode("ascii", errors="ignore") if meta_match else None
        charset = header_charset or meta_charset or "utf-8"
        charset_aliases = {
            "shift_jis": "cp932",
            "shift-jis": "cp932",
            "sjis": "cp932",
            "x-sjis": "cp932",
            "windows-31j": "cp932",
        }
        charset = charset_aliases.get(charset.lower(), charset)
        try:
            html = raw.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            html = raw.decode("utf-8", errors="replace")
    parser = PageParser()
    parser.feed(html)
    return parser, None


def allowed(url, domains):
    host = (urlparse(url).hostname or "").lower()
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in domains)


def generic_useful_link(href, anchor):
    probe = normalize(f"{href} {anchor}").lower()
    return any(k.lower() in probe for k in LINK_KEYWORDS)


def candidate_score(context, title, explicit=False):
    ctx = context.lower()
    score = 1
    if explicit:
        score += 3
    if "エラーコード" in ctx or "診断コード" in ctx or "点検コード" in ctx:
        score += 2
    elif "エラー" in ctx:
        score += 1
    if any(k in normalize(title) for k in ("エラー", "診断", "点検", "故障")):
        score += 1
    if score >= 5:
        return "high"
    if score >= 3:
        return "medium"
    return "low"


def looks_like_code(code, context, explicit=False):
    code = normalize_code(code)
    if not code or code in BLOCKED or len(code) > 7:
        return False
    if UNIT_LIKE_RE.fullmatch(code):
        return False
    if not explicit and not (re.search(r"[A-Z]", code) and re.search(r"\d", code)):
        return False
    if not explicit and not any(k in context for k in CONTEXT_KEYWORDS):
        return False
    if not explicit and len(code) >= 7:
        return False
    return True


def generic_extract(text, title, source_url, manufacturer, appliance):
    text = clean_context(text)
    found = {}

    def add(code, start, end, explicit=False):
        left = max(0, start - 140)
        right = min(len(text), end + 220)
        context = clean_context(text[left:right])
        code_n = normalize_code(code)
        if not looks_like_code(code_n, context, explicit=explicit):
            return
        item = {
            "manufacturer": manufacturer,
            "appliance": appliance,
            "code": code_n,
            "source": source_url,
            "page_title": title,
            "evidence": context[:500],
            "confidence": candidate_score(context, title, explicit=explicit),
            "status": "needs_review",
            "extraction_method": "generic",
        }
        previous = found.get(code_n)
        rank = {"low": 1, "medium": 2, "high": 3}
        if previous is None or rank[item["confidence"]] > rank[previous["confidence"]]:
            found[code_n] = item

    for match in EXPLICIT_RE.finditer(text):
        add(match.group(1), match.start(1), match.end(1), explicit=True)
    for match in CODE_RE.finditer(text):
        add(match.group(0), match.start(), match.end(), explicit=False)

    return list(found.values())


def published_keys():
    try:
        records = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    except Exception:
        return set()
    keys = set()
    for record in records:
        base = (normalize(record.get("manufacturer")), normalize(record.get("appliance")))
        for code in [record.get("code"), *(record.get("aliases") or [])]:
            code_n = normalize_code(code)
            if code_n:
                keys.add((*base, code_n))
    return keys


def mark_published(item, published):
    base = (normalize(item.get("manufacturer")), normalize(item.get("appliance")))
    codes = [item.get("code"), *(item.get("aliases") or [])]
    return any((*base, normalize_code(code)) in published for code in codes if normalize_code(code))


def should_queue(manufacturer, href, anchor):
    dedicated = should_follow(manufacturer, href, anchor)
    if dedicated is not None:
        return dedicated
    return generic_useful_link(href, anchor)


def main():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    published = published_keys()
    all_candidates = []
    source_rows = []

    for source in registry:
        if source.get("enabled", True) is False:
            continue

        manufacturer = source["manufacturer"]
        appliance = source["appliance"]
        domains = source.get("allowed_domains", [])
        max_pages = int(source.get("max_pages", 6))
        queue = deque(canonicalize_url(url, manufacturer) for url in source.get("seed_urls", []))
        seen = set()
        pages = 0

        while queue and pages < max_pages:
            url = canonicalize_url(queue.popleft(), manufacturer)
            if not url or url in seen or not allowed(url, domains):
                continue
            seen.add(url)
            pages += 1

            try:
                parser, error = fetch_page(url)
            except Exception as exc:
                parser, error = None, f"{type(exc).__name__}: {exc}"

            if error or parser is None:
                source_rows.append({
                    "manufacturer": manufacturer,
                    "appliance": appliance,
                    "url": url,
                    "status": "fetch_error",
                    "detail": str(error)[:240],
                    "candidate_count": 0,
                    "collector": "dedicated" if manufacturer in DEDICATED_MANUFACTURERS else "generic",
                })
                continue

            absolute_links = [
                (canonicalize_url(urljoin(url, href), manufacturer), anchor)
                for href, anchor in parser.links
            ]
            candidates = extract_for(
                manufacturer,
                appliance,
                parser.text,
                parser.title,
                url,
                parts=parser.parts,
                links=absolute_links,
            )
            if candidates is None:
                candidates = generic_extract(parser.text, parser.title, url, manufacturer, appliance)

            for item in candidates:
                if item.get("detail_url"):
                    item["detail_url"] = canonicalize_url(item["detail_url"], manufacturer)
                item["source"] = canonicalize_url(item["source"], manufacturer)
                item["already_published"] = mark_published(item, published)
                all_candidates.append(item)

            source_rows.append({
                "manufacturer": manufacturer,
                "appliance": appliance,
                "url": url,
                "title": parser.title,
                "status": "ok",
                "candidate_count": len(candidates),
                "collector": "dedicated" if manufacturer in DEDICATED_MANUFACTURERS else "generic",
            })

            for absolute, anchor in absolute_links:
                if (
                    absolute
                    and absolute not in seen
                    and allowed(absolute, domains)
                    and should_queue(manufacturer, absolute, anchor)
                ):
                    queue.append(absolute)

    rank = {"low": 1, "medium": 2, "high": 3}
    dedup = {}
    for item in all_candidates:
        key = (
            item["manufacturer"],
            item["appliance"],
            item["code"],
            item.get("detail_url") or item["source"],
        )
        previous = dedup.get(key)
        if previous is None or rank[item["confidence"]] > rank[previous["confidence"]]:
            dedup[key] = item

    result = sorted(
        dedup.values(),
        key=lambda x: (
            x["already_published"],
            x["manufacturer"],
            x["appliance"],
            x["code"],
            x.get("detail_url") or x["source"],
        ),
    )
    source_rows.sort(key=lambda x: (x["manufacturer"], x["url"]))

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    CANDIDATES.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SOURCES.write_text(json.dumps(source_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    new_count = sum(not x["already_published"] for x in result)
    high_count = sum((not x["already_published"]) and x["confidence"] == "high" for x in result)
    dedicated_count = sum(str(x.get("extraction_method", "")).startswith("dedicated:") for x in result)
    dedicated_high = sum(
        (not x["already_published"])
        and x["confidence"] == "high"
        and str(x.get("extraction_method", "")).startswith("dedicated:")
        for x in result
    )
    print(f"candidate records: {len(result)}")
    print(f"not published: {new_count}")
    print(f"high-confidence not published: {high_count}")
    print(f"dedicated collector records: {dedicated_count}")
    print(f"dedicated high-confidence not published: {dedicated_high}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"collector failed: {exc}", file=sys.stderr)
        raise
