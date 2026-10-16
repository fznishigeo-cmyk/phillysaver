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
from collections import Counter
from datetime import datetime
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote_plus, urlencode, urlsplit, urlunsplit

from ..savings import display_pct, known_shipping, parse_stack_step
from ..config import AppConfig, load_sources, redact_secrets, support_link, validated_drop_clock
from ..rank import appeal_summary, daily_spotlight, pick_order, pick_score, top_picks
from ..verify import (confirmation_coverage, is_amazon_deal, price_confirmed_at,
                       price_confirmation_recent, price_unconfirmed)
from ..source_policy import host_of, source_image_allowed
from ..util import iso, parse_iso
from .. import checkout_prices
from . import amazon_as_of, amazon_history, click_signal, indexnow, inventory, price_labels, product_pages, pwa, seo, seo_audit, traffic
from .paywall import is_open, split_teaser, teaser_memory
from .credits import source_display_name, source_sightings
from .tracking import apply_tracking

logger = logging.getLogger("dealradar.publish.site")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def _credit(sighting: dict[str, Any]) -> dict[str, str]:
    """Public credit, including stored sightings from before the rename."""
    name = sighting.get("source_name", "")
    url = sighting.get("source_url", "")
    host = (urlsplit(url or "").hostname or "").lower()
    if name.lower().startswith("keepa") or host == "keepa.com" or host.endswith(".keepa.com"):
        return {"name": "Amazon price drop", "url": ""}
    return {"name": source_display_name(name), "url": url}


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


_WATCH_BLOCK = re.compile(r"<!--watch-->(.*?)<!--/watch-->", re.S)


def site_template_version(price_presets: list[int] | None = None) -> str:
    """Short hash of the standard home template, preserving its version
    when only the optional support variant changes."""
    import hashlib
    html = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
    html = price_labels.template(html, False)
    html = checkout_prices.template(html, False)
    html = _PHOTOLESS_BLOCK.sub("", html)
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
    browse = (TEMPLATES_DIR / "browse.js").read_text(encoding="utf-8")
    browse = re.sub(r"<!--saved-->.*?<!--/saved-->", "", browse, flags=re.S)
    presets = AppConfig().seo_price_pages if price_presets is None else price_presets
    data += browse.replace("{{PRICE_PRESETS}}", json.dumps(presets)).encode()
    return hashlib.sha256(data).hexdigest()[:12]


_AMAZON_HOSTS = {"amazon.com", "www.amazon.com", "smile.amazon.com"}
_AMAZON_NETWORK_HOST = re.compile(r"(^|\.)amazon\.[a-z]{2,3}(\.[a-z]{2})?$")
_SOVRN_HOSTS = {"redirect.viglink.com", "sovrn.co"}
_EBAY_HOSTS = {"ebay.com", "www.ebay.com"}
#: eBay Partner Network (US) tracking: these replace any already in the link.
_EPN_PARAMS = ("mkcid", "mkrid", "siteid", "campid", "customid", "toolid", "mkevt")
_AFFILIATE_PARAMS = {
    "irgwc", "afsrc", "sourceid", "veh", "wmlspartner", "affiliates_ad_id",
    "campaign_id", "sharedid", "clickid", "irclickid", "cjevent", "cjdata",
    "ranmid", "raneaid", "ransiteid", "siteid", "sscid",
}


def is_competitor_tracker_host(host: str, hosts: list[str] | tuple[str, ...]) -> bool:
    host = host.lower().removeprefix("www.")
    return any(host == h or host.endswith("." + h) for h in hosts)


def keeps_source_tracker(url: str, cfg) -> bool:
    """True when ``url`` is a source's tracker that must stay exactly as posted.

    While the tracker repair is off, a permitted source's tracker IS its
    affiliate credit (crawl-permission condition): nothing may resolve,
    canonicalize or replace it.
    """
    if getattr(cfg, "affiliate_repair_competitor_trackers", False) or not url:
        return False
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return bool(host) and is_competitor_tracker_host(
        host, getattr(cfg, "affiliate_competitor_tracker_hosts", ()))


@lru_cache(maxsize=1)
def _deal_source_hosts() -> set[str]:
    """Known source/blog hosts, including paused sources; stores can also have feeds."""
    primary, backup = load_sources()
    retailers = {"woot.com", "meh.com", "morningsave.com", "sidedeal.com",
                 "dealgenius.com", "dailysteals.com", "tanga.com", "1sale.com"}
    return {(urlsplit(s.site or s.url).hostname or "").lower().removeprefix("www.")
            for s in primary + backup} - retailers - {""}


