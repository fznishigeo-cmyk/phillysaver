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
from .keepa_budget import (MIN_FOR_RECHECK, KeepaBudget, describe_429,
                           response_payload)
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
    # in the payload keeps hourly re-tiering from burning tokens), and only
    # while the shared token budget has a recheck's worth to spare — the
    # deal lane's /deal queries come first.
    if (deal.get("asin") and not deal.get("keepa_checked")
            and (deal.get("pct_at_checkout") or 0) >= cfg.min_pct):
        budget = KeepaBudget.load(db)
        if not budget.allows(MIN_FOR_RECHECK, now):
            return "unverified"  # retried on a later re-tiering
        verdict = keepa_check(deal, cfg, budget=budget)
        budget.save(db)
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


_IMAGE_NAME_RE = re.compile(r"[\w.,+%-]+")


def _keepa_image_name(product: dict[str, Any]) -> str:
    """The product's main image filename from a Keepa product record.
    Keepa deprecated ``imagesCSV`` for ``images`` (a list of {l: large
    filename, m: medium filename, ...}); live wart: 41 of 41 rechecked
    Amazon deals in one run came back photo-less because only imagesCSV
    was read. Both shapes are accepted; a path or junk is never used."""
    images = product.get("images")
    if isinstance(images, list):
        for img in images:
            name = ""
            if isinstance(img, dict):
                name = img.get("l") or img.get("m") or ""
            elif isinstance(img, str):
                name = img
            name = name.strip() if isinstance(name, str) else ""
            if name and "/" not in name and _IMAGE_NAME_RE.fullmatch(name):
                return name
    csv = product.get("imagesCSV")
    if isinstance(csv, str) and csv.strip():
        first = csv.split(",")[0].strip()
        if first and "/" not in first and _IMAGE_NAME_RE.fullmatch(first):
            return first
    return ""


def _harvest_image(deal: dict[str, Any], product: dict[str, Any]) -> None:
    """Free ride on any Keepa product response: the product's image filename
    on Amazon's public CDN (no extra tokens, no image ever stored by us)."""
    if deal.get("image_url"):
        return
    name = _keepa_image_name(product)
    if name:
        deal["image_url"] = f"https://m.media-amazon.com/images/I/{name}"


#: Stack-step wording whose discount Amazon's listed price never shows: the
#: shopper only sees it in the cart (clip coupon, promo code, Subscribe &
#: Save, checkout promo). A plain sale step IS the listed price.
_HIDDEN_PROMO_RE = re.compile(
    r"coupon|clip|subscribe|promo|\bcode\b|checkout|cart", re.I)


def _price_hidden_by_promo(deal: dict[str, Any]) -> bool:
    """True when the deal's final price only materialises at checkout, so
    a live LISTED price cannot be expected to match it."""
    if deal.get("codes"):
        return True
    if "subscribe_save" in set(deal.get("conditions") or []):
        return True
    return any(_HIDDEN_PROMO_RE.search(str(step))
               for step in (deal.get("stack_steps") or []))


#: Keepa's /product endpoint takes up to this many ASINs per call, one
#: token each: one request fetches a hundred photos or live prices.
KEEPA_BATCH = 100


def keepa_products(key: str, asins: list[str], stats: int,
                   budget: KeepaBudget | None) -> dict[str, dict[str, Any]] | None:
    """One Keepa /product call for up to KEEPA_BATCH ASINs: ``{asin:
    product}`` (an ASIN Keepa does not know is simply absent), or None on
    a 429 (the token bucket is empty — it refills by the minute and costs
    nothing, so that is "no opinion", never a verdict). Every response
    body, the 429 included, feeds the shared token budget when given."""
    import httpx

    asins = [a for a in asins if a][:KEEPA_BATCH]
    resp = httpx.get(
        "https://api.keepa.com/product",
        params={"key": key, "domain": 1, "asin": ",".join(asins),
                "stats": stats},
        timeout=30,
    )
    payload = response_payload(resp)
    if budget is not None:
        budget.note(payload)
    if resp.status_code == 429:
        log.warning("keepa product x%d: 429 Too Many Requests (%s); no "
                    "opinion until the bucket refills", len(asins),
                    describe_429(payload))
        return None
    resp.raise_for_status()
    products = (payload.get("products") if isinstance(payload, dict)
                else None) or []
    out: dict[str, dict[str, Any]] = {}
    for i, p in enumerate(products):
        if not isinstance(p, dict):
            continue
        asin = str(p.get("asin") or "").strip().upper()
        if not asin and len(asins) == 1:
            asin = asins[0]  # single-ASIN answers may omit the field
        if asin:
            out[asin] = p
    return out


def _keepa_product(key: str, asin: str, stats: int,
                   budget: KeepaBudget | None) -> dict[str, Any] | None:
    """One product, via the batch call (kept for the single-ASIN callers)."""
    products = keepa_products(key, [asin], stats, budget)
    if products is None:
        return None
    return products.get(asin, {})


