"""Verification tiers and liveness.

Tiers (best to worst):
- 'corroborated'   — seen from >= 2 distinct sources within the corroboration
                     window (cfg.corroborate_window_h).
- 'history-checked'— our OWN price_history for this dedupe_key (grown from
                     every parsed deal since day one) has at least
                     cfg.history_min_observations points and the current final
                     price clears the publish floor against our observed
                     median (final <= (1 - min_pct/100) * median).
- 'unverified'     — single source / list-price claim. Still published, but
                     visibly flagged and ranked lower.

Verification NEVER fetches retailer pages: it relies only on corroboration
across sources and our own accumulated price history. The optional Keepa
adapter (paid API, feature-flagged, off by default) is the only sanctioned
external check and is a deliberate stub below.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from .config import get_secret
from .util import iso, parse_iso

log = logging.getLogger("dealradar.verify")

_DEAD_RE = re.compile(
    r"\b(expired|dead|sold\s+out|out\s+of\s+stock|oos)\b", re.IGNORECASE)


def _history_ratio(cfg) -> float:
    """'Well below typical' means the same thing as the publish floor:
    final <= (1 - min_pct/100) x the typical price. One knob moves both."""
    return max(0.0, 1.0 - float(cfg.min_pct) / 100.0)


def assign_tier(db, deal: dict[str, Any], cfg, now: datetime) -> str:
    """Compute the verification tier for one deal row dict."""
    since = now - timedelta(hours=cfg.corroborate_window_h)
    if db.distinct_sources_within(deal["id"], since) >= 2:
        return "corroborated"

    final = deal.get("final_price")
    count, median = db.price_stats(deal["dedupe_key"])
    if (final is not None and median is not None
            and count >= cfg.history_min_observations
            and final <= _history_ratio(cfg) * median):
        return "history-checked"

    # Keepa cross-check (paid, flag + key gated): only for deals that would
    # otherwise publish unverified, at most once per deal (keepa_checked flag
    # in the payload keeps hourly re-tiering from burning tokens).
    if (deal.get("asin") and not deal.get("keepa_checked")
            and (deal.get("pct_at_checkout") or 0) >= cfg.min_pct):
        verdict = keepa_check(deal, cfg)
        if verdict is not None:
            deal["keepa_checked"] = True
            payload = {k: v for k, v in deal.items() if k != "id"}
            db.update_deal(deal["dedupe_key"], payload=payload)
            if verdict:
                return "history-checked"

    return "unverified"


def refresh_tiers(db, cfg, now: datetime) -> None:
    """Recompute and persist verify_tier for every alive deal."""
    for deal in db.all_deals(status="alive"):
        tier = assign_tier(db, deal, cfg, now)
        if tier != deal.get("verify_tier"):
            log.info("deal %s tier %s -> %s", deal["dedupe_key"],
                     deal.get("verify_tier"), tier)
        db.update_deal(deal["dedupe_key"], verify_tier=tier)


def mark_expired_dead(db, cfg, now: datetime) -> int:
    """Flip alive deals whose expires_at has passed to status 'dead'."""
    n = 0
    for deal in db.all_deals(status="alive"):
        expires = deal.get("expires_at") or ""
        if not expires:
            continue
        dt = parse_iso(expires)
        if dt is not None and dt < now:
            payload = {k: v for k, v in deal.items()
                       if k not in ("id", "normalized_item")}
            payload["died_at"] = iso(now)
            db.update_deal(deal["dedupe_key"], payload=payload, status="dead")
            n += 1
    if n:
        log.info("marked %d expired deal(s) dead", n)
    return n


def text_marks_dead(text: str) -> bool:
    """True when the text declares the deal over (expired / dead / sold out /
    out of stock / OOS), matched case-insensitively on word boundaries."""
    return bool(_DEAD_RE.search(text or ""))


def recheck_amazon_price(deal: dict[str, Any], cfg) -> bool | None:
    """Is this Amazon deal's price still live? (The legal equivalent of
    clicking the product page before publishing: Keepa's licensed API
    mirrors it.) True = a current offer still matches the deal's final
    price within 10%; False = the price moved (deal is over); None = no
    opinion (flag/key off, no ASIN/price, or the API failed).

    A used/open-box deal is checked against the Warehouse price; anything
    else against Amazon's own price or the lowest new offer.
    """
    if not cfg.feature_flags.get("keepa"):
        return None
    if not get_secret("KEEPA_KEY"):
        return None
    asin = str(deal.get("asin") or "").strip().upper()
    final = deal.get("final_price")
    if not asin or final is None:
        return None
    is_used = bool({"used", "open_box"} & set(deal.get("conditions") or []))
    try:
        import httpx

        resp = httpx.get(
            "https://api.keepa.com/product",
            params={"key": get_secret("KEEPA_KEY"), "domain": 1,
                    "asin": asin, "stats": 1},
            timeout=20,
        )
        resp.raise_for_status()
        products = resp.json().get("products") or []
        stats = (products[0] or {}).get("stats") if products else None
        current = stats.get("current") if isinstance(stats, dict) else None
        if not isinstance(current, list) or not current:
            return None
        idx = [9] if is_used else [0, 1]  # warehouse vs amazon/new offer
        prices = []
        for i in idx:
            if len(current) > i and isinstance(current[i], (int, float)) \
                    and current[i] >= 0:
                prices.append(float(current[i]) / 100.0)
        if not prices:
            return False  # no live offer of the right kind: deal is gone
        live = min(prices)
        ok = live <= float(final) * 1.10 + 0.01
        log.info("price recheck %s: final=$%.2f live=$%.2f (%s) -> %s",
                 asin, float(final), live,
                 "warehouse" if is_used else "new", ok)
        return ok
    except Exception as e:  # tokens exhausted, transport, schema drift
        log.warning("price recheck failed for %s: %s", asin, e)
        return None


def keepa_check(deal: dict[str, Any], cfg) -> bool | None:
    """Optional Keepa price-history cross-check (feature-flagged).

    Returns None (no opinion) unless cfg.feature_flags['keepa'] is on AND the
    KEEPA_KEY secret is set. When enabled it calls the official paid Keepa API
    with the deal's ASIN and compares the final price against Keepa's 90-day
    average Amazon price: True when the deal clears the publish floor against
    that average, False when it doesn't, None on any failure.

    Policy, non-negotiable:
    - Keepa is a PAID, official API; we only ever use it with a key.
    - NO CamelCamelCamel scraping (their ToS forbids it).
    - Retailer product pages are never fetched to verify a price.
    """
    if not cfg.feature_flags.get("keepa"):
        return None
    key = get_secret("KEEPA_KEY")
    if not key:
        log.debug("keepa flag on but KEEPA_KEY unset; skipping")
        return None
    asin = str(deal.get("asin") or "").strip().upper()
    final = deal.get("final_price")
    if not asin or final is None:
        return None
    try:
        import httpx

        resp = httpx.get(
            "https://api.keepa.com/product",
            params={"key": key, "domain": 1, "asin": asin, "stats": 90},
            timeout=20,
        )
        resp.raise_for_status()
        products = resp.json().get("products") or []
        product = (products[0] or {}) if products else {}
        # Free ride on the same response: the product's image filename on
        # Amazon's public CDN (no extra tokens, no image ever stored).
        if not deal.get("image_url"):
            csv = product.get("imagesCSV")
            if isinstance(csv, str) and csv.strip():
                first = csv.split(",")[0].strip()
                if first and "/" not in first:
                    deal["image_url"] = \
                        f"https://m.media-amazon.com/images/I/{first}"
        stats = product.get("stats")
        avg = None
        if isinstance(stats, dict):
            for field in ("avg90", "avg30", "avg"):
                vals = stats.get(field)
                if isinstance(vals, list) and vals:
                    v = vals[0]  # index 0 = Amazon price, integer cents
                    if isinstance(v, (int, float)) and v > 0:
                        avg = float(v) / 100.0
                        break
        if avg is None:
            return None
        verdict = float(final) <= _history_ratio(cfg) * avg
        log.info("keepa check %s: final=$%.2f avg90=$%.2f -> %s",
                 asin, float(final), avg, verdict)
        return verdict
    except Exception as e:  # tokens exhausted, transport, schema drift
        log.warning("keepa check failed for %s: %s", asin, e)
        return None
