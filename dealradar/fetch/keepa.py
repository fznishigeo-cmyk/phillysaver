"""Keepa source adapter: Amazon price-drop discovery via the official Keepa API.

Keepa (https://keepa.com) runs a legal, licensed Amazon price database. This
adapter queries its *deals* endpoint for products whose current price recently
dropped far below their average — the heavy-hitter radar — and synthesizes a
plain-text post per product so the normal parse/evidence/savings pipeline
applies unchanged (every published number still has a verbatim quote, here
against Keepa's own figures, with the average price as the honest basis).

Each pull pages through three /deal queries (150 rows a page): Amazon's own
price far under its 90-day average; FRESH Amazon Warehouse deals (newest
first); and the DEEP 90-day warehouse list, whose page window rotates across
pulls so successive pulls walk the whole list instead of re-reading page 0.
Pages beyond the Amazon and FRESH first pages run only while the shared token
budget keeps a reserve for the product calls (config.yaml ``keepa:``). Last,
on idle tokens only, a WAREHOUSE SWEEP (Keepa's product finder plus one
batched /product check) finds open-box items /deal never lists. Each lane
emits at most its cap of NEW posts (not yet content-hashed) per pull, so a
backlog (the first pulls after a deploy) arrives over several runs instead
of one run its job timeout cannot finish.

Requires the ``KEEPA_KEY`` secret (reported as ``needs_key`` until set).
Cost model: Keepa bills a flat subscription with a token-per-minute allowance;
running out of tokens delays data, never adds charges. Prices arrive in cents;
-1 means "no data".
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx

from .. import util
from ..config import AppConfig, get_secret, error_summary
from ..db import DB
from ..http_client import PoliteFetcher, api_fetcher
from ..errors import AdapterNotConfigured, DeadlineExceeded
from ..keepa_budget import (MIN_FOR_DEAL_QUERY, KeepaBudget, describe_429,
                            response_payload, run_gap_forecast)
from ..keepa_categories import (NAMES_META, ROOTS_META, cached_meta,
                               category_from_name, remember_roots, root_id)
from ..models import RawPost, SourceConfig
from ..verify import _keepa_image_name, is_amazon_deal, is_used_deal, keepa_time
from .base import SourceAdapter

log = logging.getLogger("dealradar.fetch.keepa")

DEALS_URL = "https://api.keepa.com/deal"
QUERY_URL = "https://api.keepa.com/query"      # product finder
PRODUCT_URL = "https://api.keepa.com/product"
CATEGORY_URL = "https://api.keepa.com/category"

#: Keepa domainId for amazon.com (US).
DOMAIN_US = 1

#: Ask Keepa for drops at/above this percent-off-average. Slightly below our
#: publish floor (publish.min_pct, 40) so near-misses still enter the DB
#: and price history.
MIN_DROP_PCT = 35

#: Warehouse queries ask as loosely as Keepa allows (10% is its documented
#: minimum): a warehouse /deal row only appears when the WAREHOUSE price
#: itself moved by this much within dateRange, so a tighter floor hides
#: open-box items far under the NEW price that never dropped against their
#: own warehouse history. The honest gate (used price vs the new price,
#: config keepa.warehouse_min_drop_pct) runs client-side.
WAREHOUSE_DELTA_PCT = (10, 100)

#: Popularity nets (Amazon sales rank). The new-price lane stays strict:
#: obscure SKUs have sparse histories whose "averages" fabricate drops. The
#: warehouse lane compares against Amazon's live price or a dense 90-day new
#: average, so it fishes a much wider net (mispriced open-box hides on
#: mid-rank items).
AMAZON_MAX_RANK = 30000
WAREHOUSE_MAX_RANK = 200000

#: Books & Kindle: algorithmic publisher pricing ($995 print-on-demand
#: reports "dropping" 60%) dominates the drop list and is noise, never a
#: deal a person wants.
EXCLUDED_CATEGORIES = [283155, 133140011]

#: Only products people actually buy: obscure OEM parts and zero-review
#: SKUs produce sparse price histories whose "averages" fabricate 95%+
#: drops. Rating is in tenths of stars (20 = 2.0 stars).
MIN_RATING = 20

#: Keepa price-type indexes: 0 = Amazon's own NEW price, 1 = lowest NEW
#: offer, 9 = Amazon Warehouse / Amazon Resale (open-box & used, sold by
#: Amazon). Third-party used sellers are deliberately NOT tracked.
PRICE_TYPE_AMAZON = 0
PRICE_TYPE_NEW = 1
PRICE_TYPE_WAREHOUSE = 9
#: 4 = the product's List Price (manufacturer/seller suggested price).
PRICE_TYPE_LIST = 4
#: ... and 11 = the count of NEW offers (a count, not a price).
COUNT_NEW = 11

#: Keepa dateRange: 0 = the last 24 hours; 3 = drop measured against the
#: 90-day average — the honest "typical price" basis (day/week windows miss
#: slow-boiled clearance). 3 is also the 90-day index of a row's ``avg``.
DATE_RANGE_DAY = 0
DATE_RANGE_MONTH = 2
DATE_RANGE_90D = 3

#: Keepa sortType: 1 = deal age (newest first), 2 = absolute drop (big-ticket
#: first: percent-sorting surfaces $5 junk), 4 = percent drop.
SORT_NEWEST = 1
SORT_ABS_DROP = 2
SORT_PCT_DROP = 4

#: Keepa's /deal page size: a page with fewer rows is the query's last.
DEAL_PAGE_SIZE = 150

#: Estimated tokens per /deal page. Only the reserve check uses it: the
#: budget learns the real tokensLeft from every response.
DEAL_PAGE_COST = 5.0

#: db meta key: the page the rotating DEEP warehouse window starts at on
#: the next pull (wraps to 0 when a page comes back short).
DEEP_PAGE_META = "keepa_deep_page"

#: db meta key: the product-finder page the warehouse sweep reads next.
SWEEP_PAGE_META = "keepa_sweep_page"

#: Product finder page size bounds: Keepa's minimum perPage is 50, and one
#: /product call checks at most 100 ASINs (1 token each with stats).
SWEEP_MIN_PER_PAGE = 50
SWEEP_MAX_PER_PAGE = 100


def _finder_cost(per_page: int) -> float:
    """Estimated tokens for one product-finder page: 10 + 1 per 100 ASINs.
    Only the sweep's gate uses it: the budget learns the real tokensLeft
    from every response."""
    return 10.0 + math.ceil(per_page / 100)


#: Self-throttle: one pull per fast-lane cycle (every 10 minutes). The
#: operator's Keepa plan refills 20 tokens/min (~200 per cycle); a pull's
#: /deal pages cost ~5 each and stop at the reserve kept for the product
#: calls (photos, rechecks). 9, not 10, so scheduler jitter never skips a
#: slot.
MIN_INTERVAL_MINUTES = 9.0


def _cents(v: Any) -> float | None:
    """Keepa prices are integer cents; -1 (or junk) means no data."""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
        return round(float(v) / 100.0, 2)
    return None


def _price_at(arr: Any, price_type: int) -> float | None:
    """Price for one price type. Keepa's ``current`` is an int array indexed
    by price type; tolerate a bare scalar too."""
    if isinstance(arr, (list, tuple)):
        if len(arr) > price_type:
            return _cents(arr[price_type])
        return None
    return _cents(arr)


def _feed_time(row: dict[str, Any], now: datetime) -> str:
    at = keepa_time(row.get("lastUpdate"))
    return util.iso(min(at, now)) if at is not None else ""


#: Amazon's public product-image CDN; Keepa reports the image filename.
AMAZON_IMAGE_CDN = "https://m.media-amazon.com/images/I/"


def _image_url(row: dict[str, Any]) -> str:
    """Product-image URL from a Keepa deal row's ``image`` field, which
    arrives as a filename string or an int/byte array of its characters.
    This points at Amazon's own public CDN for the product being linked —
    no image is ever copied or stored."""
    img = row.get("image")
    if isinstance(img, (list, tuple)):
        try:
            img = "".join(chr(int(c)) for c in img)
        except (TypeError, ValueError):
            return ""
    elif isinstance(img, bytes):
        img = img.decode("ascii", "ignore")
    if not isinstance(img, str):
        return ""
    img = img.strip()
    if not img or "/" in img or not re.fullmatch(r"[\w.,+%-]+", img):
        return ""  # defensive: a filename, never a path or junk
    return AMAZON_IMAGE_CDN + img


def _dense_history(avg: Any, price_type: int) -> bool:
    """True when every window beyond the first (week/month/90-day) carries a
    price for this price type. A sparse Amazon-price history fabricates
    absurd averages — an OEM part Amazon listed once at $460 makes today's
    normal $13 price look like a 97% drop. A product Amazon genuinely sells
    has data in every window. A flat array (degraded shape) passes when it
    has a price at all."""
    if not isinstance(avg, (list, tuple)) or not avg:
        return False
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in avg):
        return _price_at(avg, price_type) is not None
    tail = avg[1:] if len(avg) > 1 else avg
    return all(_price_at(w, price_type) is not None for w in tail)


def _avg_price(avg: Any, price_type: int) -> float | None:
    """Average price for one price type from Keepa's ``avg``, which in the
    deals response is an array of arrays: [dateRange][priceType] for the
    day/week/month/90-day windows. Prefer the longest window that has data.
    A flat int array (indexed by price type) is tolerated as well."""
    if not isinstance(avg, (list, tuple)) or not avg:
        return None
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in avg):
        return _price_at(avg, price_type)
    for window in reversed(avg):  # longest interval first
        p = _price_at(window, price_type)
        if p is not None:
            return p
    return None


def _avg90_price(avg: Any, price_type: int) -> float | None:
    """The 90-day window's average for one price type: ``avg[3]`` of a deal
    row's [dateRange][priceType] array, or a flat array (a stats ``avg90``)
    indexed by price type. No shorter-window fallback: the post labels it
    a 90-day average."""
    if not isinstance(avg, (list, tuple)) or not avg:
        return None
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in avg):
        return _price_at(avg, price_type)
    if len(avg) > DATE_RANGE_90D:
        return _price_at(avg[DATE_RANGE_90D], price_type)
    return None


def _regular_basis(avg: Any, price_type: int) -> tuple[float, str] | None:
    """The honest "regular" price for an Amazon price drop: the LOWER of
    the 30-day and 90-day averages, with its label. Live wart: clearance
    leggings that sat unsold at $16-20 for weeks had a $16.02 time-weighted
    90-day average while Amazon's own page said "Typical price: $6.01"; the
    site claimed -77% where Amazon showed -32%. A flat array (degraded
    shape) is its one average."""
    avg90 = _avg_price(avg, price_type)  # longest window with data
    if avg90 is None or avg90 <= 0:
        return None
    if isinstance(avg, (list, tuple)) and len(avg) > DATE_RANGE_90D \
            and isinstance(avg[DATE_RANGE_MONTH], (list, tuple)):
        avg30 = _price_at(avg[DATE_RANGE_MONTH], price_type)
        if avg30 is not None and 0 < avg30 < avg90:
            return avg30, "30-day average"
    return avg90, "90-day average"


def _amazon_basis(row: dict[str, Any], avg: Any,
                  list_ratio: float) -> tuple[float, str] | None:
    """The reference price Amazon itself shows, so our percent matches
    the product page (operator, 2026-10-13). Amazon shows a List Price
    only when customers recently bought at it, else a Typical price (the
    median price paid over 90 days). The List Price counts when Amazon's
    own 90-day average reached ``list_ratio`` of it (the item really sold
    near list); otherwise the lower of the 30- and 90-day averages stands
    in for the Typical price. Never a list price the item never sold at."""
    typical = _regular_basis(avg, PRICE_TYPE_AMAZON)
    if typical is None:
        return None
    listed = _price_at(row.get("current"), PRICE_TYPE_LIST)
    avg90 = _avg_price(avg, PRICE_TYPE_AMAZON)
    if listed and avg90 and listed > typical[0] and avg90 >= list_ratio * listed:
        return listed, "List Price"
    return typical


def _count_at(arr: Any, idx: int) -> int | None:
    """A count from Keepa's ``current`` array (-1 or junk = no data)."""
    if isinstance(arr, (list, tuple)) and len(arr) > idx:
        v = arr[idx]
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            return v
    return None


