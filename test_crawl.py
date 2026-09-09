"""Checks for the pure logic: URL normalising, path mapping, sanitising, region
tagging, archive exclusion, link-graph metrics. Run: venv/Scripts/python -m pytest

No network. The one HTML fixture below stands in for a WordPress page.
"""

from datetime import date
from pathlib import Path

import crawl

FIXTURE = """
<html lang="en-GB"><head>
  <title>Winter appeal | Example</title>
  <meta name="description" content="How the winter appeal ran.">
  <link rel="canonical" href="https://example.com/blog/winter-appeal/">
  <meta property="og:image" content="https://example.com/winter.jpg">
  <script type="application/ld+json">
  {"@graph":[{"@type":"Article","datePublished":"2025-11-03T09:00:00+00:00","dateModified":"2026-04-12"},
             {"@type":"Person","name":"Jo Bloggs"},
             {"@type":"BreadcrumbList","itemListElement":[{"name":"Home"},{"name":"Blog"},{"name":"Winter appeal"}]}]}
  </script>
</head><body class="single single-post">
  <header>
    <div class="et_pb_menu"><a href="/">Home</a><a href="/about/">About</a></div>
  </header>
  <nav><a href="/services/">Services</a></nav>
  <main>
    <h1>Winter appeal</h1>
    <p>We ran the <a href="/blog/hampers/">hamper scheme</a> again.</p>
    <h2>Planning</h2><h3>Targets</h3><h2>Outcome</h2>
    <img src="/a.jpg" alt="a sack of food">
    <img src="/b.jpg">
    <a href="https://charitycommission.gov.uk/x" rel="nofollow">the guidance</a>
    <script>var junk = "spamword spamword spamword spamword";</script>
  </main>
  <footer><a href="/privacy/">Privacy</a></footer>
</body></html>
"""


def test_normalise_url():
    # Path kept verbatim; only scheme/host lowercased and the fragment dropped.
    assert crawl.normalise_url("HTTPS://Example.com/Foo/#frag") == "https://example.com/Foo/"
    assert crawl.normalise_url("https://example.com") == "https://example.com/"
    assert crawl.normalise_url("https://example.com/a/b/?q=1") == "https://example.com/a/b/?q=1"


def test_slash_variant():
    assert crawl.slash_variant("https://example.com/a/") == "https://example.com/a"
    assert crawl.slash_variant("https://example.com/a") == "https://example.com/a/"


def test_bare_host():
    assert crawl.bare_host("www.Example.com:443") == "example.com"
    assert crawl.bare_host("blog.example.com") == "blog.example.com"


def test_same_site():
    assert crawl.same_site("https://www.example.com/x", "example.com")
    assert not crawl.same_site("https://other.com/x", "example.com")


def test_is_archive():
    assert crawl.is_archive("https://example.com/page/2/")
    assert crawl.is_archive("https://example.com/category/news/")
    assert crawl.is_archive("https://example.com/2025/11/")
    assert not crawl.is_archive("https://example.com/blog/winter-appeal/")


def test_sanitise_segment():
    assert crawl.sanitise_segment("hello world") == "hello world"
    assert crawl.sanitise_segment("a?b*c") == "a-b-c"
    assert crawl.sanitise_segment("con") == "con-"
    assert crawl.sanitise_segment("%E2%9C%93") == "✓"


def test_url_to_path():
    parents = {"blog"}
    # Section landing pages are named after their folder, not "index", so the
    # graph does not show a row of identical nodes.
    assert crawl.url_to_path("https://example.com/", parents) == Path("home.md")
    assert crawl.url_to_path("https://example.com/about/", parents) == Path("about.md")
    assert crawl.url_to_path("https://example.com/blog/", parents) == Path("blog/blog.md")
    assert crawl.url_to_path("https://example.com/blog/my.post/", parents) == Path("blog/my.post.md")


def test_url_to_path_length_cap():
    long = "https://example.com/" + "/".join("segment%d" % i for i in range(60))
    rel = crawl.url_to_path(long, set())
    assert len(str(rel)) <= crawl.PATH_LIMIT
    assert str(rel).endswith(".md")


def test_path_parents():
    paths = {"", "blog", "blog/post-a", "about", "services", "services/consulting"}
    assert crawl.path_parents(paths) == {"blog", "services"}


