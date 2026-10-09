"""Source-runner scaffolding: the adapter interface and the per-source runner.

``run_source`` wraps one adapter call with the circuit breaker (source_health),
the per-source deadline, and uniform error reporting so the pipeline treats
every source the same way.
"""

from __future__ import annotations

import abc
import logging
import time
from collections.abc import Callable
from datetime import datetime

from ..config import AppConfig, redact_secrets
from ..db import DB
from ..errors import AdapterDisabled, AdapterNotConfigured, DeadlineExceeded
from ..http_client import PoliteFetcher
from ..models import RawPost, SourceConfig

logger = logging.getLogger("dealradar.fetch.base")


class SourceAdapter(abc.ABC):
    """One source type (rss, reddit, woot, ...) that yields RawPosts.

    Implementations must do all network I/O through ``fetcher`` (politeness is
    enforced there), must never persist post text (RawPost.text is transient),
    and should raise:

    - :class:`AdapterDisabled` when a feature flag turns the source off,
    - :class:`AdapterNotConfigured` when required secrets are missing,
    - :class:`DeadlineExceeded` when ``deadline`` (a ``time.monotonic()``
      value) is crossed.
    """

    @abc.abstractmethod
    def fetch_posts(
        self,
        source: SourceConfig,
        fetcher: PoliteFetcher,
        db: DB,
        cfg: AppConfig,
        now: datetime,
        deadline: float | None,
    ) -> list[RawPost]:
        """Fetch and return the source's current posts."""


def run_source(
    source: SourceConfig,
    adapter: SourceAdapter,
    fetcher: PoliteFetcher,
    db: DB,
    cfg: AppConfig,
    now: datetime,
    run_deadline: float | None = None,
    clock: Callable[[], float] | None = None,
) -> tuple[list[RawPost], bool, str]:
    """Run one source through its adapter with circuit breaking and budgets.

    Returns ``(posts, ok, error)``:

    - tripped circuit breaker -> ``([], False, 'tripped')``, nothing recorded;
    - :class:`AdapterDisabled` -> ``([], True, 'disabled')``, nothing recorded;
    - :class:`AdapterNotConfigured` -> failure recorded as ``needs_key``;
    - :class:`DeadlineExceeded` -> failure recorded as ``deadline``;
    - any other exception -> failure recorded as ``str(e)[:200]``;
    - success -> success recorded with the item count, ``(posts, True, '')``.

    The adapter's deadline is ``min(run_deadline, clock() +
    cfg.per_source_budget_s)``, ignoring Nones.
    """
    clock = clock if clock is not None else time.monotonic
    if db.is_tripped(source.name, now):
        logger.info("source %s: circuit breaker tripped; skipping", source.name)
        return ([], False, "tripped")

    deadlines = [d for d in (run_deadline, clock() + cfg.per_source_budget_s)
                 if d is not None]
    deadline = min(deadlines)

    def _record_failure(error: str,
                        count_failure: bool = True) -> tuple[list[RawPost], bool, str]:
        error = redact_secrets(error)
        db.record_source_result(
            source.name, ok=False, error=error, items=0, now=now,
            trip_threshold=cfg.circuit_breaker_failures,
            cooldown_h=cfg.circuit_breaker_cooldown_h,
            count_failure=count_failure,
        )
        return ([], False, error)

    try:
        posts = adapter.fetch_posts(source, fetcher, db, cfg, now, deadline)
    except AdapterDisabled:
        logger.info("source %s: adapter disabled; nothing recorded", source.name)
        return ([], True, "disabled")
    except AdapterNotConfigured:
        logger.warning("source %s: missing required secrets (needs_key)",
                       source.name)
        # A config state, not a site failure: never trips the breaker, so the
        # source activates on the very next run once its secrets are set.
        return _record_failure("needs_key", count_failure=False)
    except DeadlineExceeded:
        logger.warning("source %s: deadline exceeded", source.name)
        return _record_failure("deadline")
    except Exception as exc:
        logger.exception("source %s: adapter failed", source.name)
        return _record_failure(str(exc)[:200])

    db.record_source_result(
        source.name, ok=True, error="", items=len(posts), now=now,
        trip_threshold=cfg.circuit_breaker_failures,
        cooldown_h=cfg.circuit_breaker_cooldown_h,
    )
    logger.info("source %s: fetched %d posts", source.name, len(posts))
    return (posts, True, "")
