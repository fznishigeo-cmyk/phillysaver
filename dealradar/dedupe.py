"""Dedupe/merge layer: canonical keys, fuzzy matching, idempotent upserts.

A deal is identified by its dedupe_key:
- 'url|<canonical_url>' when the pipeline resolved a canonical product URL;
- 'si|<store>|<normalized item>' otherwise (store + normalized item name).

Merging keeps the best (lowest) observed final price and its matching savings
fields, unions list facts (codes, conditions, rebates, stack steps), and keeps
the earliest expiry. Sightings and price history are anchored on the post's
content_hash, so re-running the pipeline over the same posts adds no rows.

Note on canonical_url fill-in: the frozen DB contract offers no method to
update the deals.canonical_url column, so a later-resolved URL is filled into
the stored payload only (the column keeps its insert-time value).
"""

from __future__ import annotations

import logging
from dataclasses import fields as dataclass_fields
from datetime import datetime
from typing import Any

from rapidfuzz import fuzz

from . import models, util

log = logging.getLogger("dealradar.dedupe")

_DEAL_FIELDS = tuple(f.name for f in dataclass_fields(models.Deal))


def canonical_key(store: str, canonical_url: str, item: str) -> str:
    """Stable dedupe key: canonical URL when known, else store + normalized item."""
    if canonical_url:
        return "url|" + canonical_url
    return "si|" + store.lower().strip() + "|" + util.normalize_item(item)


def find_existing(db, store: str, canonical_url: str, item: str,
                  fuzzy_threshold: float) -> dict[str, Any] | None:
    """Locate an existing deal row for (store, canonical_url, item).

    Lookup order: exact dedupe_key, then canonical URL, then fuzzy item-name
    match within the same store (token-sort ratio on normalized names; the
    best match at or above the threshold wins).
    """
    hit = db.get_deal(canonical_key(store, canonical_url, item))
    if hit is not None:
        return hit

    hit = db.deal_by_canonical_url(canonical_url)
    if hit is not None:
        return hit

    norm = util.normalize_item(item)
    if not norm:
        return None
    cutoff = fuzzy_threshold * 100.0
    best: dict[str, Any] | None = None
    best_score = 0.0
    for row in db.deals_by_store(store):
        score = fuzz.token_sort_ratio(norm, row["normalized_item"])
        if score >= cutoff and score > best_score:
            best, best_score = row, score
    if best is not None:
        log.debug("fuzzy match %.1f: %r -> deal %s", best_score, item,
                  best["dedupe_key"])
    return best


def _union(*lists: list[str] | None) -> list[str]:
    """Ordered union with duplicates removed (first occurrence wins)."""
    out: list[str] = []
    seen: set[str] = set()
    for lst in lists:
        for x in lst or []:
            if x not in seen:
                seen.add(x)
                out.append(x)
    return out


def _merge_online_only(old: str, new: str) -> str:
    """First non-unknown value sticks, except an explicit 'false' always wins."""
    if old == "unknown":
        return new if new in ("true", "false") else "unknown"
    if new == "false":
        return "false"
    return old


def _merge_expires(old: str, new: str) -> str:
    """Keep the earliest non-empty expiry (ISO strings)."""
    if not old:
        return new or ""
    if not new:
        return old
    od, nd = util.parse_iso(old), util.parse_iso(new)
    if od is not None and nd is not None:
        return old if od <= nd else new
    return old


def _new_payload(key: str, extracted: models.ExtractedDeal,
                 savings: models.SavingsResult, asin: str,
                 now: datetime) -> dict[str, Any]:
    """Payload dict shaped like models.Deal for a brand-new deal row."""
    return {
        "dedupe_key": key,
        "store": extracted.store,
        "item": extracted.item,
        "brand": extracted.brand,
        "canonical_url": extracted.product_url,
        "asin": asin,
        "final_price": savings.final_price,
        "regular_price": savings.regular_price,
        "pct_at_checkout": savings.pct_at_checkout,
        "pct_with_rebates": savings.pct_with_rebates,
        "abs_savings": savings.abs_savings,
        "basis": savings.basis,
        "flags": list(savings.flags),
        "codes": list(extracted.codes),
        "stack_steps": list(extracted.stack_steps),
        "rebates": list(extracted.rebates),
        "deferred_value": extracted.deferred_value,
        "conditions": list(extracted.conditions),
        "online_only": extracted.online_only,
        "shipping_cost_or_threshold": extracted.shipping_cost_or_threshold,
        "quantity_or_unit": extracted.quantity_or_unit,
        "region": extracted.region,
        "currency": extracted.currency,
        "category": extracted.category or "other",
        "expires_at": extracted.expires_at,
        "verify_tier": "unverified",
        "critic": None,
        "status": "alive",
        "first_seen_at": util.iso(now),
        "last_seen_at": util.iso(now),
        "confidence": extracted.confidence,
        "sightings": [],
    }


