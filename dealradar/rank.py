"""Ranking and publish gating.

A deal is publishable only when it is alive, explicitly online-only, priced in
USD for the US, and clears both savings floors (pct_at_checkout >= cfg.min_pct
and abs_savings >= cfg.min_abs_savings). Everything else stays in the DB but
is never published.

Score = pct_at_checkout
      + tier boost (corroborated > history-checked > unverified)
      + capped absolute-savings bonus
      - per-condition penalty
      - age penalty per hour since first sighting.
"""

from __future__ import annotations

from .conditions import USED_CONDITIONS, normalize_conditions

import logging
import math
import re
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .util import iso, parse_iso

log = logging.getLogger("dealradar.rank")

#: Deaths the site may show as GONE: two live checks agreed, or the
#: store's own API ended the deal (models.Deal.death).
VERIFIED_DEATHS = frozenset({"confirmed", "api_ended"})


def front_page_mix(rows: list[dict[str, Any]], options: dict) -> list[dict[str, Any]]:
    """Pick a varied first 24; retain every remaining card in input order.

    Input order supplies ranking or the browser's seeded shuffle. Free picks
    still precede locked cards, and gone cards keep their spaced slots. When
    supply cannot meet every cap, relax runs, then stores, then niche counts.
    Keep this selection in sync with frontPageMix in index.html.
    """
    if not options or "max_niche_top" not in options:
        return rows
    broad = set(options.get("broad_categories", []))
    niche = set(options.get("niche_categories", []))
    niche_cap = int(options["max_niche_top"])
    # The first screen is everyday deals only: niche never before this slot.
    niche_after = int(options.get("niche_after", 0))
    run_cap = int(options["max_same_category_run"])
    store_cap = int(options["max_same_store_top"])
    picked, used, stores = [], set(), {}
    niche_count, last, run = 0, "", 0
    missing_photo = False
    anchors = [d for d in rows[:24] if d.get("status") == "dead"]
    reserved_niche = sum((d.get("category") or "other") in niche
                         or (d.get("category") or "other") not in broad for d in anchors)
    reserved_stores: dict[str, int] = {}
    for d in anchors:
        store = str(d.get("store") or "").strip().lower()
        reserved_stores[store] = reserved_stores.get(store, 0) + 1
    for slot in range(min(24, len(rows))):
        if rows[slot].get("status") == "dead":
            chosen = slot
            cat = rows[slot].get("category") or "other"
            store = str(rows[slot].get("store") or "").strip().lower()
            reserved_niche -= cat in niche or cat not in broad
            reserved_stores[store] -= 1
        else:
            available = [i for i, d in enumerate(rows)
                         if i not in used and d.get("status") != "dead"]
            # Preserve the unlocked group without changing who was picked.
            unlocked = [i for i in available if not rows[i].get("locked")]
            available = unlocked or available
            without_photos = [i for i in available if not rows[i].get("image_url")]
            if options.get("prefer_photos") and missing_photo and without_photos:
                available = without_photos
            chosen = available[0]
            for relaxation in range(4):
                eligible = []
                for i in available:
                    d = rows[i]
                    cat = d.get("category") or "other"
                    store = str(d.get("store") or "").strip().lower()
                    is_niche = cat in niche or cat not in broad
                    if relaxation < 3 and is_niche and (slot < niche_after or niche_count + reserved_niche >= niche_cap):
                        continue
                    if relaxation < 2 and stores.get(store, 0) + reserved_stores.get(store, 0) >= store_cap:
                        continue
                    if relaxation < 1 and cat == last and run >= run_cap:
                        continue
                    next_row = rows[slot + 1] if slot + 1 < min(24, len(rows)) else {}
                    if relaxation < 1 and next_row.get("status") == "dead" \
                            and (next_row.get("category") or "other") == cat \
                            and (run + 1 if cat == last else 1) >= run_cap:
                        continue
                    eligible.append(i)
                if eligible:
                    photos = [i for i in eligible if rows[i].get("image_url")]
                    chosen = (photos if options.get("prefer_photos") and photos else eligible)[0]
                    break
        d = rows[chosen]
        cat = d.get("category") or "other"
        store = str(d.get("store") or "").strip().lower()
        niche_count += cat in niche or cat not in broad
        stores[store] = stores.get(store, 0) + 1
        run = run + 1 if cat == last else 1
        last = cat
        picked.append(d)
        used.add(chosen)
        missing_photo = missing_photo or not d.get("image_url")
    return picked + [d for i, d in enumerate(rows) if i not in used]


