"""Dedupe/merge layer: canonical keys, fuzzy matching, idempotent upserts.

A deal is identified by its dedupe_key:
- 'url|<canonical_url>' when the pipeline resolved a canonical product URL;
- 'si|<store>|<normalized item>' otherwise (store + normalized item name).

Merging keeps the best (lowest) observed final price and its matching savings
fields, unions list facts (codes, conditions, rebates, stack steps), and keeps
the most recently stated expiry. Sightings and price history are anchored on
the post's content_hash, so re-running the pipeline adds no rows.

Note on canonical_url fill-in: the frozen DB contract offers no method to
update the deals.canonical_url column, so a later-resolved URL is filled into
the stored payload only (the column keeps its insert-time value).
"""

from __future__ import annotations

import dataclasses
import logging
import re
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


#: With a shared distinctive promo code, two title variants of one deal are
#: merged at this lower similarity (live wart: the same Amazon deal posted
#: by two Slickdeals feeds as "(S-XL, Black)" vs "(Black, various sizes)").
CODE_ASSISTED_RATIO = 0.75


def _distinctive_codes(codes: list[str] | None) -> set[str]:
    """Codes specific enough to identify ONE deal: 6+ chars with a digit.
    Store-wide codes (EXTRA20, HOME10) also match this shape, which is why
    a code match alone never merges — a name-similarity bar still applies."""
    return {c.strip().upper() for c in (codes or [])
            if len(c.strip()) >= 6 and any(ch.isdigit() for ch in c)}


_URL_RE = re.compile(r"https?://[^\s)\]>\"']+")


def _asin_from_text(text: str) -> str:
    """The one ASIN a post links to, or '' when there is none — or more than
    one (a roundup), where guessing would pin the wrong product."""
    from .http_client import extract_asin  # lazy: written by another layer

    found = {extract_asin(u) for u in _URL_RE.findall(text or "")
             if "amazon." in u.lower()}
    found.discard("")
    return found.pop() if len(found) == 1 else ""


def find_existing(db, store: str, canonical_url: str, item: str,
                  fuzzy_threshold: float,
                  codes: list[str] | None = None) -> dict[str, Any] | None:
    """Locate an existing deal row for (store, canonical_url, item).

    Lookup order: exact dedupe_key, then canonical URL, then fuzzy item-name
    match within the same store (token-sort ratio on normalized names; the
    best match at or above the threshold wins). A shared distinctive promo
    code lowers the required similarity to CODE_ASSISTED_RATIO — title
    variants of one deal share the code; different products merely sharing
    a store-wide code stay apart on the name bar.
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
    code_cutoff = CODE_ASSISTED_RATIO * 100.0
    my_codes = _distinctive_codes(codes)
    best: dict[str, Any] | None = None
    best_score = 0.0
    for row in db.deals_by_store(store):
        score = fuzz.token_sort_ratio(norm, row["normalized_item"])
        bar = (code_cutoff
               if my_codes and (_distinctive_codes(row.get("codes")) & my_codes)
               else cutoff)
        if score >= bar and score > best_score:
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


def _merge_online_only(old: str, new: str, old_at: str, new_at: str) -> str:
    """The most recent explicit value wins; unknown never clears one."""
    if new in ("true", "false"):
        od, nd = util.parse_iso(old_at), util.parse_iso(new_at)
        if old not in ("true", "false") or od is None or (nd and nd >= od):
            return new
    return old


def _revivable_death(existing: dict[str, Any]) -> bool:
    """Any recorded death is revivable; legacy rows need a recheck's kill."""
    death = existing.get("death") or ""
    if death:
        return True
    rechecked = existing.get("price_rechecked_at") or ""
    return ("price_moved" in (existing.get("flags") or [])
            or bool(rechecked and rechecked == existing.get("died_at")))


def _merge_expires(old: str, new: str, old_at: str, new_at: str) -> str:
    """A later-published statement wins; an empty value never clears one."""
    if not old:
        return new or ""
    if not new:
        return old
    od, nd = util.parse_iso(old_at), util.parse_iso(new_at)
    if od is None or (nd and nd > od):
        return new
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
        "link_as_posted": extracted.link_as_posted,
        "asin": asin,
        "image_url": "",
        "link_shared": extracted.link_shared,
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


