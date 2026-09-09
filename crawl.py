"""Map a website into an Obsidian vault: one note per page, folders mirroring the
URL path, metadata in frontmatter, and content links as wikilinks so the graph
view shows the editorial link structure rather than the main menu.

    python crawl.py https://example.com              new and lastmod-changed pages
    python crawl.py https://example.com --full        re-fetch everything
    python crawl.py https://example.com --full-text   store full body as Markdown
    python crawl.py https://example.com --max-pages N link-crawl cap (no sitemap)
    python crawl.py https://example.com --delay S     override crawl delay
    python crawl.py https://example.com --ignore-robots
    python crawl.py https://example.com --include-archives
    python crawl.py https://example.com --keep-gone   keep notes for deleted pages

See PLAN.md for why it is shaped this way.
"""

import argparse
import gzip
import hashlib
import json
import re
import signal
import sys
import time
import urllib.parse
import urllib.robotparser
from collections import deque
from datetime import date, datetime
from pathlib import Path

import requests
from defusedxml import ElementTree as ET
from bs4 import BeautifulSoup
from markdownify import markdownify as html_to_md

HERE = Path(__file__).parent
# Identifies the crawler to site owners reading their logs, and points them at
# something they can look up. Convention for a published crawler.
USER_AGENT = "website-crawl/1.0 (+https://github.com/aidanashby/website-crawl)"
DEFAULT_DELAY = 1.0
DEFAULT_MAX_PAGES = 500
FETCH_ESTIMATE = 0.4      # typical seconds per request, for the up-front estimate only
MAX_CONSECUTIVE_FAILURES = 10   # this many in a row means we are being blocked
CHECKPOINT_EVERY = 25     # pages between manifest writes, so a long run leaves a trail
PATH_LIMIT = 240
# A link on more than this share of pages is site furniture, not an editorial
# link. 0.80 was too generous: on a real 126-page site the main menu sat at 100%
# but a "recent posts" widget sat at 71%, so five news posts became false hubs in
# the graph. Measured on that site, every threshold from 0.30 to 0.70 excluded
# exactly the same 36 targets, and the busiest genuinely editorial link was 14%,
# so 0.50 sits in the middle of a wide safe basin. Lower it if a site's furniture
# still slips through.
BOILERPLATE_RATIO = 0.50
# Below this many pages the frequency rules are switched off: on a tiny site the
# ratio measures nothing, and suppressing a real link is worse than keeping a
# menu one. Region tagging still applies at any size.
BOILERPLATE_MIN_PAGES = 10
MAX_PAGINATION_PAGES = 200   # ceiling on the pagination pass, per crawl
THIN_WORDS = 300
TITLE_MAX = 60
DESC_MIN, DESC_MAX = 70, 160   # Google truncates around 160; under 70 wastes the slot
# A <main> or <article> is only believed to be the content root if it holds at
# least this share of the page's text. See content_root.
CONTENT_ROOT_MIN_SHARE = 0.5

# Archive and feed URLs that flood the vault and distort depth/orphan metrics.
# Matched against the PATH only. Matching the whole URL silently failed on any
# anchored pattern the moment a query string was present: Divi's own pagination
# links look like /news/page/2/?et_blog, and "/page/\d+/?$" cannot match that.
ARCHIVE_PATTERNS = [
    re.compile(r"/page/\d+/?$"),
    re.compile(r"/feed/?$"),
    re.compile(r"/comments/feed/?$"),
    re.compile(r"/category/"),
    re.compile(r"/tag/"),
    re.compile(r"/author/"),
    re.compile(r"/\d{4}/\d{2}/?$"),
    re.compile(r"/wp-json/"),
]
QUERY_PATTERNS = [re.compile(r"[?&]replytocom=")]
# Pagination, specifically: these pages hold links we want but are not pages we
# want notes for.
PAGINATION = re.compile(r"/page/\d+/?$")

# Fingerprints of content fetched by JavaScript after the page loads. We cannot
# follow it, so the honest thing is to say the link counts are understated rather
# than report the page as thin or its targets as orphans.
#
# Matched against the CONTENT ROOT only, with scripts already stripped. Matching
# the whole document flagged all 126 pages of a site: "admin-ajax.php" appears in
# the boilerplate JS of every WordPress page ever served, so it says nothing about
# this page.
#
# Deliberately narrow, and measured against real pages. "pagination", "et_pb_ajax"
# and the like also appear in the post-navigation of ordinary Divi pages, so they
# separate nothing. Ordinary pagination is not lazy loading in any case: the
# pagination pass follows it, so those links are complete. What is left here is
# content this crawler genuinely cannot reach.
LAZY_MARKERS = re.compile(
    r"load[-_]?more|facetwp|infinite[-_ ]?scroll|jet-smart-filters|data-page=", re.I)

INVALID_SEGMENT = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_NAMES = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
# <header> is deliberately NOT here. Themes put the page H1, and sometimes a hero
# block, inside it, so excluding the whole element lost real content and produced
# false word counts and missing H1s. Menus are excluded by class instead, below.
LANDMARKS = {"nav": "nav", "footer": "footer", "aside": "sidebar"}

# Furniture identified by class or id rather than by element, because a page
# builder renders all of it as plain <div>s and the element name tells you
# nothing. Divi's menu is <div class="et_pb_menu"> and can sit anywhere on the
# page, including in the middle of the content.
#
# Each pattern maps to the region its links belong to. Belt and braces with
# LANDMARKS above: a link only has to match one of the two to be kept out of the
# content link counts and out of the graph.
FURNITURE = [
    # Menus, breadcrumbs, pagination and post-to-post navigation.
    (re.compile(
        r"et_pb_(fullwidth_)?menu|et[-_]menu|et_mobile_menu|mobile[-_]menu|"
        r"main[-_]?navigation|nav[-_]?menu|menu[-_]?main|primary[-_]?menu|"
        r"site[-_]?navigation|menu[-_]?toggle|breadcrumb|"
        # Pagination CONTROLS only. A bare "pagination" match is wrong: Divi wraps
        # the whole blog grid in <div class="et_pb_ajax_pagination_container">, so
        # matching it reclassified every post card as navigation and turned 83
        # linked posts into orphans.
        r"wp-pagenavi|page[-_]numbers|pagination[-_](links|wrap|controls|nav)|"
        r"et_pb_pagination\b|posts[-_]nav|post[-_]navigation|"
        r"nav[-_]single|nav[-_]links", re.I), "nav"),
    # Sidebars and widget areas. Divi's are divs, not <aside>.
    (re.compile(r"et_pb_widget_area|widget[-_]area|\bsidebar\b|secondary-content", re.I),
     "sidebar"),
    # Cookie banners, share bars, skip links, search forms. On one real site the
    # cookie notice put the same external link into the content of 99% of pages.
    (re.compile(
        r"cmplz|cookie[-_]?(banner|notice|consent|law)|complianz|borlabs|"
        r"et_social|sharedaddy|addtoany|share[-_]?(bar|buttons)|jp-relatedposts|"
        r"skip[-_]link|screen[-_]reader[-_]text|search[-_]form|searchform", re.I), "nav"),
]