def _age_hours(first_seen_at: str, now: datetime) -> float:
    dt = parse_iso(first_seen_at)
    if dt is None:
        return 0.0
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def score(deal: dict[str, Any], cfg, now: datetime) -> float:
    """Ranking score for one deal row dict (higher is better)."""
    pct = float(deal.get("pct_at_checkout") or 0.0)
    boost = float(cfg.tier_boost.get(deal.get("verify_tier", "unverified"), 0))
    abs_bonus = min(float(deal.get("abs_savings") or 0.0), 100.0) \
        * cfg.abs_savings_weight
    cond_penalty = len(deal.get("conditions") or []) * cfg.condition_penalty
    age_penalty = _age_hours(deal.get("first_seen_at", ""), now) \
        * cfg.age_penalty_per_hour
    return pct + boost + abs_bonus - cond_penalty - age_penalty


def niche_reason(deal: dict[str, Any]) -> str:
    """Clear model-specific compatibility only; ambiguous titles keep full appeal."""
    title = str(deal.get("item") or "").casefold()
    if re.search(r"\b(?:universal|headphones?|earbuds?|headsets?)\b", title) \
            or re.search(r"\b(?:smartphone|phone|tablet|smartwatch)\s+(?:with|includes?)\b", title) \
            or ("iphone" in title and "android" in title):
        return ""
    accessory = r"\b(?:cases?|covers?|screen[ -]*protectors?|bands?|straps?|" \
                r"charg(?:er|ing)[ -]*(?:docks?|stands?|stations?))\b"
    device = r"\b(?:iphone[ -]*\d+[a-z]?|(?:samsung\s+)?galaxy\s+(?:[sa]\d+[a-z]{0,2}|z\s*(?:fold|flip)\s*\d+)|" \
             r"z\s*(?:fold|flip)\s*\d+|(?:google\s+)?pixel[ -]*\d+[a-z]?|" \
             r"ipad\s+(?:(?:air|pro|mini)\s*)?\d+|apple\s+watch\s+(?:series\s*\d+|ultra\s*\d+|se\s*\d+))\b"
    if re.search(accessory, title) and re.search(device, title):
        return "named-device accessory"

    camera_accessory = r"\b(?:lens(?:es)?|(?:lens|camera)\s+mounts?|mounts?\s+for|adapters?)\b"
    camera_system = r"\b(?:e|rf|ef|ef-s|z|x|l)[ -]*mount\b|" \
                    r"\b(?:sony\s+e|canon\s+(?:rf|ef)|nikon\s+z|fujifilm\s+x)\b"
    if re.search(camera_accessory, title) and re.search(camera_system, title):
        return "camera-system accessory"

    vehicle = r"\b(?:for|fits?|compatible\s+with)\s+(?:19|20)\d{2}" \
              r"(?:\s*[-–/]\s*(?:\d{2}|(?:19|20)\d{2}))?\s+" \
              r"(?:ford|chevrolet|chevy|gmc|toyota|honda|nissan|dodge|ram|jeep|" \
              r"subaru|hyundai|kia|bmw|audi|volkswagen|tesla)\s+[a-z0-9][\w-]*\b"
    if re.search(vehicle, title):
        return "vehicle-specific part"

    part = r"\b(?:filters?|cartridges?|toner|ink|bulbs?|blades?|batter(?:y|ies)|brush[ -]*heads?)\b"
    # Also everyday words (hair rollers, a women's belt): only with "replacement"/"compatible".
    loose_part = r"\b(?:parts?|belts?|rollers?)\b"
    # A compatibility phrase needs a brand/name and model, not a pack, size or wattage.
    model = r"\b(?:compatible\s+with|replacement\s+for|for)\s+" \
            r"[a-z][\w'-]*(?:[ -]+[a-z][\w'-]*){0,3}[ -]+(?:[a-z]+[ -]*)?" \
            r"(?!\d+(?:\.\d+)?[ -]*(?:cups?|inches|inch|mm|cm|packs?|count|pcs|pieces|pairs?|sets?|" \
            r"w|watts?|v|volts?|lumens?|oz|ounces?|lbs?|ft|feet)\b)\d[\w.-]*\b"
    if (re.search(part, title) or (re.search(loose_part, title)
                                   and re.search(r"\b(?:replacements?|compatible)\b", title))) \
            and re.search(model, title):
        return "model-specific replacement part"
    return ""