def rewrite_affiliate(url: str, amazon_tag: str = "", *, sovrn_key: str = "",
                      sovrn_enabled: bool = False, cuid: str = "",
                      credit_urls: list[str] | None = None,
                      competitor_tracker_hosts: list[str] | tuple[str, ...] = (),
                      ebay_campid: str = "") -> str:
    """The one place outbound links are rewritten (nothing else may).

    With the operator's Amazon Associates Store ID (config affiliate.amazon_tag)
    an https amazon.com link carries tag=<id>, replacing any tag already in it;
    with the eBay Partner Network campaign ID (affiliate.ebay_campaign_id) an
    https ebay.com link carries our EPN parameters, replacing any in it;
    eligible other retailers use Sovrn after removing affiliate tracking.
    Emails pass neither a tag nor a Sovrn key, leaving their links unchanged.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        return url
    if is_competitor_tracker_host(host, competitor_tracker_hosts):
        return url
    if amazon_tag and host in _AMAZON_HOSTS:
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "tag"]
        query.append(("tag", amazon_tag))
        return urlunsplit(parts._replace(query=urlencode(query)))
    if re.fullmatch(r"[0-9]{10}", ebay_campid) and host in _EBAY_HOSTS:
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if k.lower() not in _EPN_PARAMS]
        query += [("mkcid", "1"), ("mkrid", "711-53200-19255-0"), ("siteid", "0"),
                  ("campid", ebay_campid)]
        if re.fullmatch(r"[A-Za-z0-9]{1,32}", cuid):
            query.append(("customid", cuid))
        query += [("toolid", "10001"), ("mkevt", "1")]
        return urlunsplit(parts._replace(query=urlencode(query)))
    if not sovrn_enabled or not re.fullmatch(r"[0-9a-f]{32}", sovrn_key):
        return url
    excluded = (_deal_source_hosts() | _SOVRN_HOSTS | set(competitor_tracker_hosts)
                | {"wowpriced.com", "amzn.to", "a.co", "link.amazon"})
    for credit in credit_urls or []:
        try:
            credit_host = (urlsplit(credit).hostname or "").lower().removeprefix("www.")
        except ValueError:
            continue
        if credit_host:
            excluded.add(credit_host)
    if _AMAZON_NETWORK_HOST.search(host) or any(host == h or host.endswith("." + h) for h in excluded):
        return url
    # Keep product options (and their original encoding) exactly as posted.
    query = [p for p in parts.query.split("&")
             if (k := unquote_plus(p.split("=", 1)[0]).lower()) not in _AFFILIATE_PARAMS
             and not k.startswith("utm_")]
    merchant = urlunsplit(parts._replace(query="&".join(query)))
    wrapped = f"https://redirect.viglink.com?key={sovrn_key}&u={quote(merchant, safe='')}"
    if re.fullmatch(r"[A-Za-z0-9]{1,32}", cuid):
        wrapped += f"&cuid={cuid}"
    return wrapped


def _link_destination(deal: dict[str, Any], amazon_tag: str,
                      competitor_tracker_hosts: list[str] | tuple[str, ...],
                      ebay_campid: str = "") -> tuple[str, str, bool]:
    """Classify the final Get deal URL once for earning checks and counts."""
    url = deal.get("url") or ""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().removeprefix("www.")
    except ValueError:
        return "none", "", False
    if not host or parts.scheme not in ("http", "https"):
        return "none", "", False
    ours = bool(amazon_tag and parts.scheme == "https"
                and ("tag", amazon_tag) in parse_qsl(parts.query))
    if host in {"amazon.com", "smile.amazon.com"}:
        bucket = "amazon_tagged" if ours else "amazon_untagged"
    elif host in {"amzn.to", "a.co", "link.amazon"}:
        bucket = "amazon_short"
    elif any(url == c.get("url") for c in deal.get("credits") or []):
        bucket = "source_post"
    elif host in _SOVRN_HOSTS:
        bucket = "sovrn"
    elif host == "ebay.com" and ebay_campid and ("campid", ebay_campid) in parse_qsl(parts.query):
        bucket = "ebay_epn"
    else:
        bucket = "other_store"
    earns = (parts.scheme == "https" and not is_competitor_tracker_host(host, competitor_tracker_hosts)
             and (bucket in {"amazon_tagged", "sovrn", "ebay_epn"}
                  or (bucket == "amazon_short" and ours)))
    return bucket, host, earns


def earning_link(deal: dict[str, Any], amazon_tag: str = "", *,
                 competitor_tracker_hosts: list[str] | tuple[str, ...] = (),
                 ebay_campid: str = "") -> bool:
    """Whether this deal's final Get deal destination credits our affiliate link."""
    return _link_destination(deal, amazon_tag, competitor_tracker_hosts, ebay_campid)[2]


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
        if reg:
            lead += f" the usual ${reg:,.2f}"
        elif deal.get("basis") == "list_price_claim":
            lead += " the claimed list price"
    parts = [lead + "."]
    if codes and not _needs_steps(deal):  # else the card's "how" box shows them
        parts.append(("Code " if len(codes) == 1 else "Codes ") + ", ".join(codes) + ".")
    if "range" in flags:
        parts.append("Price varies by option; the discount holds even at the highest price.")
    if "rebate_dependent" in flags:
        parts.append("Rebate is later; not deducted from this price.")
    if "unknown_shipping" in flags:
        parts.append("Shipping cost unclear — check at checkout.")
    tier = deal.get("verify_tier", "unverified")
    if tier == "corroborated":
        parts.append("Confirmed by multiple independent sources.")
    elif tier == "history-checked":
        if not amazon_history.hidden(cfg, deal):
            parts.append("Well below the usual price in our own price history.")
    else:
        parts.append("Reported by one source — not yet confirmed.")
    expires = parse_iso(deal.get("expires_at") or "")
    if expires is not None:
        parts.append(f"Expires {expires.strftime('%b %d')}.")
    return " ".join(parts)


