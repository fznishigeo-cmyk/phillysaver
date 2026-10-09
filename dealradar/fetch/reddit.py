"""Reddit source adapter: the official OAuth API, nothing else.

Policy (sources.yaml): registered-app OAuth only. The token comes from the
``client_credentials`` grant; listings are read from ``oauth.reddit.com`` with
Reddit's required User-Agent format (the ``REDDIT_USER_AGENT`` secret, sent
verbatim). We stay far below Reddit's 100 req/min cap: one listing call per
subreddit per run, with an injectable ``sleep_fn`` keeping us at <=1 req/s.

The adapter manages its own authenticated httpx client, so the injected
``fetcher`` is ignored. ``RawPost.text`` is transient and never persisted.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import httpx

from .. import util
from ..config import AppConfig, get_secret
from ..db import DB
from ..errors import AdapterNotConfigured, DeadlineExceeded
from ..models import RawPost, SourceConfig
from .base import SourceAdapter

log = logging.getLogger("dealradar.fetch.reddit")

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
#: Refresh the cached token this many seconds before Reddit says it expires.
TOKEN_EXPIRY_MARGIN_S = 60.0


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise DeadlineExceeded("reddit: per-source deadline crossed")


class RedditAdapter(SourceAdapter):
    """Reads one subreddit listing (``source.url`` + ``.json``) per call."""

    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http_client
        self._sleep = sleep_fn
        self._token: str = ""
        self._token_expires_at: float = 0.0  # time.monotonic() deadline

    @contextmanager
    def _client(self, cfg: AppConfig, ua: str) -> Iterator[httpx.Client]:
        if self._http is not None:
            yield self._http
            return
        client = httpx.Client(
            headers={"User-Agent": ua},
            timeout=cfg.request_timeout_s,
            follow_redirects=True,
        )
        try:
            yield client
        finally:
            client.close()

    def _ensure_token(
        self, client: httpx.Client, client_id: str, client_secret: str, ua: str
    ) -> tuple[str, bool]:
        """Return (bearer_token, fetched_now). Caches token + expiry."""
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token, False
        resp = client.post(
            TOKEN_URL,
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": ua},
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = str(payload["access_token"])
        expires_in = float(payload.get("expires_in", 3600))
        self._token_expires_at = time.monotonic() + max(
            expires_in - TOKEN_EXPIRY_MARGIN_S, 60.0
        )
        return self._token, True

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
        client_id = get_secret("REDDIT_CLIENT_ID")
        client_secret = get_secret("REDDIT_CLIENT_SECRET")
        ua = get_secret("REDDIT_USER_AGENT")
        if not client_id or not client_secret or not ua:
            raise AdapterNotConfigured("reddit credentials missing")

        with self._client(cfg, ua) as client:
            _check_deadline(deadline)
            token, fetched_now = self._ensure_token(client, client_id, client_secret, ua)
            if fetched_now:
                # Two consecutive calls (token + listing): stay at <=1 req/s.
                self._sleep(1.0)
            _check_deadline(deadline)
            listing_url = source.url + ".json?limit=50&raw_json=1"
            resp = client.get(
                listing_url,
                headers={"Authorization": f"bearer {token}", "User-Agent": ua},
            )
            resp.raise_for_status()
            payload = resp.json()

        posts: list[RawPost] = []
        children = (payload.get("data") or {}).get("children") or []
        for child in children:
            d = child.get("data") or {}
            if d.get("stickied"):
                continue
            uid = d.get("name")
            permalink = d.get("permalink")
            title = d.get("title")
            if not uid or not permalink or not title:
                continue
            # Self posts carry their body in selftext; link posts carry only a
            # destination URL. Either way the text goes through html_to_text
            # defensively before it enters the (transient) pipeline.
            body = d.get("selftext") or d.get("url", "") or ""
            text = f"{title}\n{util.html_to_text(body)}"
            created = d.get("created_utc")
            published_at = (
                datetime.fromtimestamp(float(created), tz=timezone.utc)
                if created is not None
                else None
            )
            posts.append(
                RawPost(
                    source_name=source.name,
                    uid=str(uid),
                    url="https://www.reddit.com" + str(permalink),
                    title=str(title),
                    text=text,
                    published_at=published_at,
                    fetched_at=now,
                )
            )
        log.info("reddit %s: mapped %d posts", source.name, len(posts))
        return posts
