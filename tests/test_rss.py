"""Offline tests for dealradar.fetch.rss and dealradar.fetch.autodiscovery."""

from __future__ import annotations

import html as html_lib
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
from dealradar.fetch.rss import (  # noqa: E402
    MAX_ENTRIES, RssAdapter, _pi_anchor_kind, _pi_image_url, post_photos)
from dealradar.models import FetchResult, PostPhotos, SourceConfig  # noqa: E402
from dealradar.product_images import link_key, match_product_image  # noqa: E402

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
#: The post's featured image (its first image; og:image on an article) and
#: an intro line, as live posts open: the featured image is never a photo
#: inside a product link, so layouts below start after it.
BANNER = f'<p><img src="{UP}banner.jpg"></p><p>Great deals today!</p>'
A, B, C = (f"https://www.target.com/p/{n}/-/A-{i}"
           for i, n in ((1, "markers"), (2, "lego"), (3, "barbie")))


def test_post_photos_image_inside_its_link():
    """(a) Hip2Save shape: the photo wrapped in the product link, the text
    and a second link to the same product below it — per product. The key
    is the href as a parser reads it (entities decoded). Without a banner
    the first product's photo is the post's first image, its featured one:
    never in a link's keeping."""
    x = "https://www.amazon.com/dp/B0AAAAAAAA?tag=h2s-20&amp;th=1"
    y = "https://www.amazon.com/dp/B0BBBBBBBB?tag=h2s-20"
    items = (f'<p><a href="{x}"><img src="{UP}shelf.jpg" class="wp-image-11"></a></p>'
             f'<p>SONGMICS Shelf $9.99 <a href="{x}">Buy</a></p>'
             f'<p><a href="{y}"><img src="{UP}rack.jpg" class="wp-image-12"></a></p>'
             f'<p>Drying Rack $5.99 <a href="{y}">Buy</a></p>')
    assert post_photos(BANNER + items).in_link == {
        "https://www.amazon.com/dp/B0AAAAAAAA?tag=h2s-20&th=1": UP + "shelf.jpg",
        y: UP + "rack.jpg",
    }
    assert post_photos(items).in_link == {y: UP + "rack.jpg"}


def test_post_photos_lazy_sources_and_srcset():
    """(b) Lazy-loaded images: src is a data: URI or a placeholder gif, the
    photo sits in data-src / data-lazy-src, or only in srcset (largest
    candidate wins)."""
    html = BANNER + (
        '<p><a href="https://shop.example/p/a/1"><img src="data:image/gif;base64,R0lGOD"'
        ' data-src="https://cdn.example/a.jpg" class="lazyload"></a></p>'
        '<p><a href="https://shop.example/p/b/2"><img src="https://blog.example/lazy-placeholder.gif"'
        ' data-lazy-src="https://cdn.example/b.jpg"></a></p>'
        '<p><a href="https://shop.example/p/c/3"><img srcset="https://cdn.example/c-300.jpg 300w, '
        'https://cdn.example/c-1024.jpg 1024w, https://cdn.example/c-768.jpg 768w"></a></p>')
    assert post_photos(html).in_link == {
        "https://shop.example/p/a/1": "https://cdn.example/a.jpg",
        "https://shop.example/p/b/2": "https://cdn.example/b.jpg",
        "https://shop.example/p/c/3": "https://cdn.example/c-1024.jpg",
    }


@pytest.mark.parametrize("href", [
    "https://www.target.com/c/toy-sale/-/N-5xtnr",                 # category / sale
    "https://www.walmart.com/browse/toys/4171",
    "https://www.walmart.com/cp/rollbacks/123",
    "https://www.walmart.com/search?q=lego",
    "https://www.amazon.com/b?node=12345",
    "https://www.amazon.com/s?k=lego",
    "https://www.amazon.com/deals",                                # Amazon, no ASIN
    "https://goto.target.com/c/1?u=https%3A%2F%2Fwww.target.com%2Fc%2Fsale%2F-%2FN-1",
    "https://www.bestbuy.com/site/promo/toy-sale",
    "https://www.kohls.com/",                                      # a home page
    f"{UP}lego-full.jpg",                                          # a lightbox
])
def test_post_photos_photo_inside_a_listing_link_is_no_ones(href):
    """In-link rule: the photo inside a link belongs to it only when the
    link names a product. A sale, category, search or home page is every
    product: its photo (a brand banner) names no product by name either —
    its words only compete — and the link is no product of the post. A
    lightbox's photo is still a photo of the post."""
    photos = post_photos(BANNER + f'<p><a href="{href}"><img src="{UP}lego-toys.jpg"></a></p>')
    assert photos.in_link == {}
    assert photos.links == set()
    if href.endswith(".jpg"):
        assert UP + "lego-toys.jpg" in photos.named
    else:
        assert UP + "lego-toys.jpg" not in photos.named
        assert (frozenset({"lego", "toy"}), "") in photos.names


