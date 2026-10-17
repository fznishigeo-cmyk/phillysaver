"""Private Amazon reference readings and the shared public savings projection."""

import math
import re

from .savings import amazon_list_price
from .util import iso, parse_iso


def enabled(cfg):
    return getattr(cfg, "amazon_reference", "history") == "list_price"


def list_reading(record, observed):
    """Read only Amazon's List Price slot from an already obtained API record."""
    stats = record.get("stats")
    current = stats.get("current") if isinstance(stats, dict) else record.get("current")
    price = current[4] if isinstance(current, (list, tuple)) and len(current) > 4 else None
    if price is None:
        csv = record.get("csv")
        series = csv[4] if isinstance(csv, list) and len(csv) > 4 else None
        if isinstance(series, list) and len(series) >= 2 and len(series) % 2 == 0:
            price = series[-1]
    price = price / 100 if type(price) in {int, float} and math.isfinite(price) and price > 0 else None
    if price is not None:
        from .models import Evidence, ExtractedDeal
        from .parse.extractor import verify_evidence

        # As with the Keepa post mapper, the licensed API fact supplies the
        # source text. The existing evidence verifier remains the gate.
        quote = f"List price on Amazon: ${price:,.2f}."
        fact = ExtractedDeal(str(record.get("title") or ""), "Amazon", "", "keepa",
                             regular_price=price, evidence=Evidence(regular_price_quote=quote))
        if not verify_evidence(fact, quote)[0]:
            price = None
    return {"asin": str(record.get("asin") or "").upper(), "observed_at": iso(observed), "price": price}


def remember_list_price(deal, reading, *, now):
    """Feed, ingest and paid reads cannot regress a stored display reference."""
    from .verify import price_confirmed_at

    if not isinstance(reading, dict) or reading.get("asin") != deal.get("asin"):
        return False
    at = parse_iso(reading.get("observed_at") or "")
    if at is None or at > now:
        return False
    previous = deal.get("keepa_list_price") or {}
    clocks = [clock for clock in (
        parse_iso(previous.get("observed_at") or "") if previous.get("asin") == deal.get("asin") else None,
        price_confirmed_at(deal)) if clock is not None and clock <= now]
    if clocks and at < max(clocks):
        return False
    if previous == reading:
        return False
    deal["keepa_list_price"] = reading
    return True


def uses_api_reference(deal):
    from .verify import is_amazon_deal, is_used_deal

    if not is_amazon_deal(deal) or is_used_deal(deal):
        return False
    label = str(deal.get("regular_price_basis") or "").lower()
    if label:
        return "average" in label or label in {"list price on amazon", "amazon_list_price"}
    # Older Keepa rows predate the explicit basis field. A partner's quoted
    # regular price stays source-stated even when Keepa later confirms it.
    source = str(deal.get("source_name") or "").lower()
    credits = deal.get("credits") or []
    sightings = deal.get("sightings") or []
    first = str((sightings[0] if sightings else {}).get("source_name") or "").lower()
    from .source_policy import host_of
    source_host = host_of((sightings[0] if sightings else {}).get("source_url") or "")
    # A later partner price explicitly clears the old API basis. Keep that
    # statement even when the oldest credit is the original Keepa sighting.
    if "regular_price_basis" in deal and any(
            not str(s.get("source_name") or "").lower().startswith("keepa")
            and host_of(s.get("source_url") or "") not in {"keepa.com", "www.keepa.com"}
            for s in sightings):
        return False
    return (source.startswith("keepa") or first.startswith("keepa")
            or source_host in {"keepa.com", "www.keepa.com"}
            or bool(credits and credits[0].get("name") == "Amazon price drop"))


