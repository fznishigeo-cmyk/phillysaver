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
import math
from dataclasses import dataclass, field
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


def run_gap_forecast(db, cfg, now: datetime) -> dict:
    """Bounded EMA of actual fast/hourly starts, with slack for cron drift.

    Read-only: repeated forecasts in a run see the same samples. The current
    start participates once even before start_run has inserted its row.
    """
    options = cfg.keepa_recheck_gap
    floor = cfg.keepa_recheck_interval_minutes
    ceiling = max(floor, min(60, float(options.get("max_minutes", 45))))
    count = max(1, min(48, int(options.get("samples", 12))))
    alpha = max(.01, min(1, float(options.get("ema_alpha", .3))))
    margin = max(0, min(60, float(options.get("margin_minutes", 10))))
    rows = db.conn.execute(
        "SELECT DISTINCT started_at FROM run_stats WHERE kind IN ('fast','hourly') "
        "AND started_at<=? ORDER BY started_at DESC LIMIT ?",
        (util.iso(now), count + 1)) if db is not None else []
    starts = sorted({now} | {at for row in rows
                             if (at := util.parse_iso(row["started_at"])) is not None and at <= now})
    gaps = [min(ceiling, max(floor, (b - a).total_seconds() / 60))
            for a, b in zip(starts, starts[1:])][-count:]
    ema = gaps[0] if gaps else floor
    for gap in gaps[1:]:
        ema += alpha * (gap - ema)
    expected = min(ceiling, max(floor, ema + (margin if gaps else 0)))
    return {"minutes": expected, "ema_minutes": ema, "samples": len(gaps),
            "floor_minutes": floor, "max_minutes": ceiling}


def _num(v: Any) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


@dataclass
class TokenUsage:
    """Actual receipts and separate conservative debits for lost receipts.

    https://keepa.com/api-docs/request-basics.html documents tokensConsumed.
    Bucket deltas include refill and are not a measurement of spend.
    """

    requests: int = 0
    missing: int = 0
    consumed: float = 0
    estimated: float = 0

    def note(self, payload: Any, *, estimate: float = 0, rate_limited: bool = False) -> None:
        self.requests += 1
        tokens = (0 if rate_limited else
                  _num(payload.get("tokensConsumed")) if isinstance(payload, dict) else None)
        if tokens is None or not math.isfinite(tokens) or tokens < 0:
            self.missing += 1
            self.estimated += estimate
        else:
            self.consumed += tokens

    def add(self, other: TokenUsage) -> None:
        self.requests += other.requests
        self.missing += other.missing
        self.consumed += other.consumed
        self.estimated += other.estimated

    @property
    def total(self) -> float | None:
        return None if self.missing else self.consumed

    @property
    def accounted(self) -> float:
        """Receipts plus estimated debits; never presented as actual spend."""
        return self.consumed + self.estimated


@dataclass
class KeepaBudget:
    """Last Keepa token reading plus the refill estimate since."""

    tokens_left: float | None = None
    refill_rate: float | None = None   # tokens per minute
    seen_at: datetime | None = None
    db: Any = field(default=None, repr=False, compare=False)  # roots from already-paid products
    usage: TokenUsage = field(default_factory=TokenUsage, repr=False, compare=False)

    @classmethod
    def load(cls, db) -> KeepaBudget:
        """Reading persisted by the previous run; empty (no gating) when
        missing or unreadable."""
        raw = db.meta_get(META_KEY) if db is not None else None
        if not raw:
            return cls(db=db)
        try:
            d = json.loads(raw)
        except ValueError:
            return cls(db=db)
        if not isinstance(d, dict):
            return cls(db=db)
        return cls(tokens_left=_num(d.get("tokens_left")),
                   refill_rate=_num(d.get("refill_rate")),
                   seen_at=util.parse_iso(d.get("seen_at")), db=db)

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
        estimate = self.tokens_left + rate * min(elapsed_min, MAX_REFILL_MINUTES)
        cap = self.capacity()
        return min(estimate, cap) if cap is not None else estimate

    def capacity(self) -> float | None:
        """The plan holds one hour of refill, including the last reading."""
        return self.refill_rate * MAX_REFILL_MINUTES if self.refill_rate else None

    def reserve(self, cost: float, now: datetime | None = None) -> None:
        """Debit a request before sending; an API reading replaces this estimate.

        A lost/unreadable response may still have cost tokens. It must not
        let the following batch spend the same balance again.
        """
        now = now or util.now_utc()
        available = self.available(now)
        if available is not None:
            self.tokens_left = available - cost
            self.seen_at = now

    def overflow(self, minutes: float, now: datetime | None = None) -> float | None:
        """Refill lost before the next pass if no other calls spend tokens."""
        available, cap = self.available(now), self.capacity()
        if available is None or cap is None:
            return None
        return max(0.0, available + self.refill_rate * minutes - cap)

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
