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

import heapq
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
# Keep in sync with DROP_WINDOW in publish/templates/index.html.
PICK_WINDOW = 24


def is_niche(deal: dict[str, Any], options: dict) -> bool:
    """Shared category/title classification for the existing niche ordering."""
    if (deal.get("category") or "other") in options.get("niche_categories", []):
        return True
    words = options.get("demote_words") or []
    if not words:
        return False
    title = str(deal.get("item") or "")

    def contains(phrase):
        return re.search(r"(?<!\w)" + r"\s+".join(re.escape(w) for w in phrase.split())
                         + r"(?!\w)", title, re.I) is not None

    if any(contains(phrase) for phrase in words):
        return True
    # Phone cases alone allow model/series words between the phone and case.
    return re.search(r"\bcases?\b", title, re.I) is not None and re.search(
        r"\b(?:iphone|galaxy|pixel|samsung|motorola|moto\s+g|oneplus)\b", title, re.I) is not None


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
    niche_cap = 0 if options.get("niche_last") else int(options["max_niche_top"])  # car parts sink
    # The first screen is everyday deals only: niche never before this slot.
    niche_after = int(options.get("niche_after", 0))
    run_cap = int(options["max_same_category_run"])
    store_cap = int(options["max_same_store_top"])
    picked, used, stores = [], set(), {}
    niche_count, last, run = 0, "", 0
    missing_photo = False
    anchors = [d for d in rows[:24] if d.get("status") == "dead"]
    reserved_niche = sum(is_niche(d, options)
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
            reserved_niche -= is_niche(rows[slot], options) or cat not in broad
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
                    niche = is_niche(d, options) or cat not in broad
                    if relaxation < 3 and niche and (slot < niche_after or niche_count + reserved_niche >= niche_cap):
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
        niche_count += is_niche(d, options) or cat not in broad
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


def photoless_expired(deal: dict[str, Any], cfg, now: datetime) -> bool:
    """Publish-only cutoff; keep stored deals eligible for photo recovery."""
    from .source_policy import source_image_allowed

    limit = cfg.max_photoless_h
    if limit is None or deal.get("status", "alive") != "alive" \
            or _age_hours(deal.get("first_seen_at") or "", now) < limit:
        return False
    image = deal.get("image_url") or ""
    return not (image.startswith("https://")
                and source_image_allowed(image, deal.get("sightings") or [], cfg))


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


def popular_affordable_factor(deal: dict[str, Any], cfg, now: datetime,
                              options: dict | None = None) -> float:
    """Pure ordering factor; popularity is private and specific to one ASIN."""
    from .verify import is_amazon_deal

    options = options if options is not None else cfg.front_page.get("popular_affordable") or {}
    if not options or not is_amazon_deal(deal) \
            or USED_CONDITIONS & set(normalize_conditions(deal.get("conditions"))):
        return 1.0
    price, pct = deal.get("final_price"), deal.get("pct_at_checkout")
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v)
           for v in (price, pct)) or price < 0 or price > options.get("max_price", 50) \
            or pct < cfg.min_pct + options.get("pct_over_floor", 10):
        return 1.0
    record = deal.get("keepa_popularity")
    if not isinstance(record, dict) or not deal.get("asin") or record.get("asin") != deal["asin"]:
        return 1.0
    sold = record.get("monthly_sold")
    sold_at = record.get("sold_at")
    at = parse_iso(sold_at) if isinstance(sold_at, str) else None
    if type(sold) is not int or sold < max(0, options.get("min_monthly_sold", 1000)) \
            or at is None or not -1 <= (now - at).total_seconds() / 3600 <= options.get("max_age_h", 72):
        return 1.0
    return float(options.get("weight", 1.25))


