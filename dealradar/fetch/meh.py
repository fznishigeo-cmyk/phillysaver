"""Meh source adapter: the official Meh API (one deal per day).

Two official access paths:
- With the ``MEH_API_KEY`` secret: the keyed API (richer — per-item prices
  from ``deal.items`` are written verbatim, ``Price: $19.00``, so evidence
  quotes can match them).
- Without a key: Meh's PUBLIC JSON Feed at https://meh.com/feed.json,
  published by Meh exactly for machine consumption — the source works out of
  the box.

``published_at`` from the keyed API is left None — ``posts_seen.first_seen_at``
governs freshness.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from datetime import datetime, time as day_time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..config import AppConfig, get_secret
from ..db import DB
from ..http_client import api_fetcher
from ..errors import DeadlineExceeded
from ..models import RawPost, SourceConfig
from .base import SourceAdapter

log = logging.getLogger("dealradar.fetch.meh")

CURRENT_URL = "https://meh.com/api/1/current.json"
PUBLIC_FEED_URL = "https://meh.com/feed.json"  # official keyless JSON Feed


def deal_end(seen: datetime) -> datetime:
    """The next midnight US Central, including 23/25-hour DST days."""
    tomorrow = seen.astimezone(ZoneInfo("America/Chicago")).date() + timedelta(days=1)
    return datetime.combine(tomorrow, day_time(), ZoneInfo("America/Chicago")).astimezone(timezone.utc)

_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")  # [text](url) / ![alt](url) -> text
_MD_NOISE = re.compile(r"[*_`#>]+")


def _strip_markdown(s: str) -> str:
    """Crude markdown strip: links to their text, emphasis/heading marks dropped."""
    s = _MD_LINK.sub(r"\1", s)
    s = _MD_NOISE.sub("", s)
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def _fmt_money(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"${value:,.2f}"
    s = str(value).strip()
    if not s:
        return ""
    return s if s.startswith("$") else f"${s}"


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise DeadlineExceeded("meh: per-source deadline crossed")


class MehAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http_client
        self._sleep = sleep_fn  # kept for interface symmetry (single call per run)

    def access_mode(self) -> str:
        """The keyed API is a different reader from the public feed: adding
        the key gives the source a fresh circuit breaker (run_source)."""
        return "keyed" if get_secret("MEH_API_KEY") else ""

    def fetch_posts(
        self,
        source: SourceConfig,
        fetcher: Any,
        db: DB,
        cfg: AppConfig,
        now: datetime,
        deadline: float | None,
    ) -> list[RawPost]:
        key = get_secret("MEH_API_KEY")
        if not key:
            return self._fetch_public_feed(source, fetcher, db, cfg, now, deadline)

        with api_fetcher(fetcher, db, cfg, http_client=self._http, sleep_fn=self._sleep) as client:
            _check_deadline(deadline)
            resp = client.api_get(CURRENT_URL, params={"apikey": key}, deadline=deadline,
                                  respect_robots=False)  # keyed API
            resp.raise_for_status()
            payload = resp.json()

        deal = payload.get("deal") if isinstance(payload, dict) else None
        if not isinstance(deal, dict):
            return []
        uid = str(deal.get("id") or "")
        title = str(deal.get("title") or "")
        if not uid or not title:
            return []

        lines = [title]
        seen_prices: set[str] = set()
        for item in deal.get("items") or []:
            if not isinstance(item, dict):
                continue
            price = _fmt_money(item.get("price"))
            if price and price not in seen_prices:
                seen_prices.add(price)
                lines.append(f"Price: {price}")
        story = deal.get("story") or {}
        body = _strip_markdown(str(story.get("body") or "")) if isinstance(story, dict) else ""
        if body:
            lines.append(body)

        post = RawPost(
            source_name=source.name,
            uid=uid,
            url=str(deal.get("url") or ""),
            title=title,
            text="\n".join(lines),
            published_at=None,  # first_seen governs freshness for meh
            fetched_at=now,
        )
        log.info("meh %s: mapped 1 post (%s)", source.name, uid)
        return [post]

    def _fetch_public_feed(
        self,
        source: SourceConfig,
        fetcher: Any,
        db: DB,
        cfg: AppConfig,
        now: datetime,
        deadline: float | None,
    ) -> list[RawPost]:
        """Keyless path: Meh's official public JSON Feed (feed.json)."""
        from .. import util  # html_to_text / parse_feed_date

        with api_fetcher(fetcher, db, cfg, http_client=self._http, sleep_fn=self._sleep) as client:
            _check_deadline(deadline)
            resp = client.api_get(PUBLIC_FEED_URL, deadline=deadline)
            resp.raise_for_status()
            payload = resp.json()

        items = payload.get("items") if isinstance(payload, dict) else None
        posts: list[RawPost] = []
        for item in (items or [])[:5]:
            if not isinstance(item, dict):
                continue
            uid = str(item.get("id") or item.get("url") or "")
            title = str(item.get("title") or "")
            if not uid or not title:
                continue
            body_html = str(item.get("content_html") or item.get("content_text") or "")
            text = title + "\n" + util.html_to_text(body_html)
            posts.append(RawPost(
                source_name=source.name,
                uid=uid,
                url=str(item.get("url") or ""),
                title=title,
                text=text.strip(),
                published_at=util.parse_feed_date(
                    item.get("date_published"), source.tz, now),
                fetched_at=now,
            ))
        log.info("meh %s: mapped %d posts from public feed.json",
                 source.name, len(posts))
        return posts
