"""RSS/Atom source adapter.

Fetches a feed politely (conditional requests, robots, rate limits are handled
by the injected PoliteFetcher), maps entries to transient RawPost objects, and
optionally enriches summary-only entries with the linked article's text when
the source's `allowed_html` flag permits it.

Also used for method='html' sources after first-run validation has swapped in
an autodiscovered feed URL.
"""

from __future__ import annotations

import dataclasses
import hashlib
import html as html_lib
import logging
import re
from collections import Counter
from datetime import datetime
from urllib.parse import urlsplit
from typing import Any

import feedparser

from .. import util
from ..models import PostPhotos, RawPost, SourceConfig
from ..product_images import _names_no_product, file_words, link_key, name_words, photo_key
from .base import SourceAdapter

log = logging.getLogger("dealradar.rss")

#: Hard cap on entries processed per feed per run.
MAX_ENTRIES = 60
#: Body text shorter than this is considered summary-only (enrichment candidate).
ENRICH_BELOW_CHARS = 600
#: Enriched article text is truncated to this many characters.
ARTICLE_TEXT_CAP = 20000
#: Trailing markers that mark a summary as truncated.
TRUNCATION_MARKERS = ("[…]", "...", "[&#8230;]", "Read more")

#: A deal post's body is only useful with a price in it.
_PRICE_PATTERN = re.compile(r"\$\s?\d")

_IMG_SRC = re.compile(r"<img[^>]+src=[\"'](https?://[^\"']+)[\"']", re.I)
#: Substrings that mark an <img> as a tracker/decoration, not a product shot.
_IMG_JUNK = ("feedburner", "pixel", "emoji", "gravatar", "avatar", "smilies")


_OG_IMAGE = (
    re.compile(r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"'](https?://[^\"']+)[\"']", re.I),
    re.compile(r"<meta[^>]+content=[\"'](https?://[^\"']+)[\"'][^>]+property=[\"']og:image[\"']", re.I),
)


def _og_image(html: str) -> str:
    """The page's declared share image (og:image) — on deal blogs that is the
    product photo. Empty when the page declares none."""
    for pat in _OG_IMAGE:
        m = pat.search(html or "")
        if m:
            return m.group(1)
    return ""


def _first_image(body_html: str) -> str:
    """First plausible product image in an entry body (empty if none).

    Only ever called for sources whose written grant covers displaying their
    images (SourceConfig.image_permission); the URL is hotlinked, never
    copied or stored.
    """
    for src in _IMG_SRC.findall(body_html or ""):
        low = src.lower()
        if any(t in low for t in _IMG_JUNK) or low.endswith(".svg"):
            continue
        return src
    return ""


#: The tags post_photos() reads: anchors and images.
_PI_TAG = re.compile(r"<(/?)(a|img)\b([^>]*)>", re.I)
_PI_ATTR = re.compile(
    r"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))""")
#: An innermost <figure> (a gallery's own figures hold one image each).
_PI_FIGURE = re.compile(r"<figure\b(?:(?!<figure\b).)*?</figure>", re.I | re.S)
_PI_CAPTION = re.compile(r"<figcaption\b[^>]*>(.*?)</figcaption>", re.I | re.S)
#: Decoration, not a product: matched at word edges, so a silicone spatula
#: or a throw blanket keeps its photo.
_PI_JUNK_WORD = re.compile(
    r"(?<![a-z])(?:logo|icon|badge|button|spinner|placeholder|blank|loading"
    r"|share|sprite|buy[-_ ]?now|shop[-_ ]?now)s?(?![a-z])", re.I)
#: Anchors that are never a product: social share/follow/comment links.
_PI_SOCIAL_HOSTS = ("facebook", "twitter", "pinterest", "whatsapp", "reddit",
                    "linkedin", "instagram", "youtube", "tiktok")