@pytest.mark.parametrize("href", [
    "https://www.facebook.com/sharer.php?u=x", "https://twitter.com/intent/tweet?url=x",
    "https://pinterest.com/pin/create/?url=x", "https://api.whatsapp.com/send?text=x",
    "https://www.reddit.com/submit?url=x", "https://www.linkedin.com/shareArticle?url=x",
    "https://x.com/intent/tweet?url=x", "https://mobile.x.com/intent/tweet?url=x",
    "https://t.me/share/url?url=x", "https://www.instagram.com/blog/",
    "https://www.youtube.com/@blog", "https://www.tiktok.com/@blog",
    "https://other.example/feed/", "https://other.example/2026/10/a-post/comments/",
    "https://other.example/a-post/#respond", "https://blog.example/roundup/",
    "https://blog.example/roundup/?share=email", "mailto:hi@blog.example",
])
def test_post_photos_share_follow_comment_and_post_links_are_dropped(href):
    """A share, follow, comment or feed link, or the post itself: no
    product, and a picture inside it (a 'pin it' graphic, the collage
    linked to the post) is no one's photo at all."""
    photos = post_photos(BANNER + f'<p><a href="{href}"><img src="{UP}pin.jpg"></a></p>',
                         "https://blog.example/roundup/")
    assert photos.in_link == {} and UP + "pin.jpg" not in photos.named
    assert photos.links == set() and photos.images == set()


def test_post_photos_featured_or_repeated_photo_is_never_in_a_link():
    """The post's featured image (og:image, its first image, at whatever
    size) inside a product link is not that product's: it is the collage
    or the lead photo. A picture shown twice in the post is decoration (or
    one product's photo lent to another link): in no link's keeping."""
    lead = (f'<p><a href="{A}"><img src="{UP}collage.jpg"></a></p>'
            f'<p><a href="{B}"><img src="{UP}lego.jpg"></a></p>')
    assert post_photos(lead).in_link == {B: UP + "lego.jpg"}
    og = (f'<meta property="og:image" content="{UP}lego.jpg?w=1200">'
          f'<p><img src="{UP}masthead.jpg"></p>'
          f'<p><a href="{B}"><img src="{UP}lego-300x300.jpg"></a></p>')
    assert post_photos(og).in_link == {}
    twice = BANNER + (f'<p><a href="{A}"><img src="{UP}lego.jpg"></a></p>'
                      f'<p><a href="{B}"><img src="{UP}lego.jpg"></a></p>')
    assert post_photos(twice).in_link == {}
    loose_too = BANNER + (f'<p><img src="{UP}lego.jpg"></p><p>LEGO deal!</p>'
                          f'<p><a href="{B}"><img src="{UP}lego.jpg"></a></p>')
    assert post_photos(loose_too).in_link == {}


def test_post_photos_skips_junk_images():
    """Logos, icons, share buttons, pixels, avatars and SVGs are not photos
    (every junk image comes first inside the product link: each one must be
    skipped for the real photo to win). A word that merely contains a junk
    token ("silicone", "blanket") is no junk."""
    mat = "https://shop.example/p/mat/5"
    html = BANNER + (
        f'<p>Mat $5 <a href="{mat}"><img src="{UP}site-logo.png">'
        f'<img src="{UP}icons/cart.png"><img src="{UP}t.gif" width="1" height="1">'
        f'<img src="{UP}star.svg"><img src="{UP}buy.jpg" class="share-button">'
        '<img src="https://secure.gravatar.com/avatar/1.jpg">'
        f'<img src="{UP}shop-now.png"><img src="{UP}buy-now-amazon.png">'
        f'<img src="{UP}silicone-blanket.jpg" alt="Silicone mat, silicon tray, blanket">'
        '</a></p>')
    photos = post_photos(html)
    assert photos.in_link == {mat: UP + "silicone-blanket.jpg"}
    assert set(photos.named) == {UP + "banner.jpg", UP + "silicone-blanket.jpg"}