def _new_price_basis(row: dict[str, Any]) -> tuple[float, str] | None:
    """What a shopper really pays NEW for a warehouse row, and its label.

    When Amazon sells the item, the LOWER of Amazon's own price and the
    lowest current new offer (Amazon is often the high-priced seller on a
    listing others undercut). Otherwise the LOWER of the lowest current new
    offer and the 90-day average new price. A lone third-party new offer is
    no honest basis, whatever its history (live wart: a $58.95 marketplace
    "new" price made a $5.70 open-box phone case look 90% off; the critic
    flagged it suspect_msrp), nor is one without a dense 90-day history ->
    None.
    """
    current = row.get("current")
    amazon = _price_at(current, PRICE_TYPE_AMAZON)
    offer = _price_at(current, PRICE_TYPE_NEW)
    if amazon is not None and amazon > 0:
        if offer is not None and 0 < offer < amazon:
            return offer, "lowest new price (current offer)"
        return amazon, "Amazon's own new price"
    if offer is None or offer <= 0:
        return None  # nothing sells new right now
    offers = _count_at(current, COUNT_NEW)
    if offers is not None and offers <= 1:
        return None  # one marketplace seller sets its own "new" price
    avg_raw = row.get("avg") or row.get("avg90")
    avg90 = (_avg90_price(avg_raw, PRICE_TYPE_NEW)
             if _dense_history(avg_raw, PRICE_TYPE_NEW) else None)
    if avg90 is None or avg90 <= 0:
        return None
    if avg90 < offer:
        return avg90, "lowest new price (90-day average)"
    return offer, "lowest new price (current offer)"


def _cursor(db: DB | None, key: str) -> int:
    """Start page of a rotating window (DEEP, sweep): 0 when unset."""
    raw = db.meta_get(key) if db is not None else None
    try:
        return max(0, int(raw or 0))
    except ValueError:
        return 0