def test_extract_metadata_and_regions():
    data = crawl.extract(FIXTURE, "https://example.com/blog/winter-appeal/", "lean")
    m = data["meta"]
    assert m["title"] == "Winter appeal | Example"
    assert m["meta_description"] == "How the winter appeal ran."
    assert m["h1"] == ["Winter appeal"]
    assert m["author"] == "Jo Bloggs"
    assert m["published"] == "2025-11-03"
    assert m["modified"] == "2026-04-12"
    assert m["breadcrumb"] == "Home / Blog / Winter appeal"
    assert set(m["schema_types"]) == {"Article", "BreadcrumbList", "Person"}
    assert m["content_type"] == "post"
    assert m["image_count"] == 2
    assert m["images_missing_alt"] == 1
    assert m["lang"] == "en-GB"

    regions = {l["url"].rsplit("/", 2)[-2] if l["url"].endswith("/") else l["url"]: l["region"] for l in data["links"]}
    # A Divi menu is a plain <div class="et_pb_menu">, not a <nav>, and <header>
    # itself is no longer excluded: menus are identified by class instead.
    assert regions["about"] == "nav"
    assert regions["services"] == "nav"
    assert regions["hampers"] == "content"
    assert regions["privacy"] == "footer"

    # word count excludes chrome and inline scripts
    assert "Privacy" not in [h for _, h in data["outline"]]
    assert [h for _, h in data["outline"]] == ["Planning", "Targets", "Outcome"]
    full = crawl.extract(FIXTURE, "https://example.com/blog/winter-appeal/", "full")
    assert "spamword" not in full["body_md"]
    assert full["meta"]["word_count"] < 40  # script text not counted


def test_extract_full_text_mode_hashes_differently():
    lean = crawl.extract(FIXTURE, "https://example.com/x/", "lean")
    full = crawl.extract(FIXTURE, "https://example.com/x/", "full")
    assert lean["hash"] != full["hash"]
    assert full["body_md"]
    assert "hamper scheme" in full["body_md"]


def test_build_graph_orphan_and_backlinks():
    # Keys are normalised URLs, as main() stores them (trailing slash stripped).
    manifest = {
        "https://example.com/": {
            "url": "https://example.com/", "file": "index.md", "meta": {"title": "Home"},
            "links": [
                {"url": "https://example.com/about/", "region": "nav", "anchor": "About"},
                {"url": "https://example.com/blog/post/", "region": "content", "anchor": "a post"},
            ],
        },
        "https://example.com/about": {
            "url": "https://example.com/about", "file": "about.md", "meta": {"title": "About"},
            "links": [{"url": "https://example.com/", "region": "nav", "anchor": "Home"}],
        },
        "https://example.com/blog/post": {
            "url": "https://example.com/blog/post", "file": "blog/post.md", "meta": {"title": "Post"},
            "links": [],
        },
        "https://example.com/hidden": {
            "url": "https://example.com/hidden", "file": "hidden.md", "meta": {"title": "Hidden"},
            "links": [],
        },
    }
    graph, boilerplate, resolve = crawl.build_graph(manifest, "https://example.com/")
    assert graph["https://example.com/blog/post"]["links_in_content"] == ["https://example.com/"]
    assert graph["https://example.com/blog/post"]["orphan"] is False
    assert graph["https://example.com/about"]["orphan"] is True  # only nav links in
    assert graph["https://example.com/hidden"]["orphan"] is True
    assert graph["https://example.com/hidden"]["click_depth"] is None
    assert graph["https://example.com/"]["click_depth"] == 0
    assert graph["https://example.com/blog/post"]["click_depth"] == 1


def test_build_graph_boilerplate_detection():
    # A link on every page is boilerplate and must not count as a content backlink.
    manifest = {}
    for i in range(10):
        u = f"https://example.com/p{i}"
        manifest[u] = {
            "url": u, "file": f"p{i}.md", "meta": {"title": f"P{i}"},
            "links": [{"url": "https://example.com/contact/", "region": "content", "anchor": "Contact"}],
        }
    manifest["https://example.com/contact"] = {
        "url": "https://example.com/contact", "file": "contact.md", "meta": {"title": "Contact"}, "links": [],
    }
    graph, boilerplate, _ = crawl.build_graph(manifest, "https://example.com/p0")
    assert "https://example.com/contact" in boilerplate
    assert graph["https://example.com/contact"]["links_in_content"] == []


