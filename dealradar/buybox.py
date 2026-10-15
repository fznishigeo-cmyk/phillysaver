"""Private click-price measurements from already-paid Keepa product records."""

from dataclasses import replace
import math

from .savings import exceeds_price_tolerance
from .util import iso, parse_iso
from .verify import is_amazon_deal, is_used_deal, keepa_time


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def prices(product):
    """USD facts only; unknown shipping never becomes free shipping.

    Keepa's Stats.buyBoxPrice/Shipping are cents; current[18] already includes
    shipping: https://keepa.com/api-docs/statistics-object.html and
    https://github.com/keepacom/api_backend/blob/master/src/main/java/com/keepa/api/backend/structs/Product.java
    """
    stats = product.get("stats")
    stats = stats if isinstance(stats, dict) else {}
    current = stats.get("current")

    def slot(index):
        value = number(current[index]) if isinstance(current, list) and len(current) > index else None
        return value / 100 if value is not None else None

    total = slot(18)
    price, shipping = number(stats.get("buyBoxPrice")), number(stats.get("buyBoxShipping"))
    if price is not None and shipping is not None:
        total = (price + shipping) / 100
    if stats.get("buyBoxIsUnqualified") is True:
        total = None
    amazon_seller = stats.get("buyBoxIsAmazon")
    return {"amazon": slot(0), "new": slot(1), "buybox": total,
            "buybox_is_amazon": amazon_seller if type(amazon_seller) is bool else None}


def eligible(deal):
    return is_amazon_deal(deal) and not is_used_deal(deal) and number(deal.get("final_price")) is not None


def empty_summary():
    return {"checked": 0, "confirmed_amazon": 0, "confirmed_new": 0,
            "not_confirmed": 0, "amazon_above_10pct": 0, "largest_gaps": []}


def measure(summary, deal, product, verdict):
    """One row per rechecked new Amazon deal, before any price correction.

    Attribute confirmations to the minimum slot used by today's rule; ties
    go to AMAZON. The gap count includes promo prices and inconclusive reads.
    """
    if not eligible(deal):
        return
    values = prices(product)
    amazon, new = values["amazon"], values["new"]
    summary["checked"] += 1
    if verdict is True and (amazon is not None or new is not None):
        source = "amazon" if amazon is not None and (new is None or amazon <= new) else "new"
        summary["confirmed_" + source] += 1
    else:
        summary["not_confirmed"] += 1
    if amazon is not None and exceeds_price_tolerance(amazon, deal["final_price"]):
        summary["amazon_above_10pct"] += 1
        summary["largest_gaps"].append({"asin": deal["asin"], "final": deal["final_price"],
                                       "amazon": amazon, "new": new})
        summary["largest_gaps"].sort(key=lambda r: (-(r["amazon"] - r["final"]), r["asin"]))
        del summary["largest_gaps"][5:]


def remember(deal, product, observed, *, now):
    """Store only compact facts; no raw response, seller ID, or offer text."""
    values = prices(product)
    source = "buybox" if values["buybox"] is not None else "amazon" if values["amazon"] is not None else "new"
    if source == "buybox":
        stamp = (product.get("stats") or {}).get("lastBuyBoxUpdate")
        if stamp is not None:
            observed = keepa_time(stamp)  # never borrow the listing's newer clock
    previous = deal.get("buybox_check") or {}
    previous_at = parse_iso(previous.get("at") or "")
    if (values[source] is None or observed is None or observed > now
            or (previous.get("asin") == deal["asin"] and previous.get("final") == deal["final_price"]
                and previous_at is not None and observed < previous_at <= now)):
        return  # a missing/older reading cannot clear an existing price gap
    deal["buybox_check"] = {"asin": deal["asin"], "final": deal["final_price"],
                            "at": iso(observed), "source": source, "price": values[source]}


def would_unconfirm(deal, now):
    if not eligible(deal):
        return False
    check = deal.get("buybox_check")
    if not isinstance(check, dict) or check.get("source") not in {"buybox", "amazon"}:
        return False  # no shopper-price data: preserve today's rule
    at = parse_iso(check.get("at") or "")
    since = parse_iso(deal.get("price_since") or deal.get("first_seen_at") or "")
    price = number(check.get("price"))
    return bool(check.get("asin") == deal.get("asin") and check.get("final") == deal["final_price"]
                and at is not None and at <= now and (since is None or at >= since)
                and price is not None and exceeds_price_tolerance(price, deal["final_price"]))


def shadow_summary(db, cfg, now, public_deals):
    """Count only actually public, confirmed rows; no locked/stale/private rows.

    This is a shadow counterfactual, not an on-mode removal ledger. Facts
    from previous runs remain useful until the advertised price changes.
    """
    from .publish.price_labels import qualifies
    result = {"mode": cfg.keepa_buybox_rule, "evaluated": 0, "would_label": 0, "would_hide": 0}
    if cfg.keepa_buybox_rule != "shadow":
        return result
    enforced = replace(cfg, keepa_buybox_rule="on")
    seen = set()
    for row in public_deals:
        key = row.get("dedupe_key")
        if key in seen or row.get("locked") or row.get("price_unconfirmed") or row.get("status") != "alive":
            continue
        seen.add(key)
        deal = db.get_deal(key)
        if deal is None or not eligible(deal):
            continue
        if deal.get("buybox_check"):
            result["evaluated"] += 1
        if would_unconfirm(deal, now):
            result["would_label" if qualifies(deal, enforced, now) else "would_hide"] += 1
    return result
