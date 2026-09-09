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
| Page chrome | `footer`, `nav`, `aside`, plus anything matching the furniture class patterns (menus, breadcrumbs, pagination controls, widget areas, cookie banners, share bars), excluded from content, word count and outline. `header` is **kept**: themes put the page H1 and hero copy inside it. Links are still extracted from the **whole** document, tagged by region. |
| Layout | One folder per domain, subfolders mirroring the URL path. |
| Change detection | `manifest.json` keyed on final URL. Default run fetches new plus `lastmod`-changed; `--full` re-fetches everything. |
| Link graph | Persisted in the manifest, so the second pass works on incremental runs. |
| Politeness | `robots.txt` respected by default, `--ignore-robots` for staging. `--delay` sets the rate (default 1.0s). |
| Scope | Same host only. Leading `www.` stripped for the folder name. |
| Vault boundary | Each domain folder is its own Obsidian vault. Wikilinks resolve within it; `[[blog/my-post]]` needs no domain prefix. |
| Version control | `git init` plus a public GitHub repo (`website-crawl`). `crawled/` is **not** tracked: it is site content rather than source, it gets large, and it is regenerable. The content hash already decides when a note is rewritten, so git was never doing the change detection. `git init` inside a single vault if one site needs history. |

## Shape

```
website-crawl/
├── crawl.py
├── Crawl Website.cmd       prompts for URL, then N (new) / F (full)
├── requirements.txt
├── .gitignore              venv/, __pycache__/, crawled/
├── venv/
├── README.md
├── PLAN.md
├── ANALYSE-PROMPT.md       prompt for handing a crawled vault to Claude
└── crawled/
    └── example.com/
        ├── manifest.json   final URL → file, hash, mode, lastmod, outbound links
        ├── REPORT.md       regenerated every run, plain URLs so it stays out
        │                   of the graph
        ├── home.md
        ├── about.md
        ├── blog/
        │   ├── blog.md
        │   └── my-post.md
        └── services/
            └── consulting.md
```

### URL to path

| URL | File |
|---|---|
| `/` | `home.md` |
| `/about/` | `about.md` |
| `/blog/` | `blog/blog.md` |
| `/blog/my-post/` | `blog/my-post.md` |

A URL that has children lives inside its own folder and takes that folder's
name, so a section landing page and its children never collide. Named after the
folder rather than `index.md` because Obsidian labels a graph node with its
filename, and a vault of `index.md` files shows a row of identical nodes.
Collisions get eight hex characters of the URL's SHA-256 appended.

- Trailing slash normalised away before mapping.
- Percent-decoded segments sanitised: `<>:"|?*`, control characters and reserved
  Windows device names replaced with `-`.
- Full path capped at 240 characters. Over that, truncate the stem and append
  eight hex characters of the URL's SHA-256, so truncation stays unique.
- Query strings are kept in the URL key and in the note path, since some sites
  genuinely serve distinct pages from them.
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
- **Content root**: `<body>` with `footer`, `nav`, `aside` and furniture removed,
  then the *largest* `<main>` or `<article>` if it holds at least half the page's
  text. Not the first one: a page builder wraps every teaser card in its own
  `<article>`, and taking the first undercounted a real post twentyfold. Drives
  word count, outline, excerpt, full text and the content hash.
- **Links**: extracted from the *whole* document, each tagged `content`, `nav`,
  `footer` or `sidebar` by its nearest landmark or furniture ancestor.
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
- **Boilerplate detection**: any href appearing *in page content* on more than
  50% of pages is marked boilerplate and excluded from the content link counts
  even when it sits inside the content root. Catches related-post blocks and CTA
  widgets that a tag blocklist misses. Only content-region appearances count:
  including nav ones pushed every internal target on a five-page site to 100%.
  Switched off below ten pages, where the ratio measures nothing.
- **Content links only** rewritten to full-path wikilinks with an alias,
  `[[blog/my-post|Anchor text]]`. Full paths because a dozen `index.md` files
  make a bare stem ambiguous. Menu, footer, sidebar and boilerplate links are
  listed as plain URLs so Obsidian draws no edge for them: the graph is meant to
  show the relationships an editor created, not the main menu repeated on every
  page. Four rules, any one of which excludes a link: the landmark region tag,
  the furniture class or id, the 50% content-frequency rule, and `sitemap` in the
  URL. Between them they catch a page-builder menu (Divi's
  `<div class="et_pb_menu">` sits outside `<nav>` but repeats site-wide) and an
  HTML sitemap.
- Repeated links to one target from a single page collapse to one row. A Divi
  blog module emits three per post (image, title, "read more"), which otherwise
  triples every content link count on a listing page.
- Internal link targets **not** in the crawl set get one HEAD request each and
  their status recorded. This is the only way broken internal links are ever
  found, since a sitemap by definition lists working pages.

### 5. Write REPORT.md

### 6. Reconcile

- A note is rewritten only when its rendered output actually changed, so an
  incremental run touches only what genuinely moved.
