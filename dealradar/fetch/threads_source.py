"""Threads source adapter: official keyword search on graph.threads.net.

Gated behind ``feature_flags.threads`` (off until Meta app review passes) —
raises :class:`AdapterDisabled` while the flag is off, and
:class:`AdapterNotConfigured` when ``THREADS_TOKEN`` is missing. Searches at
most two keywords per run with an injectable ``sleep_fn`` between calls.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from .. import util
from ..config import AppConfig, get_secret
from ..db import DB
from ..http_client import api_fetcher
from ..errors import AdapterDisabled, AdapterNotConfigured, DeadlineExceeded
from ..models import RawPost, SourceConfig
from .base import SourceAdapter

log = logging.getLogger("dealradar.fetch.threads_source")

SEARCH_URL = "https://graph.threads.net/v1.0/keyword_search"
FIELDS = "id,text,permalink,timestamp,username"
DEFAULT_KEYWORDS = ["deal", "% off"]
MAX_KEYWORDS = 2


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise DeadlineExceeded("threads: per-source deadline crossed")


class ThreadsAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http_client
        self._sleep = sleep_fn

    def fetch_posts(
        self,
        source: SourceConfig,
        fetcher: Any,
        db: DB,
        cfg: AppConfig,
        now: datetime,
        deadline: float | None,
    ) -> list[RawPost]:
        if not cfg.feature_flags.get("threads"):
            raise AdapterDisabled("threads flag off (pending app review)")
        token = get_secret("THREADS_TOKEN")
        if not token:
            raise AdapterNotConfigured("threads token missing")

        raw_threads = cfg.raw.get("threads") or {}
        keywords = list(raw_threads.get("keywords") or DEFAULT_KEYWORDS)[:MAX_KEYWORDS]

        posts: list[RawPost] = []
        seen_uids: set[str] = set()
        with api_fetcher(fetcher, db, cfg, http_client=self._http, sleep_fn=self._sleep) as client:
            for keyword in keywords:
                _check_deadline(deadline)
                resp = client.api_get(
                    SEARCH_URL,
                    params={
                        "q": keyword,
                        "search_type": "TOP",
                        "fields": FIELDS,
                        "access_token": token,
                    }, deadline=deadline,
                    respect_robots=False,  # keyed Graph API
                )
                resp.raise_for_status()
                for item in resp.json().get("data") or []:
                    if not isinstance(item, dict):
                        continue
                    uid = str(item.get("id") or "")
                    text = str(item.get("text") or "")
                    if not uid or uid in seen_uids or not text.strip():
                        continue
                    seen_uids.add(uid)
                    title = text.strip().splitlines()[0].strip()
                    posts.append(
                        RawPost(
                            source_name=source.name,
                            uid=uid,
                            url=str(item.get("permalink") or ""),  # credit link
                            title=title,
                            text=text,
                            published_at=util.parse_feed_date(
                                item.get("timestamp"), source.tz, now
                            ),
                            fetched_at=now,
                        )
                    )
        log.info("threads %s: mapped %d posts", source.name, len(posts))
        return posts
