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
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

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


#: Keepa cross-checks per refresh_tiers pass: the pass has no deadline of
#: its own and runs before the critic and the price recheck, so it is
#: bounded (live review finding: hundreds of new open-box deals would each
#: cost a /product call and ~1.5 s per run).
KEEPA_CHECKS_PER_PASS = 25


def assign_tier(db, deal: dict[str, Any], cfg, now: datetime,
                keepa_calls: dict[str, int] | None = None) -> str:
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
    # Used/open-box deals are measured against the NEW price, not Amazon's
    # own 90-day average, so the cross-check has nothing to say about them
    # (live review finding: it never settled and re-asked Keepa every run).
    if (deal.get("asin") and not deal.get("keepa_checked")
            and not is_used_deal(deal)
            and (deal.get("pct_at_checkout") or 0) >= cfg.min_pct):
        if keepa_calls is not None:
            if keepa_calls.get("n", 0) >= KEEPA_CHECKS_PER_PASS:
                return "unverified"  # the rest wait for the next pass
            keepa_calls["n"] = keepa_calls.get("n", 0) + 1
        budget = KeepaBudget.load(db)
        if not budget.allows(MIN_FOR_RECHECK, now):
            return "unverified"  # retried on a later re-tiering
        answered: dict[str, bool] = {}
        verdict = keepa_check(deal, cfg, budget=budget, answered=answered)
        budget.save(db)
        if verdict is not None or answered.get("yes"):
            # Settled: a verdict, or Keepa answered with no Amazon average
            # to compare against (asking again would answer the same).
            deal["keepa_checked"] = True
            payload = {k: v for k, v in deal.items() if k != "id"}
            db.update_deal(deal["dedupe_key"], payload=payload)
            if verdict:
                return "history-checked"

    return "unverified"


def refresh_tiers(db, cfg, now: datetime) -> None:
    """Recompute and persist verify_tier for every alive deal."""
    keepa_calls: dict[str, int] = {}
    for deal in db.all_deals(status="alive"):
        tier = assign_tier(db, deal, cfg, now, keepa_calls=keepa_calls)
        if tier != deal.get("verify_tier"):
            log.info("deal %s tier %s -> %s", deal["dedupe_key"],
                     deal.get("verify_tier"), tier)
        db.update_deal(deal["dedupe_key"], verify_tier=tier)


#: Sources whose end date comes from the store's own API (Woot's EndDate):
#: a deal seen ONLY there ended for sure when it passes ("api_ended").
API_END_SOURCES = frozenset({"woot"})
CONFIRM_GONE_H = 1.0


def woot_offer_url(deal: dict[str, Any]) -> str:
    """The Woot offer identity, ignoring tracking query strings."""
    try:
        url = urlsplit(deal.get("canonical_url") or "")
        host = (url.hostname or "").lower()
        if (host == "woot.com" or host.endswith(".woot.com")) and url.path.startswith("/offers/"):
            return url.path.rstrip("/")
    except ValueError:
        pass
    return ""


def mark_unconfirmed_dead(db, deal: dict[str, Any], reason: str, now: datetime) -> bool:
    """Queue an API check when possible; otherwise hide the dead deal."""
    payload = dict(deal)
    if deal.get("asin") or woot_offer_url(deal):
        if deal.get("status") == "dead" or deal.get("gone_suspect_at"):
            return False
        payload.update(gone_suspect_at=iso(now), death=reason,
                       price_rechecked_at="", recheck_tried_at="")
        db.update_deal(deal["dedupe_key"], payload=payload)
        return False
    payload.update(died_at=iso(now), death=reason)
    db.update_deal(deal["dedupe_key"], payload=payload, status="dead")
    return True


def live_price_reading(payload: dict[str, Any], verdict: bool, now: datetime) -> str:
    """Apply an API reading to an alive payload; text never counts as a reading."""
    payload["price_rechecked_at"] = iso(now)
    payload["recheck_tried_at"] = ""
    if verdict:
        payload.update(gone_suspect_at="", death="", died_at="")
        return "live"
    suspect = parse_iso(payload.get("gone_suspect_at") or "")
    if suspect is None or payload.get("death") in {"post_says_over", "stated_expiry"}:
        payload.update(gone_suspect_at=iso(now), death="")
        return "suspect"
    if now - suspect < timedelta(hours=CONFIRM_GONE_H):
        return "waiting"
    payload.update(death="confirmed", gone_suspect_at="", died_at=iso(now))
    payload["flags"] = list(dict.fromkeys([*(payload.get("flags") or []), "price_moved"]))
    return "dead"


