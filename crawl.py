"""Map a website into an Obsidian vault: one note per page, folders mirroring the
URL path, metadata in frontmatter, links as wikilinks so the graph view shows
real structure.

    python crawl.py https://example.com              new and lastmod-changed pages
    python crawl.py https://example.com --full        re-fetch everything
    python crawl.py https://example.com --full-text   store full body as Markdown
    python crawl.py https://example.com --max-pages N link-crawl cap (no sitemap)
    python crawl.py https://example.com --delay S     override crawl delay
    python crawl.py https://example.com --ignore-robots
    python crawl.py https://example.com --include-archives
    python crawl.py https://example.com --prune       delete notes for dead URLs

See PLAN.md for why it is shaped this way.
"""

import argparse
import hashlib
import json
import re
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
USER_AGENT = "website-crawl/1.0 (personal site mapping)"
DEFAULT_DELAY = 1.0
DEFAULT_MAX_PAGES = 500
PATH_LIMIT = 240
BOILERPLATE_RATIO = 0.80
THIN_WORDS = 300
TITLE_MAX = 60

# Archive and feed URLs that flood the vault and distort depth/orphan metrics.
ARCHIVE_PATTERNS = [
    re.compile(r"/page/\d+/?$"),
    re.compile(r"/feed/?$"),
    re.compile(r"/comments/feed/?$"),
    re.compile(r"/category/"),
    re.compile(r"/tag/"),
    re.compile(r"/author/"),
    re.compile(r"/\d{4}/\d{2}/?$"),
    re.compile(r"/wp-json/"),
    re.compile(r"[?&]replytocom="),
]

INVALID_SEGMENT = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_NAMES = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
LANDMARKS = {"nav": "nav", "header": "header", "footer": "footer", "aside": "sidebar"}


# --------------------------------------------------------------------------- URLs

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
    return any(pat.search(url) for pat in ARCHIVE_PATTERNS)


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
        return Path("index.md")
    segments = [sanitise_segment(s) for s in path.split("/")]
    if path in parents:
        rel = Path(*segments) / "index.md"
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
            root = ET.fromstring(body)
        except (requests.RequestException, ET.ParseError):
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
    for parent in tag.parents:
        if parent.name in LANDMARKS:
            return LANDMARKS[parent.name]
    return "content"


