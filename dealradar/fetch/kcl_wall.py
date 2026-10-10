"""KCL live deal-wall adapter (permission-based).

The Krazy Coupon Lady's deal wall (thekrazycouponlady.com/coupons-for/*)
never enters their RSS feed — the feed carries articles; the wall carries
the live deal cards. The operator holds written permission from KCL to
crawl it (documented in sources.yaml ``crawl_permission``; correspondence
on the operator's file), so the wall page is fetched through the
PoliteFetcher — rate-limited, conditional, identified UA — with the robots
override applied to KCL's own host only.

The page is converted to text with link targets preserved (the cleaner
keeps ``anchor text (URL)``), split into card-group chunks, and handed to
the normal evidence-verified parse: every published price still needs its
verbatim quote, which the cards carry ("$3.60 $19.99 82% Off"). Chunks
hash independently, so an unchanged part of the wall never re-parses.

Built without live access to the page (sandbox cannot reach the host), so
diagnostics are loud: a wall whose text carries almost no price tokens —
a JS-only render or a layout change — logs a sample and maps nothing.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from typing import Any

from .. import util
from ..db import DB
from ..models import RawPost, SourceConfig
from .base import SourceAdapter
from .rss import _permission_host

log = logging.getLogger("dealradar.fetch.kcl_wall")

#: Chunk size aimed under parse's max_chars_per_post so no card is cut.
MAX_CHUNK_CHARS = 4500
#: Cap on chunks per fetch: bounds LLM spend per wall refresh.
MAX_CHUNKS = 6
#: Fewer price tokens than this means the wall did not render server-side.
MIN_PRICE_TOKENS = 3

_PRICE_TOKEN = re.compile(r"\$\s?\d")


def _chunk_lines(text: str) -> list[str]:
    """Split wall text into card-group chunks on line boundaries."""
    chunks: list[str] = []
    cur: list[str] = []
    size = 0
    for line in text.split("\n"):
        if size + len(line) + 1 > MAX_CHUNK_CHARS and cur:
            chunks.append("\n".join(cur))
            cur, size = [], 0
            if len(chunks) >= MAX_CHUNKS:
                break
        cur.append(line)
        size += len(line) + 1
    if cur and len(chunks) < MAX_CHUNKS:
        chunks.append("\n".join(cur))
    # Only chunks that can prove a price are worth LLM time.
    return [c for c in chunks if _PRICE_TOKEN.search(c)]


class KclWallAdapter(SourceAdapter):
    def fetch_posts(
        self,
        source: SourceConfig,
        fetcher: Any,
        db: DB,
        cfg: Any,
        now: datetime,
        deadline: float | None,
    ) -> list[RawPost]:
        kw = {}
        override = _permission_host(source)
        if override:
            kw["robots_override_host"] = override
        res = fetcher.get(source.url, conditional=True, deadline=deadline, **kw)
        if res.not_modified or res.status == 304:
            return []
        if res.blocked_by_robots:
            raise RuntimeError("blocked_by_robots")
        if res.status != 200 or res.body is None:
            raise RuntimeError(f"http_{res.status or 'error'}")

        html = res.body.decode("utf-8", errors="replace")
        text = util.html_to_text(html)
        n_prices = len(_PRICE_TOKEN.findall(text))
        # Built blind (sandbox cannot reach the host): this line is the one
        # window into what KCL actually serves. Many '$' in the raw HTML but
        # few in the text means the cards live in script-tag JSON (client
        # render) and the adapter needs a different entry point.
        log.info("kcl wall %s: %d price tokens in text (%d chars); %d '$' in "
                 "raw html (%d chars); text head: %r",
                 source.name, n_prices, len(text),
                 len(_PRICE_TOKEN.findall(html)), len(html), text[:300])
        # Second window: the first run showed a 510 KB page with 33 '$' in
        # total, i.e. the cards are not text at all. If they ride a JSON
        # data blob (Next.js __NEXT_DATA__ or ld+json), its shape tells us
        # how to read them — that is the next adapter iteration.
        blob = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                         html, re.S)
        log.info("kcl wall %s: __NEXT_DATA__=%s ld+json=%d price-keys=%d "
                 "self.__next_f=%d blob head: %r",
                 source.name, "yes" if blob else "no",
                 html.count("application/ld+json"),
                 sum(html.count(k) for k in ('"price"', '"salePrice"',
                                              '"sale_price"', '"currentPrice"')),
                 html.count("self.__next_f"),
                 (blob.group(1)[:700] if blob else html[-1500:]))
        if n_prices < MIN_PRICE_TOKENS:
            # Loud diagnostic: the next run's logs must say exactly why.
            log.warning(
                "kcl wall %s: only %d price tokens in page text (%d chars) — "
                "JS-only render or layout change? sample: %r",
                source.name, n_prices, len(text), text[:300])
            return []

        posts: list[RawPost] = []
        for i, chunk in enumerate(_chunk_lines(text)):
            digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()[:12]
            posts.append(RawPost(
                source_name=source.name,
                uid=f"wall:{i}:{digest}",  # changed card group = new post
                url=source.url,            # credit: the wall itself
                title=f"KCL deal wall (part {i + 1})",
                text=chunk,
                published_at=None,         # first_seen governs freshness
                fetched_at=now,
            ))
        log.info("kcl wall %s: %d price tokens -> %d card-group posts",
                 source.name, n_prices, len(posts))
        return posts