def mark_expired_dead(db, cfg, now: datetime) -> int:
    """End API-expired offers; recheck other expiries when possible, else hide."""
    n = 0
    for deal in db.all_deals(status="alive"):
        expires = deal.get("expires_at") or ""
        if not expires:
            continue
        dt = parse_iso(expires)
        if dt is not None and dt < now:
            payload = {k: v for k, v in deal.items()
                       if k not in ("id", "normalized_item")}
            seen = {s.get("source_name") for s in db.sightings_for(deal["id"])}
            if seen and seen <= API_END_SOURCES:
                payload.update(died_at=iso(now), death="api_ended")
                db.update_deal(deal["dedupe_key"], payload=payload, status="dead")
                n += 1
            else:
                checked = parse_iso(deal.get("price_rechecked_at") or "")
                # A live reading after this expiry has already overruled it.
                if checked is None or checked < dt:
                    n += mark_unconfirmed_dead(db, deal, "stated_expiry", now)
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


def is_used_deal(deal: dict[str, Any]) -> bool:
    """True for a used / open-box deal (Amazon Warehouse, Amazon Resale)."""
    return bool({"used", "open_box"} & set(deal.get("conditions") or []))


#: Keepa timestamps count minutes from 2011-01-01T00:00Z; that moment is
#: this many minutes after the unix epoch.
KEEPA_EPOCH_MINUTES = 21564000


def keepa_time(minutes: Any) -> datetime | None:
    """A Keepa timestamp (minutes since 2011-01-01 UTC) as an aware UTC
    datetime; None when missing or negative (Keepa's "no data" is -1)."""
    if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) \
            or minutes < 0:
        return None
    return datetime.fromtimestamp((minutes + KEEPA_EPOCH_MINUTES) * 60,
                                  tz=timezone.utc)


def _keepa_since_price(minutes: Any, deal: dict[str, Any]) -> bool | None:
    """Whether Keepa gathered its data (``minutes``, Keepa time) at or after
    the deal's current price stood: the later of its first sighting and
    ``price_since`` (a price drop or a revival; older data cannot know of
    either). None when Keepa's time or both deal times are unknown."""
    at = keepa_time(minutes)
    since = [t for t in (parse_iso(deal.get("first_seen_at") or ""),
                         parse_iso(deal.get("price_since") or ""))
             if t is not None]
    if at is None or not since:
        return None
    return at >= max(since)


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
#: Marketplace offers asked for on a used/open-box recheck (Keepa's
#: minimum for the ``offers`` parameter). Only an offers scan refreshes
#: the Warehouse/used price slots.
RECHECK_OFFERS = 20
#: Keepa takes at most this many ASINs per /product call that asks for
#: offers (a longer list is rejected outright).
KEEPA_OFFERS_BATCH = 20
#: Worst-case tokens per ASIN in a used recheck: 1 for the product plus 6
#: per page of up to 10 offers found. Sizes the used batches.
USED_RECHECK_TOKENS = 1 + 6 * math.ceil(RECHECK_OFFERS / 10)


def keepa_products(key: str, asins: list[str], stats: int,
                   budget: KeepaBudget | None,
                   offers: int | None = None) -> dict[str, dict[str, Any]] | None:
    """One Keepa /product call for up to KEEPA_BATCH ASINs (KEEPA_OFFERS_BATCH
    with ``offers``): ``{asin: product}`` (an ASIN Keepa does not know is
    simply absent), or None on a 429 (the token bucket is empty — it
    refills by the minute and costs nothing, so that is "no opinion", never
    a verdict). ``offers`` asks for that many live marketplace offers per
    product (pricier). Every response body, the 429 included, feeds the
    shared token budget when given."""
    import httpx

    asins = [a for a in asins if a][
        :KEEPA_BATCH if offers is None else KEEPA_OFFERS_BATCH]
    params: dict[str, Any] = {"key": key, "domain": 1,
                              "asin": ",".join(asins), "stats": stats}
    if offers is not None:
        params["offers"] = offers
    resp = httpx.get("https://api.keepa.com/product", params=params,
                     timeout=30)
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


