"""Meh source adapter: the official Meh API (one deal per day).

Requires the ``MEH_API_KEY`` secret (reported upstream as ``needs_key`` until
set). Produces at most one RawPost per run; prices from ``deal.items`` are
written verbatim (``Price: $19.00``) so evidence quotes can match them.
``published_at`` is left None — ``posts_seen.first_seen_at`` governs freshness.
"""

from __future__ import annotations

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

log = logging.getLogger("dealradar.fetch.meh")

CURRENT_URL = "https://meh.com/api/1/current.json"

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
        key = get_secret("MEH_API_KEY")
        if not key:
            raise AdapterNotConfigured("meh api key missing")

        with self._client(cfg) as client:
            _check_deadline(deadline)
            resp = client.get(CURRENT_URL, params={"apikey": key})
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
