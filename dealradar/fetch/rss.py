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
import logging
import re
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
            )
            if source.allowed_html and url and not util.official_feed_only(url):
                body_text = util.html_to_text(body_html)
                # An image-granted source whose excerpt carried no picture
                # is worth the one article fetch for its og:image alone.
                want_image = source.image_permission and not post.image_url
                if ((_needs_enrichment(body_html, body_text) or want_image)
                        and not db.post_is_known(post.content_hash)):
                    enriched = self._enrich(post, fetcher, deadline,
                                            want_image=want_image)
                    if enriched is not None:
                        post = enriched
            posts.append(post)
        log.info("%s: %d entries", source.name, len(posts))
        return posts

    @staticmethod
    def _enrich(post: RawPost, fetcher: Any, deadline: Any,
                want_image: bool = False) -> RawPost | None:
        """Fetch the article behind a summary-only entry; None means keep the summary.

        The fetcher enforces robots.txt and rate limits; any blocked or failed
        fetch silently falls back to the summary text. With ``want_image``
        (image-granted source, no picture yet) the article's og:image is
        taken as the post image.
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
        article_text = util.html_to_text(html)[:ARTICLE_TEXT_CAP]
        if not article_text:
            # Nothing readable to add — but a picture found is still kept.
            if image and image != post.image_url:
                return dataclasses.replace(post, image_url=image)
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
            # Idempotency anchor stays the summary-level text: whether (or how)
            # enrichment succeeded must never change the content hash, or the
            # same unchanged entry would re-parse on the next run.
            hash_text=post.hash_text or post.text,
        )
