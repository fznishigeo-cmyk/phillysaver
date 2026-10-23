"""Static site publisher: writes deals.json and copies templates (source health
goes to the operator's run summary, never to the public site).

Storage policy: only extracted facts, a product link, and credit to the source
are written — never full post text or images.
"""

from __future__ import annotations

import json
import hashlib
import logging
import re
import shutil
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..savings import display_pct, known_shipping, parse_stack_step
from ..config import AppConfig, redact_secrets, support_link, validated_drop_clock
from ..rank import appeal_summary, daily_spotlight, is_clothing, is_niche, photoless_expired, pick_order, pick_score, top_picks
from ..verify import (confirmation_coverage, is_amazon_deal, price_confirmed_at,
                       price_confirmation_recent, price_unconfirmed)
from ..source_policy import source_image_allowed
from ..util import iso, parse_iso
from .. import amazon_reference, checkout_prices, front_mix, popular_affordable
from . import ai_take, amazon_as_of, amazon_history, checker, checker_page, click_signal, compare_pages, dark, drop_rotation, indexnow, inventory, manifest_refresh, price_labels, product_pages, profiles, pwa, seo, seo_audit, traffic
from .paywall import is_open, split_teaser, teaser_memory
from .credits import _deal_source_hosts, _display_names
from .link_facts import routing_policy as link_policy
from . import credits, link_facts
from .link_facts import (LinkFacts, _link_destination, _needs_steps, _override_key,
                         _simple_code_deal, earning_link, is_competitor_tracker_host)
from .tracking import apply_tracking
from .template_slots import replace_slot, validate_slot
from . import comments
from . import search_results
from . import shipping_note
from . import search_boost
from . import search_box_glow
from . import same_product_spacing
from . import card_tidy
from . import page_polish
from . import header_polish
from . import all_discounts
from . import discounts_from_link
from . import tool_pages
from . import tool_pages_1020
from . import join_dialog
from . import search_polish_1019
from . import search_buttons_1019, search_discounts_button, card_click_1020, buttons_to_tools, amazon_price_cap, tools_new_tab, tool_buttons_outside, search_focus_ring, hide_timer_ticker
from . import phone_tagline_count, timer_center, sticky_buy_bar, soft_buttons, tools_merge
from . import timer_pill_ring
from . import phone_timer_raise
from . import search_circle
from . import search_analyze
from . import analyze_top_sellers
from . import search_analyze_lazy
from . import search_more
from . import drop_fresh
from . import fresh_more_plain
from . import keep_first_cards
from . import drop_shuffle

logger = logging.getLogger("dealradar.publish.site")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def _credit(sighting: dict[str, Any]) -> dict[str, str]:
    return link_facts._credit(sighting, _display_names())


#: meta key: the keys the last drops unlocked for visitors (paywall.teaser_memory).
_TEASER_META = "teaser_recent"
#: Candidate keys by score BEFORE confirmation/caps: an unconfirmed deal
#: must be able to earn a reading and become a pick on the next publish.
PICKS_META = "published_picks"
WALL_META = "published_wall"


def util_now():
    from ..util import now_utc
    return now_utc()


_PHOTOLESS_BLOCK = re.compile(r"/\*photoless\*/(.*?)/\*/photoless\*/", re.S)
_USED_TAB_ONLY_BLOCK = re.compile(r"/\*used-tab-only\*/(.*?)/\*/used-tab-only\*/", re.S)
_DEMOTE_WORDS_BLOCK = re.compile(r"/\*demote-words\*/(.*?)/\*/demote-words\*/", re.S)


_WATCH_BLOCK = re.compile(r"<!--watch-->(.*?)<!--/watch-->", re.S)
_SEARCH_AMAZON_BLOCK = re.compile(r"/\*search-amazon\*/(.*?)/\*/search-amazon\*/", re.S)
_HOME_LINK_RESETS_BLOCK = re.compile(r"/\*home-link-resets\*/(.*?)/\*/home-link-resets\*/", re.S)

# Phones only: on a multi-column desktop wall, reload restore drifted ~1.5k px
# in Chromium. Keep the first eight in every grid fully rendered, including
# grids split by the Join band. The real card's content height replaces the
# first-visit phone fallback (375px in the task-237 measurement) before the
# next paint.
OFFSCREEN_CARDS_STYLE = """\n@media (max-width:700px){#content{--offscreen-cards:auto}
#content > .grid:not(.spotlight-grid) > .card:nth-child(n+9){content-visibility:auto;
  contain-intrinsic-size:auto var(--offscreen-card-height,375px)}}
"""


@dataclass(frozen=True)
class _BrowseScript:
    """Producer: templates/browse.js; one {{BROWSE_SCRIPT}} in index.html,
    in every home access variant, never other pages. Resolve price presets,
    then Saved markers once. Insert after the existing page feature passes,
    before variant/legacy substitutions. Membership's hash appends the
    Saved-stripped bytes; support's hash appends marked bytes (even Saved-off).
    Keep those historical inputs and their concatenation order unchanged.
    """

    marked: str
    without_saved: str
    rendered: str


def _browse_script(price_presets: list[int], saved: bool = False,
                   home_link_resets: bool = False) -> _BrowseScript:
    marked = (TEMPLATES_DIR / "browse.js").read_text(encoding="utf-8")
    marked = _HOME_LINK_RESETS_BLOCK.sub(lambda m: m[1] if home_link_resets else "", marked)
    marked = marked.replace("{{PRICE_PRESETS}}", json.dumps(price_presets))
    without_saved = re.sub(r"<!--saved-->.*?<!--/saved-->", "", marked, flags=re.S)
    rendered = (re.sub(r"<!--saved-->(.*?)<!--/saved-->", lambda m: m.group(1), marked, flags=re.S)
                if saved else without_saved)
    return _BrowseScript(marked, without_saved, rendered)


def home_search_text(html: str, cfg) -> str:
    """What Google shows for the homepage: its <title> and meta description
    (operator 2026-10-17). Unset keeps the template's text, so bytes hold."""
    title = getattr(cfg, "home_title", "")
    description = getattr(cfg, "home_description", "")
    if title:
        html = re.sub(r"<title>[^<]*</title>", lambda _: f"<title>{escape(title)}</title>", html, count=1)
    if description:
        html = re.sub(r'<meta name="description" content="[^"]*">',
                      lambda _: f'<meta name="description" content="{escape(description)}">', html, count=1)
    return html


def _page_template(html: str, cfg, *, home: bool = False) -> str:
    """Existing feature passes, before the home browse slot is inserted."""
    from .. import click_price
    html = click_price.template(html, cfg.keepa_click_price_rule == "on")
    html = price_labels.template(html, cfg.unconfirmed_label_enabled or cfg.non_amazon_freshness_enabled
                                 or cfg.keepa_click_price_rule == "on")
    html = checkout_prices.template(html, checkout_prices.enabled(cfg))
    html = _PHOTOLESS_BLOCK.sub(lambda m: m[1] if cfg.photoless_last else "", html)
    html = _USED_TAB_ONLY_BLOCK.sub(lambda m: m[1] if cfg.front_page.get("used_tab_only") else "", html)
    html = _DEMOTE_WORDS_BLOCK.sub(lambda m: m[1] if cfg.front_page.get("demote_words") else "", html)
    html = amazon_as_of.template(html, cfg.amazon_as_of_enabled)
    html = amazon_history.template(html, cfg)
    html = amazon_reference.template(html, cfg)
    html = product_pages.template(html, cfg.seo_product_pages_enabled)
    html = ai_take.template(html, cfg.ai_take["enabled"])
    if cfg.seo_product_pages_enabled and home:
        html = html.replace("</style>", product_pages.LINK_STYLE + "</style>", 1)
    if (cfg.unconfirmed_label_enabled or cfg.non_amazon_freshness_enabled or cfg.keepa_click_price_rule == "on") and home:
        html = html.replace("</style>", price_labels.STYLE + "</style>", 1)
    if cfg.non_amazon_freshness_enabled and home:
        from ..non_amazon_freshness import STYLE as AGED_STYLE
        html = html.replace("</style>", AGED_STYLE + "</style>", 1)
    if home:
        if cfg.click_price_home_fill and cfg.keepa_click_price_rule == "on":
            # Operator 2026-10-17 ("it just stops"): with fewer than 120 Add-to-Cart-confirmed deals the
            # homepage tabs showed only those (~28); confirmed deals still lead, the rest follow.
            html = replace_slot(html,
                "    return known.length >= 120 ? known.concat(rows.filter(function (d) { return d.click_price_unknown; })) : known;",
                "    return known.concat(rows.filter(function (d) { return d.click_price_unknown; }));",
                template_name="index.html", slot_name="click-price-home-fill")
        if cfg.trending_auto_cards is not None:
            # Operator 2026-10-17: Trending keeps loading as visitors scroll; missing = 300 (old bytes).
            html = replace_slot(html, "  var AUTO_CARDS = 300;", f"  var AUTO_CARDS = {cfg.trending_auto_cards};",
                                template_name="index.html", slot_name="trending-auto-cards")
        if cfg.home_link_resets:
            for selector in ('class="tklabel"', 'id="drop" class="drop"'):
                html = html.replace(selector, selector + ' style="cursor:pointer" title="Back to all deals"', 1)
        html = _SEARCH_AMAZON_BLOCK.sub(
            lambda m: m[1] if cfg.search_amazon and cfg.affiliate_amazon_tag else "", html)
        html = html.replace("{{SEARCH_AMAZON_TAG}}", json.dumps(cfg.affiliate_amazon_tag).replace("<", "\\u003c"))
        html = search_results.template(html, cfg)
        html = shipping_note.home(html, cfg)
        html = search_boost.template(html, cfg)
        html = search_circle.template(html, cfg)
        html = search_analyze.template(html, cfg)
        html = search_more.template(html, cfg)
        html = search_box_glow.template(html, cfg)
        html = card_tidy.template(html, cfg)
        html = analyze_top_sellers.template(html, cfg)
        html = page_polish.home(html, cfg)
        html = header_polish.template(html, cfg)
        html = phone_tagline_count.template(html, cfg)
        html = timer_pill_ring.template(html, cfg)
        html = phone_timer_raise.template(html, cfg)
        html = timer_center.template(html, cfg)
        from .. import appeal as title_appeal
        from . import open_box
        html = title_appeal.template(html, cfg.front_page.get("appeal") or {})
        html = drop_rotation.final_order(html, cfg)
        html = credits.home_template(html, cfg)
        html = open_box.template(html, cfg.open_box_scarcity, counts=cfg.open_box_counts)
        html = manifest_refresh.template(html, cfg)
        if cfg.offscreen_cards:
            html = html.replace("</style>", OFFSCREEN_CARDS_STYLE + "</style>", 1)
    return html


def site_template_version(price_presets: list[int] | None = None,
                          home: str | None = None, browse: _BrowseScript | None = None) -> str:
    """Short hash of the standard home template, preserving its version
    when only the optional support variant changes."""
    import hashlib
    html = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8") if home is None else home
    html = _SEARCH_AMAZON_BLOCK.sub("", html)
    from .. import click_price
    html = click_price.template(html, False)
    html = price_labels.template(html, False)
    html = checkout_prices.template(html, False)
    html = _PHOTOLESS_BLOCK.sub("", html)
    html = _USED_TAB_ONLY_BLOCK.sub("", html)
    html = _DEMOTE_WORDS_BLOCK.sub("", html)
    html = amazon_as_of.template(html, False)
    html = amazon_history.BLOCK.sub("", html)
    html = product_pages.template(html, False)
    html = _WATCH_BLOCK.sub("", html).replace("<!--saved--><!--/saved-->", "")
    # Adding the support switch leaves the standard page, including its
    # version stamp, byte-for-byte unchanged. Standard edits still invalidate it.
    html = re.sub(r"<!--support-->.*?<!--/support-->", "", html, flags=re.S)
    data = html.replace("<!--standard-->", "").replace("<!--/standard-->", "").encode()
    data += (TEMPLATES_DIR / "site-footer.html").read_bytes()
    data += (TEMPLATES_DIR / "site-footer.css").read_bytes()
    data += (TEMPLATES_DIR / "deal-actions.js").read_bytes()
    presets = AppConfig().seo_price_pages if price_presets is None else price_presets
    browse = _browse_script(presets) if browse is None else browse
    data += browse.without_saved.encode()
    return hashlib.sha256(data).hexdigest()[:12]