def test_post_photos_one_product_linked_three_times_one_entry():
    """(h) One product linked three times around one photo: one entry, and
    a second photo of the same product does not replace the first."""
    x = "https://shop.example/p/kettle/9"
    html = BANNER + (
        f'<p><a href="{x}">Kettle</a> $12 <a href="{x}"><img src="{UP}kettle.jpg"></a>'
        f' <a href="{x}">Buy now</a></p><p><a href="{x}"><img src="{UP}kettle-2.jpg"></a></p>')
    assert post_photos(html).in_link == {x: UP + "kettle.jpg"}


def test_post_photos_photo_in_a_block_nested_inside_its_link():
    """Product boxes: the link wraps a <div> with the photo and one with the
    text, the next product right after. A photo inside a link is that
    link's, whatever blocks sit between them."""
    boxes = "".join(f'<a href="{link}"><div class="pbox"><img src="{UP}{name}.jpg"></div>'
                    f'<div>Product $9</div></a>' for link, name in ((A, "a"), (B, "b")))
    assert post_photos(BANNER + boxes).in_link == {A: UP + "a.jpg", B: UP + "b.jpg"}


def test_post_photos_words_name_each_photo():
    """By-name rule input: each photo's words are its alt, title, its
    figure's figcaption and its file name (no extension, -WxH size, digits
    or deal words; a plural's s dropped). One photo served at two sizes is
    one entry; a related post's thumbnail (the blog's own link) is none of
    this post's photos."""
    post = "https://blog.example/roundup/"
    html = (
        f'<figure class="wp-block-image"><img src="{UP}IMG_2041.jpg" alt="Crayola Markers 40ct">'
        '</figure>'
        f'<figure><a href="{A}"><img src="{UP}LEGO-Classic-Bricks-300x300.jpg"></a>'
        '<figcaption>The <a href="x">Brick Box</a> today</figcaption></figure>'
        f'<p><img src="{UP}LEGO-Classic-Bricks.jpg?w=912" title="Storage Tub"></p>'
        '<figure class="wp-block-gallery"><figure><img src="https://cdn.example/1-2026.png">'
        '<figcaption>Play-Doh Starter Set</figcaption></figure></figure>'
        f'<p><a href="https://blog.example/old-lego-post/"><img src="{UP}lego-tower.jpg"'
        ' alt="LEGO Classic Tower"></a></p>')
    named = post_photos(html, post).named
    assert named == {
        UP + "IMG_2041.jpg": {"crayola", "marker", "40"},
        UP + "LEGO-Classic-Bricks-300x300.jpg": {"lego", "classic", "brick", "box",
                                                 "storage", "tub"},
        "https://cdn.example/1-2026.png": {"play", "doh", "starter"},
    }


def test_post_photos_counts_products_and_photos():
    """A post's own image may be its product's only while it shows at most
    one product link and one photo besides the featured one: counted as
    distinct off-site product links (one ASIN however linked is one; two
    redirector links differing in the product they carry are two) and
    distinct photos (one photo at two sizes is one; the featured image,
    related-post thumbnails and listing pages do not count)."""
    post = "https://blog.example/roundup/"
    trx = "https://clicks.trx-hub.com/xid/h2s?q=https%3A%2F%2Fwww.nike.com%2Ft%2F{}"
    html = (f'<meta property="og:image" content="{UP}hero.jpg">'
            f'<p><img src="{UP}hero-1024x768.jpg"></p>'
            '<p><a href="https://www.amazon.com/dp/B0AAAAAAAA?tag=x">Buy</a> '
            '<a href="https://www.amazon.com/Kettle/dp/B0AAAAAAAA">Buy</a> '
            f'<a href="{trx.format("shoe-1")}">a</a> <a href="{trx.format("shoe-2")}">b</a> '
            '<a href="https://www.amazon.com/s?k=kettle">more</a> '
            '<a href="https://blog.example/other-post/">related</a></p>'
            f'<p><img src="{UP}kettle.jpg"></p><p><img src="{UP}kettle-300x300.jpg"></p>'
            f'<p><a href="https://blog.example/other-post/"><img src="{UP}thumb.jpg"></a></p>')
    photos = post_photos(html, post)
    assert len(photos.links) == 3
    assert photos.images == {"2026/10/kettle.jpg"}


