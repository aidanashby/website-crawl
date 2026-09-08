"""Checks for the pure logic: URL normalising, path mapping, sanitising, region
tagging, archive exclusion, link-graph metrics. Run: venv/Scripts/python -m pytest

No network. The one HTML fixture below stands in for a WordPress page.
"""

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
             {"@type":"Person","name":"Aidan Ashby"},
             {"@type":"BreadcrumbList","itemListElement":[{"name":"Home"},{"name":"Blog"},{"name":"Winter appeal"}]}]}
  </script>
</head><body class="single single-post">
  <header><a href="/">Home</a><a href="/about/">About</a></header>
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
    assert crawl.url_to_path("https://example.com/", parents) == Path("index.md")
    assert crawl.url_to_path("https://example.com/about/", parents) == Path("about.md")
    assert crawl.url_to_path("https://example.com/blog/", parents) == Path("blog/index.md")
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
    assert m["author"] == "Aidan Ashby"
    assert m["published"] == "2025-11-03"
    assert m["modified"] == "2026-04-12"
    assert m["breadcrumb"] == "Home / Blog / Winter appeal"
    assert set(m["schema_types"]) == {"Article", "BreadcrumbList", "Person"}
    assert m["content_type"] == "post"
    assert m["image_count"] == 2
    assert m["images_missing_alt"] == 1
    assert m["lang"] == "en-GB"

    regions = {l["url"].rsplit("/", 2)[-2] if l["url"].endswith("/") else l["url"]: l["region"] for l in data["links"]}
    assert regions["about"] == "header"
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
