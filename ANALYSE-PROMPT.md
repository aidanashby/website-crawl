# Analysis prompt

Copy everything below the line into Claude (desktop app or Claude Code), after
replacing `<PATH>` with the path to the crawled folder, for example
`crawled/example.com`.

In Claude Code, run it from inside the crawled folder and the path is just `.`.
In the desktop app you will need Filesystem access to that folder, or you can
drag the folder in. It works in ChatGPT too: upload or attach the folder.

The prompt asks for the report back as a single saveable Markdown file (an
Artifact in Claude, a Canvas plus a download in ChatGPT, a file on disk in
Claude Code), so you end up with something you can keep and share rather than a
chat transcript.

---

I have crawled a website into the folder at `<PATH>`. I would like you to
explore it, diagnose its SEO problems, and propose a plan of action.

## What the site is for

Everything you recommend depends on what this organisation is actually trying to
achieve, so establish that before you start rather than inferring it from the
pages alone.

Use whatever context you already have: project instructions, reference documents,
brand or strategy files, and anything I have told you in this conversation. If
this is running inside a project set up for the organisation, that material is
the authority, and it outranks what you infer from the crawl.

If you genuinely have none of that, do not stop and do not ask. Work out the
likely priority order from the site itself, **state it in one line at the top of
your report**, and flag that the recommendations rest on it. Say plainly if a
different priority order would change your answer.

Read **What I would like** at the end before you start: it sets the shape of the
report, and the shape matters as much as the findings. Lead with conclusions and
put the detail underneath.

Deliver the report as a single Markdown file I can save and share, not as a wall
of chat. **How to give me the report**, below, says how to do that in whichever
tool you are.

## What the folder contains

One Markdown note per page. Folders mirror the URL path, so
`https://example.com/blog/winter-appeal/` becomes `blog/winter-appeal.md`. A
page that has children lives inside its own folder and takes that folder's name,
so `/blog/` becomes `blog/blog.md` and never collides with its children. The
homepage is `home.md`.

Two generated files sit alongside the notes:

- `REPORT.md` is an automated summary of the issues the crawler can detect on
  its own: orphans, thin content, missing or duplicate titles and meta
  descriptions, H1 problems, click depth, broken internal links, redirects,
  missing structured data, images without alt text, and external link targets by
  frequency. Treat it as a starting point, not a finding. Verify anything you
  intend to report against the notes themselves.

  Its first line states the crawl date, the page count, and **whether body text
  was stored**. In lean mode you can measure how much writing a page has, never
  how good it is, so do not judge quality of prose you cannot see.

  Every section covers indexable pages only; `noindex` pages are listed once at
  the end and are not faults to report. Say so if you think one is wrongly
  marked.
- `manifest.json` is the crawler's own record, keyed by final URL. Useful for
  bulk questions across every page at once, and faster to read than opening
  hundreds of notes.

## How to read a note

Every note starts with YAML frontmatter. The fields you will care about:

