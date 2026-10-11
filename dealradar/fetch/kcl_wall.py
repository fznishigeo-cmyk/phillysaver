"""KCL live deal-wall adapter (permission-based).

The Krazy Coupon Lady's deal wall (thekrazycouponlady.com/coupons-for/*)
never enters their RSS feed — the feed carries articles; the wall carries
the live deal cards. The operator holds written permission from KCL to
crawl it (documented in sources.yaml ``crawl_permission``; correspondence
on the operator's file), so the wall page is fetched through the
PoliteFetcher — rate-limited, conditional, identified UA — with the robots
override applied to KCL's own host only.

Live (2026-10-11), the wall renders its cards server-side as plain text
blocks — "Online Deal / Amazon / <name> / $5.40$6.49 / 17% Off / 8 hours
ago" — with no hrefs in the markup, while a Schema.org ``ld+json`` block
lists the same offers as ``{price, url}`` with KCL's own Amazon affiliate
links. Each card becomes ONE synthesized post (name, both prices, the
percent, and the Amazon link matched by price) handed to the normal
evidence-verified parse: every published price still needs its verbatim
quote, which the synthesized text carries. The "N hours ago" stamp is kept
out of the text, so an unchanged card hashes identically run after run and
is parsed (paid for) once; it only feeds ``published_at``.

When no card parses (layout change) the older path still applies: the
text is split into card-group chunks for the parser, with loud diagnostics.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
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

#: One server-rendered card, as the text cleaner emits it (one field per
#: line). The regular price, the percent line and the age line are optional
#: (a card without a strike-through price has no percent either).
_CARD_RE = re.compile(
    r"^(?P<kind>Online Deal|In-Store Deal)\n"
    r"(?P<store>[^\n$]{1,40})\n"
    r"(?P<name>[^\n$][^\n]{2,160})\n"
    r"\$(?P<final>\d[\d,]*(?:\.\d{1,2})?)"
    r"(?:\$(?P<regular>\d[\d,]*(?:\.\d{1,2})?))?\n"
    r"(?:(?P<pct>\d{1,3})% Off\n)?"
    r"(?:(?P<age>\d+ (?:minute|hour|day)s? ago|Just now)\n?)?",
    re.M)
_AGE_RE = re.compile(r"(\d+) (minute|hour|day)s? ago")
_LD_JSON_RE = re.compile(
    r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)
_ASIN_IN_URL = re.compile(r"/dp/([A-Z0-9]{10})(?:[/?]|$)")


def _money(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _parse_cards(text: str) -> list[dict[str, Any]]:
    """Every deal card in the wall text, in page order."""
    cards: list[dict[str, Any]] = []
    for m in _CARD_RE.finditer(text):
        final = _money(m.group("final"))
        if final is None:
            continue
        regular = _money(m.group("regular") or "") if m.group("regular") else None
        cards.append({
            "kind": m.group("kind"),
            "store": m.group("store").strip(),
            "name": m.group("name").strip(),
            "final": final,
            "final_raw": m.group("final"),
            "regular": regular,
            "regular_raw": m.group("regular") or "",
            "pct": m.group("pct") or "",
            "age": m.group("age") or "",
        })
    return cards


def _ld_offers(html: str) -> list[dict[str, str]]:
    """``{price, url, asin}`` for every offer in the page's Schema.org
    Product/AggregateOffer block (KCL's affiliate Amazon links)."""
    offers: list[dict[str, str]] = []
    for block in _LD_JSON_RE.findall(html):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            agg = node.get("offers")
            inner = agg.get("offers") if isinstance(agg, dict) else None
            for off in inner or []:
                if not isinstance(off, dict):
                    continue
                price = _money(str(off.get("price") or ""))
                url = str(off.get("url") or "").strip()
                if price is None or not url.startswith("http"):
                    continue
                am = _ASIN_IN_URL.search(url)
                offers.append({"price": f"{price:.2f}", "url": url,
                               "asin": am.group(1) if am else ""})
    return offers


def _match_offer(card: dict[str, Any],
                 offers: list[dict[str, str]]) -> dict[str, str] | None:
    """The one offer whose price equals the card's final price; an
    ambiguous price (two offers) yields no link rather than a guess."""
    key = f"{card['final']:.2f}"
    hits = [o for o in offers if o["price"] == key]
    return hits[0] if len(hits) == 1 else None


def _published_from_age(age: str, now: datetime) -> datetime | None:
    if not age:
        return None
    if age == "Just now":
        return now
    m = _AGE_RE.search(age)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    delta = {"minute": timedelta(minutes=n), "hour": timedelta(hours=n),
             "day": timedelta(days=n)}[unit]
    return now - delta


def _card_post(card: dict[str, Any], offer: dict[str, str] | None,
               source: SourceConfig, now: datetime) -> RawPost:
    """One synthesized post per card. The text carries every number
    verbatim as the card shows it (the parser's quotes are checked against
    this text) and never the age stamp, so it hashes stably."""
    lines = [card["name"]]
    price = f"${card['final_raw']}"
    if card["regular_raw"]:
        price += f" (regular price ${card['regular_raw']}"
        price += f", {card['pct']}% Off)" if card["pct"] else ")"
    lines.append(f"{card['kind']} at {card['store']}: {price}.")
    if offer:
        lines.append(f"Buy: {offer['url']}")
    digest = hashlib.sha256(
        f"{card['name']}|{card['final_raw']}|{card['regular_raw']}".encode(
            "utf-8")).hexdigest()[:12]
    return RawPost(
        source_name=source.name,
        uid=f"card:{digest}",
        url=source.url,                     # credit: the wall itself
        title=card["name"],
        text="\n".join(lines),
        published_at=_published_from_age(card["age"], now),
        fetched_at=now,
    )


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

        cards = _parse_cards(text)
        if cards:
            offers = _ld_offers(html)
            posts: list[RawPost] = []
            matched = 0
            for card in cards:
                offer = _match_offer(card, offers)
                matched += offer is not None
                posts.append(_card_post(card, offer, source, now))
            # One window kept for the next iteration (the sandbox cannot
            # reach the host): the markup around the first card, to find
            # the cards' own KCL deal-page links (codes live there).
            i = html.find(cards[0]["name"][:30])
            log.info("kcl wall %s: %d cards, %d matched to %d ld+json offers; "
                     "markup around card 1: %r", source.name, len(cards),
                     matched, len(offers),
                     html[max(0, i - 400):i + 300] if i != -1 else "")
            return posts

        log.info("kcl wall %s: no cards parsed; %d price tokens in text "
                 "(%d chars); text head: %r", source.name, n_prices,
                 len(text), text[:300])
        if n_prices < MIN_PRICE_TOKENS:
            # Loud diagnostic: the next run's logs must say exactly why.
            log.warning(
                "kcl wall %s: only %d price tokens in page text (%d chars) — "
                "JS-only render or layout change? sample: %r",
                source.name, n_prices, len(text), text[:300])
            return []

        posts = []
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