def upsert_extracted(db, extracted: models.ExtractedDeal,
                     savings: models.SavingsResult, post: models.RawPost,
                     cfg, now: datetime) -> dict[str, Any]:
    """Insert a new deal or merge into an existing one. Idempotent per post.

    Returns {'deal_id', 'dedupe_key', 'is_new_deal', 'price_improved',
    'payload'}.
    """
    from .http_client import extract_asin  # lazy: written by another layer

    asin = extract_asin(extracted.product_url) if extracted.product_url else ""
    existing = find_existing(db, extracted.store, extracted.product_url,
                             extracted.item, cfg.fuzzy_threshold)

    price_improved = False
    if existing is None:
        key = canonical_key(extracted.store, extracted.product_url,
                            extracted.item)
        payload = _new_payload(key, extracted, savings, asin, now)
        deal_id = db.insert_deal(
            dedupe_key=key,
            store=extracted.store,
            item=extracted.item,
            normalized_item=util.normalize_item(extracted.item),
            canonical_url=extracted.product_url,
            payload=payload,
            final_price=savings.final_price,
            pct_at_checkout=savings.pct_at_checkout,
            abs_savings=savings.abs_savings,
            online_only=extracted.online_only,
            expires_at=extracted.expires_at,
            now=now,
        )
        is_new = True
        db.add_sighting(deal_id, source_name=post.source_name,
                        source_url=post.url, content_hash=post.content_hash,
                        seen_at=now, final_price=savings.final_price)
        payload["sightings"] = db.sightings_for(deal_id)
        db.update_deal(key, payload=payload)
        log.info("new deal %s (%s @ %s)", key, extracted.item, extracted.store)
    else:
        key = existing["dedupe_key"]
        deal_id = int(existing["id"])
        is_new = False
        payload = {k: existing.get(k) for k in _DEAL_FIELDS if k in existing}

        old_final = existing.get("final_price")
        new_final = savings.final_price
        price_improved = new_final is not None and (
            old_final is None or new_final < old_final)
        if price_improved:
            payload["final_price"] = savings.final_price
            payload["regular_price"] = savings.regular_price
            payload["pct_at_checkout"] = savings.pct_at_checkout
            payload["pct_with_rebates"] = savings.pct_with_rebates
            payload["abs_savings"] = savings.abs_savings
            payload["basis"] = savings.basis
            payload["flags"] = list(savings.flags)
            # The prices the critic judged changed: its verdict is stale.
            payload["critic"] = None

        payload["codes"] = _union(existing.get("codes"), extracted.codes)
        payload["conditions"] = _union(existing.get("conditions"),
                                       extracted.conditions)
        payload["rebates"] = _union(existing.get("rebates"), extracted.rebates)
        payload["stack_steps"] = _union(existing.get("stack_steps"),
                                        extracted.stack_steps)

        merged_online = _merge_online_only(
            existing.get("online_only", "unknown"), extracted.online_only)
        payload["online_only"] = merged_online
        merged_expires = _merge_expires(existing.get("expires_at", ""),
                                        extracted.expires_at)
        payload["expires_at"] = merged_expires
        payload["confidence"] = max(
            float(existing.get("confidence") or 0.0),
            float(extracted.confidence or 0.0))
        # First concrete category sticks ('other' never overwrites a real one).
        if payload.get("category", "other") in ("", "other") \
                and extracted.category and extracted.category != "other":
            payload["category"] = extracted.category
        payload.setdefault("category", "other")
        if not payload.get("canonical_url") and extracted.product_url:
            payload["canonical_url"] = extracted.product_url
        if not payload.get("asin") and asin:
            payload["asin"] = asin
        payload["last_seen_at"] = util.iso(now)

        db.add_sighting(deal_id, source_name=post.source_name,
                        source_url=post.url, content_hash=post.content_hash,
                        seen_at=now, final_price=savings.final_price)
        payload["sightings"] = db.sightings_for(deal_id)

        db.update_deal(
            key,
            payload=payload,
            final_price=savings.final_price if price_improved else None,
            pct_at_checkout=savings.pct_at_checkout if price_improved else None,
            abs_savings=savings.abs_savings if price_improved else None,
            online_only=merged_online,
            expires_at=merged_expires,
            last_seen_at=now,
        )
        log.info("merged deal %s (price_improved=%s)", key, price_improved)

    if savings.final_price is not None:
        db.record_price(key, extracted.store, savings.final_price, now,
                        post.content_hash)

    return {
        "deal_id": deal_id,
        "dedupe_key": key,
        "is_new_deal": is_new,
        "price_improved": price_improved,
        "payload": payload,
    }