def test_post_photos_lazy_image_and_its_noscript_twin_are_one():
    """Review finding (medium): WP Rocket, Smush, a3 Lazy Load and Jetpack
    emit each lazy <img> again inside <noscript>; counted twice, every
    photo read as a repeat and no link kept its photo. A twin is one
    photo, read from the lazy copy."""
    html = BANNER + "".join(
        f'<p><a href="{link}"><img src="data:image/gif;base64,R0l" data-lazy-src="{UP}{n}.jpg">'
        f'<noscript><img src="{UP}{n}.jpg"></noscript></a></p>'
        for link, n in ((A, "a"), (B, "b")))
    photos = post_photos(html)
    assert photos.in_link == {A: UP + "a.jpg", B: UP + "b.jpg"}
    assert photos.images == {"2026/10/a.jpg", "2026/10/b.jpg"}


@pytest.mark.parametrize("post,href,kind", [
    # Link cloakers on the blog's own host: a product link in disguise.
    ("https://blog.example/2026/10/11/toy-deals/", "https://blog.example/recommends/markers/",
     "product"),
    ("https://blog.example/2026/10/11/toy-deals/", "https://blog.example/go/lego", "product"),
    ("https://blog.example/2026/10/11/toy-deals/", "https://www.blog.example/out/doh/", "product"),
    ("https://blog.example/2026/10/11/toy-deals/", "https://blog.example/link/barbie/", "product"),
    ("https://blog.example/2026/10/11/toy-deals/", "https://blog.example/refer/kettle", "product"),
    # Any other same-host path is the blog's own page: a header nav link
    # (hip2save.com/deals/) must never make a one-product post a roundup.
    ("https://blog.example/2026/10/11/toy-deals/", "https://blog.example/crayola-40ct", "site"),
    ("https://hip2save.com/deals/nike-sale/", "https://hip2save.com/deals/", "site"),
    ("https://hip2save.com/deals/nike-sale/", "https://hip2save.com/freebies/", "site"),
    ("https://blog.example/toy-deals/", "https://blog.example/go/lego", "product"),
    # The blog's own pages: dated posts, posts shaped like this one,
    # archives, home.
    ("https://blog.example/2026/10/11/toy-deals/", "https://blog.example/2026/10/09/older/", "site"),
    ("https://blog.example/2026/10/11/toy-deals/", "https://blog.example/category/toys/", "site"),
    ("https://blog.example/2026/10/11/toy-deals/", "https://blog.example/tag/lego/", "site"),
    ("https://blog.example/2026/10/11/toy-deals/", "https://blog.example/", "site"),
    ("https://blog.example/2026/10/11/toy-deals/", "https://blog.example/?p=12", "site"),
    ("https://hip2save.com/deals/nike-sale/", "https://hip2save.com/deals/other-post/", "site"),
    ("https://hip2save.com/deals/nike-sale/", "https://hip2save.com/deals/nike-sale/amp/", "site"),
    ("https://blog.example/toy-deals/", "https://blog.example/other-post/", "site"),
])
def test_same_host_links_cloaked_or_the_blogs_own(post, href, kind):
    """Review finding (medium): ThirstyAffiliates /recommends/ and Pretty
    Links /go/ links on the blog's own host read as the blog's pages, so a
    roundup of cloaked links counted no product and lent its collage. Any
    same-host link that is not the post itself, a dated or same-shaped
    permalink, an archive or the home page is a product link."""
    assert _pi_anchor_kind(href, post) == kind


