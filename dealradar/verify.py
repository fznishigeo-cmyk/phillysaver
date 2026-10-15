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

Verification NEVER fetches retailer pages. External price checks use official
keyed APIs only: Keepa below, Best Buy and eBay in store_prices.py.
"""

from __future__ import annotations

import logging
import math
import re
import time
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from .config import get_secret, error_summary, load_config
from . import util
from .db import DB
from .http_client import PoliteFetcher
from .conditions import USED_CONDITIONS, normalize_conditions
from .keepa_budget import (MIN_FOR_DEAL_QUERY, MIN_FOR_RECHECK, KeepaBudget, describe_429,
                           response_payload)
from .util import iso, parse_iso

log = logging.getLogger("dealradar.verify")

_DEAD_RE = re.compile(
    r"\b(expired|deal\s+is\s+dead|dead\s+deal|sold\s+out|out\s+of\s+stock|oos"
    r"|no\s+longer\s+available|price\s+went\s+up)\b",
    re.IGNORECASE)


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
                keepa_calls: dict[str, int] | None = None, *,
                fetcher: PoliteFetcher | None = None) -> str:
    """Compute the verification tier for one deal row dict."""
    since = now - timedelta(hours=cfg.corroborate_window_h)
    if db.distinct_sources_within(deal["id"], since, deal.get("final_price"), now) >= 2:
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
        verdict = keepa_check(deal, cfg, budget=budget, answered=answered,
                             fetcher=fetcher)
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


def refresh_tiers(db, cfg, now: datetime, *, fetcher: PoliteFetcher | None = None) -> None:
    """Recompute and persist verify_tier for every alive deal."""
    keepa_calls: dict[str, int] = {}
    for deal in db.all_deals(status="alive"):
        tier = assign_tier(db, deal, cfg, now, keepa_calls=keepa_calls, fetcher=fetcher)
        if tier != deal.get("verify_tier"):
            log.info("deal %s tier %s -> %s", deal["dedupe_key"],
                     deal.get("verify_tier"), tier)
        db.update_deal(deal["dedupe_key"], verify_tier=tier)


def audit_verdict(db, deal: dict[str, Any], cfg, now: datetime) -> dict[str, str]:
    """Legitimacy from stored facts only. Absence of evidence is not a rejection.

    History records observed offers, not purchases. Corroboration supports the
    checkout price only; it cannot by itself authenticate a regular price.
    """
    from .savings import clears_reference_floor

    def verdict(outcome, reason):
        return {"outcome": outcome, "reason": reason}

    flags = set(deal.get("flags") or [])
    if flags & {"unit_mismatch", "pack_mismatch"}:
        return verdict("reject", "unit_pack_mismatch")
    regular, final = deal.get("regular_price"), deal.get("final_price")
    if any(isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p)
           for p in (regular, final)) or regular <= 0 or final < 0:
        return verdict("needs_critic", "missing_price_basis")
    # History has no condition/unit columns. Never compare a used unit or
    # per-unit/range claim with observations of an unknown pack or condition.
    if is_used_deal(deal):
        return verdict("needs_critic", "used_comparison")
    if flags & {"per_unit", "range"} or deal.get("price_is_range") or deal.get("quantity_or_unit"):
        return verdict("needs_critic", "unit_or_range_uncertain")

    reference = deal.get("audit_reference") or {}
    label = str(deal.get("regular_price_basis") or "")
    labels = {"List Price on Amazon", "30-day average on Amazon", "90-day average on Amazon"}
    amazon_basis = None
    if is_amazon_deal(deal) and deal.get("asin"):
        if (reference.get("asin") == deal["asin"] and reference.get("basis") in labels
                and reference.get("condition") == "new"):
            price = reference.get("price")
            if type(price) in (float, int) and math.isfinite(price) and price > 0:
                amazon_basis = price
        elif not reference and label in labels and (stamp := parse_iso(deal.get("keepa_price_seen_at") or "")) \
                and stamp <= now:
            # Legacy rows: this label is written only by the licensed adapter
            # and cleared on a later partner price. The value is regular_price.
            amazon_basis = regular
    if amazon_basis is not None and regular > amazon_basis * (1 + cfg.audit_reference_max_excess_pct / 100):
        return verdict("reject", "amazon_reference_exceeded")

    text = " ".join([str(deal.get("item") or ""), *(deal.get("stack_steps") or [])])
    clearance = bool(re.search(r"\b(?:clearance|closeout|liquidation)\b", text, re.I))
    if (deal.get("pct_at_checkout") or 0) > cfg.audit_extreme_pct and not clearance:
        return verdict("needs_critic", "extreme_new_discount")

    if amazon_basis is not None and clears_reference_floor(final, amazon_basis, cfg.min_pct):
        return verdict("verified", "amazon_reference")

    rows = db.price_observations(deal["dedupe_key"], datetime.min.replace(tzinfo=timezone.utc), now)
    prices = [r for r in rows if type(r["price"]) in (int, float)
              and math.isfinite(r["price"]) and r["price"] > 0]
    near = [r for r in prices if abs(r["price"] - regular) <= regular * cfg.audit_near_price_pct / 100]
    # Require repeated support spread over time; repeated feeds on one day
    # cannot manufacture a historical basis.
    if len(prices) >= cfg.history_min_observations and len(near) >= cfg.audit_history_min_near:
        span = (parse_iso(near[-1]["observed_at"]) - parse_iso(near[0]["observed_at"])).total_seconds() / 3600
        basis = min(r["price"] for r in near)
        if span >= cfg.audit_history_min_span_h and clears_reference_floor(final, basis, cfg.min_pct):
            return verdict("verified", "recorded_regular_price")

    corroborated = db.distinct_sources_within(
        deal["id"], now - timedelta(hours=cfg.corroborate_window_h), final, now,
        tolerance_pct=0) >= 2
    if corroborated:
        return verdict("needs_critic", "corroborated_price_only")
    if not near and math.isclose(regular % cfg.audit_round_price_multiple, 0, abs_tol=1e-9):
        return verdict("needs_critic", "round_regular_without_history")
    return verdict("needs_critic", "unsupported_regular_price")


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


def mark_unconfirmed_dead(db, deal: dict[str, Any], reason: str, now: datetime, cfg=None) -> bool:
    """Queue an API check when possible; otherwise hide the dead deal."""
    if deal.get("status") == "dead":
        return False
    payload = dict(deal)
    from . import checkout_prices
    if reason == "post_says_over" and checkout_prices.enabled(cfg) and _price_hidden_by_promo(deal):
        payload["checkout_source_ended_at"] = iso(now)
    from .store_prices import enabled, store_offer
    store = store_offer(deal)[0]
    store_check = (cfg is not None and enabled(store, cfg)
                   and not (store == "bestbuy" and is_used_deal(deal)))
    if (is_amazon_deal(deal) and deal.get("asin")) or woot_offer_url(deal) or store_check:
        if deal.get("gone_suspect_at"):
            if payload.get("checkout_source_ended_at") != deal.get("checkout_source_ended_at"):
                db.update_deal(deal["dedupe_key"], payload=payload)
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
                    n += mark_unconfirmed_dead(db, deal, "stated_expiry", now, cfg)
    if n:
        log.info("marked %d expired deal(s) dead", n)
    return n


def text_marks_dead(text: str) -> bool:
    """Explicit end wording, on word boundaries; bare 'dead' is not enough."""
    return bool(_DEAD_RE.search(text or ""))


def post_marks_dead(post, extracted) -> bool:
    """Only the title or this deal's verbatim final-price evidence line counts."""
    if text_marks_dead(post.title):
        return True
    quote = extracted.evidence.final_price_quote
    if not quote:
        return False
    start = post.text.find(quote)
    while start >= 0:
        left = post.text.rfind("\n", 0, start) + 1
        right = post.text.find("\n", start + len(quote))
        if text_marks_dead(post.text[left:right if right >= 0 else len(post.text)]):
            return True
        start = post.text.find(quote, start + 1)
    return False


