"""Paywall: the public deals.json is a teaser; members get the full payload.

The static site never holds the locked data (docs/paywall.md). render_site
keeps cfg.paywall_teaser_count alive deals in full (a different set from the
top cfg.paywall_teaser_pool at every drop) and turns every
other alive deal into a locked stub — store, percent, photo, nothing that
names, prices or links it — then the pipeline POSTs the full payload to the
Worker, which serves it to signed-in members only.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import get_secret

log = logging.getLogger("dealradar.publish.paywall")

#: The only keys a locked stub carries: enough to draw a blurred card.
STUB_KEYS = ("key", "pct_at_checkout", "store", "category",
             "first_seen_at", "image_url", "status", "conditions")


def _stub(deal: dict[str, Any]) -> dict[str, Any]:
    return {"locked": True, **{k: deal.get(k) for k in STUB_KEYS}}


def split_teaser(deals: list[dict[str, Any]], cfg,
                 recent: list[str] | tuple[str, ...] = ()) -> tuple[list[dict[str, Any]], int]:
    """(teaser, locked_count) for serialized deals in site order: teaser_count
    alive deals from the top teaser_pool stay full, the other alive deals
    become locked stubs, dead deals follow unchanged. recent: the keys the
    last drops unlocked (oldest first); they go to the back of the line, so
    every drop unlocks different deals. Passthrough with the paywall off."""
    if not getattr(cfg, "paywall_enabled", False):
        return deals, 0
    n = max(0, int(getattr(cfg, "paywall_teaser_count", 8)))
    pool = max(n, int(getattr(cfg, "paywall_teaser_pool", 48)))
    alive = [d for d in deals if d.get("status") == "alive"]
    dead = [d for d in deals if d.get("status") != "alive"]
    fresh, stale = _rotate(alive[:pool], recent)
    # Fresh deals first (store variety among them), and a recently shown
    # deal only once the fresh ones run out: every drop really is new.
    picked = _pick_teaser(fresh, n)
    picked += stale[:n - len(picked)]
    chosen = {id(d) for d in picked}
    teaser = picked + [_stub(d) for d in alive if id(d) not in chosen] + dead
    return teaser, max(0, len(alive) - len(picked))


def _rotate(pool: list[dict[str, Any]], recent) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(fresh, stale): the pool's deals not unlocked in recent drops, in
    site order, and the recently unlocked ones, least recently shown first:
    a visitor who stays or comes back sees new free deals at every drop."""
    last = {k: i for i, k in enumerate(recent)}
    fresh = [d for d in pool if d.get("key") not in last]
    stale = sorted((d for d in pool if d.get("key") in last), key=lambda d: last[d.get("key")])
    return fresh, stale


def teaser_memory(teaser: list[dict[str, Any]], recent, cfg) -> list[str]:
    """recent with this drop's unlocked keys appended, keeping the last
    teaser_pool - teaser_count: a deal comes back only after the rest of the
    pool has had its turn."""
    n = max(0, int(getattr(cfg, "paywall_teaser_count", 8)))
    keep = max(0, int(getattr(cfg, "paywall_teaser_pool", 48)) - n)
    shown = [d.get("key") for d in teaser
             if d.get("status") == "alive" and not d.get("locked") and d.get("key")]
    return (list(recent) + shown)[-keep:] if keep else []


#: Unlocked picks per store in the first pass: eight $5 phone cases from one
#: seller are not "the eight most amazing deals"; variety is the tease.
_TEASER_PER_STORE = 2


def _pick_teaser(alive: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """The first n alive deals in site order (best first), but at most
    _TEASER_PER_STORE from any one store while other stores remain; the
    leftover slots take the next best regardless of store."""
    picked: list[dict[str, Any]] = []
    per_store: dict[str, int] = {}
    for d in alive:
        if len(picked) >= n:
            break
        store = str(d.get("store") or "").lower()
        if per_store.get(store, 0) >= _TEASER_PER_STORE:
            continue
        per_store[store] = per_store.get(store, 0) + 1
        picked.append(d)
    if len(picked) < n:
        chosen = {id(d) for d in picked}
        picked += [d for d in alive if id(d) not in chosen][:n - len(picked)]
    return picked


def publish_full(payload: dict[str, Any], cfg, http_client: Any = None) -> bool:
    """POST the full payload to the Worker's /api/publish. Never raises:
    False with one log line on any skip or failure (retried next run)."""
    api_url = (getattr(cfg, "paywall_api_url", "") or "").rstrip("/")
    if not api_url:
        log.info("paywall publish skipped: no api_url")
        return False
    secret = get_secret("NEWSLETTER_SECRET")
    if not secret:
        log.info("paywall publish skipped: NEWSLETTER_SECRET unset")
        return False
    if not payload:  # never overwrite the members' store with nothing
        log.info("paywall publish skipped: empty payload")
        return False
    client = http_client or httpx.Client(timeout=20)
    try:
        resp = client.post(f"{api_url}/api/publish", json=payload,
                           headers={"Authorization": f"Bearer {secret}"})
        if not 200 <= resp.status_code < 300:
            log.warning("paywall publish failed: %s", resp.status_code)
            return False
    except Exception as e:  # transport errors: warn, retry next run
        log.warning("paywall publish failed: %s", e)
        return False
    log.info("paywall publish: %d deals -> %s",
             len(payload.get("deals") or []), api_url)
    return True
