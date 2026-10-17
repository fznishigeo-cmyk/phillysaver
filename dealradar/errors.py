"""Shared exceptions used across fetch, parse, and publish layers."""

from __future__ import annotations


class DealradarError(Exception):
    """Base class."""


class DeadlineExceeded(DealradarError):
    """A time budget was exhausted; sent requests must retain paid reservations."""

    def __init__(self, *args, request_sent: bool = False):
        super().__init__(*args)
        self.request_sent = request_sent


class RobotsBlocked(DealradarError):
    """robots.txt disallows fetching this URL; the fetch was not attempted."""


class PostUnavailable(DealradarError):
    """One source post moved or was removed (3xx/404/410): not a site failure."""


class AdapterNotConfigured(DealradarError):
    """Source adapter is missing required secrets (reported as needs_key)."""


class AdapterDisabled(DealradarError):
    """Source adapter is behind a feature flag that is off (reported as disabled)."""


class ParserNotConfigured(DealradarError):
    """DEALS_CLAUDE_API_KEY is not set; the parse layer cannot run."""


class SpendCapReached(DealradarError):
    """Per-run LLM spend cap was hit; parsing aborts gracefully."""
