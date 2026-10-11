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
from dealradar.fetch.rss import MAX_ENTRIES, RssAdapter, product_images  # noqa: E402
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
    with pytest.raises(RuntimeError, match="empty_or_not_feed"):
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


def test_image_granted_source_fetches_article_for_og_image(db, cfg):
    """Hip2Save-class feeds carry text excerpts; the product photo lives on
    the article page. An image-granted source earns that one fetch even when
    the excerpt is complete (long + priced), and takes the page's og:image.
    Without the grant the same excerpt is never fetched."""
    long_body = ("deal word " * 80).strip() + " final price $10.00 today"
    feed = (
        "<?xml version='1.0'?><rss version='2.0'><channel><title>t</title>"
        "<item><guid>img-1</guid><title>Long post</title>"
        "<link>https://blog.example/article1</link>"
        f"<description>{long_body}</description></item></channel></rss>"
    ).encode()
    article = (b"<html><head><meta property='og:image' "
               b"content='https://blog.example/img/product.jpg'></head>"
               b"<body><img src='https://blog.example/img/other.jpg'>"
               b"</body></html>")
    granted = make_source(url="https://feeds.example/img", allowed_html=True,
                          image_permission=True)
    f1 = FakeFetcher({granted.url: fr(granted.url, body=feed),
                      ARTICLE_URL: fr(ARTICLE_URL, body=article)})
    posts = RssAdapter().fetch_posts(granted, f1, db, cfg, NOW, None)
    assert ARTICLE_URL in f1.calls
    assert posts[0].image_url == "https://blog.example/img/product.jpg"

    plain = make_source(url="https://feeds.example/img", allowed_html=True)
    f2 = FakeFetcher({plain.url: fr(plain.url, body=feed),
                      ARTICLE_URL: fr(ARTICLE_URL, body=article)})
    p2 = RssAdapter().fetch_posts(plain, f2, db, cfg, NOW, None)
    assert f2.calls == [plain.url]
    assert p2[0].image_url == ""


def test_long_body_not_enriched(db, cfg):
    # Long AND carrying a price: a genuinely complete deal summary — the
    # no-price-in-body rule must not fire here.
    long_body = ("deal word " * 80).strip() + " final price $10.00 today"
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
    assert "final price $10.00 today" in posts[0].text


# ------------------------------------------------- per-product roundup photos

UP = "https://blog.example/wp-content/uploads/2026/10/"


def test_product_images_image_inside_its_link():
    """(a) Hip2Save shape: the photo wrapped in the product link, the text
    and a second link to the same product below it — per product. The key
    is the href as a parser reads it (entities decoded)."""
    x = "https://www.amazon.com/dp/B0AAAAAAAA?tag=h2s-20&amp;th=1"
    y = "https://www.amazon.com/dp/B0BBBBBBBB?tag=h2s-20"
    html = (f'<p><a href="{x}"><img src="{UP}shelf.jpg" class="wp-image-11"></a></p>'
            f'<p>SONGMICS Shelf $9.99 <a href="{x}">Buy</a></p>'
            f'<p><a href="{y}"><img src="{UP}rack.jpg" class="wp-image-12"></a></p>'
            f'<p>Drying Rack $5.99 <a href="{y}">Buy</a></p>')
    assert product_images(html) == {
        "https://www.amazon.com/dp/B0AAAAAAAA?tag=h2s-20&th=1": UP + "shelf.jpg",
        y: UP + "rack.jpg",
    }


def test_product_images_lazy_sources_and_srcset():
    """(b) Lazy-loaded images: src is a data: URI or a placeholder gif, the
    photo sits in data-src / data-lazy-src, or only in srcset (largest
    candidate wins)."""
    html = ('<p><img src="data:image/gif;base64,R0lGOD" data-src="https://cdn.example/a.jpg"'
            ' class="lazyload"></p><p>A $1 <a href="https://shop.example/p/a/1">a</a></p>'
            '<p><img src="https://blog.example/lazy-placeholder.gif"'
            ' data-lazy-src="https://cdn.example/b.jpg"></p>'
            '<p>B $2 <a href="https://shop.example/p/b/2">b</a></p>'
            '<p><img srcset="https://cdn.example/c-300.jpg 300w, '
            'https://cdn.example/c-1024.jpg 1024w, https://cdn.example/c-768.jpg 768w"></p>'
            '<p>C $3 <a href="https://shop.example/p/c/3">c</a></p>')
    assert product_images(html) == {
        "https://shop.example/p/a/1": "https://cdn.example/a.jpg",
        "https://shop.example/p/b/2": "https://cdn.example/b.jpg",
        "https://shop.example/p/c/3": "https://cdn.example/c-1024.jpg",
    }