def appeal_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Private counts in landing order, excluding locked and gone cards."""
    live = [d for d in rows if not d.get("locked") and d.get("status") == "alive"]
    niche = [d for d in live if niche_reason(d)]
    return {"niche_in_wall": len(niche),
            "niche_in_top30": sum(bool(niche_reason(d)) for d in live[:30]),
            "niche_examples": [d["item"] for d in niche[:3]]}


def pick_score(deal: dict[str, Any], cfg, now: datetime, *,
               require_confirmation: bool = True, allow_used: bool = False) -> float | None:
    """Landing quality; internal recovery may score used items without featuring them."""
    from .publish.site import _needs_steps, _serialize_deal, _simple_code_deal
    from .verify import is_amazon_deal, price_confirmation_recent

    from .non_amazon_freshness import promotion_excluded
    if deal.get("price_unconfirmed") or promotion_excluded(deal, cfg, now):
        return None
    if require_confirmation and is_amazon_deal(deal) and not price_confirmation_recent(
            deal, now, cfg.keepa_pick_max_unconfirmed_h):
        return None
    # Keepa can't re-check other stores: a price nobody restated or re-checked
    # recently is no top pick (operator, 2026-10-14: a 4-day-old $7 Walmart price
    # led Trending).
    limit = (cfg.front_page.get("top_picks") or {}).get("non_amazon_max_age_h")
    if require_confirmation and limit and not is_amazon_deal(deal):
        keys = [k for k in ("price_published_at", "price_rechecked_at") if deal.get(k)] \
            or [k for k in ("first_seen_at",) if deal.get(k)]
        if not keys or min(_age_hours(deal[k], now) for k in keys) > float(limit):
            return None

    options = cfg.front_page.get("top_picks")
    conditions = set(normalize_conditions(deal.get("conditions")))
    excluded = set(normalize_conditions((options or {}).get("exclude_conditions", [])))
    if allow_used:
        excluded -= USED_CONDITIONS
    category = deal.get("category") or "other"
    if not options or deal.get("status", "alive") != "alive" or deal.get("locked") \
            or not str(deal.get("image_url") or "").startswith("https://") \
            or deal.get("price_is_range") or "range" in (deal.get("flags") or []) \
            or category == "other" or category in cfg.front_page.get("niche_categories", []) \
            or conditions & {"targeted", "new_customer", "in_store_only", "ymmv", "clearance_aisle"} \
            or conditions & excluded:
        return None
    quality = (options["pct_weight"] * float(deal.get("pct_at_checkout") or 0)
               + options["dollars_weight"] * math.log10(1 + float(deal.get("abs_savings") or 0)))
    tier = deal.get("verify_tier", "unverified")
    if tier == "corroborated":
        quality += options["corroborated_bonus"]
    elif tier == "history-checked":
        quality += options["history_bonus"]
    store = str(deal.get("store") or "").strip().lower()
    if store in {s.strip().lower() for s in options["known_stores"]}:
        quality += options["known_store_bonus"]
    if str(deal.get("brand") or "").strip():
        quality += options["brand_bonus"]
    if conditions & {"used", "open_box"}:
        quality *= options["used_factor"]
    price = deal.get("final_price")
    if price is not None and price < options["cheap_below"]:
        quality *= options["cheap_factor"]
    if _needs_steps(deal) and not _simple_code_deal(deal):
        quality *= options["steps_factor"]
    earns = deal.get("earns")
    if earns is None:
        earns = _serialize_deal(deal, now, cfg.affiliate_amazon_tag, cfg.affiliate_link_overrides,
                                sovrn_key=cfg.affiliate_sovrn_key,
                                sovrn_enabled=cfg.affiliate_sovrn_enabled,
                                competitor_tracker_hosts=cfg.affiliate_competitor_tracker_hosts,
                                ebay_campid=cfg.affiliate_ebay_campaign_id)["earns"]
    if not earns:
        quality *= options.get("no_earn_factor", 0.5)
    result = quality * 0.5 ** (_age_hours(deal.get("first_seen_at", ""), now)
                              / options["fresh_half_life_h"])
    appeal = cfg.front_page.get("appeal") or {}
    if appeal.get("enabled", False):
        result *= (appeal.get("category_factors") or {}).get(category, 1.0)
        if niche_reason(deal):
            result *= appeal.get("niche_factor", 1.0)
    return result


def top_picks(scored: list[tuple[dict[str, Any], float]], options: dict) -> list[dict[str, Any]]:
    """Greedy selection with strict caps; ties retain the input's order."""
    picks = options.get("top_picks")
    if not picks:
        return []
    stores, brands, categories, twins = Counter(), Counter(), Counter(), Counter()
    picked, last, run = [], "", 0
    for deal, _score in sorted((pair for pair in scored if pair[1] is not None),
                               key=lambda pair: pair[1], reverse=True):
        if deal.get("price_unconfirmed") or deal.get("price_aged"):
            continue
        if len(picked) >= picks["count"]:
            break
        store = str(deal.get("store") or "").strip().lower()
        brand = str(deal.get("brand") or "").strip().lower()
        category = deal.get("category") or "other"
        twin = " ".join(re.findall(r"\w+", str(deal.get("item") or "").lower())[:3])
        if stores[store] >= picks["max_same_store"] \
                or (brand and brands[brand] >= picks["max_same_brand"]) \
                or categories[category] >= picks["max_same_category"] \
                or twins[twin] >= picks["max_title_twins"] \
                or (len(picked) < 8 and category == last
                    and run >= options["max_same_category_run"]):
            continue
        picked.append(deal)
        stores[store] += 1
        brands[brand] += 1
        categories[category] += 1
        twins[twin] += 1
        run = run + 1 if category == last else 1
        last = category
    return picked