#: Keepa offer conditions that count as used / open-box: 2-5 Used (Like
#: New .. Acceptable) and 6 Refurbished. 1 is New, 0 unknown, 7+ collectible.
_USED_OFFER_CONDITIONS = frozenset(range(2, 7))


def _current_price(current: Any, i: int) -> float | None:
    """stats.current[i] in dollars; None when absent or -1 (no offer)."""
    if isinstance(current, list) and len(current) > i \
            and isinstance(current[i], (int, float)) and current[i] >= 0:
        return float(current[i]) / 100.0
    return None


def _offer_total(offer: dict[str, Any]) -> float | None:
    """One marketplace offer's current price plus shipping, in dollars:
    the last [keepaTime, price, shipping] triple of its offerCSV (shipping
    -1 = none stated, counted as free). None when the price is unknown."""
    csv = offer.get("offerCSV")
    if not isinstance(csv, list) or len(csv) < 3 or len(csv) % 3:
        return None
    price, ship = csv[-2], csv[-1]
    if not isinstance(price, (int, float)) or price < 0:
        return None
    if not isinstance(ship, (int, float)) or ship < 0:
        ship = 0
    return (float(price) + float(ship)) / 100.0


def _judge_used(asin: str, deal: dict[str, Any], product: dict[str, Any],
                stats: dict[str, Any]) -> bool | None:
    """Verdict for a used/open-box deal. Live wart: five Amazon Warehouse
    deals died within hours of being seen and were still on sale — a plain
    /product record showed the Warehouse slot at -1, but Keepa refreshes
    the Warehouse and used slots only on an offers scan. So only a fresh
    offers scan (taken since the deal's price stood) without a matching
    used offer may call the unit sold; a stats-only record can confirm a
    deal (True) and never kills one. A checkout-only discount (coupon,
    code, "extra 20% at checkout") is matched like judge_live_price does,
    against the quoted regular price, and any live used offer then
    keeps it; so does a live used offer whose price Keepa could not read."""
    final = float(deal["final_price"])
    regular = deal.get("regular_price")
    hidden = _price_hidden_by_promo(deal)
    target = max(final, float(regular)) if hidden and regular else final
    cap = target * 1.10 + 0.01
    offers, order = product.get("offers"), product.get("liveOffersOrder")
    if isinstance(offers, list) and isinstance(order, list):
        totals = [_offer_total(offers[i]) for i in order
                  if isinstance(i, int) and 0 <= i < len(offers)
                  and isinstance(offers[i], dict)
                  and offers[i].get("condition") in _USED_OFFER_CONDITIONS]
        live = min((t for t in totals if t is not None), default=None)
        if live is not None and live <= cap:
            log.info("price recheck %s: final=$%.2f live=$%.2f (used offer)"
                     " -> True", asin, final, live)
            return True
        seen = ("no live used offer" if live is None
                else f"cheapest live used offer ${live:.2f}")
        if None in totals or (hidden and live is not None):
            # A live unit is listed: at a price Keepa could not read (-2),
            # or above a price only the checkout shows. Never "sold".
            log.debug("price recheck %s: final=$%.2f, %s%s; no opinion", asin,
                      final, seen, " (and an unreadable price)"
                      if None in totals else ", checkout promo hidden")
            return None
        scanned = stats.get("lastOffersUpdate")
        # offersSuccessful=false: Keepa's scan failed, so it proves nothing.
        if _keepa_since_price(scanned, deal) \
                and product.get("offersSuccessful") is not False:
            log.info("price recheck %s: final=$%.2f, offer scan at %s shows "
                     "%s -> False", asin, final, iso(keepa_time(scanned)), seen)
            return False
        log.debug("price recheck %s: final=$%.2f, %s but no successful offer "
                  "scan since the price stood (scan at %s); no opinion", asin,
                  final, seen, iso(keepa_time(scanned)) or "unknown")
        return None
    current = stats.get("current")
    prices = [p for p in (_current_price(current, 9), _current_price(current, 2))
              if p is not None]
    live = min(prices, default=None)
    if live is not None and live <= cap:
        log.info("price recheck %s: final=$%.2f live=$%.2f (warehouse/used)"
                 " -> True", asin, final, live)
        return True
    log.debug("price recheck %s: final=$%.2f, stats-only record (%s) cannot "
              "show a sold unit; no opinion", asin, final,
              "no warehouse/used price" if live is None else f"${live:.2f}")
    return None