def furniture_region(tag):
    """The region this element imposes on links inside it, or None."""
    attrs = tag.get("class") or []
    ident = tag.get("id") or ""
    if not attrs and not ident:
        return None
    tokens = " ".join(list(attrs) + [ident])
    for pattern, region in FURNITURE:
        if pattern.search(tokens):
            return region
    return None


def is_menu(tag):
    """True when this element is site furniture rather than page content."""
    return furniture_region(tag) is not None


# --------------------------------------------------------------------------- URLs

def human_time(seconds):
    """Rough duration for the estimate line. Deliberately coarse - it is a hint."""
    if seconds < 90:
        return f"{round(seconds)}s"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} min"
    return f"{seconds / 3600:.1f} hours"


def normalise_url(url):
    """Lowercase scheme and host, drop fragment. The path is kept verbatim: many
    sites canonicalise with a trailing slash and 301 the slashless form, sometimes
    to a different host, so stripping it loses pages."""
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))


def slash_variant(url):
    """The same URL with its trailing slash toggled, for dedup and link resolution."""
    return url[:-1] if url.endswith("/") else url + "/"


def bare_host(netloc):
    return netloc.lower().split("@")[-1].split(":")[0].removeprefix("www.")


def same_site(url, root_host):
    return bare_host(urllib.parse.urlsplit(url).netloc) == root_host


def is_archive(url):
    p = urllib.parse.urlsplit(url)
    return (any(pat.search(p.path) for pat in ARCHIVE_PATTERNS)
            or any(pat.search(url) for pat in QUERY_PATTERNS))


def is_pagination(url):
    return bool(PAGINATION.search(urllib.parse.urlsplit(url).path))


def pagination_parent(url):
    """/news/page/2/?et_blog -> https://example.com/news/ . A listing page serves
    only its first screen of results, so the rest of the posts live on page 2, 3
    and so on. Those pages get no note of their own, but their links belong to the
    listing page, otherwise every post past the first ten looks like an orphan."""
    p = urllib.parse.urlsplit(url)
    path = PAGINATION.sub("/", p.path)
    return urllib.parse.urlunsplit((p.scheme, p.netloc, path, "", ""))


def is_sitemap(url):
    """Any URL with 'sitemap' in the path. An HTML sitemap links to everything,
    so its links are navigation whatever the reach ratio says: a small site's
    sitemap can fall under the ratio and still skew the graph."""
    return "sitemap" in urllib.parse.urlsplit(url).path.lower()


def days_since(iso_date):
    """Age in days of a YYYY-MM-DD string, or None. Saves a reader doing date
    arithmetic on every page. Only as trustworthy as the site's own dateModified,
    which is a hint rather than proof."""
    if not iso_date:
        return None
    try:
        return (date.today() - date.fromisoformat(iso_date[:10])).days
    except ValueError:
        return None


ASSET_EXTENSIONS = {
    "jpg", "jpeg", "png", "gif", "webp", "svg", "avif", "ico", "bmp",
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv",
    "zip", "gz", "mp3", "mp4", "mov", "avi", "wav", "webm",
}


def is_asset(url):
    """A file rather than a page, judged by extension."""
    last = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    return "." in last and last.rsplit(".", 1)[-1].lower() in ASSET_EXTENSIONS


# A meta description that is really a block of inline script. Seen live on a
# donation page whose CAF widget loader landed in the description tag: the page
# then advertises itself in search results with "var caf_BeneficiaryCampaignId".
SCRIPT_IN_TEXT = re.compile(r"var\s+\w+\s*=|document\.write|function\s*\(|<script", re.I)


def is_noindex(entry):
    """noindex is treated as authoritative: the site is saying this page is not
    part of its search presence, so the report should not judge it."""
    return "noindex" in (entry["meta"].get("meta_robots") or "").lower()


def sanitise_segment(seg):
    seg = urllib.parse.unquote(seg)
    seg = INVALID_SEGMENT.sub("-", seg).strip(". ")[:80]
    if seg.lower() in RESERVED_NAMES:
        seg += "-"
    return seg or "-"


def url_to_path(url, parents):
    """Relative note path for a URL. `parents` is the set of URL path strings that
    have children, so a section landing page becomes folder/index.md rather than
    colliding with folder.md."""
    path = urllib.parse.urlsplit(url).path.strip("/")
    if not path:
        return Path("home.md")
    segments = [sanitise_segment(s) for s in path.split("/")]
    if path in parents:
        # A section landing page is named after its own folder: housing/housing.md,
        # not housing/index.md. Obsidian's graph labels a node with its filename,
        # so a vault full of index.md files shows a row of identical "index" nodes
        # that can only be told apart by hovering. The rule stays mechanical and
        # reverses cleanly, and the note's own `url` field remains the authority.
        rel = Path(*segments) / (segments[-1] + ".md")
    else:
        rel = Path(*segments[:-1]) / (segments[-1] + ".md")
    if len(str(rel)) > PATH_LIMIT:
        digest = hashlib.sha256(url.encode()).hexdigest()[:8]
        stem = segments[-1][:40]
        # Flatten a pathologically deep path: keep the top folder, hash the rest.
        rel = Path(segments[0], f"{stem}-{digest}.md") if len(segments) > 1 else Path(f"{stem}-{digest}.md")
    return rel


def path_parents(url_paths):
    """Which URL path strings have at least one descendant."""
    parents = set()
    for a in url_paths:
        for b in url_paths:
            if b != a and b.startswith(a.rstrip("/") + "/"):
                parents.add(a)
                break
    return parents


# ------------------------------------------------------------------------- robots

def load_robots(session, root, ignore):
    """Return (RobotFileParser or None, sitemap_urls, crawl_delay or None)."""
    robots_url = urllib.parse.urljoin(root, "/robots.txt")
    sitemaps, delay = [], None
    try:
        text = session.get(robots_url, timeout=20).text
    except requests.RequestException:
        return None, [], None
    for line in text.splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "sitemap" and value:
            sitemaps.append(value)
        elif key == "crawl-delay":
            try:
                delay = float(value)
            except ValueError:
                pass
    rp = None
    if not ignore:
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(text.splitlines())
    return rp, sitemaps, delay


# ------------------------------------------------------------------------ sitemaps

def _local(elem):
    """Tag name without its XML namespace."""
    return elem.tag.rsplit("}", 1)[-1]


def discover_from_sitemaps(session, sitemap_urls):
    """Walk sitemap and sitemap-index files. Return {url: lastmod or None}."""
    found, seen = {}, set()
    queue = deque(sitemap_urls)
    while queue:
        sm = queue.popleft()
        if sm in seen:
            continue
        seen.add(sm)
        try:
            body = session.get(sm, timeout=30).content
            # Yoast and RankMath both offer .xml.gz, and some hosts gzip regardless
            # of the extension. requests only decompresses per Content-Encoding, so
            # sniff the magic number rather than trusting either.
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            root = ET.fromstring(body)
        except (requests.RequestException, ET.ParseError, OSError, EOFError):
            continue
        if _local(root) == "sitemapindex":
            for loc in root.iter():
                if _local(loc) == "loc" and loc.text:
                    queue.append(loc.text.strip())
        elif _local(root) == "urlset":
            for entry in root.iter():
                if _local(entry) != "url":
                    continue
                loc = next((c for c in entry if _local(c) == "loc"), None)
                lastmod = next((c for c in entry if _local(c) == "lastmod"), None)
                if loc is not None and loc.text:
                    found[loc.text.strip()] = lastmod.text.strip()[:10] if lastmod is not None and lastmod.text else None
    return found