def _post_image(extracted: models.ExtractedDeal, post: models.RawPost,
                shared: bool = False) -> str:
    """The photo a post gives this deal, or '' (product_images.pick_photo):
    the photo inside the deal's own product link, else the one the post
    names after the deal's item — against the names of EVERY deal the parse
    read in the post (RawPost.parsed); both, and they differ: none. Live
    wart: a roundup's first image (a collage, or another product's photo)
    went onto every deal of the post, and pairing by layout shifted photos
    to the next product. A wrong photo is worse than none: an unmatched
    deal stays photo-less and unflagged, so the backfill tries again.

    Else the post's own image, only for a post of one product: one deal
    parsed (parse_posts blanks a multi-deal post's image), at most one
    off-site product link and one photo besides the featured one.

    A link several products share (``shared``, or the deal's link_shared:
    a store or sale page, "shop the sale here" over a list of items) names
    none of them: no photo inside it and no post image, only one named
    after the item. So does a link that resolved to a store, search or
    category page (an amzn.to short link to a brand store): no photo
    inside the link as posted either, and no post image."""
    from .product_images import _names_no_product, pick_photo, single_product

    shared = shared or extracted.link_shared
    url = extracted.product_url or ""
    no_product = url.lower().startswith(("http://", "https://")) and _names_no_product(url)
    links = [extracted.link_as_posted, extracted.product_url]
    items = list(post.parsed) or [(extracted.item, extracted.link_as_posted)]
    photo = pick_photo(links, extracted.item, items, post.photos,
                       in_link=not (shared or no_product))
    if photo is None:
        return ""  # its link and its name point at different photos
    if photo:
        return photo
    if shared or no_product or not single_product(post.photos):
        return ""
    return post.image_url


def _same_product(existing: dict[str, Any], extracted: models.ExtractedDeal) -> bool:
    """True when an incoming deal names the stored row's product: the same
    retailer product id / link when both have one, else the same item."""
    from .product_images import link_key

    def keys(*urls: str) -> set[str]:
        return {link_key(u) for u in urls
                if u and u.lower().startswith(("http://", "https://"))} - {""}

    row = keys(existing.get("canonical_url") or "")
    new = keys(extracted.product_url or "", extracted.link_as_posted or "")
    if row and new:
        return bool(row & new)
    return util.normalize_item(extracted.item or "") == (existing.get("normalized_item") or "")


