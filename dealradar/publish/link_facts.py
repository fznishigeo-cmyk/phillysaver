"""Pure identity, credit and Get deal routing; no price or publish eligibility.

The caller resolves source/affiliate policy before deriving facts. Only a single
render may reuse them; neither deal keys nor observations are a cache lifetime.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import parse_qsl, quote, unquote_plus, urlencode, urlsplit, urlunsplit

from ..source_policy import host_of
from .credits import source_sightings


@dataclass(frozen=True, slots=True)
class LinkPolicy:
    amazon_tag: str = ""
    overrides: Mapping[str, str] = field(default_factory=dict)
    sovrn_key: str = ""
    sovrn_enabled: bool = False
    competitor_tracker_hosts: tuple[str, ...] = ()
    repair_competitor_trackers: bool = False
    exact_variant_links: bool = False
    ebay_campid: str = ""
    source_names: Mapping[str, str] = field(default_factory=dict)
    source_hosts: frozenset[str] = frozenset()

    def __post_init__(self):
        # Own immutable copies, including when callers supplied mutable config.
        object.__setattr__(self, "overrides", MappingProxyType(dict(self.overrides)))
        object.__setattr__(self, "source_names", MappingProxyType(dict(self.source_names)))
        object.__setattr__(self, "competitor_tracker_hosts", tuple(self.competitor_tracker_hosts))
        object.__setattr__(self, "source_hosts", frozenset(self.source_hosts))


@dataclass(frozen=True, slots=True)
class LinkFacts:
    key: str
    credits: tuple[tuple[str, str], ...]
    needs_steps: bool
    simple_code: bool
    url: str
    earns: bool


def routing_policy(*args, repair_competitor_trackers=False, exact_variant_links=False,
                   **kwargs) -> LinkPolicy:
    """Add routing policy without changing the credit renderer's version bytes."""
    from .credits import _deal_source_hosts, link_policy

    return replace(link_policy(*args, **kwargs),
                   repair_competitor_trackers=repair_competitor_trackers,
                   exact_variant_links=exact_variant_links,
                   source_hosts=_deal_source_hosts())


# Stores whose own feed is a source: their "post" is the product page itself
# (the same list credits._deal_source_hosts excludes; credits.py bytes version
# the credit renderer, so it keeps its own copy).
RETAILER_SOURCE_HOSTS = frozenset({"woot.com", "meh.com", "morningsave.com", "sidedeal.com",
                                   "dealgenius.com", "dailysteals.com", "tanga.com", "1sale.com"})
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


def _credit(sighting: dict[str, Any], source_names: Mapping[str, str]) -> dict[str, str]:
    """Public credit, including stored sightings from before the rename."""
    name = sighting.get("source_name", "")
    url = sighting.get("source_url", "")
    host = (urlsplit(url or "").hostname or "").lower()
    if name.lower().startswith("keepa") or host == "keepa.com" or host.endswith(".keepa.com"):
        return {"name": "Amazon price drop", "url": ""}
    return {"name": source_names.get(name, name), "url": url}


def rewrite_affiliate(url: str, amazon_tag: str = "", *, sovrn_key: str = "",
                      sovrn_enabled: bool = False, cuid: str = "",
                      credit_urls: list[str] | None = None,
                      competitor_tracker_hosts: list[str] | tuple[str, ...] = (),
                      ebay_campid: str = "", source_hosts: frozenset[str] = frozenset()) -> str:
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
    excluded = (set(source_hosts) | _SOVRN_HOSTS | set(competitor_tracker_hosts)
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


def routing_key(deal: dict[str, Any]) -> tuple:
    """All routing inputs; labels, money, clocks and rank are deliberately absent.

    The policy is separately frozen for one render. Include raw sighting order:
    duplicate feeds can still change which first post receives public credit.
    """
    return (str(deal.get("dedupe_key", "")), deal.get("canonical_url") or "",
            deal.get("link_as_posted") or "", deal.get("asin") or "", deal.get("store") or "",
            tuple((s.get("source_name", ""), s.get("source_url", ""))
                  for s in deal.get("sightings") or []),
            bool(deal.get("codes")), tuple(str(c) for c in deal.get("codes") or []),
            tuple(str(s) for s in deal.get("stack_steps") or []),
            tuple(deal.get("conditions") or []), bool(deal.get("rebates")),
            "rebate_dependent" in (deal.get("flags") or []), _variant_asin(deal))


def _variant_asin(deal: dict[str, Any]) -> str:
    """The deal's ASIN when Keepa read it as one size/colour of a parent listing."""
    check = deal.get("click_price_check") or {}
    asin = str(deal.get("asin") or "").upper()
    return asin if (re.fullmatch(r"[A-Z0-9]{10}", asin) and check.get("variation_child")
                    and str(check.get("asin") or "").upper() == asin) else ""


def exact_variant(url: str, deal: dict[str, Any]) -> str:
    """Open a size/colour child as itself, not the shopper's last-viewed size.

    th=1&psc=1 is the form Amazon's own affiliate links use for a variation;
    the tag is added after it as usual. Operator 2026-10-21 (shoe at $5.91
    opened in her saved size at $15.23).
    """
    asin = _variant_asin(deal)
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if (not asin or parts.scheme != "https" or (parts.hostname or "").lower() not in _AMAZON_HOSTS
            or not re.search(rf"/(?:dp|gp/product)/{asin}(?:/|$)", parts.path, re.IGNORECASE)):
        return url
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in {"th", "psc"}]
    return urlunsplit(parts._replace(query=urlencode([("th", "1"), ("psc", "1"), *query])))