def test_product_images_list_items():
    """(c) One product per <li>: photo, name, link."""
    html = ('<ul><li><img src="https://cdn.example/1.jpg"> Towels $4 '
            '<a href="https://www.walmart.com/ip/towels/111">link</a></li>'
            '<li><img src="https://cdn.example/2.jpg"> Sheets $9 '
            '<a href="https://www.walmart.com/ip/sheets/222">link</a></li></ul>')
    assert product_images(html) == {
        "https://www.walmart.com/ip/towels/111": "https://cdn.example/1.jpg",
        "https://www.walmart.com/ip/sheets/222": "https://cdn.example/2.jpg",
    }


def test_product_images_figure_with_caption():
    """(d) Gutenberg image block: linked photo in a <figure>, caption below."""
    html = ('<figure class="wp-block-image"><a href="https://www.kohls.com/product/prd-5/x.jsp">'
            f'<img src="{UP}kohls.jpg"></a><figcaption>Blender $19.99</figcaption></figure>')
    assert product_images(html) == {
        "https://www.kohls.com/product/prd-5/x.jsp": UP + "kohls.jpg"}


def test_product_images_skips_junk_and_non_product_links():
    """(e) Logos, icons, share buttons, pixels and SVGs are not photos;
    share/comment/feed links and the post's own link are not products (a
    picture linked to the post itself is its collage: dropped). A word that
    merely contains a junk token ("silicone", "blanket") is no junk."""
    post = "https://blog.example/roundup/"
    mat = "https://shop.example/p/mat/5"
    html = (f'<p><a href="{post}"><img src="{UP}collage.jpg"></a></p>'
            f'<p><img src="{UP}pin-me.jpg"> '
            '<a href="https://www.facebook.com/sharer.php?u=x">Share</a> '
            '<a href="https://x.com/intent/tweet?url=x">Post</a> '
            '<a href="https://pinterest.com/pin/create/?url=x">Pin</a> '
            f'<a href="{post}?share=email">Email</a> <a href="{post}#respond">Reply</a> '
            '<a href="https://blog.example/comments/feed/">Comments</a> '
            '<a href="mailto:hi@blog.example">Mail</a></p>'
            # Every junk image comes first inside the product link: each one
            # must be skipped for the real photo to win.
            f'<p>Mat $5 <a href="{mat}"><img src="{UP}site-logo.png">'
            f'<img src="{UP}icons/cart.png"><img src="{UP}t.gif" width="1" height="1">'
            f'<img src="{UP}star.svg"><img src="{UP}buy.jpg" class="share-button">'
            '<img src="https://secure.gravatar.com/avatar/1.jpg">'
            f'<img src="{UP}silicone-blanket.jpg" alt="Silicone mat, silicon tray, blanket">'
            '</a></p>')
    assert product_images(html, post) == {mat: UP + "silicone-blanket.jpg"}


def test_product_images_adjacent_products_never_swap():
    """(f) Loose photos in their own paragraph, each above its product's
    text and link: each pairs with the link right after it, never with
    the previous product's (nor with both); a photo farther than the
    distance limit pairs with nothing."""
    html = (f'<p><img src="{UP}a.jpg"></p>'
            '<p>Shelf $9.99 <a href="https://www.target.com/p/shelf/-/A-111">Buy</a></p>'
            f'<p><img src="{UP}b.jpg"></p>'
            '<p>Rack $5 <a href="https://www.target.com/p/rack/-/A-222">Buy</a></p>'
            f'<p><img src="{UP}far.jpg"></p><p>{"words " * 200}</p>'
            '<p>Far away $1 <a href="https://www.target.com/p/far/-/A-333">Buy</a></p>')
    assert product_images(html) == {
        "https://www.target.com/p/shelf/-/A-111": UP + "a.jpg",
        "https://www.target.com/p/rack/-/A-222": UP + "b.jpg",
    }
    # One photo between two links: the link after it only.
    html = ('<p>Mug <a href="https://shop.example/p/mug/1">Buy</a></p>'
            f'<p><img src="{UP}cup.jpg"></p>'
            '<p>Cup <a href="https://shop.example/p/cup/2">Buy</a></p>')
    assert product_images(html) == {"https://shop.example/p/cup/2": UP + "cup.jpg"}


def test_product_images_lightbox_link():
    """(g) A photo linked to its full-size file (lightbox) is not a product
    link: the photo belongs to the nearest product link instead."""
    html = (f'<p><a href="{UP}shelf-full.jpg"><img src="{UP}shelf-300x300.jpg"></a></p>'
            '<p>Shelf $9.99 <a href="https://shop.example/p/shelf/1">Buy</a></p>')
    assert product_images(html) == {
        "https://shop.example/p/shelf/1": UP + "shelf-300x300.jpg"}


