"""Operator 2026-10-22 (A34) and C16: a tool price reads like Amazon's item price, with
"+ $25.15 shipping" under it, or "incl. shipping" under a Buy Box total that has no split.

Fake Deal Spotter already shows it; this adds the same note to the homepage's "From Amazon
right now" results (also Analyze's) and to the Review Checker report (Codex review
2026-10-23 #6). price_shipping_note false = previous bytes.
"""

import hashlib
import re
from pathlib import Path

from . import search_more
from .template_slots import replace_slot

CARD = "      return '<article class=\"card\">' +\n"
SHIP = ('      // A34/C16, as on Fake Deal Spotter: Amazon\'s item price with its shipping, or a Buy Box total.\n'
        '      var ship = Number.isSafeInteger(d.shipping_cents) && d.shipping_cents > 0 ?\n'
        '        "+ $" + (d.shipping_cents / 100).toFixed(2) + " shipping" : d.price_includes_shipping === true ? "incl. shipping" : "";\n')
LINE = "</p><p class=\"headline\">Amazon price as of '"
NOTE = "</p>' + (ship ? '<p class=\"headline\">' + ship + '</p>' : '') + '<p class=\"headline\">Amazon price as of '"

REVIEW_PRICE = ("      copy.append(element('p', new Intl.NumberFormat('en-US', {style:'currency',currency:'USD'})"
                ".format(item.price_cents / 100), 'current-price'));\n")
REVIEW_NOTE = ("      var price = element('p', new Intl.NumberFormat('en-US', {style:'currency',currency:'USD'})"
               ".format(item.price_cents / 100), 'current-price');\n"
               "      // A34/C16, as on Fake Deal Spotter: Amazon's item price with its shipping, or a Buy Box total.\n"
               "      var shipping = Number.isSafeInteger(item.shipping_cents) && item.shipping_cents > 0 ?\n"
               "        '+ $' + (item.shipping_cents / 100).toFixed(2) + ' shipping' : item.price_includes_shipping === true ? 'incl. shipping' : '';\n"
               "      if (shipping) price.append(element('small', shipping, 'result-shipping'));\n"
               "      copy.append(price);\n")
# The spotter's own rule (deal-spotter.css .result-shipping).
REVIEW_CSS = ".result-shipping{display:block;font-size:14px;font-weight:600;letter-spacing:0;color:var(--muted);line-height:1.5}"


def enabled(cfg):
    return getattr(cfg, "price_shipping_note", False) is True


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]


def cards(script, name):
    """The shared Amazon results card (search-results.js) in the homepage or search-more.js."""
    script = replace_slot(script, CARD, SHIP + CARD, template_name=name, slot_name="shipping-note")
    return replace_slot(script, LINE, NOTE, template_name=name, slot_name="shipping-line")


def home(html, cfg):
    if not enabled(cfg) or "  function amazonResultsPaint(q, items) {" not in html:
        return html
    return cards(html, "index.html")


def publish(out, cfg):
    """search_more moves the card into its own asset: add the note there and re-version it."""
    asset = out / search_more.ASSET
    if not enabled(cfg) or not search_more.enabled(cfg) or not asset.exists():
        return
    script = cards(asset.read_text(), search_more.ASSET)
    asset.write_text(script, encoding="utf-8")
    home_page = out / "index.html"
    html = home_page.read_text()
    old = re.findall(r'/search-more\.js\?v=[0-9a-f]+', html)
    if len(old) != 1:
        raise ValueError("index.html: missing slot search-more-version")
    home_page.write_text(html.replace(old[0], '/search-more.js?v=' + hashlib.sha256(script.encode()).hexdigest()[:12]),
                         encoding="utf-8")


def review_script(script, cfg):
    if not enabled(cfg):
        return script
    return replace_slot(script, REVIEW_PRICE, REVIEW_NOTE, template_name="review-checker.js", slot_name="shipping-note")


def review_css(cfg):
    return REVIEW_CSS if enabled(cfg) else ''
