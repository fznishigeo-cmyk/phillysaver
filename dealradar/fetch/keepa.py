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
#: publish floor so near-misses still enter the DB and price history.
MIN_DROP_PCT = 60

#: Keepa price-type index 0 = Amazon's own price (not third-party sellers).
PRICE_TYPE_AMAZON = 0

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

        selection = {
            "page": 0,
            "domainId": DOMAIN_US,
            "priceTypes": [PRICE_TYPE_AMAZON],
            "deltaPercentRange": [MIN_DROP_PCT, 100],
            "dateRange": DATE_RANGE_90D,
            "isRangeEnabled": True,
            "sortType": 4,      # biggest percent drop first (per Keepa docs)
        }
        with self._client(cfg) as client:
            _check_deadline(deadline)
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
        posts: list[RawPost] = []
        for row in (rows or [])[:MAX_DEALS_PER_RUN]:
            if not isinstance(row, dict):
                continue
            post = self._map_row(row, source, now)
            if post is not None:
                posts.append(post)
        tokens_left = (payload.get("tokensLeft")
                       if isinstance(payload, dict) else None)
        log.info("keepa %s: %d raw deals -> mapped %d price-drop posts "
                 "(tokensLeft=%s)", source.name, len(rows or []), len(posts),
                 tokens_left)
        if rows and not posts:
            sample = rows[0] if isinstance(rows[0], dict) else {}
            log.warning(
                "keepa %s: raw deals present but none mapped — schema drift? "
                "sample keys=%s current=%r avg=%r",
                source.name, sorted(sample)[:20],
                sample.get("current"), sample.get("avg"))
        return posts

    @staticmethod
    def _map_row(row: dict[str, Any], source: SourceConfig,
                 now: datetime) -> RawPost | None:
        asin = str(row.get("asin") or "").strip().upper()
        title = str(row.get("title") or "").strip()
        if not asin or not title:
            return None
        current = _price_at(row.get("current"), PRICE_TYPE_AMAZON)
        avg = _avg_price(row.get("avg") or row.get("avg90") or row.get("avg30"),
                         PRICE_TYPE_AMAZON)
        if current is None or avg is None or avg <= 0 or current >= avg:
            return None

        # Synthesized source text: the quotes the evidence check will verify.
        # The 90-day average is the honest "regular" basis — never a claimed MSRP.
        lines = [
            title,
            f"Amazon price drop: now ${current:,.2f}",
            f"recent average price ${avg:,.2f}",
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
        )