def test_wikilink():
    assert crawl.wikilink("blog/post.md", "Anchor") == "[[blog/post|Anchor]]"
    assert crawl.wikilink("index.md", "") == "[[index|index]]"
    # pipes and brackets in the label would break Obsidian's wikilink parser
    assert crawl.wikilink("about.md", "About | Site") == "[[about|About - Site]]"


def test_yaml_value_quoting():
    assert crawl.yaml_value("plain") == "plain"
    assert crawl.yaml_value("has: colon") == '"has: colon"'
    assert crawl.yaml_value(["a", "b"]) == '["a", "b"]'
    assert crawl.yaml_value(True) == "true"
    assert crawl.yaml_value(42) == "42"


def _tiny_corpus():
    """Two pages built from the fixture, with note paths and graph assigned the
    same way main() does it."""
    import urllib.parse

    home = "https://example.com/"
    post = "https://example.com/blog/winter-appeal/"
    # A third page so the content link to /blog/hampers/ resolves, and so the
    # 80% boilerplate rule has enough pages to tell a menu link from a body one.
    hampers = "https://example.com/blog/hampers/"
    hampers_html = """
    <html><head><title>Hamper scheme | Example</title></head><body>
      <header><a href="/">Home</a><a href="/about/">About</a></header>
      <nav><a href="/services/">Services</a></nav>
      <main><h1>Hamper scheme</h1><p>Some words about the hampers.</p></main>
      <footer><a href="/privacy/">Privacy</a></footer>
    </body></html>
    """
    # Filler pages so the corpus is big enough for the boilerplate ratio to
    # behave: the content link to /blog/hampers/ sits on 2 of 6 pages, well under
    # BOILERPLATE_RATIO, while the header and footer links sit on all 6.
    filler = [(f"https://example.com/page-{i}/", hampers_html.replace(
        "Hamper scheme", f"Page {i}")) for i in range(1, 4)]

    manifest = {}
    for u, html in [(home, FIXTURE.replace("/blog/winter-appeal/", "/")),
                    (post, FIXTURE), (hampers, hampers_html)] + filler:
        d = crawl.extract(html, u, "lean")
        manifest[u] = {"url": u, "hash": d["hash"], "mode": "lean", "lastmod": None,
                       "crawled": "2026-09-09", "status": 200, "meta": d["meta"],
                       "links": d["links"], "outline": d["outline"],
                       "excerpt": d["excerpt"], "body_md": d["body_md"]}
    paths = {urllib.parse.urlsplit(u).path.strip("/") for u in manifest}
    parents = crawl.path_parents(paths | {""})
    for u, e in manifest.items():
        e["file"] = str(crawl.url_to_path(u, parents)).replace("\\", "/")
    graph, boilerplate, resolve = crawl.build_graph(manifest, home)
    return manifest, graph, boilerplate, resolve


def test_report_has_no_wikilinks(tmp_path):
    # The whole point: REPORT.md must not re-enter Obsidian's graph. A wikilink
    # here would make the report a hub linked to every page it mentions.
    manifest, graph, _, _ = _tiny_corpus()
    out = tmp_path / "REPORT.md"
    crawl.write_report(out, manifest, graph, [], [], [])
    text = out.read_text("utf-8")
    assert "[[" not in text and "]]" not in text
    assert "https://example.com/blog/winter-appeal/" in text
    assert "Winter appeal | Example - https://example.com/" in text


def test_report_marks_a_partial_run(tmp_path):
    manifest, graph, _, _ = _tiny_corpus()
    out = tmp_path / "REPORT.md"
    crawl.write_report(out, manifest, graph, [], [], [], partial=True)
    assert "Partial run" in out.read_text("utf-8")


def test_render_note_still_uses_wikilinks():
    # The notes are where the graph edges belong, so they must NOT be changed.
    manifest, graph, boilerplate, resolve = _tiny_corpus()
    u = "https://example.com/blog/winter-appeal/"
    note = crawl.render_note(manifest[u], graph[u], manifest, resolve, boilerplate)
    assert "[[blog/hampers|hamper scheme]]" in note


def test_human_time():
    assert crawl.human_time(45) == "45s"
    assert crawl.human_time(600) == "10 min"
    assert crawl.human_time(7200) == "2.0 hours"


