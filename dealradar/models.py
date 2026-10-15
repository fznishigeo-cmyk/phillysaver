"""Shared dataclasses: the contracts between fetch, parse, savings, and publish layers.

Storage policy (non-negotiable): we store extracted facts (prices, store, codes,
product URL, posted time), a link, and credit to the source. RawPost.text is held
in memory for the duration of one pipeline run only and is NEVER written to the
database or any published output.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SourceConfig:
    name: str
    method: str  # "rss" | "api" | "html" | "email"
    url: str
    region: str = "US"
    allowed_html: bool = False
    fast_lane: bool = False
    adapter: str = ""  # for method="api": reddit|woot|meh|telegram|threads|instagram
    enabled: bool = True
    tz: str = "America/New_York"  # tz assumed for naive feed dates
    notes: str = ""
    # Non-empty = the operator holds WRITTEN permission from this site to
    # crawl it (keep the correspondence on file; this string documents it).
    # Bypasses robots.txt for THIS SOURCE'S OWN HOST ONLY — never other
    # hosts, never bot walls, and every other politeness rule still applies.
    crawl_permission: str = ""
    # True ONLY when the written grant above explicitly covers displaying the
    # source's own deal images with credit. Images are hotlinked from the
    # source, never copied or stored.
    image_permission: bool = False
    display_name: str = ""
    site: str = ""
    newsletter_from: list[str] = field(default_factory=list)  # optional partner sender addresses


@dataclass
class PostPhotos:
    """What a post's HTML shows (fetch.rss.post_photos; image-granted
    sources only). Transient like RawPost.text, never persisted; the URLs
    are hotlinked, never copied or stored."""

    # Product link as posted -> the photo inside that link (<a href=X><img>).
    in_link: dict[str, str] = field(default_factory=dict)
    # Photo -> the words naming it (alt, title, figcaption, file name),
    # normalized (product_images.name_words).
    named: dict[str, frozenset[str]] = field(default_factory=dict)
    # Distinct product links (off-site, or cloaked on the blog's own host),
    # and distinct photos besides the post's featured ones: more than one of
    # either is more than one product.
    links: set[str] = field(default_factory=set)
    images: set[str] = field(default_factory=set)
    # The post's featured photos (product_images.photo_key): og:image, its
    # first image, any named after the post. No product's on a roundup.
    featured: set[str] = field(default_factory=set)
    # Every product the page names, as (words, product_images.link_key or
    # ''): product-ish headings and link texts. Competing names a photo is
    # checked against, so an unparsed product's photo is not lent out.
    names: set[tuple[frozenset[str], str]] = field(default_factory=set)


@dataclass
class RawPost:
    """One post/item from a source. `text` is transient (never persisted)."""

    source_name: str
    uid: str                      # stable per-source id (guid, reddit fullname, msg id)
    url: str                      # the source's own post URL (kept for credit)
    title: str
    text: str                     # cleaned plain text (title + summary/body)
    published_at: datetime | None # tz-aware UTC, defensively parsed
    fetched_at: datetime          # tz-aware UTC
    image_url: str = ""           # retailer's own public product-image URL
                                  # (e.g. Amazon's media CDN via Keepa), or a
                                  # source-blog image ONLY for sources whose
                                  # written grant covers it (image_permission).
                                  # Always hotlinked, never copied or stored.
    hash_text: str = ""           # stable text for content_hash; adapters set it
                                  # to the PRE-enrichment text so a flaky article
                                  # fetch can't make the same post hash differently
                                  # between runs. Empty -> `text` is used.
    # The photos the post shows (roundups: one per product). Image-granted
    # sources only; transient like `text`, never persisted.
    photos: PostPhotos = field(default_factory=PostPhotos)
    # (item, product link as posted) of EVERY deal the parse read in this
    # post, before evidence checks drop any: the names a photo is matched
    # against, the holders of a shared link. Set by parse_posts; transient.
    parsed: list[tuple[str, str]] = field(default_factory=list)
    structured: dict | None = None  # API facts + quotes; transient like text
    via_email: bool = False        # never fetch this newsletter's partner pages
    price_recheck: bool = False    # current permitted post/feed read, bypasses seen hash

    @property
    def content_hash(self) -> str:
        basis = self.hash_text or self.text
        norm = " ".join(basis.split()).lower()
        raw = f"{self.source_name}\n{self.uid}\n{norm}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class Evidence:
    """Exact quotes from the source text backing every extracted price.

    The code verifies each non-empty quote is present verbatim in the text that
    was given to the model; otherwise the deal is dropped.
    """

    regular_price_quote: str = ""
    final_price_quote: str = ""
    pct_quote: str = ""
    price_upper_bound_quote: str = ""  # required when price_is_range
    shipping_quote: str = ""
    deal_type_quote: str = ""


@dataclass
class ExtractedDeal:
    """Raw facts extracted by the LLM from one post. No math is done by the LLM."""

    item: str
    store: str
    source_url: str
    source_name: str
    brand: str = ""
    product_url: str = ""
    online_only: str = "unknown"          # "true" | "false" | "unknown"
    regular_price: float | None = None
    final_price: float | None = None
    pct_off: float | None = None          # only if the source states "X% off"
    abs_savings: float | None = None
    shipping_cost_or_threshold: str = ""  # e.g. "5.99" or "free over $35" or ""
    stack_steps: list[str] = field(default_factory=list)  # in stated order
    codes: list[str] = field(default_factory=list)
    rebates: list[str] = field(default_factory=list)
    deferred_value: str = ""              # gift card back / portal cashback / store credit
    conditions: list[str] = field(default_factory=list)
    expires_at: str = ""                  # ISO datetime or "" (code resolves "ends today")
    quantity_or_unit: str = ""            # e.g. "2-pack", "per unit", "12 oz"
    regular_price_unit: str = ""          # "per_unit" | "pack" | "" (as stated)
    final_price_unit: str = ""            # "per_unit" | "pack" | "" (as stated)
    region: str = "US"
    currency: str = "USD"
    category: str = "other"               # electronics|home|kitchen|kids_baby|...
    evidence: Evidence = field(default_factory=Evidence)
    confidence: float = 0.0
    # Deal-type markers the LLM tags from wording; savings math interprets them.
    deal_type: str = ""                   # "", "bogo", "b2g1", "b2g3", "b1g1_50",
                                          # "free_after_rebate", "moneymaker", "penny"
    price_is_range: bool = False          # "as low as $X" / price ranges
    price_upper_bound: float | None = None
    # The product link exactly as the post gave it, kept by the pipeline
    # before product_url is resolved to the retailer URL; it is what the
    # post's photos are keyed by (RawPost.photos.in_link). Never persisted.
    link_as_posted: str = ""
    # Set by the pipeline: two or more different products of this post hold
    # this deal's link (a store or sale page). It names none of them; the
    # deal row keeps the fact as Deal.link_shared.
    link_shared: bool = False

    def __post_init__(self) -> None:
        from .conditions import normalize_conditions

        self.conditions = normalize_conditions(self.conditions)


@dataclass
class SavingsResult:
    """Output of the pure-code savings engine (see SAVINGS_RULES.md)."""

    computable: bool
    final_price: float | None = None      # shipping-adjusted price actually paid at checkout
    regular_price: float | None = None
    pct_at_checkout: float | None = None  # ranking/publishing uses this
    pct_with_rebates: float | None = None # shown as bonus only
    abs_savings: float | None = None
    basis: str = ""                       # "quoted_regular" | "list_price_claim" | "bogo" |
                                          # "free_after_rebate" | "stated_pct" | ...
    flags: list[str] = field(default_factory=list)
    # flags: "range", "unknown_shipping", "rebate_dependent", "per_unit", ...
    exclude_reason: str = ""              # non-empty => never published


@dataclass
class Deal:
    """A deduplicated deal as stored/published. One row in `deals`."""

    dedupe_key: str
    store: str
    item: str
    brand: str = ""
    canonical_url: str = ""
    # The product link as the post gave it (often an affiliate redirect):
    # the post's photos are keyed by it (photo backfill, in-link evidence).
    link_as_posted: str = ""
    asin: str = ""
    image_url: str = ""                   # retailer's public product image URL
    final_price: float | None = None
    regular_price: float | None = None
    pct_at_checkout: float | None = None
    pct_with_rebates: float | None = None
    abs_savings: float | None = None
    basis: str = ""
    regular_price_basis: str = ""          # API reference price used by a reprice
    flags: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    stack_steps: list[str] = field(default_factory=list)
    rebates: list[str] = field(default_factory=list)
    deferred_value: str = ""
    conditions: list[str] = field(default_factory=list)
    online_only: str = "unknown"
    shipping_cost_or_threshold: str = ""
    quantity_or_unit: str = ""
    region: str = "US"
    currency: str = "USD"
    category: str = "other"               # electronics|home|kitchen|kids_baby|...
    expires_at: str = ""
    verify_tier: str = "unverified"       # corroborated | history-checked | unverified
    status: str = "alive"                 # alive | dead
    # Deal-critic verdict {plausible, confidence, reason} or None (not judged).
    # Implausible => 'suspect_msrp' in flags => never published (configurable).
    critic: dict[str, Any] | None = None
    audit: dict[str, Any] | None = None  # code verdict, bound to the audited facts
    audit_reference: dict[str, Any] | None = None  # licensed Keepa price basis
    # ISO UTC of the last live price re-check (Keepa, Best Buy or eBay).
    price_rechecked_at: str = ""
    buybox_check: dict[str, Any] | None = None  # private click-price facts, bound to this price
    woot_listed_at: str = ""              # Woot's feed still lists it at <= this price
    source_recheck_tried_at: str = ""
    source_recheck_run: str = ""
    store_recheck_run: str = ""
    keepa_price_seen_at: str = ""         # a backed current price from the Keepa lane
    keepa_warehouse_seen_at: str = ""     # exact used price in the Warehouse lane
    keepa_checked: bool = False           # the one-time Keepa cross-check ran
    # ISO UTC of the moment status flipped to 'dead' (site shows a dead deal
    # only briefly, as "just missed it" proof, then drops it).
    died_at: str = ""
    # ISO UTC of a later price drop or revival; the recheck kills a deal
    # only on Keepa data newer than this and first_seen_at.
    price_since: str = ""
    price_published_at: str = ""
    online_only_published_at: str = ""
    expires_published_at: str = ""
    # ISO UTC of the last Keepa recheck that ANSWERED without a verdict
    # (inconclusive offers scan, stale data): the deal waits a retry window
    # instead of being re-asked, and paid for, on every run.
    recheck_tried_at: str = ""
    # ISO UTC of a suspected end (post/expiry, or a gone API reading).
    # death holds post_says_over/stated_expiry until the first API reading.
    # For API readings:
    # one reading is not enough to kill a deal; a second, at least
    # _CONFIRM_GONE_H later, is (live: Keepa missed a Warehouse offer at
    # 01:23 that its deals feed listed again, same price, at 07:01).
    gone_suspect_at: str = ""
    # Why a dead deal died: "confirmed" (two live checks), "api_ended" (the
    # store's own API says it ended), "stated_expiry" (a post's end date
    # passed) or "post_says_over". Only the first two are shown as GONE.
    death: str = ""
    # The deal's link is one several products of a post or article shared
    # (a store or sale page): it names no product, so no photo is ever
    # matched to it — not the post's, the article's or the retailer's.
    link_shared: bool = False
    first_seen_at: str = ""               # ISO UTC
    last_seen_at: str = ""
    confidence: float = 0.0
    sightings: list[dict[str, Any]] = field(default_factory=list)
    # each sighting: {source_name, source_url, seen_at, final_price}
    # Publication time of the sighting supplying each explicit merge value.
    online_only_published_at: str = ""
    expires_published_at: str = ""


@dataclass
class FetchResult:
    """Result of one polite HTTP fetch."""

    url: str
    final_url: str
    status: int                 # HTTP status; 0 = transport error; 304 = not modified
    body: bytes | None
    error: str = ""
    not_modified: bool = False  # true on 304 (cached validators matched)
    blocked_by_robots: bool = False