def _pct_key(pct: float, floors: tuple[float, ...]) -> str:
    """The discount as the content hash sees it: the whole percent rounded
    DOWN, plus how many publish floors it clears. Keepa's averages drift by
    cents every update, so only a real move may re-parse; but a drift
    across a floor must (a stored 40.3% may not stay published at the 40%
    floor once the honest figure is 39.8%)."""
    pct = round(pct, 6)  # float noise never flips a bucket
    key = f"{math.floor(pct)}%"
    if floors:
        key += f" (clears {sum(1 for f in floors if pct >= f)}/{len(floors)})"
    return key


def _structured_category(row: dict, names: dict[str, str] | None) -> dict:
    name = (names or {}).get(root_id(row), "")
    category = category_from_name(name)
    return {"category": category, "keepa_category_name": name} if category else {}


def _product_row(product: dict[str, Any]) -> dict[str, Any]:
    """A /product record (stats) in /deal row shape for the warehouse
    mapper: ``stats.current`` uses the same price-type indexes, and
    ``stats.avg90`` (flat, by price type) is the 90-day basis."""
    stats = product.get("stats")
    stats = stats if isinstance(stats, dict) else {}
    return {"asin": product.get("asin"), "title": product.get("title"),
            "lastUpdate": product.get("lastUpdate"),
            "rootCat": product.get("rootCategory"),
            "current": stats.get("current"), "avg90": stats.get("avg90"),
            "image": _keepa_image_name(product)}


def reprice_post(product: dict[str, Any], deal: dict[str, Any], cfg,
                 now: datetime, *, buybox: bool = False) -> RawPost | None:
    """A correction from an already-paid record, even below the deal floors."""
    from ..verify import _keepa_since_price, _price_hidden_by_promo, is_used_deal

    if is_used_deal(deal) or _price_hidden_by_promo(deal) \
            or (not buybox and _keepa_since_price(product.get("lastUpdate"), deal) is False):
        return None
    stats = product.get("stats")
    if not isinstance(stats, dict):
        return None
    # The legacy correction uses Amazon's own slot. Top picks can instead
    # use a qualified Buy Box total from its independently dated reading.
    live = _price_at(stats.get("current"), PRICE_TYPE_AMAZON)
    stamp = product.get("lastUpdate")
    if buybox:
        from ..buybox import fresh_reading
        reading = fresh_reading(deal, product, cfg, now)
        if reading is None:
            return None
        live, _observed = reading
        stamp = stats["lastBuyBoxUpdate"]
    avg30 = _price_at(stats.get("avg30"), PRICE_TYPE_AMAZON)
    avg90 = _price_at(stats.get("avg90"), PRICE_TYPE_AMAZON)
    shown = deal.get("final_price")
    if live is None or avg30 is None or avg90 is None or min(avg30, avg90) <= 0 \
            or not isinstance(shown, (int, float)) or shown <= 0:
        return None
    if live == shown or abs(live - shown) * 100 / shown + 1e-9 < cfg.keepa_reprice_min_pct:
        return None
    # The same reference rule as the /deal lane (_amazon_basis): a List
    # Price the item recently sold near, else the Typical-price stand-in.
    regular = min(avg30, avg90)
    label = "30-day average" if avg30 < avg90 else "90-day average"
    listed = _price_at(stats.get("current"), PRICE_TYPE_LIST)
    if listed and listed > regular \
            and avg90 >= cfg.keepa_list_price_min_avg_ratio * listed:
        regular, label = listed, "List Price"
    asin, title = str(deal["asin"]).upper(), str(deal["item"])
    lines = [title, f"On sale for ${live:,.2f} at Amazon.",
             f"Regular price ({label} on Amazon): ${regular:,.2f}.",
             "Sold online at Amazon.com (price tracked by Keepa).",
             f"Product link: https://www.amazon.com/dp/{asin}"]
    if buybox:
        lines[1] = f"Buy Box total including shipping: ${live:,.2f} at Amazon."
    return RawPost(
        source_name="keepa", uid=f"reprice:{deal['dedupe_key']}",
        url=f"https://keepa.com/#!product/1-{asin}", title=title,
        text="\n".join(lines), published_at=now, fetched_at=now,
        hash_text="\n".join(lines) + f"\nReading: {stamp}",
        structured={"item": title, "store": "Amazon", "online_only": "true",
                    "product_url": f"https://www.amazon.com/dp/{asin}",
                    "final_price": live, "regular_price": regular,
                    "regular_price_basis": f"{label} on Amazon", "confidence": 1.0,
                    "evidence": {"final_price_quote": lines[1],
                                 "regular_price_quote": lines[2]}})


def _selection(price_type: int, delta_pct: tuple[int, int], date_range: int,
               sort_type: int, max_rank: int) -> dict[str, Any]:
    """One /deal query (page 0); pages differ only in ``page``, as Keepa
    requires for browsing a cached result."""
    return {
        "page": 0,
        "domainId": DOMAIN_US,
        "priceTypes": [price_type],
        "deltaPercentRange": list(delta_pct),
        "dateRange": date_range,
        "isRangeEnabled": True,
        "sortType": sort_type,
        "excludeCategories": list(EXCLUDED_CATEGORIES),
        "salesRankRange": [1, max_rank],
        "minRating": MIN_RATING,
    }


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise DeadlineExceeded("keepa: per-source deadline crossed")


class _TokensShort(Exception):
    """The token bucket cannot cover a query's first page (budget says so,
    or Keepa answered 429). Internal: the pull ends early with what it has."""


class _FeedConfirmations:
    """Confirm existing prices before mapping, caps or content-hash skips."""

    def __init__(self, db: DB | None, now: datetime):
        self.db, self.now = db, now
        self.confirmed: dict[str, set[str]] = {"new": set(), "used": set()}
        self.by_asin: dict[str, list[dict[str, Any]]] = {}
        for deal in db.all_deals(status="alive") if db is not None else []:
            asin = str(deal.get("asin") or "").strip().upper()
            if asin and is_amazon_deal(deal):
                self.by_asin.setdefault(asin, []).append(deal)

    def note(self, rows: list[dict[str, Any]], *, used: bool) -> None:
        from ..dedupe import _DEAL_FIELDS

        for row in rows:
            at = keepa_time(row.get("lastUpdate"))
            price = _price_at(row.get("current"),
                              PRICE_TYPE_WAREHOUSE if used else PRICE_TYPE_AMAZON)
            if at is None or price is None:
                continue
            at = min(at, self.now)
            asin = str(row.get("asin") or "").strip().upper()
            for deal in self.by_asin.get(asin, []):
                if is_used_deal(deal) != used or deal.get("final_price") != price:
                    continue  # another offer/condition is never a gone verdict
                since = util.parse_iso(deal.get("price_since") or "")
                if since is not None and at < since:
                    continue
                changed = False
                fields = ["keepa_price_seen_at"] + (["keepa_warehouse_seen_at"] if used else [])
                for name in fields:
                    previous = util.parse_iso(deal.get(name) or "")
                    if previous is None or at > previous:
                        deal[name] = util.iso(at)
                        changed = True
                if changed:
                    self.db.update_deal(deal["dedupe_key"], payload={
                        k: deal[k] for k in _DEAL_FIELDS if k in deal})
                    self.confirmed["used" if used else "new"].add(deal["dedupe_key"])