def everyday_price_factor(deal: dict[str, Any], options: dict) -> float:
    """Softly favor everyday checkout prices across all stores."""
    price = deal.get("final_price")
    if not options or not isinstance(price, (int, float)) or isinstance(price, bool):
        return 1.0
    try:
        finite = math.isfinite(price)
    except OverflowError:  # an int too large for a float is still finite
        finite = True
    if not finite or price < 0:
        return 1.0
    full, floor = options.get("full_until", 60), options.get("floor_from", 200)
    factor = options.get("floor_factor", .85)
    if price <= full:
        return 1.0
    if price >= floor:
        return float(factor)
    return 1.0 - (1.0 - factor) * (price - full) / (floor - full)


def pick_popularity_factor(deal, cfg, now, options=None):
    """Marginal private lift after the shared title-lift ceiling."""
    from . import appeal

    appeal_options = cfg.front_page.get("appeal") or {}
    if appeal.blocked(deal, appeal_options):
        return 1.0
    popular = popular_affordable_factor(deal, cfg, now, options)
    _, _, title = appeal.signals(deal, cfg, now)
    from .appeal_score import factor
    rating = max(1.0, factor(deal, cfg))
    if title <= 1 and rating <= 1:
        return popular
    everyday = cfg.front_page.get("everyday_price") or {}
    price = everyday_price_factor(deal, everyday) if everyday.get("enabled") else 1.0
    cap = appeal.lift_cap(appeal_options, deal, title)
    return appeal.capped_lift([popular, price, title, rating], cap, demand=popular) / appeal.capped_lift([price, title, rating], cap)


