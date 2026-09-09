# Website crawl

Maps a website into an Obsidian vault so you can see how its content links
together. One note per page, folders mirroring the URL path, SEO and structure
metadata in frontmatter, every internal link rewritten as a wikilink so
Obsidian's graph view shows the real shape of the site.

Built for content planning (finding gaps and orphaned pages) and SEO review of
server-rendered WordPress sites, and tuned against WordPress with the Divi page
builder in particular.

The `Crawl Website.cmd` launcher is Windows-only. `crawl.py` itself runs
anywhere Python does.

## Running it

Double-click **`Crawl Website.cmd`**, enter a URL, and choose:

- **N — New and changed pages** (default). Fetches pages new since the last run,
  plus any the sitemap flags as modified.
- **F — Full re-fetch**. Re-fetches everything and rewrites only the notes whose
  rendered output actually changed, so the file timestamps show what moved.

Both modes delete notes for pages that are no longer on the site. See **Pages
that disappear** below.

Or from a terminal:

```
venv\Scripts\python crawl.py https://example.com
venv\Scripts\python crawl.py https://example.com --full
venv\Scripts\python crawl.py https://example.com --full-text
venv\Scripts\python crawl.py https://example.com --max-pages 500
venv\Scripts\python crawl.py https://example.com --delay 0.2
venv\Scripts\python crawl.py https://example.com --ignore-robots
venv\Scripts\python crawl.py https://example.com --include-archives
venv\Scripts\python crawl.py https://example.com --keep-gone
venv\Scripts\python crawl.py https://example.com --skip-pagination
```

| Flag | Effect |
|---|---|
| `--full` | Re-fetch every page, not just new/changed ones. |
| `--full-text` | Store the full page body as Markdown, not just the excerpt. Recorded per file; switching modes re-renders the note. |
| `--max-pages N` | Cap for the link-crawl fallback (default 500). Ignored when a sitemap is found. |
| `--delay S` | Seconds between requests. Defaults to the site's `Crawl-delay`, else 1.0. |
| `--ignore-robots` | Bypass `robots.txt` `Disallow` rules. Staging and dev installs usually carry `Disallow: /`. Still honours the crawl delay. |
| `--include-archives` | Keep `/page/2/`, `/category/`, `/tag/`, `/author/`, date archives and feeds. Excluded by default because they distort the depth and orphan metrics. |
| `--keep-gone` | Keep notes for pages that no longer exist. They are deleted by default. |
| `--skip-pagination` | Do not follow `/page/2/` links to harvest a listing page's remaining links. |

## Setup

```
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
```

Python 3.11+. Dependencies: `requests`, `beautifulsoup4`, `lxml`, `markdownify`,
`defusedxml`, plus `pytest` for the tests.

## Tests

```
venv\Scripts\python -m pytest
```

No network: they run against HTML fixtures. They cover the parts that have
actually gone wrong on real sites — region tagging, the content root, the
boilerplate ratio, URL to path mapping, and the report's structure — so run them
after touching any of the thresholds at the top of `crawl.py`.

## How it works

1. Reads `robots.txt` for `Sitemap:` directives and `Crawl-delay`, else tries
   `/sitemap.xml`. Sitemap-index files are expanded recursively. No usable
   sitemap means a breadth-first link crawl from the homepage, capped by
   `--max-pages`.
2. Fetches each page, following redirects. Everything is keyed on the **final**
   URL; the original is recorded as an alias so a redirect never produces two
   notes.
3. Parses with BeautifulSoup. `<footer>`, `<nav>`, `<aside>` and anything matching
   the furniture patterns are removed before measuring word count, the heading
   outline and the content hash. `<header>` is kept, since it often holds the page
   H1. Links are extracted from the **whole** page and tagged by region
   (`content`, `nav`, `footer`, `sidebar`), because the nav *is* the site's link
   structure.
4. Follows `/page/2/` and beyond on listing pages, folding their links into the
   listing page. No notes are written for paginated URLs.
5. A second pass over the whole corpus (read from `manifest.json`, so it works on
   incremental runs) computes backlinks, click depth from the homepage, and the
   orphan flag. See **What the graph shows** for how a content link is told apart
   from site furniture.
6. Internal link targets not in the crawl set get a `HEAD` request, confirmed with
   a `GET` before being called broken, so broken internal links surface in the
   report without false positives from servers that refuse `HEAD`.
7. Writes one note per page, deletes notes for pages no longer on the site,
   regenerates `REPORT.md`, updates `manifest.json`.

## Layout