def keeps_source_tracker(url: str, cfg) -> bool:
    """True when ``url`` is a source's tracker that must stay exactly as posted.

    Repair off preserves the legacy routing. The operator clarified on
    2026-10-20 that the permission condition is credit by name on each deal.
    """
    if getattr(cfg, "affiliate_repair_competitor_trackers", False) or not url:
        return False
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return bool(host) and is_competitor_tracker_host(
        host, getattr(cfg, "affiliate_competitor_tracker_hosts", ()))


def rewrite_affiliate(url: str, amazon_tag: str = "", *, sovrn_key: str = "",
                      sovrn_enabled: bool = False, cuid: str = "",
                      credit_urls: list[str] | None = None,
                      competitor_tracker_hosts: list[str] | tuple[str, ...] = (),
                      ebay_campid: str = "") -> str:
    """Compatibility entry point for the sole pure affiliate rewriter."""
    hosts = frozenset(_deal_source_hosts()) if sovrn_enabled and re.fullmatch(r"[0-9a-f]{32}", sovrn_key) else frozenset()
    return link_facts.rewrite_affiliate(
        url, amazon_tag, sovrn_key=sovrn_key, sovrn_enabled=sovrn_enabled, cuid=cuid,
        credit_urls=credit_urls, competitor_tracker_hosts=competitor_tracker_hosts,
        ebay_campid=ebay_campid, source_hosts=hosts)


def link_mix(deals: list[dict[str, Any]], amazon_tag: str = "", *,
             competitor_tracker_hosts: list[str] | tuple[str, ...] = (),
             ebay_campid: str = "") -> dict[str, Any]:
    """Count public live Get deal destinations, without fetching or rewriting.

    Pass the configured Store ID to distinguish our tag from another source's.
    Locked stubs and ended deals have no live public Get deal link to count.
    """
    counts = dict.fromkeys(("amazon_tagged", "amazon_untagged", "amazon_short",
                            "source_post", "sovrn", "ebay_epn", "other_store", "none",
                            "earning", "not_earning"), 0)
    stores: Counter[str] = Counter()
    for deal in deals:
        if deal.get("locked") or not deal.get("alive", deal.get("status") == "alive"):
            continue
        bucket, host, earns = _link_destination(deal, amazon_tag, competitor_tracker_hosts,
                                                ebay_campid)
        if bucket == "other_store":
            stores[host] += 1
        counts[bucket] += 1
        counts["earning" if earns else "not_earning"] += 1
    return {**counts, "by_store": dict(sorted(stores.items(), key=lambda x: (-x[1], x[0]))[:10]),
            "amazon_untagged_flag": bool(amazon_tag and counts["amazon_untagged"])}


def _headline(deal: dict[str, Any], cfg=None) -> str:
    """One plain-English sentence or two per deal, for humans skimming the page."""
    item = deal.get("item") or "This item"
    store = deal.get("store") or "an online store"
    final = deal.get("final_price")
    reg = deal.get("regular_price")
    pct = deal.get("pct_at_checkout")
    flags = deal.get("flags") or []
    codes = deal.get("codes") or []

    if final is None:
        lead = f"{item} at {store}"
    elif final <= 0.01:
        lead = f"{item} is free (or a penny) at {store}"
    else:
        lead = f"{item} is ${final:,.2f} at {store}"
    if pct is not None:
        lead += f" — {display_pct(pct)}% off"
        if reg and amazon_reference.enabled(cfg):
            lead += (f" the List price ${reg:,.2f}" if deal.get("basis") == "amazon_list_price"
                     else f" the stated ${reg:,.2f}")
        elif reg:
            lead += f" the usual ${reg:,.2f}"
        elif deal.get("basis") == "list_price_claim":
            lead += " the claimed list price"
    parts = [lead + "."]
    if codes and not _needs_steps(deal):  # else the card's "how" box shows them
        parts.append(("Code " if len(codes) == 1 else "Codes ") + ", ".join(codes) + ".")
    if "range" in flags:
        parts.append("Price varies by option; the discount uses the upper price." if amazon_reference.enabled(cfg)
                     else "Price varies by option; the discount holds even at the highest price.")
    if "rebate_dependent" in flags:
        parts.append("Rebate is later; not deducted from this price.")
    if "unknown_shipping" in flags:
        parts.append("Shipping cost unclear — check at checkout.")
    tier = deal.get("verify_tier", "unverified")
    if tier == "corroborated":
        parts.append("Confirmed by multiple independent sources.")
    elif tier == "verified" and amazon_reference.enabled(cfg):
        parts.append("The price was verified.")
    elif tier == "history-checked":
        if not amazon_history.hidden(cfg, deal):
            parts.append("Well below the usual price in our own price history.")
    else:
        parts.append("Reported by one source — not yet confirmed.")
    expires = parse_iso(deal.get("expires_at") or "")
    if expires is not None:
        parts.append(f"Expires {expires.strftime('%b %d')}.")
    return " ".join(parts)


#: What a step condition asks of the shopper, when the post gave no steps.
_CONDITION_HOW = {
    "subscribe_save": "Choose Subscribe & Save", "in_cart": "The price drops in the cart",
    "new_customer": "New customers only", "targeted": "Price may vary by account",
    "ymmv": "Price may vary by account", "multi_buy": "Multi-buy price",
    "in_store_only": "In store only", "clearance_aisle": "In-store clearance",
    "prime_only": "Prime membership required", "membership_only": "Membership required",
}


#: Support wins over open/paywall. Shared membership-only regions use
#: <!--standard--> so the two legacy variants need not duplicate them.
_VARIANT_RE = re.compile(r"<!--(paywall|open|support)-->(.*?)<!--/\1-->", re.S)
_STANDARD_RE = re.compile(r"<!--standard-->(.*?)<!--/standard-->", re.S)
SUPPORT_BODY = (
    "wowpriced is free for everyone. Checking prices around the clock costs us {cost_phrase} a "
    "month in price data and servers. If you find it useful, you can make an optional "
    "payment to support our work."
)
SUPPORT_NOTE = (
    "One-time payment, any amount, through Stripe. wowpriced is a for-profit business: "
    "support is not tax-deductible and buys nothing extra; every deal stays free."
)
#: With the monthly link too: the renewal terms sit next to the button.
SUPPORT_NOTE_MONTHLY = (
    "Once: any amount. Monthly: from $5 ($1 times the number you pick, 5 or more), renewing each month "
    "until you cancel (email hello@wowpriced.com anytime). Both through Stripe. "
    "wowpriced is a for-profit business: support is not tax-deductible and buys "
    "nothing extra; every deal stays free."
)
JOIN_FORM = (
    '<form class="joinform" data-clarity-mask="true"><label for="join-email">Email</label>'
    '<input id="join-email" name="email" type="email" data-clarity-mask="true" '
    'placeholder="Email" autocomplete="email" required><button type="submit" class="pwbtn btn" disabled>Continue</button>'
    '<p class="meta muted">One email with the best deals. Unsubscribe anytime.</p>'
    '<p data-join-message role="status" hidden></p></form>'
)


_SUPPORT_ASK_BLOCK = re.compile(r"^ *// support-ask\n(.*?)^ *// /support-ask\n", re.M | re.S)
_SUPPORT_DELAY_BLOCK = re.compile(r"^ *// support-delay\n(.*?)^ *// /support-delay\n", re.M | re.S)
_BRAND_PAGES_BLOCK = re.compile(r"^ *// brand-pages\n(.*?)^ *// /brand-pages\n", re.M | re.S)


def support_script(link: str = "", monthly: str = "", panel_delay_s: int = 0,
                   *, brand_meta: dict | None = None) -> str:
    """Omit inactive timing/ask code to preserve the historical page bytes."""
    script = (TEMPLATES_DIR / "support.js").read_text(encoding="utf-8")
    script = _BRAND_PAGES_BLOCK.sub(lambda m: m.group(1) if brand_meta is not None else "", script)
    if brand_meta is not None:
        script = script.replace("{{BRAND_META}}", json.dumps(brand_meta, separators=(",", ":")).replace("<", "\\u003c"))
    script = _SUPPORT_DELAY_BLOCK.sub(lambda m: m.group(1) if panel_delay_s else "", script)
    script = script.replace("{{SUPPORT_PANEL_DELAY_S}}", str(panel_delay_s)).replace(
        "{{SUPPORT_PANEL_WAIT}}", "panelWaiting || " if panel_delay_s else "")
    script = _SUPPORT_ASK_BLOCK.sub(lambda m: m.group(1) if link else "", script)
    if not link:
        return script
    ask = seo._fill((TEMPLATES_DIR / "support-ask.html").read_text(encoding="utf-8"),
                    SUPPORT_ASK_LINK=escape(link, quote=True),
                    SUPPORT_ASK_MONTHLY=", or from $5 a month" if monthly else "",
                    SUPPORT_ASK_MONTHLY_ACTION=(
                        f'<a data-support-monthly href="{escape(monthly, quote=True)}" '
                        'target="_blank" rel="noopener">Support monthly</a>' if monthly else ""),
                    SUPPORT_ASK_NOTE=escape(SUPPORT_NOTE_MONTHLY if monthly else SUPPORT_NOTE))
    return script.replace("{{SUPPORT_ASK_HTML}}", json.dumps(ask)).replace(
        "{{SUPPORT_ASK_STYLE}}", json.dumps((TEMPLATES_DIR / "support-ask.css").read_text(encoding="utf-8")))


def linked_disclosure(disclosure: str) -> str:
    """Link the existing label without lengthening the configured short copy."""
    link = '<a href="/disclosure.html">Disclosure</a>'
    if disclosure.startswith("Disclosure:"):
        return link + escape(disclosure[len("Disclosure"):])
    return escape(disclosure) + (" " if disclosure else "") + link


def site_footer(html: str, now: datetime, disclosure: str, support: bool,
                account: bool = False, *, amazon_as_of_enabled: bool = False,
                product_pages_enabled: bool = False, event_links: list | None = None,
                checker_enabled: bool = False) -> str:
    """One footer for root and nested pages, with the configured disclosure."""
    if "{{SITE_FOOTER}}" not in html:
        return html
    footer = (TEMPLATES_DIR / "site-footer.html").read_text(encoding="utf-8")
    if event_links:
        footer = footer.replace('</nav>', ' · ' + ' · '.join(
            f'<a href="{escape(path)}">{escape(name)}</a>' for path, name in event_links) + '\n  </nav>', 1)
    if product_pages_enabled:
        footer = footer.replace('<a href="/hacks.html">Deal hacks</a>',
                                '<a href="/hacks.html">Deal hacks</a> · <a href="/p/">Price history</a>', 1)
    if checker_enabled:
        footer = footer.replace('</nav>', ' · ' + checker_page.LINK + '\n  </nav>', 1)
    if amazon_as_of_enabled:
        footer = footer.replace("</span></p>", "</span> " + amazon_as_of.FOOTER + "</p>", 1)
    footer = seo._fill(footer, FOOTER_YEAR=str(now.year), FOOTER_DISCLOSURE=linked_disclosure(disclosure),
                       FOOTER_SIGNUP=(TEMPLATES_DIR / "footer-signup.html").read_text(encoding="utf-8") if support else "",
                       ACCOUNT_FOOTER=(
                           '<span id="footterms" hidden></span><p id="acctfoot" class="meta" hidden>Your account: '
                           '<button type="button" id="billing" class="linkbtn">Manage membership</button></p>'
                       ) if account else "")
    html = html.replace("{{SITE_FOOTER}}", footer)
    css = (TEMPLATES_DIR / "site-footer.css").read_text(encoding="utf-8")
    if support:
        css += (TEMPLATES_DIR / "footer-signup.css").read_text(encoding="utf-8")
    html = html.replace("</style>", css + "</style>", 1)
    if support and "window.wpSupport =" not in html:
        script = support_script()
        html = html.replace("</body>", "<script>" + script + "</script>\n</body>", 1)
    return html