def test_post_photos_cloaked_links_count_and_keep_their_photos():
    post = "https://www.couponingwithrachel.com/2026/10/11/toy-deals/"
    cloaked = [f"https://www.couponingwithrachel.com/recommends/{n}/" for n in "abc"]
    html = (f'<meta property="og:image" content="{UP}toy-deals-collage.jpg">'
            f'<p><img src="{UP}toy-deals-collage.jpg"></p>' + "".join(
                f"<p><a href='{link}'><img src='{UP}IMG_90{i}.jpg'></a></p>"
                f"<p><a href='{link}'>Toy {i}</a> $9.99</p>" for i, link in enumerate(cloaked)))
    photos = post_photos(html, post)
    assert len(photos.links) == 3 and len(photos.images) == 3
    assert photos.in_link == {link: f"{UP}IMG_90{i}.jpg" for i, link in enumerate(cloaked)}


def test_post_photos_photo_named_after_the_post_is_featured():
    """Review finding (high): a featured collage named after the post's
    slug or title (WordPress upload names, SEO-plugin alts) is the post's
    own picture wherever it sits, never a product's on a roundup."""
    url = "https://blog.example/2026/10/11/crayola-markers-and-more-toy-deals/"
    title = "Back to School Deals: Crayola, Elmer's and More"
    html = (f'<p><img src="{UP}masthead.jpg"></p>'
            f'<figure><img src="{UP}crayola-markers-and-more-toy-deals-1.jpg"></figure>'
            f'<p><a href="{A}"><img src="{UP}IMG_1.jpg" alt="{title}"></a></p>'
            f'<p><a href="{B}"><img src="{UP}lego-classic.jpg"></a></p>'
            f'<p><a href="{C}"><img src="{UP}IMG_2.jpg" alt="Toy collage"></a></p>')
    photos = post_photos(html, url, title=title)
    assert photos.featured == {"2026/10/masthead.jpg", "2026/10/img_1.jpg", "2026/10/img_2.jpg",
                               "2026/10/crayola-markers-and-more-toy-deals-1.jpg"}
    assert photos.in_link == {B: UP + "lego-classic.jpg"}
    assert photos.images == {"2026/10/lego-classic.jpg"}
    # An article page names itself in og:title or <title>.
    page = f'<head><meta property="og:title" content="{title}"></head>' + html
    assert post_photos(page, "https://blog.example/?p=1").in_link == {B: UP + "lego-classic.jpg"}
    page = f'<head><title>{title} | Blog</title></head>' + html
    assert post_photos(page, "https://blog.example/?p=1").in_link == {B: UP + "lego-classic.jpg"}


def test_post_photos_names_every_product_the_page_names():
    """Competing names: product-ish headings and product link texts (list
    numbers and prices dropped; "Buy now" names nothing), with the product
    link each carries."""
    cloak = "https://blog.example/recommends/doh/"
    html = (BANNER + f'<h3><a href="{A}">1. Crayola Markers 40ct $19.99</a></h3>'
            '<h2>Crayola Washable Kids Paint</h2>'
            f'<p><a href="{B}">LEGO Classic Bricks</a> $20 <a href="{B}">Buy now</a></p>'
            f'<p><a href="{cloak}">Play-Doh Starter Set</a></p>'
            '<p><a href="https://blog.example/other-post/">Our other LEGO deals</a></p>')
    assert post_photos(html, "https://blog.example/roundup/").names == {
        (frozenset({"crayola", "marker", "40"}), link_key(A)),
        (frozenset({"crayola", "washable", "kid", "paint"}), ""),
        (frozenset({"lego", "classic", "brick"}), link_key(B)),
        (frozenset({"play", "doh", "starter"}), link_key(cloak)),
    }


ROUNDUP_URL = "https://blog.example/target-roundup/"
GOTO = "https://goto.target.com/c/1/2/3?u=https%3A%2F%2Fwww.target.com%2Fp%2F"
ROUNDUP_BODY = (
    f'<p><a href="{ROUNDUP_URL}"><img src="{UP}collage.jpg"></a></p>'
    f'<p><a href="{GOTO}shelf%2F-%2FA-111"><img src="{UP}shelf.jpg" class="wp-image-11"></a></p>'
    f'<p>SONGMICS 5-Tier Shelf $39.99 (reg $59.99) <a href="{GOTO}shelf%2F-%2FA-111">Buy</a></p>'
    f'<p><img src="{UP}Laundry-Drying-Rack.jpg" class="wp-image-12"></p>'
    f'<p>Laundry Drying Rack $19.99 (reg $34.99) <a href="{GOTO}rack%2F-%2FA-222">Buy</a></p>'
)


