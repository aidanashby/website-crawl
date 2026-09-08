# Plan: website crawler

Maps a site into an Obsidian vault: one note per page, folders mirroring the URL
path, metadata in frontmatter, links as wikilinks so the graph view shows real
structure. Built to find content gaps and SEO problems on sites I administer.

## Decisions

| Question | Decision |
|---|---|
| Entry point | `robots.txt` → `Sitemap:` directives, else `/sitemap.xml`. Sitemap-index files expanded recursively. |
| No sitemap | Breadth-first link crawl from the homepage, capped by `--max-pages` (default 500). |
| Stack | `requests`, `beautifulsoup4`, `lxml`, `markdownify` (for `--full-text`) in a venv. |
| Body content | Meta description + heading outline + link lists. `--full-text` stores the full main content as Markdown. |
| Page chrome | `header`, `footer`, `nav`, `aside` excluded from content, word count and outline. Links are still extracted from the **whole** document, tagged by region. |
| Layout | One folder per domain, subfolders mirroring the URL path. |
| Change detection | `manifest.json` keyed on final URL. Default run fetches new plus `lastmod`-changed; `--full` re-fetches everything. |
| Link graph | Persisted in the manifest, so the second pass works on incremental runs. |
| Politeness | `robots.txt` respected by default, `--ignore-robots` for staging. `--delay` sets the rate (default 1.0s). |
| Scope | Same host only. Leading `www.` stripped for the folder name. |
| Vault boundary | Each domain folder is its own Obsidian vault. Wikilinks resolve within it; `[[blog/my-post]]` needs no domain prefix. |
| Version control | `git init` plus a private GitHub repo (`website-crawl`), created now. Crawled folders are tracked: `git diff` after a `--full` run is the point. `**/.obsidian/` is not tracked. |

## Shape

```
website-crawl/
├── crawl.py
├── Crawl Website.cmd       prompts for URL, then N (new) / F (full)
├── requirements.txt
├── .gitignore              venv/, __pycache__/
├── venv/
├── README.md
├── PLAN.md
└── example.com/
    ├── manifest.json       final URL → file, hash, mode, lastmod, outbound links
    ├── REPORT.md           regenerated every run
    ├── index.md
    ├── about.md
    ├── blog/
    │   ├── index.md
    │   └── my-post.md
    └── services/
        └── consulting.md
```

### URL to path

| URL | File |
|---|---|
| `/` | `index.md` |
| `/about/` | `about.md` |
| `/blog/` | `blog/index.md` |
| `/blog/my-post/` | `blog/my-post.md` |

A URL that has children becomes `index.md` inside its own folder, so a section
landing page and its children never collide.

- Trailing slash normalised away before mapping.
- Percent-decoded segments sanitised: `<>:"|?*`, control characters and reserved
  Windows device names replaced with `-`.
- Full path capped at 240 characters. Over that, truncate the stem and append
  eight hex characters of the URL's SHA-256, so truncation stays unique.
- Query-string URLs skipped and logged, unless a page has no other form.
- Non-HTML responses (PDF, images) are never written as notes, only recorded as
  link targets.

### Excluded by default

`/page/\d+/`, `/feed/`, `/comments/feed/`, `/category/`, `/tag/`, `/author/`,
date archives (`/YYYY/MM/`), `/wp-json/`, `?replytocom=`.

These flood the vault and distort the orphan and depth figures. Yoast excludes
some of them from the sitemap but not all. `--include-archives` disables the
whole list.

## Flow

### 1. Resolve entry points

Fetch `robots.txt`. Read `Sitemap:` directives and `Crawl-delay`. Fall back to
`/sitemap.xml`. If neither exists or the XML is malformed, fall back to the link
crawl and say so in the output.

### 2. Build the URL set

Expand sitemap-index files recursively. Collect each URL with its `lastmod`
where given. Apply the exclusion patterns.

### 3. Fetch and parse each page

- Skip if the URL is in the manifest, the body mode matches, and `lastmod` is
  not newer. `--full` ignores all three.
- GET, following redirects. **Key everything on the final URL** and record the
  requested URL as an alias, so `/old/` and `/new/` do not both produce a note.
- Reject anything that is not `text/html`.
- Parse with BeautifulSoup and `lxml`.
- **Content root**: the first of `<main>`, `<article>`, else `<body>` with
  `header`, `footer`, `nav` and `aside` removed. Drives word count, outline,
  excerpt, full text and the content hash.
- **Links**: extracted from the *whole* document, each tagged `content`, `nav`,
  `header`, `footer` or `sidebar` by its nearest landmark ancestor.
- Hash the normalised content root, not the whole page, so a nav edit does not
  mark all 400 pages as changed.

Extracting links from the full document while taking content from the stripped
one is deliberate. The nav *is* the site's link structure; strip it and click
depth becomes meaningless and almost everything looks orphaned. Keeping the
region tag is also the most useful SEO distinction available: a footer link on
every page carries no signal, an in-content contextual link does.

### 4. Second pass over the whole graph

Reads outbound links for **every** page from the manifest, not just the pages
fetched this run. Without that, an incremental run would compute orphan and
depth figures from a partial graph and be silently wrong.

- Backlinks split into `links_in_content` and `links_in_nav`.
- `click_depth`: BFS from the homepage over all links, nav included, because
  that is how a visitor actually reaches a page.
- `orphan`: `links_in_content == 0` and not the homepage. Nav reachability is
  not editorial linking, and under the naive definition nothing is ever an
  orphan.
- **Boilerplate detection**: any href appearing on more than 80% of pages is
  marked boilerplate and excluded from the content link counts even when it sits
  inside the content root. Catches related-post blocks and CTA widgets that a
  tag blocklist misses.
