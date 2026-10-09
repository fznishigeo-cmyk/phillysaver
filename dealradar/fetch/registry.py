"""Adapter registry: map a SourceConfig to the adapter instance that serves it.

All adapter imports are deferred into the branch that needs them so that
importing the registry never drags in adapters (or their optional
dependencies) that the current source set does not use.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..config import AppConfig
from ..models import SourceConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .base import SourceAdapter

log = logging.getLogger("dealradar.fetch.registry")


def get_adapter(source: SourceConfig, cfg: AppConfig, *, fast_lane: bool = False) -> "SourceAdapter":
    """Return the adapter for ``source``; raise ValueError for unknowns.

    Feature-flag gating (threads/instagram) lives inside the adapters
    themselves, not here. ``cfg`` is part of the stable signature for callers
    even though construction currently needs nothing from it.
    """
    method = (source.method or "").lower()
    if method in ("rss", "html"):
        # html sources are served by the RSS adapter after first-run
        # validation has autodiscovered a feed URL for them.
        from .rss import RssAdapter
        return RssAdapter()
    if method == "api":
        adapter_name = (source.adapter or "").lower()
        if adapter_name == "reddit":
            from .reddit import RedditAdapter
            return RedditAdapter()
        if adapter_name == "woot":
            from .woot import WootAdapter
            return WootAdapter()
        if adapter_name == "meh":
            from .meh import MehAdapter
            return MehAdapter()
        if adapter_name == "telegram":
            from .telegram_source import TelegramAdapter
            return TelegramAdapter(fast_lane=fast_lane)
        if adapter_name == "threads":
            from .threads_source import ThreadsAdapter
            return ThreadsAdapter()
        if adapter_name == "instagram":
            from .instagram_source import InstagramAdapter
            return InstagramAdapter()
        raise ValueError(
            f"unknown api adapter {source.adapter!r} for source {source.name!r}"
        )
    raise ValueError(f"unknown method {source.method!r} for source {source.name!r}")
