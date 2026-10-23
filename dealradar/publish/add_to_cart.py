"""Operator 2026-10-22: "Add to Amazon cart" beside the Amazon button.

Amazon's own Add to Cart form (Product Advertising API 5.0 docs, "Add to Cart
form"): https://www.amazon.com/gp/aws/cart/add.html with ASIN.1, Quantity.1 and
AssociateTag, our affiliate.amazon_tag. It is a plain link the shopper clicks:
no script submits it, no hidden form, no prefetch, nothing added automatically.

Only new-condition Amazon items with a known ASIN: an Amazon deal page whose
Get deal is our tagged Amazon product link (its th=1&psc=1 size/colour ASIN
when it has one) and the Fake Deal Spotter's graded result. Never other
stores, used, open-box, Warehouse/Resale/Renewed offers or a typed product
name. add_to_cart.enabled false/missing = previous bytes.
"""

import re
from html import escape
from urllib.parse import parse_qsl, urlencode, urlsplit

from ..amazon_used import used_offer
from .template_slots import replace_slot

CART = "https://www.amazon.com/gp/aws/cart/add.html"
LABEL = "Add to Amazon cart"
REL = "sponsored nofollow noopener"
AMAZON_HOSTS = frozenset({"amazon.com", "www.amazon.com", "smile.amazon.com", "m.amazon.com"})
ASIN_PATH = re.compile(r"/(?:dp|gp/product)/([A-Za-z0-9]{10})(?:/|$)")

# Deal pages: an outline twin of Get deal in the page's teal, as tall as it (48px+).
# Beside Get deal on wide screens; under it, full width, on phones (Get deal stays first).
STYLE = ('<style>.deal-detail>.addcart{display:inline-flex;align-items:center;justify-content:center;'
         'min-height:48px;padding:11px 22px;margin-left:10px;border:2px solid var(--accent);border-radius:10px;'
         'background:transparent;color:var(--accent);font-weight:600;font-size:16px;text-decoration:none}'
         '.deal-detail>.addcart:hover{background:var(--chip);text-decoration:none}'
         '.deal-detail>.addcart:focus-visible{outline:2px solid var(--accent);outline-offset:3px}'
         '@media (max-width:640px){.deal-detail>.addcart{width:100%;margin:10px 0 0}}</style>')

# Deal pages: the same deal_click as Get deal, told apart by button: "add_to_cart".
DEAL_CLICK = ('        category: go.getAttribute("data-category") || "other", method: method});\n')
DEAL_CLICK_CART = ('        category: go.getAttribute("data-category") || "other", method: method,\n'
                   '        button: go.hasAttribute("data-cart") ? "add_to_cart" : ""});\n')
DISPATCH = '    if (name === "deal_click" && pageMethods.indexOf(p.method) === -1) delete p.method;\n'
DISPATCH_CART = '    if (name === "deal_click" && params && params.button === "add_to_cart") p.button = "add_to_cart";\n'

# Fake Deal Spotter: a secondary button right under "Get this deal on Amazon".
SPOTTER_BUY = ('<a id="result-buy" class="primary" target="_blank" rel="sponsored nofollow noopener">'
               'Get this deal on Amazon <span aria-hidden="true">↗</span></a>')
SPOTTER_CART = f'<a id="result-cart" class="secondary" target="_blank" rel="{REL}">{LABEL}</a>'
SPOTTER_HREF = "    $('result-buy').href = toolAmazon(item.asin);\n"
SPOTTER_CART_HREF = ("    if ($('result-cart')) $('result-cart').href = '" + CART + "?ASIN.1=' + encodeURIComponent(item.asin) +\n"
                     "      '&Quantity.1=1&AssociateTag=' + encodeURIComponent(toolSettings.tag);\n")


def enabled(cfg):
    return getattr(cfg, "add_to_cart", {}).get("enabled") is True and bool(getattr(cfg, "affiliate_amazon_tag", ""))


def cart_url(asin, tag):
    """One item, quantity 1, credited to our Associates tag."""
    return CART + "?" + urlencode([("ASIN.1", asin), ("Quantity.1", "1"), ("AssociateTag", tag)])


def deal_asin(d, tag):
    """A new-condition Amazon deal's ASIN when its Get deal is our tagged product link; else ""."""
    try:
        parts = urlsplit(str(d.get("url") or ""))
        host = (parts.hostname or "").lower()
    except ValueError:
        return ""
    match = ASIN_PATH.search(parts.path)
    if (not tag or parts.scheme != "https" or host not in AMAZON_HOSTS or not match
            or [v for k, v in parse_qsl(parts.query) if k == "tag"] != [tag] or used_offer(d)):
        return ""
    return match[1].upper()


def deal_button(d, cfg):
    asin = deal_asin(d, cfg.affiliate_amazon_tag) if enabled(cfg) else ""
    if not asin:
        return ""
    from .seo import _outbound_attrs
    # Store and category only: never the requirements event or first-party click counts.
    attrs = _outbound_attrs({"store": d.get("store"), "category": d.get("category")})
    return (f'<a class="addcart" href="{escape(cart_url(asin, cfg.affiliate_amazon_tag))}" target="_blank" '
            f'rel="{REL}"{attrs} data-cart>{LABEL}</a>')


def publish(out, cfg):
    """Deal pages with the button: deal_click carries button: "add_to_cart" (after apply_tracking)."""
    if not enabled(cfg):
        return
    for path in (out / "deal").glob("*/index.html"):
        html = path.read_text(encoding="utf-8")
        if " data-cart>" not in html or DEAL_CLICK_CART in html:
            continue
        html = replace_slot(html, DEAL_CLICK, DEAL_CLICK_CART, template_name="deal page", slot_name="add-to-cart")
        if DISPATCH in html:  # no analytics tag configured = no wpTrack to extend
            html = replace_slot(html, DISPATCH, DISPATCH + DISPATCH_CART,
                                template_name="deal page", slot_name="add-to-cart-tracking")
        path.write_text(html, encoding="utf-8")


def spotter_page(html, cfg):
    if not enabled(cfg):
        return html
    return replace_slot(html, SPOTTER_BUY, SPOTTER_BUY + SPOTTER_CART,
                        template_name="deal-spotter.html", slot_name="add-to-cart")


def spotter_script(script, cfg):
    if not enabled(cfg):
        return script
    return replace_slot(script, SPOTTER_HREF, SPOTTER_HREF + SPOTTER_CART_HREF,
                        template_name="deal-spotter.js", slot_name="add-to-cart")
