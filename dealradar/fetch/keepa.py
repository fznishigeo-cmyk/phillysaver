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
Pages beyond each query's first run only while the shared token budget keeps
a reserve for the product calls (config.yaml ``keepa:``).

Requires the ``KEEPA_KEY`` secret (reported as ``needs_key`` until set).
Cost model: Keepa bills a flat subscription with a token-per-minute allowance;
running out of tokens delays data, never adds charges. Prices arrive in cents;
-1 means "no data".
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx

from .. import util
from ..config import AppConfig, get_secret
from ..db import DB
from ..errors import AdapterNotConfigured, DeadlineExceeded
from ..keepa_budget import (MIN_FOR_DEAL_QUERY, KeepaBudget, describe_429,
                            response_payload)
from ..models import RawPost, SourceConfig
from .base import SourceAdapter

log = logging.getLogger("dealradar.fetch.keepa")

DEALS_URL = "https://api.keepa.com/deal"

#: Keepa domainId for amazon.com (US).
DOMAIN_US = 1

#: Ask Keepa for drops at/above this percent-off-average. Slightly below our
#: publish floor (publish.min_pct, 40) so near-misses still enter the DB
#: and price history.
MIN_DROP_PCT = 35

#: Warehouse queries ask loosely: Keepa's server-side delta is vs the
#: warehouse price's OWN average, so the honest gate (used price vs the new
#: price, config keepa.warehouse_min_drop_pct) runs client-side.
WAREHOUSE_DELTA_PCT = (30, 100)

#: Popularity nets (Amazon sales rank). The new-price lane stays strict:
#: obscure SKUs have sparse histories whose "averages" fabricate drops. The
#: warehouse lane compares against Amazon's live price or a dense 90-day new
#: average, so it fishes a much wider net (mispriced open-box hides on
#: mid-rank items).
AMAZON_MAX_RANK = 30000
WAREHOUSE_MAX_RANK = 200000

#: Keepa price-type indexes: 0 = Amazon's own NEW price, 1 = lowest NEW
#: offer, 9 = Amazon Warehouse / Amazon Resale (open-box & used, sold by
#: Amazon). Third-party used sellers are deliberately NOT tracked.
PRICE_TYPE_AMAZON = 0
PRICE_TYPE_NEW = 1
PRICE_TYPE_WAREHOUSE = 9

#: Keepa dateRange: 0 = the last 24 hours; 3 = drop measured against the
#: 90-day average — the honest "typical price" basis (day/week windows miss
#: slow-boiled clearance). 3 is also the 90-day index of a row's ``avg``.
DATE_RANGE_DAY = 0
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


def _new_price_basis(row: dict[str, Any]) -> tuple[float, str] | None:
    """What a shopper really pays NEW for a warehouse row, and its label.

    Amazon's own current price when Amazon sells the item; otherwise the
    LOWER of the lowest current new offer and the 90-day average new price.
    A lone third-party new offer with no dense 90-day history is no honest
    basis (live wart: a $58.95 marketplace "new" price made a $5.70 open-box
    phone case look 90% off; the critic flagged it suspect_msrp) -> None.
    """
    current = row.get("current")
    amazon = _price_at(current, PRICE_TYPE_AMAZON)
    if amazon is not None and amazon > 0:
        return amazon, "Amazon's own new price"
    offer = _price_at(current, PRICE_TYPE_NEW)
    if offer is None or offer <= 0:
        return None  # nothing sells new right now
    avg_raw = row.get("avg") or row.get("avg90")
    avg90 = (_avg90_price(avg_raw, PRICE_TYPE_NEW)
             if _dense_history(avg_raw, PRICE_TYPE_NEW) else None)
    if avg90 is None or avg90 <= 0:
        return None
    if avg90 < offer:
        return avg90, "lowest new price (90-day average)"
    return offer, "lowest new price (current offer)"


def _deep_start(db: DB | None) -> int:
    """Start page of the rotating DEEP warehouse window (0 when unset)."""
    raw = db.meta_get(DEEP_PAGE_META) if db is not None else None
    try:
        return max(0, int(raw or 0))
    except ValueError:
        return 0


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
        # Books & Kindle: algorithmic publisher pricing ($995 print-on-
        # demand reports "dropping" 60%) dominates the drop list and is
        # noise, never a deal a person wants.
        "excludeCategories": [283155, 133140011],
        # Only products people actually buy: obscure OEM parts and
        # zero-review SKUs produce sparse price histories whose
        # "averages" fabricate 95%+ drops. Rank is Amazon sales rank;
        # rating is in tenths of stars (20 = 2.0 stars).
        "salesRankRange": [1, max_rank],
        "minRating": 20,
    }


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise DeadlineExceeded("keepa: per-source deadline crossed")


