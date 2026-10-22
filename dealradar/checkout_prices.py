"""Optional checkout-price context from existing paid product rechecks."""

from datetime import timedelta
from html import escape
import re

from . import buybox
from .savings import page_price_moved
from .util import iso, parse_iso
from .verify import _price_hidden_by_promo, keepa_reading_at, keepa_time

BLOCK = re.compile(r"/\*checkout-price\*/(.*?)/\*/checkout-price\*/", re.S)
SUFFIXES = {"code": "until you enter the code", "coupon": "before the coupon",
            "subscribe_save": "before Subscribe & Save"}


def enabled(cfg):
    return bool(cfg and (cfg.front_page.get("checkout_price_deals") or {}).get("enabled", False))


def template(text, on):
    return BLOCK.sub(lambda m: m[1] if on else "", text)


def kind(deal):
    if not _price_hidden_by_promo(deal):
        return None
    steps = " ".join(str(s) for s in deal.get("stack_steps") or [])
    if deal.get("codes") or re.search(r"\bcode\b", steps, re.I):
        return "code"
    if "subscribe_save" in (deal.get("conditions") or []) or re.search("subscribe", steps, re.I):
        return "subscribe_save"
    return "coupon"


def remember(deal, product, cfg, now):
    """Never borrow fetch time or a listing clock for a Buy Box observation."""
    if not enabled(cfg) or not buybox.eligible(deal) or not _price_hidden_by_promo(deal):
        return
    values = buybox.prices(product)
    source = "buybox" if values["buybox"] is not None else "amazon"
    stats = product.get("stats") or {}
    stamp = stats.get("lastBuyBoxUpdate") if source == "buybox" else product.get("lastUpdate")
    at, price = keepa_reading_at(keepa_time(stamp), now), values[source]
    if price is None or at is None:
        return
    previous = _valid_reading(deal, deal.get("checkout_price_reading"), now)
    if previous and at < parse_iso(previous["at"]):
        return
    deal["checkout_price_reading"] = {"asin": deal.get("asin"), "final": deal["final_price"],
                                       "price": price, "at": iso(at)}
    if not deal.get("checkout_price_first_reading") and label(deal, cfg, now):
        deal["checkout_price_first_reading"] = dict(deal["checkout_price_reading"])


def _valid_reading(deal, check, now):
    if not buybox.eligible(deal) or not isinstance(check, dict):
        return None
    at = parse_iso(check.get("at") or "")
    since = parse_iso(deal.get("price_since") or deal.get("first_seen_at") or "")
    if (not deal.get("asin") or check.get("asin") != deal["asin"]
            or check.get("final") != deal["final_price"] or buybox.number(check.get("price")) is None
            or at is None or at > now
            or (since is not None and at < since)):
        return None
    return check


def reading(deal, cfg, now):
    check = _valid_reading(deal, deal.get("checkout_price_reading"), now)
    if check and now - parse_iso(check["at"]) <= timedelta(hours=cfg.keepa_show_max_unconfirmed_h):
        return check
    return None


def pick_evidence(deal, cfg, now):
    """Gone evidence wins; observation clocks never borrow a DB touch time."""
    options = cfg.front_page.get("checkout_price_deals") or {}
    end = parse_iso(deal.get("expires_at") or "")
    if (deal.get("status", "alive") != "alive" or deal.get("death") or deal.get("checkout_source_ended_at")
            or deal.get("gone_suspect_at") or "price_moved" in (deal.get("flags") or [])
            or (end is not None and end <= now)):
        return "skipped_expired", []
    first = _valid_reading(deal, deal.get("checkout_price_first_reading"), now)
    latest = _valid_reading(deal, deal.get("checkout_price_reading"), now)
    if first and latest and parse_iso(latest["at"]) >= parse_iso(first["at"]):
        if page_price_moved(latest["price"], first["price"], cfg.keepa_reprice_min_pct):
            return "skipped_page_price_moved", []
    else:
        latest = None  # an unbound or earlier reading cannot support unchanged math

    kept = []
    stated = parse_iso(deal.get("expires_published_at") or deal.get("first_seen_at") or "")
    if (end is not None and stated is not None and stated <= now
            and now < min(end, stated + timedelta(days=float(options.get("max_stated_days", 7))))):
        kept.append("stated_end")
    # Require active evidence even before the silence backstop. A larger
    # observation window cannot extend the configured maximum silence.
    silent = float(options.get("max_silent_h", 72))
    repost = float(options.get("repost_window_h", 24))
    if any(s.get("final_price") == deal.get("final_price")
           and (at := parse_iso(s.get("seen_at") or "")) is not None
           and now - timedelta(hours=repost) <= at <= now
           and now - at < timedelta(hours=silent)
           for s in deal.get("sightings") or []):
        kept.append("repost")
    if (latest and now - parse_iso(latest["at"]) <= timedelta(hours=cfg.keepa_pick_max_unconfirmed_h)
            and now - parse_iso(latest["at"]) < timedelta(hours=silent)):
        kept.append("page_price")
    return (None, kept) if kept else ("skipped_silent", [])


def label(deal, cfg, now):
    if not enabled(cfg) or deal.get("status", "alive") != "alive" or not kind(deal):
        return None
    check = reading(deal, cfg, now)
    if check is None or check["price"] <= deal["final_price"]:
        return None
    return {"price": check["price"], "kind": kind(deal)}


def text(public):
    label = public.get("checkout_price")
    if not label or public.get("status") == "dead":
        return ""
    return f'Amazon shows ${label["price"]:.2f} {SUFFIXES[label["kind"]]}'


def html(public):
    note = text(public)
    return f'<div class="checkout-price">{escape(note)}</div>' if note else ""


def summary():
    return dict(in_top_picks=0, skipped_expired=0, skipped_page_price_moved=0, skipped_silent=0,
                kept_by=dict(stated_end=0, repost=0, page_price=0),
                skipped_window=0, labelled=0, unlabelled_no_reading=0)
