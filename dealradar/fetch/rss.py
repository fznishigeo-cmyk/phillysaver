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
from ..models import RawPost, SourceConfig
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


#: The tags product_images() reads: anchors and images, plus the block
#: openers that separate one product of a roundup from the next.
_PI_TAG = re.compile(
    r"<(/?)(a|img|p|li|div|figure|h[1-6]|tr|section|article|table)\b([^>]*)>",
    re.I)
_PI_ATTR = re.compile(
    r"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))""")
#: A loose image pairs with an anchor at most this many characters away.
_PI_MAX_GAP = 800
#: Decoration, not a product: matched at word edges, so a silicone spatula
#: or a throw blanket keeps its photo.
_PI_JUNK_WORD = re.compile(
    r"(?<![a-z])(?:logo|icon|badge|button|spinner|placeholder|blank|loading"
    r"|share|sprite|buy[-_ ]?now|shop[-_ ]?now)s?(?![a-z])", re.I)
#: Anchors that are never a product: social share/comment links.
_PI_SOCIAL_HOSTS = ("facebook", "twitter", "pinterest", "whatsapp", "reddit",
                    "linkedin")
_PI_SOCIAL_EXACT = ("x.com", "t.me")
_PI_NOT_PRODUCT = ("mailto", "/feed/", "/comments", "#respond")
#: An anchor to an image file is a lightbox, not a product.
_PI_LIGHTBOX = (".jpg", ".jpeg", ".png", ".gif", ".webp")


def _pi_attrs(raw: str) -> dict[str, str]:
    return {m.group(1).lower(): html_lib.unescape(
                m.group(2) if m.group(2) is not None
                else m.group(3) if m.group(3) is not None
                else m.group(4)).strip()
            for m in _PI_ATTR.finditer(raw)}


def _pi_anchor_kind(href: str, article_url: str) -> str:
    """'product', 'lightbox' or 'ignore' for one <a href>."""
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
    return "product"


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


def product_images(html: str, article_url: str = "") -> dict[str, str]:
    """Each product link of a post -> the photo shown next to it.

    Live wart: a roundup (one post, several products) put its FIRST image —
    a collage or one product's photo — on every deal of the post (three
    Southern Savers Target cards showed the store name instead). Deal blogs
    are WordPress: each product has its own <img>, inside its link or in
    the same paragraph/list item/figure as its link and text.

    One sequential scan of <a>, </a> and <img> tags (positions kept), the
    HTML split into blocks at <p|li|div|figure|h1-6|tr|section|article|
    table> openers. A wrong photo is worse than none, so a photo pairs
    only when the layout says whose it is:
      0. an <img> inside <a href=X>..</a> belongs to X;
      1. a block holding one loose photo and one product link (a list
         item, a paragraph, a figure) is that product;
      2. else photos and links are read in runs, a run cut wherever a
         text-bearing block (a description, a heading) or more than
         _PI_MAX_GAP characters separates two of them. A run opening with
         a photo and closing with a link reads photo-then-link: each photo
         is the very next link's. One opening with a link and closing with
         a photo reads link-then-photo (a heading-linked listicle): each
         photo is the link just before it. Any other run reads both ways
         and pairs nothing — the reviewed swaps (two products in one <p>,
         a product's second photo after a text-only product) were all
         such runs read one way.
    Never paired loose: the post's featured image (og:image, its first
    image: a collage, a banner, or one product's photo), and decoration —
    a picture repeated across the post, inside two products' links or
    loose twice (a lazy placeholder, a 'buy now' button, a divider); it
    is no one's photo. Each image goes to one href, each href keeps its
    first image. Share, comment, mailto and the post's own links are not
    products (an image inside them is dropped); a lightbox link (href to
    an image file) is not either, but its image is kept loose.

    Only ever called for image-granted sources; the URLs are hotlinked,
    never copied or stored, and no text of the post is kept.
    """
    html = html or ""
    anchors: list[list] = []                             # href, start, end of </a>, block
    images: list[tuple[str, int, int, int, str]] = []    # url, start, end, block, owner
    openers = [0]                                        # block k starts at openers[k]
    first = ""                                           # the first image, wherever it sits
    open_kind, open_href = "", ""
    for m in _PI_TAG.finditer(html):
        closing, tag = m.group(1), m.group(2).lower()
        block = len(openers) - 1
        if tag == "a":
            if closing:
                if open_kind == "product":
                    anchors[-1][2] = m.end()
                open_kind, open_href = "", ""
                continue
            href = _pi_attrs(m.group(3)).get("href", "")
            open_kind, open_href = _pi_anchor_kind(href, article_url), href
            if open_kind == "product":
                anchors.append([href, m.start(), m.end(), block])
        elif tag == "img":
            url = "" if closing else _pi_image_url(_pi_attrs(m.group(3)))
            first = first or url
            if url and open_kind != "ignore":
                owner = open_href if open_kind == "product" else ""
                images.append((url, m.start(), m.end(), block, owner))
        elif not closing:
            openers.append(m.start())

    owners: dict[str, list[str]] = {}
    for url, _s, _e, _b, owner in images:
        owners.setdefault(url, []).append(owner)
    decor = {u for u, o in owners.items() if len(set(o) - {""}) > 1 or o.count("") > 1}
    featured = {_og_image(html), _first_image(html), first}
    out: dict[str, str] = {}
    used: set[str] = set()

    def take(href: str, url: str) -> None:
        if href not in out and url not in used:
            out[href] = url
            used.add(url)

    for url, _s, _e, _b, owner in images:                # 0. inside its own link
        if owner and url not in decor:
            take(owner, url)
    # The featured image stays in the layout (it opens the run it sits in:
    # dropping it would flip how that run reads) but is never paired.
    loose = [i for i in images if not i[4] and i[0] not in decor]
    links_in: dict[int, set[str]] = {}
    for href, _s, _e, blk in anchors:
        links_in.setdefault(blk, set()).add(href)
    photos_in = Counter(i[3] for i in loose)
    rest = []
    for url, start, end, blk, _o in loose:               # 1. its block's product
        if url not in featured and photos_in[blk] == 1 and len(links_in.get(blk, ())) == 1 \
                and min(max(a[1] - end, start - a[2])
                        for a in anchors if a[3] == blk) <= _PI_MAX_GAP:
            take(next(iter(links_in[blk])), url)
        else:
            rest.append((start, end, blk, "", url))
    runs: list[list[tuple[int, int, int, str, str]]] = []  # 2. by the layout
    for tok in sorted([(a[1], a[2], a[3], a[0], "") for a in anchors] + rest):
        prev = runs[-1][-1] if runs else None
        if prev and tok[0] - prev[1] <= _PI_MAX_GAP and (
                tok[2] == prev[2]
                or not _pi_has_text(html, openers[prev[2] + 1], openers[tok[2]])):
            runs[-1].append(tok)
        else:
            runs.append([tok])
    for run in runs:
        opens_photo, closes_photo = bool(run[0][4]), bool(run[-1][4])
        if opens_photo == closes_photo:
            continue  # reads both ways
        step = 1 if opens_photo else -1
        for j, (_s, _e, _b, _h, url) in enumerate(run):
            if url and url not in featured and 0 <= j + step < len(run) and run[j + step][3]:
                take(run[j + step][3], url)
    return out


