"""Dedupe/merge layer: canonical keys, fuzzy matching, idempotent upserts.

A deal is identified by its dedupe_key:
- 'url|<canonical_url>' when the pipeline resolved a canonical product URL;
- 'si|<store>|<normalized item>' otherwise (store + normalized item name).
  Conflicting quantities under a legacy name key get an identity suffix;
  existing stored keys stay unchanged.

Merging keeps the latest backed price and its matching checkout facts, and keeps
the most recently stated expiry. Sightings and price history are anchored on
the post's content_hash, so re-running the pipeline adds no rows.

An initially missing product URL is filled into the row as well as its
payload, so later merges can compare the product's known identity.
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


def _identity_key(key: str, item: str) -> str:
    """Disambiguate a legacy name-key collision without renaming stored rows."""
    return key + "|identity|" + " ".join(sorted(util.item_identity(item)))


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
    return asin_from_links(_URL_RE.findall(text or ""))


def asin_from_links(links) -> str:
    """One unambiguous ASIN, reusing the existing tracker decoders."""
    from .http_client import extract_asin, tracker_target
    from .product_images import _unwrap

    found = set()
    for link in links:
        for _ in range(5):
            try:
                asin = extract_asin(link or "")
                if asin:
                    found.add(asin)
                    break
                target = tracker_target(link or "") or _unwrap(link or "")
            except (ValueError, TypeError):
                break
            if not target or target == link:
                break
            link = target
    return next(iter(found)) if len(found) == 1 else ""



def find_existing(db, store: str, canonical_url: str, item: str,
                  fuzzy_threshold: float,
                  codes: list[str] | None = None) -> dict[str, Any] | None:
    """Locate an existing deal row for (store, canonical_url, item).

    Lookup order: exact dedupe_key, then canonical URL, then fuzzy item-name
    match within the same store (token-sort ratio on normalized names; the
    best match at or above the threshold wins). A shared distinctive promo
    code lowers the required similarity to CODE_ASSISTED_RATIO — title
    variants of one deal share the code; different products merely sharing
    a store-wide code stay apart on the name bar. Name-based matches also
    reject conflicting model, quantity and qualifier facts. Expanded titles
    can also match on the same brand, model and product type.
    """
    key = canonical_key(store, canonical_url, item)
    hit = db.get_deal(key)
    if hit is not None and (canonical_url or not util.item_identity_conflict(item, hit["item"])):
        return hit
    if hit is not None and not canonical_url:
        hit = db.get_deal(_identity_key(key, item))
        if hit is not None and not util.item_identity_conflict(item, hit["item"]):
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
    from .product_images import link_key

    identity = link_key(canonical_url) if canonical_url else ""
    best: dict[str, Any] | None = None
    best_score = 0.0
    for row in db.deals_by_store(store):
        other = link_key(row.get("canonical_url") or "")
        if identity and other and identity != other:
            continue
        if util.item_identity_conflict(item, row["item"]):
            continue
        score = fuzz.token_sort_ratio(norm, row["normalized_item"])
        if util.item_model_match(item, row["item"]):
            score = 100.0
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
                shared: bool = False, cfg=None) -> str:
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
    from .source_policy import source_image_allowed

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
        return photo if source_image_allowed(photo, [{"source_url": post.url}], cfg) else ""
    if shared or no_product or not single_product(post.photos):
        return ""
    return post.image_url if source_image_allowed(
        post.image_url, [{"source_url": post.url}], cfg) else ""


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
    from .http_client import extract_asin
    from .photo_recovery import identity_changes, post_identity
    from .conditions import USED_CONDITIONS, condition_class
    from .fetch.woot import _condition_is_explicit
    from .verify import woot_offer_url

    identity = post_identity(extracted, post)
    asin = extract_asin(identity) if identity else asin_from_links(
        [extracted.product_url, extracted.link_as_posted])
    if not asin_from_links([extracted.product_url, extracted.link_as_posted]) \
            and "amazon" in (extracted.store or "").lower() \
            and len({util.normalize_item(i) for i, _ in post.parsed}) <= 1:
        # Preserve the pre-253 click URL and key only when the stricter
        # identity agrees with the post's one Amazon product.
        from .publish.site import keeps_source_tracker
        legacy = asin_from_links([extracted.product_url, extracted.link_as_posted]
                                 + _URL_RE.findall(post.text or "")
                                 + list(post.photos.links) + post.resolved_links)
        if legacy and legacy == extract_asin(identity or "") \
                and "amazon." not in (extracted.product_url or "").lower() \
                and not keeps_source_tracker(extracted.product_url or "", cfg):
            extracted = dataclasses.replace(
                extracted, product_url=f"https://www.amazon.com/dp/{legacy}")
    photo_deal = dataclasses.replace(extracted, product_url=identity) if identity else extracted
    existing = find_existing(db, extracted.store, extracted.product_url,
                             extracted.item, cfg.fuzzy_threshold,
                             codes=extracted.codes)
    condition_known = _condition_is_explicit(post) if post.source_name == "woot" else (
        condition_class(extracted.conditions) != "new"
        or any(re.fullmatch(r"(?:brand[\s_-]+)?new", str(c), re.I)
               for c in extracted.conditions)
        or bool(re.search(r"(?im)^\s*(?:(?:item\s+)?condition\s*:\s*(?:brand[ -]+)?new"
                          r"|brand[ -]+new)[.!]?\s*$", post.text)))
    if (existing is not None and woot_offer_url(existing) and condition_known
            and condition_class(existing.get("conditions")) != condition_class(extracted.conditions)):
        # A reused Woot URL with another condition is no reading of this offer.
        return {"deal_id": int(existing["id"]), "dedupe_key": existing["dedupe_key"],
                "is_new_deal": False, "price_improved": False, "revived": False,
                "payload": existing}

    price_improved = revived = False
    post_at = post.published_at or post.fetched_at
    if existing is None:
        key = canonical_key(extracted.store, extracted.product_url,
                            extracted.item)
        if not extracted.product_url and db.get_deal(key) is not None:
            key = _identity_key(key, extracted.item)
        payload = _new_payload(key, extracted, savings, asin, now)
        payload["online_only_published_at"] = (
            util.iso(post_at) if extracted.online_only in ("true", "false") else "")
        payload["expires_published_at"] = util.iso(post_at) if extracted.expires_at else ""
        payload["price_published_at"] = util.iso(post_at)
        if cfg.open_box_scarcity:
            from .publish.open_box import capture
            stock = capture(extracted, post, now)
            if stock:
                payload["open_box_stock_evidence"] = stock
        if identity:
            payload.update(identity_changes(payload, identity))
        payload["image_url"] = _post_image(photo_deal, post, cfg=cfg)
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
        payload = {k: v for k, v in existing.items()
                   if k not in ("id", "normalized_item")}

        fresh = db.add_sighting(deal_id, source_name=post.source_name,
                                source_url=post.url, content_hash=post.content_hash,
                                seen_at=now, final_price=savings.final_price)

        old_final = existing.get("final_price")
        new_final = savings.final_price
        price_at = util.parse_iso(existing.get("price_published_at")
                                  or existing.get("last_seen_at") or "")
        current = fresh and (price_at is None or post_at >= price_at)
        # 087b can correct a price before this pull's posts are parsed. An
        # equal fetch/publication clock must not undo that paid correction;
        # only a feed's genuinely newer observation can win the tie.
        checked = util.parse_iso(existing.get("price_rechecked_at") or "")
        if current and post_at == price_at and checked is not None \
                and existing.get("price_since") == util.iso(checked):
            incoming = util.parse_iso((post.structured or {}).get("keepa_price_seen_at") or "")
            if incoming is None or incoming <= checked:
                current = False
        price_improved = current and new_final is not None and (
            old_final is None or new_final < old_final)
        price_changed = current and (
            new_final != old_final or savings.regular_price != existing.get("regular_price"))
        if current:
            # Keep the independent licensed reference across partner claims.
            # A new Keepa basis replaces it below; audit fingerprints invalidate
            # decisions whenever the active checkout facts change.
            payload["final_price"] = savings.final_price
            payload["regular_price"] = savings.regular_price
            payload["pct_at_checkout"] = savings.pct_at_checkout
            payload["pct_with_rebates"] = savings.pct_with_rebates
            payload["abs_savings"] = savings.abs_savings
            payload["basis"] = savings.basis
            payload["regular_price_basis"] = (post.structured or {}).get("regular_price_basis", "")
            payload["flags"] = list(savings.flags)
            if not savings.computable:
                payload["flags"].append("unbacked_price")
            elif not price_changed and (existing.get("critic") or {}).get("plausible") is False:
                payload["flags"].append("suspect_msrp")
            if existing.get("status") == "dead" and "price_moved" in (existing.get("flags") or []):
                payload["flags"].append("price_moved")
            # The prices the critic judged changed: its verdict is stale.
            if price_changed:
                payload["critic"] = None
                if existing.get("status") == "alive":
                    payload["price_rechecked_at"] = ""
                    payload["keepa_price_seen_at"] = ""
                    payload["keepa_warehouse_seen_at"] = ""
                # Keepa data older than this update cannot judge the new price.
                payload["price_since"] = util.iso(now)
            payload["price_published_at"] = util.iso(post_at)
            for field in ("codes", "rebates", "stack_steps", "shipping_cost_or_threshold",
                          "deferred_value", "quantity_or_unit"):
                payload[field] = getattr(extracted, field)

        incoming_conditions = extracted.conditions
        if woot_offer_url(existing) and not condition_known:
            incoming_conditions = [c for c in incoming_conditions if c not in USED_CONDITIONS]
        payload["conditions"] = _union(existing.get("conditions"), incoming_conditions)

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
        # Once several products shared the row's link, no post names it.
        payload["link_shared"] = bool(existing.get("link_shared")) \
            or extracted.link_shared
        # The incoming deal's photo (and its link as posted, which the photo
        # backfill matches on) only when it IS this row's product: a fuzzy
        # item match can merge a variant (review finding: the Series 9
        # photo landed on the Series 8 row, the 65-inch TV's on the 55-inch).
        same = _same_product(existing, extracted)
        known_identity = payload.get("product_identity_url") or (
            "https://www.amazon.com/dp/" + payload["asin"] if payload.get("asin") else "")
        if same and identity and known_identity:
            from .product_images import link_key
            same = link_key(identity) == link_key(known_identity)
        if same and not payload.get("asin") and asin:
            payload["asin"] = asin
            if payload.get("photo_asin"):
                payload.update(photo_asin="", asin_source="link", image_url="")
        if same and identity and not payload.get("product_identity_url") \
                and (not payload.get("asin") or payload["asin"] == extract_asin(identity)):
            payload.update(identity_changes(payload, identity))
        if not payload.get("link_as_posted") and extracted.link_as_posted and same:
            payload["link_as_posted"] = extracted.link_as_posted
        if not payload.get("image_url") and same:
            image = _post_image(photo_deal, post, shared=payload["link_shared"], cfg=cfg)
            if image:
                payload["image_url"] = image
        payload["last_seen_at"] = util.iso(now)

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
        # mark_expired_dead). A row with no stored price has nothing to back
        # (live crash 2026-10-14: an older post merged into one).
        backed = new_final is not None and (price_improved or (
            old_final is not None and new_final <= float(old_final) * 1.10 + 0.01))
        ended = util.parse_iso(existing.get("checkout_source_ended_at") or "")
        if current and backed and ended is not None and post_at > ended and post.source_name != "keepa":
            from .verify import post_marks_dead
            if not post_marks_dead(post, extracted):
                payload["checkout_source_ended_at"] = ""
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
            payload["keepa_warehouse_seen_at"] = ""
            payload["price_since"] = util.iso(now)
            payload["flags"] = [f for f in payload.get("flags") or []
                                if f != "price_moved"]

        db.update_deal(
            key,
            payload=payload,
            final_price=savings.final_price if current else None,
            pct_at_checkout=savings.pct_at_checkout if current else None,
            abs_savings=savings.abs_savings if current else None,
            replace_prices=current,
            canonical_url=payload.get("canonical_url") or None,
            online_only=merged_online,
            status="alive" if revived else None,
            expires_at=merged_expires,
            last_seen_at=now,
        )
        if revived:
            log.info("revived dead deal %s: new sighting from %s", key,
                     post.source_name)
        log.info("merged deal %s (price_improved=%s)", key, price_improved)

    if savings.computable and post.source_name.lower().startswith("keepa") \
            and (is_new or current) and payload.get("final_price") == savings.final_price:
        facts = post.structured or {}
        for field in ("keepa_price_seen_at", "keepa_warehouse_seen_at"):
            stamp = facts.get(field, util.iso(now) if field == "keepa_price_seen_at" else "")
            if field in facts:
                payload.setdefault(field, "")  # missing data time is not fetch time
            at = util.parse_iso(stamp)
            previous = util.parse_iso(payload.get(field) or "")
            if at is not None and (previous is None or at > previous):
                payload[field] = util.iso(min(at, now))
        if "keepa_list_price" in facts:
            from .amazon_reference import remember_list_price
            remember_list_price(payload, facts["keepa_list_price"], now=now)
        if (post.structured or {}).get("regular_price_basis"):
            from .verify import is_used_deal

            payload["regular_price_basis"] = post.structured["regular_price_basis"]
            payload["audit_reference"] = {
                "asin": payload.get("asin"), "price": savings.regular_price,
                "basis": payload["regular_price_basis"],
                "condition": "used" if is_used_deal(payload) else "new"}
        db.update_deal(key, payload=payload)

    if savings.final_price is not None:
        db.record_price(key, extracted.store, savings.final_price, now,
                        post.content_hash)

    payload["product_id"] = db.get_deal(key)["product_id"]
    return {
        "deal_id": deal_id,
        "dedupe_key": key,
        "is_new_deal": is_new,
        "price_improved": price_improved,
        "revived": revived,
        "payload": payload,
    }


def reprice_existing(db, deal: dict[str, Any], post: models.RawPost,
                     cfg, now: datetime, *, observed_at: datetime | None = None,
                     buybox: bool = False) -> bool:
    """Verify/compute a live correction and update this exact row, without intake.

    This is a price reading, not a new sourced deal: keep the product's
    existing critic verdict and source credits, and never queue an LLM call.
    A suspect verdict/flag is retained, so a correction cannot launder it.
    """
    from .parse.extractor import verify_evidence
    from .parse.schema import LLMDeal, to_extracted
    from .savings import compute_savings
    from .conditions import condition_class
    from .fetch.woot import _condition_is_explicit
    from .verify import _price_hidden_by_promo, is_amazon_deal, is_used_deal, woot_offer_url
    from .store_prices import plain_price, store_offer

    current = db.get_deal(deal["dedupe_key"])
    if current is None or current.get("status") != "alive":
        return False
    store, identity = store_offer(current)
    retailer = bool(store and post.source_name == store + "_live"
                    and store_offer({"canonical_url": (post.structured or {}).get("product_url")})
                    == (store, identity))
    woot = bool(post.source_name == "woot" and woot_offer_url(current)
                and woot_offer_url(current) == woot_offer_url(
                    {"canonical_url": (post.structured or {}).get("product_url")}))
    if woot:
        if ((_condition_is_explicit(post)
                and condition_class(current.get("conditions")) != condition_class(
                    (post.structured or {}).get("conditions")))
                or _price_hidden_by_promo(current) or current.get("gone_suspect_at")):
            return False
    elif retailer:
        if not plain_price(current) or (store == "bestbuy" and is_used_deal(current)):
            return False
    elif not is_amazon_deal(current) or is_used_deal(current) or _price_hidden_by_promo(current):
        return False

    extracted = to_extracted(LLMDeal.model_validate(post.structured), post, "UTC", now)
    if not verify_evidence(extracted, post.text)[0]:
        return False
    exact_buybox = buybox and cfg.keepa_click_price_rule == "on"
    savings = compute_savings(extracted, allow_price_only=True) if retailer or exact_buybox else compute_savings(extracted)
    if not savings.computable or savings.final_price is None:
        return False
    shown = current.get("final_price")
    if woot or exact_buybox:
        unchanged = shown == savings.final_price
    elif retailer:
        unchanged = (shown == savings.final_price
                     and current.get("regular_price") == savings.regular_price
                     and current.get("regular_price_basis") == post.structured["regular_price_basis"])
    else:
        unchanged = (not shown or shown == savings.final_price
                     or abs(savings.final_price - shown) * 100 / shown + 1e-9 < cfg.keepa_reprice_min_pct)
    if unchanged:
        return False  # double-apply does not touch the row or add history
    payload = {k: v for k, v in current.items() if k not in ("id", "normalized_item")}
    if not payload.get("image_url") and deal.get("image_url"):
        payload["image_url"] = deal["image_url"]  # harvested from this paid response
    for name in ("final_price", "regular_price", "pct_at_checkout", "pct_with_rebates",
                 "abs_savings", "basis"):
        payload[name] = getattr(savings, name)
    payload["regular_price_basis"] = post.structured.get("regular_price_basis", "")
    payload["audit"] = None
    payload["audit_reference"] = None if woot else {
        "asin": current.get("asin"), "price": savings.regular_price,
        "basis": payload["regular_price_basis"], "condition": "new"}
    obsolete = {"price_moved", "unbacked_price", "range", "unknown_shipping",
                "rebate_dependent", "per_unit", "deferred_value"}
    payload["flags"] = _union(savings.flags, [f for f in current.get("flags") or [] if f not in obsolete])
    for name in ("stack_steps", "codes", "rebates", "deferred_value", "shipping_cost_or_threshold"):
        payload[name] = getattr(extracted, name)
    observed_at = min(observed_at, now) if observed_at is not None else now
    payload.update(price_since=util.iso(observed_at), price_published_at=util.iso(now),
                   price_rechecked_at=util.iso(observed_at), recheck_tried_at="",
                   gone_suspect_at="", death="", died_at="", verify_tier="unverified")
    db.update_deal(deal["dedupe_key"], payload=payload, final_price=savings.final_price,
                   pct_at_checkout=savings.pct_at_checkout, abs_savings=savings.abs_savings,
                   replace_prices=True, verify_tier="unverified")
    db.record_price(deal["dedupe_key"], current["store"], savings.final_price,
                    observed_at, post.content_hash)
    deal.update(db.get_deal(deal["dedupe_key"]))
    return True