_PI_SOCIAL_EXACT = ("x.com", "t.me")
_PI_NOT_PRODUCT = ("/feed/", "/comments", "#respond")  # mailto: is no http link
#: An anchor to an image file is a lightbox, not a product.
_PI_LIGHTBOX = (".jpg", ".jpeg", ".png", ".gif", ".webp")
#: Link cloakers' prefixes on the blog's own host (ThirstyAffiliates
#: /recommends/, Pretty Links and Lasso /go/, ...): a product link in disguise.
_PI_CLOAK = frozenset(("recommends", "recommend", "go", "goto", "out", "link", "links",
                       "refer", "visit", "aff", "affiliate"))
#: First path segments of the blog's own archive and system pages.
_PI_ARCHIVE = frozenset(("category", "tag", "author", "page", "feed", "search", "comments",
                         "amp", "wp-content", "wp-admin", "wp-json", "wp-login.php"))
#: Lazy-load plugins repeat each <img> inside <noscript>: one photo, not two.
_PI_NOSCRIPT = re.compile(r"<noscript\b[^>]*>.*?</noscript\s*>", re.I | re.S)
_PI_HEADING = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1\s*>", re.I | re.S)
_PI_OPEN_A = re.compile(r"<a\b([^>]*)>", re.I)
#: A list number before a heading or a link text ("1. ", "#2) ").
_PI_LIST_NO = re.compile(r"^\s*#?\d{1,2}\s*[.):]\s+")
_PI_TITLE = (
    re.compile(r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"']([^\"']*)[\"']", re.I),
    re.compile(r"<meta[^>]+content=[\"']([^\"']*)[\"'][^>]+property=[\"']og:title[\"']", re.I),
    re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.I | re.S),
)
#: A photo is named after the post (a featured collage) when at least
#: this share of its words, two or more, are the post's title or slug.
_PI_POST_SHARE = 0.6


def _pi_attrs(raw: str) -> dict[str, str]:
    return {m.group(1).lower(): html_lib.unescape(
                m.group(2) if m.group(2) is not None
                else m.group(3) if m.group(3) is not None
                else m.group(4)).strip()
            for m in _PI_ATTR.finditer(raw)}


def _pi_own_page(path: str, article_path: str) -> bool:
    """A path on the blog's own host that is one of its pages, not a
    product: the home page, a page of the post itself (/amp/, /2/), an
    archive, a dated /YYYY/ permalink, or a link shaped like the post's
    own permalink (same depth; same section when there is one: Hip2Save
    /deals/<slug>/). A cloaker's prefix (/recommends/, /go/, /out/,
    /link/, /refer/) or any other same-host path is a cloaked product
    link."""
    segments = [s for s in path.split("/") if s]
    post = [s for s in article_path.split("/") if s]
    if not segments or (post and segments[:len(post)] == post):
        return True
    # Only a cloaker's prefix makes a same-host link a product link; every
    # other path is the blog's own page (review finding: one header nav link
    # such as https://hip2save.com/deals/ made a single-product post a
    # "roundup" and its card lost its photo).
    return segments[0] not in _PI_CLOAK


def _pi_anchor_kind(href: str, article_url: str) -> str:
    """What one <a href> is: 'product' (an off-site page naming one
    product, or a cloaked affiliate link on the blog's own host),
    'listing' (a category, sale, search or home page: every product, so
    none), 'site' (the blog's own pages: related posts, archives, home),
    'lightbox' (an image file) or 'ignore' (share, follow, comment and
    feed links, the post itself, non-http)."""
    if not href.startswith(("http://", "https://")):
        return "ignore"  # fragment, mailto:, javascript:, tel:, relative
    low = href.lower()
    parts = urlsplit(low)
    host = parts.hostname or ""
    if parts.path.endswith(_PI_LIGHTBOX):
        return "lightbox"
    if any(s in host for s in _PI_SOCIAL_HOSTS) \
            or any(host == h or host.endswith("." + h) for h in _PI_SOCIAL_EXACT) \
            or any(t in low for t in _PI_NOT_PRODUCT):
        return "ignore"
    if article_url:
        art = urlsplit(article_url.lower())
        if host == (art.hostname or "") \
                and parts.path.rstrip("/") == art.path.rstrip("/"):
            return "ignore"  # the post itself (?share=..., #comments)
        if host.removeprefix("www.") == (art.hostname or "").removeprefix("www."):
            return "site" if _pi_own_page(parts.path, art.path) else "product"
    return "listing" if _names_no_product(href) else "product"