def content_root(soup):
    for junk in soup.find_all(["script", "style", "noscript", "template", "svg"]):
        junk.decompose()
    for name in ("main", "article"):
        node = soup.find(name)
        if node:
            return node
    body = soup.body or soup
    for chrome in body.find_all(["header", "footer", "nav", "aside"]):
        chrome.decompose()
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
        return tag["content"].strip() if tag and tag.get("content") else None

    # JSON-LD lives in <script> tags, so read it before content_root strips them.
    graph = json_ld_graph(soup)
    schema_types = sorted({t for node in graph for t in _types(node)})

    root = content_root(soup)
    text = root.get_text(" ", strip=True)
    word_count = len(text.split())

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
    h1s = [" ".join(h.get_text().split()) for h in soup.find_all("h1")]
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

    metadata = {
        "title": (soup.title.get_text().strip() if soup.title else None),
        "meta_description": meta("description"),
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

    # Boilerplate: internal hrefs present on >80% of pages.
    total = len(manifest) or 1
    href_pages = {}
    for u, e in manifest.items():
        for target in {resolve(l["url"]) for l in e["links"] if resolve(l["url"])}:
            href_pages.setdefault(target, set()).add(u)
    boilerplate = {t for t, pages in href_pages.items() if len(pages) / total > BOILERPLATE_RATIO}

    inbound = {u: {"content": [], "nav": []} for u in manifest}
    for u, e in manifest.items():
        for link in e["links"]:
            target = resolve(link["url"])
            if not target or target == u:
                continue
            bucket = "content" if link["region"] == "content" and target not in boilerplate else "nav"
            inbound[target][bucket].append(u)

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
        "meta_description": m["meta_description"],
        "h1": m["h1"],
        "word_count": m["word_count"],
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
    }

    internal = [l for l in entry["links"] if resolve(l["url"])]
    external = [l for l in entry["links"] if not resolve(l["url"]) and l["url"].startswith("http")]
    content_out = [l for l in internal if l["region"] == "content" and resolve(l["url"]) not in boilerplate]
    nav_out = [l for l in internal if l not in content_out]

    fm["links_out_content"] = len(content_out)
    fm["links_out_nav"] = len(nav_out)
    fm["links_out_external"] = len(external)
    fm["links_in_content"] = len(graph_entry["links_in_content"])
    fm["links_in_nav"] = len(graph_entry["links_in_nav"])
    fm["click_depth"] = graph_entry["click_depth"]
    fm["orphan"] = graph_entry["orphan"]

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

    def link_line(l):
        target = resolve(l["url"])
        if target:
            return "- " + wikilink(manifest[target]["file"], l["anchor"])
        host = urllib.parse.urlsplit(l["url"]).netloc
        return f'- {l["url"]} "{l["anchor"]}" ({host})' if l["anchor"] else f"- {l['url']} ({host})"

    lines.append("## Links out")
    lines.append(f"### In content ({len(content_out)})")
    lines += [link_line(l) for l in content_out] or ["- none"]
    lines.append(f"\n### In nav and footer ({len(nav_out)})")
    lines += [link_line(l) for l in nav_out] or ["- none"]
    lines.append(f"\n### External ({len(external)})")
    lines += [link_line(l) for l in external] or ["- none"]

    lines.append("\n## Linked from")
    lines.append(f"### In content ({len(graph_entry['links_in_content'])})")
    lines += [f"- {wikilink(manifest[u]['file'], manifest[u]['meta']['title'])}" for u in graph_entry["links_in_content"]] or ["- none"]
    lines.append(f"\n### In nav ({len(graph_entry['links_in_nav'])})")
    lines += [f"- {wikilink(manifest[u]['file'], manifest[u]['meta']['title'])}" for u in graph_entry["links_in_nav"]] or ["- none"]

    return "\n".join(lines) + "\n"


# -------------------------------------------------------------------------- report

