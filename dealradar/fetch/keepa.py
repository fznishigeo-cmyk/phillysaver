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


def _cents(v: Any) -> float | None:
    """Keepa prices are integer cents; -1 (or junk) means no data."""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
        return round(float(v) / 100.0, 2)
    return None


def _first_price(obj: Any) -> float | None:
    """Pull a price out of Keepa's value-or-array shapes defensively."""
    if isinstance(obj, (list, tuple)):
        for v in obj:
            p = _cents(v)
            if p is not None:
                return p
        return None
    return _cents(obj)


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
            "priceTypes": [0],  # 0 = Amazon's own price
            "deltaPercentRange": [MIN_DROP_PCT, 100],
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

        deals_obj = payload.get("deals") if isinstance(payload, dict) else None
        rows = (deals_obj or {}).get("dr") if isinstance(deals_obj, dict) else None
        posts: list[RawPost] = []
        for row in (rows or [])[:MAX_DEALS_PER_RUN]:
            if not isinstance(row, dict):
                continue
            post = self._map_row(row, source, now)
            if post is not None:
                posts.append(post)
        log.info("keepa %s: mapped %d price-drop posts", source.name, len(posts))
        return posts

    @staticmethod
    def _map_row(row: dict[str, Any], source: SourceConfig,
                 now: datetime) -> RawPost | None:
        asin = str(row.get("asin") or "").strip().upper()
        title = str(row.get("title") or "").strip()
        if not asin or not title:
            return None
        current = _first_price(row.get("current"))
        avg = _first_price(row.get("avg") or row.get("avg90") or row.get("avg30"))
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