def pick_score(deal: dict[str, Any], cfg, now: datetime, *,
               require_confirmation: bool = True, allow_used: bool = False,
               links=None, popularity: bool = True) -> float | None:
    """Landing quality; internal recovery may score used items without featuring them.
    popularity=False leaves out the private popularity boost (public ``ps``)."""
    from .publish.credits import link_policy
    from .publish.link_facts import _needs_steps, _simple_code_deal, derive_link_facts
    from .verify import _price_hidden_by_promo, is_amazon_deal, price_confirmation_recent
    from . import checkout_prices

    from .non_amazon_freshness import promotion_excluded
    if deal.get("price_unconfirmed") or promotion_excluded(deal, cfg, now):
        return None
    from .click_price import excluded
    if require_confirmation and excluded(deal, cfg, now):
        return None
    checkout = checkout_prices.enabled(cfg) and _price_hidden_by_promo(deal)
    if require_confirmation and is_amazon_deal(deal) and not checkout and not price_confirmation_recent(
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
            or category == "other" or is_niche(deal, cfg.front_page) \
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
    earns = deal.get("earns")
    if links is None and earns is None:
        links = derive_link_facts(deal, link_policy(
            cfg.affiliate_amazon_tag, cfg.affiliate_link_overrides,
            sovrn_key=cfg.affiliate_sovrn_key, sovrn_enabled=cfg.affiliate_sovrn_enabled,
            competitor_tracker_hosts=cfg.affiliate_competitor_tracker_hosts,
            ebay_campid=cfg.affiliate_ebay_campaign_id))
    steps = links.needs_steps if links is not None else _needs_steps(deal)
    simple = links.simple_code if links is not None else (steps and _simple_code_deal(deal))
    if steps and not simple:
        quality *= options["steps_factor"]
    if earns is None:
        earns = links.earns
    if not earns:
        quality *= options.get("no_earn_factor", 0.5)
    result = quality * 0.5 ** (_age_hours(deal.get("first_seen_at", ""), now)
                              / options["fresh_half_life_h"])
    appeal = cfg.front_page.get("appeal") or {}
    from . import appeal as title_appeal
    blocked = title_appeal.blocked(deal, appeal)
    if appeal.get("enabled", False):
        category_factor = (appeal.get("category_factors") or {}).get(category, 1.0)
        result *= min(1.0, category_factor) if blocked else category_factor
        if niche_reason(deal):
            result *= appeal.get("niche_factor", 1.0)
    popularity_factor = 1.0
    if popularity and (cfg.front_page.get("popular_affordable") or {}).get("enabled", False):
        popularity_factor = 1.0 if blocked else popular_affordable_factor(deal, cfg, now)
    everyday = cfg.front_page.get("everyday_price") or {}
    price_factor = 1.0
    if everyday.get("enabled", False):
        price_factor = everyday_price_factor(deal, everyday)
    _, _, title_factor = title_appeal.signals(deal, cfg, now)
    from .appeal_score import factor
    rating_factor = factor(deal, cfg)
    if blocked:
        rating_factor = min(1.0, rating_factor)
    if title_factor > 1 or rating_factor > 1:
        factors = [popularity_factor, price_factor, title_factor]
        if rating_factor > 1:
            factors.append(rating_factor)
        result *= title_appeal.capped_lift(factors,
                                          title_appeal.lift_cap(appeal, deal, title_factor),
                                          demand=popularity_factor)
    else:
        # Preserve the exact historical operation order/rounding with switches off.
        result *= popularity_factor
        result *= price_factor
    if rating_factor < 1:
        result *= rating_factor
    return result


def top_picks(scored: list[tuple[dict[str, Any], float]], options: dict,
              now: datetime | None = None, *, summary: dict | None = None, cfg=None,
              boosted: set[str] | None = None) -> list[dict[str, Any]]:
    """Greedy selection with strict caps; ties retain the input's order."""
    from .verify import _price_hidden_by_promo
    from . import checkout_prices

    picks = options.get("top_picks")
    if not picks:
        return []
    checkout = options.get("checkout_price_deals") or {}
    enabled = checkout.get("enabled", False)
    popular = options.get("popular_affordable") or {}
    popular_on = popular.get("enabled", False)
    if (enabled or popular_on) and cfg is None:
        from .config import AppConfig
        cfg = AppConfig(front_page=options)
    now = now or datetime.now(timezone.utc)
    window = max(1, int(checkout.get("top_pick_window", 5)))
    maximum = max(0, int(checkout.get("top_pick_max_in_window", 1)))
    promo_positions = []
    stores, brands, categories, twins = Counter(), Counter(), Counter(), Counter()
    picked, last, run = [], "", 0
    ranked = sorted((pair for pair in scored if pair[1] is not None),
                    key=lambda pair: pair[1], reverse=True)
    pending = iter(ranked)
    clothing_cap = int(picks.get("max_clothing_per_window") or 0)
    mix_window = max(1, int(picks.get("mix_window", 6)))
    mix_slots = int(picks.get("mix_slots", 24))
    clothing_counts, deferred = Counter(), []
    use_queue = popular_on or clothing_cap > 0
    boost_counts = Counter()
    boost_cap = math.floor(popular.get("max_share", .25) * PICK_WINDOW) if popular_on else 0
    queue = [(-value, i, deal, pick_popularity_factor(deal, cfg, now, popular) if popular_on else 1.0)
             for i, (deal, value) in enumerate(ranked)] if use_queue else []
    heapq.heapify(queue)
    while True:
        factor = 1.0
        if use_queue:
            if deferred and (len(picked) >= mix_slots or clothing_counts[len(picked) // mix_window] < clothing_cap):
                for entry in deferred:
                    heapq.heappush(queue, entry)
                deferred.clear()
            if not queue:
                break
            negative, i, deal, factor = heapq.heappop(queue)
            if factor > 1 and boost_counts[len(picked) // PICK_WINDOW] >= boost_cap:
                # Compete again at the ordinary score; never discard overflow.
                heapq.heappush(queue, (negative / factor, i, deal, 1.0))
                continue
        else:
            try:
                deal, _score = next(pending)
            except StopIteration:
                break
        if deal.get("price_unconfirmed") or deal.get("price_aged"):
            continue
        # Unappealing offers stay on the wall, outside the rotating pick windows.
        if cfg is not None and cfg.appeal_score.get("enabled"):
            from .appeal import blocked
            if deal.get("_appeal_score") == 1 or blocked(deal, options.get("appeal") or {}):
                continue
        from .click_price import excluded
        if cfg is not None and excluded(deal, cfg, now):
            continue
        if len(picked) >= picks["count"]:
            break
        clothing = is_clothing(deal, picks) if clothing_cap > 0 else False
        if clothing and len(picked) < mix_slots and clothing_counts[len(picked) // mix_window] >= clothing_cap:
            # Re-offer overflow by score when the next window has room. All
            # existing caps and checkout evidence are checked on insertion.
            deferred.append((negative, i, deal, factor))
            continue
        promo = _price_hidden_by_promo(deal)
        kept_by = []
        if enabled and promo:
            skipped, kept_by = checkout_prices.pick_evidence(deal, cfg, now)
            if skipped:
                if summary is not None:
                    summary[skipped] += 1
                continue
            if sum(pos > len(picked) - window for pos in promo_positions) >= maximum:
                if summary is not None:
                    summary["skipped_window"] += 1
                continue
        store = str(deal.get("store") or "").strip().lower()
        brand = str(deal.get("brand") or "").strip().lower() or title_brand(deal, picks)
        category = deal.get("category") or "other"
        twin = " ".join(re.findall(r"\w+", str(deal.get("item") or "").lower())[:3])
        if stores[store] >= picks["max_same_store"] \
                or (brand and brands[brand] >= picks["max_same_brand"]) \
                or categories[category] >= picks["max_same_category"] \
                or twins[twin] >= picks["max_title_twins"] \
                or (len(picked) < 8 and category == last
                    and run >= options["max_same_category_run"]):
            continue
        if promo:
            promo_positions.append(len(picked))
            if summary is not None:
                for reason in kept_by:
                    summary["kept_by"][reason] += 1
        if factor > 1:
            boost_counts[len(picked) // PICK_WINDOW] += 1
            if boosted is not None:
                boosted.add(deal["dedupe_key"])
        clothing_counts[len(picked) // mix_window] += clothing
        picked.append(deal)
        stores[store] += 1
        brands[brand] += 1
        categories[category] += 1
        twins[twin] += 1
        run = run + 1 if category == last else 1
        last = category
    if summary is not None:
        summary["in_top_picks"] = len(promo_positions)
    return picked


def title_brand(deal: dict[str, Any], picks: dict) -> str:
    """A missing brand read from the title's first two words (operator
    2026-10-17: one brand's team sweatshirts filled the first screen)."""
    if not picks.get("brand_from_title"):
        return ""
    words = re.findall(r"[a-z0-9']+", str(deal.get("item") or "").lower())
    return " ".join(words[:2]) if words and not words[0][0].isdigit() else ""


def is_clothing(deal: dict[str, Any], picks: dict) -> bool:
    """One classifier for greedy selection and the public rotation class."""
    from .appeal import matches

    return (deal.get("category") in picks.get("clothing_categories", [])
            or matches(deal.get("item"), picks.get("clothing_words", [])))


def pick_order(rows: list[dict[str, Any]], options: dict) -> list[dict[str, Any]]:
    """Published pick order followed by the existing order of the rest; with
    niche_last, niche categories (car parts, office supplies) come after the
    rest of the wall."""
    def sunk(d):
        return bool(options.get("niche_last")) and is_niche(d, options)
    if not options.get("top_picks"):
        return sorted(front_page_mix(rows, options),
                      key=lambda d: (bool(d.get("price_unconfirmed") or d.get("price_aged")), sunk(d)))
    return sorted(rows, key=lambda d: (bool(d.get("price_unconfirmed") or d.get("price_aged")), sunk(d),
                                       d.get("pick", math.inf)))


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
    from .appeal import blocked
    appeal_options = cfg.front_page.get("appeal") or {}
    primary, backup = load_sources()
    zones = {s.name: s.tz for s in primary + backup}
    candidates = []
    for deal in rows:
        # Adult titles are never promoted, the spotlight included (operator 2026-10-17).
        if push_excluded(deal, cfg, now) or blocked(deal, appeal_options):
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
        if push_excluded(deal, cfg, now) or photoless_expired(deal, cfg, now):
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