def _roundup_feed(body_tag: str) -> bytes:
    return (
        "<?xml version='1.0'?><rss version='2.0' "
        "xmlns:content='http://purl.org/rss/1.0/modules/content/'><channel><title>t</title>"
        "<item><guid>rt-1</guid><title>Target home deals</title>"
        f"<link>{ROUNDUP_URL}</link>{body_tag}</item></channel></rss>"
    ).encode()


def test_feed_roundup_entry_carries_its_photos(db, cfg):
    """A content:encoded roundup with two products: the post carries the
    shelf's photo inside its link and the rack's loose photo by its name
    only (the collage linked to the post is no one's); image_url stays the
    first image, as before. No grant, no photos."""
    feed = _roundup_feed(f"<content:encoded><![CDATA[{ROUNDUP_BODY}]]></content:encoded>")
    granted = make_source(url="https://feeds.example/roundup", image_permission=True)
    f1 = FakeFetcher({granted.url: fr(granted.url, body=feed)})
    post = RssAdapter().fetch_posts(granted, f1, db, cfg, NOW, None)[0]
    assert post.photos.in_link == {GOTO + "shelf%2F-%2FA-111": UP + "shelf.jpg"}
    assert set(post.photos.named) == {UP + "shelf.jpg", UP + "Laundry-Drying-Rack.jpg"}
    assert len(post.photos.links) == 2
    assert post.image_url == UP + "collage.jpg"
    assert f1.calls == [granted.url]

    plain = make_source(url="https://feeds.example/roundup")
    f2 = FakeFetcher({plain.url: fr(plain.url, body=feed)})
    assert RssAdapter().fetch_posts(plain, f2, db, cfg, NOW, None)[0].photos == PostPhotos()


def test_article_photos_fill_gaps_body_photos_win(db, cfg):
    """An excerpt (no price -> the article is fetched) shows one product;
    the article shows more. The article fills the gaps; for the product the
    excerpt already holds, the excerpt's photo stays, and that photo is
    never handed to a second link. One photo at two sizes is one name."""
    shelf, rack = GOTO + "shelf%2F-%2FA-111", GOTO + "rack%2F-%2FA-222"
    lamp = GOTO + "lamp%2F-%2FA-333"
    excerpt = (f'<p><img src="{UP}lead.jpg"></p>'
               f'<p><a href="{shelf}"><img src="{UP}shelf-excerpt.jpg"></a> Shelf deal</p>')
    feed = _roundup_feed(f"<description><![CDATA[{excerpt}]]></description>")
    article = (f'<html><body><p><img src="{UP}lead.jpg"></p>'
               f'<p><a href="{shelf}"><img src="{UP}shelf.jpg"></a></p>'
               f'<p>Shelf $39.99 <a href="{shelf}">Buy</a></p>'
               f'<p><a href="{rack}"><img src="{UP}rack.jpg"></a></p>'
               f'<p>Rack $19.99 <a href="{rack}">Buy</a></p>'
               f'<p><a href="{lamp}"><img src="{UP}shelf-excerpt-300x300.jpg"></a></p>'
               '</body></html>').encode()
    src = make_source(url="https://feeds.example/roundup", allowed_html=True,
                      image_permission=True)
    fetcher = FakeFetcher({src.url: fr(src.url, body=feed),
                           ROUNDUP_URL: fr(ROUNDUP_URL, body=article)})
    post = RssAdapter().fetch_posts(src, fetcher, db, cfg, NOW, None)[0]
    assert ROUNDUP_URL in fetcher.calls
    assert post.photos.in_link == {shelf: UP + "shelf-excerpt.jpg", rack: UP + "rack.jpg"}
    assert set(post.photos.named) == {UP + "lead.jpg", UP + "shelf-excerpt.jpg",
                                      UP + "shelf.jpg", UP + "rack.jpg"}
    assert len(post.photos.links) == 3
    assert "$39.99" in post.text


IMG = "https://cdn.example/"