def mark_ended_post(db, post, now: datetime, cfg=None) -> None:
    """An updated single-product title can end its old sighting without a parse."""
    if not text_marks_dead(post.title):
        return
    deals = db.deals_for_post(post.source_name, post.url)
    if len(deals) != 1:
        return  # a roundup's title does not identify which product ended
    deal = deals[0]
    price_at = parse_iso(deal.get("price_published_at") or deal.get("last_seen_at") or "")
    post_at = post.published_at or post.fetched_at
    if price_at is None or post_at >= price_at:
        mark_unconfirmed_dead(db, deal, "post_says_over", now, cfg)


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
    return bool(USED_CONDITIONS & set(normalize_conditions(deal.get("conditions"))))


def is_amazon_deal(deal: dict[str, Any]) -> bool:
    """Amazon prices only; an ASIN attached to another store is not enough."""
    if woot_offer_url(deal):
        return False
    store = str(deal.get("store") or "").strip().lower()
    try:
        host = (urlsplit(deal.get("canonical_url") or "").hostname or "").lower()
    except ValueError:
        host = ""
    return store.startswith("amazon") or host == "amazon.com" or host.endswith(".amazon.com")


def price_confirmed_at(deal: dict[str, Any]) -> datetime | None:
    """Last confirmation of the CURRENT price; a gone reading is no proof."""
    if deal.get("gone_suspect_at") or deal.get("death"):
        return None
    since = parse_iso(deal.get("price_since") or "")
    stamps = [parse_iso(deal.get(name) or "")
              for name in ("price_rechecked_at", "keepa_price_seen_at")]
    # Legacy rows lack the field. An explicit empty field means the feed
    # supplied no usable data time: fetch/sighting time cannot replace it.
    if "keepa_price_seen_at" not in deal:
        for sighting in deal.get("sightings") or []:
            if str(sighting.get("source_name") or "").lower().startswith("keepa") \
                    and sighting.get("final_price") == deal.get("final_price"):
                stamps.append(parse_iso(sighting.get("seen_at") or ""))
        if str(deal.get("source_name") or "").lower().startswith("keepa"):
            stamps.append(parse_iso(deal.get("first_seen_at") or ""))
    valid = [at for at in stamps if at is not None and (since is None or at >= since)]
    return max(valid) if valid else None


