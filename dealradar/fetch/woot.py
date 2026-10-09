"""Woot source adapter: the official Woot developer API (api.woot.com).

Requires the ``WOOT_API_KEY`` secret (reported upstream as ``needs_key`` until
set). One GET per run; prices are written into the post text verbatim
(``Sale price: $49.99`` / ``List price: $199.99``) so the evidence-quote check
in the parse layer can match them exactly.
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

EVENTS_URL = "https://api.woot.com/2/events.json"


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise DeadlineExceeded("woot: per-source deadline crossed")


def _fmt_money(value: Any) -> str:
    """Format an API price (number, string, or [min, max] list) as ``$X.XX``."""
    if value is None:
        return ""
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
            resp = client.get(
                EVENTS_URL,
                params={"eventType": "Daily", "key": key},
                headers={"x-api-key": key},
            )
            resp.raise_for_status()
            payload = resp.json()

        if isinstance(payload, list):
            events = payload
        elif isinstance(payload, dict):
            events = payload.get("Events") or [payload]
        else:
            events = []

        posts: list[RawPost] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("EventId") or "")
            event_url = str(event.get("Url") or "")
            published_at = util.parse_feed_date(event.get("StartDate"), source.tz, now)
            # Offers live under event['Offers']; some payload shapes put the
            # price fields on the event itself, so fall back to the event.
            offers = event.get("Offers") or [event]
            for offer in offers:
                if not isinstance(offer, dict):
                    continue
                title = str(offer.get("Title") or event.get("Title") or "")
                uid = str(offer.get("Id") or offer.get("OfferId") or event_id)
                if not title or not uid:
                    continue
                lines = [title]
                subtitle = str(offer.get("Subtitle") or event.get("Subtitle") or "")
                if subtitle:
                    lines.append(subtitle)
                sale = _fmt_money(offer.get("SalePrice"))
                if sale:
                    lines.append(f"Sale price: {sale}")
                list_price = _fmt_money(offer.get("ListPrice"))
                if list_price:
                    lines.append(f"List price: {list_price}")
                posts.append(
                    RawPost(
                        source_name=source.name,
                        uid=uid,
                        url=event_url,
                        title=title,
                        text="\n".join(lines),
                        published_at=published_at,
                        fetched_at=now,
                    )
                )
        log.info("woot %s: mapped %d posts", source.name, len(posts))
        return posts
