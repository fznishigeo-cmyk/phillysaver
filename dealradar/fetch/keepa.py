"""Keepa source adapter: Amazon price-drop discovery via the official Keepa API.

Keepa (https://keepa.com) runs a legal, licensed Amazon price database. This
adapter queries its *deals* endpoint for products whose current price recently
dropped far below their average — the heavy-hitter radar — and synthesizes a
plain-text post per product so the normal parse/evidence/savings pipeline
applies unchanged (every published number still has a verbatim quote, here
against Keepa's own figures, with the average price as the honest basis).

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
from datetime import datetime
from typing import Any

import httpx

from ..config import AppConfig, get_secret
from ..db import DB
from ..errors import AdapterNotConfigured, DeadlineExceeded
from ..models import RawPost, SourceConfig
from .base import SourceAdapter

log = logging.getLogger("dealradar.fetch.keepa")

DEALS_URL = "https://api.keepa.com/deal"

#: Keepa domainId for amazon.com (US).
DOMAIN_US = 1

#: Cap on products mapped per run: bounds both Keepa tokens and LLM spend.
MAX_DEALS_PER_RUN = 20

#: Ask Keepa for drops at/above this percent-off-average. Slightly below our
#: publish floor (publish.min_pct, 60) so near-misses still enter the DB
#: and price history.
MIN_DROP_PCT = 55

#: Warehouse/used offers are plentiful, so they carry a HIGHER bar: ingest
#: only used prices this far below the LIVE new price (publish floor for
#: used deals is higher still — config publish.used_min_pct, 70).
WAREHOUSE_MIN_DROP_PCT = 65

#: Keepa price-type indexes: 0 = Amazon's own NEW price, 1 = lowest NEW
#: offer, 9 = Amazon Warehouse / Amazon Resale (open-box & used, sold by
#: Amazon). Third-party used sellers are deliberately NOT tracked.
PRICE_TYPE_AMAZON = 0
PRICE_TYPE_NEW = 1
PRICE_TYPE_WAREHOUSE = 9

#: Keepa dateRange 3 = drop measured against the 90-day average — the honest
#: "typical price" basis (day/week windows miss slow-boiled clearance).
DATE_RANGE_90D = 3


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


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise DeadlineExceeded("keepa: per-source deadline crossed")


class KeepaDealsAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http_client
        self._sleep = sleep_fn  # interface symmetry; one call per run

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
        # fetcher/db unused: this adapter manages its own authenticated client.
        key = get_secret("KEEPA_KEY")
        if not key:
            raise AdapterNotConfigured("keepa api key missing")

        with self._client(cfg) as client:
            # Pass 1: Amazon's own NEW price dropped >= MIN_DROP_PCT below
            # its 90-day average.
            _check_deadline(deadline)
            rows = self._query_rows(client, key, PRICE_TYPE_AMAZON,
                                    [MIN_DROP_PCT, 100], source.name)
            posts: list[RawPost] = []
            for row in rows[:MAX_DEALS_PER_RUN]:
                if not isinstance(row, dict):
                    continue
                post = self._map_row(row, source, now)
                if post is not None:
                    posts.append(post)
            n_amazon = len(posts)

            # Pass 2: Amazon Warehouse (Amazon Resale) open-box/used offers.
            # Keepa's server-side delta is vs the warehouse's OWN average, so
            # ask loosely and apply the honest gate client-side: the used
            # price must clear MIN_DROP_PCT against the LIVE new price.
            _check_deadline(deadline)
            wh_rows = self._query_rows(client, key, PRICE_TYPE_WAREHOUSE,
                                       [30, 100], source.name + "/warehouse")
            for row in wh_rows[:MAX_DEALS_PER_RUN]:
                if not isinstance(row, dict):
                    continue
                post = self._map_warehouse_row(row, source, now)
                if post is not None:
                    posts.append(post)

        log.info("keepa %s: mapped %d amazon-price + %d warehouse posts",
                 source.name, n_amazon, len(posts) - n_amazon)
        if (rows or wh_rows) and not posts:
            sample = next((r for r in (rows + wh_rows) if isinstance(r, dict)),
                          {})
            log.warning(
                "keepa %s: raw deals present but none mapped — schema drift? "
                "sample keys=%s current=%r avg=%r",
                source.name, sorted(sample)[:20],
                sample.get("current"), sample.get("avg"))
        return posts

    def _query_rows(self, client: httpx.Client, key: str, price_type: int,
                    delta_pct_range: list[int], label: str) -> list:
        selection = {
            "page": 0,
            "domainId": DOMAIN_US,
            "priceTypes": [price_type],
            "deltaPercentRange": delta_pct_range,
            "dateRange": DATE_RANGE_90D,
            "isRangeEnabled": True,
            "sortType": 4,      # biggest percent drop first (per Keepa docs)
            # Books & Kindle: algorithmic publisher pricing ($995 print-on-
            # demand reports "dropping" 60%) dominates the drop list and is
            # noise, never a deal a person wants.
            "excludeCategories": [283155, 133140011],
            # Only products people actually buy: obscure OEM parts and
            # zero-review SKUs produce sparse price histories whose
            # "averages" fabricate 95%+ drops. Rank is Amazon sales rank;
            # rating is in tenths of stars (20 = 2.0 stars).
            "salesRankRange": [1, 30000],
            "minRating": 20,
        }
        resp = client.get(DEALS_URL, params={
            "key": key, "selection": json.dumps(selection),
        })
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("error"):
            # Keepa reports some failures in-body on a 200: surface them as a
            # source failure instead of silently "mapping 0 posts".
            raise RuntimeError(f"keepa api error: {payload['error']}")
        deals_obj = payload.get("deals") if isinstance(payload, dict) else None
        rows = (deals_obj or {}).get("dr") if isinstance(deals_obj, dict) else None
        tokens_left = (payload.get("tokensLeft")
                       if isinstance(payload, dict) else None)
        log.info("keepa %s: %d raw deals (tokensLeft=%s)",
                 label, len(rows or []), tokens_left)
        return list(rows or [])

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
        return RawPost(
            source_name=source.name,
            uid=f"{asin}:{current:.2f}",  # a further drop is a new post
            url=f"https://keepa.com/#!product/1-{asin}",  # credit link: Keepa's page
            title=title,
            text="\n".join(lines),
            published_at=None,  # first_seen governs freshness
            fetched_at=now,
            image_url=_image_url(row),
        )

    @staticmethod
    def _map_warehouse_row(row: dict[str, Any], source: SourceConfig,
                           now: datetime) -> RawPost | None:
        """Amazon Warehouse (Amazon Resale) open-box/used offer, compared
        honestly against the LIVE new-condition price — exactly the
        "$12.07 used vs $81.55 new = -85%" framing Amazon itself shows.
        No live new price => no honest basis => skipped."""
        asin = str(row.get("asin") or "").strip().upper()
        title = str(row.get("title") or "").strip()
        if not asin or not title:
            return None
        used = _price_at(row.get("current"), PRICE_TYPE_WAREHOUSE)
        new_p = _price_at(row.get("current"), PRICE_TYPE_AMAZON)
        if new_p is None:
            new_p = _price_at(row.get("current"), PRICE_TYPE_NEW)
        if used is None or new_p is None or new_p <= 0:
            return None
        if used >= new_p * (1.0 - WAREHOUSE_MIN_DROP_PCT / 100.0):
            return None  # used supply is plentiful: only the deep cuts

        lines = [
            title,
            f"Amazon Warehouse (Amazon Resale) open-box/used deal: "
            f"yours for ${used:,.2f}.",
            f"New condition price: ${new_p:,.2f}.",
            "Condition: used/open-box, sold online by Amazon Resale with "
            "free returns. Warehouse stock is usually one unit and sells "
            "out fast.",
            f"Product link: https://www.amazon.com/dp/{asin}",
        ]
        return RawPost(
            source_name=source.name,
            uid=f"{asin}:wh:{used:.2f}",  # a further drop is a new post
            url=f"https://keepa.com/#!product/1-{asin}",
            title=title,
            text="\n".join(lines),
            published_at=None,
            fetched_at=now,
            image_url=_image_url(row),
        )
