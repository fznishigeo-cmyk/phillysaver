"""Paywall: the public deals.json is a teaser; members get the full payload.

The static site never holds the locked data (docs/paywall.md). render_site
keeps cfg.paywall_teaser_count alive deals in full (a different set at every
drop: no deal is free twice until every live deal has had a turn) and turns every
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
# "src" is the sites that reported a deal (domains, never the post URL),
# so the source filter counts locked deals too.
STUB_KEYS = ("key", "pct_at_checkout", "store", "category",
             "first_seen_at", "image_url", "status", "conditions", "src")


def _stub(deal: dict[str, Any]) -> dict[str, Any]:
    return {"locked": True, **{k: deal.get(k) for k in STUB_KEYS}}


def is_open(cfg) -> bool:
    """True when every visitor gets every deal in full: the paywall off, or
    open access (config.yaml paywall.open_access)."""
    return bool(getattr(cfg, "support_enabled", False)) \
        or not getattr(cfg, "paywall_enabled", False) \
        or bool(getattr(cfg, "paywall_open_access", False))


def split_teaser(deals: list[dict[str, Any]], cfg,
                 recent: list[str] | tuple[str, ...] = ()) -> tuple[list[dict[str, Any]], int]:
    """(teaser, locked_count) for serialized deals in site order: teaser_count
    alive deals stay full, the other alive deals become locked stubs, dead
    deals follow unchanged. The full ones are the best deals not unlocked
    before (recent: the keys past drops unlocked, oldest first), among every
    live deal, or the top teaser_pool when that is set. A deal is free again
    only when no unshown one is left, least recently shown first.
    Passthrough with the paywall off or open (is_open)."""
    if is_open(cfg):
        return deals, 0
    n = max(0, int(getattr(cfg, "paywall_teaser_count", 8)))
    pool = int(getattr(cfg, "paywall_teaser_pool", 0) or 0)
    alive = [d for d in deals if d.get("status") == "alive"]
    dead = [d for d in deals if d.get("status") != "alive"]
    fresh, stale = _rotate(alive[:max(n, pool)] if pool > 0 else alive, recent)
    # Fresh deals first (store variety among them), and a shown deal only
    # once the fresh ones run out, strictly least recently shown first: a
    # store cap here would skip to the last drop's deals while older ones
    # wait, so every drop really is new.
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
    """recent with this drop's unlocked keys appended (a key once, at its
    latest showing), keeping only live deals' keys (locked stubs carry
    theirs): a dead deal's key leaves, a live one stays until every other
    live deal has had its turn. With teaser_pool set, the last
    teaser_pool - teaser_count of them."""
    n = max(0, int(getattr(cfg, "paywall_teaser_count", 8)))
    pool = int(getattr(cfg, "paywall_teaser_pool", 0) or 0)
    live = {d.get("key") for d in teaser if d.get("status") == "alive" and d.get("key")}
    shown = [d.get("key") for d in teaser
             if d.get("status") == "alive" and not d.get("locked") and d.get("key")]
    out, seen = [], set()
    for k in reversed(list(recent) + shown):
        if k in live and k not in seen:
            seen.add(k)
            out.append(k)
    out.reverse()
    if pool <= 0:
        return out
    keep = max(0, pool - n)
    return out[-keep:] if keep else []


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