```
crawled/
└── example.com/            one Obsidian vault per domain (leading www. dropped)
    ├── manifest.json       final URL -> file, hash, mode, lastmod, links
    ├── REPORT.md           regenerated every run
    ├── home.md             the homepage
    ├── about.md
    └── blog/
        ├── blog.md         the /blog/ landing page
        └── my-post.md
```

Open the domain folder directly in Obsidian. A page with children lives inside
its own folder and takes that folder's name, so a section landing page and its
children never collide. It is named after the folder rather than `index.md`
because Obsidian labels a graph node with its filename, and a vault full of
`index.md` files shows a row of identical nodes you can only tell apart by
hovering. The homepage is `home.md` for the same reason.

The rule is mechanical and reverses cleanly, and every note carries its own `url`
in the frontmatter, which stays the authority for what a note actually is. Notes
whose path changes have their old file removed on the next crawl.

## What the graph shows

Only editorial links inside a page's content become wikilinks. Menu, footer and
sidebar links, and anything repeating across most of the site, are listed as
plain URLs instead, so Obsidian draws no edge for them. The graph therefore
shows the relationships an editor actually created, not the main menu repeated
across every page.

Four independent rules do this, and a link only needs to trip one:

- **Position.** Anything inside `<nav>`, `<footer>` or `<aside>` is tagged by
  region and excluded. `<header>` is deliberately **not** excluded: themes put
  the page H1, and sometimes a hero block, inside it, so dropping the whole
  element lost real content and produced false word counts and missing H1s.
- **Furniture class or id.** Menus, breadcrumbs, pagination controls, sidebars
  and widget areas, cookie banners, share bars, skip links and search forms are
  matched by class or id and excluded wherever they sit. A page builder renders
  all of these as plain `<div>`s, so the element name tells you nothing: Divi's
  menu is `<div class="et_pb_menu">` and can appear mid-content. This is belt and
  braces with the rule above, and it is what keeps a header's menu out while
  keeping the header's heading in.
- **Frequency.** Any link appearing *in page content* on more than
  `BOILERPLATE_RATIO` (50%) of pages is treated as boilerplate, wherever it sits
  in the markup. Only content-region appearances are counted, because nav links
  are already handled by the rule above. Counting every region breaks small
  sites: on a five-page site every page is in the main menu, so every internal
  target reaches 100% and even a genuine body link gets suppressed.
- **Sitemap pages.** Any URL with `sitemap` in the path is an index page, so all
  of its outbound links are treated as navigation. An HTML sitemap links to
  everything, and without this it becomes a single node wired to every other
  note.

  This is a URL test on purpose. An earlier version also caught any page whose
  content links reached most of the site, but that cannot tell a sitemap from a
  section hub: with pagination folded in, a news landing page reached 71% of the
  site and had its news-to-post links discarded. An all-pages index not called
  "sitemap" will still show as a hub; rename it or add a pattern.

Both frequency rules switch off below `BOILERPLATE_MIN_PAGES` (10 pages), where
a ratio measures nothing. Region tagging still applies at any size, and a URL
containing `sitemap` is always excluded.

Repeated links to the same target from one page collapse to a single row, since
a listing module usually emits three (image, title, "read more"). The most
descriptive anchor wins.


Both counts stay visible in the frontmatter (`links_out_content` versus
`links_out_nav`, `links_in_content` versus `links_in_nav`), and the nav links
are still listed in the note, so nothing is lost. If a site's navigation is
unusual enough to slip through all three rules, lower the ratios at the top of
`crawl.py`.

Expect the orphan count to be high on a site whose pages are reachable mainly
through the menu. That is the finding, not a fault in the crawl: `orphan: true`
means no other page links to it from body copy.

**Two isolation figures, and the second is usually the interesting one.** A page
linked from a news index is not an orphan, but "the page that lists everything
links to me" is not the same as "another page thought me worth citing". Any page
whose own content links reach more than `LISTING_RATIO` (25%) of the site counts
as a listing, and `editorially_isolated` marks pages nothing but a listing cites.
On one real site the orphan count read 26 while a further 83 pages were cited by
nothing except the news index, which was the site's actual problem. Listing links
stay in the graph either way: a section hub pointing at its own posts is real
structure.

## While it is running

Every page prints as it is handled, so you can see a slow site apart from a
blocked one:

```
  [  34/847]  200  /blog/winter-appeal/                   1,420w
  [  35/847]  403  /members/area/                         BLOCKED
  [  36/847]   --  /about/                                unchanged
```