# --------------------------------------------------------------------- page parse

def region_of(tag):
    """Nearest meaningful ancestor wins, so a menu inside a footer reads as nav."""
    for parent in tag.parents:
        if parent.name in LANDMARKS:
            return LANDMARKS[parent.name]
        if parent.name:
            region = furniture_region(parent)
            if region:
                return region
    return "content"


def content_root(soup):
    """The element holding the page's own content.

    Taking the first <main> or <article> is not safe. A page builder wraps every
    teaser in its own <article>, so on a Divi post the first one held 48 words
    while the page body held 978 - a twentyfold undercount that fed straight into
    word_count, the thin-content list, the excerpt and the outline. So: strip the
    chrome, then take the biggest candidate, and only trust it if it actually
    holds most of the page's text."""
    for junk in soup.find_all(["script", "style", "noscript", "template", "svg"]):
        junk.decompose()
    body = soup.body or soup
    # <header> is kept: it often carries the page H1 and a hero block. The menu
    # inside it is removed by class, so its link text does not count as content.
    for chrome in body.find_all(["footer", "nav", "aside"]):
        chrome.decompose()
    for furniture in body.find_all(is_menu):
        furniture.decompose()

    def words(node):
        return len(node.get_text(" ", strip=True).split())

    body_words = words(body)
    candidates = body.find_all("main") + body.find_all("article")
    if candidates and body_words:
        best = max(candidates, key=words)
        if words(best) >= CONTENT_ROOT_MIN_SHARE * body_words:
            return best
    return body


def json_ld_graph(soup):
    """Flat list of every JSON-LD node, unwrapping Yoast/RankMath @graph arrays."""
    nodes = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and "@graph" in item:
                nodes.extend(n for n in item["@graph"] if isinstance(n, dict))
            elif isinstance(item, dict):
                nodes.append(item)
    return nodes


def _types(node):
    t = node.get("@type", [])
    return [t] if isinstance(t, str) else list(t)


def clean_text(value, limit=None):
    """Collapse whitespace. A <title> or meta description split across lines would
    otherwise carry a newline straight into the YAML frontmatter and break it."""
    if not value:
        return None
    out = " ".join(str(value).split())
    return (out[:limit] if limit else out) or None


def extract(html, final_url, mode):
    """Everything we keep about one page: metadata dict, link list, rendered body."""
    soup = BeautifulSoup(html, "lxml")

    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        links.append({
            "url": urllib.parse.urljoin(final_url, href).split("#")[0],
            "region": region_of(a),
            "anchor": " ".join(a.get_text().split())[:200],
            "nofollow": "nofollow" in (a.get("rel") or []),
        })

    def meta(name, prop=False):
        tag = soup.find("meta", attrs={"property" if prop else "name": name})
        return clean_text(tag["content"]) if tag and tag.get("content") else None

    # JSON-LD lives in <script> tags, so read it before content_root strips them.
    graph = json_ld_graph(soup)
    schema_types = sorted({t for node in graph for t in _types(node)})

    root = content_root(soup)
    text = root.get_text(" ", strip=True)
    word_count = len(text.split())
    lazy = bool(LAZY_MARKERS.search(str(root)))

    author = None
    published = modified = breadcrumb = None
    for node in graph:
        types = _types(node)
        if "Person" in types and not author:
            author = node.get("name")
        if {"Article", "BlogPosting", "NewsArticle", "WebPage"} & set(types):
            published = published or node.get("datePublished")
            modified = modified or node.get("dateModified")
        if "BreadcrumbList" in types and node.get("itemListElement"):
            names = [el.get("name") for el in node["itemListElement"] if isinstance(el, dict) and el.get("name")]
            breadcrumb = " / ".join(names) or None

    canonical_tag = soup.find("link", rel="canonical")
    canonical = normalise_url(urllib.parse.urljoin(final_url, canonical_tag["href"])) if canonical_tag and canonical_tag.get("href") else None
    html_tag = soup.find("html")
    lang = html_tag.get("lang", "").strip()[:5] if html_tag else ""

    images = root.find_all("img")
    # Prefer H1s inside the content root: a themed site puts its logo or site name
    # in a header H1, which would otherwise show as a spurious "multiple H1".
    h1s = [clean_text(h.get_text()) for h in root.find_all("h1")]
    h1s = [h for h in h1s if h] or [clean_text(h.get_text()) for h in soup.find_all("h1")]
    h1s = [h for h in h1s if h]
    outline = [(h.name, " ".join(h.get_text().split())) for h in root.find_all(["h2", "h3"])]

    body_class = " ".join((soup.body.get("class") or []) if soup.body else [])
    content_type = "page"
    if m := re.search(r"\bsingle-(\w+)", body_class):
        content_type = "post" if m.group(1) == "post" else m.group(1)
    elif "page-template" in body_class or re.search(r"\bpage(-id-\d+)?\b", body_class):
        content_type = "page"

    excerpt = meta("description") or ""
    if not excerpt:
        first_p = root.find("p")
        if first_p:
            excerpt = " ".join(first_p.get_text().split())

    body_md = html_to_md(str(root), heading_style="ATX").strip() if mode == "full" else ""

    path_segments = [s for s in urllib.parse.urlsplit(final_url).path.split("/") if s]
    title = clean_text(soup.title.get_text()) if soup.title else None
    description = meta("description")

    metadata = {
        "title": title,
        "title_length": len(title) if title else 0,
        "meta_description": description,
        "meta_description_length": len(description) if description else 0,
        # Top-level URL folder, so a reader can group the site without re-parsing
        # every URL. A page at the root has no section: on a WordPress site whose
        # posts sit at /slug/ that would make each post its own "section" and turn
        # the grouping into a list of every page.
        "section": ("(home)" if not path_segments
                    else "(root)" if len(path_segments) == 1
                    else path_segments[0]),
        "path_depth": len(path_segments),
        "lazy_loaded": lazy or None,
        "links_nofollow": sum(1 for l in links if l["nofollow"]) or None,
        "meta_robots": meta("robots"),
        "canonical": canonical,
        "lang": lang or None,
        "h1": h1s or None,
        "word_count": word_count,
        "published": (published or "")[:10] or None,
        "modified": (modified or "")[:10] or None,
        "author": author,
        "breadcrumb": breadcrumb,
        "og_title": meta("og:title", prop=True),
        "og_description": meta("og:description", prop=True),
        "og_image": meta("og:image", prop=True),
        "og_type": meta("og:type", prop=True),
        "schema_types": schema_types or None,
        "content_type": content_type,
        "image_count": len(images),
        "images_missing_alt": sum(1 for i in images if not i.get("alt", "").strip()),
        "featured_image": meta("og:image", prop=True),
    }
    return {
        "meta": metadata,
        "links": links,
        "outline": outline,
        "excerpt": excerpt,
        "body_md": body_md,
        "hash": hashlib.sha256((mode + "\n" + text).encode("utf-8")).hexdigest(),
    }


# --------------------------------------------------------------------------- fetch