def price_confirmation_recent(deal: dict[str, Any], now: datetime, hours: float) -> bool:
    at = price_confirmed_at(deal)
    return at is not None and timedelta() <= now - at <= timedelta(hours=hours)


def price_unconfirmed(deal: dict[str, Any], cfg, now: datetime) -> bool:
    """Hide stale Amazon offers; never-read posts get only the initial grace."""
    if not is_amazon_deal(deal):
        return False
    if cfg.keepa_buybox_rule == "on":
        from .buybox import would_unconfirm
        if would_unconfirm(deal, now):
            return True
    at = price_confirmed_at(deal) or parse_iso(deal.get("first_seen_at") or "")
    hours = (cfg.keepa_show_max_unconfirmed_used_h if is_used_deal(deal)
             else cfg.keepa_show_max_unconfirmed_h)
    return at is None or at > now or now - at > timedelta(hours=hours)


def confirmed_age(deals: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Shares of the whole Amazon set, including prices with no confirmation."""
    amazon = [d for d in deals if is_amazon_deal(d) and d.get("status", "alive") == "alive"]
    ages = [(now - at).total_seconds() / 3600 for d in amazon
            if (at := price_confirmed_at(d)) is not None and at <= now]
    result = {"amazon": len(amazon), "unconfirmed": len(amazon) - len(ages),
            **{f"within_{h}h": sum(age <= h for age in ages) / len(amazon)
               if amazon else None for h in (1, 2, 3, 12)},
            "oldest_h": round(max(ages), 2) if ages else None}
    return result


def confirmation_coverage(deals: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Whole-set and condition-specific shares use the same confirmation rules."""
    return {**confirmed_age(deals, now),
            "new": confirmed_age([d for d in deals if not is_used_deal(d)], now),
            "used": confirmed_age([d for d in deals if is_used_deal(d)], now)}


#: Keepa timestamps count minutes from 2011-01-01T00:00Z; that moment is
#: this many minutes after the unix epoch.
KEEPA_EPOCH_MINUTES = 21564000


def keepa_time(minutes: Any) -> datetime | None:
    """A Keepa timestamp (minutes since 2011-01-01 UTC) as an aware UTC
    datetime; None when missing or negative (Keepa's "no data" is -1)."""
    if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) \
            or not math.isfinite(minutes) or minutes < 0:
        return None
    try:
        return datetime.fromtimestamp((minutes + KEEPA_EPOCH_MINUTES) * 60,
                                      tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


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


def keepa_observed_at(product: dict[str, Any] | None, now: datetime, *, used=False) -> datetime:
    """Use the observation behind the verdict, never a future confirmation."""
    product = product or {}
    stats = product.get("stats")
    stats = stats if isinstance(stats, dict) else {}
    at = None
    if used and isinstance(product.get("offers"), list) and isinstance(product.get("liveOffersOrder"), list):
        at = keepa_time(stats.get("lastOffersUpdate"))
    at = at or keepa_time(product.get("lastUpdate"))
    return min(at, now) if at is not None else now


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


@lru_cache(maxsize=1)
def _standalone_keepa_fetcher() -> PoliteFetcher:
    return PoliteFetcher(DB(":memory:"), load_config())


def keepa_products(key: str, asins: list[str], stats: int,
                   budget: KeepaBudget | None,
                   offers: int | None = None, *,
                   live: bool = False,
                   buybox: bool = False, conditional_live: bool = False,
                   fetcher: PoliteFetcher | None = None) -> dict[str, dict[str, Any]] | None:
    """One Keepa /product call for up to KEEPA_BATCH ASINs (KEEPA_OFFERS_BATCH
    with ``offers``): ``{asin: product}`` (an ASIN Keepa does not know is
    simply absent), or None on a 429 (the token bucket is empty — it
    refills by the minute and costs nothing, so that is "no opinion", never
    a verdict). ``offers`` asks for that many live marketplace offers per
    product (pricier). Every response body, the 429 included, feeds the
    shared token budget when given."""
    asins = [a for a in asins if a][
        :KEEPA_BATCH if offers is None else KEEPA_OFFERS_BATCH]
    # history=0 drops the price-history arrays (unused; same token cost):
    # with them a batch could pass the 5 MB body cap, and a cut-off answer
    # paid for every ASIN in it and returned none (live, 2026-10-14).
    params: dict[str, Any] = {"key": key, "domain": 1,
                              "asin": ",".join(asins), "stats": stats,
                              "history": 0}
    if offers is not None:
        params["offers"] = offers
    if live:
        params["update"] = 1 if conditional_live else 0
    if buybox:
        params["buybox"] = 1
    fetcher = fetcher if fetcher is not None else _standalone_keepa_fetcher()
    # Keyed API (robots.txt is for crawlers); a 429 is an empty token bucket,
    # answered next pass, so it is not retried here.
    reserved = len(asins) * ((USED_RECHECK_TOKENS if offers else 1) + int(live) + 2 * buybox)
    if budget is not None:
        budget.reserve(reserved)
    payload, resp = None, None
    started = time.monotonic()
    try:
        resp = fetcher.api_get("https://api.keepa.com/product", params=params,
                               max_attempts=1, respect_robots=False)
        payload = response_payload(resp)
    finally:
        if budget is not None:
            budget.usage.note(payload, estimate=reserved,
                              rate_limited=resp is not None and resp.status_code == 429)
            from .keepa_rechecks import CallHistory
            tokens = payload.get("tokensConsumed") if isinstance(payload, dict) else None
            if (not isinstance(tokens, (int, float)) or isinstance(tokens, bool)
                    or not math.isfinite(tokens) or tokens < 0
                    or resp is None or resp.status_code != 200):
                tokens = None
            CallHistory(budget.db, util.now_utc()).record(
                used=offers is not None, live=live, count=len(asins),
                seconds=time.monotonic() - started, tokens=tokens, buybox=buybox)
    if budget is not None:
        budget.note(payload)
    if resp.status_code == 429:
        log.warning("keepa product x%d: 429 Too Many Requests (%s); no "
                    "opinion until the bucket refills", len(asins),
                    describe_429(payload))
        return None
    resp.raise_for_status()
    if not isinstance(payload, dict):
        log.warning("keepa product x%d: unreadable answer (%d bytes); no "
                    "opinion this pass", len(asins), len(resp.content or b""))
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
    if budget is not None:
        from .keepa_categories import remember_roots
        remember_roots(budget.db, [dict(p, asin=a) for a, p in out.items()])
    return out


def _keepa_product(key: str, asin: str, stats: int,
                   budget: KeepaBudget | None, *,
                   fetcher: PoliteFetcher | None = None) -> dict[str, Any] | None:
    """One product, via the batch call (kept for the single-ASIN callers)."""
    products = keepa_products(key, [asin], stats, budget, fetcher=fetcher)
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


def _live_new_price(product: dict[str, Any]) -> float | None:
    """The same new-condition listing price for the judge and drift report."""
    stats = product.get("stats")
    current = stats.get("current") if isinstance(stats, dict) else None
    return min((p for i in (0, 1) if (p := _current_price(current, i)) is not None),
               default=None)


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


def judge_live_price(deal: dict[str, Any], product: dict[str, Any],
                     tolerance_pct: float = 10.0) -> bool | None:
    """The verdict for one deal given its Keepa product record: True =
    a current offer still matches the deal's final price within
    ``tolerance_pct`` (config keepa.recheck_tolerance_pct) (or,
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
    live = _live_new_price(product)
    updated = product.get("lastUpdate")
    # Only a False verdict needs fresh data; unknown times apply no gate.
    stale = _keepa_since_price(updated, deal) is False
    if live is None:
        if stale:
            log.debug("price recheck %s: no live new offer in Keepa data from "
                      "%s, before the price stood (%s); no opinion", asin,
                      iso(keepa_time(updated)),
                      deal.get("price_since") or deal.get("first_seen_at"))
            return None
        log.info("price recheck %s: final=$%.2f, no live new offer -> False",
                 asin, float(final))
        return False
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
    ok = live <= target * (1 + tolerance_pct / 100.0) + 0.01
    if not ok and stale:
        log.debug("price recheck %s: final=$%.2f live=$%.2f in Keepa data from "
                  "%s, before the price stood (%s); no opinion", asin,
                  float(final), live, iso(keepa_time(updated)),
                  deal.get("price_since") or deal.get("first_seen_at"))
        return None
    log.info("price recheck %s: final=$%.2f live=$%.2f vs %s $%.2f (new)"
             " -> %s", asin, float(final), live, basis, target, ok)
    return ok


class PriceRechecks(dict):
    """Verdict map carrying the same paid products for evidence and drift."""

    def __init__(self):
        super().__init__()
        self.products: dict[str, dict[str, Any]] = {}
        self.costs: dict[str, tuple[float, float]] = {}
        self.rate_limited = False


def recheck_amazon_prices(deals: list[dict[str, Any]], cfg,
                          budget: KeepaBudget | None = None,
                          used_offers: bool = True, *,
                          live: bool = False,
                          buybox: bool = False, conditional_live: bool = False,
                          fetcher: PoliteFetcher | None = None
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
    out = PriceRechecks()
    if not cfg.feature_flags.get("keepa"):
        return out
    key = get_secret("KEEPA_KEY")
    if not key:
        return out
    budget = budget if budget is not None else KeepaBudget()
    new: list[tuple[str, dict[str, Any]]] = []
    used: list[tuple[str, dict[str, Any]]] = []
    for deal in deals:
        asin = str(deal.get("asin") or "").strip().upper()
        if asin and deal.get("final_price") is not None:
            (used if used_offers and is_used_deal(deal) else new).append(
                (asin, deal))
    for wanted, offers, per_call in ((new, None, KEEPA_BATCH),
                                     (used, RECHECK_OFFERS, KEEPA_OFFERS_BATCH)):
        start = 0
        while start < len(wanted):
            cost = (USED_RECHECK_TOKENS if offers else 1) + int(live) + 2 * buybox
            avail = budget.available()
            if not budget.allows(max(MIN_FOR_RECHECK, MIN_FOR_DEAL_QUERY + cost)):
                break
            size = per_call if avail is None else min(
                per_call, int((avail - MIN_FOR_DEAL_QUERY) / cost))
            chunk = wanted[start:start + size]
            start += len(chunk)
            before = budget.usage.accounted, budget.usage.estimated
            try:
                options = {"live": True} if live else {}
                if buybox:
                    options["buybox"] = True
                if conditional_live:
                    options["conditional_live"] = True
                products = keepa_products(key, [a for a, _ in chunk], 1,
                                          budget, offers=offers, fetcher=fetcher, **options)
            except Exception as e:  # transport, schema drift
                log.warning("price recheck batch of %d failed: %s",
                            len(chunk), error_summary(e))
                continue
            finally:
                for _, deal in chunk:
                    out.costs[deal["dedupe_key"]] = (
                        (budget.usage.accounted - before[0]) / len(chunk),
                        (budget.usage.estimated - before[1]) / len(chunk))
            if products is None:
                out.rate_limited = True
                return out  # even a malformed 429 ends every batch in this pass
            for asin, deal in chunk:
                product = products.get(asin)
                if product is None:
                    continue
                out.products[deal["dedupe_key"]] = product
                out[deal["dedupe_key"]] = judge_live_price(
                    deal, product, getattr(cfg, "keepa_recheck_tolerance_pct", 10.0))
    return out


def recheck_amazon_price(deal: dict[str, Any], cfg,
                         budget: KeepaBudget | None = None, *,
                         fetcher: PoliteFetcher | None = None) -> bool | None:
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
        product = _keepa_product(get_secret("KEEPA_KEY"), asin, 1, budget, fetcher=fetcher)
        if product is None:
            return None  # 429: the bucket is empty, try a later pass
        return judge_live_price(deal, product,
                                getattr(cfg, "keepa_recheck_tolerance_pct", 10.0))
    except Exception as e:  # tokens exhausted, transport, schema drift
        log.warning("price recheck failed for %s: %s", asin, error_summary(e))
        return None


def keepa_check(deal: dict[str, Any], cfg,
                budget: KeepaBudget | None = None,
                answered: dict[str, bool] | None = None, *,
                fetcher: PoliteFetcher | None = None) -> bool | None:
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
        product = _keepa_product(key, asin, 90, budget, fetcher=fetcher)
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
        log.warning("keepa check failed for %s: %s", asin, error_summary(e))
        return None