def _pi_url_ok(url: str) -> bool:
    low = url.lower()
    return (low.startswith(("http://", "https://"))
            and not any(t in low for t in _IMG_JUNK)
            and not urlsplit(low).path.endswith(".svg")
            and not _PI_JUNK_WORD.search(url))


def _pi_image_url(attrs: dict[str, str]) -> str:
    """The <img>'s product photo URL: the lazy-load copies, else src, else
    the largest srcset candidate; '' for decoration. A lazy image's src is
    a data: URI or a placeholder gif (Jetpack: 1x1.trans.gif), so the lazy
    copy wins whenever there is one."""
    if _PI_JUNK_WORD.search(attrs.get("class", "") + " " + attrs.get("alt", "")):
        return ""
    for dim in ("width", "height"):
        m = re.match(r"\d+", attrs.get(dim, ""))
        if m and int(m.group(0)) <= 50:
            return ""  # tracking pixel, avatar, icon
    for name in ("data-lazy-src", "data-src", "data-original", "src"):
        if _pi_url_ok(attrs.get(name, "")):
            return attrs[name]
    best, best_size = "", -1.0
    for name in ("srcset", "data-srcset", "data-lazy-srcset"):
        for cand in attrs.get(name, "").split(","):
            bits = cand.split()
            if not bits or not _pi_url_ok(bits[0]):
                continue
            desc = bits[1] if len(bits) > 1 else "1x"
            try:
                size = float(desc[:-1])
            except ValueError:
                size = 0.0
            if size > best_size:
                best, best_size = bits[0], size
    return best


def _pi_name(fragment: str) -> frozenset[str]:
    """The product words of a heading or a link text (HTML), list number
    and prices dropped; none under two words ("Buy now", "here")."""
    text = html_lib.unescape(re.sub(r"<[^>]*>", " ", fragment))
    words = name_words(_PI_LIST_NO.sub("", text))
    return words if len(words) >= 2 else frozenset()


def _post_words(html: str, article_url: str, title: str) -> frozenset[str]:
    """The words of the post's own name: its title (as given, og:title,
    <title>) and its slug."""
    words = set(name_words(title))
    for pattern in _PI_TITLE:
        m = pattern.search(html)
        if m:
            words |= name_words(html_lib.unescape(re.sub(r"<[^>]*>", " ", m.group(1))))
    slug = [s for s in urlsplit(article_url).path.split("/") if s]
    if slug:
        words |= name_words(slug[-1], file_name=True)
    return frozenset(words)