def judge_live_price(deal: dict[str, Any], product: dict[str, Any]) -> bool | None:
    """The verdict for one deal given its Keepa product record: True =
    a current offer still matches the deal's final price within 10% (or,
    for a deal whose discount only shows at checkout — coupon, code,
    Subscribe & Save — the quoted regular price); False = the price moved
    (deal is over); None = no opinion (no current prices in the record).
    A used/open-box deal is checked against the Warehouse price; anything
    else against Amazon's own price or the lowest new offer. The record's
    image is harvested for free either way."""
    asin = str(deal.get("asin") or "").strip().upper()
    final = deal.get("final_price")
    is_used = bool({"used", "open_box"} & set(deal.get("conditions") or []))
    _harvest_image(deal, product)
    stats = product.get("stats")
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
    target = float(final)
    basis = "final"
    regular = deal.get("regular_price")
    if _price_hidden_by_promo(deal) and regular:
        # Live wart: every coupon / promo-code deal died at recheck
        # because the listing shows the pre-coupon price. For those the
        # deal is only over once the LISTING itself climbs past the
        # regular price the post quoted; the coupon is invisible here.
        target = max(target, float(regular))
        basis = "regular (promo hidden from listing)"
    ok = live <= target * 1.10 + 0.01
    log.info("price recheck %s: final=$%.2f live=$%.2f vs %s $%.2f (%s)"
             " -> %s", asin, float(final), live, basis, target,
             "warehouse" if is_used else "new", ok)
    return ok


def recheck_amazon_prices(deals: list[dict[str, Any]], cfg,
                          budget: KeepaBudget | None = None
                          ) -> dict[str, bool | None]:
    """Live price verdicts for many Amazon deals in as few Keepa calls as
    possible (KEEPA_BATCH ASINs per call): ``{dedupe_key: verdict}`` per
    judge_live_price; a deal left out (flag/key off, no ASIN or price,
    tokens exhausted, transport failure, unknown ASIN) has no entry.
    ``budget`` learns every response's token state."""
    out: dict[str, bool | None] = {}
    if not cfg.feature_flags.get("keepa"):
        return out
    key = get_secret("KEEPA_KEY")
    if not key:
        return out
    wanted: list[tuple[str, dict[str, Any]]] = []
    for deal in deals:
        asin = str(deal.get("asin") or "").strip().upper()
        if asin and deal.get("final_price") is not None:
            wanted.append((asin, deal))
    for start in range(0, len(wanted), KEEPA_BATCH):
        chunk = wanted[start:start + KEEPA_BATCH]
        try:
            products = keepa_products(key, [a for a, _ in chunk], 1, budget)
        except Exception as e:  # transport, schema drift
            log.warning("price recheck batch of %d failed: %s", len(chunk), e)
            continue
        if products is None:
            continue  # 429: the bucket is empty, try a later pass
        for asin, deal in chunk:
            product = products.get(asin)
            if product is None:
                continue
            out[deal["dedupe_key"]] = judge_live_price(deal, product)
    return out


def recheck_amazon_price(deal: dict[str, Any], cfg,
                         budget: KeepaBudget | None = None) -> bool | None:
    """One deal's live price verdict (see judge_live_price); None = no
    opinion (flag/key off, no ASIN/price, tokens exhausted, or the API
    failed). The pipeline uses the batched recheck_amazon_prices."""
    if not cfg.feature_flags.get("keepa"):
        return None
    if not get_secret("KEEPA_KEY"):
        return None
    asin = str(deal.get("asin") or "").strip().upper()
    final = deal.get("final_price")
    if not asin or final is None:
        return None
    try:
        product = _keepa_product(get_secret("KEEPA_KEY"), asin, 1, budget)
        if product is None:
            return None  # 429: the bucket is empty, try a later pass
        return judge_live_price(deal, product)
    except Exception as e:  # tokens exhausted, transport, schema drift
        log.warning("price recheck failed for %s: %s", asin, e)
        return None


def keepa_check(deal: dict[str, Any], cfg,
                budget: KeepaBudget | None = None) -> bool | None:
    """Optional Keepa price-history cross-check (feature-flagged).

    Returns None (no opinion) unless cfg.feature_flags['keepa'] is on AND the
    KEEPA_KEY secret is set. When enabled it calls the official paid Keepa API
    with the deal's ASIN and compares the final price against Keepa's 90-day
    average Amazon price: True when the deal clears the publish floor against
    that average, False when it doesn't, None on any failure (a 429 — empty
    token bucket — included). ``budget`` learns the response's token state.

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
        product = _keepa_product(key, asin, 90, budget)
        if product is None:
            return None  # 429: the bucket is empty, no opinion
        _harvest_image(deal, product)
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