def upsert_extracted(db, extracted: models.ExtractedDeal,
                     savings: models.SavingsResult, post: models.RawPost,
                     cfg, now: datetime) -> dict[str, Any]:
    """Insert a new deal or merge into an existing one. Idempotent per post.

    Returns {'deal_id', 'dedupe_key', 'is_new_deal', 'price_improved',
    'revived', 'payload'}; 'revived' = a new sighting brought a dead deal
    back alive.
    """
    from .http_client import extract_asin, tracker_target  # lazy: written by another layer

    asin = extract_asin(extracted.product_url) if extracted.product_url else ""
    if not asin:
        asin = extract_asin(tracker_target(extracted.product_url))
    if not asin and "amazon" in (extracted.store or "").lower():
        # Deal blogs link the product in the body while the LLM's product_url
        # is often a tracker/redirect: the one Amazon link in a single-deal
        # post is the product. Without an ASIN the deal gets no Keepa price
        # check and no photo (live: 6 photo-less Amazon cards for this).
        asin = _asin_from_text(post.text)
        from .publish.site import keeps_source_tracker
        if asin and "amazon." not in (extracted.product_url or "").lower() \
                and not keeps_source_tracker(extracted.product_url or "", cfg):
            extracted = dataclasses.replace(
                extracted, product_url=f"https://www.amazon.com/dp/{asin}")
    existing = find_existing(db, extracted.store, extracted.product_url,
                             extracted.item, cfg.fuzzy_threshold,
                             codes=extracted.codes)

    price_improved = revived = False
    post_at = post.published_at or post.fetched_at
    if existing is None:
        key = canonical_key(extracted.store, extracted.product_url,
                            extracted.item)
        payload = _new_payload(key, extracted, savings, asin, now)
        payload["online_only_published_at"] = (
            util.iso(post_at) if extracted.online_only in ("true", "false") else "")
        payload["expires_published_at"] = util.iso(post_at) if extracted.expires_at else ""
        payload["image_url"] = _post_image(extracted, post)
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
            # Keepa data older than this drop cannot judge the new price.
            payload["price_since"] = util.iso(now)

        payload["codes"] = _union(existing.get("codes"), extracted.codes)
        payload["conditions"] = _union(existing.get("conditions"),
                                       extracted.conditions)
        payload["rebates"] = _union(existing.get("rebates"), extracted.rebates)
        payload["stack_steps"] = _union(existing.get("stack_steps"),
                                        extracted.stack_steps)

        new_at = util.iso(post_at)
        online_at = (existing.get("online_only_published_at")
                     or existing.get("last_seen_at") or "")
        merged_online = _merge_online_only(
            existing.get("online_only", "unknown"), extracted.online_only,
            online_at, new_at)
        payload["online_only"] = merged_online
        if extracted.online_only in ("true", "false") and (
                existing.get("online_only") not in ("true", "false")
                or not online_at or new_at >= online_at):
            payload["online_only_published_at"] = new_at
        else:
            payload["online_only_published_at"] = online_at
        expires_at = (existing.get("expires_published_at")
                      or existing.get("last_seen_at") or "")
        merged_expires = _merge_expires(existing.get("expires_at", ""),
                                        extracted.expires_at, expires_at, new_at)
        payload["expires_at"] = merged_expires
        if extracted.expires_at and (
                not existing.get("expires_at") or not expires_at or new_at > expires_at):
            payload["expires_published_at"] = new_at
        else:
            payload["expires_published_at"] = expires_at
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
        # Once several products shared the row's link, no post names it.
        payload["link_shared"] = bool(existing.get("link_shared")) \
            or extracted.link_shared
        # The incoming deal's photo (and its link as posted, which the photo
        # backfill matches on) only when it IS this row's product: a fuzzy
        # item match can merge a variant (review finding: the Series 9
        # photo landed on the Series 8 row, the 65-inch TV's on the 55-inch).
        same = _same_product(existing, extracted)
        if not payload.get("link_as_posted") and extracted.link_as_posted and same:
            payload["link_as_posted"] = extracted.link_as_posted
        if not payload.get("image_url") and same:
            image = _post_image(extracted, post, shared=payload["link_shared"])
            if image:
                payload["image_url"] = image
        payload["last_seen_at"] = util.iso(now)

        fresh = db.add_sighting(deal_id, source_name=post.source_name,
                                source_url=post.url,
                                content_hash=post.content_hash,
                                seen_at=now, final_price=savings.final_price)
        payload["sightings"] = db.sightings_for(deal_id)
        # A NEW sighting of a dead deal at (or within the recheck's 10% of)
        # its stored price is proof it is on sale again. Live wart
        # (B0BKQSDF5C): the price recheck killed a Warehouse deal and
        # fifteen minutes later Keepa's deals feed listed it LOWER; the
        # merge left it dead. A dearer sighting backs no stored price: it
        # is recorded and the deal stays dead. The recheck stamp is cleared
        # so the next pass re-judges it, on Keepa data newer than the
        # revival (price_since); a post that says "sold out" (or a past
        # expiry) still ends it in the same run (pipeline.run,
        # mark_expired_dead).
        backed = new_final is not None and (
            price_improved or new_final <= float(old_final) * 1.10 + 0.01)
        # A recorded death is undone only by a post written after
        # the death (live review finding: an older article, merged in the
        # same batch, revived a deal a source had just declared sold out).
        died = util.parse_iso(existing.get("died_at") or "")
        after_death = (post.published_at is None or died is None
                       or post.published_at > died)
        if existing.get("death") == "post_says_over":
            after_death = (post.published_at is not None and died is not None
                           and post.published_at > died)
        # A live check's kill is undone this way (live: a recheck killed
        # the Prime Guard DEF Warehouse offer at 01:23 and Keepa's deals
        # feed listed it again, same price, at 07:01, but the deal stayed
        # dead because its flag was gone). A passed end date still blocks
        # revival unless a later post extended it.
        exp = util.parse_iso(merged_expires or "")
        revived = (fresh and existing.get("status") == "dead" and backed
                   and after_death and _revivable_death(existing)
                   and (exp is None or exp >= now))
        if revived:
            payload["status"] = "alive"
            payload["died_at"] = ""
            payload["death"] = ""
            payload["gone_suspect_at"] = ""
            payload["price_rechecked_at"] = ""
            payload["price_since"] = util.iso(now)
            payload["flags"] = [f for f in payload.get("flags") or []
                                if f != "price_moved"]

        db.update_deal(
            key,
            payload=payload,
            final_price=savings.final_price if price_improved else None,
            pct_at_checkout=savings.pct_at_checkout if price_improved else None,
            abs_savings=savings.abs_savings if price_improved else None,
            online_only=merged_online,
            status="alive" if revived else None,
            expires_at=merged_expires,
            last_seen_at=now,
        )
        if revived:
            log.info("revived dead deal %s: new sighting from %s", key,
                     post.source_name)
        log.info("merged deal %s (price_improved=%s)", key, price_improved)

    if savings.final_price is not None:
        db.record_price(key, extracted.store, savings.final_price, now,
                        post.content_hash)

    return {
        "deal_id": deal_id,
        "dedupe_key": key,
        "is_new_deal": is_new,
        "price_improved": price_improved,
        "revived": revived,
        "payload": payload,
    }
