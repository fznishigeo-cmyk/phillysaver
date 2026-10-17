"""Honest open-box scarcity from already stored source statements only."""

import re

from ..conditions import normalize_conditions
from ..util import iso, parse_iso

_CURRENT_COUNT = re.compile(
    r"(?:(?:now|today|for|at|is|are|on sale|currently)\s+)*"
    r"(?:there (?:is|are)\s+)?only\s+([1-3])\s+left(?:\s+in\s+stock)?"
    r"(?:\s+(?:now|today))?", re.I)
STYLE = "\n.condtag.scarcity{top:38px;font-size:12px;letter-spacing:0;white-space:nowrap}\n"


def _current_count(quote, item):
    # Accept a standalone current assertion alongside the item and price.
    # Extra prose (history, negation, conditions, restocks) is ambiguous.
    if not item or item not in quote or len(quote) > 300:
        return None
    context = quote.replace(item, "", 1)
    context = re.sub(r"\$\s*\d+(?:[.,]\d+)*", " ", context)
    context = re.sub(r"[:;,!.()–—-]", " ", context)
    match = _CURRENT_COUNT.fullmatch(" ".join(context.split()))
    return int(match[1]) if match else None


def capture(extracted, post, now):
    """Keep a short verified stock fact from the offer's own price quote.

    Titles alone are unverified. Ambiguous roundups and quotes that do not
    name this exact item fall back to the general scarcity wording.
    """
    from ..parse.extractor import verify_evidence

    quote = " ".join(extracted.evidence.final_price_quote.split())
    item = " ".join(extracted.item.split())
    count = _current_count(quote, item)
    if "open_box" not in extracted.conditions or extracted.link_shared \
            or extracted.final_price is None or count is None \
            or len(set(post.parsed)) > 1 or any(name != extracted.item for name, _ in post.parsed) \
            or not verify_evidence(extracted, post.text)[0]:
        return None
    return {"quote": quote, "item": extracted.item, "product_url": extracted.product_url,
            "price": extracted.final_price,
            "source_url": post.url, "checked_at": iso(min(now, post.fetched_at)),
            "published_at": iso(post.published_at or post.fetched_at)}


def facts(deal, now):
    if deal.get("status", "alive") != "alive" or "open_box" not in normalize_conditions(deal.get("conditions")):
        return None
    # Only ingestion's verified, offer-bound quote can supply a number.
    # Rechecks/reposts never renew its observation or publication clock.
    evidence = deal.get("open_box_stock_evidence") or {}
    seen = parse_iso(deal.get("first_seen_at") or "")
    checked = parse_iso(evidence.get("checked_at") or "")
    published = parse_iso(evidence.get("published_at") or "")
    stamps = (seen, checked, published)
    count = _current_count(evidence.get("quote") or "", " ".join((deal.get("item") or "").split()))
    bound = (evidence.get("item") == deal.get("item")
             and evidence.get("product_url") == (deal.get("canonical_url") or "")
             and evidence.get("price") == deal.get("final_price")
             and evidence.get("published_at") == deal.get("price_published_at")
             and any(s.get("source_url") == evidence.get("source_url")
                     and s.get("final_price") == evidence.get("price")
                     for s in deal.get("sightings") or []))
    if bound and count is not None and all(at is not None and 0 <= (now - at).total_seconds() <= 7200 for at in stamps):
        return {"count": count, "checked_at": iso(min(stamps))}
    return {}


def label(deal, now):
    if not deal.get("open_box_scarcity") or deal.get("status", "alive") != "alive":
        return ""
    stock = deal.get("open_box_stock") or {}
    at = parse_iso(stock.get("checked_at") or "")
    count = stock.get("count")
    fresh = at is not None and 0 <= (now - at).total_seconds() <= 7200
    return f"OPEN BOX · only {count} left" if type(count) is int and 1 <= count <= 3 and fresh else "OPEN BOX · often only 1"


def template(html, enabled, *, counts=False):
    if not enabled:
        return html
    html = html.replace("</style>", STYLE + "</style>", 1)
    label_js = '      return "OPEN BOX · often only 1";'
    if counts:
        label_js = '''      var stock = d.open_box_stock || {}, elapsed = Date.now() - Date.parse(stock.checked_at);
      return "OPEN BOX · " + (Number.isInteger(stock.count) && stock.count >= 1 && stock.count <= 3 &&
        elapsed >= 0 && elapsed <= 7200000 ? "only " + stock.count + " left" : "often only 1");'''
    html = html.replace('    return conds.indexOf("open_box") !== -1 ? "OPEN BOX"', '''    if (conds.indexOf("open_box") !== -1 && d.open_box_scarcity && d.status !== "dead") {
''' + label_js + '''
    }
    return conds.indexOf("open_box") !== -1 ? "OPEN BOX"''', 1)
    return html.replace("'<span class=\"condtag\">' + usedTag(d)",
                        "'<span class=\"condtag' + (d.open_box_scarcity && d.status !== \"dead\" ? ' scarcity' : '') + '\">' + usedTag(d)", 1)
