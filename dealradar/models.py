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
    method: str  # "rss" | "api" | "html"
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
    # Product link as posted -> the photo next to it in the post (roundups:
    # one photo per product). Image-granted sources only; transient like
    # `text`, never persisted. Values are hotlinked, never copied or stored.
    product_images: dict[str, str] = field(default_factory=dict)
    # Distinct products the parse found in this post (set by the pipeline
    # before upsert; 0 = not counted). Two or more: a roundup, whose first
    # image is never lent to a deal it was not paired with. Transient.
    products: int = 0

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
    # post's photos are keyed by (RawPost.product_images). Never persisted.
    link_as_posted: str = ""


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
    asin: str = ""
    image_url: str = ""                   # retailer's public product image URL
    final_price: float | None = None
    regular_price: float | None = None
    pct_at_checkout: float | None = None
    pct_with_rebates: float | None = None
    abs_savings: float | None = None
    basis: str = ""
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
    # ISO UTC of the last live price re-check (Amazon deals, via Keepa).
    price_rechecked_at: str = ""
    keepa_checked: bool = False           # the one-time Keepa cross-check ran
    # ISO UTC of the moment status flipped to 'dead' (site shows a dead deal
    # only briefly, as "just missed it" proof, then drops it).
    died_at: str = ""
    # ISO UTC of a later price drop or revival; the recheck kills a deal
    # only on Keepa data newer than this and first_seen_at.
    price_since: str = ""
    # ISO UTC of the last Keepa recheck that ANSWERED without a verdict
    # (inconclusive offers scan, stale data): the deal waits a retry window
    # instead of being re-asked, and paid for, on every run.
    recheck_tried_at: str = ""
    first_seen_at: str = ""               # ISO UTC
    last_seen_at: str = ""
    confidence: float = 0.0
    sightings: list[dict[str, Any]] = field(default_factory=list)
    # each sighting: {source_name, source_url, seen_at, final_price}


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