#: Conditions under which the product page alone may not show the price
#: (a cart-only drop, a targeted or new-customer offer, a multi-buy, an
#: in-store price): the source post says how to get it.
_STEP_CONDITIONS = frozenset(("subscribe_save", "in_cart", "new_customer", "targeted",
                              "ymmv", "multi_buy", "in_store_only", "clearance_aisle"))


def _needs_steps(deal: dict[str, Any]) -> bool:
    """True when the price takes more than opening the product page: a code,
    a coupon to clip, Subscribe & Save, a rebate or a step condition (live:
    a $2.99 thermometer whose Amazon page said $8.99 before the code)."""
    from ..verify import _price_hidden_by_promo

    return bool(_price_hidden_by_promo(deal) or deal.get("rebates")
                or _STEP_CONDITIONS & set(deal.get("conditions") or []))


def _simple_code_deal(deal: dict[str, Any]) -> bool:
    """Only applying a listed code is needed; unfamiliar steps stay on the post."""
    codes = [re.escape(str(c).strip()) for c in deal.get("codes") or []
             if str(c).strip()]
    if not codes or deal.get("rebates") or "rebate_dependent" in (deal.get("flags") or []) \
            or _STEP_CONDITIONS & set(deal.get("conditions") or []):
        return False
    code_step = re.compile(
        r"(?:use|enter|apply)\s+(?:the\s+)?"
        r"(?:(?:(?:coupon|promo|discount)\s+)?code\s*:?\s*)?"
        r"[\"']?(?:" + "|".join(codes) + r")[\"']?(?:\s+at\s+checkout)?[.!]?", re.I)
    return all(code_step.fullmatch(str(step).strip())
               for step in deal.get("stack_steps") or [])


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
    "wowpriced is free for everyone. Checking prices around the clock costs us hundreds of dollars a "
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


def support_script(link: str = "", monthly: str = "") -> str:
    """Omit the optional ask entirely without a support link, preserving bytes."""
    script = (TEMPLATES_DIR / "support.js").read_text(encoding="utf-8")
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
                product_pages_enabled: bool = False, event_links: list | None = None) -> str:
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


