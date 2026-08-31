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

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "source_registry.json"
PUBLISHED = ROOT / "data" / "errors.json"
REVIEW_DIR = ROOT / "review"
CANDIDATES = REVIEW_DIR / "candidates.json"
SOURCES = REVIEW_DIR / "discovered_sources.json"

UA = "kaden-error-checker/1.0 (+https://github.com/nano-tani/kaden-error-checker)"
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

# obvious false positives frequently found in dates, HTTP text, dimensions, etc.
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


def fetch_page(url):
    req = Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})
    with urlopen(req, timeout=20) as res:
        ctype = res.headers.get("Content-Type", "")
        if "text/html" not in ctype and "application/xhtml+xml" not in ctype:
            return None, f"unsupported content-type: {ctype}"
        raw = res.read(2_000_000)
        charset = res.headers.get_content_charset() or "utf-8"
        try:
            html = raw.decode(charset, errors="replace")
        except LookupError:
            html = raw.decode("utf-8", errors="replace")
    parser = PageParser()
    parser.feed(html)
    return parser, None


def allowed(url, domains):
    host = (urlparse(url).hostname or "").lower()
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in domains)


def useful_link(href, anchor):
    probe = normalize(f"{href} {anchor}").lower()
    return any(k.lower() in probe for k in LINK_KEYWORDS)


def clean_context(text):
    return re.sub(r"\s+", " ", normalize(text)).strip()


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
    # Generic extraction requires a letter+digit combination. Numeric-only codes
    # are accepted only when the page explicitly labels them as an error/diagnostic code.
    if not explicit and not (re.search(r"[A-Z]", code) and re.search(r"\d", code)):
        return False
    if not explicit and not any(k in context for k in CONTEXT_KEYWORDS):
        return False
    # Exclude long model-like strings unless explicitly labelled as an error code.
    if not explicit and len(code) >= 7:
        return False
    return True


def extract_candidates(text, title, source_url, manufacturer, appliance):
    text = clean_context(text)
    found = {}

    def add(code, start, end, explicit=False):
        left = max(0, start - 140)
        right = min(len(text), end + 220)
        context = clean_context(text[left:right])
        code_n = normalize_code(code)
        if not looks_like_code(code_n, context, explicit=explicit):
            return
        key = code_n
        item = {
            "manufacturer": manufacturer,
            "appliance": appliance,
            "code": code_n,
            "source": source_url,
            "page_title": title,
            "evidence": context[:500],
            "confidence": candidate_score(context, title, explicit=explicit),
            "status": "needs_review",
        }
        previous = found.get(key)
        rank = {"low": 1, "medium": 2, "high": 3}
        if previous is None or rank[item["confidence"]] > rank[previous["confidence"]]:
            found[key] = item

    for m in EXPLICIT_RE.finditer(text):
        add(m.group(1), m.start(1), m.end(1), explicit=True)
    for m in CODE_RE.finditer(text):
        add(m.group(0), m.start(), m.end(), explicit=False)

    return list(found.values())


def published_keys():
    try:
        records = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    except Exception:
        return set()
    return {
        (normalize(r.get("manufacturer")), normalize(r.get("appliance")), normalize_code(r.get("code")))
        for r in records
    }


def main():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    published = published_keys()
    all_candidates = []
    source_rows = []

    for source in registry:
        manufacturer = source["manufacturer"]
        appliance = source["appliance"]
        domains = source.get("allowed_domains", [])
        max_pages = int(source.get("max_pages", 6))
        queue = deque(source.get("seed_urls", []))
        seen = set()
        pages = 0

        while queue and pages < max_pages:
            url = urldefrag(queue.popleft())[0]
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
                })
                continue

            candidates = extract_candidates(parser.text, parser.title, url, manufacturer, appliance)
            for item in candidates:
                key = (normalize(manufacturer), normalize(appliance), normalize_code(item["code"]))
                item["already_published"] = key in published
                all_candidates.append(item)

            source_rows.append({
                "manufacturer": manufacturer,
                "appliance": appliance,
                "url": url,
                "title": parser.title,
                "status": "ok",
                "candidate_count": len(candidates),
            })

            for href, anchor in parser.links:
                absolute = urldefrag(urljoin(url, href))[0]
                if absolute not in seen and allowed(absolute, domains) and useful_link(absolute, anchor):
                    queue.append(absolute)

    # Keep the strongest evidence when the same maker/appliance/code/source repeats.
    rank = {"low": 1, "medium": 2, "high": 3}
    dedup = {}
    for item in all_candidates:
        key = (item["manufacturer"], item["appliance"], item["code"], item["source"])
        prev = dedup.get(key)
        if prev is None or rank[item["confidence"]] > rank[prev["confidence"]]:
            dedup[key] = item

    result = sorted(
        dedup.values(),
        key=lambda x: (x["already_published"], x["manufacturer"], x["appliance"], x["code"], x["source"]),
    )
    source_rows.sort(key=lambda x: (x["manufacturer"], x["url"]))

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    CANDIDATES.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SOURCES.write_text(json.dumps(source_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    new_count = sum(not x["already_published"] for x in result)
    high_count = sum((not x["already_published"]) and x["confidence"] == "high" for x in result)
    print(f"candidate records: {len(result)}")
    print(f"not published: {new_count}")
    print(f"high-confidence not published: {high_count}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"collector failed: {exc}", file=sys.stderr)
        raise
