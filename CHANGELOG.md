# Changelog

Notable changes to website-crawl. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[semantic](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-09-09

First public release. Everything below describes what the tool does, since there
is no earlier version to compare against.

### Crawling

- Discovers URLs from `robots.txt` `Sitemap:` directives, else `/sitemap.xml`,
  expanding sitemap-index files recursively and handling gzipped sitemaps. Falls
  back to a breadth-first link crawl from the homepage when no sitemap is usable.
- Keys everything on the **final** URL after redirects, recording the original as
  an alias, so a redirect never produces two notes.
- Follows listing pagination (`/news/page/2/` and beyond) to harvest links,
  without writing notes for the paginated URLs. Without this, every post past a
  listing's first screen looks orphaned.
- Respects `robots.txt` and `Crawl-delay` by default. `--ignore-robots` for
  staging installs, which usually carry `Disallow: /`.
- Incremental by default: fetches pages that are new or that the sitemap flags as
  modified. `--full` re-fetches everything.
- Deletes notes for pages that no longer exist, whether they have left the sitemap
  or now return 404/410. A 403, timeout or 5xx never counts as gone.

### Output

- One Obsidian note per page, in `crawled/<domain>/`, with folders mirroring the
  URL path. A section landing page takes its folder's name (`housing/housing.md`)
  rather than `index.md`, so the graph does not show a row of identical nodes.
- SEO and structure metadata in YAML frontmatter, including title and description
  lengths, word count, section, click depth, per-region link counts, and the
  isolation flags described below.
- **Only editorial links inside page content become wikilinks.** Menus, footers,
  sidebars, breadcrumbs, cookie banners, share bars and anything repeating across
  most of the site are listed as plain text, so Obsidian's graph shows the
  relationships an editor created rather than the main menu drawn 126 times.
  Identified by landmark element, by class or id, and by frequency, so a page
  builder's `<div class="et_pb_menu">` is caught wherever it sits.
- `REPORT.md`, regenerated every run: orphans, pages cited only by a listing,
  thin content, title and meta description problems, H1 problems, click depth,
  broken internal links, internal links pointing at a redirect, linked-to pages
  with no note ranked by inbound links, pages whose description is inline script,
  and more. `noindex` pages are held out of every section and listed once at the
  end. The report cites pages as plain URLs so it never enters the graph itself.
- Reports **two isolation figures**. A page linked from a news index is not an
  orphan, but "the page that lists everything links to me" is not the same as
  "another page thought me worth citing", and the gap between the two was the
  main finding on a real site.

### Running it

- `Crawl Website.cmd` for a double-click run on Windows; `crawl.py` runs anywhere
  Python 3.9+ does.
- Live progress per page, an up-front time estimate, and stage headers for the
  phases that would otherwise be silent.
- Stops after ten consecutive failures on the assumption the site is blocking the
  crawler, writing whatever it has.
- Ctrl+C stops cleanly and keeps every page fetched so far; the next run resumes.
  `manifest.json` is checkpointed every 25 pages.
- A partial or blocked run never deletes anything and never reports pages as
  removed, because most of the site was never seen.

### Alongside

- `ANALYSE-PROMPT.md`: a prompt for handing a crawled vault to an assistant and
  getting back a saveable SEO review, structured conclusions-first.
- 58 tests, no network required.
- MIT licence.

[1.0.0]: https://github.com/aidanashby/website-crawl/releases/tag/v1.0.0
