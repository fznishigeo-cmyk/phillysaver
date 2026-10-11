"""Stripe (read-only key, the same STRIPE_SECRET_KEY the pipeline uses):
trials, paying members, conversions, cancellations, MRR estimate."""

from __future__ import annotations

from typing import Any

SUBSCRIPTIONS = "https://api.stripe.com/v1/subscriptions"
MAX_PAGES = 50  # 5,000 subscriptions; counts are partial beyond that


def _in(ts: Any, lo: int, hi: int) -> bool:
    return isinstance(ts, int) and lo <= ts < hi


def stats(client: Any, key: str, *, yesterday_start: int, window_start: int,
          today_start: int, monthly_price: float) -> dict:
    """Counts over every subscription (status=all, newest first). Windows are
    [start, today_start) in epoch seconds; trialing/active are as of now."""
    subs: list[dict] = []
    after = None
    for _ in range(MAX_PAGES):
        params = {"status": "all", "limit": "100"}
        if after:
            params["starting_after"] = after
        resp = client.get(SUBSCRIPTIONS, params=params, auth=(key, ""))
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data") or []
        subs.extend(data)
        if not body.get("has_more") or not data:
            break
        after = data[-1].get("id")

    def trials(lo: int) -> int:
        return sum(1 for s in subs if s.get("trial_start") and _in(s.get("created"), lo, today_start))

    active = sum(1 for s in subs if s.get("status") == "active")
    return {"test_mode": key.startswith(("sk_test_", "rk_test_")),
            "trials_yesterday": trials(yesterday_start),
            "trials_window": trials(window_start),
            "trialing": sum(1 for s in subs if s.get("status") == "trialing"),
            "active": active,
            # Trial ended in the window and the subscription is paying now.
            "converted": sum(1 for s in subs if s.get("status") == "active"
                             and _in(s.get("trial_end"), window_start, today_start)),
            # canceled_at is set on request, so cancel-at-period-end counts too.
            "canceled": sum(1 for s in subs if _in(s.get("canceled_at"), window_start, today_start)),
            "mrr": round(active * monthly_price, 2)}