def affiliate_programs(cfg: AppConfig) -> str:
    """Our active programs only; source affiliates keep their own credit."""
    programs = []
    if cfg.affiliate_amazon_tag:
        programs.append('<li><strong>Amazon Associates.</strong> As an Amazon Associate, '
                        'wowpriced earns from qualifying purchases.</li>')
    if cfg.affiliate_ebay_campaign_id:
        programs.append('<li><strong>eBay Partner Network.</strong> We may earn a commission '
                        'on qualifying purchases through our eBay links.</li>')
    if cfg.affiliate_sovrn_enabled:
        programs.append('<li><strong>Sovrn Commerce.</strong> We may earn a commission '
                        'on purchases through eligible retailer links.</li>')
    return '<ul>' + ''.join(programs) + '</ul>' if programs else (
        '<p>We currently have no active affiliate programs of our own. '
        'A deal source may still earn a commission through its links.</p>')


def copy_variant(html: str, open_access: bool, support: bool = False) -> str:
    """html with the wording for the current access mode only."""
    # Shared membership markup/scripts need not be duplicated in open/paywall.
    html = _STANDARD_RE.sub(lambda m: "" if support else m.group(1), html)
    keep = "support" if support else ("open" if open_access else "paywall")
    return _VARIANT_RE.sub(lambda m: m.group(2) if m.group(1) == keep else "", html)


def _how(deal: dict[str, Any]) -> list[str]:
    """All extracted instructions, without dropping conditions or truncating facts."""
    steps = []
    kinds = set()
    for raw in list(deal.get("stack_steps") or []) + list(deal.get("rebates") or []):
        raw = str(raw).strip()
        if raw:
            kind = parse_stack_step(raw)["kind"]
            kinds.add(kind)
            later = kind in {"rebate_abs", "rebate_pct", "deferred"}
            steps.append(("After purchase (not deducted at checkout): " if later else "") + raw)
    if deal.get("deferred_value"):
        kinds.add("deferred")
        steps.append("After purchase (not deducted at checkout): " + deal["deferred_value"].strip())
    steps += _requirements(deal)
    flags = set(deal.get("flags") or [])
    if "range" in flags:
        steps.append("Price varies by option; the displayed price is the verified upper bound.")
    if "per_unit" in flags:
        steps.append("Price is per unit; the checkout total depends on quantity.")
    if deal.get("quantity_or_unit"):
        steps.append("Quantity: " + deal["quantity_or_unit"])
    if "rebate_dependent" in flags and not kinds & {"rebate_abs", "rebate_pct"}:
        steps.append("Rebate is later; its terms are not stated here. Check the source post.")
    if "deferred_value" in flags and "deferred" not in kinds:
        steps.append("Later reward; its terms are not stated here. Check the source post.")
    return list(dict.fromkeys(steps))


def _requirements(deal: dict[str, Any]) -> list[str]:
    informational = {"used", "open_box", "refurbished", "new", "online_only", "online only",
                     "limited_qty", "pricing_error"}
    return list(dict.fromkeys(_CONDITION_HOW.get(c, str(c).replace("_", " "))
                              for c in deal.get("conditions") or [] if c not in informational))


def _checkout(deal: dict[str, Any], steps: bool) -> dict[str, Any]:
    """Copy requirements and label the savings result; never calculate a price."""
    flags = set(deal.get("flags") or [])
    summary = _requirements(deal)
    if "range" in flags:
        summary.append("Price varies by option")
    if "per_unit" in flags:
        summary.append("Price is per unit")
    if deal.get("codes"):
        summary.append("Promo code required")
    raw_steps = deal.get("stack_steps") or []
    if any(re.search(r"\bclip\b|\bcoupon\b(?!\s+code)", s, re.I) for s in raw_steps):
        summary.append("Coupon required")
    if any(re.search(r"subscribe|s&s", s, re.I) for s in raw_steps) and "subscribe_save" not in (deal.get("conditions") or []):
        summary.append("Choose Subscribe & Save")
    if any(re.search(r"\b(?:join|sign up|enroll)\b", s, re.I) for s in raw_steps):
        summary.append("Sign-up required")
    later = [parse_stack_step(s)["kind"] for s in raw_steps + (deal.get("rebates") or [])]
    if ("rebate_dependent" in flags or any(k.startswith("rebate_") for k in later)
            or any(parse_stack_step(s)["kind"] == "unknown" for s in deal.get("rebates") or [])):
        summary.append("Rebate after purchase")
    if deal.get("deferred_value") or "deferred_value" in flags or "deferred" in later:
        summary.append("Reward after purchase")
    shipping = str(deal.get("shipping_cost_or_threshold") or "").strip()
    known = known_shipping(deal)
    # Unconditional free shipping is information, not a requirement or a new card row.
    free_shipping = re.fullmatch(
        r"(?:free(?:\s+(?:standard\s+)?(?:shipping|delivery))?|"
        r"(?:shipping|delivery)(?:\s+is)?\s+free|\$?0(?:\.0+)?)[.!]?", shipping, re.I)
    if shipping and not free_shipping:
        summary.append("Shipping: " + shipping + (" (included)" if known else ""))
    elif "unknown_shipping" in flags:
        summary.append("Shipping not confirmed")
    if not summary and (steps or any(parse_stack_step(s)["kind"] == "unknown" for s in raw_steps)):
        summary.append("Checkout steps required")
    price = deal.get("final_price")
    now = (f"Pay now: ${price:,.2f}" + (" including stated shipping; before tax." if known
           else " before tax; shipping not confirmed.")) if price is not None else "Checkout amount not stated."
    if "per_unit" in flags:
        now = now.replace("Pay now:", "Price per unit:")
    elif "range" in flags:
        now = now.replace("Pay now:", "Price varies; up to")
    if deal.get("status") == "dead":
        now = now.replace("Pay now:", "Last checkout price:")
    # The card keeps its one box (steps, then copyable codes) and adds only
    # what neither the steps, the codes nor the condition chips already say:
    # money that comes later, a price that varies, or shipping not included.
    card = [s for s in dict.fromkeys(summary) if s in (
        "Price varies by option", "Price is per unit", "Rebate after purchase",
        "Reward after purchase", "Shipping not confirmed")
        or (s.startswith("Shipping: ") and not known)]
    # Never say a step twice in the one box (live card 2026-10-14: "Price is per
    # unit" above "Price is per unit; the checkout total depends on quantity.").
    said = _how(deal)
    card = [s for s in card if not any(step.startswith(s) for step in said)]
    return {"summary": " · ".join(dict.fromkeys(summary)), "card": " · ".join(card),
            "now": now, "shipping_known": known,
            "shipping": ("Shipping: " + shipping + ("." if not shipping.endswith(".") else ""))
                        if shipping else "Shipping not stated; check the store for the total."}


def checkout_metrics(deals: list[dict[str, Any]]) -> dict[str, Any]:
    """Current public live offers only; empty denominators are unavailable."""
    live = [d for d in deals if not d.get("locked") and d.get("status", "alive") == "alive"]
    required = [d for d in live if d["checkout"]["summary"]]
    covered = [d for d in required if not any("terms are not stated here" in s for s in d["how"])]
    known = sum(d["checkout"]["shipping_known"] for d in live)
    return {"known_shipping_share": known / len(live) if live else None,
            "requirement_coverage": len(covered) / len(required) if required else None,
            "public_live": len(live), "known_shipping": known,
            "with_requirements": len(required), "covered": len(covered)}


def _serialize_deal(deal: dict[str, Any], now: datetime, amazon_tag: str = "",
                    overrides: dict[str, str] | None = None, *,
                    sovrn_key: str = "", sovrn_enabled: bool = False,
                    competitor_tracker_hosts: list[str] | tuple[str, ...] = (),
                    ebay_campid: str = "", cfg=None, links: LinkFacts | None = None) -> dict[str, Any]:
    """Project a deal dict to the public JSON shape (facts + link + credit)."""
    original = deal
    deal = amazon_reference.project(deal, cfg, now)
    first_seen = parse_iso(deal.get("first_seen_at") or "")
    age_hours: float | None = None
    if first_seen is not None:
        age_hours = round((now - first_seen).total_seconds() / 3600.0, 2)
    links = links if links is not None else link_facts.derive_link_facts(deal, link_policy(
        amazon_tag, overrides, sovrn_key=sovrn_key, sovrn_enabled=sovrn_enabled,
        competitor_tracker_hosts=competitor_tracker_hosts, ebay_campid=ebay_campid,
        repair_competitor_trackers=getattr(cfg, "affiliate_repair_competitor_trackers", False),
        exact_variant_links=getattr(cfg, "affiliate_exact_variant_links", False)))
    # Each projection owns its JSON credits; internal facts never share lists.
    credits = [{"name": name, "url": url} for name, url in links.credits]
    if amazon_reference.enabled(cfg) and not deal.get("regular_price_basis") \
            and deal.get("regular_price") is not None and credits and credits[0]["name"] == "Amazon price drop":
        source = next((c["name"] for c in reversed(credits) if c["name"] != "Amazon price drop"), "")
        if source:
            # Resolve the later partner's attribution before the history gate
            # can mistake the original discovery credit for a historical basis.
            deal = dict(deal, regular_price_basis=f"as stated by {source}")
    if amazon_history.hidden(cfg, deal) and re.search(
            r"\b(?:\d+.day|average|median|histor(?:y|ical))\b",
            seo._basis_label(dict(deal, credits=credits)), re.I):
        # A historical comparator also supplies the discount and headline.
        # Hide the entire comparison in public copies; keep stored facts intact.
        deal = dict(deal, regular_price=None, pct_at_checkout=None, abs_savings=None)
        deal.pop("regular_price_basis", None)
    status = deal.get("status", "alive")
    steps = links.needs_steps
    checkout = _checkout(deal, steps)
    image = deal.get("image_url") or ""
    if not source_image_allowed(image, deal.get("sightings") or [], cfg):
        image = ""
    public = {
        "dedupe_key": deal.get("dedupe_key", ""),
        # Opaque id (locked stubs carry only this: the dedupe key names the
        # product link or the item words, which a visitor must not see).
        "key": links.key,
        "store": deal.get("store", ""),
        "item": deal.get("item", ""),
        "brand": deal.get("brand", ""),
        "url": links.url,
        "earns": links.earns,
        "image_url": image,
        "final_price": deal.get("final_price"),
        "regular_price": deal.get("regular_price"),
        "pct_at_checkout": deal.get("pct_at_checkout"),
        "abs_savings": deal.get("abs_savings"),
        "flags": deal.get("flags") or [],
        "codes": deal.get("codes") or [],
        # The card shows how even when Get deal uses an earning store link.
        "needs_steps": steps,
        "how": _how(deal) if checkout["summary"] else [],
        "checkout": checkout,
        "conditions": deal.get("conditions") or [],
        "category": deal.get("category") or "other",
        "badge": "verified" if amazon_history.hidden(cfg, deal) and deal.get("verify_tier") == "history-checked"
                 else deal.get("verify_tier", "unverified"),
        "headline": _headline(deal, cfg),
        "status": status,
        "alive": status == "alive",
        "gone": status == "dead" and deal.get("death") in {"confirmed", "api_ended"},
        "died_at": deal.get("died_at", ""),
        "first_seen_at": deal.get("first_seen_at", ""),
        "age_hours": age_hours,
        "credits": credits,
    }
    if amazon_history.watches_hidden(cfg):
        public["amazon"] = is_amazon_deal(deal)
    # Legacy field name: true means "no saved-price watch" for this deal.
    if amazon_history.live_only(cfg, deal):
        public["amazon"] = True
    from ..store_prices import store_offer
    checked = price_confirmed_at(deal) if is_amazon_deal(deal) or store_offer(deal)[0] else None
    if checked is not None and checked <= now:
        public["checked_at"] = iso(checked)
        if getattr(cfg, "amazon_as_of_enabled", False) and is_amazon_deal(deal):
            public["amazon_as_of"] = amazon_as_of.time_text(checked)
    if deal.get("regular_price_basis"):
        public["regular_price_basis"] = deal["regular_price_basis"]
    if amazon_reference.enabled(cfg):
        if deal.get("price_drop"):
            public["price_drop"] = True
        if amazon_reference.uses_api_reference(original):
            # Tier membership only; the private percentage never leaves state.
            public["primary_offer"] = (original.get("pct_at_checkout") or 0) >= cfg.min_pct
    if deal.get("spotlight"):
        public["spotlight"] = deal["spotlight"]
    if deal.get("price_unconfirmed"):
        public["price_unconfirmed"] = True
    from .. import click_price
    if cfg and cfg.keepa_click_price_rule == "on" and click_price.status(deal, cfg, now) == "unknown":
        public["click_price_unknown"] = True
    if cfg and click_price.select_sizes(deal, cfg, now):
        public["select_sizes"] = True
    if deal.get("price_aged"):
        public["price_aged"] = deal["price_aged"]
    if cfg and cfg.front_page.get("demote_words") and is_niche(deal, cfg.front_page):
        public["niche"] = True
    label = checkout_prices.label(deal, cfg, now)
    if label:
        public["checkout_price"] = label
    from .. import appeal as title_appeal
    from . import open_box
    if title_appeal.is_allure(deal, getattr(cfg, "front_page", {}).get("appeal") or {}):
        public["allure"] = True
    if mix := front_mix.bucket(deal, getattr(cfg, "front_page", {})):
        public["mx"] = mix
    if getattr(cfg, "open_box_scarcity", False) and (stock := open_box.facts(deal, now)) is not None:
        public["open_box_scarcity"] = True
        if stock and getattr(cfg, "open_box_counts", False):
            public["open_box_stock"] = stock
    if cfg and drop_fresh.enabled(cfg):
        if is_clothing(deal, cfg.front_page.get("top_picks") or {}):
            public["cl"] = 1
        if checkout_prices.enabled(cfg) and checkout_prices.kind(deal):
            public["pick_checkout"] = True
        from ..appeal import matches
        excluded = (cfg.front_page.get("appeal", {}).get("want") or {}).get("exclude_words", [])
        if matches(deal.get("item"), excluded + cfg.search_boost.get("exclude_words", [])):
            public["fresh_blocked"] = True
    return public