def _pi_has_text(html: str, start: int, end: int) -> bool:
    """Visible text (tags dropped) in html[start:end]."""
    return end > start and bool(
        html_lib.unescape(re.sub(r"<[^>]*>", " ", html[start:end])).strip())


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


def _permission_host(source) -> str | None:
    """Host whose robots.txt the operator's WRITTEN permission overrides.

    Only set for sources carrying crawl_permission in sources.yaml (the
    correspondence stays on the operator's file); only ever the source's
    own host.
    """
    if not getattr(source, "crawl_permission", ""):
        return None
    return urlsplit(source.url).netloc.lower() or None


class RssAdapter(SourceAdapter):
    """Adapter for method='rss' sources (and 'html' sources via autodiscovered feeds)."""

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
        override = _permission_host(source)
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
                           if source.image_permission else ""),
                product_images=(product_images(body_html, url)
                                if source.image_permission else {}),
            )
            if source.allowed_html and url and not util.official_feed_only(url):
                body_text = util.html_to_text(body_html)
                # An image-granted source whose excerpt carried no picture
                # is worth the one article fetch for its og:image alone.
                want_image = source.image_permission and not post.image_url
                if ((_needs_enrichment(body_html, body_text) or want_image)
                        and not db.post_is_known(post.content_hash)):
                    enriched = self._enrich(post, fetcher, deadline,
                                            want_image=want_image,
                                            images=source.image_permission)
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
        article's per-product photos fill the gaps of the entry body's.
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
        photos = post.product_images
        if images:
            # The entry body's pairs win; the article only fills gaps, and
            # never with a photo the body already gave another link.
            taken = set(post.product_images.values())
            photos = {**{k: v for k, v in product_images(html, post.url).items()
                         if v not in taken},
                      **post.product_images}
        article_text = util.html_to_text(html)[:ARTICLE_TEXT_CAP]
        if not article_text:
            # Nothing readable to add — but a picture found is still kept.
            if (image and image != post.image_url) \
                    or photos != post.product_images:
                return dataclasses.replace(post, image_url=image,
                                           product_images=photos)
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
            product_images=photos,
            # Idempotency anchor stays the summary-level text: whether (or how)
            # enrichment succeeded must never change the content hash, or the
            # same unchanged entry would re-parse on the next run.
            hash_text=post.hash_text or post.text,
        )
