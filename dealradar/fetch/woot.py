"""Woot source adapter: the official Woot developer API (developer.woot.com).

Requires the ``WOOT_API_KEY`` secret (reported upstream as ``needs_key`` until
set). By default, one GET of ``/feed/All`` per run — the key's quota is
1,000 requests/day, so hourly + the 15-minute lane together use ~120 (12%).
Prices are written into the post text verbatim (``Sale price: $49.99`` /
``List price: $199.99``) so the evidence-quote check can match them exactly;
Woot's ``Condition`` field is quoted too (Woot sells a lot of refurbs, which
must carry the used/open-box floor, never pass as new).

The All feed holds thousands of offers, mostly shallow or unpriced, so every
offer is scored from its structured prices before mapping: only offers whose
discount off their own list price comes within 5 points of their publish
floor are mapped, used/refurb/open-box first, deepest first (a selection
prefilter only: savings for publishing are still computed in savings.py).
At most ``woot.max_new_items_per_run`` of them are posts the pipeline has
not seen yet (each costs an LLM parse and a critic call); the rest of the
selection arrives over the next runs.

Extra named feeds and numbered pages are opt-in after the manual probe
confirms them. Their combined, deduplicated offers also recheck stored deals.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from .. import util
from ..config import AppConfig, get_secret
from ..db import DB
from ..http_client import api_fetcher
from ..errors import AdapterNotConfigured, DeadlineExceeded
from ..models import RawPost, SourceConfig
from ..verify import (CONFIRM_GONE_H, _price_hidden_by_promo,
                      live_price_reading, woot_offer_url)
from .base import SourceAdapter

log = logging.getLogger("dealradar.fetch.woot")

FEED_URL = "https://developer.woot.com/feed/All"


def _feed_items(payload: Any) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        items = payload.get("Items") or payload.get("items") or payload.get("Offers") or []
        return items if isinstance(items, list) else []
    return []


def _offer_keys(item: Any) -> set[tuple[str, str]]:
    if not isinstance(item, dict):
        return set()
    keys = set()
    uid = item.get("OfferId") or item.get("Id")
    if uid:
        keys.add(("id", str(uid)))
    url = woot_offer_url({"canonical_url": item.get("Url")})
    if url:
        keys.add(("url", url))
    return keys

#: Selection ingests offers this many points under their publish floor
#: (near-misses still feed price history), like the Keepa lanes do.
FLOOR_SLACK_PCT = 5.0

#: A [min, max] price range wider than this (max/min) has no single price
#: to score: "from $9.99 to $49.99" is a menu, not a deal.
MAX_RANGE_RATIO = 1.10

#: Condition words that make an offer used for the floor (Woot's own labels:
#: "Refurbished", "Manufacturer Refurbished", "Used - Like New", "Open Box",
#: "Grade A", "New - Floor Model", "New - Damaged Box", ...). Anything not
#: plainly "New" counts as used as well, an unlabeled offer included.
_USED_RE = re.compile(
    r"refurb|recondition|renewed|\bused\b|open[\s-]*box|pre[\s-]*owned|"
    r"\bgrade\b|like[\s-]*new|floor[\s-]*model|display|damaged", re.I)
_NEW_RE = re.compile(r"(brand[\s-]+)?new\b", re.I)

#: Said in every used post, in the words the extractor tags as used: Woot's
#: own labels ("Grade B", "New - Floor Model", none at all) may not read so,
#: and an untagged post would publish against the lower new floor.
USED_MARKER = ("Counted as used/refurbished: Woot does not list it as "
               "plainly new.")


def _is_used(condition: str) -> bool:
    c = condition.strip()
    return not c or bool(_USED_RE.search(c)) or not _NEW_RE.match(c)


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip().lstrip("$").replace(",", ""))
        except ValueError:
            return None
    return None


def _single_price(value: Any, *, high: bool, allow_range: bool = False) -> float | None:
    """One price from Woot's shapes (number, string, [min, max] list or
    ``{"Minimum", "Maximum"}``): the high end for a sale price, the low end
    for a list price (the conservative discount). None when missing, zero,
    or a range whose ends differ by more than 10% (unless checking whether
    an existing price is listed within the full range)."""
    if isinstance(value, dict):
        vals = [value.get("Minimum"), value.get("Maximum")]
    elif isinstance(value, (list, tuple)):
        vals = list(value)
    else:
        vals = [value]
    nums = [n for n in (_num(v) for v in vals) if n is not None]
    if not nums or min(nums) <= 0:
        return None
    lo, hi = min(nums), max(nums)
    if not allow_range and hi > lo * MAX_RANGE_RATIO:
        return None
    return hi if high else lo


def _score(item: dict, cfg: AppConfig) -> tuple[float, bool] | None:
    """(percent off list, is_used) for an offer worth mapping, else None."""
    sale = _single_price(item.get("SalePrice"), high=True)
    list_price = _single_price(item.get("ListPrice"), high=False)
    if sale is None or list_price is None or sale >= list_price:
        return None  # no list price, or priced at/above it: no deal
    pct = 100.0 * (1.0 - sale / list_price)
    used = _is_used(str(item.get("Condition") or ""))
    floor = cfg.used_min_pct if used else cfg.min_pct
    if pct < floor - FLOOR_SLACK_PCT:
        return None
    return pct, used


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise DeadlineExceeded("woot: per-source deadline crossed")


def _fmt_money(value: Any) -> str:
    """Format an API price (number, string, [min, max] list, or Woot's
    ``{"Minimum": x, "Maximum": y}`` object) as ``$X.XX``."""
    if value is None:
        return ""
    if isinstance(value, dict):
        lo = _fmt_money(value.get("Minimum"))
        hi = _fmt_money(value.get("Maximum"))
        if lo and hi and lo != hi:
            return f"{lo} - {hi}"
        return lo or hi
    if isinstance(value, (list, tuple)):
        parts = [_fmt_money(v) for v in value]
        return " - ".join(p for p in parts if p)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"${value:,.2f}"
    s = str(value).strip()
    if not s:
        return ""
    return s if s.startswith("$") else f"${s}"


def _photo_url(value: Any) -> str:
    """Woot's feed Photo URI or its offer/OfferItem Photos [{Url, ...}]."""
    if isinstance(value, str):
        return value if value.startswith(("http://", "https://")) else ""
    if isinstance(value, dict):
        return _photo_url(value.get("Url"))
    if isinstance(value, list):
        return next((url for photo in value if (url := _photo_url(photo))), "")
    return ""