def test_only_content_links_become_wikilinks():
    # The graph must show editorial links between pages, not the main menu
    # repeated on every page. Nav, header, footer and boilerplate render as
    # plain URLs so Obsidian draws no edge for them.
    manifest, graph, boilerplate, resolve = _tiny_corpus()
    u = "https://example.com/blog/winter-appeal/"
    note = crawl.render_note(manifest[u], graph[u], manifest, resolve, boilerplate)
    nav_section = note.split("### In nav and footer")[1].split("### External")[0]
    assert "[[" not in nav_section
    assert "https://example.com/" in nav_section
    linked_from_nav = note.split("### In nav (")[1]
    assert "[[" not in linked_from_nav


def test_index_page_links_are_not_wikilinks():
    # A page that links to most of the site (an HTML sitemap, an "all posts"
    # listing) is navigation, not editorial. Without this it becomes one node
    # wired to every other note and swamps the graph.
    manifest, graph, boilerplate, resolve = _tiny_corpus()
    sitemap = "https://example.com/sitemap/"
    links = [{"url": u, "anchor": "x", "region": "content"} for u in manifest]
    manifest[sitemap] = {
        "url": sitemap, "hash": "x", "mode": "lean", "lastmod": None,
        "crawled": "2026-09-09", "status": 200, "file": "sitemap.md",
        "meta": dict(manifest["https://example.com/"]["meta"], title="Sitemap"),
        "links": links, "outline": [], "excerpt": "", "body_md": "",
    }
    graph, boilerplate, resolve = crawl.build_graph(manifest, "https://example.com/")
    assert graph[sitemap]["index_page"] is True

    note = crawl.render_note(manifest[sitemap], graph[sitemap], manifest, resolve, boilerplate)
    assert "[[" not in note

    # and its links do not count as content backlinks for the pages it points at
    assert sitemap not in graph["https://example.com/blog/hampers/"]["links_in_content"]


def test_duplicate_links_to_one_target_collapse():
    # A listing module emits image, title and "read more" links to the same post.
    # One row per target, keeping the most descriptive anchor.
    manifest, graph, boilerplate, resolve = _tiny_corpus()
    u = "https://example.com/blog/winter-appeal/"
    target = "https://example.com/blog/hampers/"
    manifest[u]["links"] += [
        {"url": target, "region": "content", "anchor": "read more"},
        {"url": target, "region": "content", "anchor": "blog-hampers-slug"},
    ]
    graph, boilerplate, resolve = crawl.build_graph(manifest, "https://example.com/")
    note = crawl.render_note(manifest[u], graph[u], manifest, resolve, boilerplate)
    assert note.count("[[blog/hampers") == 1
    assert "hamper scheme" in note      # the anchor with real words won
    assert "read more" not in note


def test_is_sitemap():
    assert crawl.is_sitemap("https://example.com/sitemap/")
    assert crawl.is_sitemap("https://example.com/html-sitemap")
    assert crawl.is_sitemap("https://example.com/about/Sitemap/")
    assert not crawl.is_sitemap("https://example.com/about/")


def test_sitemap_page_links_are_never_wikilinks():
    # The URL alone disqualifies it, at any site size and whatever it links to.
    manifest, graph, boilerplate, resolve = _tiny_corpus()
    sm = "https://example.com/sitemap/"
    manifest[sm] = {
        "url": sm, "hash": "x", "mode": "lean", "lastmod": None, "crawled": "2026-09-09",
        "status": 200, "file": "sitemap.md",
        "meta": dict(manifest["https://example.com/"]["meta"], title="Sitemap"),
        "links": [{"url": "https://example.com/blog/hampers/", "region": "content",
                   "anchor": "Hampers"}],
        "outline": [], "excerpt": "", "body_md": "",
    }
    graph, boilerplate, resolve = crawl.build_graph(manifest, "https://example.com/")
    assert graph[sm]["index_page"] is True
    note = crawl.render_note(manifest[sm], graph[sm], manifest, resolve, boilerplate)
    assert "[[" not in note
    assert sm not in graph["https://example.com/blog/hampers/"]["links_in_content"]


def test_report_excludes_noindex_and_lists_it_separately(tmp_path):
    manifest, graph, _, _ = _tiny_corpus()
    hidden = "https://example.com/blog/hampers/"
    manifest[hidden]["meta"]["meta_robots"] = "noindex, follow"
    manifest[hidden]["meta"]["meta_description"] = ""   # would trip a section
    out = tmp_path / "REPORT.md"
    crawl.write_report(out, manifest, graph, [], [], [])
    text = out.read_text("utf-8")

    before, after = text.split("## Noindex")
    assert hidden not in before      # absent from every judging section
    assert hidden in after           # listed once, at the end
    assert "noindex, follow" in after