def pick_order(rows: list[dict[str, Any]], options: dict) -> list[dict[str, Any]]:
    """Published pick order followed by the existing order of the rest."""
    if not options.get("top_picks"):
        return sorted(front_page_mix(rows, options), key=lambda d: bool(d.get("price_unconfirmed") or d.get("price_aged")))
    return sorted(rows, key=lambda d: (bool(d.get("price_unconfirmed") or d.get("price_aged")), d.get("pick", math.inf)))


def spotlight_sources(cfg) -> set[str]:
    options = cfg.front_page.get("spotlight") or {}
    if int(options.get("max_cards", 0)) <= 0:
        return set()
    return {str(s).casefold() for s in options.get("sources", [])}


def daily_spotlight(rows: list[dict[str, Any]], cfg, now: datetime) -> list[dict[str, Any]]:
    """Today's backed daily offers, separate from ordinary publish sets."""
    from .config import load_sources
    from .fetch.meh import deal_end
    from .publish.credits import source_display_name
    from .publish.price_labels import push_excluded
    from .source_policy import source_image_allowed

    sources = spotlight_sources(cfg)
    if not sources or not rows:
        return []
    primary, backup = load_sources()
    zones = {s.name: s.tz for s in primary + backup}
    candidates = []
    for deal in rows:
        if push_excluded(deal, cfg, now):
            continue
        if not is_publishable(deal, cfg, allow_spotlight_price=True)[0]:
            continue
        price = deal.get("final_price")
        if not isinstance(price, (int, float)) or isinstance(price, bool) \
                or not math.isfinite(price) or price < 0:
            continue
        image = str(deal.get("image_url") or "")
        sightings = deal.get("sightings") or []
        if not image.startswith("https://") or not source_image_allowed(image, sightings, cfg):
            continue
        expiry = parse_iso(deal.get("expires_at") or "")
        if expiry is not None and expiry <= now:
            continue
        for sighting in sightings:
            source = str(sighting.get("source_name") or "").casefold()
            if source not in sources or sighting.get("final_price") != deal.get("final_price"):
                continue
            seen = parse_iso(sighting.get("seen_at") or deal.get("first_seen_at") or "")
            published = parse_iso(deal.get("price_published_at") or "")
            if seen is not None and published is not None:
                seen = min(seen, published)
            zone = ZoneInfo("America/Chicago" if source == "meh" else zones.get(source, "UTC"))
            if seen is None or seen > now or seen.astimezone(zone).date() != now.astimezone(zone).date():
                continue
            day_end = (deal_end(seen) if source == "meh" else datetime.combine(
                seen.astimezone(zone).date() + timedelta(days=1), time(), zone).astimezone(timezone.utc))
            end = expiry or (day_end if source == "meh" else None)
            candidates.append((dict(deal, spotlight={
                "source": source_display_name(source),
                "ends_at": iso(end) if end else "",
                "until": iso(min(day_end, end) if end else day_end),
            }), seen))
            break  # One card even when several daily sources credited it.
    candidates.sort(key=lambda pair: (
        pair[0].get("pct_at_checkout") is not None,
        pair[0].get("pct_at_checkout") or 0, pair[1]), reverse=True)
    return [d for d, _seen in candidates[:int(cfg.front_page["spotlight"]["max_cards"])]]