| Field | Meaning |
|---|---|
| `url` | The final URL after redirects. Everything is keyed on this. |
| `title`, `meta_description`, `h1` | As found in the HTML. `h1` is a list, because a page can wrongly have several. |
| `word_count` | Words in the main content, with header, footer, nav and sidebar stripped out. |
| `published`, `modified` | From structured data where the site provides it. |
| `author`, `categories`, `tags`, `breadcrumb` | From structured data. Often absent. |
| `canonical` | Present only when it points somewhere other than this page, which is usually worth a look. |
| `redirected_from` | Old URLs that redirect here. |
| `meta_robots` | `noindex`, `nofollow` and so on. |
| `lang`, `og_image`, `schema_types` | Language, social share image, structured data types found. |
| `image_count`, `images_missing_alt` | Accessibility and image SEO. |
| `links_out_content` | Links from the body text. This is the meaningful editorial number. |
| `links_out_nav` | Links from nav, header, footer and sidebar. |
| `links_out_external` | Links leaving the site. |
| `links_in_content` | How many *other pages* link here from body text. Counted once per source page, so a listing page linking to a post three times as image, title and "read more" counts as one. |
| `links_out_uncrawled` | Links to this site that have no note here. Present only when there are some. |
| `links_nofollow` | Outbound links marked `rel="nofollow"`. |
| `section` | Top-level URL folder, for grouping. `(root)` for a top-level page, `(home)` for the homepage. |
| `path_depth` | Number of URL segments. |
| `title_length`, `meta_description_length` | Character counts, so you do not have to count. |
| `days_since_modified` | Age of `modified`, in days. Only as reliable as that field. |
| `lazy_loaded` | Set when the page loads content with JavaScript. Its link and word counts are a floor, not a total. Do not report its link targets as orphans without checking. |
| `links_in_nav` | Nav links pointing here. |
| `click_depth` | Clicks from the homepage. Absent means unreachable by internal links. |
| `orphan` | True when no *body copy* links to it. **Not** the usual SEO sense: an orphan here can still sit in the main menu on every page and be perfectly reachable. Read `orphan_reason` before treating it as a problem. |
| `orphan_reason` | `nav_only` (reachable through the menu, just never cited in anyone's copy) or `no_inbound_links` (genuinely stranded). Only the second is an orphan in the usual sense. |
| `is_section_landing` | The front page of a section, e.g. `/housing/`. |

Below the frontmatter: the page heading, an excerpt (or the full body if the
crawl used `--full-text`), an **Outline** of the H2 and H3 headings, a **Links
out** section, and a **Linked from** section.

## How links are written, and how to read them

This part matters. Do not treat a wikilink as a broken or literal string.

**`[[blog/winter-appeal|Read the winter appeal]]`** is an Obsidian wikilink. It
is a link between two notes in this folder, written as
`[[<file path without the .md>|<the anchor text used on the site>]]`.

To turn one back into a live site URL, open the note at that path and read its
`url` field in the frontmatter. That field is the authority. Do not try to
reconstruct the URL from the file path yourself: `blog/winter-appeal.md` usually
means `https://example.com/blog/winter-appeal/`, but a redirect, a `www.`
prefix, or a name collision can break that assumption. `manifest.json` maps
every URL to its file if you want to do it in bulk.

A section landing page is named after its own folder, so `[[blog/blog|Blog]]`
points at `blog/blog.md`, which is the landing page of the `/blog/` section. The
homepage is `home.md`. The repeated name is the naming rule, not a mistake.

**Only editorial links inside page content are written as wikilinks.** This is
deliberate. Everything else is listed as plain text, so the four sections under
**Links out** mean different things:

| Section | What it holds | Format |
|---|---|---|
| In content | Editorial links in the body copy. These are the real relationships between pages. | Wikilink |
| In nav and footer | Menu, header, footer, sidebar, and any link repeated across most of the site. | `Title - URL` |
| Internal, no note in this vault | Links to this same site that were not crawled: excluded archives (category, tag, author, pagination), pages missing from the sitemap, or pages that failed to fetch. | Plain URL |
| External | Links to other domains. | Plain URL |

**Linked from** works the same way: **In content** lists wikilinks to the pages
that link here editorially, and **In nav** lists nav sources as plain text.

So a page appearing under "In nav and footer" as plain text is **not** a
crawling failure or a broken link. It means the crawler classified that link as
site furniture rather than an editorial link. The frontmatter counts
(`links_out_content` against `links_out_nav`, `links_in_content` against
`links_in_nav`) reflect the same split.

Pages with `sitemap` in the URL are always treated as navigation, so an HTML
sitemap never contributes wikilinks.

## What the data cannot tell you

Please respect these limits and say so rather than guessing:

- **`modified` is a hint, not proof.** Some themes set it to the publish date
  and never touch it again. Some SEO plugins bump every URL at once. Do not
  build an argument about content freshness on this field alone.
- **No JavaScript was rendered.** A page with `word_count: 0` is almost
  certainly a rendering artefact, not an empty page. Flag it as needing a manual
  look rather than as thin content.
- **A link appearing in the content of over half the pages is treated as
  boilerplate** and excluded from the content link counts, as are menus,
  footers, sidebars, breadcrumbs, cookie banners and share bars. This is why a
  main-menu page can show few content backlinks while still being prominent on
  the site. A high orphan count usually means the site does little editorial
  cross-linking, not that pages are unreachable.
- **Listing pages are followed through their pagination**, so a news landing
  page's links include posts beyond its first screen. A page flagged
  `lazy_loaded` is the exception: its content arrives by JavaScript and cannot be
  read, so its links are incomplete.
- **Archive URLs are excluded by default**: pagination, category, tag, author
  and date archives, and feeds. Their absence is not a finding.
- **There is no traffic, ranking, conversion, backlink or search-console data
  here.** If a conclusion needs any of those, say what you would need and why,
  rather than inferring it.

## How to give me the report

The whole report goes in **one self-contained Markdown document that I can save
and hand to a colleague**, not spread across chat messages. Use whichever of
these fits the tool you are running in:

Take the **first** of these that you can actually do. More than one may apply;
the earlier one wins.

1. **You can write files directly** (Claude Code, an IDE agent, a local tool):
   write it to disk in the folder above and tell me the path. This beats an
   artifact or a canvas even when you could do those too.
2. **Claude (app or web)**: create it as a single Markdown **Artifact**. If you
   revise it, update that same artifact rather than making a second one.
3. **ChatGPT**: put it in a **Canvas**, and also write it to a **downloadable
   `.md` file** and give me the download link. I want the file, not just the
   canvas.
4. **Anything else**: output the whole thing as one fenced Markdown code block I
   can copy in a single go.

Name it `SEO-REVIEW-<domain>-<crawl date>.md`, taking the crawl date from the
line at the top of `REPORT.md`, not today's date. For example
`SEO-REVIEW-example.com-2026-09-09.md`.

Three rules for the file itself:

- **Plain Markdown.** No HTML, no wikilinks, no `[[double brackets]]`. Cite
  pages as their note path and URL in plain text. The vault's own `REPORT.md`
  does the same, for the same reason: a wikilink would distort the very graph
  the report is describing.
- **Self-contained.** It will be read on its own, away from this conversation,
  so it needs to make sense without it. Name the site and the crawl date at the
  top.
- **Do not repeat it in chat.** In the chat itself, give me only the file's name
  or link, the Verdict, and the "Fix this week" table, since that is the part I
  will act on today. Everything else lives in the file.

## What I would like

Three things: understand the site, diagnose its problems, propose what to do.
The structure of your answer matters as much as its content, so please follow
the shape below exactly.

### How to structure the report

Write it so I can act on the first page and read the rest later. Most of the
value is in the top 300 words. Work from conclusions down to evidence, never the
other way round: no build-up, no narrating your process, no summarising what you
are about to say.

**1. Verdict.** Five sentences at most. What kind of shape is this site in, what
is the single biggest problem, and what is the one thing I should do first.

**2. The shape of the problem.** One paragraph, and only if it earns its place.
Sites often have one underlying problem showing up in a dozen ways. If that is
true here, name the cause plainly, and say which of the findings below are
symptoms of it. If the problems really are unrelated, write "no single cause"
and move on. Do not manufacture a theme that is not there.

**3. Fix this week.** At most five items, as a table: what to do, where, why it
matters, and roughly how long. "Roughly how long" means my own time editing in
WordPress, not a developer's. Only things genuinely quick, safe and clearly worth
doing; if something is quick but you are unsure it helps, put it lower down.

**4. Bigger decisions.** Work that needs planning, a judgement call, or someone
else's agreement. One line each on what the choice actually is. Aim for five,
but if this site's value sits here rather than in the quick wins, say so and use
as many as it needs. Do not pre-empt my decision, but do say what you would
pick.

**5. What is fine.** A short paragraph. Genuinely say what is working, so I know
where not to spend effort. If something the report flags is not actually a
problem, this is where to say so.

Everything above is the headline, and should fit on the file's first screen or
two. Then:

**6. The detail.** The full findings, grouped by theme rather than by report
section, with evidence cited by note path so I can check it. Order the themes by
how much they matter, not by how much you have to say about them. For each
finding, be clear whether you are confident or inferring, and **end it with what
to do**: the change, the pages affected, the rough effort, and what improvement
you expect. The plan lives here, attached to its evidence, rather than in a
separate section that repeats it.

**7. What I could not tell from this data.** What you would need in order to
answer the questions the crawl cannot: traffic, rankings, conversions, backlinks,
search-console data, or knowledge of the organisation's priorities.

### Rules for the whole report

- **Rank by consequence, not by count.** Forty pages missing a meta description
  is one finding, not forty. A single wrong page title on the main service page
  can matter more than a hundred cosmetic issues, and should sit above them.
  Give the size of each problem in the site's own terms: how many pages, and what
  share of the site.
- **No generic SEO advice.** Everything must point at something in this vault. If
  a recommendation would apply unchanged to any website, cut it. Assume I know
  the site and understand SEO basics.
- **Never pad a section to fill it.** If a section is genuinely empty, say so in
  one line and move on.
- British English, plain language, short paragraphs.

### The three tasks themselves

**Explore and understand the site.** Work out what the organisation does, who it
is talking to, and how the content is organised. Give me the structure as you
actually find it, not as the navigation claims it is. Note the main sections,
roughly how much content sits in each, and anything orphaned, duplicated or
abandoned.

**Diagnose the SEO and information-architecture problems.** Go past the
mechanical checks in `REPORT.md`, which I can already read. I am interested in
things like: pages competing for the same intent, important pages buried deep or
poorly linked, sections with no clear entry point, titles and descriptions that
do not match what the page is really about, thin pages that should be merged,
and content that appears abandoned.

**Propose a plan of action.** For each item say what to change, which pages it
affects, roughly how much work it is, and what improvement you expect. Be honest
where the payoff is uncertain. If something needs a decision from me rather than
a fix, say so and give me the options.
