"""Operator 2026-10-23: where the typed-words box's two Amazon links go, one switch per tool.

The "Shopping for <words>?" box (tools-merge-words.js) keeps its texts, colours, order and
layout; only its two links change, and neither carries the $50 cap (p_36):
- Review Checker (~20:45Z, A51): yellow "See highly rated <words>" = Best Sellers + All
  Discounts; blue "See <words> discounts" = Featured (Amazon's default, no s=) + coupons.
- Fake Deal Spotter and its keyword pages (~20:50Z, A52): blue = Featured + Today's Deals;
  yellow = Best Sellers + coupons.
This pass rewrites the two published scripts after every other writer (amazon_price_cap
reads only .html, so it cannot add the cap back) and re-versions the pages that load them.
Every other exit keeps its cap. Needs tools_merge; review_checker_word_links /
deal_spotter_word_links false/missing = previous bytes.
"""

import hashlib
import re

from . import tools_merge

# Amazon's p_n_deal_type filters and its Best Sellers sort.
ALL_DISCOUNTS, COUPONS, TODAYS_DEALS = "23566065011", "210906366011", "23566064011"
BEST_SELLERS = "&s=exact-aware-popularity-rank"
# Published script: (switch, yellow link's filter and sort, blue link's filter).
TOOLS = {
    "review-checker.js": ("review_checker_word_links", "&rh=p_n_deal_type%3A" + ALL_DISCOUNTS + BEST_SELLERS,
                          "&rh=p_n_deal_type%3A" + COUPONS),
    "deal-spotter.js": ("deal_spotter_word_links", "&rh=p_n_deal_type%3A" + COUPONS + BEST_SELLERS,
                        "&rh=p_n_deal_type%3A" + TODAYS_DEALS),
}
# The yellow link's sort (tool-name-panel.js) and the blue link's coupons filter, capped or not
# (tool-pages.js via buttons_to_tools and amazon_price_cap).
RATED = re.compile(re.escape("'&s=review-rank&tag='"))
DISCOUNT = re.compile(r'"&rh=p_n_deal_type%3A210906366011(?:%2Cp_36%3A-\d+)?&"')


def enabled(cfg, name):
    return getattr(cfg, TOOLS[name][0], False) is True and tools_merge.enabled(cfg)


def links(script, rated, discount):
    """The words box's two URL builders get the new query parts; nothing else in the script changes."""
    for name, old, new in (("highlyRatedUrl", RATED, "'" + rated + "&tag='"),
                           ("toolDiscount", DISCOUNT, '"' + discount + '&"')):
        found = list(re.finditer(r"^function " + name + r"\(.*?^\}", script, re.S | re.M))
        if len(found) != 1 or len(old.findall(found[0][0])) != 1:
            raise ValueError("word_links: " + name + " changed; update dealradar/publish/word_links.py")
        body = found[0]
        script = script[:body.start()] + old.sub(lambda _: new, body[0]) + script[body.end():]
    return script


def _version(text):
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def publish(out, cfg):
    refs = {}
    for name, (_, rated, discount) in TOOLS.items():
        path = out / name
        if not enabled(cfg, name) or not path.exists():
            continue
        old = path.read_text()
        new = links(old, rated, discount)
        path.write_text(new)
        refs["/" + name + "?v=" + _version(old)] = "/" + name + "?v=" + _version(new)
    if not refs:
        return
    for path in out.rglob("*.html"):
        html = new = path.read_text()
        for old, ref in refs.items():
            new = new.replace(old, ref)
        if new != html:
            path.write_text(new)