def derive_link_facts(deal: dict[str, Any], policy: LinkPolicy) -> LinkFacts:
    """Derive only pure link facts, preserving preliminary versus final earns."""
    credits = []
    for credit in (_credit(s, policy.source_names) for s in source_sightings(deal)):
        if all(credit["name"] != c["name"] for c in credits):
            credits.append(credit)
    canon = deal.get("canonical_url") or ""
    posted = deal.get("link_as_posted") or ""
    protected_link = next((u for u in (posted, canon) if u and
                           is_competitor_tracker_host(host_of(u), policy.competitor_tracker_hosts)), "")
    protected = bool(protected_link) and not policy.repair_competitor_trackers
    if protected:
        canon = protected_link
    if policy.repair_competitor_trackers:
        # Operator 2026-10-20: an Amazon item never opens the source's own post.
        source_urls = {s.get("source_url") for s in deal.get("sightings") or []}
        source_link = canon in source_urls or is_competitor_tracker_host(
            host_of(canon), tuple(policy.source_hosts) + policy.competitor_tracker_hosts)
        asin = str(deal.get("asin") or "").upper()
        if not protected and (not canon or source_link) and re.fullmatch(r"[A-Z0-9]{10}", asin):
            canon = f"https://www.amazon.com/dp/{asin}"
    if policy.exact_variant_links and not protected:
        canon = exact_variant(canon, deal)
    post = next((c["url"] for c in credits
                 if str(c["url"]).startswith(("https://", "http://"))), "")
    steps = _needs_steps(deal)
    simple = _simple_code_deal(deal)
    key = hashlib.sha256(str(deal.get("dedupe_key", "")).encode("utf-8")).hexdigest()[:16]
    store_link = rewrite_affiliate(canon, policy.amazon_tag, sovrn_key=policy.sovrn_key,
                                   sovrn_enabled=policy.sovrn_enabled, cuid=key,
                                   credit_urls=[c["url"] for c in credits],
                                   competitor_tracker_hosts=policy.competitor_tracker_hosts,
                                   ebay_campid=policy.ebay_campid,
                                   source_hosts=policy.source_hosts) if canon else ""
    override = policy.overrides.get(_override_key(canon)) if canon else None
    try:
        host = (urlsplit(canon).hostname or "").lower() if canon.startswith("https://") else ""
    except ValueError:
        host = ""
    if override and not protected:
        store_link = override
    earning = earning_link({"url": store_link, "credits": credits}, policy.amazon_tag,
                           competitor_tracker_hosts=policy.competitor_tracker_hosts,
                           ebay_campid=policy.ebay_campid)
    # Tagged Amazon always routes to the store, including complex checkout
    # steps; other earning stores do so only for a simple listed code.
    amazon = bool(policy.amazon_tag and host in _AMAZON_HOSTS)
    ours = amazon or (earning and simple)
    url = post if steps and post and not ours else \
        store_link if canon else (credits[0]["url"] if credits else "")
    if protected:
        url = canon
    earns = earning_link({"url": url, "credits": credits}, policy.amazon_tag,
                         competitor_tracker_hosts=policy.competitor_tracker_hosts,
                         ebay_campid=policy.ebay_campid)
    return LinkFacts(key, tuple((c["name"], c["url"]) for c in credits), steps, simple, url, earns)