def post_photos(html: str, article_url: str = "", title: str = "") -> PostPhotos:
    """What a post's HTML shows: the photo inside each product link, the
    words naming each photo, the products it names, and how many products
    and photos it shows.

    Live wart: a roundup (one post, several products) put its FIRST image —
    a collage or one product's photo — on every deal of the post. Pairing
    loose photos with links by layout then failed three review rounds (a
    heading, a price line or a featured image between a photo and its link
    shifted every photo to the next product), and a wrong photo is worse
    than none. So nothing here is paired by position:
      - featured: the post's own pictures — og:image, its first image, a
        collage, and any photo named after the post (most of its words, two
        or more, are the title's or the slug's: crayola-markers-and-more-
        toy-deals.jpg, an SEO plugin's alt). On a roundup they are no
        product's, neither in a link nor by name (product_images.
        photo_by_name);
      - in_link: an <img> inside <a href=X>..</a> is X's — only when X names
        a product (an off-site product page, or a cloaked affiliate link on
        the blog's own host; not a category, sale, search or home page, a
        related post, a share or follow link, a lightbox or the post
        itself), the image is not featured, and it appears once in the
        post (a repeated picture is decoration: a lazy placeholder, a
        button, a divider). A lazy image's <noscript> twin is no repeat;
      - named: every photo (loose or linked, featured too) with its words:
        alt, title, its figure's figcaption and its file name. Photos
        inside the blog's own links (related posts) are not this post's;
        one inside a sale or category link names a brand, not a product:
        its words only compete;
      - names: every product the page names — product-ish headings and
        product link texts — with the link it carries: a photo fitting one
        of them better is not a deal's;
      - links / images: the distinct product links, and the distinct
        photos besides the featured ones.
    The deal side (product_images.pick_photo) takes the photo in its own
    link, else the one named after its item.

    Only ever called for image-granted sources; the URLs are hotlinked,
    never copied or stored, and no text of the post is kept (names are
    normalized word sets, transient like the post).
    """
    html = _PI_NOSCRIPT.sub(" ", html or "")
    figures = [(m.start(), m.end(), _PI_CAPTION.search(m.group(0)))
               for m in _PI_FIGURE.finditer(html)]
    found: list[tuple[str, str, str, frozenset[str]]] = []   # url, kind, href, words
    photos = PostPhotos()
    first = ""                                               # the first image, wherever it sits
    kind, href, opened = "", "", 0
    for m in _PI_TAG.finditer(html):
        closing, tag = m.group(1), m.group(2).lower()
        if tag == "a":
            if closing and kind == "product":
                words = _pi_name(html[opened:m.start()])
                if words:
                    photos.names.add((words, link_key(href)))
            kind, href = "", ""
            if not closing:
                href = _pi_attrs(m.group(3)).get("href", "")
                kind = _pi_anchor_kind(href, article_url)
                opened = m.end()
                if kind == "product":
                    photos.links.add(link_key(href))
            continue
        attrs = {} if closing else _pi_attrs(m.group(3))
        url = _pi_image_url(attrs) if attrs else ""
        first = first or url
        if not url or kind == "ignore":
            continue
        caption = next((c.group(1) for s, e, c in figures if c and s <= m.start() < e), "")
        text = " ".join((attrs.get("alt", ""), attrs.get("title", ""),
                         html_lib.unescape(re.sub(r"<[^>]*>", " ", caption))))
        found.append((url, kind, href, name_words(text) | file_words(url)))

    for heading in _PI_HEADING.finditer(html):
        words = _pi_name(heading.group(2))
        if words:
            hrefs = (_pi_attrs(a.group(1)).get("href", "")
                     for a in _PI_OPEN_A.finditer(heading.group(2)))
            photos.names.add((words, next((link_key(h) for h in hrefs
                                           if _pi_anchor_kind(h, article_url) == "product"), "")))

    post = _post_words(html, article_url, title)
    featured = {photo_key(u) for u in (_og_image(html), _first_image(html), first) if u}
    featured |= {photo_key(u) for u, _kind, _href, words in found if "collage" in words
                 or (len(words) >= 2 and len(words & post) >= _PI_POST_SHARE * len(words))}
    seen = Counter(photo_key(u) for u, *_rest in found)
    named_as: dict[str, str] = {}                            # photo key -> its first URL
    for url, kind, href, words in found:
        key = photo_key(url)
        if key not in featured and seen[key] == 1 and kind == "product":
            photos.in_link.setdefault(href, url)
        if kind == "site":
            continue  # a related post's thumbnail is not this post's photo
        if key not in featured:
            photos.images.add(key)
        if kind == "listing":
            if len(words) >= 2:
                photos.names.add((words, ""))  # a sale banner: a brand, not a product
            continue
        url = named_as.setdefault(key, url)
        photos.named[url] = photos.named.get(url, frozenset()) | words
    photos.featured = featured
    return photos