def write_report(path, manifest, graph, dead_links, redirects, gone):
    L = ["# Crawl report", f"\n_{date.today().isoformat()} - {len(manifest)} pages_\n"]

    def section(title, rows):
        L.append(f"\n## {title} ({len(rows)})")
        L.extend(f"- {r}" for r in rows) if rows else L.append("- none")

    def link(u):
        return f"[[{manifest[u]['file'][:-3]}|{manifest[u]['meta']['title'] or u}]]"

    section("Orphans (no inbound content links)", [link(u) for u, g in graph.items() if g["orphan"]])
    section("Thin content (under %d words)" % THIN_WORDS,
            [f"{link(u)} - {e['meta']['word_count']}w" for u, e in manifest.items() if e["meta"]["word_count"] < THIN_WORDS])
    section("Missing meta description", [link(u) for u, e in manifest.items() if not e["meta"]["meta_description"]])

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
    section("Redirect chains", [f"{frm} → {to}" for frm, to in redirects])
    section("No structured data", [link(u) for u, e in manifest.items() if not e["meta"]["schema_types"]])
    section("Images missing alt text", [f"{link(u)} - {e['meta']['images_missing_alt']}" for u, e in manifest.items() if e["meta"]["images_missing_alt"]])
    section("Zero-content pages (likely JS-rendered)", [link(u) for u, e in manifest.items() if e["meta"]["word_count"] == 0])

    ext = {}
    for e in manifest.values():
        for l in e["links"]:
            host = urllib.parse.urlsplit(l["url"]).netloc
            if host and l["url"].startswith("http"):
                ext[host] = ext.get(host, 0) + 1
    section("External link targets by frequency",
            [f"{h} - {n}" for h, n in sorted(ext.items(), key=lambda kv: -kv[1])])
    section("Pages removed upstream", list(gone))

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
    ap.add_argument("--prune", action="store_true")
    args = ap.parse_args()

    start = normalise_url(args.url)
    root_host = bare_host(urllib.parse.urlsplit(start).netloc)
    domain_dir = HERE / root_host
    domain_dir.mkdir(exist_ok=True)
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

    print(f"\n{'Full re-fetch' if args.full else 'Incremental run'} - {len(discovered)} URL(s) to consider, delay {delay}s\n")

    seen, redirects, failed = set(), [], []
    queue = deque(discovered)
    new = changed = unchanged = 0

    def mark_seen(u):
        seen.add(u)
        seen.add(slash_variant(u))

    while queue:
        url = queue.popleft()
        if url in seen or not same_site(url, root_host):
            continue
        mark_seen(url)
        if not args.include_archives and is_archive(url):
            continue
        if not allowed(url):
            failed.append((url, "blocked by robots.txt"))
            continue

        known = manifest.get(url)
        lastmod = discovered.get(url)
        fresh = known and known.get("mode") == mode and not args.full and (
            not lastmod or not known.get("lastmod") or lastmod <= known["lastmod"]
        )

        if fresh:
            unchanged += 1
            if link_crawl:
                for l in known["links"]:
                    if same_site(l["url"], root_host):
                        queue.append(normalise_url(l["url"]))
            continue

        try:
            final_url, status, ctype, html = fetch(session, url)
        except requests.RequestException as e:
            failed.append((url, str(e)))
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

        if link_crawl and len(seen) < args.max_pages:
            for l in data["links"]:
                if same_site(l["url"], root_host):
                    queue.append(normalise_url(l["url"]))

    # Drop half-built alias stubs that never resolved to a real page.
    manifest = {u: e for u, e in manifest.items() if e.get("meta")}

    # Assign note paths now the full URL set is known.
    url_paths = {urllib.parse.urlsplit(u).path.strip("/") for u in manifest}
    parents = path_parents(url_paths | {""})
    for u, e in manifest.items():
        e["file"] = str(url_to_path(u, parents)).replace("\\", "/")

    graph, boilerplate, resolve = build_graph(manifest, start)

    # HEAD internal link targets that were never in the crawl set.
    candidates = set()
    for e in manifest.values():
        for l in e["links"]:
            n = normalise_url(l["url"])
            if same_site(n, root_host) and not resolve(n) and n.startswith("http") and not is_archive(n):
                candidates.add(n)
    dead_links = []
    for target in sorted(candidates):
        try:
            hr = session.head(target, timeout=20, allow_redirects=True)
            if hr.status_code >= 400:
                src = next((u for u, e in manifest.items() if any(normalise_url(l["url"]) == target for l in e["links"])), "?")
                dead_links.append((src, target, hr.status_code))
        except requests.RequestException:
            pass
        time.sleep(delay)

    # Write notes whose rendered output actually changed.
    written = 0
    for u, e in manifest.items():
        note = render_note(e, graph[u], manifest, resolve, boilerplate)
        dest = domain_dir / e["file"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or dest.read_text("utf-8") != note:
            dest.write_text(note, encoding="utf-8")
            written += 1

    # Reconcile: URLs gone from the sitemap.
    gone = []
    if not link_crawl:
        gone = [u for u in list(manifest) if u not in seen]
        for u in gone:
            if args.prune:
                (domain_dir / manifest[u]["file"]).unlink(missing_ok=True)
                del manifest[u]

    write_report(domain_dir / "REPORT.md", manifest, graph, dead_links, redirects, gone)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")

    orphans = sum(1 for g in graph.values() if g["orphan"])
    print(f"  new        {new}")
    print(f"  changed    {changed}")
    print(f"  unchanged  {unchanged}")
    print(f"  failed     {len(failed)}")
    print(f"  notes written {written}")
    print(f"\n  {len(manifest)} pages, {orphans} orphan(s), {len(dead_links)} broken internal link(s)")
    for u, why in failed:
        print(f"    {u}: {why}")
    if gone:
        verb = "pruned" if args.prune else "gone from sitemap (use --prune)"
        print(f"  {len(gone)} {verb}")
    print(f"\n  vault: {domain_dir}\n")


if __name__ == "__main__":
    main()