@pytest.mark.parametrize("attrs,want", [
    ({"src": IMG + "a.jpg", "alt": "Hip2Save logo"}, ""),            # alt says junk
    ({"src": IMG + "a.jpg", "width": "40"}, ""),                     # headshot, icon
    ({"src": IMG + "a.jpg", "height": "50"}, ""),
    ({"src": IMG + "a.jpg", "width": "51", "height": "300"}, IMG + "a.jpg"),
    ({"src": "data:image/gif;base64,R0l", "data-original": IMG + "o.jpg"}, IMG + "o.jpg"),
    ({"data-srcset": f"{IMG}s.jpg 300w, {IMG}l.jpg 1024w"}, IMG + "l.jpg"),
    ({"data-lazy-srcset": f"{IMG}s.jpg 1x, {IMG}l.jpg 2x"}, IMG + "l.jpg"),
    # Lazy copies first (a lazy image's src is a placeholder), then src,
    # then srcset.
    ({"data-lazy-src": IMG + "1.jpg", "data-src": IMG + "2.jpg",
      "data-original": IMG + "3.jpg", "src": IMG + "4.jpg"}, IMG + "1.jpg"),
    ({"data-src": IMG + "2.jpg", "data-original": IMG + "3.jpg", "src": IMG + "4.jpg"},
     IMG + "2.jpg"),
    ({"data-original": IMG + "3.jpg", "src": IMG + "4.jpg"}, IMG + "3.jpg"),
    ({"src": IMG + "4.jpg", "srcset": IMG + "5.jpg 2000w"}, IMG + "4.jpg"),
])
def test_image_source_rules(attrs, want):
    assert _pi_image_url(attrs) == want


#: Product links of today's photo-less roundup deals, exactly as posted
#: (and stored as canonical_url): the redirect was never resolved.
REAL_LINKS = (
    "https://on.ltk.com/+wbCJ7EyRtF6F4udehhgsUA",
    "https://clicks.trx-hub.com/xid/hip2save_c4db4_hip2save?q=https%3A%2F%2Fwww.dpbolvw.net"
    "%2Fclick-3278587-17049705%3Furl%3Dhttps%253A%252F%252Fwww.nike.com%252Ft%252F"
    "team-hustle-d-12%252FHF6279-600&p=https%3A%2F%2Fhip2save.com%2Fdeals%2Fnike-sale%2F"
    "&event_type=click&devicetype=desktop",
    "https://clicks.trx-hub.com/xid/hip2save_c4db4_hip2save?q=https%3A%2F%2Fwww.tkqlhce.com"
    "%2Fclick-3278587-17049705%3Furl%3Dhttps%253A%252F%252Fwww.nike.com%252Ft%252F"
    "pacific-womens-shoes%252FHM4771-105&p=https%3A%2F%2Fhip2save.com%2Fdeals%2Fnike-sale%2F"
    "&event_type=click&devicetype=desktop",
    "https://shoplowes.me/4ygfhQm",
    "https://www.creator.anchor.store/a/RshXRNgFxi",
    "https://goto.target.com/Gb319V",
    "https://go.shopmy.us/p-93257227",
    "https://www.walmart.com/ip/P10-Ultra/17569107127",
    "https://www.amazon.com/dp/B0G5QHSJBB",
)


def real_roundup_html(post_url: str) -> str:
    """A Hip2Save-shaped roundup: the featured collage linked to the post,
    then per product its photo inside its link, the name linked again."""
    out = [f'<p><a href="{post_url}"><img src="{UP}collage.jpg"></a></p>'
           '<p>Lots of deals today!</p>']
    for i, link in enumerate(REAL_LINKS):
        href = html_lib.escape(link)  # as served: & is &amp;
        out.append(f'<p><a href="{href}"><img src="{UP}p{i}.jpg" width="600"></a></p>'
                   f'<p><a href="{href}">Product {i}</a> only $9.99 (reg $19.99)</p>')
    return "".join(out)


@pytest.mark.parametrize("i", range(len(REAL_LINKS)))
def test_real_affiliate_links_find_their_own_photo(i):
    """The deal's link as posted (and stored) is the href its photo sits
    in: each one finds that photo, never a neighbour's."""
    post_url = "https://hip2save.com/deals/nike-sale/"
    photos = post_photos(real_roundup_html(post_url), post_url)
    assert match_product_image([REAL_LINKS[i]], photos.in_link) == f"{UP}p{i}.jpg"


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
