# Website crawl

Maps a website into an Obsidian vault so you can see how its content links
together. One note per page, folders mirroring the URL path, SEO and structure
metadata in frontmatter, every internal link rewritten as a wikilink so
Obsidian's graph view shows the real shape of the site.

Built for content planning (finding gaps and orphaned pages) and SEO review on
sites I administer.

## Running it

Double-click **`Crawl Website.cmd`**, enter a URL, and choose:

- **N — New and changed pages** (default). Fetches pages new since the last run,
  plus any the sitemap flags as modified.
- **F — Full re-fetch**. Re-fetches everything and overwrites only what actually
  changed, so `git diff` shows exactly what moved.

Or from a terminal:

```
venv\Scripts\python crawl.py https://example.com
venv\Scripts\python crawl.py https://example.com --full
venv\Scripts\python crawl.py https://example.com --full-text
venv\Scripts\python crawl.py https://example.com --max-pages 500
venv\Scripts\python crawl.py https://example.com --delay 0.2
venv\Scripts\python crawl.py https://example.com --ignore-robots
venv\Scripts\python crawl.py https://example.com --include-archives
venv\Scripts\python crawl.py https://example.com --prune
```

| Flag | Effect |
|---|---|
| `--full` | Re-fetch every page, not just new/changed ones. |
| `--full-text` | Store the full page body as Markdown, not just the excerpt. Recorded per file; switching modes re-renders the note. |
| `--max-pages N` | Cap for the link-crawl fallback (default 500). Ignored when a sitemap is found. |
| `--delay S` | Seconds between requests. Defaults to the site's `Crawl-delay`, else 1.0. |
| `--ignore-robots` | Bypass `robots.txt` `Disallow` rules. Staging and dev installs usually carry `Disallow: /`. Still honours the crawl delay. |
| `--include-archives` | Keep `/page/2/`, `/category/`, `/tag/`, `/author/`, date archives and feeds. Excluded by default because they distort the depth and orphan metrics. |
| `--prune` | Delete notes for URLs that have vanished from the sitemap. |

## Setup

```
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
```

Python 3.11+. Dependencies: `requests`, `beautifulsoup4`, `lxml`, `markdownify`,
`defusedxml`.

## How it works

1. Reads `robots.txt` for `Sitemap:` directives and `Crawl-delay`, else tries
   `/sitemap.xml`. Sitemap-index files are expanded recursively. No usable
   sitemap means a breadth-first link crawl from the homepage, capped by
   `--max-pages`.
2. Fetches each page, following redirects. Everything is keyed on the **final**
   URL; the original is recorded as an alias so a redirect never produces two
   notes.
3. Parses with BeautifulSoup. `<header>`, `<footer>`, `<nav>` and `<aside>` are
   removed before measuring word count, the heading outline and the content
   hash. Links are extracted from the **whole** page and tagged by region
   (`content`, `nav`, `header`, `footer`, `sidebar`), because the nav *is* the
   site's link structure.
4. A second pass over the whole corpus (read from `manifest.json`, so it works on
   incremental runs) computes backlinks, click depth from the homepage, and the
   orphan flag. A link on more than 80% of pages is treated as boilerplate and
   left out of the content link counts.
5. Internal link targets not in the crawl set get a `HEAD` request so broken
   internal links surface in the report.
6. Writes one note per page, regenerates `REPORT.md`, updates `manifest.json`.

## Layout

```
example.com/                one Obsidian vault per domain (leading www. dropped)
├── manifest.json           final URL -> file, hash, mode, lastmod, links
├── REPORT.md               regenerated every run
├── index.md                the homepage
├── about.md
└── blog/
    ├── index.md
    └── my-post.md
```

Open the domain folder directly in Obsidian. A page with children becomes
`index.md` inside its own folder, so a section landing page and its children
never collide.

## What the report covers

Orphans, thin content, missing or duplicate titles and meta descriptions, H1
problems, pages more than three clicks deep, pages unreachable from the homepage,
broken internal links, redirect chains, pages with no structured data, images
missing alt text, likely JS-rendered pages, and external link targets by
frequency.

## Notes

- **`lastmod` is a hint, not truth.** Some themes set it to the publish date and
  never touch it again; some SEO plugins bump every URL at once. The `--full`
  run plus the content hash plus `git diff` is the real change detection.
- **No JavaScript rendering.** Fine for server-rendered WordPress. A JS-driven
  site produces thin notes, which the zero-content flag in the report catches.
- **`.obsidian/` is not tracked**, so vault settings can differ between machines.