def test_uncrawled_internal_links_are_not_called_external():
    # /about/ is internal but has no note. Calling it "External" misleads both a
    # reader and an assistant handed the vault.
    manifest, graph, boilerplate, resolve = _tiny_corpus()
    u = "https://example.com/blog/winter-appeal/"
    note = crawl.render_note(manifest[u], graph[u], manifest, resolve, boilerplate)
    uncrawled = note.split("### Internal pages, no note in this vault")[1].split("### Files")[0]
    ext = note.split("### External")[1].split("## Linked from")[0]
    assert "https://example.com/about/" in uncrawled
    assert "https://example.com/about/" not in ext
    assert "charitycommission.gov.uk" in ext


def test_small_site_keeps_its_body_links():
    # Regression: on a five-page site every page sits in the main menu, so
    # counting nav links towards the boilerplate ratio pushed every internal
    # target to 100% and suppressed genuine body links like a "Explore
    # Workspaces" button.
    manifest = {}
    pages = [f"https://example.com/p{i}" for i in range(5)]
    for i, u in enumerate(pages):
        nav = [{"url": p, "region": "nav", "anchor": f"P{j}"} for j, p in enumerate(pages)]
        body = [{"url": pages[1], "region": "content", "anchor": "Explore Workspaces"}] if i == 0 else []
        manifest[u] = {"url": u, "file": f"p{i}.md", "meta": {"title": f"P{i}"},
                       "links": nav + body}
    graph, boilerplate, resolve = crawl.build_graph(manifest, pages[0])
    assert boilerplate == set()
    assert graph[pages[1]]["links_in_content"] == [pages[0]]
    assert graph[pages[1]]["orphan"] is False


def test_nav_only_link_is_still_not_a_content_backlink_on_a_small_site():
    # Region tagging must keep working when the frequency rules are switched off.
    manifest = {
        "https://example.com/": {
            "url": "https://example.com/", "file": "index.md", "meta": {"title": "Home"},
            "links": [{"url": "https://example.com/about", "region": "nav", "anchor": "About"}],
        },
        "https://example.com/about": {
            "url": "https://example.com/about", "file": "about.md", "meta": {"title": "About"},
            "links": [],
        },
    }
    graph, _, _ = crawl.build_graph(manifest, "https://example.com/")
    assert graph["https://example.com/about"]["links_in_content"] == []
    assert graph["https://example.com/about"]["orphan"] is True


def test_archive_patterns_survive_a_query_string():
    # Divi's own pagination links carry ?et_blog. Matching anchored patterns
    # against the whole URL silently failed the moment a query was present.
    assert crawl.is_archive("https://example.com/news/page/2/?et_blog")
    assert crawl.is_archive("https://example.com/news/page/2/")
    assert crawl.is_archive("https://example.com/feed/?x=1")
    assert crawl.is_archive("https://example.com/x/?replytocom=5")
    assert not crawl.is_archive("https://example.com/news/?s=hello")


def test_pagination_parent():
    assert crawl.is_pagination("https://example.com/news/page/2/?et_blog")
    assert not crawl.is_pagination("https://example.com/news/")
    assert crawl.pagination_parent("https://example.com/news/page/2/?et_blog") == \
        "https://example.com/news/"
    assert crawl.pagination_parent("https://example.com/a/b/page/17/") == \
        "https://example.com/a/b/"


def test_clean_text_collapses_newlines():
    # A multi-line <title> would carry a newline into the YAML and break it.
    assert crawl.clean_text("  Winter\n  appeal  ") == "Winter appeal"
    assert crawl.clean_text("") is None
    assert crawl.clean_text(None) is None


def test_days_since():
    assert crawl.days_since(None) is None
    assert crawl.days_since("not-a-date") is None
    assert crawl.days_since(date.today().isoformat()) == 0


def test_h1_prefers_the_content_root():
    # A themed site puts its logo or site name in a header H1. Counting that
    # produces a spurious "multiple H1" on every page of the site.
    html = """<html><body>
      <header><h1>Site Name</h1></header>
      <main><h1>The real heading</h1><p>words</p></main>
    </body></html>"""
    assert crawl.extract(html, "https://example.com/x/", "lean")["meta"]["h1"] == \
        ["The real heading"]


