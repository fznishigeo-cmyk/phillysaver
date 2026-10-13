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

from ..savings import display_pct
from ..config import load_sources, redact_secrets, support_link
from ..rank import daily_spotlight, pick_score, top_picks
from ..source_policy import host_of, source_image_allowed
from ..util import iso, parse_iso
from . import seo
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


def util_now():
    from ..util import now_utc
    return now_utc()


def site_template_version() -> str:
    """Short hash of the standard home template, preserving its version
    when only the optional support variant changes."""
    import hashlib
    html = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
    # Adding the support switch leaves the standard page, including its
    # version stamp, byte-for-byte unchanged. Standard edits still invalidate it.
    html = re.sub(r"<!--support-->.*?<!--/support-->", "", html, flags=re.S)
    data = html.replace("<!--standard-->", "").replace("<!--/standard-->", "").encode()
    data += (TEMPLATES_DIR / "site-footer.html").read_bytes()
    data += (TEMPLATES_DIR / "site-footer.css").read_bytes()
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


def _headline(deal: dict[str, Any]) -> str:
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
        parts.append("Needs a rebate to reach this price.")
    if "unknown_shipping" in flags:
        parts.append("Shipping cost unclear — check at checkout.")
    tier = deal.get("verify_tier", "unverified")
    if tier == "corroborated":
        parts.append("Confirmed by multiple independent sources.")
    elif tier == "history-checked":
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
}