class _TokensShort(Exception):
    """The token bucket cannot cover a query's first page (budget says so,
    or Keepa answered 429). Internal: the pull ends early with what it has."""


@dataclass
class _Pull:
    """One pull's shared paging state."""

    client: httpx.Client
    key: str
    budget: KeepaBudget
    db: DB | None
    now: datetime
    deadline: float | None
    reserve: float          # tokens extra pages never touch
    interval_s: float       # politeness gap between requests to Keepa
    margin_s: float         # a page may take this long: stop before the deadline
    sleep: Callable[[float], None]
    requests: int = 0
    raw_rows: int = 0
    sample: dict[str, Any] | None = None  # first raw row, for drift warnings


class KeepaDealsAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http_client
        self._sleep = sleep_fn  # politeness gap between /deal pages

    @contextmanager
    def _client(self, cfg: AppConfig) -> Iterator[httpx.Client]:
        if self._http is not None:
            yield self._http
            return
        client = httpx.Client(
            headers={"User-Agent": cfg.resolved_user_agent()},
            timeout=cfg.request_timeout_s,
            follow_redirects=True,
        )
        try:
            yield client
        finally:
            client.close()

    def fetch_posts(
        self,
        source: SourceConfig,
        fetcher: Any,
        db: DB,
        cfg: AppConfig,
        now: datetime,
        deadline: float | None,
    ) -> list[RawPost]:
        # fetcher unused: this adapter manages its own authenticated client.
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
        # calls included) decides whether each page may spend. A query's
        # first page needs only MIN_FOR_DEAL_QUERY (the supply goes last);
        # a pull cut short there, or by a 429, leaves keepa_last_fetch_at
        # alone so the next lane tick retries as soon as the bucket refills.
        # Every further page must leave cfg.keepa_reserve_tokens untouched.
        budget = KeepaBudget.load(db)
        amazon: list[RawPost] = []
        warehouse: list[RawPost] = []
        amazon_seen: set[str] = set()
        wh_seen: set[str] = set()  # one ASIN, one post across both shapes
        amazon_cap = cfg.keepa_amazon_max_posts_per_pull
        wh_cap = cfg.keepa_warehouse_max_posts_per_pull
        wh_pages = cfg.keepa_warehouse_pages_per_pull

        def map_amazon(row: dict[str, Any]) -> RawPost | None:
            return self._map_row(row, source, now)

        def map_warehouse(row: dict[str, Any]) -> RawPost | None:
            return self._map_warehouse_row(
                row, source, now,
                min_drop_pct=cfg.keepa_warehouse_min_drop_pct,
                min_new_price=cfg.keepa_warehouse_min_new_price)

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
        with self._client(cfg) as client:
            pull = _Pull(client=client, key=key, budget=budget, db=db,
                         now=now, deadline=deadline,
                         reserve=cfg.keepa_reserve_tokens,
                         interval_s=cfg.min_host_interval_s,
                         margin_s=cfg.request_timeout_s, sleep=self._sleep)
            try:
                _check_deadline(deadline)
                # Pass 1, page 0: Amazon's own NEW price dropped
                # >= MIN_DROP_PCT below its 90-day average.
                _, amazon_next = self._run_pass(
                    pull, amazon_sel, 0, 1, source.name, amazon_seen,
                    map_amazon, amazon, amazon_cap)
                # Pass 2: warehouse, a third of the pages FRESH and the rest
                # (plus any FRESH left unused) DEEP.
                fresh_used, _ = self._run_pass(
                    pull, fresh_sel, 0, max(1, wh_pages // 3),
                    source.name + "/warehouse-fresh", wh_seen,
                    map_warehouse, warehouse, wh_cap)
                self._run_pass(
                    pull, deep_sel, _deep_start(db),
                    max(1, wh_pages - fresh_used),
                    source.name + "/warehouse-deep", wh_seen,
                    map_warehouse, warehouse, wh_cap,
                    cursor_key=DEEP_PAGE_META)
                # Pass 1, further pages (all of them reserve-gated).
                if amazon_next is not None:
                    self._run_pass(
                        pull, amazon_sel, amazon_next,
                        cfg.keepa_amazon_pages_per_pull - 1, source.name,
                        amazon_seen, map_amazon, amazon, amazon_cap,
                        first_is_extra=True)
                complete = True
            except _TokensShort:
                pass  # logged where it happened; keep what was mapped
            finally:
                budget.save(db)

        # Warehouse first: if a heavy run's parse stops early, the posts
        # left over should be the ones re-read next pull (Amazon pages are),
        # not a DEEP page the window will not revisit until it wraps.
        posts = warehouse + amazon
        if db is not None and complete:
            db.meta_set("keepa_last_fetch_at", util.iso(now))
        log.info("keepa %s: %d /deal pages (~%.0f tokens), %d raw rows -> "
                 "mapped %d amazon-price + %d warehouse posts%s",
                 source.name, pull.requests, pull.requests * DEAL_PAGE_COST,
                 pull.raw_rows, len(amazon), len(warehouse),
                 "" if complete else " (pull cut short: tokens; retry next tick)")
        if pull.raw_rows and not posts:
            sample = pull.sample or {}
            log.warning(
                "keepa %s: raw deals present but none mapped — schema drift? "
                "sample keys=%s current=%r avg=%r",
                source.name, sorted(sample)[:20],
                sample.get("current"), sample.get("avg"))
        return posts

    def _run_pass(self, pull: _Pull, selection: dict[str, Any], start: int,
                  max_pages: int, label: str, seen: set[str],
                  mapper: Callable[[dict[str, Any]], RawPost | None],
                  out: list[RawPost], cap: int, *,
                  first_is_extra: bool = False,
                  cursor_key: str | None = None) -> tuple[int, int | None]:
        """Page through one query from page ``start``, mapping rows whose
        ASIN is not yet in ``seen`` into ``out`` until ``cap`` posts.

        Returns (pages fetched, next page — None once a short page ended
        the list). The first page needs only MIN_FOR_DEAL_QUERY (else
        :class:`_TokensShort`, as on any 429); every further page (and the
        first, with ``first_is_extra``) also keeps the token reserve and
        the time margin, and is a clean stop when it can't. With
        ``cursor_key`` the next start page is saved in db meta after every
        page, wrapping to 0 at the end of the list.
        """
        fetched = 0
        page = start
        while fetched < max_pages and len(out) < cap:
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
                log.warning("keepa %s p%d: %s; %s", label, page, e,
                            "treating it as the end of the list" if refused
                            else "paging stops, retried next pull")
                if not refused:
                    break
                rows = []
            fetched += 1
            for row in rows:
                if len(out) >= cap:
                    break
                if not isinstance(row, dict):
                    continue
                asin = str(row.get("asin") or "").strip().upper()
                if not asin or asin in seen:
                    continue  # a row seen on an earlier page/shape this pull
                seen.add(asin)
                post = mapper(row)
                if post is not None:
                    out.append(post)
            ended = len(rows) < DEAL_PAGE_SIZE
            page = 0 if ended else page + 1
            if cursor_key and pull.db is not None:
                pull.db.meta_set(cursor_key, str(page))
            if ended:
                return fetched, None
        return fetched, page

    @staticmethod
    def _may_fetch(pull: _Pull, label: str, *, extra: bool) -> bool:
        """Clean stops: the time budget (after the pull's first request) and,
        for an extra page, the token reserve kept for product calls."""
        if (pull.requests and pull.deadline is not None
                and time.monotonic() > pull.deadline - pull.margin_s):
            log.info("keepa %s: per-source time budget nearly spent; "
                     "paging stops", label)
            return False
        if extra:
            avail = pull.budget.available(pull.now)
            if avail is not None and avail - DEAL_PAGE_COST < pull.reserve:
                log.info("keepa %s: ~%.0f tokens left; paging stops to keep "
                         "the %.0f-token reserve for product calls", label,
                         avail, pull.reserve)
                return False
        return True

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
        if pull.requests:
            pull.sleep(pull.interval_s)  # <= 1 request/second/host
        pull.requests += 1
        resp = pull.client.get(DEALS_URL, params={
            "key": pull.key, "selection": json.dumps(selection),
        })
        payload = response_payload(resp)
        budget.note(payload, now)  # a 429 body carries the token fields too
        if resp.status_code == 429:
            # Empty token bucket: it refills by the minute and costs nothing,
            # so this is a deferral, never a source failure for the breaker.
            log.warning("keepa %s: 429 Too Many Requests (%s); token bucket "
                        "empty, retrying next tick", label,
                        describe_429(payload))
            raise _TokensShort
        resp.raise_for_status()
        if isinstance(payload, dict) and payload.get("error"):
            # Keepa reports some failures in-body on a 200: surface them as a
            # source failure instead of silently "mapping 0 posts".
            raise RuntimeError(f"keepa api error: {payload['error']}")
        deals_obj = payload.get("deals") if isinstance(payload, dict) else None
        rows = (deals_obj or {}).get("dr") if isinstance(deals_obj, dict) else None
        rows = list(rows or [])
        pull.raw_rows += len(rows)
        if pull.sample is None:
            pull.sample = next((r for r in rows if isinstance(r, dict)), None)
        tokens_left = (payload.get("tokensLeft")
                       if isinstance(payload, dict) else None)
        log.info("keepa %s: %d raw deals (tokensLeft=%s)",
                 label, len(rows), tokens_left)
        return rows

    @staticmethod
    def _map_row(row: dict[str, Any], source: SourceConfig,
                 now: datetime) -> RawPost | None:
        asin = str(row.get("asin") or "").strip().upper()
        title = str(row.get("title") or "").strip()
        if not asin or not title:
            return None
        avg_raw = row.get("avg") or row.get("avg90") or row.get("avg30")
        current = _price_at(row.get("current"), PRICE_TYPE_AMAZON)
        avg = _avg_price(avg_raw, PRICE_TYPE_AMAZON)
        if current is None or avg is None or avg <= 0 or current >= avg:
            return None
        if not _dense_history(avg_raw, PRICE_TYPE_AMAZON):
            return None  # sparse history: the "average" is an artifact

        # Synthesized source text: the quotes the evidence check will verify.
        # The 90-day average is the honest "regular" basis — never a claimed
        # MSRP — and it is LABELED as such. The wording deliberately matches
        # the extraction vocabulary ("On sale for" -> final, "Regular price"
        # -> regular): the first live runs showed the model correctly
        # refusing to treat an unlabeled "recent average" as a regular
        # price, which stored every Keepa deal priceless.
        lines = [
            title,
            f"On sale for ${current:,.2f} at Amazon.",
            f"Regular price (90-day average on Amazon): ${avg:,.2f}.",
            "Sold online at Amazon.com (price tracked by Keepa).",
            f"Product link: https://www.amazon.com/dp/{asin}",
        ]
        # Keepa's averages are time-weighted and move by cents on every
        # update: hashing them verbatim re-parsed every unchanged deal on
        # every pull (live: one ASIN 18 times in a day). The hash keeps the
        # price and the WHOLE-percent discount, so only a real move parses.
        pct = round(100.0 * (1.0 - current / avg))
        return RawPost(
            source_name=source.name,
            uid=f"{asin}:{current:.2f}",  # a further drop is a new post
            url=f"https://keepa.com/#!product/1-{asin}",  # credit link: Keepa's page
            title=title,
            text="\n".join(lines),
            published_at=None,  # first_seen governs freshness
            fetched_at=now,
            image_url=_image_url(row),
            hash_text=f"{title}\n${current:,.2f}, {pct}% under the 90-day average",
        )

    @staticmethod
    def _map_warehouse_row(row: dict[str, Any], source: SourceConfig,
                           now: datetime, *, min_drop_pct: float = 45.0,
                           min_new_price: float = 25.0) -> RawPost | None:
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
        # drifts by cents, so only the whole-percent discount is hashed.
        pct = round(100.0 * (1.0 - used / new_p))
        return RawPost(
            source_name=source.name,
            uid=f"{asin}:wh:{used:.2f}",  # a further drop is a new post
            url=f"https://keepa.com/#!product/1-{asin}",
            title=title,
            text="\n".join(lines),
            published_at=None,
            fetched_at=now,
            image_url=_image_url(row),
            hash_text=f"{title}\n${used:,.2f} used, {pct}% under the {label}",
        )