def test_lazy_load_is_flagged():
    plain = crawl.extract("<html><body><main><p>hi</p></main></body></html>",
                          "https://example.com/", "lean")
    assert plain["meta"]["lazy_loaded"] is None
    ajax = crawl.extract(
        '<html><body><main><p>hi</p><a class="load-more" href="/x/">More</a>'
        '<script>var u="/wp-admin/admin-ajax.php";</script></main></body></html>',
        "https://example.com/", "lean")
    assert ajax["meta"]["lazy_loaded"] is True


def test_section_and_path_depth():
    m = crawl.extract("<html><body><main><p>x</p></main></body></html>",
                      "https://example.com/get-help/directory/acme/", "lean")["meta"]
    assert m["section"] == "get-help"
    assert m["path_depth"] == 3
    home = crawl.extract("<html><body><main><p>x</p></main></body></html>",
                         "https://example.com/", "lean")["meta"]
    assert home["section"] == "(home)"
    assert home["path_depth"] == 0


def test_section_groups_root_level_pages_together():
    # On a site whose posts live at /slug/, using the first segment would make
    # every post its own section and turn the grouping into a list of pages.
    def sec(path):
        return crawl.extract("<html><body><main><p>x</p></main></body></html>",
                             "https://example.com" + path, "lean")["meta"]["section"]
    assert sec("/") == "(home)"
    assert sec("/a-long-post-slug/") == "(root)"
    assert sec("/news/") == "(root)"
    assert sec("/housing/almshouses/") == "housing"


def test_lazy_load_ignores_sitewide_wordpress_boilerplate():
    # admin-ajax.php is in the boilerplate JS of every WordPress page, so it says
    # nothing about this page. Only markers inside the content count.
    ordinary = crawl.extract(
        '<html><body><main><p>An ordinary page.</p></main>'
        '<script>var ajaxurl="/wp-admin/admin-ajax.php";</script></body></html>',
        "https://example.com/about/", "lean")
    assert ordinary["meta"]["lazy_loaded"] is None
    listing = crawl.extract(
        '<html><body><main><p>Directory</p>'
        '<a class="loadmore" href="#">Load more</a></main></body></html>',
        "https://example.com/dir/", "lean")
    assert listing["meta"]["lazy_loaded"] is True


def test_content_root_ignores_teaser_articles():
    # A page builder wraps every teaser card in its own <article>. Taking the
    # first one undercounted a real post twentyfold.
    html = """<html><body>
      <header><nav><a href="/">Home</a></nav></header>
      <article class="teaser">A short card blurb.</article>
      <div class="entry">""" + ("real content word " * 200) + """</div>
      <footer>footer text</footer>
    </body></html>"""
    data = crawl.extract(html, "https://example.com/post/", "lean")
    assert data["meta"]["word_count"] > 500
    assert "footer text" not in str(data["meta"]["word_count"])


def test_content_root_still_prefers_a_real_main():
    html = "<html><body><header>chrome</header><main>" + ("body word " * 100) + \
           "</main></body></html>"
    root = crawl.content_root(crawl.BeautifulSoup(html, "lxml"))
    assert root.name == "main"


def test_header_content_is_kept_but_its_menu_is_not():
    # Themes put the page H1, and sometimes a hero block, inside <header>.
    # Excluding the whole element lost real content; the menu goes by class.
    html = """<html><body>
      <header>
        <div class="et_pb_menu"><a href="/about/">About us</a><a href="/x/">Shop</a></div>
        <h1>Meeting rooms for hire</h1>
        <p>Twelve accessible rooms in the heart of Fishponds.</p>
      </header>
      <div class="entry"><p>Body copy here.</p>
        <a href="/book/">Book a room</a></div>
      <footer><a href="/privacy/">Privacy</a></footer>
    </body></html>"""
    data = crawl.extract(html, "https://example.com/rooms/", "lean")
    assert data["meta"]["h1"] == ["Meeting rooms for hire"]
    assert "accessible rooms" in " ".join(data["excerpt"].split()) or \
        data["meta"]["word_count"] > 10
    # menu link text must not inflate the word count
    assert "About us" not in str(data["outline"])

    regions = {l["url"]: l["region"] for l in data["links"]}
    assert regions["https://example.com/about/"] == "nav"    # in the menu div
    assert regions["https://example.com/privacy/"] == "footer"
    assert regions["https://example.com/book/"] == "content"