- Internal links rewritten to full-path wikilinks with an alias,
  `[[blog/my-post|Anchor text]]`. Full paths because a dozen `index.md` files
  make bare `[[index]]` ambiguous.
- Internal link targets **not** in the crawl set get one HEAD request each and
  their status recorded. This is the only way broken internal links are ever
  found, since a sitemap by definition lists working pages.

### 5. Write REPORT.md

### 6. Reconcile

- A note is rewritten only when its rendered output actually changed, so
  incremental runs produce a clean `git diff`.
- URLs in the manifest but no longer reachable are reported. `--prune` deletes
  their files.

## Frontmatter

Empty fields are omitted. A note carrying thirty keys with eighteen of them
blank is unreadable in Obsidian and useless to grep.

```yaml
---
url: https://example.com/blog/my-post/
crawled: 2026-09-08
status: 200
content_type: post
title: "How we ran the winter appeal"
meta_description: "..."
h1: ["How we ran the winter appeal"]
word_count: 812
published: 2025-11-03
modified: 2026-04-12
author: "Aidan Ashby"
categories: [Fundraising]
tags: [appeals, winter]
breadcrumb: "Home / Blog / How we ran the winter appeal"
og_image: https://example.com/wp-content/uploads/winter.jpg
schema_types: [Article, BreadcrumbList, Person]
image_count: 6
images_missing_alt: 2
links_out_content: 9
links_out_nav: 24
links_out_external: 3
links_in_content: 4
links_in_nav: 1
click_depth: 2
orphan: false
---
```

Conditional fields, written only when they apply: `canonical` (only when it
differs from `url`), `redirected_from`, `meta_robots`, `lang` (only when it
differs from the site default), `parent`, `featured_image`, `alias`.

Derived values are deliberately absent: title length, description length,
whether the H1 matches the title, path depth. All of them are computable from
fields already present, so the report computes them rather than the frontmatter
storing them.

`schema_types` walks the JSON-LD `@graph` array, since Yoast and RankMath nest
everything inside a single one.

## Note body

```markdown
# How we ran the winter appeal

Meta description here, or the full content as Markdown under `--full-text`.

## Outline
- Planning the appeal
  - Setting the target
- What we learned

## Links out
### In content (9)
- [[blog/christmas-hampers|our hamper scheme]]
- https://charitycommission.gov.uk/x "the guidance" (charitycommission.gov.uk)

### In nav and footer (24)
- [[services/index|Services]]

## Linked from
### In content (4)
- [[index|Home]]

### In nav (1)
- [[blog/index|Blog]]
```

The wikilinks and the "Linked from" section are what make Obsidian's graph view
show genuine site structure rather than a cloud of unconnected notes.

## REPORT.md

- **Orphans**: no inbound content links
- **Thin content**: under 300 words in the content root
- **Missing meta description**
- **Titles**: missing, duplicated, or over 60 characters
- **H1**: missing, duplicated across pages, or more than one on a page
- **Click depth over 3**
- **Broken internal links**, from the HEAD pass
- **Redirect chains**
- **Pages with no schema**
- **Images missing alt text**, by page
- **External link targets** by frequency
- **Zero-content pages**, usually JS-rendered

## CLI

```
python crawl.py https://example.com                    new and lastmod-changed
python crawl.py https://example.com --full             re-fetch all, clean git diff
python crawl.py https://example.com --full-text        store full body as Markdown
python crawl.py https://example.com --max-pages 500    link-crawl cap
python crawl.py https://example.com --delay 0.2        override the crawl delay
python crawl.py https://example.com --ignore-robots    staging sites
python crawl.py https://example.com --include-archives
python crawl.py https://example.com --prune            delete notes for dead URLs
```

`--full-text` is recorded per file in the manifest, and a mode change counts as
a change. Otherwise re-running with the flag would hit an unchanged hash and
leave the vault mixing both formats.

`--ignore-robots` exists because staging and dev WordPress installs almost
always carry `Disallow: /`, and those are exactly the sites worth mapping. It
still honours the crawl delay.

## Failure handling

- Non-200 or non-HTML: log it, skip it, keep going, report at the end. Never
  write a note from a response that failed validation.
- Network error: one retry after a pause, then skip.
- Malformed sitemap XML: fall back to the link crawl, say so.
- A page that parses to zero content: write the note, flag it in the report.
  Usually a JS-rendered page.

## Notes on the data

- **`lastmod` is a hint, not truth.** Some themes set it to the publish date and
  never update it on edit; Yoast sometimes bumps every URL at once over a
  settings change. The `--full` run plus the content hash plus `git diff` is the
  real change detection.
- **No JavaScript rendering.** WordPress serves server-side HTML, so this is
  fine for the sites in scope. A JS-driven site produces thin notes, which is
  what the zero-content flag is for.

## Steps

1. `git init`, `.gitignore`, `requirements.txt`, venv.
2. Write `crawl.py`.
3. Test against one small site: verify sitemap discovery, path mapping, region
   tagging and the manifest, without waiting on a full crawl.
4. Verify counts, spot-check three notes, open the vault in Obsidian and confirm
   the graph looks like the site.
5. Full run against the real sites.
6. `gh repo create` as private, push.
7. `README.md`.

## What I am not building

- **JavaScript rendering.** No headless browser.
- **Multi-domain crawls in one run.** One URL, one run.
- **Authenticated pages.**
- **Scheduled runs.**
- **A config file.** Flags plus constants at the top of `crawl.py`.
- **Concurrency.** `--delay` covers the speed problem. Add a thread pool when a
  crawl is genuinely too slow, not before.
- **`REFERENCE.md`.** It earned its place in the Divi project by documenting one
  site's quirks. This tool is generic, so the README holds what little there is.

Add any of these when there is an actual reason, not before.
