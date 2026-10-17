"""Private homepage ordering comparison over stored facts; no paid work."""

import json
import copy
from datetime import timedelta
from statistics import median

from . import checkout_prices
from .rank import PICK_WINDOW, everyday_price_factor, popular_affordable_factor, top_picks
from .util import iso, parse_iso
from .verify import is_amazon_deal

META = "popular_affordable_shadow"


def compare(scored, cfg, now):
    """Today's ordering versus both tilts, independent of the live switches."""
    options = cfg.front_page.get("popular_affordable") or {}
    everyday = cfg.front_page.get("everyday_price") or {}
    baseline, tilted = [], []
    from .appeal import active
    title_on = active(cfg.front_page.get("appeal") or {})
    if title_on:
        # A capped lift cannot be divided back into independent factors.
        # Recompute the two small score projections from the same stored facts.
        from .rank import pick_score
        without_cfg, with_cfg = copy.copy(cfg), copy.copy(cfg)
        without_cfg.front_page = {**cfg.front_page, "popular_affordable": {**options, "enabled": False},
                                  "everyday_price": {**everyday, "enabled": False}}
        with_cfg.front_page = {**cfg.front_page, "popular_affordable": {**options, "enabled": True},
                               "everyday_price": {**everyday, "enabled": True}}
    for deal, value in scored:
        if title_on:
            baseline.append((deal, pick_score(deal, without_cfg, now, require_confirmation=False)))
            tilted.append((deal, pick_score(deal, with_cfg, now, require_confirmation=False)))
            continue
        popular_factor = popular_affordable_factor(deal, cfg, now)
        price_factor = everyday_price_factor(deal, everyday)
        if options.get("enabled", False):
            value /= popular_factor
        if everyday.get("enabled", False):
            value /= price_factor
        baseline.append((deal, value))
        tilted.append((deal, value * popular_factor * price_factor))
    without_options = {**cfg.front_page, "popular_affordable": {**options, "enabled": False}}
    with_options = {**cfg.front_page, "popular_affordable": {**options, "enabled": True}}
    without = top_picks(baseline, without_options, now, cfg=cfg)
    boosted = set()
    with_boost = top_picks(tilted, with_options, now, cfg=with_cfg if title_on else cfg, boosted=boosted)
    amazon = [d for d, _ in scored if is_amazon_deal(d)]
    records = [d for d in amazon if isinstance(d.get("keepa_popularity"), dict)
               and d.get("asin") and d["keepa_popularity"].get("asin") == d["asin"]
               and type(d["keepa_popularity"].get("monthly_sold")) is int
               and d["keepa_popularity"]["monthly_sold"] >= 0]
    fresh = [d for d in records if isinstance(d["keepa_popularity"].get("sold_at"), str)
             and (at := parse_iso(d["keepa_popularity"]["sold_at"]))
             is not None and -1 <= (now - at).total_seconds() / 3600 <= options.get("max_age_h", 72)]
    eligible = [d for d in amazon if not (checkout_prices.enabled(cfg) and checkout_prices.kind(d)
                and checkout_prices.pick_evidence(d, cfg, now)[0])]
    before_keys = {d["dedupe_key"] for d in without}
    after_keys = {d["dedupe_key"] for d in with_boost}
    entered = [d for d in with_boost if d["dedupe_key"] not in before_keys]
    appeal = cfg.front_page.get("appeal") or {}
    tilt = (appeal.get("category_factors") or {}) if appeal.get("enabled") else {}

    def prices(rows):
        values = [d["final_price"] for d in rows[:PICK_WINDOW] if d.get("final_price") is not None]
        return median(values) if values else None

    def under_60(rows):
        window = rows[:PICK_WINDOW]
        return sum(d.get("final_price") is not None and d["final_price"] <= 60
                   for d in window) / len(window) if window else None

    return {
        "amazon_candidates": len(amazon), "with_monthly_sold": len(records), "fresh": len(fresh),
        "qualifying_at": {str(n): sum(popular_affordable_factor(
            d, cfg, now, {**options, "min_monthly_sold": n}) > 1 for d in eligible)
            for n in (500, 1000, 5000)},
        "boosted_in_picks": len(boosted), "entered_picks": len(entered),
        "left_picks": len(before_keys - after_keys),
        "first_window_changed": len({d["dedupe_key"] for d in with_boost[:PICK_WINDOW]}
                                    - {d["dedupe_key"] for d in without[:PICK_WINDOW]}),
        "median_price_top24": {"without": prices(without), "with": prices(with_boost)},
        "under_60_share_top24": {"without": under_60(without), "with": under_60(with_boost)},
        "amazon_in_picks": {"without": sum(is_amazon_deal(d) for d in without),
                            "with": sum(is_amazon_deal(d) for d in with_boost)},
        "tilted_top24": {"without": sum(tilt.get(d.get("category"), 1) > 1 for d in without[:PICK_WINDOW]),
                         "with": sum(tilt.get(d.get("category"), 1) > 1 for d in with_boost[:PICK_WINDOW])},
        "examples": [d["item"] for d in entered[:3]],
    }


def record(db, now, run_key, summary):
    try:
        ring = json.loads(db.meta_get(META) or "{}")
    except (TypeError, ValueError):
        ring = {}
    if not isinstance(ring, dict):
        ring = {}
    cutoff = now - timedelta(hours=26)
    ring = {key: entry for key, entry in ring.items() if isinstance(entry, dict)
            and (at := parse_iso(entry.get("at") or "")) is not None and at > cutoff}
    ring[run_key or iso(now)] = {"at": iso(now), **summary}
    db.meta_set(META, json.dumps(ring, sort_keys=True))


def report_data(db, now):
    """Read the encrypted ring for private delivery; never log its contents."""
    ring = json.loads(db.meta_get(META) or "{}")
    rows = sorted((entry for entry in ring.values() if isinstance(entry, dict)
                   and (at := parse_iso(entry.get("at") or "")) is not None
                   and now - timedelta(hours=26) < at <= now), key=lambda r: r["at"])
    day = [row for row in rows if parse_iso(row["at"]) > now - timedelta(hours=24)]

    def medians(values):
        # All numeric leaves are counts, prices or shares; preserve their names.
        result = {}
        for key in sorted({key for value in values for key in value} - {"at", "examples"}):
            samples = [value[key] for value in values if key in value]
            if all(isinstance(value, dict) for value in samples):
                result[key] = medians(samples)
            else:
                numbers = [value for value in samples if type(value) in (int, float)]
                result[key] = median(numbers) if numbers else None
        return result

    examples = list(dict.fromkeys(title for row in reversed(day) for title in row.get("examples", [])))[:3]
    return {"latest": rows[-1] if rows else None,
            "last24h": {"runs": len(day), **medians(day), "examples": examples}}


def report_lines(data):
    lines = ["== Homepage tilts (private) =="]
    latest = (data or {}).get("latest")
    if not latest:
        return lines + ["Unavailable: no retained publish observations."]
    for label, row in (("Latest " + latest["at"], latest), ("24h medians", data["last24h"])):
        # Nested counts and pairs retain their labels in the private plain-text report.
        values = {key: value for key, value in row.items() if key not in {"at", "examples"}}
        lines.append(label + ": " + json.dumps(values, sort_keys=True))
        lines.append("Examples: " + ("; ".join(row.get("examples", [])[:3]) or "none"))
    return lines