def judge_live_price(deal: dict[str, Any], product: dict[str, Any]) -> bool | None:
    """The verdict for one deal given its Keepa product record: True =
    a current offer still matches the deal's final price within 10% (or,
    for a deal whose discount only shows at checkout — coupon, code,
    Subscribe & Save — the quoted regular price); False = the price moved
    or no offer is left (deal is over); None = no opinion. A used/open-box
    deal is judged by _judge_used (live offers when the record carries
    them, else the Warehouse/used price, which can only confirm). Anything
    else is checked against Amazon's own price or the lowest new offer,
    and a False needs Keepa data (lastUpdate) gathered since the deal's
    price stood (first seen, or a later drop/revival: price_since): older
    data cannot know the deal ended. The record's image is harvested for
    free either way."""
    asin = str(deal.get("asin") or "").strip().upper()
    final = deal.get("final_price")
    _harvest_image(deal, product)
    stats = product.get("stats")
    if is_used_deal(deal):
        return _judge_used(asin, deal, product,
                           stats if isinstance(stats, dict) else {})
    current = stats.get("current") if isinstance(stats, dict) else None
    if not isinstance(current, list) or not current:
        return None
    prices = [p for p in (_current_price(current, 0), _current_price(current, 1))
              if p is not None]
    updated = product.get("lastUpdate")
    # Only a False verdict needs fresh data; unknown times apply no gate.
    stale = _keepa_since_price(updated, deal) is False
    if not prices:
        if stale:
            log.debug("price recheck %s: no live new offer in Keepa data from "
                      "%s, before the price stood (%s); no opinion", asin,
                      iso(keepa_time(updated)),
                      deal.get("price_since") or deal.get("first_seen_at"))
            return None
        log.info("price recheck %s: final=$%.2f, no live new offer -> False",
                 asin, float(final))
        return False
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
    if not ok and stale:
        log.debug("price recheck %s: final=$%.2f live=$%.2f in Keepa data from "
                  "%s, before the price stood (%s); no opinion", asin,
                  float(final), live, iso(keepa_time(updated)),
                  deal.get("price_since") or deal.get("first_seen_at"))
        return None
    log.info("price recheck %s: final=$%.2f live=$%.2f vs %s $%.2f (new)"
             " -> %s", asin, float(final), live, basis, target, ok)
    return ok


def recheck_amazon_prices(deals: list[dict[str, Any]], cfg,
                          budget: KeepaBudget | None = None,
                          used_offers: bool = True
                          ) -> dict[str, bool | None]:
    """Live price verdicts for many Amazon deals in as few Keepa calls as
    possible (KEEPA_BATCH ASINs per call): ``{dedupe_key: verdict}`` per
    judge_live_price; a deal left out (flag/key off, no ASIN or price,
    tokens exhausted, transport failure, unknown ASIN) has no entry.
    Used/open-box deals go in batches of their own (KEEPA_OFFERS_BATCH
    ASINs) that ask for RECHECK_OFFERS live offers (only an offers scan can
    show a Warehouse unit sold); ``used_offers=False`` keeps every batch
    stats-only, where a used deal can only be confirmed. ``budget`` learns
    every response's token state."""
    out: dict[str, bool | None] = {}
    if not cfg.feature_flags.get("keepa"):
        return out
    key = get_secret("KEEPA_KEY")
    if not key:
        return out
    new: list[tuple[str, dict[str, Any]]] = []
    used: list[tuple[str, dict[str, Any]]] = []
    for deal in deals:
        asin = str(deal.get("asin") or "").strip().upper()
        if asin and deal.get("final_price") is not None:
            (used if used_offers and is_used_deal(deal) else new).append(
                (asin, deal))
    for wanted, offers, per_call in ((new, None, KEEPA_BATCH),
                                     (used, RECHECK_OFFERS, KEEPA_OFFERS_BATCH)):
        for start in range(0, len(wanted), per_call):
            chunk = wanted[start:start + per_call]
            try:
                products = keepa_products(key, [a for a, _ in chunk], 1,
                                          budget, offers=offers)
            except Exception as e:  # transport, schema drift
                log.warning("price recheck batch of %d failed: %s",
                            len(chunk), e)
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
                budget: KeepaBudget | None = None,
                answered: dict[str, bool] | None = None) -> bool | None:
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
        if answered is not None:
            answered["yes"] = True  # Keepa answered (even if inconclusive)
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