def is_publishable(deal: dict[str, Any], cfg,
                   min_pct: float | None = None, *, require_critic: bool = True,
                   allow_spotlight_price: bool = False) -> tuple[bool, str]:
    """All publish gates; returns (False, reason) naming the first failure.

    ``min_pct`` overrides cfg.min_pct (the site's lower "strong deals" tier
    reuses every other gate unchanged); alerts always use cfg.min_pct.
    ``allow_spotlight_price`` admits a configured daily source's price-only
    offer; known discounts still clear both floors. Ordinary sets never opt in.
    """
    status = deal.get("status", "alive")
    if status != "alive":
        return False, f"status: {status!r} is not 'alive'"
    if "unbacked_price" in (deal.get("flags") or []):
        return False, "price: latest sighting has no computable checkout price"

    sources = [deal.get("source_name", "")] + [
        s.get("source_name", "") for s in deal.get("sightings") or []]
    if (require_critic and cfg.critic_tiering["enabled"] and cfg.critic_enabled and cfg.critic_model
            and deal.get("regular_price") and deal.get("final_price") is not None):
        from .legitimacy import fingerprint, settled_critic, valid_code_basis

        verdict = deal.get("critic")
        if not (cfg.audit_mode == "enforce" and valid_code_basis(deal, cfg)):
            if ("critic_tier_pending" in (deal.get("flags") or []) or not settled_critic(verdict)
                    or (verdict.get("audit_fingerprint")
                        and verdict["audit_fingerprint"] != fingerprint(deal, cfg))):
                return False, "critic: tiered deal awaits a valid verdict"
    if require_critic and cfg.audit_mode == "enforce":
        from .legitimacy import fingerprint, settled_critic, valid_code_basis

        audit = deal.get("audit") or {}
        if audit.get("outcome") == "reject" and audit.get("fingerprint") == fingerprint(deal, cfg):
            return False, "audit: suspect_msrp (" + audit["reason"] + ")"
        verdict = deal.get("critic")
        if isinstance(verdict, dict) and verdict.get("plausible") is False:
            return False, "critic: suspect_msrp"
        if not valid_code_basis(deal, cfg):
            if (not settled_critic(verdict) or not verdict["plausible"]
                    or (verdict.get("audit_fingerprint")
                        and verdict["audit_fingerprint"] != fingerprint(deal, cfg))):
                return False, "audit: awaits a verified code basis or critic verdict"
    elif require_critic and any(str(s or "").casefold().startswith("keepa") for s in sources):
        verdict = deal.get("critic")
        if not isinstance(verdict, dict) or type(verdict.get("plausible")) is not bool:
            return False, "critic: Keepa deal awaits a valid verdict"

    online_only = deal.get("online_only", "unknown")
    if online_only != "true":
        return False, (f"online_only: {online_only!r} (only explicit 'true' "
                       "is publishable)")

    currency = deal.get("currency") or "USD"
    if currency != "USD":
        return False, f"currency: {currency!r} is not 'USD'"

    if getattr(cfg, "exclude_suspect_msrp", True) \
            and "suspect_msrp" in (deal.get("flags") or []):
        return False, ("flags: suspect_msrp (deal critic judged the claimed "
                       "regular price implausible)")

    region = deal.get("region") or "US"
    if region != "US":
        return False, f"region: {region!r} is not 'US'"

    floor = cfg.min_pct if min_pct is None else min_pct
    pct = deal.get("pct_at_checkout")
    final = deal.get("final_price")
    price_only = (allow_spotlight_price and pct is None
                  and deal.get("regular_price") is None
                  and bool(spotlight_sources(cfg) & {str(s or "").casefold() for s in sources})
                  and isinstance(final, (int, float)) and not isinstance(final, bool)
                  and math.isfinite(final) and final >= 0)
    if (pct is None and not price_only) or (pct is not None and pct < floor):
        return False, f"pct_at_checkout: {pct!r} < min_pct {floor}"

    # Sanity: "100% off" while money changes hands is a contradiction
    # (seen live: a $254.98 phone published as 100% off via a rebate rule).
    if pct is not None and pct >= 99.95 and final is not None and final > 0.01:
        return False, (f"inconsistent: pct_at_checkout {pct:g} claims free "
                       f"but final_price is ${final:g}")

    # Used/open-box supply is plentiful: such deals clear a HIGHER absolute
    # floor, and the site's lower "strong deals" tier never softens it.
    if USED_CONDITIONS & set(normalize_conditions(deal.get("conditions"))):
        used_floor = getattr(cfg, "used_min_pct", 80.0)
        if pct is None or pct < used_floor:
            return False, (f"pct_at_checkout: {pct!r} < used_min_pct "
                           f"{used_floor} (used/open-box)")

    abs_savings = deal.get("abs_savings")
    # With no discount basis, dollar savings are also unknown. The price-only
    # spotlight makes no savings claim; any known dollar value keeps its floor.
    if (abs_savings is None and not price_only) \
            or (abs_savings is not None and abs_savings < cfg.min_abs_savings):
        return False, (f"abs_savings: {abs_savings!r} < min_abs_savings "
                       f"{cfg.min_abs_savings}")

    return True, ""


