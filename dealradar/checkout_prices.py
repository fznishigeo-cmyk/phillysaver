"""Optional checkout-price context from existing paid product rechecks."""

from datetime import timedelta
from html import escape
import re

from . import buybox
from .util import iso, parse_iso
from .verify import _price_hidden_by_promo, keepa_time

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
    at, price = keepa_time(stamp), values[source]
    if price is None or at is None or at > now:
        return
    previous = reading(deal, cfg, now)
    if previous and at < parse_iso(previous["at"]):
        return
    deal["checkout_price_reading"] = {"asin": deal.get("asin"), "final": deal["final_price"],
                                       "price": price, "at": iso(at)}


def reading(deal, cfg, now):
    check = deal.get("checkout_price_reading")
    if not buybox.eligible(deal) or not isinstance(check, dict):
        return None
    at = parse_iso(check.get("at") or "")
    since = parse_iso(deal.get("price_since") or deal.get("first_seen_at") or "")
    if (not deal.get("asin") or check.get("asin") != deal["asin"]
            or check.get("final") != deal["final_price"] or buybox.number(check.get("price")) is None
            or at is None or not now - timedelta(hours=cfg.keepa_show_max_unconfirmed_h) <= at <= now
            or (since is not None and at < since)):
        return None
    return check


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
    return dict(in_top_picks=0, skipped_age=0, skipped_window=0, labelled=0, unlabelled_no_reading=0)