def fetch(session, url):
    """GET with one retry. Returns (final_url, status, content_type, body_bytes).
    Bytes, not text: BeautifulSoup detects the encoding from the <meta> charset
    far more reliably than requests' header-only guess."""
    for attempt in (1, 2):
        try:
            r = session.get(url, timeout=30, allow_redirects=True)
            return normalise_url(r.url), r.status_code, r.headers.get("content-type", ""), r.content
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(5)


# ---------------------------------------------------------------------- link graph

def build_graph(manifest, home_url):
    """Second pass over the whole corpus (from the manifest, not just this run's
    fetches). Fills in backlinks, click depth, orphan flag, boilerplate."""
    by_url = {u: e for u, e in manifest.items()}
    alias_to_canon = {}
    for u, e in manifest.items():
        for a in e.get("aliases", []):
            alias_to_canon[normalise_url(a)] = u

    def resolve(link_url):
        n = normalise_url(link_url)
        alt = slash_variant(n)
        if n in by_url:
            return n
        if alt in by_url:
            return alt
        return alias_to_canon.get(n) or alias_to_canon.get(alt)

    # Boilerplate: a target that keeps turning up in page *content*. Counting
    # content-region links only is the whole point. Counting every region instead
    # breaks small sites: on a five-page site every page sits in the main menu, so
    # every internal target hits 100% and even a genuine body link gets
    # suppressed. Links that live in nav, header, footer or aside are already
    # excluded by region, so this rule only has to catch the ones that hide inside
    # the content root - a page-builder menu, a related-posts block, a CTA widget.
    total = len(manifest) or 1
    href_pages = {}
    for u, e in manifest.items():
        for target in {resolve(l["url"]) for l in e["links"]
                       if l["region"] == "content" and resolve(l["url"])}:
            href_pages.setdefault(target, set()).add(u)
    # Below a handful of pages the ratio is noise: three pages out of five is not
    # evidence of a widget. Region tagging still applies.
    boilerplate = set() if total < BOILERPLATE_MIN_PAGES else {
        t for t, pages in href_pages.items() if len(pages) / total > BOILERPLATE_RATIO}

    # An HTML sitemap links to everything, so without this it becomes a single node
    # wired to every other note and swamps the graph. Identified by URL alone.
    #
    # This used to also catch any page whose content links reached most of the
    # site, but that measure cannot tell a sitemap from a section hub: once
    # pagination was folded in, /news/ reached 71% of the site and had its
    # news-to-post links thrown away - the exact links the graph exists to show.
    # A URL test is cruder and predictable, which is the better trade here. An
    # all-pages index NOT called "sitemap" will still show up as a hub.
    index_pages = {u for u in manifest if is_sitemap(u)}

    inbound = {u: {"content": [], "nav": []} for u in manifest}
    for u, e in manifest.items():
        for link in e["links"]:
            target = resolve(link["url"])
            if not target or target == u:
                continue
            editorial = (link["region"] == "content"
                         and target not in boilerplate
                         and u not in index_pages)
            inbound[target]["content" if editorial else "nav"].append(u)

    # Click depth: BFS from the homepage over every internal link.
    depth = {home_url: 0} if home_url in manifest else {}
    queue = deque(depth)
    while queue:
        u = queue.popleft()
        for link in manifest[u]["links"]:
            target = resolve(link["url"])
            if target and target not in depth:
                depth[target] = depth[u] + 1
                queue.append(target)

    graph = {}
    for u, e in manifest.items():
        content_in = sorted(set(inbound[u]["content"]))
        nav_in = sorted(set(inbound[u]["nav"]))
        graph[u] = {
            "links_in_content": content_in,
            "links_in_nav": nav_in,
            "click_depth": depth.get(u),
            "orphan": not content_in and u != home_url,
            # "Orphan" carries a strong meaning in SEO - unreachable - and this is
            # not that. A page with no body links in but 125 menu links in is
            # perfectly reachable and merely uncited, which is a different problem
            # with a different fix. Say which it is, next to the flag.
            "orphan_reason": (None if content_in or u == home_url
                              else "nav_only" if nav_in else "no_inbound_links"),
            "index_page": u in index_pages,
        }
    return graph, boilerplate, resolve


# ------------------------------------------------------------------------- writing

def wikilink(target_file, anchor):
    stem = target_file[:-3] if target_file.endswith(".md") else target_file
    label = (anchor or stem.rsplit("/", 1)[-1]).replace("|", "-").replace("]", ")").strip()
    return f"[[{stem}|{label}]]"


def yaml_value(v):
    if isinstance(v, list):
        return "[" + ", ".join(json.dumps(str(x), ensure_ascii=False) for x in v) + "]"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    return json.dumps(s, ensure_ascii=False) if re.search(r'[:#\[\]{}"\n]|^\s|\s$', s) else s