def published_deals(db, cfg, now: datetime) -> list[dict[str, Any]]:
    """Alive deals that pass every publish gate, scored, best first.

    This is the ALERT set (Telegram, digest): dead deals never alert.
    """
    from .publish.price_labels import push_excluded

    out: list[dict[str, Any]] = []
    for deal in db.all_deals(status="alive"):
        if push_excluded(deal, cfg, now):
            continue
        ok, reason = is_publishable(deal, cfg)
        if not ok:
            log.debug("deal %s not publishable: %s",
                      deal.get("dedupe_key", "?"), reason)
            continue
        deal["verify_tier"] = deal.get("verify_tier", "unverified")
        deal["score"] = score(deal, cfg, now)
        out.append(deal)
    out.sort(key=lambda d: d["score"], reverse=True)
    return out


def site_deals(db, cfg, now: datetime, *, include_unconfirmed: bool = False) -> list[dict[str, Any]]:
    """The SITE set: every deal clearing the gates at the SITE floor
    (cfg.site_show_from_pct, never above cfg.min_pct), alive or recently
    dead (dead ones shown marked dead; the spec wants them visible on the
    page, never alerted). Deals between the site floor and cfg.min_pct are
    the page's "strong deals" tier — on the page, never alerted. Retention
    prunes dead rows cfg.deals_days after they died; a live deal is never
    removed for its age."""
    from datetime import timedelta
    from .verify import price_unconfirmed
    from .non_amazon_freshness import hidden, promotion_excluded, label

    site_floor = min(cfg.min_pct,
                     getattr(cfg, "site_show_from_pct", cfg.min_pct))
    max_dead_age = timedelta(hours=getattr(cfg, "show_dead_hours", 24.0))
    out: list[dict[str, Any]] = []
    for status in ("alive", "dead"):
        for deal in db.all_deals(status=status):
            if status == "alive" and hidden(deal, cfg, now):
                continue
            if status == "alive" and promotion_excluded(deal, cfg, now):
                deal = dict(deal, price_aged=label(deal, now))
            if status == "alive" and not include_unconfirmed and price_unconfirmed(deal, cfg, now):
                continue
            if status == "dead":
                died = parse_iso(deal.get("died_at") or "")
                # No stamp, or dead too long: off the page. A fresh death
                # stays briefly as honest "just missed it" proof.
                if died is None or now - died > max_dead_age:
                    continue
                # Only a death we verified is shown as GONE (operator:
                # "make sure the deals are really expired"); a post's end
                # date or wording leaves the wall quietly instead.
                if deal.get("death") not in VERIFIED_DEATHS:
                    continue
            probe = dict(deal)
            probe["status"] = "alive"  # dead rows: test the other gates only
            ok, _reason = is_publishable(probe, cfg, min_pct=site_floor)
            if not ok:
                continue
            deal["verify_tier"] = deal.get("verify_tier", "unverified")
            deal["score"] = score(deal, cfg, now)
            out.append(deal)
    out.sort(key=lambda d: (bool(d.get("price_aged")), -d["score"]))
    return out
