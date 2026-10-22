"""Qualified shopper prices from existing, budgeted Keepa product reads."""

from datetime import timedelta
import re

from . import buybox
from .savings import exceeds_price_tolerance
from .util import iso, parse_iso
from .verify import _price_hidden_by_promo, keepa_reading_at, keepa_time, price_confirmed_at

VISIBLE_META = "click_price_visible"
PICKS_META = "click_price_picks"
WALL_META = "click_price_wall"
HOME_FIRST = 120
WALL_CHECKS = 300
BLOCK = re.compile(r"/\*click-price\*/(.*?)/\*/click-price\*/", re.S)


def template(text, enabled):
    return BLOCK.sub(lambda m: m[1] if enabled else "", text)


def remember(deal, product, observed, now):
    """Persist facts only. Missing or older data cannot erase a known gap."""
    if not buybox.eligible(deal) or not deal.get("asin") or product.get("asin") != deal["asin"]:
        return
    # Carry an existing gap into the new check before either check is replaced.
    if not deal.get("click_price_check") and has_gap(deal, now):
        legacy = deal["buybox_check"]
        deal["click_price_check"] = dict(legacy, buybox=legacy["price"])
    values = buybox.prices(product)
    stats = product.get("stats") or {}
    raw = keepa_time(stats.get("lastBuyBoxUpdate")) if isinstance(stats, dict) else None
    at = keepa_reading_at(raw, now)
    if raw is not None and at is None:
        return  # beyond the real clock: never a future confirmation
    previous = deal.get("click_price_check") or {}
    same = previous.get("asin") == deal["asin"] and previous.get("final") == deal["final_price"]
    previous_at = parse_iso(previous.get("at") or "") if same else None
    reading_at = at if values["buybox"] is not None else observed
    if (reading_at is not None and reading_at > now) or (
            previous_at is not None and reading_at is not None and reading_at < previous_at <= now):
        return
    if (same and buybox.number(previous.get("buybox")) is not None
            and previous["buybox"] != deal["final_price"]
            and (values["buybox"] is None or at is None)):
        return
    deal["click_price_check"] = {
        "asin": deal["asin"], "final": deal["final_price"], "at": iso(at) if at else "",
        "buybox": values["buybox"], "amazon": values["amazon"], "new": values["new"],
        "variation_child": bool(product.get("parentAsin")),
    }


def _reading(deal, now):
    if not buybox.eligible(deal):
        return None
    check = deal.get("click_price_check") or {}
    # Old facts preserve gaps, but lack proof of an independent Buy Box clock.
    legacy = deal.get("buybox_check") or {}
    if (not check and legacy.get("source") == "buybox"
            and buybox.number(legacy.get("price")) is not None
            and exceeds_price_tolerance(legacy["price"], deal["final_price"])):
        check = dict(legacy, buybox=legacy["price"])
    at, price = parse_iso(check.get("at") or ""), buybox.number(check.get("buybox"))
    since = [t for name in ("first_seen_at", "price_since")
             if (t := parse_iso(deal.get(name) or "")) is not None]
    if (not deal.get("asin") or check.get("asin") != deal["asin"]
            or check.get("final") != deal["final_price"] or price is None or at is None
            or at > now or not since or at < max(since)):
        return None
    return price, at


def has_gap(deal, now):
    """A retained gap still hides after its reading becomes Unknown."""
    reading = _reading(deal, now)
    return bool(reading and not _price_hidden_by_promo(deal)
                and reading[0] != deal["final_price"])


def status(deal, cfg, now):
    if not buybox.eligible(deal):
        return None
    if _price_hidden_by_promo(deal):
        return "promo"
    reading = _reading(deal, now)
    if reading is None:
        return "unknown"
    price, at = reading
    previous = price_confirmed_at(deal)
    if (now - at > timedelta(minutes=cfg.keepa_buybox_interval_min)
            or (previous is not None and at < previous)):
        return "unknown"
    # Tolerance judges whether an offer is still live, never its displayed price.
    return "known_above" if price != deal["final_price"] else "known_ok"


def excluded(deal, cfg, now):
    return cfg.keepa_click_price_rule == "on" and status(deal, cfg, now) in {"unknown", "known_above"}


def select_sizes(deal, cfg, now):
    return bool(cfg.keepa_click_price_rule == "on" and deal.get("category") == "fashion"
                and (deal.get("click_price_check") or {}).get("variation_child")
                and status(deal, cfg, now) == "known_ok")


def home_rows(rows):
    """Unknown offers may follow 120 other cards, even with sparse supply."""
    known = [d for d in rows if not d.get("click_price_unknown")]
    return known + [d for d in rows if d.get("click_price_unknown")] if len(known) >= HOME_FIRST else known


def summary(db, cfg, now, picks, wall, extra_tokens=0):
    result = {"mode": cfg.keepa_click_price_rule, "extra_tokens": extra_tokens,
              "extra_tokens_basis": "reserved", "examples": []}
    seen = set()
    for name, rows in (("top_picks", picks), ("wall", wall)):
        counts = dict.fromkeys(("known_ok", "known_above", "unknown", "promo"), 0)
        for row in rows:
            if row.get("locked") or row.get("status", "alive") != "alive":
                continue
            deal = (db.get_deal(row["dedupe_key"]) if db is not None else None) or row
            kind = status(deal, cfg, now)
            if kind is None:
                continue
            counts[kind] += 1
            if deal.get("asin") and deal["asin"] not in seen and len(result["examples"]) < 5:
                seen.add(deal.get("asin"))
                check = deal.get("click_price_check") or {}
                result["examples"].append({"asin": deal.get("asin"), "final": deal.get("final_price"),
                    **{key: check.get(key) for key in ("buybox", "amazon", "new")}})
        result[name] = counts
    return result