def render_note(entry, graph_entry, manifest, resolve, boilerplate):
    m = entry["meta"]
    fm = {
        "url": entry["url"],
        "crawled": entry["crawled"],
        "status": entry["status"],
        "content_type": m["content_type"],
        "title": m["title"],
        "title_length": m.get("title_length"),
        "meta_description": m["meta_description"],
        "meta_description_length": m.get("meta_description_length"),
        "h1": m["h1"],
        "word_count": m["word_count"],
        "section": m.get("section"),
        "path_depth": m.get("path_depth"),
        "published": m["published"],
        "modified": m["modified"],
        "author": m["author"],
        "categories": m.get("categories"),
        "tags": m.get("tags"),
        "breadcrumb": m["breadcrumb"],
        "canonical": m["canonical"] if m["canonical"] and m["canonical"] != entry["url"] else None,
        "redirected_from": entry.get("aliases") or None,
        "meta_robots": m["meta_robots"],
        "lang": m["lang"],
        "og_image": m["og_image"],
        "schema_types": m["schema_types"],
        "image_count": m["image_count"],
        "images_missing_alt": m["images_missing_alt"] or None,
        "links_nofollow": m.get("links_nofollow"),
        # Set when the page loads content with JavaScript, which this crawler does
        # not run: its link and word counts are a floor, not a total.
        "lazy_loaded": m.get("lazy_loaded"),
        "days_since_modified": days_since(m.get("modified")),
    }

    def dedupe(links, key):
        """One row per target. A listing module typically emits three links to the
        same post (image, title, "read more"), which triples the link counts and
        fills the note with repeats. Keep the most descriptive anchor: real text
        over a bare slug, which is why an anchor with a space wins."""
        best = {}
        for l in links:
            k = key(l)
            current = best.get(k)
            if current is None:
                best[k] = l
                continue
            a, b = l["anchor"] or "", current["anchor"] or ""
            if (" " in a, len(a)) > (" " in b, len(b)):
                best[k] = l
        return list(best.values())

    internal = [l for l in entry["links"] if resolve(l["url"])]
    # A link can be internal and still have no note: excluded archives, pages the
    # sitemap omits, anything that failed to fetch. Listing those as "External"
    # was actively misleading, both to a reader and to an assistant reading the
    # vault, so they get their own section.
    unresolved = [l for l in entry["links"]
                  if not resolve(l["url"]) and l["url"].startswith("http")]
    host = urllib.parse.urlsplit(entry["url"]).netloc
    site = bare_host(host)
    internal_unresolved = dedupe([l for l in unresolved if same_site(l["url"], site)],
                                 lambda l: l["url"])
    # Most of these are images and PDFs, not pages: on one site 35 of 42 were file
    # assets. Mixed together they bury the handful that are real page links worth
    # looking at.
    assets = [l for l in internal_unresolved if is_asset(l["url"])]
    uncrawled = [l for l in internal_unresolved if not is_asset(l["url"])]
    external = dedupe([l for l in unresolved if not same_site(l["url"], site)], lambda l: l["url"])
    content_out = [] if graph_entry.get("index_page") else dedupe(
        [l for l in internal
         if l["region"] == "content" and resolve(l["url"]) not in boilerplate],
        lambda l: resolve(l["url"]))
    content_targets = {resolve(l["url"]) for l in content_out}
    nav_out = dedupe([l for l in internal if resolve(l["url"]) not in content_targets],
                     lambda l: resolve(l["url"]))

    fm["links_out_content"] = len(content_out)
    fm["links_out_nav"] = len(nav_out)
    fm["links_out_uncrawled"] = len(uncrawled) or None
    fm["links_out_external"] = len(external)
    fm["links_in_content"] = len(graph_entry["links_in_content"])
    fm["links_in_nav"] = len(graph_entry["links_in_nav"])
    fm["click_depth"] = graph_entry["click_depth"]
    fm["orphan"] = graph_entry["orphan"]
    fm["orphan_reason"] = graph_entry.get("orphan_reason")
    fm["is_section_landing"] = m.get("is_section_landing")

    lines = ["---"]
    for k, v in fm.items():
        if v is None or v == [] or v == "":
            continue
        lines.append(f"{k}: {yaml_value(v)}")
    lines.append("---\n")

    lines.append(f"# {m['h1'][0] if m['h1'] else (m['title'] or entry['url'])}\n")
    if entry["mode"] == "full" and entry.get("body_md"):
        lines.append(entry["body_md"] + "\n")
    elif entry["excerpt"]:
        lines.append(entry["excerpt"] + "\n")

    if entry["outline"]:
        lines.append("## Outline")
        for level, heading in entry["outline"]:
            lines.append(("  - " if level == "h3" else "- ") + heading)
        lines.append("")

    # Only editorial links inside the page content become wikilinks. Nav, header,
    # footer, sidebar and site-wide boilerplate are listed as plain URLs, so
    # Obsidian's graph shows the relationships an editor actually created rather
    # than the shape of the main menu repeated on every page.
    def link_line(l, wiki):
        target = resolve(l["url"])
        if target and wiki:
            return "- " + wikilink(manifest[target]["file"], l["anchor"])
        if target:
            title = manifest[target]["meta"]["title"] or l["anchor"]
            return f"- {title} - {l['url']}" if title else f"- {l['url']}"
        # The host is worth showing for an external link, but it is just noise on
        # an internal one: the section heading already says which it is.
        h = urllib.parse.urlsplit(l["url"]).netloc
        suffix = "" if same_site(l["url"], site) else f" ({h})"
        return (f'- {l["url"]} "{l["anchor"]}"{suffix}' if l["anchor"]
                else f"- {l['url']}{suffix}")

    def plain_ref(u):
        title = manifest[u]["meta"]["title"]
        return f"- {title} - {u}" if title else f"- {u}"

    lines.append("## Links out")
    lines.append(f"### In content ({len(content_out)})")
    lines += [link_line(l, True) for l in content_out] or ["- none"]
    lines.append(f"\n### In nav and footer ({len(nav_out)})")
    lines += [link_line(l, False) for l in nav_out] or ["- none"]
    lines.append(f"\n### Internal pages, no note in this vault ({len(uncrawled)})")
    lines += [link_line(l, False) for l in uncrawled] or ["- none"]
    lines.append(f"\n### Files (images, PDFs) ({len(assets)})")
    lines += [link_line(l, False) for l in assets] or ["- none"]
    lines.append(f"\n### External ({len(external)})")
    lines += [link_line(l, False) for l in external] or ["- none"]

    lines.append("\n## Linked from")
    lines.append(f"### In content ({len(graph_entry['links_in_content'])})")
    lines += [f"- {wikilink(manifest[u]['file'], manifest[u]['meta']['title'])}" for u in graph_entry["links_in_content"]] or ["- none"]
    lines.append(f"\n### In nav ({len(graph_entry['links_in_nav'])})")
    lines += [plain_ref(u) for u in graph_entry["links_in_nav"]] or ["- none"]

    return "\n".join(lines) + "\n"


# -------------------------------------------------------------------------- report

