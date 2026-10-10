"""Woot source adapter: the official Woot developer API (developer.woot.com).

Requires the ``WOOT_API_KEY`` secret (reported upstream as ``needs_key`` until
set). One GET of the ``/feed/All`` endpoint per run — the key's quota is
1,000 requests/day, so hourly + the 15-minute lane together use ~120 (12%).
Prices are written into the post text verbatim (``Sale price: $49.99`` /
``List price: $199.99``) so the evidence-quote check can match them exactly;
Woot's ``Condition`` field is quoted too (Woot sells a lot of refurbs, which
must carry the used/open-box floor, never pass as new).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import httpx

from .. import util
from ..config import AppConfig, get_secret
from ..db import DB
from ..errors import AdapterNotConfigured, DeadlineExceeded
from ..models import RawPost, SourceConfig
from .base import SourceAdapter

log = logging.getLogger("dealradar.fetch.woot")

FEED_URL = "https://developer.woot.com/feed/All"

#: Cap on offers mapped per run: bounds LLM spend. The All feed holds
#: thousands of offers; content-hash dedupe means only unseen ones parse.
MAX_ITEMS_PER_RUN = 150


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


class WootAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http_client
        self._sleep = sleep_fn  # kept for interface symmetry (single call per run)

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
        key = get_secret("WOOT_API_KEY")
        if not key:
            raise AdapterNotConfigured("woot api key missing")

        with self._client(cfg) as client:
            _check_deadline(deadline)
            # The key travels ONLY in the x-api-key header: query-string keys
            # end up inside httpx error messages and from there could leak
            # into recorded (published) source errors.
            resp = client.get(FEED_URL, headers={"x-api-key": key})
            resp.raise_for_status()
            payload = resp.json()

        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = (payload.get("Items") or payload.get("items")
                     or payload.get("Offers") or [])
        else:
            items = []

        posts: list[RawPost] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("IsSoldOut"):
                continue
            post = self._map_item(item, source, now)
            if post is not None:
                posts.append(post)
            if len(posts) >= MAX_ITEMS_PER_RUN:
                break
        log.info("woot %s: %d raw offers -> mapped %d posts",
                 source.name, len(items), len(posts))
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
        end_date = str(item.get("EndDate") or "")
        if end_date:
            lines.append(f"Ends: {end_date}")
        lines.append("Sold online at Woot.com (Amazon's official deals site).")
        photo = item.get("Photo")
        image = photo if isinstance(photo, str) \
            and photo.startswith(("http://", "https://")) else ""
        return RawPost(
            source_name=source.name,
            uid=uid,
            url=str(item.get("Url") or ""),
            title=title,
            text="\n".join(lines),
            published_at=util.parse_feed_date(item.get("StartDate"),
                                              source.tz, now),
            fetched_at=now,
            image_url=image,
        )