Ten failures in a row stops the crawl with a warning, on the assumption the site
is blocking us. Whatever was fetched is still written, and the next run carries
on from there.

- **Ctrl+C stops cleanly.** Every page fetched so far is kept and the report is
  written. Press it twice to quit immediately.
- **Ctrl+S pauses the console, Ctrl+Q resumes.** With QuickEdit on, clicking in
  the window also suspends it until you press Enter.
- **`manifest.json` is written every 25 pages**, so a long crawl leaves a trail
  and a crash costs you nothing.

Notes only appear at the end. Backlinks and click depth need every page before
any note can be rendered.

## Pages that disappear

**Every run deletes notes for pages that no longer exist**, so the vault matches
the live site without you having to remember a flag. This applies to the
incremental run and the full re-fetch alike, including the double-click launcher.
Deleted pages are named on screen and listed in the report under **No longer on
the site**. Pass `--keep-gone` to leave them in place instead.

A page counts as gone in two cases:

- **The sitemap no longer lists it.** Every sitemap URL is marked as seen before
  the freshness check, so a page skipped as unchanged still counts as present. A
  manifest entry missing from this run's sitemap is genuinely gone.
- **The server answers 404 or 410** for a URL the sitemap still carries, which is
  a stale sitemap entry.

Nothing else counts. A **403** is usually a firewall, and a **timeout or 5xx** is
usually temporary; deleting a note over either would throw away a page that still
exists. Those stay put and are listed under **Failed to fetch this run** instead,
so a note that may have gone stale still surfaces.

Two situations suspend deletion entirely:

- **A link crawl** (no usable sitemap). Without a sitemap there is no
  authoritative list of what exists, only what happened to be reachable.
- **An interrupted or blocked run**, because most of the site was never reached.
  The report says the run was partial.

Notes are cheap to lose: the next crawl rebuilds anything deleted in error.

A note whose *path* changes (a URL moves, or a page gains children and becomes a
section landing page) has its old file removed automatically. Only paths recorded
in `manifest.json` are ever touched, never anything else in the folder.

## What the report covers

Orphans, pages cited only by a listing, thin content, missing or duplicate titles
and meta descriptions, descriptions that are really inline script, H1 problems,
pages more than three clicks deep, pages unreachable from the homepage, broken
internal links, internal links pointing at a redirect, linked-to pages with no
note ranked by inbound links, redirect chains, pages with no structured data,
images missing alt text, likely JS-rendered pages, and external link targets by
frequency.

It opens with **Site at a glance**: page count, median and range of word counts,
a breakdown by content type and by section, the click-depth spread, and how many
pages load content with JavaScript. Orientation before faults.

**`noindex` is treated as authoritative.** Those pages are excluded from every
section and listed once at the end instead. A page the site has removed from
search is not a missing meta description, so judging it only adds noise. The
header line says how many pages were set aside.

The report cites pages as plain URLs rather than wikilinks, deliberately. A
wikilink would draw a graph edge from the report to every page it mentions and
turn it into the biggest hub in the vault, which is the opposite of what the
graph is for.

## Handing a crawl to Claude

`ANALYSE-PROMPT.md` holds a copy-and-paste prompt that points Claude at a
crawled folder, explains the frontmatter and the caveats, and asks it to explore
the site, diagnose SEO problems, and propose a plan of action.

## Notes

- **`lastmod` is a hint, not truth.** Some themes set it to the publish date and
  never touch it again; some SEO plugins bump every URL at once. A `--full` run
  plus the content hash is the real change detection: a note is only rewritten
  when its rendered output differs.
- **`crawled/` is not tracked by git.** It is site content rather than source,
  it gets large, and it can be regenerated by re-running the crawl. Run
  `git init` inside a vault folder if you want version history for one site.
- **Pagination is followed, lazy loading is not.** A listing page serves only its
  first screen of results, so `/news/page/2/` and beyond are fetched for their
  links and folded into the listing page they belong to. No notes are written for
  them. Without this, every post past the first screen looks orphaned. Use
  `--skip-pagination` to turn it off.
- **No JavaScript rendering.** Fine for server-rendered WordPress. A JS-driven
  site produces thin notes, which the zero-content flag in the report catches.
  A page that loads its content with a "load more" button or an AJAX filter is
  flagged `lazy_loaded`, and its link counts are a floor rather than a total, so
  its targets may look like orphans when they are not.
- **`.obsidian/` is not tracked**, so vault settings can differ between machines.

## Licence

MIT. See [LICENSE](LICENSE).