def write_report(path, manifest, graph, dead_links, redirects, gone, partial=False,
                 failed=(), gone_deleted=True, stale_links=(), mode="lean",
                 sitemap_urls=()):
    L = ["# Crawl report",
         f"\n_{date.today().isoformat()} - {len(manifest)} pages - "
         # Whether the crawl stored full body text decides what any reader can
         # actually judge: in lean mode you can measure how much writing a page
         # has, never how good it is. That was not discoverable anywhere before.
         f"{'full text stored' if mode == 'full' else 'lean (excerpts only, no body text)'}_\n"]
    if sitemap_urls:
        L.append(f"\n_Sitemaps used: {', '.join(sitemap_urls)}_\n")
    if partial:
        L.append("\n> **Partial run.** The crawl stopped early, so this covers only the\n"
                 "> pages fetched so far. Run again to carry on where it stopped.\n")

    def section(title, rows):
        L.append(f"\n## {title} ({len(rows)})")
        L.extend(f"- {r}" for r in rows) if rows else L.append("- none")

    every_page = manifest

    def link(u):
        # Plain text, never a wikilink: the report is an outside observer, and a
        # wikilink here would draw an edge from it to every page it mentions,
        # making the report a hub in Obsidian's graph. A bare URL renders as an
        # external link, which creates no edge.
        title = every_page[u]["meta"]["title"]
        return f"{title} - {u}" if title else u

    # noindex is taken as authoritative. A page the site has excluded from search
    # is not a missing meta description or a thin page, it is deliberately out of
    # scope, so it is held back and listed on its own at the end. Every section
    # below therefore sees indexable pages only.
    noindex = [u for u, e in manifest.items() if is_noindex(e)]
    manifest = {u: e for u, e in manifest.items() if u not in set(noindex)}
    graph = {u: g for u, g in graph.items() if u in manifest}
    if noindex:
        L.append(f"\n_{len(manifest)} indexable, {len(noindex)} noindex (listed at the end,"
                 " and excluded from every section below)_\n")

    # Orientation first. Anyone reading this - or any assistant handed the vault -
    # wants the shape of the site before the list of faults.
    if manifest:
        words = sorted(e["meta"]["word_count"] for e in manifest.values())
        median = words[len(words) // 2]
        by_section, by_type = {}, {}
        for e in manifest.values():
            s = e["meta"].get("section") or "(home)"
            by_section[s] = by_section.get(s, 0) + 1
            t = e["meta"].get("content_type") or "page"
            by_type[t] = by_type.get(t, 0) + 1
        depths = [g["click_depth"] for g in graph.values() if g["click_depth"] is not None]
        L.append("\n## Site at a glance")
        L.append(f"- {len(manifest)} indexable pages, median {median} words"
                 f" ({words[0]} to {words[-1]})")
        L.append("- By type: " + ", ".join(f"{t} {n}" for t, n in sorted(by_type.items(), key=lambda kv: -kv[1])))
        top = sorted(by_section.items(), key=lambda kv: -kv[1])
        shown, rest = top[:12], top[12:]
        L.append("- By section: " + ", ".join(f"{s} {n}" for s, n in shown)
                 + (f", and {len(rest)} smaller section(s)" if rest else ""))
        if depths:
            spread = {d: depths.count(d) for d in sorted(set(depths))}
            L.append("- Click depth: " + ", ".join(f"{d}={n}" for d, n in spread.items())
                     + f", unreachable {len(manifest) - len(depths)}")
        lazy = [u for u, e in manifest.items() if e["meta"].get("lazy_loaded")]
        if lazy:
            L.append(f"- {len(lazy)} page(s) load content with JavaScript, so their link"
                     " counts are a floor, not a total")

    section("Orphans (no inbound content links)", [link(u) for u, g in graph.items() if g["orphan"]])
    section("Thin content (under %d words)" % THIN_WORDS,
            [f"{link(u)} - {e['meta']['word_count']}w" for u, e in manifest.items() if e["meta"]["word_count"] < THIN_WORDS])
    section("Missing meta description", [link(u) for u, e in manifest.items() if not e["meta"]["meta_description"]])
    section("Meta description outside %d-%d chars" % (DESC_MIN, DESC_MAX),
            [f"{link(u)} - {e['meta'].get('meta_description_length', 0)}"
             for u, e in manifest.items()
             if e["meta"]["meta_description"]
             and not DESC_MIN <= e["meta"].get("meta_description_length", 0) <= DESC_MAX])

    descs = {}
    for u, e in manifest.items():
        d = (e["meta"]["meta_description"] or "").strip()
        if d:
            descs.setdefault(d, []).append(u)
    section("Duplicate meta descriptions",
            [f'"{d[:60]}..." - ' + ", ".join(link(u) for u in us)
             for d, us in descs.items() if len(us) > 1])

    titles = {}
    for u, e in manifest.items():
        titles.setdefault((e["meta"]["title"] or "").strip(), []).append(u)
    section("Missing title", [link(u) for u, e in manifest.items() if not e["meta"]["title"]])
    section("Duplicate titles", [f'"{t}" - ' + ", ".join(link(u) for u in us) for t, us in titles.items() if t and len(us) > 1])
    section("Titles over %d chars" % TITLE_MAX, [f"{link(u)} - {len(e['meta']['title'])}" for u, e in manifest.items() if e["meta"]["title"] and len(e["meta"]["title"]) > TITLE_MAX])

    section("Missing H1", [link(u) for u, e in manifest.items() if not e["meta"]["h1"]])
    section("Multiple H1", [f"{link(u)} - {len(e['meta']['h1'])}" for u, e in manifest.items() if e["meta"]["h1"] and len(e["meta"]["h1"]) > 1])
    section("Click depth over 3", [f"{link(u)} - {g['click_depth']}" for u, g in graph.items() if g["click_depth"] and g["click_depth"] > 3])
    section("Unreachable from homepage", [link(u) for u, g in graph.items() if g["click_depth"] is None])
    section("Broken internal links", [f"{src} → {tgt} ({status})" for src, tgt, status in dead_links])
    # Links to an old URL that still 301s. They work, so nothing looks wrong, but
    # every one is a link the site is choosing not to point straight at.
    section("Internal links pointing at a redirect",
            [f"{frm} → {to}" for frm, to in stale_links])
    section("Meta description contains script, not prose",
            [f"{link(u)} - {(e['meta']['meta_description'] or '')[:60]}..."
             for u, e in manifest.items()
             if e["meta"]["meta_description"] and SCRIPT_IN_TEXT.search(e["meta"]["meta_description"])])
    section("Redirect chains", [f"{frm} → {to}" for frm, to in redirects])
    section("No structured data", [link(u) for u, e in manifest.items() if not e["meta"]["schema_types"]])
    section("Images missing alt text", [f"{link(u)} - {e['meta']['images_missing_alt']}" for u, e in manifest.items() if e["meta"]["images_missing_alt"]])
    section("Zero-content pages (likely JS-rendered)", [link(u) for u, e in manifest.items() if e["meta"]["word_count"] == 0])
    section("Content loaded by JavaScript (link counts understated)",
            [link(u) for u, e in manifest.items() if e["meta"].get("lazy_loaded")])

    ext = {}
    for e in manifest.values():
        for l in e["links"]:
            host = urllib.parse.urlsplit(l["url"]).netloc
            if host and l["url"].startswith("http"):
                ext[host] = ext.get(host, 0) + 1
    section("External link targets by frequency",
            [f"{h} - {n}" for h, n in sorted(ext.items(), key=lambda kv: -kv[1])])
    # A URL still listed in the sitemap but failing now is NOT "removed upstream":
    # it was seen this run, so it never reaches the gone list. Its note is left in
    # place and would quietly go stale, so say so here rather than only on screen.
    section("Failed to fetch this run (note may be stale)",
            [f"{u} - {why}" for u, why in failed])
    section("No longer on the site (notes %s)" % ("deleted" if gone_deleted else "kept"),
            list(gone))
    section("Noindex (excluded from every section above)",
            [f"{link(u)} - {every_page[u]['meta']['meta_robots']}" for u in sorted(noindex)])

    path.write_text("\n".join(L) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Map a website into an Obsidian vault.")
    ap.add_argument("url")
    ap.add_argument("--full", action="store_true", help="re-fetch every page")
    ap.add_argument("--full-text", action="store_true", help="store full body as Markdown")
    ap.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    ap.add_argument("--delay", type=float, default=None)
    ap.add_argument("--ignore-robots", action="store_true")
    ap.add_argument("--include-archives", action="store_true")
    ap.add_argument("--keep-gone", action="store_true",
                    help="keep notes for pages that no longer exist (they are"
                         " deleted by default)")
    ap.add_argument("--skip-pagination", action="store_true",
                    help="do not follow /page/2/ links to harvest listing links")
    args = ap.parse_args()

    start = normalise_url(args.url)
    root_host = bare_host(urllib.parse.urlsplit(start).netloc)
    domain_dir = HERE / "crawled" / root_host
    domain_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = domain_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() else {}
    mode = "full" if args.full_text else "lean"
    today = date.today().isoformat()

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    rp, robot_sitemaps, robot_delay = load_robots(session, start, args.ignore_robots)
    delay = args.delay if args.delay is not None else (robot_delay or DEFAULT_DELAY)

    def allowed(u):
        return args.ignore_robots or rp is None or rp.can_fetch(USER_AGENT, u)

    sitemap_urls = robot_sitemaps or [urllib.parse.urljoin(start, "/sitemap.xml")]
    discovered = discover_from_sitemaps(session, sitemap_urls)
    discovered = {normalise_url(u): lm for u, lm in discovered.items() if same_site(u, root_host)}
    link_crawl = not discovered
    if link_crawl:
        print("No usable sitemap - falling back to a link crawl from the homepage.")
        discovered = {start: None}

    if not args.include_archives:
        discovered = {u: lm for u, lm in discovered.items() if not is_archive(u)}

    kind = "Full re-fetch" if args.full else "Incremental run"
    print(f"\n  {kind} - {len(discovered)} page(s) to consider, {delay}s delay")
    print(f"  Rough estimate: {human_time(len(discovered) * (delay + FETCH_ESTIMATE))}"
          " (plus a link-check pass at the end)")
    print("\n  Ctrl+C  stop cleanly - keeps every page fetched so far, and the next")
    print("          run picks up where this one stopped")
    print("  Ctrl+S  pause the console, Ctrl+Q to resume")
    print("\n  Notes are written at the end: backlinks and click depth need every")
    print("  page first. manifest.json updates as it goes.\n", flush=True)

    seen, redirects, failed = set(), [], []
    vanished = {}          # url -> status, for pages the server says are gone
    queue = deque(discovered)
    new = changed = unchanged = 0
    aborted = False
    misses = 0            # consecutive failures; a run of these means we are blocked
    since_checkpoint = 0
    total = None if link_crawl else len(discovered)
    done = 0

    def mark_seen(u):
        seen.add(u)
        seen.add(slash_variant(u))

    def progress(code, url, note=""):
        nonlocal done
        done += 1
        counter = f"{done:>4}/{total}" if total else f"{done:>4}/?"
        path = urllib.parse.urlsplit(url).path or "/"
        if len(path) > 44:
            path = path[:41] + "..."
        print(f"  [{counter}] {code:>4}  {path:<44} {note}", flush=True)

    def checkpoint():
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8")

    # Ctrl+C sets a flag rather than raising, so an in-flight request finishes and
    # we fall through to the normal write path. A second Ctrl+C kills it outright.
    def on_interrupt(signum, frame):
        nonlocal aborted
        if aborted:
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            raise KeyboardInterrupt
        aborted = True
        print("\n  Stopping after this page. Press Ctrl+C again to quit now.\n", flush=True)

    signal.signal(signal.SIGINT, on_interrupt)

    while queue:
        if aborted:
            break
        url = queue.popleft()
        if url in seen or not same_site(url, root_host):
            continue
        mark_seen(url)
        if not args.include_archives and is_archive(url):
            continue
        if not allowed(url):
            failed.append((url, "blocked by robots.txt"))
            progress("--", url, "blocked by robots.txt")
            misses += 1
            if misses >= MAX_CONSECUTIVE_FAILURES:
                aborted = True
                break
            continue

        known = manifest.get(url)
        lastmod = discovered.get(url)
        # `not known["lastmod"]` used to count as fresh, so a page first seen
        # without a lastmod stayed stale forever once the sitemap started
        # reporting one. New date information is a reason to refetch, not to skip.
        known_lastmod = known.get("lastmod") if known else None
        fresh = bool(known) and known.get("mode") == mode and not args.full and (
            not lastmod or (bool(known_lastmod) and lastmod <= known_lastmod)
        )

        if fresh:
            unchanged += 1
            progress("--", url, "unchanged")
            if link_crawl:
                for l in known["links"]:
                    if same_site(l["url"], root_host):
                        queue.append(normalise_url(l["url"]))
            continue

        try:
            final_url, status, ctype, html = fetch(session, url)
        except requests.RequestException as e:
            failed.append((url, str(e)))
            progress("ERR", url, str(e)[:60])
            misses += 1
            if misses >= MAX_CONSECUTIVE_FAILURES:
                aborted = True
                break
            continue
        time.sleep(delay)

        if final_url != url:
            redirects.append((url, final_url))
            manifest.setdefault(final_url, {}).setdefault("aliases", [])
            if url not in manifest[final_url]["aliases"]:
                manifest[final_url]["aliases"].append(url)
            if not same_site(final_url, root_host):
                continue
            url = final_url
            if url in seen:
                continue
            mark_seen(url)

        if status != 200 or "html" not in ctype:
            failed.append((url, f"status {status}, {ctype or 'no content-type'}"))
            # 404 and 410 are the server stating the page is gone. Nothing else
            # is: a 403 is usually a WAF, a 5xx or a timeout is usually temporary,
            # and deleting a note over either would lose a page that still exists.
            if status in (404, 410):
                vanished[url] = status
            if status >= 400:
                progress(status, url, "BLOCKED" if status in (401, 403, 429) else "error")
                misses += 1
                if misses >= MAX_CONSECUTIVE_FAILURES:
                    aborted = True
                    break
            else:
                # A PDF or image in the sitemap is not a sign of trouble, so it
                # does not count towards the blocked-run detector.
                progress(status, url, f"skipped ({ctype.split(';')[0] or 'no type'})")
            continue

        data = extract(html, url, mode)
        entry = manifest.get(url, {})
        entry.update({
            "url": url,
            "file": entry.get("file"),  # assigned after we know the full path set
            "hash": data["hash"],
            "mode": mode,
            "lastmod": lastmod or entry.get("lastmod"),
            "crawled": today,
            "status": status,
            "meta": data["meta"],
            "links": data["links"],
            "outline": data["outline"],
            "excerpt": data["excerpt"],
            "body_md": data["body_md"],
        })
        manifest[url] = entry
        if known:
            changed += 1
        else:
            new += 1
        misses = 0
        progress(status, url, f"{data['meta']['word_count']:,}w")

        since_checkpoint += 1
        if since_checkpoint >= CHECKPOINT_EVERY:
            checkpoint()
            since_checkpoint = 0

        if link_crawl and len(seen) < args.max_pages:
            for l in data["links"]:
                if same_site(l["url"], root_host):
                    queue.append(normalise_url(l["url"]))

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    if misses >= MAX_CONSECUTIVE_FAILURES:
        print(f"\n  {misses} failures in a row - the site is very likely blocking this crawler.")
        print(f"  Stopping here. {new + changed} page(s) fetched this run.")
        print("  Try a longer --delay, or --ignore-robots if this is a staging site.")
        print("  Writing the manifest and report for what we have.\n", flush=True)
    elif aborted:
        print(f"\n  Stopped. {new + changed} page(s) fetched this run. Writing what we have.\n", flush=True)

    # Drop half-built alias stubs that never resolved to a real page.
    manifest = {u: e for u, e in manifest.items() if e.get("meta")}

    # Pagination pass: a listing page serves only its first screen of results, so
    # /news/ shows ten posts and the rest sit on /news/page/2/ and beyond. Those
    # pages get no note - they would distort depth and orphan metrics - but their
    # links are folded into the listing page they belong to. Without this, every
    # post past the first screen looks orphaned.
    if not args.skip_pagination and not aborted:
        follow, queued = deque(), set()

        def consider(link_url):
            n = normalise_url(link_url)
            if (n not in queued and same_site(n, root_host) and is_pagination(n)
                    and allowed(n) and pagination_parent(n) in manifest):
                queued.add(n)
                follow.append(n)

        for e in list(manifest.values()):
            for l in e["links"]:
                consider(l["url"])

        if follow:
            print(f"  Following {len(follow)} pagination page(s) for links"
                  " (no notes written for these)...", flush=True)
        harvested = 0
        while follow and len(queued) <= MAX_PAGINATION_PAGES:
            purl = follow.popleft()
            parent = pagination_parent(purl)
            try:
                final_url, status, ctype, phtml = fetch(session, purl)
            except requests.RequestException:
                continue
            time.sleep(delay)
            if status != 200 or "html" not in ctype:
                continue
            pdata = extract(phtml, final_url, mode)
            existing = {(l["url"], l["region"]) for l in manifest[parent]["links"]}
            for l in pdata["links"]:
                if l["region"] == "content" and (l["url"], l["region"]) not in existing:
                    manifest[parent]["links"].append(l)
                    existing.add((l["url"], l["region"]))
                    harvested += 1
            for l in pdata["links"]:
                consider(l["url"])
        if harvested:
            print(f"  Folded {harvested} link(s) from pagination into their listing pages.",
                  flush=True)

    # Assign note paths now the full URL set is known.
    url_paths = {urllib.parse.urlsplit(u).path.strip("/") for u in manifest}
    parents = path_parents(url_paths | {""})
    # Two URLs can sanitise to the same note path: segments are truncated at 80
    # characters, and long slugs sharing a prefix are common on a news site. Left
    # alone, one page silently overwrites the other and its links vanish from the
    # graph. Give any collision a short hash of its URL.
    taken, collisions, renamed = {}, 0, []
    for u in sorted(manifest):
        rel = str(url_to_path(u, parents)).replace("\\", "/")
        if rel in taken:
            stem, _, ext = rel.rpartition(".")
            rel = f"{stem}-{hashlib.sha256(u.encode()).hexdigest()[:8]}.{ext}"
            collisions += 1
        taken[rel] = u
        # `section` needs the whole corpus, so it is finalised here rather than in
        # extract(). A section's own landing page belongs in that section: giving
        # /housing/ the section "(root)" while /housing/barstaple-house/ got
        # "housing" split a section from its own front door and dumped the landing
        # page in with every root-level post.
        path = urllib.parse.urlsplit(u).path.strip("/")
        segments = [s for s in path.split("/") if s]
        landing = path in parents
        manifest[u]["meta"]["is_section_landing"] = landing or None
        manifest[u]["meta"]["section"] = (
            "(home)" if not segments
            else segments[-1] if landing
            else segments[0] if len(segments) > 1
            else "(root)")
        was = manifest[u].get("file")
        if was and was != rel:
            renamed.append(was)
        manifest[u]["file"] = rel
    if collisions:
        print(f"  {collisions} note path collision(s) resolved with a URL hash.", flush=True)

    # A note whose path changed leaves its old file behind, which would show in
    # the vault as a duplicate. Only paths this tool recorded in the manifest are
    # removed, never anything else in the folder.
    for old in renamed:
        stale = domain_dir / old
        if stale.exists() and old not in taken:
            stale.unlink()
            try:
                stale.parent.relative_to(domain_dir)
                stale.parent.rmdir()        # only succeeds when now empty
            except (OSError, ValueError):
                pass
    if renamed:
        print(f"  {len(renamed)} note(s) renamed, old files removed.", flush=True)

    print(f"  {len(manifest)} page(s) in the vault. Building link graph...", flush=True)
    graph, boilerplate, resolve = build_graph(manifest, start)

    # HEAD internal link targets that were never in the crawl set.
    candidates = set()
    for e in manifest.values():
        for l in e["links"]:
            n = normalise_url(l["url"])
            if same_site(n, root_host) and not resolve(n) and n.startswith("http") and not is_archive(n):
                candidates.add(n)
    dead_links, stale_links = [], []
    if candidates:
        print(f"  Checking {len(candidates)} internal link target(s) not in the crawl set"
              f" (about {human_time(len(candidates) * (delay + FETCH_ESTIMATE))})...", flush=True)
    for target in sorted(candidates):
        try:
            hr = session.head(target, timeout=20, allow_redirects=True)
            # Plenty of servers refuse HEAD outright, and a WAF often answers it
            # with 403 while serving the same page happily to a browser. Confirm
            # with a GET before calling a link broken: a false "broken link" sends
            # you hunting for a problem that does not exist.
            if hr.status_code in (401, 403, 405, 501) or hr.status_code >= 400:
                time.sleep(delay)
                hr = session.get(target, timeout=30, allow_redirects=True, stream=True)
                hr.close()
            if hr.status_code >= 400:
                src = next((u for u, e in manifest.items() if any(normalise_url(l["url"]) == target for l in e["links"])), "?")
                dead_links.append((src, target, hr.status_code))
            elif normalise_url(hr.url) != target and not is_asset(target):
                # Works, so nothing looks broken, but the site is linking to an
                # old URL and leaning on a redirect to fix it.
                stale_links.append((target, normalise_url(hr.url)))
        except requests.RequestException:
            pass
        time.sleep(delay)

    # Write notes whose rendered output actually changed.
    print("  Writing notes...", flush=True)
    written = 0
    for u, e in manifest.items():
        note = render_note(e, graph[u], manifest, resolve, boilerplate)
        dest = domain_dir / e["file"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or dest.read_text("utf-8") != note:
            dest.write_text(note, encoding="utf-8")
            written += 1

    # Reconcile: URLs gone from the sitemap.
    # An aborted run never reached most of the sitemap, so almost nothing is in
    # `seen`. Without this guard the report would declare the whole site removed
    # upstream, and the default deletion would empty the vault.
    # Two ways a page stops existing: the sitemap no longer lists it, or the
    # server answers 404/410 for a URL the sitemap still carries. Both are
    # deleted by default, so an ordinary run leaves a vault that matches the live
    # site. --keep-gone turns that off.
    #
    # A link crawl never prunes: without a sitemap there is no authoritative list
    # of what exists, only what happened to be reachable this run. An aborted or
    # blocked run never prunes either, because most of the site was never seen.
    gone = []
    if not link_crawl and not aborted:
        gone = sorted(set([u for u in manifest if u not in seen]) | set(vanished))
        for u in gone:
            if args.keep_gone or u not in manifest:
                continue
            note = domain_dir / manifest[u]["file"]
            note.unlink(missing_ok=True)
            try:
                note.parent.relative_to(domain_dir)
                note.parent.rmdir()          # only succeeds when now empty
            except (OSError, ValueError):
                pass
            del manifest[u]

    # A 404 is reported as gone, not as a fetch failure: listing it in both places
    # reads like two separate problems.
    still_failing = [(u, why) for u, why in failed if u not in vanished]
    write_report(domain_dir / "REPORT.md", manifest, graph, dead_links, redirects,
                 gone, aborted, still_failing, gone_deleted=not args.keep_gone,
                 stale_links=stale_links, mode=mode, sitemap_urls=sitemap_urls)
    checkpoint()

    orphans = sum(1 for g in graph.values() if g["orphan"])
    print()
    if aborted:
        print("  PARTIAL RUN - run again to carry on where this stopped.")
    print(f"  new        {new}")
    print(f"  changed    {changed}")
    print(f"  unchanged  {unchanged}")
    print(f"  failed     {len(failed)}")
    print(f"  notes written {written}")
    print(f"\n  {len(manifest)} pages, {orphans} orphan(s), {len(dead_links)} broken internal link(s)")
    for u, why in failed:
        print(f"    {u}: {why}")
    if gone:
        verb = "kept (--keep-gone)" if args.keep_gone else "deleted"
        print(f"  {len(gone)} page(s) no longer on the site, notes {verb}:")
        for u in gone:
            print(f"    {u}" + (f" (status {vanished[u]})" if u in vanished else ""))
    print(f"\n  vault: {domain_dir}\n")


if __name__ == "__main__":
    main()