def _item_photo(item: dict) -> str:
    image = _photo_url(item.get("Photo")) or _photo_url(item.get("Photos"))
    if image:
        return image
    # Offer-level Photos take precedence over the variants' photos.
    for variant in item.get("Items") or []:
        if isinstance(variant, dict):
            image = (_photo_url(variant.get("Photo")) or
                     _photo_url(variant.get("Photos")))
            if image:
                return image
    return ""


def _recheck_offers(db: DB, items: list, now: datetime, *,
                    cfg: AppConfig | None = None,
                    source: SourceConfig | None = None) -> dict[str, int]:
    """Reuse the official feed; absence is never evidence an offer ended."""
    from ..dedupe import reprice_existing
    from .. import accuracy

    cfg = cfg if cfg is not None else AppConfig()
    source = source or SourceConfig(name="woot", method="api", url=FEED_URL, adapter="woot")
    stats = dict(confirmed=0, repriced=0, range_skipped=0, above_range=0)
    offers: dict[str, list[dict]] = {}
    for item in items:
        if isinstance(item, dict):
            key = woot_offer_url({"canonical_url": item.get("Url")})
            if key:
                offers.setdefault(key, []).append(item)
    for deal in db.all_deals(status="alive"):
        matches = offers.get(woot_offer_url(deal), [])
        if len(matches) != 1:
            continue
        item = matches[0]
        payload = dict(deal)
        ends = util.parse_iso(str(item.get("EndDate") or ""))
        if ends is not None and ends <= now:
            accuracy.record(db, cfg, deal["dedupe_key"], now, source="woot", unavailable=True)
            payload.update(death="api_ended", died_at=util.iso(now), gone_suspect_at="")
            db.update_deal(deal["dedupe_key"], payload=payload, status="dead")
            continue
        low = _single_price(item.get("SalePrice"), high=False, allow_range=True)
        price = _single_price(item.get("SalePrice"), high=True, allow_range=True)
        final = deal.get("final_price")
        promo = _price_hidden_by_promo(deal)
        if item.get("IsSoldOut") is True:
            accuracy.record(db, cfg, deal["dedupe_key"], now, source="woot", unavailable=True)
        elif item.get("IsSoldOut") is False and low is not None and low == price:
            accuracy.record(db, cfg, deal["dedupe_key"], now, source="woot", price=price)
        if item.get("IsSoldOut") is False and low is not None and price is not None and final is not None:
            if low != price:
                stats["range_skipped"] += 1
                stats["above_range"] += int(final > price + 0.01)
            if not deal.get("gone_suspect_at") and not promo:
                if low == price and abs(price - final) > max(0.01, final * 0.005) + 1e-9:
                    # Collapse equal sale endpoints only; ListPrice goes through
                    # the existing mapper unchanged, without the intake floors.
                    post = WootAdapter._map_item(dict(item, SalePrice=price), source, now)
                    if post is not None and post.structured is not None:
                        post.structured["regular_price_basis"] = "List price on Woot"
                    if post is not None and post.structured is not None \
                            and reprice_existing(db, deal, post, cfg, now):
                        stats["repriced"] += 1
                        payload = dict(deal)
                        final = deal["final_price"]
                    elif price < final:
                        continue  # an uncorrected lower price cannot refresh ours
                if low - 0.01 <= final <= price + 0.01:
                    payload["woot_listed_at"] = util.iso(now)
                    db.update_deal(deal["dedupe_key"], payload=payload)
                    stats["confirmed"] += 1
        checked = util.parse_iso(deal.get("price_rechecked_at") or "")
        every = CONFIRM_GONE_H if deal.get("gone_suspect_at") else 3.0
        if checked and (now - checked).total_seconds() < every * 3600:
            continue
        if item.get("IsSoldOut") is True:
            verdict = False
        elif item.get("IsSoldOut") is False and price is not None and deal.get("final_price") is not None:
            if price != low or promo:
                continue  # a variant range or coupon is not proof the exact price moved
            verdict = price <= float(deal["final_price"]) + 0.01
        else:
            continue
        outcome = live_price_reading(payload, verdict, now)
        db.update_deal(deal["dedupe_key"], payload=payload,
                       status="dead" if outcome == "dead" else None)
    return stats


class WootAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http_client
        self._sleep = sleep_fn

    @staticmethod
    def _fetch_items(client: Any, cfg: AppConfig, key: str,
                     deadline: float | None, now: datetime | None = None) -> list:
        named = list(cfg.woot_named_feeds)
        if named and cfg.woot_named_feeds_rotation_h > 0 and now is not None:
            # One category feed per period, in turn: the key allows 1,000
            # requests/day and every run already reads All (probe 2026-10-15:
            # All stops at 5,000 offers; category feeds hold thousands more).
            slot = int(now.timestamp() // (cfg.woot_named_feeds_rotation_h * 3600))
            named = [named[slot % len(named)]]
        feeds = ["All"]
        for name in named:
            # Feed names are path segments, never URLs or query strings.
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
                raise ValueError("invalid Woot feed name")
            if name.lower() not in {f.lower() for f in feeds}:
                feeds.append(name)
        expanded = cfg.woot_feed_pages > 1 or len(feeds) > 1
        items: list = []
        seen: set[tuple[str, str]] = set()
        fetched = False
        for feed in feeds:
            page_size = 0
            feed_seen: set[tuple[str, str]] = set()
            for page in range(1, max(1, cfg.woot_feed_pages) + 1):
                try:
                    _check_deadline(deadline)
                    # No paging contract is assumed by default. Opt-in uses
                    # the page candidate tested by woot-probe; never follow
                    # server-supplied links with our credential.
                    url = FEED_URL.rsplit("/", 1)[0] + "/" + feed
                    resp = client.api_get(
                        url, params={"page": page} if page > 1 else None,
                        headers={"x-api-key": key}, deadline=deadline,
                        respect_robots=False, follow_redirects=False)
                    resp.raise_for_status()
                    batch = _feed_items(resp.json())
                except DeadlineExceeded:
                    if not fetched:
                        raise
                    return items  # completed pages still provide evidence
                except (httpx.HTTPError, ValueError):
                    if not fetched:
                        raise
                    log.info("woot: extra feed request failed; using completed pages")
                    return items
                fetched = True
                if not expanded:
                    return batch  # preserve existing order/counts, even duplicates
                if page == 1:
                    page_size = len(batch)
                new_on_page = False
                for item in batch:
                    keys = _offer_keys(item)
                    if keys and not keys & feed_seen:
                        new_on_page = True
                    feed_seen.update(keys)
                    if not keys or not keys & seen:
                        items.append(item)
                    seen.update(keys)
                # Per-feed identities matter: All may already contain a named
                # feed's first page, but that feed's later pages can add offers.
                if not batch or len(batch) < page_size or not new_on_page:
                    break
        return items

    def fetch_posts(
        self,
        source: SourceConfig,
        fetcher: Any,
        db: DB,
        cfg: AppConfig,
        now: datetime,
        deadline: float | None,
    ) -> list[RawPost]:
        # db (when given) tells which posts the pipeline has already seen.
        key = get_secret("WOOT_API_KEY")
        if not key:
            raise AdapterNotConfigured("woot api key missing")

        with api_fetcher(fetcher, db, cfg, http_client=self._http, sleep_fn=self._sleep) as client:
            # The key travels ONLY in the x-api-key header: query-string keys
            # end up inside httpx error messages and from there could leak
            # into recorded (published) source errors.
            items = self._fetch_items(client, cfg, key, deadline, now)

        if db is not None:
            self.recheck_stats = _recheck_offers(db, items, now, cfg=cfg, source=source)

        # Score every live offer, then select used before new, deepest
        # first (feed order breaks ties), up to the per-run cap. Posts the
        # pipeline has seen ride free; at most max_new_items_per_run unseen
        # ones go out per run, the rest of the selection on later runs.
        scored: list[tuple[bool, float, int, dict]] = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict) or item.get("IsSoldOut"):
                continue
            ends = util.parse_iso(str(item.get("EndDate") or ""))
            if ends is not None and ends <= now:
                continue  # already over
            score = _score(item, cfg)
            if score is not None:
                pct, used = score
                scored.append((not used, -pct, idx, item))
        scored.sort(key=lambda s: s[:3])
        n_used = sum(1 for s in scored if not s[0])

        posts: list[RawPost] = []
        selected = unseen = held = 0
        for _new, _pct, _idx, item in scored:
            if selected >= cfg.woot_max_items_per_run:
                break
            post = self._map_item(item, source, now)
            if post is None:
                continue
            selected += 1
            if db is None or not db.post_is_known(post.content_hash):
                if unseen >= cfg.woot_max_new_items_per_run:
                    held += 1
                    continue
                unseen += 1
            posts.append(post)
        log.info("woot: %d raw offers -> %d scored (%d used, %d new) -> "
                 "%d mapped (%d unseen, %d more held for later runs)",
                 len(items), len(scored), n_used, len(scored) - n_used,
                 len(posts), unseen, held)
        return posts

    @staticmethod
    def _map_item(item: dict, source: SourceConfig,
                  now: datetime) -> RawPost | None:
        uid = str(item.get("OfferId") or item.get("Id") or "")
        title = str(item.get("Title") or "")
        if not uid or not title:
            return None
        lines = [title]
        site = str(item.get("Site") or "")
        if site:
            lines.append(f"Woot category: {site}.")
        sale = _fmt_money(item.get("SalePrice"))
        if sale:
            lines.append(f"Sale price: {sale}")
        list_price = _fmt_money(item.get("ListPrice"))
        if list_price:
            lines.append(f"List price: {list_price}")
        condition = str(item.get("Condition") or "")
        if condition:
            # Quoted so the extractor tags refurb/used honestly.
            lines.append(f"Condition: {condition}.")
        if _is_used(condition):
            lines.append(USED_MARKER)
        end_date = str(item.get("EndDate") or "")
        if end_date:
            lines.append(f"Ends: {end_date}")
        lines.append("Sold online at Woot.com (Amazon's official deals site).")
        structured = None
        plain_money = r"\$\d+(?:,\d{3})*(?:\.\d{1,2})?"
        if re.fullmatch(plain_money, sale) and re.fullmatch(plain_money, list_price):
            conditions = []
            if _is_used(condition):
                conditions = ["used"]
                if re.search(r"open[\s-]*box", condition, re.I):
                    conditions.append("open_box")
            structured = {
                "item": title, "store": "Woot",
                "product_url": str(item.get("Url") or ""),
                "online_only": "true", "final_price": _num(sale),
                "regular_price": _num(list_price), "conditions": conditions,
                "expires_at_raw": end_date, "confidence": 1.0,
                "evidence": {"final_price_quote": f"Sale price: {sale}",
                             "regular_price_quote": f"List price: {list_price}"},
            }
        return RawPost(
            source_name=source.name,
            uid=uid,
            url=str(item.get("Url") or ""),
            title=title,
            text="\n".join(lines),
            published_at=util.parse_feed_date(item.get("StartDate"),
                                              source.tz, now),
            fetched_at=now,
            image_url=_item_photo(item),
            structured=structured,
        )
