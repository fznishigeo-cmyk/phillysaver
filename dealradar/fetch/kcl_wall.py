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

The page also embeds its cards' full data objects (an "initialDeals"
array in a serialized-state attribute: title, store, products with the
regular/sale prices and KCL's Amazon affiliate link, promo lines and codes,
final price, percent, expiry). That is the primary path: one synthesized
post per embedded deal, every number and promo line as the page states it,
so the evidence check still holds and a coupon-only price reaches the
parser WITH its coupon. The text cards are the fallback, and the chunk
path the fallback's fallback, each with loud diagnostics.
"""

from __future__ import annotations

import hashlib
import html as _html_lib
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


_INITIAL_DEALS_KEY = '"initialDeals":'


def _bracketed(text: str, start: int) -> str:
    """The JSON array starting at text[start] == '[' through its matching
    ']', respecting strings; '' when unbalanced."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return ""


def _devalue(v: Any) -> Any:
    """Decode the page's serialized state: ``[0, x]`` is the value x,
    ``[0]`` is null, ``[1, [...]]`` is an array; objects carry encoded
    values. Anything else is returned as is."""
    if isinstance(v, list):
        if v and v[0] == 0:
            return _devalue(v[1]) if len(v) > 1 else None
        if len(v) == 2 and v[0] == 1 and isinstance(v[1], list):
            return [_devalue(x) for x in v[1]]
        return [_devalue(x) for x in v]
    if isinstance(v, dict):
        return {k: _devalue(x) for k, x in v.items()}
    return v


def _embedded_deals(html: str) -> list[dict[str, Any]]:
    """The deal objects the page embeds for its own cards (title, store,
    products with regular/sale price and Amazon link, promos with codes,
    final price, percent, expiry), decoded; [] when absent or changed."""
    raw = _html_lib.unescape(html)
    i = raw.find(_INITIAL_DEALS_KEY)
    if i == -1:
        return []
    j = raw.find("[", i + len(_INITIAL_DEALS_KEY))
    if j == -1:
        return []
    block = _bracketed(raw, j)
    if not block:
        return []
    try:
        decoded = _devalue(json.loads(block))
    except ValueError:
        return []
    return [d for d in decoded if isinstance(d, dict) and d.get("title")] \
        if isinstance(decoded, list) else []


def _fmt_money(v: Any) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return ""


def _promo_lines(product: dict[str, Any]) -> list[str]:
    """Every promo / coupon line (with its code when given) attached to a
    product, in the page's own words: the parser turns these into stack
    steps, so a coupon-only price is never mistaken for a shelf price."""
    out: list[str] = []
    for key, val in product.items():
        if not (key == "promos" or key.endswith("oupons")) or not isinstance(val, list):
            continue
        for entry in val:
            if not isinstance(entry, dict):
                continue
            line = str(entry.get("line") or entry.get("title") or
                       entry.get("description") or "").strip()
            code = str(entry.get("code") or "").strip()
            if line and code:
                out.append(f"{line} (code {code})")
            elif line:
                out.append(line)
            elif code:
                out.append(f"use code {code}")
    return out


def _embedded_post(deal: dict[str, Any], source: SourceConfig,
                   now: datetime) -> RawPost | None:
    """One synthesized post per embedded deal; the text carries every
    number as the page states it (regular, sale, final, percent), the
    promo wording and codes, the expiry and the Amazon link. Volatile
    fields (updatedAt) stay out so an unchanged deal hashes identically."""
    products = [p for p in (deal.get("products") or []) if isinstance(p, dict)]
    product = products[0] if products else {}
    name = str(product.get("name") or deal.get("title") or "").strip()
    final = deal.get("finalPrice")
    if not name or final is None:
        return None
    store = (deal.get("store") or {}).get("name") if isinstance(
        deal.get("store"), dict) else ""
    store = str(store or "Amazon")
    kinds = [k for k in (deal.get("dealType") or []) if isinstance(k, str)]
    kind = "Online Deal" if "Online" in kinds or not kinds else "In-Store Deal"
    regular = deal.get("originalPrice")
    if regular is None:
        regular = product.get("regularPrice")
    sale = product.get("discountPrice")
    pct = deal.get("percentSavings")

    price = _fmt_money(final)
    bits = []
    if regular is not None and _fmt_money(regular):
        bits.append(f"regular price {_fmt_money(regular)}")
    if pct is not None:
        try:
            bits.append(f"{int(round(float(pct)))}% Off")
        except (TypeError, ValueError):
            pass
    lines = [name, f"{kind} at {store}: {price}" + (f" ({', '.join(bits)})" if bits else "") + "."]
    if sale is not None and _fmt_money(sale) and _fmt_money(sale) != price:
        lines.append(f"Sale price {_fmt_money(sale)}; final price {price} after the promo below.")
    promos = _promo_lines(product)
    if promos:
        lines.append("How: " + " + ".join(promos) + ".")
    if deal.get("displayAsLowAs"):
        lines.append("Price shown is \"as low as\".")
    expires = str(deal.get("expiredAt") or "").strip()
    if expires:
        lines.append(f"Expires: {expires[:10]}.")
    url = str(product.get("url") or "").strip()
    if url.startswith("http"):
        lines.append(f"Buy: {url}")
    created = util.parse_iso(str(deal.get("createdAt") or ""))
    uid = str(deal.get("_id") or "").strip()
    if not uid:
        uid = hashlib.sha256(f"{name}|{final}|{regular}".encode("utf-8")).hexdigest()[:12]
    return RawPost(
        source_name=source.name,
        uid=f"deal:{uid}",
        url=source.url,                     # credit: the wall itself
        title=name,
        text="\n".join(lines),
        published_at=created,
        fetched_at=now,
    )


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

        embedded = _embedded_deals(html)
        if embedded:
            posts = [p for p in (_embedded_post(d, source, now) for d in embedded)
                     if p is not None]
            log.info("kcl wall %s: %d embedded deals -> %d posts",
                     source.name, len(embedded), len(posts))
            return posts

        cards = _parse_cards(text)
        if cards:
            offers = _ld_offers(html)
            posts: list[RawPost] = []
            matched = 0
            for card in cards:
                offer = _match_offer(card, offers)
                matched += offer is not None
                posts.append(_card_post(card, offer, source, now))
            # Reached only when the embedded deal objects are gone (page
            # change): say so loudly, with the markup head for the next fix.
            log.warning("kcl wall %s: no embedded deals; %d text cards, %d "
                        "matched to %d ld+json offers; markup head: %r",
                        source.name, len(cards), matched, len(offers),
                        html[:600])
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