def source_health(db, now: datetime, last_run: dict[str, Any] | None = None) -> dict[str, Any]:
    """Per-source health (state, last success, failures, trip time, last
    error) for the operator: errors are scrubbed of secrets again here."""
    sources = []
    for row in db.all_source_health():
        entry = dict(row)
        entry["last_error"] = redact_secrets(entry.get("last_error") or "")
        until = parse_iso(row.get("tripped_until") or "")
        entry["tripped"] = until is not None and now < until
        sources.append(entry)
    return {"generated_at": iso(now), "sources": sources, "last_run": last_run or {}}


def health_markdown(doc: dict[str, Any]) -> str:
    """The health table as GitHub-flavoured markdown (the run summary)."""
    rows = sorted(doc.get("sources") or [], key=lambda r: (not r.get("tripped"), r.get("source_name", "")))
    tripped = sum(1 for r in rows if r.get("tripped"))
    cell = lambda v: str(v if v not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")[:160]
    lines = [f"## Source health ({len(rows)} sources, {tripped} tripped)", "",
             "| Source | State | Last success | Items | Failures | Tripped until | Last error |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| " + " | ".join(cell(v) for v in (
            r.get("source_name"), "tripped" if r.get("tripped") else "ok",
            r.get("last_success_at"), r.get("items_last_run"), r.get("consecutive_failures"),
            r.get("tripped_until"), r.get("last_error"))) + " |")
    return "\n".join(lines) + "\n"


def render_site(db, cfg, now: datetime, *, out_dir: str | Path | None = None,
                deals: list[dict[str, Any]] | None = None,
                last_run: dict[str, Any] | None = None, ai_parser=None) -> dict[str, Any]:
    """Write deals.json and the HTML pages to out_dir; return the counts,
    the members' full payload and the operator's source health."""
    lap, timings = [time.perf_counter()], {}

    def timed(section):
        """The time since the last mark goes to section (the "render timings" log line)."""
        mark = time.perf_counter()
        timings[section] = timings.get(section, 0.0) + mark - lap[0]
        lap[0] = mark

    # Preflight the one required home slot before ranking, state writes or
    # touching the previous publish/IndexNow manifest. Reuse this exact load.
    home_template = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
    slot = dict(template_name="index.html", slot_name="BROWSE_SCRIPT")
    validate_slot(home_template, "{{BROWSE_SCRIPT}}", **slot)
    home_template = drop_rotation.template(home_template, cfg)
    support = bool(getattr(cfg, "support_enabled", False))
    open_access = is_open(cfg)
    saved_deals = support and bool(cfg.engagement.get("saved_deals", False))
    price_presets = sorted({n for n in cfg.seo_price_pages if type(n) is int and n > 0})
    browse = _browse_script(price_presets, saved_deals, cfg.home_link_resets)
    home_html = _page_template(home_template, cfg, home=True)
    # A moved slot must still belong to the selected home variant.
    active_home = copy_variant(home_html, open_access, support)
    active_home = re.sub(r"<!--saved-->(.*?)<!--/saved-->",
                         lambda m: m.group(1) if saved_deals else "", active_home, flags=re.S)
    active_home = re.sub(r"<!--engagement-->(.*?)<!--/engagement-->",
                         lambda m: m.group(1) if cfg.engagement else "", active_home, flags=re.S)
    validate_slot(active_home, "{{BROWSE_SCRIPT}}", **slot)
    home_html = replace_slot(home_html, "{{BROWSE_SCRIPT}}", browse.rendered, **slot)
    home_html = search_analyze.browse_template(home_html, cfg)
    home_html = drop_shuffle.template(home_html, cfg)
    home_html = same_product_spacing.template(home_html, cfg)
    home_html = drop_fresh.template(home_html, cfg)

    if deals is None:
        from dealradar import rank  # lazy: avoid hard import cycle at module load

        # The site shows qualifying deals that recently died too (marked dead);
        # alerts and the digest use rank.published_deals (alive only).
        spotlight_rows = db.all_deals(status="alive")
        deals = rank.site_deals(db, cfg, now, include_unconfirmed=True)
    else:
        spotlight_rows = deals
    from ..amazon_used import excluded as amazon_used_excluded
    deals = [d for d in deals if not amazon_used_excluded(d, cfg)]
    spotlight_rows = [d for d in spotlight_rows if not amazon_used_excluded(d, cfg)]
    # A price-only offer has one home: the separate spotlight payload. It
    # never enters the grid, teaser rotation, picks or SEO discount pages.
    deals = [d for d in deals if d.get("pct_at_checkout") is not None]

    out = Path(out_dir) if out_dir is not None else Path(cfg.site_dir)
    out.mkdir(parents=True, exist_ok=True)
    # A partial render must not leave the previous run's deployment manifest.
    indexnow.manifest_path(out).unlink(missing_ok=True)
    (out / manifest_refresh.NAME).unlink(missing_ok=True)

    amazon_tag = getattr(cfg, "affiliate_amazon_tag", "")
    overrides = getattr(cfg, "affiliate_link_overrides", {}) or {}
    link_options = dict(sovrn_key=cfg.affiliate_sovrn_key,
                        sovrn_enabled=cfg.affiliate_sovrn_enabled,
                        competitor_tracker_hosts=cfg.affiliate_competitor_tracker_hosts,
                        ebay_campid=cfg.affiliate_ebay_campaign_id, cfg=cfg)
    policy = link_policy(amazon_tag, overrides,
                         **{k: v for k, v in link_options.items() if k != "cfg"},
                         repair_competitor_trackers=cfg.affiliate_repair_competitor_trackers,
                         exact_variant_links=cfg.affiliate_exact_variant_links)
    link_cache: dict[tuple, LinkFacts] = {}

    def facts_for(deal, *, required=False):
        if not cfg.reuse_link_facts_enabled and not required:
            return None
        # Labels may copy a row. A complete routing key lets those copies
        # reuse facts, while a changed URL/credit/condition derives again.
        key = link_facts.routing_key(deal)
        if key not in link_cache:
            link_cache[key] = link_facts.derive_link_facts(deal, policy)
        return link_cache[key]

    candidate_scores = [(d, value) for d in deals
                        if (value := pick_score(d, cfg, now, require_confirmation=False, links=facts_for(d))) is not None]
    candidate_scores.sort(key=lambda pair: pair[1], reverse=True)
    db.meta_set(PICKS_META, json.dumps([d["dedupe_key"] for d, _ in
                                      candidate_scores[:cfg.keepa_recheck_top_n]]))
    # Keep hidden candidates above so rechecks can bring them back by themselves.
    before_labels = deals
    from ..non_amazon_freshness import hidden, promotion_excluded, label, STYLE as AGED_STYLE
    deals = [dict(d, price_aged=label(d, now)) if d.get("status") != "dead" and promotion_excluded(d, cfg, now) else d
             for d in deals if d.get("status") == "dead" or not hidden(d, cfg, now)]
    deals.sort(key=lambda d: bool(d.get("price_aged")))
    labeled = [dict(d, price_unconfirmed=True) for d in deals if price_labels.qualifies(d, cfg, now)]
    deals = [d for d in deals if d.get("status") == "dead" or not price_unconfirmed(d, cfg, now)] + labeled
    visible = [d for d in deals if not photoless_expired(d, cfg, now)]
    unpublished_photoless = len(deals) - len(visible)
    hidden_links = {}
    if cfg.hide_deal_site_links is True:
        hosts = tuple(_deal_source_hosts()) + tuple(cfg.affiliate_competitor_tracker_hosts)
        report_keys = {d["dedupe_key"] for d in visible + daily_spotlight(spotlight_rows, cfg, now)}

        def store_destination(deal):
            if deal.get("status", "alive") != "alive":
                return True
            links = facts_for(deal, required=True)
            credit_urls = {s.get("source_url") for s in deal.get("sightings") or []}
            credit_urls.update(url for _, url in links.credits)
            try:
                host = (urlsplit(links.url).hostname or "").lower()
            except ValueError:
                host = ""
            own_store = any(host == h or host.endswith("." + h) for h in link_facts.RETAILER_SOURCE_HOSTS)
            if (links.url and links.url in credit_urls and not own_store) or (
                    host and is_competitor_tracker_host(host, hosts)):
                if deal["dedupe_key"] in report_keys:
                    hidden_links[deal["dedupe_key"]] = links
                return False
            return True

        visible = [d for d in visible if store_destination(d)]
        spotlight_rows = [d for d in spotlight_rows if store_destination(d)]
    hidden_by_credit = dict(Counter(name for links in hidden_links.values()
                                    for name, _ in links.credits).most_common(10))
    if cfg.hide_deal_site_links is True:
        logger.info("hidden_deal_site_links: %d; by credit: %s", len(hidden_links), hidden_by_credit)
    deals = visible
    timed("rank")
    serialized = [_serialize_deal(d, now, amazon_tag, overrides, links=facts_for(d), **link_options) for d in deals
                  if d.get("status") != "dead" or d.get("death") in {"confirmed", "api_ended"}]
    earns = {d["dedupe_key"]: d["earns"] for d in serialized}
    scores = {d["dedupe_key"]: pick_score(dict(d, earns=earns[d["dedupe_key"]]), cfg, now,
                                       require_confirmation=False, links=facts_for(d))
              for d in deals if d["dedupe_key"] in earns}
    originals = {d["dedupe_key"]: d for d in deals}
    # Public ps never carries the private popularity boost (scored without it,
    # not divided out: float rounding would still leak it).
    public_scores = {d["dedupe_key"]: pick_score(dict(d, earns=earns[d["dedupe_key"]]), cfg, now,
                                                 require_confirmation=False, links=facts_for(d),
                                                 popularity=False)
                     for d in deals if d["dedupe_key"] in earns} \
        if (cfg.front_page.get("popular_affordable") or {}).get("enabled", False) else scores
    # Preserve the existing rounded ordering/ties without exporting magnitudes
    # that can reconstruct a withheld historical comparison.
    ordinal_scores = {value: n for n, value in enumerate(sorted({round(v, 1)
        for v in public_scores.values() if v is not None}), 1)} if amazon_reference.enabled(cfg) else None
    for d in serialized:
        value = public_scores[d["dedupe_key"]]
        if value is not None:
            d["ps"] = ordinal_scores[round(value, 1)] if ordinal_scores is not None else round(value, 1)
    # The public file is a teaser when the paywall is on: the top deals in
    # full, the rest as locked stubs (docs/paywall.md). The full payload is
    # returned for the member upload and never written to the site.
    # Each drop unlocks different deals: the keys the last drops showed are
    # kept in meta and go to the back of the line.
    try:
        recent = [str(k) for k in json.loads(db.meta_get(_TEASER_META) or "[]")]
    except (TypeError, ValueError):
        recent = []
    # Keep the original rotation and scheduler wall unchanged. Labeled offers
    # are appended only for display; they cannot displace confirmed free picks.
    teaser, locked_count = split_teaser([d for d in serialized if not d.get("price_unconfirmed")], cfg, recent)
    if is_open(cfg):
        teaser += [d for d in serialized if d.get("price_unconfirmed")]
    else:
        from .paywall import _stub
        extras = [d for d in serialized if d.get("price_unconfirmed")]
        teaser += [dict(_stub(d), price_unconfirmed=True,
                        **({"click_price_unknown": True} if d.get("click_price_unknown") else {})) for d in extras]
        locked_count += len(extras)
    unlocked = {d["dedupe_key"] for d in teaser if not d.get("locked")}
    spotlight = [_serialize_deal(d, now, amazon_tag, overrides, links=facts_for(d), **link_options)
                 for d in daily_spotlight([d for d in spotlight_rows
                     if d.get("pct_at_checkout") is None or d["dedupe_key"] in unlocked], cfg, now)]
    spotlight_only = [d for d in spotlight if d["dedupe_key"] not in unlocked]
    timed("serialize")
    candidates = [(d, scores[d["dedupe_key"]]) for d in teaser
                  if not d.get("locked") and scores[d["dedupe_key"]] is not None]
    confirmed_candidates = [(d, value) for d, value in candidates
                            if not is_amazon_deal(originals[d["dedupe_key"]])
                            or (checkout_prices.enabled(cfg) and checkout_prices.kind(originals[d["dedupe_key"]]))
                            or price_confirmation_recent(originals[d["dedupe_key"]], now,
                                                         cfg.keepa_pick_max_unconfirmed_h)]
    from .. import click_price
    confirmed_candidates = [(d, value) for d, value in confirmed_candidates
                            if not click_price.excluded(originals[d["dedupe_key"]], cfg, now)]
    checkout_summary = checkout_prices.summary()
    # Rank original facts: public rows omit sightings and raw checkout steps.
    public_candidates = {d["dedupe_key"]: d for d, _ in confirmed_candidates}
    original_candidates = [(originals[d["dedupe_key"]], value) for d, value in confirmed_candidates]
    pick_candidates = original_candidates
    if drop_rotation.active(cfg):
        spotlight_keys = {d["dedupe_key"] for d in spotlight}
        pick_candidates = [(d, value) for d, value in original_candidates if d["dedupe_key"] not in spotlight_keys]
    if cfg.appeal_score.get("enabled", False):
        from .. import appeal_score
        from ..appeal import blocked
        pick_candidates = [(d, value) for d, value in pick_candidates
                           if not blocked(d, cfg.front_page.get("appeal") or {})]
        # The model sees exactly the published prices, never hidden history,
        # critic opinions, evidence text, popularity or internal numeric scores.
        appeal_candidates = [(dict(public_candidates[d["dedupe_key"]]), value)
                             for d, value in pick_candidates]
        ratings = appeal_score.ratings(db, cfg, now, appeal_candidates, parser=ai_parser,
                                      run_key=(last_run or {}).get("run_key", ""))
        rated = [dict(d, _appeal_score=ratings[d["dedupe_key"]]) if d["dedupe_key"] in ratings else d
                 for d, _ in pick_candidates]
        pick_candidates = [(d, pick_score(dict(d, earns=earns[d["dedupe_key"]]), cfg, now,
                                         require_confirmation=False, links=facts_for(d))) for d in rated]
    boosted = set()
    original_picks = top_picks(pick_candidates, drop_fresh.pick_options(cfg), now,
                               summary=checkout_summary, cfg=cfg, boosted=boosted)
    original_picks = drop_rotation.prepare_picks(original_picks, cfg, boosted)
    best_extra = front_mix.extras(pick_candidates, {d["dedupe_key"] for d in original_picks}, cfg, now)
    picked = [public_candidates[d["dedupe_key"]] for d in original_picks]
    for n, d in enumerate(picked, 1):
        d["pick"] = n
        if cfg.front_page["top_picks"].get("max_clothing_per_window", 0) > 0 \
                and is_clothing(originals[d["dedupe_key"]], cfg.front_page["top_picks"]):
            d["cl"] = 1
    from .. import appeal as title_appeal
    want_on = (cfg.front_page.get("appeal", {}).get("want") or {}).get("enabled")
    lift_summary = title_appeal.summary(
        [originals[d["dedupe_key"]] for d in seo.home_order(teaser, cfg.front_page) if d.get("pick")], cfg, now) \
        if title_appeal.active(cfg.front_page.get("appeal") or {}) else None
    popular_summary = None
    popular_options = cfg.front_page.get("popular_affordable") or {}
    if popular_options.get("shadow") or popular_options.get("enabled") \
            or (cfg.front_page.get("everyday_price") or {}).get("enabled"):
        try:
            popular_summary = popular_affordable.compare(original_candidates, cfg, now)
            if lift_summary is not None:
                popular_summary["want_season"] = lift_summary
            popular_affordable.record(db, now, (last_run or {}).get("run_key"), popular_summary)
        except Exception as e:
            logger.warning("%s", type(e).__name__)
            popular_summary = None
    if drop_rotation.active(cfg) and checkout_prices.enabled(cfg):
        for d in picked:
            if checkout_prices.kind(originals[d["dedupe_key"]]):
                d["pick_checkout"] = True
    if checkout_prices.enabled(cfg):
        for d in teaser + spotlight_only:
            if d.get("locked") or d.get("status") != "alive":
                continue
            original = originals.get(d["dedupe_key"], d)
            checkout_summary["labelled"] += bool(d.get("checkout_price"))
            if is_amazon_deal(original) and checkout_prices.kind(original):
                checkout_summary["unlabelled_no_reading"] += checkout_prices.reading(original, cfg, now) is None
    wall = [d for d in teaser if not d.get("price_unconfirmed")]
    wall = (seo.home_order(wall, cfg.front_page) if want_on else
            seo.place_gone(pick_order(wall, cfg.front_page)))[:cfg.keepa_recheck_wall_n]
    db.meta_set(WALL_META, json.dumps([d["dedupe_key"] for d in wall
                                     if not d.get("locked") and d.get("status") == "alive"]))
    if cfg.keepa_click_price_rule != "off":
        db.meta_set(click_price.PICKS_META, json.dumps([d["dedupe_key"] for d in picked]))
        static_wall = pick_order(teaser, cfg.front_page)[:click_price.WALL_CHECKS]
        # topOrder in the browser puts non-picks in arrival order. The static
        # score order is a separate scope: both surfaces need premium reads.
        click_wall = [d for d in teaser if not (cfg.front_page.get("used_tab_only") and seo.in_group(d, "USED"))]
        click_wall.sort(key=lambda d: (bool(d.get("locked")), d.get("pick", float("inf")),
            -(at.timestamp() if (at := parse_iso(d.get("first_seen_at") or "")) else 0)))
        if cfg.front_page.get("niche_last"):
            click_wall.sort(key=lambda d: is_niche(d, cfg.front_page))
        if cfg.photoless_last:
            click_wall.sort(key=lambda d: (bool(d.get("locked")), not bool(d.get("image_url"))))
        click_wall.sort(key=lambda d: bool(d.get("price_unconfirmed") or d.get("price_aged")))
        click_wall = click_price.home_rows(seo.place_gone(click_price.home_rows(click_wall)))
        # Browser jitter/new-arrival state breaks timestamp ties differently
        # on each drop. Any tied non-pick can cross the 300-card boundary.
        boundary = click_wall[click_price.WALL_CHECKS - 1] if len(click_wall) >= click_price.WALL_CHECKS else None
        arrival_ties = [d for d in click_wall[click_price.WALL_CHECKS:] if boundary
            and d.get("pick") is None and boundary.get("pick") is None
            and parse_iso(d.get("first_seen_at") or "") == parse_iso(boundary.get("first_seen_at") or "")]
        click_wall = click_wall[:click_price.WALL_CHECKS]
        db.meta_set(click_price.WALL_META, json.dumps([d["dedupe_key"] for d in click_wall
            if not d.get("locked") and d.get("status") == "alive"]))
        # Retain candidates as well as current features so an unknown price
        # can earn its way back after the next qualified reading.
        check_rows = [d for d, _ in candidate_scores[:cfg.keepa_recheck_top_n]]
        check_rows += spotlight + static_wall + click_wall + arrival_ties
        db.meta_set(click_price.VISIBLE_META, json.dumps(list(dict.fromkeys(
            d["dedupe_key"] for d in check_rows if not d.get("locked") and d.get("status") == "alive"))))
    # Select once, before JSON is written: remembered URLs only for pages
    # this publish emits. Free rows share their objects with member rows;
    # locked stubs and member-only deals never gain a public path.
    timed("picks")
    deal_pages = seo._deal_pages(db, cfg, now, out, teaser, deals)
    paths = deal_pages[0]
    product_public = [d for d in teaser + spotlight + deal_pages[1] if not d.get("locked")]
    products, product_paths, product_summary = product_pages.prepare(
        db, cfg, now, product_public, spotlight_rows + deals)
    try:
        take_summary = ai_take.prepare(db, cfg, now, products, parser=ai_parser,
                                      run_key=(last_run or {}).get("run_key", ""))
    except Exception as exc:
        logger.warning("AI takes unavailable (%s)", type(exc).__name__)
        for page in products.values():
            page.pop("ai_take", None)
        take_summary = dict(eligible=0, requested=0, verified=0,
                            rejected={"unavailable": 1}, cached=0, usd=0.0)
    product_public_keys = {d["key"] for d in product_public}
    for d in serialized + spotlight:
        path = paths.get(d["key"][:8])
        if path:
            d["deal_path"] = path
        if d["dedupe_key"] in product_paths and d["key"] in product_public_keys:
            d["product_path"] = product_paths[d["dedupe_key"]]
    for d in deal_pages[1]:
        if d["dedupe_key"] in product_paths:
            d["product_path"] = product_paths[d["dedupe_key"]]
    timed("deal_pages")
    link = support_link(getattr(cfg, "support_payment_link", ""))
    monthly = support_link(getattr(cfg, "support_monthly_link", "")) if link else ""
    if not open_access:  # open: the rotation pauses, to resume when it closes
        db.meta_set(_TEASER_META, json.dumps(teaser_memory(
            [d for d in teaser if not d.get("price_unconfirmed")], recent, cfg)))
    # The mode changes the published wording: a flip reloads open tabs.
    site_version = site_template_version(price_presets, home_template, browse) + ("-open" if open_access else "")
    if support:
        home = home_template
        home = _SEARCH_AMAZON_BLOCK.sub("", home)
        home = click_price.template(home, False)
        home = price_labels.template(home, False)
        home = checkout_prices.template(home, False)
        home = _PHOTOLESS_BLOCK.sub("", home)
        home = _USED_TAB_ONLY_BLOCK.sub("", home)
        home = _DEMOTE_WORDS_BLOCK.sub("", home)
        home = amazon_as_of.template(home, False)
        home = amazon_history.BLOCK.sub("", home)
        home = product_pages.template(home, False)
        # Optional watch markup must not change the disabled page's version.
        home = _WATCH_BLOCK.sub("", home).replace("<!--saved--><!--/saved-->", "")
        data = (home.encode()
                + support_script(link, monthly, cfg.support_panel_delay_s).encode()
                + (TEMPLATES_DIR / "join-card.html").read_bytes()
                + (TEMPLATES_DIR / "join-card.css").read_bytes()
                + (TEMPLATES_DIR / "footer-signup.html").read_bytes()
                + (TEMPLATES_DIR / "footer-signup.css").read_bytes()
                + (TEMPLATES_DIR / "site-footer.html").read_bytes()
                + (TEMPLATES_DIR / "site-footer.css").read_bytes()
                + (TEMPLATES_DIR / "deal-actions.js").read_bytes()
                + str(cfg.join_prompt).encode() + link.encode() + monthly.encode() + browse.marked.encode())
        if cfg.support_cost_phrase != "hundreds of dollars":
            data += b"\0support.cost_phrase=" + cfg.support_cost_phrase.encode()
        site_version = hashlib.sha256(data).hexdigest()[:12] + "-support"
    engagement = {name: bool(cfg.engagement.get(name, False)) for name in (
        "new_since_visit", "weekly_finds", "visit_streak", "surprise_me", "hot_meter")}
    if "saved_deals" in cfg.engagement:
        engagement["saved_deals"] = saved_deals
    saved_limit = max(1, min(200, cfg.engagement_saved_deals_limit))
    if cfg.engagement:
        engagement_script = (TEMPLATES_DIR / "engagement.js").read_text(encoding="utf-8")
        engagement_script = amazon_history.watches_template(engagement_script, cfg)
        engagement_script = amazon_reference.template(engagement_script, cfg)
        engagement_script = price_labels.template(engagement_script, cfg.unconfirmed_label_enabled
            or cfg.non_amazon_freshness_enabled or cfg.keepa_click_price_rule == "on")
        engagement_script = re.sub(r"/\* saved-deals \*/(.*?)/\* /saved-deals \*/",
                                   lambda m: m.group(1) if saved_deals else "",
                                   engagement_script, flags=re.S)
        site_version = hashlib.sha256(
            site_version.encode() + engagement_script.encode()
            + str(saved_deals).encode() + str(saved_limit).encode()
            + ("".join(_WATCH_BLOCK.findall(home_template)).encode()
               if saved_deals else b"")
        ).hexdigest()[:12]
    if traffic.enabled(cfg):
        site_version = traffic.site_version(site_version, cfg)
    site_version = profiles.site_version(site_version, cfg)
    if cfg.unconfirmed_label_enabled or cfg.non_amazon_freshness_enabled or cfg.keepa_click_price_rule == "on":
        site_version = hashlib.sha256((site_version + price_labels.STYLE).encode()
            + "".join(price_labels.BLOCK.findall(home_template)).encode()).hexdigest()[:12]
    if cfg.keepa_click_price_rule == "on":
        site_version = hashlib.sha256(site_version.encode()
            + "".join(click_price.BLOCK.findall(home_template)).encode()).hexdigest()[:12]
    if cfg.non_amazon_freshness_enabled:
        site_version = hashlib.sha256((site_version + AGED_STYLE).encode()).hexdigest()[:12]
    if checkout_prices.enabled(cfg):
        site_version = hashlib.sha256(site_version.encode()
            + "".join(checkout_prices.BLOCK.findall(home_template)).encode()).hexdigest()[:12]
    if cfg.photoless_last:
        site_version = hashlib.sha256(site_version.encode()
            + "".join(_PHOTOLESS_BLOCK.findall(home_template)).encode()).hexdigest()[:12]
    if cfg.amazon_as_of_enabled:
        site_version = hashlib.sha256(site_version.encode()
            + "".join(amazon_as_of.BLOCK.findall(home_template)).encode()
            + (amazon_as_of.FOOTER + amazon_as_of.DISCLAIMER + amazon_as_of.STYLE).encode()).hexdigest()[:12]
    if cfg.seo_product_pages_enabled:
        site_version = hashlib.sha256((site_version + product_pages.LINK_STYLE).encode()
            + "".join(product_pages.BLOCK.findall(home_template)).encode()).hexdigest()[:12]
    if cfg.checker["enabled"]:
        site_version = hashlib.sha256((site_version + "deal-check-page").encode()).hexdigest()[:12]
    if cfg.compare["publish"] == "on":
        site_version = hashlib.sha256((site_version + "compare-prices").encode()).hexdigest()[:12]
    if cfg.amazon_history == "hide":
        site_version = hashlib.sha256((site_version + "amazon-history-hide").encode()).hexdigest()[:12]
    elif amazon_history.watches_hidden(cfg):
        site_version = hashlib.sha256((site_version + "amazon-watches-off").encode()).hexdigest()[:12]
    if amazon_reference.enabled(cfg):
        site_version = hashlib.sha256(site_version.encode() + Path(amazon_reference.__file__).read_bytes()).hexdigest()[:12]
    if "live_only" in cfg.store_policy.values():
        site_version = hashlib.sha256((site_version + json.dumps(
            cfg.store_policy, sort_keys=True)).encode()).hexdigest()[:12]
    if cfg.seo_event_pages_enabled:
        site_version = hashlib.sha256((site_version + "event-pages" + json.dumps(
            cfg.seo_event_pages_events, sort_keys=True)).encode()).hexdigest()[:12]
    if inventory.enabled(cfg):
        site_version = inventory.site_version(site_version, cfg)
    if cfg.pwa_enabled or cfg.pwa_android["enabled"]:
        site_version = pwa.site_version(site_version, cfg)
    from .. import clicks
    if clicks.enabled(cfg) and clicks.endpoint(cfg):
        site_version = hashlib.sha256((site_version + clicks.endpoint(cfg)).encode()
            + (TEMPLATES_DIR / "click-signal.js").read_bytes()).hexdigest()[:12]
    site_version = manifest_refresh.site_version(site_version, cfg)
    if cfg.offscreen_cards:
        site_version = hashlib.sha256((site_version + OFFSCREEN_CARDS_STYLE).encode()).hexdigest()[:12]
    if cfg.dark_polish:
        site_version = hashlib.sha256((site_version + dark.CSS).encode()).hexdigest()[:12]
    if cfg.search_amazon and amazon_tag:
        site_version = hashlib.sha256(site_version.encode() + amazon_tag.encode()
            + "".join(_SEARCH_AMAZON_BLOCK.findall(home_template)).encode()).hexdigest()[:12]
    if cfg.static_photos:
        site_version = hashlib.sha256((site_version + "static-photos" + seo.STATIC_PHOTO_STYLE).encode()).hexdigest()[:12]
    if cfg.front_page.get("used_tab_only"):
        site_version = hashlib.sha256(site_version.encode()
            + "".join(_USED_TAB_ONLY_BLOCK.findall(home_template)).encode()).hexdigest()[:12]
    if cfg.front_page.get("demote_words"):
        site_version = hashlib.sha256(site_version.encode()
            + json.dumps(cfg.front_page["demote_words"]).encode()
            + "".join(_DEMOTE_WORDS_BLOCK.findall(home_template)).encode()).hexdigest()[:12]
    site_version = search_results.site_version(site_version, cfg)
    site_version = search_boost.site_version(site_version, cfg)
    site_version = search_circle.site_version(site_version, cfg)
    site_version = search_analyze.site_version(site_version, cfg)
    site_version = credits.site_version(site_version, cfg)
    site_version = search_more.site_version(site_version, cfg)
    site_version = drop_shuffle.site_version(site_version, cfg)
    site_version = search_analyze_lazy.site_version(site_version, cfg)
    site_version = search_box_glow.site_version(site_version, cfg)
    site_version = card_tidy.site_version(site_version, cfg)
    site_version = page_polish.site_version(site_version, cfg)
    site_version = same_product_spacing.site_version(site_version, cfg)
    site_version = drop_fresh.site_version(site_version, cfg)
    site_version = keep_first_cards.site_version(site_version, cfg)
    site_version = fresh_more_plain.site_version(site_version, cfg)
    site_version = header_polish.site_version(site_version, cfg)
    site_version = all_discounts.site_version(site_version, cfg)
    site_version = discounts_from_link.site_version(site_version, cfg)
    site_version = search_polish_1019.site_version(site_version, cfg)
    site_version = search_buttons_1019.site_version(site_version, cfg)
    site_version = search_discounts_button.site_version(site_version, cfg)
    site_version = card_click_1020.site_version(site_version, cfg)
    site_version = timer_pill_ring.site_version(site_version, cfg)
    site_version = timer_center.site_version(site_version, cfg)
    site_version = analyze_top_sellers.site_version(site_version, cfg)
    site_version = comments.site_version(site_version, cfg)
    if getattr(cfg, "tool_pages_hero", False) is True:
        site_version = hashlib.sha256((site_version + "tool-pages-hero").encode()).hexdigest()[:12]
    site_version = tool_pages_1020.site_version(site_version, cfg)
    site_version = buttons_to_tools.site_version(site_version, cfg)
    site_version = amazon_price_cap.site_version(site_version, cfg)
    site_version = tools_new_tab.site_version(site_version, cfg)
    site_version = tool_buttons_outside.site_version(site_version, cfg)
    site_version = search_focus_ring.site_version(site_version, cfg)
    site_version = hide_timer_ticker.site_version(site_version, cfg)
    site_version = sticky_buy_bar.site_version(site_version, cfg)
    site_version = front_mix.site_version(site_version, cfg)
    site_version = soft_buttons.site_version(site_version, cfg)
    site_version = tools_merge.site_version(site_version, cfg)
    site_version = shipping_note.site_version(site_version, cfg)
    if cfg.brand_pages:
        site_version = hashlib.sha256((site_version + "brand-pages").encode()).hexdigest()[:12]
    if cfg.open_box_scarcity or (cfg.front_page.get("appeal", {}).get("want") or {}).get("enabled"):
        version_home = home_html if saved_deals else _WATCH_BLOCK.sub("", home_html).replace("<!--saved--><!--/saved-->", "")
        site_version = hashlib.sha256((site_version + version_home).encode()).hexdigest()[:12]
    meta = {
        "site_version": site_version,
        "generated_at": iso(now),
        # The moment this file was built (now is the run's start; a long
        # run publishes minutes later and the page's countdown anchors here).
        "published_at": iso(util_now()),
        "last_run": last_run or {},
        # The page's drop clock step (config.yaml publish.drop_minutes): the
        # real gap between publishes of either lane, so the countdown is not
        # shorter than the cron that feeds it.
        "cadence_minutes": int(getattr(cfg, "drop_minutes", 15)),
        "drop_clock": validated_drop_clock(cfg.drop_clock, cfg.drop_minutes),
        # With a Store ID, the disclosure says links may earn wowpriced a
        # commission and carries Amazon's required sentence.
        "disclosure": (getattr(cfg, "affiliate_disclosure", "") if amazon_tag else "")
                      or cfg.ftc_disclosure,
        "count": len(serialized) + len(spotlight_only),
        # Membership links (empty until the operator pastes them in
        # config.yaml); the page hides the Subscribe button when empty.
        "subscribe_url": getattr(cfg, "subscribe_url", "") or "",
        "portal_url": getattr(cfg, "portal_url", "") or "",
        # Daily email list (the day's top 10): the subscribe Worker's URL (the forms
        # POST to it); empty until the Worker is deployed and pasted in.
        "newsletter_url": (getattr(cfg, "newsletter_worker_url", "") or "")
        if getattr(cfg, "newsletter_enabled", True) else "",
        "floors": {
            "min_pct": cfg.min_pct,
            "min_abs_savings": cfg.min_abs_savings,
            "site_show_from_pct": min(
                cfg.min_pct,
                getattr(cfg, "site_show_from_pct", cfg.min_pct)),
        },
        # Paywall: the page draws the locked cards and the auth modal
        # from these; api_url empty = no paywall UI at all.
        "api_url": getattr(cfg, "paywall_api_url", "") or "",
        "google_client_id": getattr(cfg, "paywall_google_client_id", "") or "",
        "teaser_count": int(getattr(cfg, "paywall_teaser_count", 8)),
        "locked_count": locked_count,
        # Every deal in full for everyone: the page's sign-up copy drops
        # "unlock" (nothing is locked).
        "open_access": open_access,
        # Omit zero so recorded pre-delay payloads keep their exact bytes.
        "support": {"enabled": support, "link": link, "monthly_link": monthly,
                    **({"panel_delay_s": cfg.support_panel_delay_s} if support and cfg.support_panel_delay_s else {})},
        "join": {"prompt": support and cfg.join_prompt,
                 "prompt_delay_s": max(15, cfg.join_prompt_delay_s), "apple": cfg.join_apple},
        "engagement": engagement,
        **({"saved_deals_limit": saved_limit} if saved_deals else {}),
        # Ranking boosts are applied in Python; omit their knobs so disabling restores
        # the previous payload bytes as well as the previous scores/order.
        "front_page": {k: v for k, v in cfg.front_page.items() if k not in {"appeal", "popular_affordable", "everyday_price", "demote_words", "mix"}
                       and (k != "fresh_page_size" or drop_fresh.enabled(cfg))
                       and (k != "used_tab_only" or v)
                       and (k != "checkout_price_deals" or checkout_prices.enabled(cfg))},
        "trial_days": int(getattr(cfg, "paywall_trial_days", 7)),
        "yearly_price_usd": max(0.0, getattr(cfg, "paywall_yearly_price_usd", 0) or 0),
    }
    if amazon_reference.enabled(cfg) and "top_picks" in meta["front_page"]:
        meta["front_page"]["top_picks"] = {k: v for k, v in meta["front_page"]["top_picks"].items()
                                          if k != "history_bonus"}
    full_doc = {"meta": meta, "deals": serialized}
    public_doc = {"meta": meta, "deals": teaser}
    if spotlight:
        full_doc["spotlight"] = public_doc["spotlight"] = spotlight
    (out / "deals.json").write_text(
        json.dumps(public_doc, separators=(",", ":")), encoding="utf-8"
    )

    # Source health is the operator's, never the public site's (operator
    # call, 2026-10-11): it goes back to the caller for the run's GitHub
    # Actions summary, and no health page or file is published. A stale
    # health.html/health.json from an older publish is removed.
    health_doc = source_health(db, now, last_run)
    for stale in ("health.json", "health.html"):
        (out / stale).unlink(missing_ok=True)

    pages = ("index.html", "welcome.html", "about.html",
             "careers.html", "contact.html", "how-it-works.html", "hacks.html",
             "subscribe.html", "privacy.html", "disclosure.html")
    if support:
        pages += ("join.html",)
    else:
        (out / "join.html").unlink(missing_ok=True)
    for name in pages:
        if name == "index.html":
            html = home_html
        else:
            shutil.copyfile(TEMPLATES_DIR / name, out / name)
            html = _page_template((out / name).read_text(encoding="utf-8"), cfg)
        if name == "subscribe.html":
            html = page_polish.support(html, cfg)
        if name == "index.html":
            html = html.replace("{{PRICE_OPTIONS}}", "".join(
                f'<option value="{n}">Under ${n}</option>' for n in price_presets))
            html = home_search_text(html, cfg)
        yearly = meta["yearly_price_usd"]
        for place in ("panel", "dialog"):
            choices = ""
            if yearly > 0:
                choices = (
                    f'<fieldset id="{place}plans" class="plans" data-clarity-mask="true">'
                    '<legend class="sr-only">Billing plan</legend>'
                    f'<label><input type="radio" name="{place}plan" value="month" data-clarity-mask="true" checked>'
                    '<span>Monthly $9.99</span></label>'
                    f'<label><input type="radio" name="{place}plan" value="year" data-clarity-mask="true">'
                    f'<span>Yearly ${yearly:g}</span></label></fieldset>'
                )
            html = html.replace("{{PLAN_CHOICES_" + place.upper() + "}}", choices)
        html = copy_variant(html, open_access, support)
        html = html.replace("{{DEAL_ACTIONS_SCRIPT}}", (TEMPLATES_DIR / "deal-actions.js").read_text(encoding="utf-8"))
        html = re.sub(r"<!--engagement-->(.*?)<!--/engagement-->",
                      lambda m: m.group(1) if cfg.engagement else "", html, flags=re.S)
        if name == "index.html" and cfg.engagement:
            html = html.replace("{{ENGAGEMENT_SCRIPT}}", engagement_script)
            html = html.replace("{{SURPRISE_HIDDEN}}", "" if engagement["surprise_me"] else "hidden")
        html = re.sub(r"<!--saved-->(.*?)<!--/saved-->",
                      lambda m: m.group(1) if saved_deals else "", html, flags=re.S)
        html = html.replace("<!--watch-->", "").replace("<!--/watch-->", "")
        html = html.replace("{{HOT_EXPLANATION}}", (
            " Flames highlight offers with a combination of strong discounts, "
            "independent confirmation and recent arrival."
        ) if engagement["hot_meter"] else "")
        html = html.replace("{{DISCLOSURE}}", escape(meta["disclosure"]))
        if name == "disclosure.html":
            html = html.replace("{{AFFILIATE_PROGRAMS}}", affiliate_programs(cfg))
        if support:
            html = html.replace("{{SUPPORT_SCRIPT}}", support_script(link, monthly, cfg.support_panel_delay_s))
            html = html.replace("{{SUPPORT_BODY}}", SUPPORT_BODY.format(cost_phrase=escape(cfg.support_cost_phrase)))
            html = html.replace("{{SUPPORT_ACTION}}", (
                f'<a class="pwbtn btn" data-support href="{escape(link, quote=True)}" '
                'target="_blank" rel="noopener">' + ("Support once" if monthly else "Support our work")
                + '</a>' if link else "") + (
                f'<a class="pwbtn btn" data-support-monthly href="{escape(monthly, quote=True)}" '
                'target="_blank" rel="noopener">Support monthly</a>' if monthly else ""))
            html = html.replace("{{SUPPORT_NOTE}}", (
                f'<p class="disc muted" data-support-note>{SUPPORT_NOTE_MONTHLY if monthly else SUPPORT_NOTE}</p>'
                if link else ""))
            card = (TEMPLATES_DIR / "join-card.html").read_text(encoding="utf-8")
            card = card.replace("{{JOIN_FORM}}", JOIN_FORM)
            card = card.replace("{{JOIN_HEADING}}", "h1" if name == "join.html" else "h2")
            card = card.replace("{{JOIN_CLOSE}}", '' if name == "join.html" else (
                '<button type="button" class="joinclose" data-join-close aria-label="Close sign-up prompt">✕</button>'))
            html = html.replace("{{JOIN_CARD}}", card)
            html = html.replace("{{JOIN_PROMPT}}", (
                '<div id="join-prompt" class="joinoverlay" hidden><div role="dialog" aria-modal="true" '
                'aria-labelledby="join-title" aria-describedby="join-description" tabindex="-1">'
                + card + '</div></div>'))
            html = html.replace("{{JOIN_STYLE}}", (TEMPLATES_DIR / "join-card.css").read_text(encoding="utf-8"))
        html = site_footer(html, now, meta["disclosure"], support,
                           account=name == "index.html" and not support,
                           amazon_as_of_enabled=cfg.amazon_as_of_enabled,
                           product_pages_enabled=cfg.seo_product_pages_enabled and not amazon_reference.enabled(cfg),
                           event_links=seo.event_pages.links(cfg), checker_enabled=cfg.checker["enabled"])
        (out / name).write_text(html, encoding="utf-8")
    # The home page learns its own version so a cached copy reloads itself
    # when a newer publish (meta.site_version) appears.
    index = (out / "index.html").read_text(encoding="utf-8")
    (out / "index.html").write_text(index.replace("__SITE_VERSION__", site_version, 1),
                                    encoding="utf-8")

    # Every page carries the operator's email (the contact line, and "or
    # email ..." wherever cancelling is explained) only once it is really
    # configured; the unset placeholder must never reach the public site
    # (the pages drop their email wording when the token resolves to "").
    # It lands in HTML attributes and script strings, so only a plain
    # address is used.
    email = getattr(cfg, "contact_email", "") or ""
    if email == "set-me@example.com" or not re.fullmatch(r"[^\s@\"'<>\\&]+@[^\s@\"'<>\\&]+", email):
        email = ""
    for name in pages:
        html = (out / name).read_text(encoding="utf-8")
        html = html.replace("{{CONTACT_EMAIL}}", email)
        html = html.replace("{{DISCLOSURE_CONTACT}}", (
            f'Email <a href="mailto:{email}">{email}</a>.' if email else
            '<a href="/contact.html">Contact us</a>.'))
        html = html.replace("{{DROP_MINUTES}}", str(int(cfg.drop_minutes)))
        (out / name).write_text(html, encoding="utf-8")

    # Search engines: canonical/og URLs on every page ({{SITE_URL}}), plus
    # robots.txt and sitemap.xml for the public pages (welcome.html is
    # noindex: it is the after-checkout page).
    domain = getattr(cfg, "custom_domain", "") or ""
    site_url = f"https://{domain}" if domain else ""
    for name in pages:
        html = (out / name).read_text(encoding="utf-8")
        if "{{SITE_URL}}" in html:
            (out / name).write_text(html.replace("{{SITE_URL}}", site_url), encoding="utf-8")
    public = ["", "about.html", "how-it-works.html", "hacks.html", "subscribe.html",
              "contact.html", "careers.html", "privacy.html", "disclosure.html"]
    if support:
        public.append("join.html")
    # The server-rendered wall, category hubs, 404.html, _headers, the
    # sitemap and the IndexNow due-list, all from this publish's teaser.
    offsite_keys = set() if cfg.offsite_unseen_days is not None else None
    timed("home")
    seo_paths, seo_wall = seo.publish(db, cfg, now, out, teaser, site_url, public, meta, deals,
                                     spotlight=spotlight, deal_pages=deal_pages,
                                     products=products,
                                     run_key=(last_run or {}).get("run_key"),
                                     offsite_protected_keys=offsite_keys, best_extra=best_extra)
    timed("seo")
    checker_summary = {}
    if cfg.checker["enabled"]:
        checker_summary = checker.publish(out, cfg, now, checker.build(
            db, cfg, now, product_public, products, spotlight_rows + deals))
    else:
        check_dir = out / "check"
        if check_dir.is_dir():
            shutil.rmtree(check_dir)
    (out / "robots.txt").write_text(
        "User-agent: *\nAllow: /\nDisallow: /welcome.html\n"
        + ("Disallow: /check/data/\n" if cfg.checker["enabled"] else "")
        + (f"Sitemap: {site_url}/sitemap.xml\n" if site_url else ""), encoding="utf-8")

    # Harmless and kept for a branch-based host: GitHub Pages deploys made by
    # Actions ignore CNAME (the domain is set in Settings -> Pages).
    if getattr(cfg, "custom_domain", ""):
        (out / "CNAME").write_text(cfg.custom_domain + "\n", encoding="utf-8")

    if cfg.checker["enabled"]:
        checker_page.publish(out, cfg, now, site_url, meta["disclosure"])
        seo_paths.append("/check/")

    try:
        compare_paths = compare_pages.publish(out, db, cfg, now, site_url, meta["disclosure"], products,
            deals=[{**originals[d["dedupe_key"]], **d, "pct_at_checkout": d.get("pct_at_checkout")}
                   for d in deal_pages[1]
                   if not d.get("price_unconfirmed") and d["dedupe_key"] in originals], deal_paths=paths)
        compare_pages.footer_links(out, cfg)
        if cfg.compare["publish"] == "on":  # preview pages stay out of the sitemap
            seo_paths.extend(compare_paths)
    except Exception as exc:
        logger.warning("compare pages unavailable (%s)", type(exc).__name__)
        if (out / "compare").is_dir():
            shutil.rmtree(out / "compare")
    timed("checker_compare")

    if support and (link or cfg.support_panel_delay_s):
        # Generated pages share the same footer join success as root pages.
        before, after = support_script(), support_script(link, monthly, cfg.support_panel_delay_s)
        for path in out.rglob("*.html"):
            html = path.read_text(encoding="utf-8")
            updated = html.replace("<script>" + before + "</script>", "<script>" + after + "</script>")
            if updated != html:
                path.write_text(updated, encoding="utf-8")

    if not cfg.pwa_android["enabled"]:
        pwa.publish(out, cfg)
    traffic.publish(out, cfg, [d for d in deal_pages[1] if not d.get("price_unconfirmed")], paths, site_url)
    profiles.publish(out, cfg, site_url)
    inventory.publish(out, cfg)
    search_results.publish(out, cfg)
    search_more.publish(out, cfg)
    analyze_top_sellers.publish(out, cfg)
    shipping_note.publish(out, cfg)
    page_polish.publish(out, cfg)
    if cfg.brand_pages:
        from . import brand_pages
        brand_pages.publish(out, cfg, public_doc)
    dark.publish(out, cfg)

    # Last: the operator's analytics and ad tags (config.yaml tracking:) go
    # into every page written above or by another pass, nested ones included.
    apply_tracking(out, cfg)
    click_signal.publish(out, cfg)
    manifest_refresh.publish(out, cfg)
    if cfg.pwa_android["enabled"]:
        # App detection must precede tracking, including on the private reader.
        # Write the price-free offline page after every global script pass.
        pwa.publish(out, cfg)
    # All template markers and injected scripts have had their final consumer.
    search_analyze_lazy.publish(out, cfg)
    drop_fresh.publish(out, cfg)
    fresh_more_plain.publish(out, cfg)
    front_mix.publish(out, cfg)
    keep_first_cards.publish(out, cfg)
    header_polish.publish(out, cfg)
    card_tidy.publish(out, cfg)
    all_discounts.publish(out, cfg)
    search_polish_1019.publish(out, cfg)
    discounts_from_link.publish(out, cfg)
    search_buttons_1019.publish(out, cfg)
    search_discounts_button.publish(out, cfg)
    card_click_1020.publish(out, cfg)
    buttons_to_tools.publish(out, cfg)
    timer_pill_ring.publish(out, cfg)
    timer_center.publish(out, cfg)
    credits.publish(out, cfg)
    tool_pages_1020.publish_home(out, cfg)
    comments.publish(out, cfg)
    amazon_price_cap.publish(out, cfg)
    tools_new_tab.publish(out, cfg)
    tool_buttons_outside.publish(out, cfg)
    search_focus_ring.publish(out, cfg)
    hide_timer_ticker.publish(out, cfg)
    sticky_buy_bar.publish(out, cfg)
    soft_buttons.publish(out, cfg)
    tools_merge.publish(out, cfg)
    from . import add_to_cart
    add_to_cart.publish(out, cfg)
    timed("page_passes")
    seo_paths.extend(tool_pages.publish(out, cfg, now, site_url, meta["disclosure"],
                                       pick_order(teaser + spotlight_only, cfg.front_page)))
    from . import deal_spotter
    seo_paths.extend(deal_spotter.publish(out, cfg, now, site_url, meta["disclosure"]))
    from . import review_checker
    seo_paths.extend(review_checker.publish(out, cfg, now, site_url, meta["disclosure"]))
    from . import word_links
    word_links.publish(out, cfg)
    join_dialog.publish(out, cfg)
    timed("tools")
    seo_pages = seo.publish_sitemap(db, cfg, now, out, site_url, seo_paths, seo_wall)
    timed("sitemap")
    audit_result = seo_audit.audit(out, cfg, site_url, pages=seo_pages)
    indexnow.seal_publish(db, cfg, now, out, (last_run or {}).get("run_key"),
                         excluded={site_url + path for path, page in seo_pages.items() if page.unconfirmed}
                         | indexnow.product_exclusions(db, site_url, seo_pages)
                         | seo.event_pages.indexnow_exclusions(db, site_url, seo_pages)
                         | tool_pages.indexnow_exclusions(cfg, site_url))
    timed("audit")

    if clicks.enabled(cfg):
        try:
            public_rows = [d for d in spotlight + pick_order(teaser, cfg.front_page)
                           if not d.get("locked") and d.get("status") == "alive"]
            public_rows = list({d["key"]: d for d in public_rows}.values())
            clicks.record_shadow(db, cfg, now, public_rows, run_key=(last_run or {}).get("run_key"))
        except Exception as exc:
            logger.warning("click shadow unavailable: %s", type(exc).__name__)
    else:
        # Do not count a disabled interval if the operator later re-enables it.
        clicks.observe_publication(db, cfg, now, [])

    logger.info("site rendered: %d deals (%d locked) -> %s",
                meta["count"], locked_count, out)
    site_files = sum(p.is_file() for p in out.rglob("*"))
    if site_files > 15000:
        logger.warning("site_files exceeds 15000: %d", site_files)
    from ..keepa_rechecks import History
    top_candidates = [d for d, _ in candidate_scores[:cfg.keepa_recheck_top_n]]
    label_report = price_labels.diagnostic(before_labels, cfg, now, teaser)
    db.meta_set(price_labels.META, json.dumps(label_report, sort_keys=True))
    wall_rows = [originals[d["dedupe_key"]] for d in wall
                 if not d.get("locked") and d.get("dedupe_key") in originals]
    history = History(db, cfg, now)
    tier_coverage = {}
    if cfg.keepa_volatility.get("enabled", False):
        from ..volatility import Plan
        from ..keepa_budget import run_gap_forecast
        plan = Plan(history, db.all_deals(status="alive"),
                    {d["dedupe_key"]: i for i, d in enumerate(top_candidates)},
                    {d["dedupe_key"]: i for i, d in enumerate(wall_rows)},
                    run_gap_forecast(db, cfg, now)["minutes"])
        tier_coverage = {name: {"tiers": plan.coverage(rows)}
                         for name, rows in (("top_candidates", top_candidates), ("wall", wall_rows))}
    from .. import accuracy
    spotlight_keys = {d["dedupe_key"] for d in spotlight}
    audit_wall = [d for d in teaser if not d.get("locked") and d["dedupe_key"] not in spotlight_keys]
    audit_wall = seo.home_order(audit_wall, cfg.front_page)
    accuracy.publish(db, spotlight + audit_wall, now,
                     (last_run or {}).get("run_key") or getattr(db, "accuracy_run_key", None) or iso(now))
    offsite = {}
    if cfg.offsite_unseen_days is not None:
        from ..offsite_expiry import protected_keys
        try:
            offsite["offsite_protected_keys"] = protected_keys(
                db, serialized + spotlight + deal_pages[1], products)
            if offsite["offsite_protected_keys"] is not None:
                offsite["offsite_protected_keys"].update(offsite_keys)
        except (ValueError, TypeError, KeyError):
            # Unreadable surface facts cannot establish absence. The tail
            # retains every non-dead row until a complete proof is available.
            offsite["offsite_protected_keys"] = None
    result = {"published": len(serialized) + len(spotlight_only), "out_dir": str(out),
            **offsite,
            "unpublished_photoless": unpublished_photoless,
            "hidden_deal_site_links": len(hidden_links),
            "hidden_deal_site_links_by_credit": hidden_by_credit,
            **({"amazon_reference": {"list_price": sum(d.get("regular_price_basis") == "List price" for d in teaser if not d.get("locked")),
                                      "price_drop": sum(bool(d.get("price_drop")) and is_amazon_deal(originals.get(d.get("dedupe_key"), d))
                                                        for d in teaser if not d.get("locked"))}}
               if amazon_reference.enabled(cfg) else {}),
            "site_files": site_files,
            "public_deals": teaser + spotlight_only,
            "unconfirmed_label": label_report,
            "checkout": checkout_metrics(teaser + spotlight_only),
            "appeal": appeal_summary(seo.home_order(teaser, cfg.front_page) if want_on
                                     else pick_order(teaser, cfg.front_page)),
            **({"want_season": lift_summary} if lift_summary is not None else {}),
            **({"popular_affordable": popular_summary} if popular_summary is not None else {}),
            "checkout_price_deals": checkout_summary,
            "product_pages": product_summary,
            **({"ai_take": take_summary} if cfg.ai_take["enabled"] else {}),
            **({"checker": checker_summary} if cfg.checker["enabled"] else {}),
            "posted_redirects": deal_pages[3],
            "confirmed_age": {
                "top_candidates": {**confirmation_coverage(top_candidates, now),
                                   "request_age": history.request_age(top_candidates),
                                   **tier_coverage.get("top_candidates", {})},
                "wall": {**confirmation_coverage(wall_rows, now),
                         "request_age": history.request_coverage(wall_rows),
                         **tier_coverage.get("wall", {})}},
            "link_mix": link_mix(teaser + spotlight_only, amazon_tag,
                                 competitor_tracker_hosts=cfg.affiliate_competitor_tracker_hosts,
                                 ebay_campid=cfg.affiliate_ebay_campaign_id),
            "full_payload": full_doc, "health": health_doc, "seo_audit": audit_result}
    timed("tail")
    # Slowest first, so the next live runs show where render time goes.
    logger.info("render timings: %s (total %.1fs)", ", ".join(
        f"{name} {seconds:.1f}s" for name, seconds in sorted(timings.items(), key=lambda kv: -kv[1])),
        sum(timings.values()))
    return result