def _merge_photos(body: PostPhotos, page: PostPhotos) -> PostPhotos:
    """The entry body's photos plus its article page's. The body's links
    win; the page fills gaps, never with a photo the body gave another
    link, nor with a featured photo of either. One photo is one entry
    whatever size each served it at."""
    featured = body.featured | page.featured
    taken = {photo_key(v) for v in body.in_link.values()}
    in_link = {**{k: v for k, v in page.in_link.items() if photo_key(v) not in taken},
               **body.in_link}
    in_link = {k: v for k, v in in_link.items() if photo_key(v) not in featured}
    named = dict(body.named)
    urls = {photo_key(u): u for u in named}
    for url, words in page.named.items():
        url = urls.setdefault(photo_key(url), url)
        named[url] = named.get(url, frozenset()) | words
    return PostPhotos(in_link=in_link, named=named, links=body.links | page.links,
                      images=(body.images | page.images) - featured, featured=featured,
                      names=body.names | page.names)


def _entry_body(entry: Any) -> str:
    """Prefer full content (content:encoded / atom content) over the summary."""
    content = entry.get("content")
    if content:
        try:
            value = content[0].get("value", "") or ""
        except (IndexError, AttributeError, TypeError):
            value = ""
        if value:
            return value
    return entry.get("summary", "") or ""


def _needs_enrichment(body_html: str, body_text: str) -> bool:
    """Summary-only heuristic: short body, a trailing truncation marker, or —
    the one that matters on deal blogs — NO PRICE in the body. Hip2Save-class
    excerpts run long but cut off before the prices; without a single '$'
    the extractor can prove nothing, so the full article is worth one
    polite, robots-checked fetch (live case: a 63%-off lounge set whose
    teaser carried no price and therefore never became a deal)."""
    if len(body_text) < ENRICH_BELOW_CHARS:
        return True
    if not _PRICE_PATTERN.search(body_text):
        return True
    tail_text = body_text.rstrip()
    tail_html = body_html.rstrip()
    return any(
        tail_text.endswith(marker) or tail_html.endswith(marker)
        for marker in TRUNCATION_MARKERS
    )


def _permission_host(source, cfg=None) -> str | None:
    """Host whose robots.txt the operator's WRITTEN permission overrides.

    Only set for sources carrying crawl_permission in sources.yaml (the
    correspondence stays on the operator's file); only ever the source's
    own host.
    """
    from ..source_policy import grant_active
    if not getattr(source, "crawl_permission", "") or not grant_active(source, cfg):
        return None
    return urlsplit(source.site).netloc.lower() or None