#: Support wins over open/paywall. Shared membership-only regions use
#: <!--standard--> so the two legacy variants need not duplicate them.
_VARIANT_RE = re.compile(r"<!--(paywall|open|support)-->(.*?)<!--/\1-->", re.S)
_STANDARD_RE = re.compile(r"<!--standard-->(.*?)<!--/standard-->", re.S)
SUPPORT_BODY = (
    "wowpriced is free for everyone: no paywall, no ads. Every day we check thousands "
    "of prices so you only see real deals. If wowpriced saved you money, please chip "
    "in whatever it's worth to you. Most people never do. If you can, you keep it free for everyone."
)
SUPPORT_NOTE = (
    "One-time payment, any amount, through Stripe. wowpriced is a for-profit business: "
    "support is not tax-deductible and buys nothing extra; every deal stays free."
)
#: With the monthly link too: the renewal terms sit next to the button.
SUPPORT_NOTE_MONTHLY = (
    "Once: any amount. Monthly: $1 times the number you pick, renewing each month "
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


def site_footer(html: str, now: datetime, disclosure: str, support: bool,
                account: bool = False) -> str:
    """One footer for root and nested pages, with the configured disclosure."""
    if "{{SITE_FOOTER}}" not in html:
        return html
    footer = (TEMPLATES_DIR / "site-footer.html").read_text(encoding="utf-8")
    footer = seo._fill(footer, FOOTER_YEAR=str(now.year), FOOTER_DISCLOSURE=escape(disclosure),
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
        script = (TEMPLATES_DIR / "support.js").read_text(encoding="utf-8")
        html = html.replace("</body>", "<script>" + script + "</script>\n</body>", 1)
    return html


_SOURCE_ONLY_ABOUT = re.compile(
    r"Some\s+of those links are the source's affiliate links\. That is how they keep the lights\s+on, and we think they earned it\.")
AFFILIATE_ABOUT = ("Some of those links are affiliate links, the source's or ours: wowpriced or "
                   "the source may earn a commission, at no extra cost to you. As an Amazon "
                   "Associate, wowpriced earns from qualifying purchases. That is how the site "
                   "stays free.")


def affiliate_copy(html: str, amazon_tag: str) -> str:
    """With a Store ID set, the about copy says wowpriced may earn too
    (FTC + Amazon Associates); without one it stays the source-only line."""
    if not amazon_tag:
        return html
    return _SOURCE_ONLY_ABOUT.sub(AFFILIATE_ABOUT, html)


def copy_variant(html: str, open_access: bool, support: bool = False) -> str:
    """html with the wording for the current access mode only."""
    # Shared membership markup/scripts need not be duplicated in open/paywall.
    html = _STANDARD_RE.sub(lambda m: "" if support else m.group(1), html)
    keep = "support" if support else ("open" if open_access else "paywall")
    return _VARIANT_RE.sub(lambda m: m.group(2) if m.group(1) == keep else "", html)


def _how(deal: dict[str, Any]) -> list[str]:
    """The steps as the post stated them (stack steps, then rebates), short
    and at most four: the card's "how to get this price" line. With none,
    what the deal's conditions ask (once each)."""
    steps = [str(x).strip() for x in
             list(deal.get("stack_steps") or []) + list(deal.get("rebates") or [])]
    steps = [x[:90] for x in steps if x]
    if not steps:
        steps = list(dict.fromkeys(_CONDITION_HOW[c] for c in deal.get("conditions") or []
                                   if c in _CONDITION_HOW))
    return steps[:4]


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
        "how": _how(deal) if steps else [],
        "conditions": deal.get("conditions") or [],
        "category": deal.get("category") or "other",
        "badge": deal.get("verify_tier", "unverified"),
        "headline": _headline(deal),
        "status": status,
        "alive": status == "alive",
        "gone": status == "dead" and deal.get("death") in {"confirmed", "api_ended"},
        "died_at": deal.get("died_at", ""),
        "first_seen_at": deal.get("first_seen_at", ""),
        "age_hours": age_hours,
        "credits": credits,
    }
    if deal.get("spotlight"):
        public["spotlight"] = deal["spotlight"]
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
        deals = rank.site_deals(db, cfg, now)
    else:
        spotlight_rows = deals
    # A price-only offer has one home: the separate spotlight payload. It
    # never enters the grid, teaser rotation, picks or SEO discount pages.
    deals = [d for d in deals if d.get("pct_at_checkout") is not None]

    out = Path(out_dir) if out_dir is not None else Path(cfg.site_dir)
    out.mkdir(parents=True, exist_ok=True)

    amazon_tag = getattr(cfg, "affiliate_amazon_tag", "")
    overrides = getattr(cfg, "affiliate_link_overrides", {}) or {}
    link_options = dict(sovrn_key=cfg.affiliate_sovrn_key,
                        sovrn_enabled=cfg.affiliate_sovrn_enabled,
                        competitor_tracker_hosts=cfg.affiliate_competitor_tracker_hosts,
                        ebay_campid=cfg.affiliate_ebay_campaign_id, cfg=cfg)
    serialized = [_serialize_deal(d, now, amazon_tag, overrides, **link_options) for d in deals
                  if d.get("status") != "dead" or d.get("death") in {"confirmed", "api_ended"}]
    earns = {d["dedupe_key"]: d["earns"] for d in serialized}
    scores = {d["dedupe_key"]: pick_score(dict(d, earns=earns[d["dedupe_key"]]), cfg, now)
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
    teaser, locked_count = split_teaser(serialized, cfg, recent)
    unlocked = {d["dedupe_key"] for d in teaser if not d.get("locked")}
    spotlight = [_serialize_deal(d, now, amazon_tag, overrides, **link_options)
                 for d in daily_spotlight([d for d in spotlight_rows
                     if d.get("pct_at_checkout") is None or d["dedupe_key"] in unlocked], cfg, now)]
    spotlight_only = [d for d in spotlight if d["dedupe_key"] not in unlocked]
    candidates = [(d, scores[d["dedupe_key"]]) for d in teaser
                  if not d.get("locked") and scores[d["dedupe_key"]] is not None]
    for n, d in enumerate(top_picks(candidates, cfg.front_page), 1):
        d["pick"] = n
    open_access = is_open(cfg)
    support = bool(getattr(cfg, "support_enabled", False))
    link = support_link(getattr(cfg, "support_payment_link", ""))
    monthly = support_link(getattr(cfg, "support_monthly_link", "")) if link else ""
    if not open_access:  # open: the rotation pauses, to resume when it closes
        db.meta_set(_TEASER_META, json.dumps(teaser_memory(teaser, recent, cfg)))
    # The mode changes the published wording: a flip reloads open tabs.
    site_version = site_template_version() + ("-open" if open_access else "")
    if support:
        data = ((TEMPLATES_DIR / "index.html").read_bytes()
                + (TEMPLATES_DIR / "support.js").read_bytes()
                + (TEMPLATES_DIR / "join-card.html").read_bytes()
                + (TEMPLATES_DIR / "join-card.css").read_bytes()
                + (TEMPLATES_DIR / "footer-signup.html").read_bytes()
                + (TEMPLATES_DIR / "footer-signup.css").read_bytes()
                + (TEMPLATES_DIR / "site-footer.html").read_bytes()
                + (TEMPLATES_DIR / "site-footer.css").read_bytes()
                + str(cfg.join_prompt).encode() + link.encode() + monthly.encode())
        site_version = hashlib.sha256(data).hexdigest()[:12] + "-support"
    engagement = {name: bool(cfg.engagement.get(name, False)) for name in (
        "new_since_visit", "weekly_finds", "visit_streak", "surprise_me", "hot_meter")}
    if cfg.engagement:
        site_version = hashlib.sha256(
            site_version.encode() + (TEMPLATES_DIR / "engagement.js").read_bytes()
        ).hexdigest()[:12]
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
        "front_page": cfg.front_page,
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
             "subscribe.html", "privacy.html")
    if support:
        pages += ("join.html",)
    else:
        (out / "join.html").unlink(missing_ok=True)
    for name in pages:
        shutil.copyfile(TEMPLATES_DIR / name, out / name)
        html = (out / name).read_text(encoding="utf-8")
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
        html = re.sub(r"<!--engagement-->(.*?)<!--/engagement-->",
                      lambda m: m.group(1) if cfg.engagement else "", html, flags=re.S)
        if name == "index.html" and cfg.engagement:
            html = html.replace("{{ENGAGEMENT_SCRIPT}}", (TEMPLATES_DIR / "engagement.js").read_text(encoding="utf-8"))
            html = html.replace("{{SURPRISE_HIDDEN}}", "" if engagement["surprise_me"] else "hidden")
        html = html.replace("{{HOT_EXPLANATION}}", (
            " Flames mark real signals: at least two are needed, from at least 60% off, "
            "confirmation by two or more independent sources, and a first sighting less "
            "than two hours ago. Two signals show two flames; all three show three."
        ) if engagement["hot_meter"] else "")
        html = affiliate_copy(html, getattr(cfg, "affiliate_amazon_tag", ""))
        if support:
            html = html.replace("{{SUPPORT_SCRIPT}}", (TEMPLATES_DIR / "support.js").read_text(encoding="utf-8"))
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
                           account=name == "index.html" and not support)
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
              "contact.html", "careers.html", "privacy.html"]
    if support:
        public.append("join.html")
    # The server-rendered wall, category hubs, 404.html, _headers, the
    # sitemap and the IndexNow due-list, all from this publish's teaser.
    seo.publish(db, cfg, now, out, teaser, site_url, public, meta, deals, spotlight=spotlight)
    (out / "robots.txt").write_text(
        "User-agent: *\nAllow: /\nDisallow: /welcome.html\n"
        + (f"Sitemap: {site_url}/sitemap.xml\n" if site_url else ""), encoding="utf-8")

    # Harmless and kept for a branch-based host: GitHub Pages deploys made by
    # Actions ignore CNAME (the domain is set in Settings -> Pages).
    if getattr(cfg, "custom_domain", ""):
        (out / "CNAME").write_text(cfg.custom_domain + "\n", encoding="utf-8")

    # Last: the operator's analytics and ad tags (config.yaml tracking:) go
    # into every page written above or by another pass, nested ones included.
    apply_tracking(out, cfg)

    logger.info("site rendered: %d deals (%d locked) -> %s",
                meta["count"], locked_count, out)
    return {"published": len(serialized) + len(spotlight_only), "out_dir": str(out),
            "link_mix": link_mix(teaser + spotlight_only, amazon_tag,
                                 competitor_tracker_hosts=cfg.affiliate_competitor_tracker_hosts,
                                 ebay_campid=cfg.affiliate_ebay_campaign_id),
            "full_payload": full_doc, "health": health_doc}
