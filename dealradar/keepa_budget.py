"""Keepa token budget: one shared estimate of the API tokens left to spend.

Keepa bills a flat fee and meters every call from a token bucket that
refills by the minute (``refillRate`` tokens/min); an empty bucket answers
HTTP 429 until it refills — never a charge, just delayed data. Live
incident: the product calls (6-hourly price rechecks, in-window photo
backfill, the verify cross-check) drained the bucket, the two /deal
queries then hit three 429s in a row, the circuit breaker tripped the
ONLY used/open-box source for six hours, and Amazon photos stopped too.

Every Keepa response carries ``tokensLeft``, ``refillRate`` and
``refillIn``. This module keeps the latest reading in db meta so each
class of call can check the estimate before spending: photo-only calls
yield first, due rechecks next, the deal lane (the supply) always last.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from . import util

log = logging.getLogger("dealradar.keepa_budget")

#: db meta key holding the last reading as JSON.
META_KEY = "keepa_budget"

#: Refill credited between readings is capped at one hour: Keepa's bucket
#: never holds more than an hour of refill, and a stale reading must not
#: fabricate a full bucket.
MAX_REFILL_MINUTES = 60.0

#: Estimated tokens that must be left before each class of call runs. The
#: /deal queries (the only used/open-box supply) get the lowest bar; the
#: photo-only product calls, a nice-to-have, the highest.
MIN_FOR_DEAL_QUERY = 10.0
MIN_FOR_RECHECK = 15.0
MIN_FOR_IMAGE_BACKFILL = 60.0


def _num(v: Any) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


@dataclass
class KeepaBudget:
    """Last Keepa token reading plus the refill estimate since."""

    tokens_left: float | None = None
    refill_rate: float | None = None   # tokens per minute
    seen_at: datetime | None = None

    @classmethod
    def load(cls, db) -> KeepaBudget:
        """Reading persisted by the previous run; empty (no gating) when
        missing or unreadable."""
        raw = db.meta_get(META_KEY) if db is not None else None
        if not raw:
            return cls()
        try:
            d = json.loads(raw)
        except ValueError:
            return cls()
        if not isinstance(d, dict):
            return cls()
        return cls(tokens_left=_num(d.get("tokens_left")),
                   refill_rate=_num(d.get("refill_rate")),
                   seen_at=util.parse_iso(d.get("seen_at")))

    def save(self, db) -> None:
        if db is None or self.seen_at is None:
            return  # nothing learned: never overwrite a reading with nothing
        db.meta_set(META_KEY, json.dumps({
            "tokens_left": self.tokens_left,
            "refill_rate": self.refill_rate,
            "seen_at": util.iso(self.seen_at),
        }))

    def note(self, payload: Any, now: datetime | None = None) -> None:
        """Record the token fields of one Keepa response body (any status,
        a 429 body carries them too). Bodies without ``tokensLeft`` are
        ignored."""
        if not isinstance(payload, dict):
            return
        tokens = _num(payload.get("tokensLeft"))
        if tokens is None:
            return
        self.tokens_left = tokens
        rate = _num(payload.get("refillRate"))
        if rate is not None and rate >= 0:
            self.refill_rate = rate
        self.seen_at = now or util.now_utc()

    def available(self, now: datetime | None = None) -> float | None:
        """Estimated tokens in the bucket now: the last reading plus the
        refill since (at most one hour of it). None = never seen a
        response, which means no gating."""
        if self.tokens_left is None or self.seen_at is None:
            return None
        now = now or util.now_utc()
        elapsed_min = max(0.0, (now - self.seen_at).total_seconds() / 60.0)
        rate = self.refill_rate or 0.0
        return self.tokens_left + rate * min(elapsed_min, MAX_REFILL_MINUTES)

    def allows(self, need: float, now: datetime | None = None) -> bool:
        """True when the estimate clears ``need`` — or is unknown."""
        avail = self.available(now)
        return avail is None or avail >= need


def describe_429(payload: Any) -> str:
    """Human-readable token state from a 429 body, for the warning line."""
    if not isinstance(payload, dict):
        return "tokensLeft=? refillIn=?"
    return (f"tokensLeft={payload.get('tokensLeft', '?')} "
            f"refillIn={payload.get('refillIn', '?')}ms")


def response_payload(resp: Any) -> Any:
    """The JSON body of an httpx response, or None when it isn't JSON
    (Keepa answers JSON on 429 too, but never trust that)."""
    try:
        return resp.json()
    except ValueError:
        return None