def test_is_menu_matches_builder_and_theme_markup():
    from bs4 import BeautifulSoup
    def tag(markup):
        return BeautifulSoup(markup, "lxml").body.find(True)
    assert crawl.is_menu(tag('<div class="et_pb_menu foo">x</div>'))
    assert crawl.is_menu(tag('<div class="et_mobile_menu">x</div>'))
    assert crawl.is_menu(tag('<ul id="primary-menu">x</ul>'))
    assert crawl.is_menu(tag('<div class="main-navigation">x</div>'))
    assert not crawl.is_menu(tag('<div class="et_pb_text">x</div>'))
    assert not crawl.is_menu(tag('<div class="entry-content">x</div>'))


def test_menu_anywhere_on_the_page_counts_as_nav():
    # A page builder can drop a menu module into the middle of the content.
    html = """<html><body><div class="entry">
      <p>Words.</p>
      <div class="et_pb_menu"><a href="/services/">Services</a></div>
      <a href="/real/">A real link</a>
    </div></body></html>"""
    data = crawl.extract(html, "https://example.com/x/", "lean")
    regions = {l["url"]: l["region"] for l in data["links"]}
    assert regions["https://example.com/services/"] == "nav"
    assert regions["https://example.com/real/"] == "content"


def test_furniture_regions():
    from bs4 import BeautifulSoup
    def tag(markup):
        return BeautifulSoup(markup, "lxml").body.find(True)
    assert crawl.furniture_region(tag('<div class="et_pb_menu">x</div>')) == "nav"
    assert crawl.furniture_region(tag('<div class="breadcrumb">x</div>')) == "nav"
    assert crawl.furniture_region(tag('<div class="wp-pagenavi">x</div>')) == "nav"
    assert crawl.furniture_region(tag('<div class="et_pb_widget_area">x</div>')) == "sidebar"
    assert crawl.furniture_region(tag('<div id="cmplz-cookiebanner">x</div>')) == "nav"
    assert crawl.furniture_region(tag('<div class="et_social_networks">x</div>')) == "nav"
    assert crawl.furniture_region(tag('<div class="et_pb_text">x</div>')) is None


def test_cookie_banner_links_are_not_content():
    # A cookie notice put the same external link into the content of 99% of the
    # pages on one real site.
    html = """<html><body><div class="entry">
      <p>Real words.</p><a href="/real/">A real link</a>
      <div id="cmplz-cookiebanner"><a href="https://cookiedatabase.org/x/">Read more</a></div>
      <div class="et_pb_widget_area"><a href="/widget-target/">Widget link</a></div>
    </div></body></html>"""
    data = crawl.extract(html, "https://example.com/x/", "lean")
    regions = {l["url"]: l["region"] for l in data["links"]}
    assert regions["https://example.com/real/"] == "content"
    assert regions["https://cookiedatabase.org/x/"] == "nav"
    assert regions["https://example.com/widget-target/"] == "sidebar"
    assert "Read more" not in data["excerpt"]


def test_results_container_is_not_mistaken_for_pagination():
    # Divi wraps the whole blog grid in et_pb_ajax_pagination_container. That
    # holds the results, not the controls: matching it made every linked post an
    # orphan.
    from bs4 import BeautifulSoup
    def tag(markup):
        return BeautifulSoup(markup, "lxml").body.find(True)
    assert crawl.furniture_region(tag('<div class="et_pb_ajax_pagination_container">x</div>')) is None
    assert crawl.furniture_region(tag('<div class="et_pb_salvattore_content">x</div>')) is None
    # real controls are still caught
    assert crawl.furniture_region(tag('<div class="wp-pagenavi">x</div>')) == "nav"
    assert crawl.furniture_region(tag('<ul class="page-numbers">x</ul>')) == "nav"
    assert crawl.furniture_region(tag('<div class="pagination-links">x</div>')) == "nav"

    html = """<html><body><div class="et_pb_ajax_pagination_container">
      <article class="et_pb_post"><a href="/a-post/">A post</a></article>
    </div></body></html>"""
    data = crawl.extract(html, "https://example.com/news/", "lean")
    assert data["links"][0]["region"] == "content"


def test_failed_fetches_reach_the_report(tmp_path):
    # A URL still in the sitemap but failing now is seen, so it never reaches the
    # gone list. Its note stays on disk and would silently go stale.
    manifest, graph, _, _ = _tiny_corpus()
    out = tmp_path / "REPORT.md"
    crawl.write_report(out, manifest, graph, [], [], [], failed=[
        ("https://example.com/contact/", "status 404, text/html")])
    text = out.read_text("utf-8")
    assert "Failed to fetch this run (note may be stale) (1)" in text
    assert "https://example.com/contact/ - status 404, text/html" in text