def _override_key(url: str) -> str:
    """"walmart.com/222918738": a store page's host and its last path segment
    (the product id), so the slug and tracking query never matter."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower().removeprefix("www.")
    last = parts.path.rstrip("/").rsplit("/", 1)[-1]
    return f"{host}/{last}" if host and last else ""


def _serialize_deal(deal: dict[str, Any], now: datetime, amazon_tag: str = "",
                    overrides: dict[str, str] | None = None, *,
                    sovrn_key: str = "", sovrn_enabled: bool = False,
                    competitor_tracker_hosts: list[str] | tuple[str, ...] = (),
                    ebay_campid: str = "", cfg=None) -> dict[str, Any]:
    """Project a deal dict to the public JSON shape (facts + link + credit)."""
    first_seen = parse_iso(deal.get("first_seen_at") or "")
    age_hours: float | None = None
    if first_seen is not None:
        age_hours = round((now - first_seen).total_seconds() / 3600.0, 2)
    # One credit per source as shown: two feeds of one partner (Hip2Save and
    # Hip2Save Amazon), or its feed and newsletter, read "via Hip2Save" once,
    # with its first post's link.
    credits = []
    for credit in (_credit(s) for s in source_sightings(deal)):
        if all(credit["name"] != c["name"] for c in credits):
            credits.append(credit)
    status = deal.get("status", "alive")
    # No resolved product URL -> link the deal to the source post that
    # reported it (which also carries the credit) rather than nothing. A
    # deal that needs steps links there too: the product page alone shows
    # a higher price, the post says how to get this one.
    canon = deal.get("canonical_url") or ""
    posted = deal.get("link_as_posted") or ""
    protected_link = next((u for u in (posted, canon) if u and
                           is_competitor_tracker_host(host_of(u), competitor_tracker_hosts)), "")
    protected = bool(protected_link)
    if protected:
        canon = protected_link
    post = next((c["url"] for c in credits
                 if str(c["url"]).startswith(("https://", "http://"))), "")
    steps = _needs_steps(deal)
    checkout = _checkout(deal, steps)
    key = hashlib.sha256(str(deal.get("dedupe_key", "")).encode("utf-8")).hexdigest()[:16]
    store_link = rewrite_affiliate(canon, amazon_tag, sovrn_key=sovrn_key,
                                   sovrn_enabled=sovrn_enabled, cuid=key,
                                   credit_urls=[c["url"] for c in credits],
                                   competitor_tracker_hosts=competitor_tracker_hosts,
                                   ebay_campid=ebay_campid) if canon else ""
    override = (overrides or {}).get(_override_key(canon)) if canon else None
    # Get deal goes to the store when it earns (operator, 2026-10-12): any
    # Amazon deal with our tag, and simple code deals at a store with a
    # Sovrn wrap or override. Other steps keep the post. The card keeps the
    # code, the steps and the "via" credit either way.
    try:
        host = (urlsplit(canon).hostname or "").lower() if canon.startswith("https://") else ""
    except ValueError:
        host = ""
    if override and not protected:
        store_link = override
    earning = earning_link({"url": store_link, "credits": credits}, amazon_tag,
                           competitor_tracker_hosts=competitor_tracker_hosts,
                           ebay_campid=ebay_campid)
    amazon = bool(amazon_tag and host in _AMAZON_HOSTS)
    ours = amazon or (earning and _simple_code_deal(deal))
    url = post if steps and post and not ours else \
        store_link if canon else (credits[0]["url"] if credits else "")
    if protected:
        url = canon
    image = deal.get("image_url") or ""
    if not source_image_allowed(image, deal.get("sightings") or [], cfg):
        image = ""
    public = {
        "dedupe_key": deal.get("dedupe_key", ""),
        # Opaque id (locked stubs carry only this: the dedupe key names the
        # product link or the item words, which a visitor must not see).
        "key": key,
        "store": deal.get("store", ""),
        "item": deal.get("item", ""),
        "brand": deal.get("brand", ""),
        "url": url,
        "earns": earning_link({"url": url, "credits": credits}, amazon_tag,
                              competitor_tracker_hosts=competitor_tracker_hosts,
                              ebay_campid=ebay_campid),
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
    if getattr(cfg, "amazon_history", "show") == "hide":
        public["amazon"] = is_amazon_deal(deal)
    from ..store_prices import store_offer
    checked = price_confirmed_at(deal) if is_amazon_deal(deal) or store_offer(deal)[0] else None
    if checked is not None and checked <= now:
        public["checked_at"] = iso(checked)
        if getattr(cfg, "amazon_as_of_enabled", False) and is_amazon_deal(deal):
            public["amazon_as_of"] = amazon_as_of.time_text(checked)
    if deal.get("regular_price_basis"):
        public["regular_price_basis"] = deal["regular_price_basis"]
    if deal.get("spotlight"):
        public["spotlight"] = deal["spotlight"]
    if deal.get("price_unconfirmed"):
        public["price_unconfirmed"] = True
    if deal.get("price_aged"):
        public["price_aged"] = deal["price_aged"]
    label = checkout_prices.label(deal, cfg, now)
    if label:
        public["checkout_price"] = label
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
                last_run: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write deals.json and the HTML pages to out_dir; return the counts,
    the members' full payload and the operator's source health."""
    if deals is None:
        from dealradar import rank  # lazy: avoid hard import cycle at module load

        # The site shows qualifying deals that recently died too (marked dead);
        # alerts and the digest use rank.published_deals (alive only).
        spotlight_rows = db.all_deals(status="alive")
        deals = rank.site_deals(db, cfg, now, include_unconfirmed=True)
    else:
        spotlight_rows = deals
    # A price-only offer has one home: the separate spotlight payload. It
    # never enters the grid, teaser rotation, picks or SEO discount pages.
    deals = [d for d in deals if d.get("pct_at_checkout") is not None]

    out = Path(out_dir) if out_dir is not None else Path(cfg.site_dir)
    out.mkdir(parents=True, exist_ok=True)
    # A partial render must not leave the previous run's deployment manifest.
    indexnow.manifest_path(out).unlink(missing_ok=True)

    amazon_tag = getattr(cfg, "affiliate_amazon_tag", "")
    overrides = getattr(cfg, "affiliate_link_overrides", {}) or {}
    link_options = dict(sovrn_key=cfg.affiliate_sovrn_key,
                        sovrn_enabled=cfg.affiliate_sovrn_enabled,
                        competitor_tracker_hosts=cfg.affiliate_competitor_tracker_hosts,
                        ebay_campid=cfg.affiliate_ebay_campaign_id, cfg=cfg)
    candidate_scores = [(d, value) for d in deals
                        if (value := pick_score(d, cfg, now, require_confirmation=False)) is not None]
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
    serialized = [_serialize_deal(d, now, amazon_tag, overrides, **link_options) for d in deals
                  if d.get("status") != "dead" or d.get("death") in {"confirmed", "api_ended"}]
    earns = {d["dedupe_key"]: d["earns"] for d in serialized}
    scores = {d["dedupe_key"]: pick_score(dict(d, earns=earns[d["dedupe_key"]]), cfg, now,
                                       require_confirmation=False)
              for d in deals if d["dedupe_key"] in earns}
    for d in serialized:
        value = scores[d["dedupe_key"]]
        if value is not None:
            d["ps"] = round(value, 1)
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
        teaser += [dict(_stub(d), price_unconfirmed=True) for d in extras]
        locked_count += len(extras)
    unlocked = {d["dedupe_key"] for d in teaser if not d.get("locked")}
    spotlight = [_serialize_deal(d, now, amazon_tag, overrides, **link_options)
                 for d in daily_spotlight([d for d in spotlight_rows
                     if d.get("pct_at_checkout") is None or d["dedupe_key"] in unlocked], cfg, now)]
    spotlight_only = [d for d in spotlight if d["dedupe_key"] not in unlocked]
    candidates = [(d, scores[d["dedupe_key"]]) for d in teaser
                  if not d.get("locked") and scores[d["dedupe_key"]] is not None]
    originals = {d["dedupe_key"]: d for d in deals}
    confirmed_candidates = [(d, value) for d, value in candidates
                            if not is_amazon_deal(originals[d["dedupe_key"]])
                            or (checkout_prices.enabled(cfg) and checkout_prices.kind(originals[d["dedupe_key"]]))
                            or price_confirmation_recent(originals[d["dedupe_key"]], now,
                                                         cfg.keepa_pick_max_unconfirmed_h)]
    checkout_summary = checkout_prices.summary()
    # Rank original facts: public rows omit sightings and raw checkout steps.
    public_candidates = {d["dedupe_key"]: d for d, _ in confirmed_candidates}
    picked = [public_candidates[d["dedupe_key"]] for d in top_picks(
        [(originals[d["dedupe_key"]], value) for d, value in confirmed_candidates],
        cfg.front_page, now, summary=checkout_summary, cfg=cfg)]
    for n, d in enumerate(picked, 1):
        d["pick"] = n
    if checkout_prices.enabled(cfg):
        for d in teaser + spotlight_only:
            if d.get("locked") or d.get("status") != "alive":
                continue
            original = originals.get(d["dedupe_key"], d)
            checkout_summary["labelled"] += bool(d.get("checkout_price"))
            if is_amazon_deal(original) and checkout_prices.kind(original):
                checkout_summary["unlabelled_no_reading"] += checkout_prices.reading(original, cfg, now) is None
    wall = seo.place_gone(pick_order([d for d in teaser if not d.get("price_unconfirmed")],
                                   cfg.front_page))[:cfg.keepa_recheck_wall_n]
    db.meta_set(WALL_META, json.dumps([d["dedupe_key"] for d in wall
                                     if not d.get("locked") and d.get("status") == "alive"]))
    # Select once, before JSON is written: remembered URLs only for pages
    # this publish emits. Free rows share their objects with member rows;
    # locked stubs and member-only deals never gain a public path.
    deal_pages = seo._deal_pages(db, cfg, now, out, teaser, deals)
    paths = deal_pages[0]
    product_public = [d for d in teaser + spotlight + deal_pages[1] if not d.get("locked")]
    products, product_paths, product_summary = product_pages.prepare(
        db, cfg, now, product_public, spotlight_rows + deals)
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
    open_access = is_open(cfg)
    support = bool(getattr(cfg, "support_enabled", False))
    link = support_link(getattr(cfg, "support_payment_link", ""))
    monthly = support_link(getattr(cfg, "support_monthly_link", "")) if link else ""
    if not open_access:  # open: the rotation pauses, to resume when it closes
        db.meta_set(_TEASER_META, json.dumps(teaser_memory(
            [d for d in teaser if not d.get("price_unconfirmed")], recent, cfg)))
    # The mode changes the published wording: a flip reloads open tabs.
    browse_script = (TEMPLATES_DIR / "browse.js").read_text(encoding="utf-8")
    price_presets = sorted({n for n in cfg.seo_price_pages if type(n) is int and n > 0})
    browse_script = browse_script.replace("{{PRICE_PRESETS}}", json.dumps(price_presets))
    site_version = site_template_version(price_presets) + ("-open" if open_access else "")
    if support:
        home = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
        home = price_labels.template(home, False)
        home = checkout_prices.template(home, False)
        home = _PHOTOLESS_BLOCK.sub("", home)
        home = amazon_as_of.template(home, False)
        home = amazon_history.BLOCK.sub("", home)
        home = product_pages.template(home, False)
        # Optional watch markup must not change the disabled page's version.
        home = _WATCH_BLOCK.sub("", home).replace("<!--saved--><!--/saved-->", "")
        data = (home.encode()
                + support_script(link, monthly).encode()
                + (TEMPLATES_DIR / "join-card.html").read_bytes()
                + (TEMPLATES_DIR / "join-card.css").read_bytes()
                + (TEMPLATES_DIR / "footer-signup.html").read_bytes()
                + (TEMPLATES_DIR / "footer-signup.css").read_bytes()
                + (TEMPLATES_DIR / "site-footer.html").read_bytes()
                + (TEMPLATES_DIR / "site-footer.css").read_bytes()
                + (TEMPLATES_DIR / "deal-actions.js").read_bytes()
                + str(cfg.join_prompt).encode() + link.encode() + monthly.encode() + browse_script.encode())
        site_version = hashlib.sha256(data).hexdigest()[:12] + "-support"
    engagement = {name: bool(cfg.engagement.get(name, False)) for name in (
        "new_since_visit", "weekly_finds", "visit_streak", "surprise_me", "hot_meter")}
    saved_deals = support and bool(cfg.engagement.get("saved_deals", False))
    if "saved_deals" in cfg.engagement:
        engagement["saved_deals"] = saved_deals
    saved_limit = max(1, min(200, cfg.engagement_saved_deals_limit))
    if cfg.engagement:
        engagement_script = (TEMPLATES_DIR / "engagement.js").read_text(encoding="utf-8")
        engagement_script = amazon_history.template(engagement_script, cfg)
        engagement_script = price_labels.template(engagement_script, cfg.unconfirmed_label_enabled or cfg.non_amazon_freshness_enabled)
        engagement_script = re.sub(r"/\* saved-deals \*/(.*?)/\* /saved-deals \*/",
                                   lambda m: m.group(1) if saved_deals else "",
                                   engagement_script, flags=re.S)
        site_version = hashlib.sha256(
            site_version.encode() + engagement_script.encode()
            + str(saved_deals).encode() + str(saved_limit).encode()
            + ("".join(_WATCH_BLOCK.findall((TEMPLATES_DIR / "index.html").read_text(encoding="utf-8"))).encode()
               if saved_deals else b"")
        ).hexdigest()[:12]
    if traffic.enabled(cfg):
        site_version = traffic.site_version(site_version, cfg)
    if cfg.unconfirmed_label_enabled or cfg.non_amazon_freshness_enabled:
        site_version = hashlib.sha256((site_version + price_labels.STYLE).encode()
            + "".join(price_labels.BLOCK.findall((TEMPLATES_DIR / "index.html").read_text(encoding="utf-8"))).encode()).hexdigest()[:12]
    if cfg.non_amazon_freshness_enabled:
        site_version = hashlib.sha256((site_version + AGED_STYLE).encode()).hexdigest()[:12]
    if checkout_prices.enabled(cfg):
        site_version = hashlib.sha256(site_version.encode()
            + "".join(checkout_prices.BLOCK.findall((TEMPLATES_DIR / "index.html").read_text(encoding="utf-8"))).encode()).hexdigest()[:12]
    if cfg.photoless_last:
        site_version = hashlib.sha256(site_version.encode()
            + "".join(_PHOTOLESS_BLOCK.findall((TEMPLATES_DIR / "index.html").read_text(encoding="utf-8"))).encode()).hexdigest()[:12]
    if cfg.amazon_as_of_enabled:
        site_version = hashlib.sha256(site_version.encode()
            + "".join(amazon_as_of.BLOCK.findall((TEMPLATES_DIR / "index.html").read_text(encoding="utf-8"))).encode()
            + (amazon_as_of.FOOTER + amazon_as_of.DISCLAIMER + amazon_as_of.STYLE).encode()).hexdigest()[:12]
    if cfg.seo_product_pages_enabled:
        site_version = hashlib.sha256((site_version + product_pages.LINK_STYLE).encode()
            + "".join(product_pages.BLOCK.findall((TEMPLATES_DIR / "index.html").read_text(encoding="utf-8"))).encode()).hexdigest()[:12]
    if cfg.amazon_history == "hide":
        site_version = hashlib.sha256((site_version + "amazon-history-hide").encode()).hexdigest()[:12]
    if cfg.seo_event_pages_enabled:
        site_version = hashlib.sha256((site_version + "event-pages" + json.dumps(
            cfg.seo_event_pages_events, sort_keys=True)).encode()).hexdigest()[:12]
    if inventory.enabled(cfg):
        site_version = inventory.site_version(site_version, cfg)
    if cfg.pwa_enabled:
        site_version = pwa.site_version(site_version, cfg)
    from .. import clicks
    if clicks.enabled(cfg) and clicks.endpoint(cfg):
        site_version = hashlib.sha256((site_version + clicks.endpoint(cfg)).encode()
            + (TEMPLATES_DIR / "click-signal.js").read_bytes()).hexdigest()[:12]
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
        "support": {"enabled": support, "link": link, "monthly_link": monthly},
        "join": {"prompt": support and cfg.join_prompt,
                 "prompt_delay_s": max(15, cfg.join_prompt_delay_s), "apple": cfg.join_apple},
        "engagement": engagement,
        **({"saved_deals_limit": saved_limit} if saved_deals else {}),
        # Appeal is applied in Python; omit its knobs so disabling it restores
        # the previous payload bytes as well as the previous scores/order.
        "front_page": {k: v for k, v in cfg.front_page.items() if k != "appeal"
                       and (k != "checkout_price_deals" or checkout_prices.enabled(cfg))},
        "trial_days": int(getattr(cfg, "paywall_trial_days", 7)),
        "yearly_price_usd": max(0.0, getattr(cfg, "paywall_yearly_price_usd", 0) or 0),
    }
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
        shutil.copyfile(TEMPLATES_DIR / name, out / name)
        html = (out / name).read_text(encoding="utf-8")
        html = price_labels.template(html, cfg.unconfirmed_label_enabled or cfg.non_amazon_freshness_enabled)
        html = checkout_prices.template(html, checkout_prices.enabled(cfg))
        html = _PHOTOLESS_BLOCK.sub(lambda m: m[1] if cfg.photoless_last else "", html)
        html = amazon_as_of.template(html, cfg.amazon_as_of_enabled)
        html = amazon_history.template(html, cfg)
        html = product_pages.template(html, cfg.seo_product_pages_enabled)
        if cfg.seo_product_pages_enabled and name == "index.html":
            html = html.replace("</style>", product_pages.LINK_STYLE + "</style>", 1)
        if (cfg.unconfirmed_label_enabled or cfg.non_amazon_freshness_enabled) and name == "index.html":
            html = html.replace("</style>", price_labels.STYLE + "</style>", 1)
        if cfg.non_amazon_freshness_enabled and name == "index.html":
            html = html.replace("</style>", AGED_STYLE + "</style>", 1)
        if name == "index.html":
            html = html.replace("{{BROWSE_SCRIPT}}", browse_script)
            html = html.replace("{{PRICE_OPTIONS}}", "".join(
                f'<option value="{n}">Under ${n}</option>' for n in price_presets))
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
            html = html.replace("{{SUPPORT_SCRIPT}}", support_script(link, monthly))
            html = html.replace("{{SUPPORT_BODY}}", SUPPORT_BODY)
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
                           product_pages_enabled=cfg.seo_product_pages_enabled,
                           event_links=seo.event_pages.links(cfg))
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
    seo_paths, seo_wall = seo.publish(db, cfg, now, out, teaser, site_url, public, meta, deals,
                                     spotlight=spotlight, deal_pages=deal_pages,
                                     products=products,
                                     run_key=(last_run or {}).get("run_key"))
    (out / "robots.txt").write_text(
        "User-agent: *\nAllow: /\nDisallow: /welcome.html\n"
        + (f"Sitemap: {site_url}/sitemap.xml\n" if site_url else ""), encoding="utf-8")

    # Harmless and kept for a branch-based host: GitHub Pages deploys made by
    # Actions ignore CNAME (the domain is set in Settings -> Pages).
    if getattr(cfg, "custom_domain", ""):
        (out / "CNAME").write_text(cfg.custom_domain + "\n", encoding="utf-8")

    if support and link:
        # Generated pages share the same footer join success as root pages.
        before, after = support_script(), support_script(link, monthly)
        for path in out.rglob("*.html"):
            html = path.read_text(encoding="utf-8")
            updated = html.replace("<script>" + before + "</script>", "<script>" + after + "</script>")
            if updated != html:
                path.write_text(updated, encoding="utf-8")

    pwa.publish(out, cfg)
    traffic.publish(out, cfg, [d for d in deal_pages[1] if not d.get("price_unconfirmed")], paths, site_url)
    inventory.publish(out, cfg)

    # Last: the operator's analytics and ad tags (config.yaml tracking:) go
    # into every page written above or by another pass, nested ones included.
    apply_tracking(out, cfg)
    click_signal.publish(out, cfg)
    seo_pages = seo.publish_sitemap(db, cfg, now, out, site_url, seo_paths, seo_wall)
    audit_result = seo_audit.audit(out, cfg, site_url, pages=seo_pages)
    indexnow.seal_publish(db, cfg, now, out, (last_run or {}).get("run_key"),
                         excluded={site_url + path for path, page in seo_pages.items() if page.unconfirmed}
                         | indexnow.product_exclusions(db, site_url, seo_pages)
                         | seo.event_pages.indexnow_exclusions(db, site_url, seo_pages))

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
    audit_wall = (seo.place_gone(pick_order(audit_wall, cfg.front_page)) if cfg.front_page.get("top_picks")
                  else pick_order(seo.place_gone(audit_wall), cfg.front_page))
    accuracy.publish(db, spotlight + audit_wall, now,
                     (last_run or {}).get("run_key") or getattr(db, "accuracy_run_key", None) or iso(now))
    return {"published": len(serialized) + len(spotlight_only), "out_dir": str(out),
            "public_deals": teaser + spotlight_only,
            "unconfirmed_label": label_report,
            "checkout": checkout_metrics(teaser + spotlight_only),
            "appeal": appeal_summary(pick_order(teaser, cfg.front_page)),
            "checkout_price_deals": checkout_summary,
            "product_pages": product_summary,
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
