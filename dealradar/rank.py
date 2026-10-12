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

import logging
import math
import re
from collections import Counter
from datetime import datetime
from typing import Any

from .util import parse_iso

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
    if not options:
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


def pick_score(deal: dict[str, Any], cfg, now: datetime) -> float | None:
    """Landing-page quality, with checkout effort and age factored in."""
    from .publish.site import _needs_steps, _simple_code_deal

    options = cfg.front_page.get("top_picks")
    conditions = set(deal.get("conditions") or [])
    category = deal.get("category") or "other"
    if not options or deal.get("status", "alive") != "alive" or deal.get("locked") \
            or not str(deal.get("image_url") or "").startswith("https://") \
            or deal.get("price_is_range") or "range" in (deal.get("flags") or []) \
            or category == "other" or category in cfg.front_page.get("niche_categories", []) \
            or conditions & {"targeted", "new_customer", "in_store_only", "ymmv", "clearance_aisle"}:
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
    return quality * 0.5 ** (_age_hours(deal.get("first_seen_at", ""), now)
                             / options["fresh_half_life_h"])


def top_picks(scored: list[tuple[dict[str, Any], float]], options: dict) -> list[dict[str, Any]]:
    """Greedy selection with strict caps; ties retain the input's order."""
    picks = options.get("top_picks")
    if not picks:
        return []
    stores, brands, categories, twins = Counter(), Counter(), Counter(), Counter()
    picked, last, run = [], "", 0
    for deal, _score in sorted(scored, key=lambda pair: pair[1], reverse=True):
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
        return front_page_mix(rows, options)
    return sorted(rows, key=lambda d: d.get("pick", math.inf))


def is_publishable(deal: dict[str, Any], cfg,
                   min_pct: float | None = None) -> tuple[bool, str]:
    """All publish gates; returns (False, reason) naming the first failure.

    ``min_pct`` overrides cfg.min_pct (the site's lower "strong deals" tier
    reuses every other gate unchanged); alerts always use cfg.min_pct.
    """
    status = deal.get("status", "alive")
    if status != "alive":
        return False, f"status: {status!r} is not 'alive'"

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
    if pct is None or pct < floor:
        return False, f"pct_at_checkout: {pct!r} < min_pct {floor}"

    # Sanity: "100% off" while money changes hands is a contradiction
    # (seen live: a $254.98 phone published as 100% off via a rebate rule).
    final = deal.get("final_price")
    if pct >= 99.95 and final is not None and final > 0.01:
        return False, (f"inconsistent: pct_at_checkout {pct:g} claims free "
                       f"but final_price is ${final:g}")

    # Used/open-box supply is plentiful: such deals clear a HIGHER absolute
    # floor, and the site's lower "strong deals" tier never softens it.
    if {"used", "open_box"} & set(deal.get("conditions") or []):
        used_floor = getattr(cfg, "used_min_pct", 80.0)
        if pct < used_floor:
            return False, (f"pct_at_checkout: {pct!r} < used_min_pct "
                           f"{used_floor} (used/open-box)")

    abs_savings = deal.get("abs_savings")
    if abs_savings is None or abs_savings < cfg.min_abs_savings:
        return False, (f"abs_savings: {abs_savings!r} < min_abs_savings "
                       f"{cfg.min_abs_savings}")

    return True, ""


def published_deals(db, cfg, now: datetime) -> list[dict[str, Any]]:
    """Alive deals that pass every publish gate, scored, best first.

    This is the ALERT set (Telegram, digest): dead deals never alert.
    """
    out: list[dict[str, Any]] = []
    for deal in db.all_deals(status="alive"):
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


def site_deals(db, cfg, now: datetime) -> list[dict[str, Any]]:
    """The SITE set: every deal clearing the gates at the SITE floor
    (cfg.site_show_from_pct, never above cfg.min_pct), alive or recently
    dead (dead ones shown marked dead; the spec wants them visible on the
    page, never alerted). Deals between the site floor and cfg.min_pct are
    the page's "strong deals" tier — on the page, never alerted. Retention
    prunes dead rows cfg.deals_days after they died; a live deal is never
    removed for its age."""
    from datetime import timedelta

    site_floor = min(cfg.min_pct,
                     getattr(cfg, "site_show_from_pct", cfg.min_pct))
    max_dead_age = timedelta(hours=getattr(cfg, "show_dead_hours", 24.0))
    out: list[dict[str, Any]] = []
    for status in ("alive", "dead"):
        for deal in db.all_deals(status=status):
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
    out.sort(key=lambda d: d["score"], reverse=True)
    return out