- URLs in the manifest but no longer on the site are deleted by default. `--keep-gone` keeps
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
title_length: 31
meta_description_length: 142
word_count: 812
section: blog
path_depth: 2
published: 2025-11-03
days_since_modified: 150
modified: 2026-04-12
author: "Jo Bloggs"
categories: [Fundraising]
tags: [appeals, winter]
breadcrumb: "Home / Blog / How we ran the winter appeal"
og_image: https://example.com/wp-content/uploads/winter.jpg
schema_types: [Article, BreadcrumbList, Person]
image_count: 6
images_missing_alt: 2
links_out_content: 9
links_out_nav: 24
links_out_uncrawled: 2
links_out_external: 3
links_nofollow: 1
links_in_content: 4
links_in_nav: 1
click_depth: 2
orphan: false
---
```

Conditional fields, written only when they apply: `canonical` (only when it
differs from `url`), `redirected_from`, `meta_robots`, `lang`, `featured_image`,
`links_nofollow`, `lazy_loaded`.

Some derived values *are* stored, against the original plan: `title_length`,
`meta_description_length`, `path_depth`, `section`, `days_since_modified`. They
are all computable from other fields, and the first draft left them out for that
reason. In practice the vault's main reader is an assistant answering questions
across 126 files at once, and making it recompute the same arithmetic per file
is a poor trade for five short keys. `section` in particular cannot be derived
reliably without the whole corpus, since a root-level page is not its own
section.

`lazy_loaded` marks a page whose content is fetched by JavaScript, so its link
and word counts are a floor rather than a total.

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

### In nav and footer (24)
- Services - https://example.com/services/

### Internal, no note in this vault (2)
- https://example.com/category/fundraising/ "Fundraising"

### External (3)
- https://charitycommission.gov.uk/x "the guidance" (charitycommission.gov.uk)

## Linked from
### In content (4)
- [[home|Home]]

### In nav (1)
- Blog - https://example.com/blog/
```

Only the content sections carry wikilinks. Everything else is plain text, so
Obsidian draws no edge for it.

The content wikilinks and the "Linked from" section are what make Obsidian's
graph view show genuine editorial structure rather than a cloud of unconnected
notes, or a hairball of menu links.

## REPORT.md

Rows cite pages as plain URLs, never wikilinks. A wikilink here would draw an
edge from the report to every page it mentions and make it the largest hub in
the vault, drowning the structure the graph exists to show.

`noindex` is authoritative: those pages are held out of every section and listed
once at the end. The site has already said they are not part of its search
presence, so flagging their thin content or missing description is noise.

- **Site at a glance**, first: page count, median and range of word counts, a
  breakdown by content type and section, the click-depth spread. Orientation
  before faults.
- **Orphans**: no inbound content links
- **Thin content**: under 300 words in the content root
- **Meta description**: missing, outside 70-160 characters, or duplicated
- **Titles**: missing, duplicated, or over 60 characters
- **H1**: missing, duplicated across pages, or more than one on a page
- **Click depth over 3**, and pages unreachable from the homepage
- **Broken internal links**, from the HEAD pass
- **Redirect chains**
- **Pages with no schema**
- **Images missing alt text**, by page
- **Zero-content pages**, usually JS-rendered
- **Content loaded by JavaScript**, where link counts are a floor
- **External link targets** by frequency
- **Failed to fetch this run**, whose notes may now be stale
- **No longer on the site**, whose notes were deleted
- **Noindex**, listed last and excluded from every section above

## CLI

```
python crawl.py https://example.com                    new and lastmod-changed
python crawl.py https://example.com --full             re-fetch every page
python crawl.py https://example.com --full-text        store full body as Markdown
python crawl.py https://example.com --max-pages 500    link-crawl cap
python crawl.py https://example.com --delay 0.2        override the crawl delay
python crawl.py https://example.com --ignore-robots    staging sites
python crawl.py https://example.com --include-archives
python crawl.py https://example.com --skip-pagination  do not follow /page/2/
python crawl.py https://example.com --keep-gone         keep notes for deleted pages
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
  settings change. The `--full` run plus the content hash is the
  real change detection.
- **No JavaScript rendering.** WordPress serves server-side HTML, so this is
  fine for the sites in scope. A JS-driven site produces thin notes, which is
  what the zero-content flag is for.

## Steps

1. ~~`git init`, `.gitignore`, `requirements.txt`, venv.~~
2. ~~Write `crawl.py`.~~
3. ~~Test against one small site: verify sitemap discovery, path mapping, region
   tagging and the manifest.~~ Done against a small five-page site.
4. ~~Verify counts, spot-check notes, open the vault in Obsidian and confirm the
   graph looks like the site.~~ This is what surfaced the header, content-root
   and boilerplate bugs recorded below.
5. ~~Full run against the real sites.~~ Done: a 126-page WordPress and Divi site.
6. `gh repo create` as private, push. **Outstanding.**
7. ~~`README.md`.~~ Plus `ANALYSE-PROMPT.md`.

### What real crawls changed

The plan above survived contact with two live sites; these did not, and each was
found by looking at output rather than by reasoning about it:

- **`<header>` was excluded from content.** Themes put the page H1 and hero copy
  inside it, so word counts and H1 detection were wrong. Menus are now excluded
  by class instead, which is also what catches a page-builder menu sitting in the
  middle of the content.
- **The content root took the first `<article>`.** Divi wraps every teaser card
  in one, so a real post measured 48 words against a true 978. Median word count
  across the site was wrong by a factor of twenty, and the thin-content list was
  almost entirely false.
- **Boilerplate counted links from every region.** On a five-page site every page
  is in the menu, so every internal target hit 100% and genuine body links were
  suppressed.
- **Listing pages only serve their first screen.** Without following pagination,
  every post past the tenth looked orphaned.
- **Anchored URL patterns were matched against the whole URL**, so a query string
  defeated them: Divi's `/news/page/2/?et_blog` was never recognised as an
  archive.

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
