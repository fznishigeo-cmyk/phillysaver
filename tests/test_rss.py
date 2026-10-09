"""Offline tests for dealradar.fetch.rss and dealradar.fetch.autodiscovery."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import pytest


def _ensure_fetch_base() -> None:
    """Provide a minimal dealradar.fetch.base when the sibling module is absent.

    dealradar/fetch/base.py is owned by another agent; until it lands, tests
    register a fake with the same SourceAdapter signature. Once the real module
    exists this shim is a no-op.
    """
    try:
        import dealradar.fetch.base  # noqa: F401
        return
    except Exception:
        pass
    import abc
    import types

    mod = types.ModuleType("dealradar.fetch.base")

    class SourceAdapter(abc.ABC):
        @abc.abstractmethod
        def fetch_posts(self, source, fetcher, db, cfg, now, deadline):
            ...

    mod.SourceAdapter = SourceAdapter  # type: ignore[attr-defined]
    sys.modules["dealradar.fetch.base"] = mod


_ensure_fetch_base()

from dealradar.config import AppConfig  # noqa: E402
from dealradar.db import DB  # noqa: E402
from dealradar.fetch import autodiscovery  # noqa: E402
from dealradar.fetch.rss import MAX_ENTRIES, RssAdapter  # noqa: E402
from dealradar.models import FetchResult, SourceConfig  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 9, 15, 0, 0, tzinfo=timezone.utc)


def fr(url: str, *, status: int = 200, body: bytes | None = b"",
       not_modified: bool = False, blocked: bool = False, error: str = "",
       final_url: str = "") -> FetchResult:
    return FetchResult(url=url, final_url=final_url or url, status=status,
                       body=body, error=error, not_modified=not_modified,
                       blocked_by_robots=blocked)


class FakeFetcher:
    """Same get/head signature as PoliteFetcher; serves canned FetchResults."""

    def __init__(self, routes: dict[str, FetchResult] | None = None) -> None:
        self.routes = dict(routes or {})
        self.calls: list[str] = []

    def get(self, url, *, conditional=True, headers=None, deadline=None) -> FetchResult:
        self.calls.append(url)
        res = self.routes.get(url)
        if res is None:
            return fr(url, status=404, body=None, error="not routed")
        return res

    def head(self, url, *, conditional=True, headers=None, deadline=None) -> FetchResult:
        return self.get(url, conditional=conditional, headers=headers, deadline=deadline)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def make_source(**kw) -> SourceConfig:
    defaults: dict = dict(name="blog", method="rss",
                          url="https://feeds.example/main", allowed_html=False)
    defaults.update(kw)
    return SourceConfig(**defaults)


@pytest.fixture()
def db(tmp_path):
    d = DB(tmp_path / "state.db")
    yield d
    d.close()


@pytest.fixture()
def cfg():
    return AppConfig()


# ---------------------------------------------------------------- RssAdapter


def test_wp_feed_maps_entries(db, cfg):
    src = make_source()
    fetcher = FakeFetcher({src.url: fr(src.url, body=fixture_bytes("rss_wp_feed.xml"))})
    posts = RssAdapter().fetch_posts(src, fetcher, db, cfg, NOW, None)

    assert len(posts) == 3
    p1 = posts[0]
    assert p1.source_name == "blog"
    assert p1.uid == "https://blog.example/?p=101"
    assert p1.url == "https://blog.example/p1"
    assert p1.title == "Instant Pot Duo 7-in-1 just $49.99 (Reg. $99.99)"
    assert p1.text.startswith("Instant Pot Duo 7-in-1 just $49.99")
    # content:encoded preferred over the summary, tags stripped
    assert "KITCHEN10" in p1.text
    assert "Instant Pot deal summary" not in p1.text
    assert "<p>" not in p1.text and "<b>" not in p1.text
    assert p1.published_at == datetime(2026, 10, 8, 12, 30, tzinfo=timezone.utc)
    assert p1.fetched_at == NOW
    # no enrichment fetch happened (allowed_html=False)
    assert fetcher.calls == [src.url]


def test_future_date_clamped_to_now(db, cfg):
    src = make_source()
    fetcher = FakeFetcher({src.url: fr(src.url, body=fixture_bytes("rss_wp_feed.xml"))})
    posts = RssAdapter().fetch_posts(src, fetcher, db, cfg, NOW, None)
    future = next(p for p in posts if p.uid == "https://blog.example/?p=102")
    assert future.published_at == NOW


def test_missing_guid_and_date(db, cfg):
    src = make_source()
    fetcher = FakeFetcher({src.url: fr(src.url, body=fixture_bytes("rss_wp_feed.xml"))})
    posts = RssAdapter().fetch_posts(src, fetcher, db, cfg, NOW, None)
    p3 = posts[2]
    assert p3.uid == "https://blog.example/p3"  # falls back to the link
    assert p3.published_at is None


def test_atom_feed(db, cfg):
    src = make_source(url="https://atom.example/feed")
    fetcher = FakeFetcher({src.url: fr(src.url, body=fixture_bytes("rss_atom_feed.xml"))})
    posts = RssAdapter().fetch_posts(src, fetcher, db, cfg, NOW, None)
    assert len(posts) == 2
    p1 = posts[0]
    assert p1.uid == "tag:atom.example,2026:entry-1"
    assert p1.url == "https://atom.example/earbuds"
    assert "$23.99" in p1.text and "<b>" not in p1.text
    assert p1.published_at == datetime(2026, 10, 7, 9, 15, tzinfo=timezone.utc)
    # second entry has no content element -> summary used
    assert "Board game gift set".lower() in posts[1].text.lower()


def test_naive_date_uses_source_tz(db, cfg, monkeypatch):
    entry = feedparser.FeedParserDict({
        "id": "naive-1",
        "title": "Naive date deal",
        "link": "https://blog.example/n1",
        "summary": "s " * 400,  # long: never an enrichment candidate
        "published": "2026-10-09T08:00:00",  # naive, no *_parsed companion
    })
    fake_feed = feedparser.FeedParserDict({"bozo": False, "entries": [entry]})
    monkeypatch.setattr("dealradar.fetch.rss.feedparser.parse",
                        lambda body: fake_feed)
    src = make_source(tz="America/New_York")
    fetcher = FakeFetcher({src.url: fr(src.url, body=b"irrelevant")})
    posts = RssAdapter().fetch_posts(src, fetcher, db, cfg, NOW, None)
    # 08:00 naive, interpreted in America/New_York (EDT, UTC-4) -> 12:00 UTC
    assert posts[0].published_at == datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def test_not_modified_returns_empty(db, cfg):
    src = make_source()
    fetcher = FakeFetcher({src.url: fr(src.url, status=304, body=None, not_modified=True)})
    assert RssAdapter().fetch_posts(src, fetcher, db, cfg, NOW, None) == []


def test_robots_blocked_raises(db, cfg):
    src = make_source()
    fetcher = FakeFetcher({src.url: fr(src.url, status=0, body=None, blocked=True)})
    with pytest.raises(RuntimeError, match="blocked_by_robots"):
        RssAdapter().fetch_posts(src, fetcher, db, cfg, NOW, None)


def test_http_error_raises(db, cfg):
    src = make_source()
    fetcher = FakeFetcher({src.url: fr(src.url, status=503, body=None, error="boom")})
    with pytest.raises(RuntimeError, match="http_503"):
        RssAdapter().fetch_posts(src, fetcher, db, cfg, NOW, None)


def test_not_a_feed_raises(db, cfg):
    src = make_source()
    fetcher = FakeFetcher({src.url: fr(src.url, body=fixture_bytes("rss_bozo.html"))})
    with pytest.raises(RuntimeError, match="not_a_feed"):
        RssAdapter().fetch_posts(src, fetcher, db, cfg, NOW, None)


def test_entry_cap(db, cfg):
    items = "".join(
        f"<item><guid>g{i}</guid><title>t{i}</title>"
        f"<link>https://b.example/{i}</link></item>"
        for i in range(70)
    )
    body = (
        "<?xml version='1.0'?><rss version='2.0'><channel><title>big</title>"
        f"{items}</channel></rss>"
    ).encode()
    src = make_source()
    fetcher = FakeFetcher({src.url: fr(src.url, body=body)})
    posts = RssAdapter().fetch_posts(src, fetcher, db, cfg, NOW, None)
    assert len(posts) == MAX_ENTRIES == 60


# ------------------------------------------------------ summary enrichment

ARTICLE_URL = "https://blog.example/article1"


def _summary_routes(article: FetchResult | None) -> dict[str, FetchResult]:
    url = "https://feeds.example/summary"
    routes = {url: fr(url, body=fixture_bytes("rss_summary_feed.xml"))}
    if article is not None:
        routes[ARTICLE_URL] = article
    return routes


def test_enrichment_when_allowed_html(db, cfg):
    src = make_source(url="https://feeds.example/summary", allowed_html=True)
    fetcher = FakeFetcher(_summary_routes(
        fr(ARTICLE_URL, body=fixture_bytes("rss_article.html"))))
    posts = RssAdapter().fetch_posts(src, fetcher, db, cfg, NOW, None)

    assert ARTICLE_URL in fetcher.calls
    p = posts[0]
    assert p.text.startswith(p.title + "\n")
    assert "SAVE20" in p.text                      # article body made it in
    assert "evilScriptVariable" not in p.text      # scripts stripped
    assert "<p>" not in p.text


def test_no_enrichment_without_allowed_html(db, cfg):
    src = make_source(url="https://feeds.example/summary", allowed_html=False)
    fetcher = FakeFetcher(_summary_routes(
        fr(ARTICLE_URL, body=fixture_bytes("rss_article.html"))))
    posts = RssAdapter().fetch_posts(src, fetcher, db, cfg, NOW, None)
    assert ARTICLE_URL not in fetcher.calls
    assert "Huge deal on paper towels" in posts[0].text
    assert "SAVE20" not in posts[0].text


def test_enrichment_falls_back_on_blocked_article(db, cfg):
    src = make_source(url="https://feeds.example/summary", allowed_html=True)
    fetcher = FakeFetcher(_summary_routes(fr(ARTICLE_URL, status=0, body=None, blocked=True)))
    posts = RssAdapter().fetch_posts(src, fetcher, db, cfg, NOW, None)
    assert ARTICLE_URL in fetcher.calls            # attempted, robots-gated by fetcher
    assert "Huge deal on paper towels" in posts[0].text  # silently kept the summary


def test_enrichment_skipped_when_post_known(db, cfg):
    src = make_source(url="https://feeds.example/summary", allowed_html=True)

    # First run: article blocked -> post keeps the summary text; record it.
    f1 = FakeFetcher(_summary_routes(fr(ARTICLE_URL, status=0, body=None, blocked=True)))
    first = RssAdapter().fetch_posts(src, f1, db, cfg, NOW, None)
    assert db.record_post(first[0], NOW) is True

    # Second run: article now available, but the post is already known ->
    # no article fetch is wasted and the text stays the summary.
    f2 = FakeFetcher(_summary_routes(fr(ARTICLE_URL, body=fixture_bytes("rss_article.html"))))
    second = RssAdapter().fetch_posts(src, f2, db, cfg, NOW, None)
    assert ARTICLE_URL not in f2.calls
    assert second[0].text == first[0].text


def test_truncation_marker_triggers_enrichment_even_when_long(db, cfg):
    # Body is over the short-text threshold but ends with a truncation marker.
    long_truncated = ("deal word " * 80).strip() + " Read more"
    body = (
        "<?xml version='1.0'?><rss version='2.0'><channel><title>t</title>"
        "<item><guid>tm-1</guid><title>Huge Paper Towel Deal at Target</title>"
        "<link>https://blog.example/article1</link>"
        f"<description>{long_truncated}</description></item></channel></rss>"
    ).encode()
    src = make_source(url="https://feeds.example/truncated", allowed_html=True)
    fetcher = FakeFetcher({
        src.url: fr(src.url, body=body),
        ARTICLE_URL: fr(ARTICLE_URL, body=fixture_bytes("rss_article.html")),
    })
    posts = RssAdapter().fetch_posts(src, fetcher, db, cfg, NOW, None)
    assert ARTICLE_URL in fetcher.calls
    assert "SAVE20" in posts[0].text


def test_long_body_not_enriched(db, cfg):
    long_body = ("deal word " * 80).strip() + " final price ten dollars"
    body = (
        "<?xml version='1.0'?><rss version='2.0'><channel><title>t</title>"
        "<item><guid>long-1</guid><title>Long post</title>"
        "<link>https://blog.example/long</link>"
        f"<description>{long_body}</description></item></channel></rss>"
    ).encode()
    src = make_source(url="https://feeds.example/long", allowed_html=True)
    fetcher = FakeFetcher({src.url: fr(src.url, body=body)})
    posts = RssAdapter().fetch_posts(src, fetcher, db, cfg, NOW, None)
    assert fetcher.calls == [src.url]              # body is long, no marker -> no fetch
    assert "final price ten dollars" in posts[0].text


# ------------------------------------------------------------ autodiscovery


def test_site_root():
    assert autodiscovery.site_root("https://blog.example/feed/") == "https://blog.example"
    assert autodiscovery.site_root("http://a.example/x?y=1") == "http://a.example"
    assert autodiscovery.site_root("blog.example/path") == "https://blog.example"
    assert autodiscovery.site_root("") == ""


def test_discover_via_link_tag():
    root = "https://site.example"
    feed_url = "https://site.example/custom/feed.xml"
    fetcher = FakeFetcher({
        root: fr(root, body=fixture_bytes("rss_discovery_page.html")),
        feed_url: fr(feed_url, body=fixture_bytes("rss_wp_feed.xml")),
    })
    found = autodiscovery.discover_feed(fetcher, "https://site.example/old/feed")
    assert found == feed_url
    assert fetcher.calls[0] == root                # root scanned first


def test_discover_falls_back_to_feed_path():
    root = "https://site2.example"
    empty_feed = (
        b"<?xml version='1.0'?><rss version='2.0'><channel>"
        b"<title>empty</title></channel></rss>"
    )
    page = (
        b"<html><head>"
        b"<link rel='alternate' type='application/rss+xml' href='/empty.xml'>"
        b"</head><body>home</body></html>"
    )
    fetcher = FakeFetcher({
        root: fr(root, body=page),
        # advertised feed parses but has zero entries -> skipped
        root + "/empty.xml": fr(root + "/empty.xml", body=empty_feed),
        root + "/feed/": fr(root + "/feed/", body=fixture_bytes("rss_wp_feed.xml")),
    })
    found = autodiscovery.discover_feed(fetcher, root + "/dead")
    assert found == root + "/feed/"
    assert root + "/empty.xml" in fetcher.calls


def test_discover_nothing():
    fetcher = FakeFetcher({})  # everything 404s
    assert autodiscovery.discover_feed(fetcher, "https://gone.example/feed") == ""
    assert fetcher.calls == [
        "https://gone.example",
        "https://gone.example/feed/",
        "https://gone.example/rss",
        "https://gone.example/atom.xml",
    ]