class RssAdapter(SourceAdapter):
    """Adapter for method='rss' sources (and 'html' sources via autodiscovered feeds)."""

    follow_redirects = True

    def fetch_posts(
        self,
        source: SourceConfig,
        fetcher: Any,
        db: Any,
        cfg: Any,
        now: datetime,
        deadline: Any = None,
    ) -> list[RawPost]:
        kw = {}
        if not self.follow_redirects:
            kw["follow_redirects"] = False
        from ..source_policy import image_granted
        override = _permission_host(source, cfg)
        images = image_granted(source, cfg)
        if override:  # only sources with documented written permission
            kw["robots_override_host"] = override
        res = fetcher.get(source.url, conditional=True, deadline=deadline, **kw)
        if res.not_modified or res.status == 304:
            log.debug("%s: feed not modified", source.name)
            return []
        if res.blocked_by_robots:
            raise RuntimeError("blocked_by_robots")
        if res.status != 200 or res.body is None:
            reason = f"http_{res.status}"
            if res.error:
                reason = f"{reason} {res.error}"
            raise RuntimeError(reason[:200])

        feed = feedparser.parse(res.body)
        if not feed.entries:
            # Covers bozo feeds AND HTTP-200 HTML error pages that feedparser
            # "parses" to zero entries — a success here would mask a dead feed
            # from the circuit breaker forever.
            raise RuntimeError("empty_or_not_feed")

        tz = source.tz or getattr(cfg, "timezone", "") or "UTC"
        posts: list[RawPost] = []
        for entry in feed.entries[:MAX_ENTRIES]:
            title = entry.get("title", "") or ""
            url = entry.get("link", "") or ""
            uid = (
                entry.get("id")
                or url
                or hashlib.sha256(title.encode("utf-8")).hexdigest()
            )
            body_html = _entry_body(entry)
            text = util.html_to_text(title + "\n" + body_html)
            published_at = util.parse_feed_date(
                entry.get("published_parsed")
                or entry.get("updated_parsed")
                or entry.get("published")
                or entry.get("updated"),
                tz,
                now,
            )
            post = RawPost(
                source_name=source.name,
                uid=str(uid),
                url=url,
                title=title,
                text=text,
                published_at=published_at,
                fetched_at=now,
                image_url=(_first_image(body_html)
                           if images else ""),
                photos=(post_photos(body_html, url, title)
                        if images else PostPhotos()),
            )
            if source.allowed_html and url and not util.official_feed_only(url):
                body_text = util.html_to_text(body_html)
                # An image-granted source whose excerpt carried no picture
                # is worth the one article fetch for its og:image alone.
                want_image = images and not post.image_url
                if ((_needs_enrichment(body_html, body_text) or want_image)
                        and not db.post_is_known(post.content_hash)):
                    enriched = self._enrich(post, fetcher, deadline,
                                            want_image=want_image,
                                            images=images)
                    if enriched is not None:
                        post = enriched
            posts.append(post)
        log.info("%s: %d entries", source.name, len(posts))
        return posts

    @staticmethod
    def _enrich(post: RawPost, fetcher: Any, deadline: Any,
                want_image: bool = False,
                images: bool = False) -> RawPost | None:
        """Fetch the article behind a summary-only entry; None means keep the summary.

        The fetcher enforces robots.txt and rate limits; any blocked or failed
        fetch silently falls back to the summary text. With ``want_image``
        (image-granted source, no picture yet) the article's og:image is
        taken as the post image. With ``images`` (image-granted source) the
        article's photos fill the gaps of the entry body's.
        """
        res = fetcher.get(post.url, conditional=False, deadline=deadline)
        if res.blocked_by_robots or res.status != 200 or not res.body:
            log.debug("enrichment fallback for %s (status=%s blocked=%s)",
                      post.url, res.status, res.blocked_by_robots)
            return None
        html = res.body.decode("utf-8", errors="replace")
        image = post.image_url
        if want_image and not image:
            image = _og_image(html) or _first_image(html)
        photos = post.photos
        if images:
            photos = _merge_photos(post.photos, post_photos(html, post.url, post.title))
        article_text = util.html_to_text(html)[:ARTICLE_TEXT_CAP]
        if not article_text:
            # Nothing readable to add — but a picture found is still kept.
            if (image and image != post.image_url) or photos != post.photos:
                return dataclasses.replace(post, image_url=image, photos=photos)
            return None
        return RawPost(
            source_name=post.source_name,
            uid=post.uid,
            url=post.url,
            title=post.title,
            text=post.title + "\n" + article_text,
            published_at=post.published_at,
            fetched_at=post.fetched_at,
            image_url=image,  # enrichment must not drop (or may add) the image
            photos=photos,
            # Idempotency anchor stays the summary-level text: whether (or how)
            # enrichment succeeded must never change the content hash, or the
            # same unchanged entry would re-parse on the next run.
            hash_text=post.hash_text or post.text,
        )