def test_report_survives_pruned_urls_missing_from_the_manifest(tmp_path):
    # --prune deletes manifest entries after the graph was built, so the graph
    # still carries keys the manifest no longer has.
    manifest, graph, _, _ = _tiny_corpus()
    dropped = "https://example.com/blog/hampers/"
    del manifest[dropped]
    out = tmp_path / "REPORT.md"
    crawl.write_report(out, manifest, graph, [], [], [dropped])   # must not raise
    assert dropped in out.read_text("utf-8")       # only under "removed upstream"


def test_is_asset():
    assert crawl.is_asset("https://example.com/wp-content/uploads/a.jpg")
    assert crawl.is_asset("https://example.com/report.PDF")
    assert not crawl.is_asset("https://example.com/about/")
    assert not crawl.is_asset("https://example.com/a.b.page/")


def test_assets_split_out_of_the_uncrawled_page_list():
    # 35 of 42 uncrawled internal links on one real site were images, burying the
    # handful that were real page links.
    manifest, graph, boilerplate, resolve = _tiny_corpus()
    u = "https://example.com/blog/winter-appeal/"
    manifest[u]["links"] += [
        {"url": "https://example.com/wp-content/uploads/photo.jpg", "region": "content",
         "anchor": "a photo", "nofollow": False},
    ]
    graph, boilerplate, resolve = crawl.build_graph(manifest, "https://example.com/")
    note = crawl.render_note(manifest[u], graph[u], manifest, resolve, boilerplate)
    pages = note.split("### Internal pages, no note in this vault")[1].split("### Files")[0]
    files = note.split("### Files (images, PDFs)")[1].split("## Linked from")[0]
    assert "photo.jpg" in files and "photo.jpg" not in pages
    assert "/about/" in pages


def test_orphan_reason_distinguishes_nav_only_from_unreachable():
    manifest = {
        "https://example.com/": {
            "url": "https://example.com/", "file": "home.md", "meta": {"title": "Home"},
            "links": [{"url": "https://example.com/nav-only/", "region": "nav", "anchor": "N"}],
        },
        "https://example.com/nav-only/": {
            "url": "https://example.com/nav-only/", "file": "nav-only.md",
            "meta": {"title": "Nav only"}, "links": [],
        },
        "https://example.com/unlinked/": {
            "url": "https://example.com/unlinked/", "file": "unlinked.md",
            "meta": {"title": "Unlinked"}, "links": [],
        },
    }
    graph, _, _ = crawl.build_graph(manifest, "https://example.com/")
    assert graph["https://example.com/nav-only/"]["orphan_reason"] == "nav_only"
    assert graph["https://example.com/unlinked/"]["orphan_reason"] == "no_inbound_links"
    assert graph["https://example.com/"]["orphan_reason"] is None


def test_report_header_states_the_crawl_mode(tmp_path):
    # Without this there is no way to know whether body text was stored, so no
    # way to know whether page quality can be judged at all.
    manifest, graph, _, _ = _tiny_corpus()
    out = tmp_path / "REPORT.md"
    crawl.write_report(out, manifest, graph, [], [], [], mode="lean")
    assert "lean (excerpts only, no body text)" in out.read_text("utf-8")
    crawl.write_report(out, manifest, graph, [], [], [], mode="full")
    assert "full text stored" in out.read_text("utf-8")


def test_report_flags_script_in_meta_description(tmp_path):
    # Seen live: a donation widget's loader script landed in the description tag.
    manifest, graph, _, _ = _tiny_corpus()
    u = "https://example.com/"
    manifest[u]["meta"]["meta_description"] = (
        "var caf_BeneficiaryCampaignId=3439;document.write(unescape('%3Cscript'))")
    out = tmp_path / "REPORT.md"
    crawl.write_report(out, manifest, graph, [], [], [])
    text = out.read_text("utf-8")
    assert "Meta description contains script, not prose (1)" in text


def test_report_lists_internal_links_that_redirect(tmp_path):
    manifest, graph, _, _ = _tiny_corpus()
    out = tmp_path / "REPORT.md"
    crawl.write_report(out, manifest, graph, [], [], [], stale_links=[
        ("https://example.com/old/", "https://example.com/new/")])
    assert "Internal links pointing at a redirect (1)" in out.read_text("utf-8")
