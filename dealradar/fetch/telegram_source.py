"""Telegram source adapter: public channels via the MTProto *client* API.

This is Telethon with a user StringSession — NOT the Bot API. telethon is an
optional dependency and is imported lazily inside ``fetch_posts``; when it is
not installed (or any of the three secrets is missing, or no channels are
configured) the adapter raises :class:`AdapterNotConfigured` so the source is
reported upstream as ``needs_key``.

Tests inject a fake client via ``client_factory`` (a callable returning a
context manager that yields an object with ``get_messages(channel, limit=N)``),
so no telethon install and no network are ever needed.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from ..config import AppConfig, get_secret
from ..db import DB
from ..errors import AdapterNotConfigured, DeadlineExceeded
from ..models import RawPost, SourceConfig
from .base import SourceAdapter

log = logging.getLogger("dealradar.fetch.telegram_source")

#: Messages pulled per channel per run.
MESSAGES_PER_CHANNEL = 30


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise DeadlineExceeded("telegram: per-source deadline crossed")


class TelegramAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        fast_lane: bool = False,
        client_factory: Callable[[str, str, str], Any] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._fast_lane = fast_lane
        self._client_factory = client_factory
        self._sleep = sleep_fn

    def _make_client(self, api_id: str, api_hash: str, session: str) -> Any:
        """Return a context manager yielding a (sync) Telethon-like client."""
        if self._client_factory is not None:
            return self._client_factory(api_id, api_hash, session)
        try:
            # Lazy import: telethon is optional and not installed in CI.
            from telethon.sessions import StringSession  # type: ignore[import-not-found]
            from telethon.sync import TelegramClient  # type: ignore[import-not-found]
        except ImportError as exc:
            raise AdapterNotConfigured("telethon not installed") from exc
        try:
            numeric_api_id = int(api_id)
        except ValueError as exc:
            raise AdapterNotConfigured("TELEGRAM_API_ID must be numeric") from exc
        # Synchronous Telethon: usable as `with TelegramClient(...) as client:`.
        return TelegramClient(StringSession(session), numeric_api_id, api_hash)

    def fetch_posts(
        self,
        source: SourceConfig,
        fetcher: Any,
        db: DB,
        cfg: AppConfig,
        now: datetime,
        deadline: float | None,
    ) -> list[RawPost]:
        # fetcher/db unused: this adapter manages its own MTProto client.
        api_id = get_secret("TELEGRAM_API_ID")
        api_hash = get_secret("TELEGRAM_API_HASH")
        session = get_secret("TELEGRAM_SESSION")
        if not api_id or not api_hash or not session:
            raise AdapterNotConfigured("telegram credentials missing")
        channels = [c for c in (cfg.telegram_channels or []) if c]
        if not channels:
            raise AdapterNotConfigured("no telegram channels configured")
        if self._fast_lane:
            channels = channels[:1]

        # A permission partner's channel is credited to that partner, as its
        # newsletter is (config telegram.channel_sources).
        credit = cfg.telegram_channel_sources or {}
        posts: list[RawPost] = []
        with self._make_client(api_id, api_hash, session) as client:
            for i, channel in enumerate(channels):
                if i:
                    self._sleep(1.0)  # <=1 request/s across channels
                _check_deadline(deadline)
                messages = client.get_messages(channel, limit=MESSAGES_PER_CHANNEL)
                for msg in messages or []:
                    text = getattr(msg, "message", "") or ""
                    if not text.strip():
                        continue  # media-only or empty message
                    msg_id = getattr(msg, "id", None)
                    if msg_id is None:
                        continue
                    published_at = getattr(msg, "date", None)
                    if published_at is not None and published_at.tzinfo is None:
                        published_at = published_at.replace(tzinfo=timezone.utc)
                    title = text.strip().splitlines()[0].strip()
                    posts.append(
                        RawPost(
                            source_name=credit.get(channel) or source.name,
                            uid=f"{channel}/{msg_id}",
                            url=f"https://t.me/{channel}/{msg_id}",
                            title=title,
                            text=text,
                            published_at=published_at,
                            fetched_at=now,
                        )
                    )
        log.info("telegram %s: mapped %d posts from %d channel(s)",
                 source.name, len(posts), len(channels))
        return posts