def project(deal, cfg, now):
    """Never mutate backend prices, floors or scores; never export the hidden pct."""
    if not enabled(cfg):
        return deal
    from .verify import is_used_deal, price_confirmed_at

    result = dict(deal)
    if uses_api_reference(deal):
        savings = amazon_list_price(deal.get("final_price"), deal.get("keepa_list_price"),
                                    asin=deal.get("asin"), confirmed_at=price_confirmed_at(deal),
                                    now=now, max_age_h=cfg.keepa_show_max_unconfirmed_h)
        for name in ("regular_price", "pct_at_checkout", "pct_with_rebates", "abs_savings"):
            result[name] = getattr(savings, name)
        result["basis"] = savings.basis
        result["regular_price_basis"] = "List price" if savings.computable else ""
        result["price_drop"] = not savings.computable
    if result.get("verify_tier") == "history-checked":
        result["verify_tier"] = "verified"
    if is_used_deal(deal) and result.get("regular_price_basis") == "lowest new price (current offer)":
        result["regular_price_basis"] = "new price on Amazon"
    # Also covers legacy non-Amazon history bases. Source-stated was-prices
    # retain their facts and attribution.
    if re.search(r"average|usual|lowest|highest|history|\d+-day", str(result.get("regular_price_basis") or ""), re.I):
        result.update(regular_price=None, pct_at_checkout=None, pct_with_rebates=None,
                      abs_savings=None, regular_price_basis="", price_drop=True)
    return result


def template(text, cfg):
    """The opt-in display changes leave every legacy template byte intact."""
    if not enabled(cfg):
        return text
    replacements = {
        'price += " <s>$"': 'price += (d.regular_price_basis === "List price" ? " List price" : "") + " <s>$"',
        'if (usedTag(d)) media': 'if (pct == null && d.price_drop) media += \'<span class="flag">Price drop</span>\';\n    if (usedTag(d)) media',
        'num(d.pct_at_checkout) >= alertFloor': '(d.primary_offer === true || (d.primary_offer == null && num(d.pct_at_checkout) >= alertFloor))',
        'num(d.pct_at_checkout) < alertFloor': '(d.primary_offer === false || (d.primary_offer == null && num(d.pct_at_checkout) < alertFloor))',
        '"history-checked": "✓ price history"': '"verified": "✓ verified"',
        'the lowest prices': 'prices from low to high',
        'the highest price': 'the upper price',
        'Lowest current price:': 'Current price from:',
        'if (floor != null && !priceOnly.length)':
            'if (floor != null && !priceOnly.length && state.deals.every(function (d) { return d.pct_at_checkout != null && d.pct_at_checkout >= floor; }))',
        '"under " + Math.round(alertFloor) + "% off"': '"more verified offers"',
        'stamp: "-" + pct + "%",': 'stamp: d.price_drop ? "Price drop" : d.pct_at_checkout == null ? "" : "-" + pct + "%",',
        'plain: "-" + pct + "% " + plain,': 'plain: (d.price_drop ? "Price drop " : d.pct_at_checkout == null ? "" : "-" + pct + "% ") + plain,',
        'stamp: d.pct_at_checkout == null ? "" : "-" + pct + "%",':
            'stamp: d.price_drop ? "Price drop" : d.pct_at_checkout == null ? "" : "-" + pct + "%",',
        'plain: (d.pct_at_checkout == null ? "" : "-" + pct + "% ") + plain,':
            'plain: (d.price_drop ? "Price drop " : d.pct_at_checkout == null ? "" : "-" + pct + "% ") + plain,',
        'other sellers and recent prices': 'other sellers',
        'Check price history': 'Compare matching products',
        'Price history can help you see whether a sale is unusual or repeats often.':
            'Compare the current offers from different sellers.',
        'prices may differ from what an item usually costs.': 'prices are supplied by sellers.',
        'A “2+ sources” badge means independent sources agree; a “price history” badge\n   means the offer compares favorably with recorded prices.':
            'A “2+ sources” badge means independent sources agree; a “verified” badge means the price passed our checks.',
        'the price is unusually low': 'the offer is good value',
        '<span class="badge tier-history-checked">✓ price history</span>\n   means the offer compares favorably with recorded prices.':
            '<span class="badge tier-verified">✓ verified</span> means the price passed our checks.',
        'The answer compares our recorded checks when we have enough history.':
            'Results show the current verified offer.',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # The Saved/Surprise script is inserted after the home template pass.
    text = text.replace('num(d.pct_at_checkout) >= floor',
                        '(d.primary_offer === true || (d.primary_offer == null && num(d.pct_at_checkout) >= floor))')
    text = text.replace('num(d.pct_at_checkout) < floor',
                        '(d.primary_offer === false || (d.primary_offer == null && num(d.pct_at_checkout) < floor))')
    return text