def test_product_images_one_image_many_links_one_entry():
    """(h) One product linked three times around one photo: one entry, and
    a second photo of the same product does not replace the first."""
    x = "https://shop.example/p/kettle/9"
    html = (f'<p><a href="{x}">Kettle</a> $12 <a href="{x}"><img src="{UP}kettle.jpg"></a>'
            f' <a href="{x}">Buy now</a></p><p><a href="{x}"><img src="{UP}kettle-2.jpg"></a></p>')
    assert product_images(html) == {x: UP + "kettle.jpg"}


ROUNDUP_URL = "https://blog.example/target-roundup/"
GOTO = "https://goto.target.com/c/1/2/3?u=https%3A%2F%2Fwww.target.com%2Fp%2F"
ROUNDUP_BODY = (
    f'<p><a href="{ROUNDUP_URL}"><img src="{UP}collage.jpg"></a></p>'
    f'<p><img src="{UP}shelf.jpg" class="wp-image-11"></p>'
    f'<p>SONGMICS 5-Tier Shelf $39.99 (reg $59.99) <a href="{GOTO}shelf%2F-%2FA-111">Buy</a></p>'
    f'<p><img src="{UP}rack.jpg" class="wp-image-12"></p>'
    f'<p>Laundry Drying Rack $19.99 (reg $34.99) <a href="{GOTO}rack%2F-%2FA-222">Buy</a></p>'
)


def _roundup_feed(body_tag: str) -> bytes:
    return (
        "<?xml version='1.0'?><rss version='2.0' "
        "xmlns:content='http://purl.org/rss/1.0/modules/content/'><channel><title>t</title>"
        "<item><guid>rt-1</guid><title>Target home deals</title>"
        f"<link>{ROUNDUP_URL}</link>{body_tag}</item></channel></rss>"
    ).encode()


def test_feed_roundup_entry_carries_per_product_photos(db, cfg):
    """A content:encoded roundup with two products: the post carries each
    product link's own photo (the collage linked to the post is not one);
    image_url stays the first image, as before. No grant, no photos."""
    feed = _roundup_feed(f"<content:encoded><![CDATA[{ROUNDUP_BODY}]]></content:encoded>")
    granted = make_source(url="https://feeds.example/roundup", image_permission=True)
    f1 = FakeFetcher({granted.url: fr(granted.url, body=feed)})
    post = RssAdapter().fetch_posts(granted, f1, db, cfg, NOW, None)[0]
    assert post.product_images == {
        GOTO + "shelf%2F-%2FA-111": UP + "shelf.jpg",
        GOTO + "rack%2F-%2FA-222": UP + "rack.jpg",
    }
    assert post.image_url == UP + "collage.jpg"
    assert f1.calls == [granted.url]

    plain = make_source(url="https://feeds.example/roundup")
    f2 = FakeFetcher({plain.url: fr(plain.url, body=feed)})
    assert RssAdapter().fetch_posts(plain, f2, db, cfg, NOW, None)[0].product_images == {}


def test_article_photos_fill_gaps_body_photos_win(db, cfg):
    """An excerpt (no price -> the article is fetched) shows one product;
    the article shows more. The article fills the gaps; for the product the
    excerpt already paired, the excerpt's photo stays, and that photo is
    never handed to a second link."""
    shelf, rack = GOTO + "shelf%2F-%2FA-111", GOTO + "rack%2F-%2FA-222"
    lamp = GOTO + "lamp%2F-%2FA-333"
    excerpt = f'<p><a href="{shelf}"><img src="{UP}shelf-excerpt.jpg"></a> Shelf deal</p>'
    feed = _roundup_feed(f"<description><![CDATA[{excerpt}]]></description>")
    article = (f'<html><body><p><a href="{shelf}"><img src="{UP}shelf.jpg"></a></p>'
               f'<p>Shelf $39.99 <a href="{shelf}">Buy</a></p>'
               f'<p><a href="{rack}"><img src="{UP}rack.jpg"></a></p>'
               f'<p>Rack $19.99 <a href="{rack}">Buy</a></p>'
               f'<p><a href="{lamp}"><img src="{UP}shelf-excerpt.jpg"></a></p>'
               '</body></html>').encode()
    src = make_source(url="https://feeds.example/roundup", allowed_html=True,
                      image_permission=True)
    fetcher = FakeFetcher({src.url: fr(src.url, body=feed),
                           ROUNDUP_URL: fr(ROUNDUP_URL, body=article)})
    post = RssAdapter().fetch_posts(src, fetcher, db, cfg, NOW, None)[0]
    assert ROUNDUP_URL in fetcher.calls
    assert post.product_images == {shelf: UP + "shelf-excerpt.jpg", rack: UP + "rack.jpg"}
    assert "$39.99" in post.text


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