@dataclass
class _Pull:
    """One pull's shared paging state."""

    client: PoliteFetcher
    key: str
    budget: KeepaBudget
    db: DB | None
    now: datetime
    deadline: float | None
    reserve: float          # tokens extra pages never touch
    interval_s: float       # politeness gap between requests to Keepa
    margin_s: float         # a page may take this long: stop before the deadline
    sleep: Callable[[float], None]
    requests: int = 0       # every Keepa request (the politeness gap)
    pages: int = 0          # /deal pages
    raw_rows: int = 0
    sample: dict[str, Any] | None = None  # first raw row, for drift warnings
    category_names: dict[str, str] | None = None
    categories_tried: set[str] | None = None
    focus_tried: set[int] = field(default_factory=set)
    confirmations: _FeedConfirmations | None = None
    overflow_only: bool = False


@dataclass
class _Lane:
    """One lane's NEW-post allowance for a pull. A new post (content hash
    not yet in posts_seen) costs an LLM parse, a critic call and a Keepa
    check downstream; a post the pipeline already handled rides free. A new
    post past the cap is held back: Amazon and FRESH pages are re-read every
    pull, and a rotating window stays on the page it was held from, so it
    arrives on a later pull."""

    cap: int
    db: DB | None
    new: int = 0
    held: int = 0

    @property
    def full(self) -> bool:
        return self.new >= self.cap

    def admit(self, post: RawPost) -> bool:
        if self.db is not None and self.db.post_is_known(post.content_hash):
            return True
        if self.full:
            self.held += 1
            return False
        self.new += 1
        return True


class KeepaDealsAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http_client
        self._sleep = sleep_fn  # politeness gap between /deal pages
        self.feed_confirmed: dict[str, set[str]] = {"new": set(), "used": set()}
        from ..keepa_budget import TokenUsage
        self.token_usage = TokenUsage()
        self.recheck: Callable | None = None  # pipeline: due work before overflow discovery
        self.rate_limited = False

    def fetch_posts(
        self,
        source: SourceConfig,
        fetcher: Any,
        db: DB,
        cfg: AppConfig,
        now: datetime,
        deadline: float | None,
    ) -> list[RawPost]:
        self.feed_confirmed = {"new": set(), "used": set()}
        from ..keepa_budget import TokenUsage
        self.token_usage = TokenUsage()
        self.rate_limited = False
        key = get_secret("KEEPA_KEY")
        if not key:
            raise AdapterNotConfigured("keepa api key missing")

        # Self-throttle to one pull per cycle whatever the lane's cadence:
        # a pull inside the window is a clean no-op, not a failure.
        if db is not None:
            last = util.parse_iso(db.meta_get("keepa_last_fetch_at") or "")
            if last is not None and (now - last) < timedelta(
                    minutes=MIN_INTERVAL_MINUTES):
                log.info("keepa %s: last pull %.1f min ago (< %.0f); "
                         "skipping this lane tick", source.name,
                         (now - last).total_seconds() / 60.0,
                         MIN_INTERVAL_MINUTES)
                return []

        # The shared token budget (fed by every Keepa response, product
        # calls included) decides whether each page may spend. The Amazon
        # and FRESH first pages need only MIN_FOR_DEAL_QUERY (the supply
        # goes last); a pull cut short there, or by a 429, leaves
        # keepa_last_fetch_at alone so the next lane tick retries as soon
        # as the bucket refills. Every further page (DEEP's first included)
        # must leave cfg.keepa_reserve_tokens untouched.
        budget = KeepaBudget.load(db)
        confirmations = _FeedConfirmations(db, now)
        self.feed_confirmed = confirmations.confirmed
        amazon: list[RawPost] = []
        fresh: list[RawPost] = []
        deep: list[RawPost] = []
        sweep: list[RawPost] = []
        amazon_seen: set[str] = set()
        wh_seen: set[str] = set()  # one ASIN, one post across every shape
        amazon_lane = _Lane(cfg.keepa_amazon_max_new_posts_per_pull, db)
        wh_lane = _Lane(cfg.keepa_warehouse_max_new_posts_per_pull, db)
        wh_pages = cfg.keepa_warehouse_pages_per_pull
        floors = tuple(sorted({float(cfg.min_pct), float(cfg.used_min_pct),
                               float(cfg.site_show_from_pct)}))

        def map_amazon(row: dict[str, Any]) -> RawPost | None:
            return self._map_row(row, source, now, floors=floors,
                                 category_names=pull.category_names,
                                 list_ratio=cfg.keepa_list_price_min_avg_ratio)

        def map_warehouse(row: dict[str, Any]) -> RawPost | None:
            return self._map_warehouse_row(
                row, source, now,
                min_drop_pct=cfg.keepa_warehouse_min_drop_pct,
                min_new_price=cfg.keepa_warehouse_min_new_price,
                floors=floors, category_names=pull.category_names)

        amazon_sel = _selection(PRICE_TYPE_AMAZON, (MIN_DROP_PCT, 100),
                                DATE_RANGE_90D, SORT_PCT_DROP, AMAZON_MAX_RANK)
        # Warehouse (Amazon Resale) open-box/used, two shapes: FRESH =
        # newest deals of the last 24 h first (new arrivals sell out fast);
        # DEEP = the 90-day list sorted by absolute drop, read through a
        # page window that rotates across pulls (coverage, not repetition).
        fresh_sel = _selection(PRICE_TYPE_WAREHOUSE, WAREHOUSE_DELTA_PCT,
                               DATE_RANGE_DAY, SORT_NEWEST, WAREHOUSE_MAX_RANK)
        deep_sel = _selection(PRICE_TYPE_WAREHOUSE, WAREHOUSE_DELTA_PCT,
                              DATE_RANGE_90D, SORT_ABS_DROP, WAREHOUSE_MAX_RANK)
        complete = False
        with api_fetcher(fetcher, db, cfg, http_client=self._http, sleep_fn=self._sleep) as client:
            pull = _Pull(client=client, key=key, budget=budget, db=db,
                         now=now, deadline=deadline,
                         reserve=cfg.keepa_reserve_tokens,
                         interval_s=cfg.min_host_interval_s,
                         margin_s=cfg.request_timeout_s, sleep=self._sleep,
                         category_names=cached_meta(db, NAMES_META),
                         categories_tried=set(), confirmations=confirmations)
            try:
                _check_deadline(deadline)
                self._lookup_categories(pull, list(cached_meta(db, ROOTS_META).values()),
                                        all_roots=bool(cfg.keepa_focus_categories))
                self._run_focus(pull, cfg, amazon_sel, amazon_seen,
                                map_amazon, amazon, amazon_lane)
                # Pass 1, page 0: Amazon's own NEW price dropped
                # >= MIN_DROP_PCT below its 90-day average.
                _, amazon_next = self._run_pass(
                    pull, amazon_sel, 0, 1, source.name, amazon_seen,
                    map_amazon, amazon, amazon_lane)
                # A first /deal response can establish an unknown bucket.
                self._lookup_categories(pull, [], all_roots=bool(cfg.keepa_focus_categories))
                self._run_focus(pull, cfg, amazon_sel, amazon_seen,
                                map_amazon, amazon, amazon_lane)
                # Pass 2: warehouse, a third of the pages FRESH and the rest
                # (plus any FRESH left unused) DEEP. Only the Amazon and
                # FRESH first pages may run under the reserve.
                fresh_used, fresh_next = self._run_pass(
                    pull, fresh_sel, 0, max(1, wh_pages // 3),
                    source.name + "/warehouse-fresh", wh_seen,
                    map_warehouse, fresh, wh_lane)
                _, deep_next = self._run_pass(
                    pull, deep_sel, _cursor(db, DEEP_PAGE_META),
                    max(1, wh_pages - fresh_used),
                    source.name + "/warehouse-deep", wh_seen,
                    map_warehouse, deep, wh_lane,
                    first_is_extra=True, cursor_key=DEEP_PAGE_META)
                # Pass 1, further pages (all of them reserve-gated).
                if amazon_next is not None:
                    _, amazon_next = self._run_pass(
                        pull, amazon_sel, amazon_next,
                        cfg.keepa_amazon_pages_per_pull - 1, source.name,
                        amazon_seen, map_amazon, amazon, amazon_lane,
                        first_is_extra=True)
                # Last and lowest: the warehouse sweep, on idle tokens only.
                self._run_sweep(pull, cfg, wh_seen, map_warehouse, sweep,
                                wh_lane)
                complete = True
            except _TokensShort:
                pass  # logged where it happened; keep what was mapped
            finally:
                budget.save(db)
            if complete and self.recheck is not None:
                # Resume the exact windows/ASIN sets and NEW-post allowances;
                # no rereading page zero or bypassing the normal intake caps.
                passes = [
                    (amazon_sel, amazon_next, amazon_seen, map_amazon, amazon, amazon_lane, None),
                    (deep_sel, deep_next, wh_seen, map_warehouse, deep, wh_lane, DEEP_PAGE_META),
                    (fresh_sel, fresh_next, wh_seen, map_warehouse, fresh, wh_lane, None),
                ]

                def discover(shared_budget):
                    pull.budget = shared_budget
                    return self._run_overflow(pull, cfg, passes)

                self.recheck(discover, pull.deadline)
            elif self.rate_limited and self.recheck is not None:
                self.recheck(None, pull.deadline)  # no product calls after a feed's first 429

        # The rotating windows lead: if a run's parse stops early, the posts
        # left over should be ones re-read next pull (FRESH and Amazon pages
        # are), not DEEP or sweep pages the windows have moved past.
        posts = deep + sweep + fresh + amazon
        if db is not None and complete:
            db.meta_set("keepa_last_fetch_at", util.iso(now))
        log.info("keepa %s: %d /deal pages (~%.0f tokens), %d raw rows -> "
                 "mapped %d amazon-price + %d warehouse posts (%d deep, "
                 "%d sweep, %d fresh); new posts %d amazon + %d warehouse, "
                 "%d held for later pulls%s",
                 source.name, pull.pages, pull.pages * DEAL_PAGE_COST,
                 pull.raw_rows, len(amazon), len(deep) + len(sweep) + len(fresh),
                 len(deep), len(sweep), len(fresh), amazon_lane.new,
                 wh_lane.new, amazon_lane.held + wh_lane.held,
                 "" if complete else " (pull cut short: tokens; retry next tick)")
        if pull.raw_rows and not posts and not (amazon_lane.held
                                                or wh_lane.held):
            sample = pull.sample or {}
            log.warning(
                "keepa %s: raw deals present but none mapped — schema drift? "
                "sample keys=%s current=%r avg=%r",
                source.name, sorted(sample)[:20],
                sample.get("current"), sample.get("avg"))
        return posts

    def _run_overflow(self, pull: _Pull, cfg: AppConfig, passes: list) -> dict:
        """Bounded /deal continuations, after due work and before idle checks.

        One page per lane in rotation. Short lists, full post allowances,
        deadlines and reserves can leave tokens for the recheck fallback.
        """
        start_pages, start_tokens = pull.pages, self.token_usage.accounted
        start_estimated = self.token_usage.estimated
        lanes = {id(p[5]): p[5] for p in passes}
        start_new = sum(lane.new for lane in lanes.values())
        limited = False
        # Due checks may have repriced or killed a row since the first page.
        # Free confirmations must match that current row, never its old copy.
        pull.confirmations = _FeedConfirmations(pull.db, pull.now)
        pull.confirmations.confirmed = self.feed_confirmed
        pull.overflow_only = True
        gap = run_gap_forecast(pull.db, cfg, pull.now)["minutes"]
        try:
            while passes and pull.pages - start_pages < cfg.keepa_overflow_extra_pages_max:
                overflow = pull.budget.overflow(gap, pull.now)
                if overflow is None or overflow < DEAL_PAGE_COST:
                    break
                reserve = max(MIN_FOR_DEAL_QUERY, pull.reserve,
                              cfg.keepa_recheck_surplus_reserve if cfg.keepa_recheck_surplus else 0)
                if not pull.budget.allows(reserve + DEAL_PAGE_COST,
                                          pull.now):
                    break
                selection, page, seen, mapper, out, lane, cursor = passes.pop(0)
                if page is None or lane.full:
                    continue
                if not self._may_fetch(pull, "overflow", extra=True):
                    break
                fetched, nxt = self._run_pass(
                    pull, selection, page, 1, "overflow", seen, mapper, out, lane,
                    first_is_extra=True, cursor_key=cursor)
                if fetched and nxt is not None:
                    passes.append((selection, nxt, seen, mapper, out, lane, cursor))
        except _TokensShort:
            limited = True  # a first 429 stops discovery AND the remaining rechecks
        except (DeadlineExceeded, RuntimeError, httpx.HTTPError):
            log.warning("keepa overflow discovery unavailable; keeping earlier posts")
        finally:
            pull.overflow_only = False
        return {"pages": pull.pages - start_pages,
                "tokens": self.token_usage.accounted - start_tokens,
                "estimated_tokens": self.token_usage.estimated - start_estimated,
                "new_candidates": sum(lane.new for lane in lanes.values()) - start_new,
                "rate_limited": limited}

    def _lookup_categories(self, pull: _Pull, ids: list[str], *,
                           all_roots: bool = False) -> None:
        """Keepa /category costs 1 token for up to 10 IDs, or all roots (0).
        Cache names forever; defer on unknown/short tokens or schema drift.
        https://keepa.com/api-docs/category-lookup.html
        """
        names = pull.category_names
        tried = pull.categories_tried
        if names is None or tried is None or pull.overflow_only:
            return
        roots_loaded = pull.db is not None and pull.db.meta_get("keepa_root_categories_loaded")
        missing = sorted({str(i) for i in ids if str(i) not in names and str(i) not in tried})
        batches = [missing[i:i + 10] for i in range(0, len(missing), 10)]
        if all_roots and not roots_loaded and "0" not in tried:
            batches.insert(0, ["0"])
        for batch in batches:
            batch = [i for i in batch if i not in names and i not in tried]
            if not batch:
                continue
            avail = pull.budget.available(pull.now)
            if avail is None or avail - 1 < pull.reserve or not self._time_left(pull, "categories"):
                return
            _check_deadline(pull.deadline)
            tried.update(batch)
            try:
                body = self._get(pull, CATEGORY_URL, {
                    "key": pull.key, "domain": DOMAIN_US,
                    "category": ",".join(batch), "parents": 0}, "categories")
            except _TokensShort:
                raise
            except (RuntimeError, httpx.HTTPError):
                log.warning("keepa category lookup unavailable; using titles this pull")
                return
            categories = body.get("categories") if isinstance(body, dict) else None
            if not isinstance(categories, dict):
                log.warning("keepa category response has no category map; using titles")
                continue
            for cid, category in categories.items():
                name = category.get("name") if isinstance(category, dict) else None
                if str(cid).isdigit() and isinstance(name, str) and name.strip():
                    names[str(cid)] = name.strip()
            if batch != ["0"]:
                for cid in batch:
                    names.setdefault(cid, "")  # successful lookup with no name: no opinion
            if pull.db is not None:
                pull.db.meta_set(NAMES_META, json.dumps(names))
                if batch == ["0"] and categories:
                    pull.db.meta_set("keepa_root_categories_loaded", "1")

    def _run_focus(self, pull: _Pull, cfg: AppConfig, selection: dict,
                   seen: set[str], mapper, out: list[RawPost], lane: _Lane) -> None:
        """One Amazon page per named root, sharing the Amazon new-post cap.
        Focus pages lead so a full general page cannot crowd them out.
        """
        ids = {name.casefold(): int(cid) for cid, name in (pull.category_names or {}).items()}
        for name in cfg.keepa_focus_categories:
            cid = ids.get(name.strip().casefold())
            if cid is None or cid in pull.focus_tried or not self._may_fetch(pull, "focus", extra=True):
                continue
            # The focus page always keeps at least the standing 300-token reserve.
            avail = pull.budget.available(pull.now)
            if avail is None or avail - DEAL_PAGE_COST < max(300, pull.reserve):
                continue
            pull.focus_tried.add(cid)
            rows = self._query_rows(pull, {**selection, "includeCategories": [cid]}, "focus")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                asin = str(row.get("asin") or "").strip().upper()
                if not asin or asin in seen:
                    continue
                seen.add(asin)
                post = mapper(row)
                if post is not None and lane.admit(post):
                    out.append(post)

    def _run_pass(self, pull: _Pull, selection: dict[str, Any], start: int,
                  max_pages: int, label: str, seen: set[str],
                  mapper: Callable[[dict[str, Any]], RawPost | None],
                  out: list[RawPost], lane: _Lane, *,
                  first_is_extra: bool = False,
                  cursor_key: str | None = None) -> tuple[int, int | None]:
        """Page through one query from page ``start``, mapping rows whose
        ASIN is not yet in ``seen`` into ``out`` until ``lane`` is full.

        Returns (pages fetched, next page — None once a short page ended
        the list). The first page needs only MIN_FOR_DEAL_QUERY (else
        :class:`_TokensShort`, as on any 429); every further page (and the
        first, with ``first_is_extra``) also keeps the token reserve and
        the time margin, and is a clean stop when it can't. With
        ``cursor_key`` the next start page is saved in db meta after every
        page, wrapping to 0 at the end of the list — or staying on a page
        whose new posts the lane held back, so the next pull maps the rest.
        """
        fetched = 0
        page = start
        while fetched < max_pages and not lane.full:
            if not self._may_fetch(pull, label, extra=fetched > 0 or first_is_extra):
                break
            try:
                rows = self._query_rows(pull, {**selection, "page": page},
                                        f"{label} p{page}")
            except (RuntimeError, httpx.HTTPError) as e:
                if page == 0:
                    raise  # the query itself fails: a real source failure
                # A later page failing is no reason to lose the pull. A
                # refusal (in-body error, 4xx: a page past the end) ends
                # the list; a hiccup (transport, 5xx) retries next pull.
                refused = isinstance(e, RuntimeError) or (
                    isinstance(e, httpx.HTTPStatusError)
                    and e.response.status_code < 500)
                log.warning("keepa %s p%d: %s; %s", label, page, error_summary(e),
                            "treating it as the end of the list" if refused
                            else "paging stops, retried next pull")
                if not refused:
                    break
                rows = []
            fetched += 1
            held = lane.held
            for row in rows:
                if not isinstance(row, dict):
                    continue
                asin = str(row.get("asin") or "").strip().upper()
                if not asin or asin in seen:
                    continue  # a row seen on an earlier page/shape this pull
                seen.add(asin)
                post = mapper(row)
                if post is not None and lane.admit(post):
                    out.append(post)
            ended = len(rows) < DEAL_PAGE_SIZE
            if lane.held > held:
                nxt = page  # new posts held back: this page again next pull
            else:
                nxt = 0 if ended else page + 1
            if cursor_key and pull.db is not None:
                pull.db.meta_set(cursor_key, str(nxt))
            if ended:
                return fetched, None
            page = nxt
        return fetched, page

    @staticmethod
    def _time_left(pull: _Pull, label: str) -> bool:
        """False once the time budget is nearly spent (never before the
        pull's first request): a request may take margin_s."""
        if (pull.requests and pull.deadline is not None
                and time.monotonic() > pull.deadline - pull.margin_s):
            log.info("keepa %s: per-source time budget nearly spent; "
                     "paging stops", label)
            return False
        return True

    @classmethod
    def _may_fetch(cls, pull: _Pull, label: str, *, extra: bool) -> bool:
        """Clean stops: the time budget (after the pull's first request) and,
        for an extra page, the token reserve kept for product calls."""
        if not cls._time_left(pull, label):
            return False
        if extra:
            avail = pull.budget.available(pull.now)
            if avail is not None and avail - DEAL_PAGE_COST < pull.reserve:
                log.info("keepa %s: ~%.0f tokens left; paging stops to keep "
                         "the %.0f-token reserve for product calls", label,
                         avail, pull.reserve)
                return False
        return True

    def _get(self, pull: _Pull, url: str, params: dict[str, Any],
             label: str, *, estimate: float = 0) -> Any:
        """One Keepa GET, a polite second after the pull's previous one,
        its token fields fed to the budget (a 429 body carries them too).
        Raises :class:`_TokensShort` on a 429 (an empty bucket refills by
        the minute and costs nothing: a deferral, never a source failure
        for the breaker), RuntimeError on an in-body error, and httpx
        errors as they come."""
        pull.requests += 1
        # Keyed API (robots.txt is for crawlers); a 429 is an empty token
        # bucket, deferred to the next pull, so it is not retried here.
        # A lost answer may still have spent tokens. A bucket reading
        # replaces this debit; a spend receipt replaces the cost estimate.
        pull.budget.reserve(estimate, pull.now)
        payload, resp = None, None
        try:
            resp = pull.client.api_get(url, params=params, deadline=pull.deadline,
                                       max_attempts=1, respect_robots=False)
            payload = response_payload(resp)
        finally:
            # Only deal discovery: /deal, sweep finder and sweep products.
            # Category lookups and downstream rechecks have separate purposes.
            if url != CATEGORY_URL:
                self.token_usage.note(payload, estimate=estimate,
                                      rate_limited=resp is not None and resp.status_code == 429)
        pull.budget.note(payload, pull.now)
        if resp.status_code == 429:
            self.rate_limited = True
            log.warning("keepa %s: 429 Too Many Requests (%s); token bucket "
                        "empty, retrying next tick", label,
                        describe_429(payload))
            raise _TokensShort
        resp.raise_for_status()
        if isinstance(payload, dict) and payload.get("error"):
            # Keepa reports some failures in-body on a 200: surface them as a
            # source failure instead of silently "mapping 0 posts".
            raise RuntimeError("keepa api error")
        return payload

    def _query_rows(self, pull: _Pull, selection: dict[str, Any],
                    label: str) -> list:
        """One /deal page. Raises :class:`_TokensShort` when the budget
        says the bucket is too low to spend, or when Keepa answers 429."""
        budget, now = pull.budget, pull.now
        if not budget.allows(MIN_FOR_DEAL_QUERY, now):
            log.info("keepa %s: ~%.0f tokens left (< %.0f needed); skipping "
                     "the query until the bucket refills", label,
                     budget.available(now), MIN_FOR_DEAL_QUERY)
            raise _TokensShort
        pull.pages += 1
        payload = self._get(pull, DEALS_URL, {
            "key": pull.key, "selection": json.dumps(selection),
        }, label, estimate=DEAL_PAGE_COST)
        deals_obj = payload.get("deals") if isinstance(payload, dict) else None
        rows = (deals_obj or {}).get("dr") if isinstance(deals_obj, dict) else None
        rows = list(rows or [])
        pull.raw_rows += len(rows)
        if pull.sample is None:
            pull.sample = next((r for r in rows if isinstance(r, dict)), None)
        valid_rows = [r for r in rows if isinstance(r, dict)]
        if pull.confirmations is not None:
            pull.confirmations.note(valid_rows, used=selection.get("priceTypes") == [PRICE_TYPE_WAREHOUSE])
        remember_roots(pull.db, valid_rows)
        self._lookup_categories(pull, [root_id(r) for r in valid_rows if root_id(r)])
        tokens_left = (payload.get("tokensLeft")
                       if isinstance(payload, dict) else None)
        log.info("keepa %s: %d raw deals (tokensLeft=%s)",
                 label, len(rows), tokens_left)
        return rows

    def _sweep_affordable(self, pull: _Pull, cost: float, keep: float,
                          what: str) -> bool:
        """The sweep spends idle tokens only: the time budget must allow a
        request and the bucket must still hold ``keep`` after ``cost``. An
        unknown bucket is not idle."""
        if not self._time_left(pull, "sweep"):
            return False
        avail = pull.budget.available(pull.now)
        if avail is None or avail - cost < keep:
            log.info("keepa sweep: ~%s tokens; the %s (~%.0f) must leave "
                     "%.0f for rechecks and the deal pages; skipped",
                     "?" if avail is None else f"{avail:.0f}", what, cost,
                     keep)
            return False
        return True

    def _run_sweep(self, pull: _Pull, cfg: AppConfig, seen: set[str],
                   mapper: Callable[[dict[str, Any]], RawPost | None],
                   out: list[RawPost], lane: _Lane) -> None:
        """WAREHOUSE SWEEP, the pull's lowest-priority spend. A warehouse
        /deal row only exists when the warehouse price itself moved, so an
        open-box item far under the NEW price that never dropped against
        its own warehouse history is invisible there. Keepa's product
        finder lists products that currently HAVE a warehouse offer (new
        price >= the value floor, sales rank <= WAREHOUSE_MAX_RANK, best
        sellers first); one batched /product call (stats) checks the ASINs
        this pull has not seen through the same honest warehouse logic as
        a /deal row. Its page window rotates across pulls (db meta
        keepa_sweep_page, wrapping on a short page); a page whose check was
        deferred (429, time, tokens, a hiccup) or whose new posts the lane
        held back is read again next pull."""
        if not cfg.keepa_sweep_enabled:
            return
        if lane.full:
            log.info("keepa sweep: the warehouse new-post cap is full; "
                     "skipped this pull")
            return
        per_page = max(SWEEP_MIN_PER_PAGE,
                       min(SWEEP_MAX_PER_PAGE, cfg.keepa_sweep_asins_per_pull))
        keep = pull.reserve + cfg.keepa_sweep_reserve_tokens
        if not self._sweep_affordable(pull, _finder_cost(per_page) + per_page,
                                      keep, "finder + product check"):
            return
        page = _cursor(pull.db, SWEEP_PAGE_META)
        selection = {
            "current_WAREHOUSE_gte": 1,      # has a warehouse offer now
            # The honest basis never exceeds the lowest new offer, so this
            # is a safe prefilter for keepa.warehouse_min_new_price.
            "current_NEW_gte": int(round(cfg.keepa_warehouse_min_new_price * 100)),
            "current_SALES_gte": 1,
            "current_SALES_lte": WAREHOUSE_MAX_RANK,
            "current_RATING_gte": MIN_RATING,
            "categories_exclude": list(EXCLUDED_CATEGORIES),
            "sort": [["current_SALES", "asc"]],
            "perPage": per_page,
            "page": page,
        }
        label = f"sweep p{page}"
        try:
            found = self._get(pull, QUERY_URL, {
                "key": pull.key, "domain": DOMAIN_US,
                "selection": json.dumps(selection)}, label, estimate=_finder_cost(per_page))
        except _TokensShort:
            return  # the same page next pull
        except (RuntimeError, httpx.HTTPError) as e:
            # A refused later page (in-body error, 4xx) is past the end of
            # a shrunken list: wrap. A hiccup retries this page next pull.
            refused = isinstance(e, RuntimeError) or (
                isinstance(e, httpx.HTTPStatusError)
                and e.response.status_code < 500)
            log.warning("keepa %s: %s; %s", label, error_summary(e),
                        "wrapping to page 0" if refused and page
                        else "retried next pull")
            if refused and page and pull.db is not None:
                pull.db.meta_set(SWEEP_PAGE_META, "0")
            return
        listed = (found.get("asinList") if isinstance(found, dict) else None) or []
        ended = len(listed) < per_page
        asins = [a for a in dict.fromkeys(
            str(x or "").strip().upper() for x in listed)
            if a and a not in seen][:SWEEP_MAX_PER_PAGE]
        tokens_left = found.get("tokensLeft") if isinstance(found, dict) else None
        mapped = 0
        held = lane.held
        if asins:
            # Re-checked against the reading the finder just returned.
            if not self._sweep_affordable(pull, float(len(asins)), keep,
                                          "product check"):
                return
            try:
                body = self._get(pull, PRODUCT_URL, {
                    "key": pull.key, "domain": DOMAIN_US,
                    "asin": ",".join(asins), "stats": 1, "history": 0},
                    label + " products", estimate=len(asins))
            except _TokensShort:
                return
            except (RuntimeError, httpx.HTTPError) as e:
                log.warning("keepa %s: product check failed (%s); retried "
                            "next pull", label, error_summary(e))
                return
            tokens_left = (body.get("tokensLeft") if isinstance(body, dict)
                           else None)
            wanted = set(asins)
            products = (body.get("products") if isinstance(body, dict)
                        else None) or []
            valid_products = [p for p in products if isinstance(p, dict)]
            from .. import accuracy
            accuracy.keepa(pull.db, cfg, {
                str(p.get("asin") or "").strip().upper(): p for p in valid_products
                if str(p.get("asin") or "").strip().upper() in wanted}, util.now_utc())
            if pull.confirmations is not None:
                pull.confirmations.note([_product_row(p) for p in valid_products
                                         if str(p.get("asin") or "").strip().upper() in wanted],
                                        used=True)
            remember_roots(pull.db, valid_products)
            self._lookup_categories(pull, [root_id(p) for p in valid_products if root_id(p)])
            for product in products:
                if not isinstance(product, dict):
                    continue
                asin = str(product.get("asin") or "").strip().upper()
                if asin not in wanted or asin in seen:
                    continue
                seen.add(asin)
                post = mapper(_product_row(product))
                if post is not None and lane.admit(post):
                    out.append(post)
                    mapped += 1
        nxt = page if lane.held > held else (0 if ended else page + 1)
        if pull.db is not None:
            pull.db.meta_set(SWEEP_PAGE_META, str(nxt))
        log.info("keepa sweep: page %d, %d asins -> %d mapped (tokensLeft=%s)",
                 page, len(asins), mapped, tokens_left)

    @staticmethod
    def _map_row(row: dict[str, Any], source: SourceConfig, now: datetime,
                 *, floors: tuple[float, ...] = (),
                 category_names: dict[str, str] | None = None,
                 list_ratio: float = 0.9) -> RawPost | None:
        asin = str(row.get("asin") or "").strip().upper()
        title = str(row.get("title") or "").strip()
        if not asin or not title:
            return None
        avg_raw = row.get("avg") or row.get("avg90") or row.get("avg30")
        current = _price_at(row.get("current"), PRICE_TYPE_AMAZON)
        basis = _amazon_basis(row, avg_raw, list_ratio)
        if current is None or basis is None or current >= basis[0]:
            return None
        if not _dense_history(avg_raw, PRICE_TYPE_AMAZON):
            return None  # sparse history: the "average" is an artifact
        avg, label = basis

        # Synthesized source text: the quotes the evidence check will verify.
        # The regular basis is the reference Amazon shows (a List Price the
        # item recently sold at, else the Typical-price stand-in), never a
        # bare claimed MSRP, and it is LABELED as such. The wording deliberately matches
        # the extraction vocabulary ("On sale for" -> final, "Regular price"
        # -> regular): the first live runs showed the model correctly
        # refusing to treat an unlabeled "recent average" as a regular
        # price, which stored every Keepa deal priceless.
        lines = [
            title,
            f"On sale for ${current:,.2f} at Amazon.",
            f"Regular price ({label} on Amazon): ${avg:,.2f}.",
            "Sold online at Amazon.com (price tracked by Keepa).",
            f"Product link: https://www.amazon.com/dp/{asin}",
        ]
        # Keepa's averages are time-weighted and move by cents on every
        # update: hashing them verbatim re-parsed every unchanged deal on
        # every pull (live: one ASIN 18 times in a day). The hash keeps the
        # price and the discount bucket (_pct_key: the whole percent and
        # the publish floors it clears), so only a real move parses.
        pct = _pct_key(100.0 * (1.0 - current / avg), floors)
        return RawPost(
            source_name=source.name,
            uid=f"{asin}:{current:.2f}",  # a further drop is a new post
            url=f"https://keepa.com/#!product/1-{asin}",  # credit link: Keepa's page
            title=title,
            text="\n".join(lines),
            published_at=None,  # first_seen governs freshness
            fetched_at=now,
            image_url=_image_url(row),
            hash_text=f"{title}\n${current:,.2f}, {pct} under the {label}",
            structured={
                **_structured_category(row, category_names),
                "keepa_price_seen_at": _feed_time(row, now),
                "item": title, "asin": asin, "store": "Amazon",
                "product_url": f"https://www.amazon.com/dp/{asin}",
                "online_only": "true", "final_price": round(current, 2),
                "regular_price": round(avg, 2),
                "regular_price_basis": f"{label} on Amazon",
                "conditions": [], "confidence": 1.0,
                "evidence": {"final_price_quote": lines[1],
                             "regular_price_quote": lines[2]},
            },
        )

    @staticmethod
    def _map_warehouse_row(row: dict[str, Any], source: SourceConfig,
                           now: datetime, *, min_drop_pct: float = 45.0,
                           min_new_price: float = 25.0,
                           floors: tuple[float, ...] = (),
                           category_names: dict[str, str] | None = None) -> RawPost | None:
        """Amazon Warehouse (Amazon Resale) open-box/used offer, compared
        honestly against what the item really costs NEW (see
        :func:`_new_price_basis`) — the "$12.07 used vs $81.55 new = -85%"
        framing Amazon itself shows. No honest new basis => skipped."""
        asin = str(row.get("asin") or "").strip().upper()
        title = str(row.get("title") or "").strip()
        if not asin or not title:
            return None
        used = _price_at(row.get("current"), PRICE_TYPE_WAREHOUSE)
        basis = _new_price_basis(row)
        if used is None or used <= 0 or basis is None:
            return None
        new_p, label = basis
        if new_p < min_new_price:
            return None  # low-value item: not a deal anyone subscribes for
        if used >= new_p * (1.0 - min_drop_pct / 100.0):
            return None  # used supply is plentiful: only the deep cuts

        lines = [
            title,
            f"Amazon Warehouse (Amazon Resale) open-box/used deal: "
            f"yours for ${used:,.2f}.",
            f"New condition price: ${new_p:,.2f}.",
            f"New price basis: {label}.",
            "Condition: used/open-box, sold online by Amazon Resale with "
            "free returns. Warehouse stock is usually one unit and sells "
            "out fast.",
            f"Product link: https://www.amazon.com/dp/{asin}",
        ]
        # Same hash rule as the Amazon-price pass: a 90-day-average basis
        # drifts by cents, so only the discount bucket is hashed.
        pct = _pct_key(100.0 * (1.0 - used / new_p), floors)
        return RawPost(
            source_name=source.name,
            uid=f"{asin}:wh:{used:.2f}",  # a further drop is a new post
            url=f"https://keepa.com/#!product/1-{asin}",
            title=title,
            text="\n".join(lines),
            published_at=None,
            fetched_at=now,
            image_url=_image_url(row),
            hash_text=f"{title}\n${used:,.2f} used, {pct} under the {label}",
            structured={
                **_structured_category(row, category_names),
                "keepa_price_seen_at": _feed_time(row, now),
                "keepa_warehouse_seen_at": _feed_time(row, now),
                "item": title, "asin": asin, "store": "Amazon",
                "product_url": f"https://www.amazon.com/dp/{asin}",
                "online_only": "true", "final_price": round(used, 2),
                "regular_price": round(new_p, 2), "regular_price_basis": label,
                "conditions": ["used", "open_box"], "confidence": 1.0,
                "evidence": {"final_price_quote": lines[1],
                             "regular_price_quote": lines[2]},
            },
        )
