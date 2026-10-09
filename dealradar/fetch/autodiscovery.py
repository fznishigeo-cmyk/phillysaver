"""Feed autodiscovery for sources whose configured feed URL has gone dead.

Scans the site root's HTML for <link rel="alternate" type="application/rss+xml
| application/atom+xml"> tags, then falls back to common feed paths. Every
request goes through the injected PoliteFetcher, which enforces robots.txt and
per-host rate limits.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

import feedparser

from ..util import official_feed_only

log = logging.getLogger("dealradar.autodiscovery")

FEED_TYPES = {"application/rss+xml", "application/atom+xml"}
FALLBACK_PATHS = ("/feed/", "/rss", "/atom.xml")

_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(
    r"""([a-zA-Z][a-zA-Z0-9:_-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))"""
)


def site_root(url: str) -> str:
    """scheme+host of `url` ('' when no host can be found)."""
    u = (url or "").strip()
    if not u:
        return ""
    parts = urlsplit(u if "://" in u else "https://" + u)
    if not parts.netloc:
        return ""
    scheme = parts.scheme or "https"
    return f"{scheme}://{parts.netloc}"


def _alternate_hrefs(html: str, base: str) -> list[str]:
    """Absolute hrefs of <link rel=alternate> feed tags, in document order."""
    found: list[str] = []
    for tag in _LINK_TAG_RE.findall(html):
        attrs = {
            m.group(1).lower(): (m.group(2) or m.group(3) or m.group(4) or "")
            for m in _ATTR_RE.finditer(tag)
        }
        rel = attrs.get("rel", "").lower()
        typ = attrs.get("type", "").strip().lower()
        href = attrs.get("href", "").strip()
        if "alternate" in rel.split() and typ in FEED_TYPES and href:
            found.append(urljoin(base, href))
    return found


def discover_feed(fetcher: Any, site_url: str, deadline: Any = None) -> str:
    """Return the first working feed URL for `site_url`, or '' when none is found.

    A candidate "works" when its body parses via feedparser with >= 1 entry.
    Hosts pinned to their official feeds (util.OFFICIAL_FEED_ONLY_HOSTS, e.g.
    slickdeals.net) are never probed: their HTML must not be fetched.
    """
    root = site_root(site_url)
    if not root:
        return ""
    if official_feed_only(root):
        log.info("autodiscovery forbidden for official-feed-only host %s", root)
        return ""

    candidates: list[str] = []
    res = fetcher.get(root, conditional=False, deadline=deadline)
    if res.status == 200 and res.body and not res.blocked_by_robots:
        base = res.final_url or root
        html = res.body.decode("utf-8", errors="replace")
        candidates.extend(_alternate_hrefs(html, base))
    candidates.extend(root.rstrip("/") + p for p in FALLBACK_PATHS)

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        r = fetcher.get(candidate, conditional=False, deadline=deadline)
        if r.blocked_by_robots or r.status != 200 or not r.body:
            continue
        feed = feedparser.parse(r.body)
        if len(feed.entries) >= 1:
            log.info("discovered feed for %s: %s", site_url, candidate)
            return candidate
    log.info("no feed discovered for %s", site_url)
    return ""
