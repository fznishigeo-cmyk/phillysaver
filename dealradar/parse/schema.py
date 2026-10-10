"""LLM-facing Pydantic schema mirroring models.ExtractedDeal, plus prompts.

The LLM never does arithmetic: it copies numbers exactly as written and backs
every price with a short verbatim quote. ``to_extracted`` maps the validated
LLM output onto the frozen ``models.ExtractedDeal`` contract.

Cycle note: ``to_extracted`` resolves ``expires_at_raw`` with
``extractor.resolve_expires``; the import is done lazily inside the function
because ``extractor`` imports this module at load time.
"""

from __future__ import annotations

import logging
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from ..models import Evidence, ExtractedDeal, RawPost

log = logging.getLogger("dealradar.parse.schema")

CATEGORIES = (
    "electronics", "computers", "home", "kitchen", "kids_baby", "toys_games",
    "fashion", "beauty", "health", "grocery", "pets", "outdoors", "sports",
    "office", "auto", "books_media", "gaming", "other",
)

DEAL_TYPES = ("", "bogo", "b2g1", "b2g3", "b1g1_50", "free_after_rebate",
              "moneymaker", "penny")


def _drop_null_keys(data):
    """Drop null-valued keys so field defaults apply. Real models answer the
    JSON fallback with null for unknown optional fields; a null must mean
    'use the default', never a validation failure that wastes the post."""
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if v is not None}
    return data


class LLMEvidence(BaseModel):
    """Exact verbatim quotes backing each extracted price field."""

    regular_price_quote: str = ""
    final_price_quote: str = ""
    pct_quote: str = ""
    price_upper_bound_quote: str = ""

    _drop_nulls = model_validator(mode="before")(_drop_null_keys)


class LLMDeal(BaseModel):
    """One deal as extracted by the LLM. Mirrors models.ExtractedDeal 1:1.

    Deliberately SIMPLE in structure: plain strings with normalizing
    validators instead of Literal enums. Two hard-won reasons:
    - the structured-output API rejects big enum-heavy schemas outright
      ("Schema is too complex"), knocking every post onto the fallback path;
    - on the JSON fallback, real models send null for unknown fields and
      "$6.25" for prices; strictness there throws away good deals.
    Validators normalize, they never invent: out-of-vocabulary values
    collapse to safe defaults, price strings are PARSED (no arithmetic),
    and evidence quotes are still verified verbatim downstream.
    """

    item: str = Field(min_length=1)
    store: str = Field(min_length=1)
    brand: str = ""
    product_url: str = ""
    online_only: str = "unknown"          # true | false | unknown
    regular_price: float | None = None
    final_price: float | None = None
    pct_off: float | None = None
    abs_savings: float | None = None
    shipping_cost_or_threshold: str = ""
    stack_steps: list[str] = Field(default_factory=list)
    codes: list[str] = Field(default_factory=list)
    rebates: list[str] = Field(default_factory=list)
    deferred_value: str = ""
    conditions: list[str] = Field(default_factory=list)
    expires_at_raw: str = ""
    quantity_or_unit: str = ""
    regular_price_unit: str = ""          # per_unit | pack | ""
    final_price_unit: str = ""            # per_unit | pack | ""
    currency: str = "USD"
    region: str = "US"                    # US | other
    category: str = "other"               # one of CATEGORIES
    evidence: LLMEvidence = Field(default_factory=LLMEvidence)
    confidence: float = 0.0
    deal_type: str = ""                   # one of DEAL_TYPES
    price_is_range: bool = False
    price_upper_bound: float | None = None

    _drop_nulls = model_validator(mode="before")(_drop_null_keys)

    @field_validator("regular_price", "final_price", "pct_off", "abs_savings",
                     "price_upper_bound", mode="before")
    @classmethod
    def _tolerant_number(cls, v):
        """'$6.25' / '70%' / '1,299.00' -> float. Parsing, never arithmetic;
        anything that is not one plain number means 'no value'."""
        if isinstance(v, str):
            s = v.strip().lstrip("$").replace(",", "").rstrip("%").strip()
            if not s:
                return None
            try:
                return float(s)
            except ValueError:
                return None
        return v

    @field_validator("online_only")
    @classmethod
    def _norm_online_only(cls, v: str) -> str:
        v = v.strip().lower()
        return v if v in ("true", "false") else "unknown"

    @field_validator("category")
    @classmethod
    def _norm_category(cls, v: str) -> str:
        v = v.strip().lower().replace(" ", "_").replace("&", "and")
        return v if v in CATEGORIES else "other"

    @field_validator("deal_type")
    @classmethod
    def _norm_deal_type(cls, v: str) -> str:
        v = v.strip().lower()
        return v if v in DEAL_TYPES else ""

    @field_validator("regular_price_unit", "final_price_unit")
    @classmethod
    def _norm_price_unit(cls, v: str) -> str:
        v = v.strip().lower()
        return v if v in ("per_unit", "pack") else ""

    @field_validator("region")
    @classmethod
    def _norm_region(cls, v: str) -> str:
        return "US" if v.strip().lower() in ("us", "usa", "united states") \
            else "other"


class LLMDeals(BaseModel):
    """Top-level structured output: a list of deals (possibly empty)."""

    deals: list[LLMDeal] = Field(default_factory=list)


SYSTEM_PROMPT = """\
You are a precise deal-extraction engine. You extract facts from one online-deal
post. Extraction only — you NEVER do arithmetic, unit conversion, or rounding:
copy every number exactly as written in the post.

Rules:
- If the post is a roundup covering several distinct deals, split it into one
  entry per distinct deal. A post with no concrete deal yields an empty list.
- EVIDENCE IS MANDATORY. Every price field you fill MUST be backed by a short
  EXACT verbatim quote copied character-for-character from the post:
  regular_price -> evidence.regular_price_quote,
  final_price -> evidence.final_price_quote,
  pct_off -> evidence.pct_quote.
  If you cannot quote it, leave the price null. Quotes are checked verbatim
  against the post; a quote that is not in the post gets the deal rejected.
- conditions: use only this vocabulary, zero or more of: new_customer,
  prime_only, targeted, subscribe_save, in_cart, limited_qty, pricing_error,
  in_store_only, ymmv, clearance_aisle, multi_buy, used, open_box.
- Used / open-box / refurbished / warehouse wording (e.g. Amazon Warehouse,
  Amazon Resale): add condition 'used' (and 'open_box' when stated). These
  are honest deals when the post states the new-condition price — extract
  the used price as final_price and the stated new price as regular_price,
  each with its quote.
- online_only: 'false' when the wording is in-store (clearance aisle, YMMV,
  'my store', shelf or tag photos); 'true' only when the deal is clearly
  orderable online; otherwise 'unknown'.
- product_url: the direct link to buy the item. Links in the post appear as
  'anchor text (https://...)'; copy the URL of the link that points at the
  product or store page. Empty when the post has none.
- expires_at_raw: copy the expiry wording exactly as written ('ends today',
  'through 10/12', an ISO date) WITHOUT interpreting it. Empty if none.
- deal_type: one of '', 'bogo', 'b2g1', 'b2g3', 'b1g1_50', 'free_after_rebate',
  'moneymaker', 'penny' — tag from the wording only.
- 'as low as $X' and price ranges: set price_is_range=true and
  price_upper_bound to the highest price mentioned, backed by its own exact
  verbatim quote in evidence.price_upper_bound_quote (no quote -> leave
  price_upper_bound null).
- region: 'US' only when the deal is orderable in the United States; any
  other market -> 'other'.
- Units: regular_price_unit / final_price_unit are 'per_unit' when a price is
  per item/each, 'pack' when it covers a multi-pack bundle, '' when unstated.
- currency: as stated in the post; default 'USD'.
- stack_steps: the stacking steps in the order the post states them. Amazon
  promo wording counts as steps too: 'clip the $X coupon' / 'clip X% coupon'
  (a coupon step), 'Subscribe & Save X%' (a percent step; also add condition
  subscribe_save), and sale prices. Multi-buy promos like 'save $X on 2
  select items' or 'X% off when you buy 2' apply only when buying multiples:
  record them as stack steps AND add the condition multi_buy.
- codes / rebates: promo codes and rebates exactly as written.
- category: the single best fit from electronics, computers, home, kitchen,
  kids_baby, toys_games, fashion, beauty, health, grocery, pets, outdoors,
  sports, office, auto, books_media, gaming, other.
- confidence: your 0-1 confidence that this is a real, correctly copied deal.
"""

class CriticVerdict(BaseModel):
    """Deal-critic structured output: is the claimed regular price honest?"""

    plausible: bool
    confidence: float = 0.0
    reason: str = ""


CRITIC_SYSTEM_PROMPT = """\
You are a skeptical deal reviewer. You are given one deal: an item name, a
store, a claimed regular price, and a sale price. Judge ONE question: is the
claimed regular price a plausible real-world street price for THIS EXACT
item — or an inflated number (fake MSRP, or a price-tracker average built
from sparse/manipulated listing history) that fabricates a huge discount?

Rules:
- Judge the claimed regular price for the EXACT product named (brand AND
  model/part number/size), not for the brand's prestige. A premium brand
  does not make any number plausible: a $460 "regular" for one ordinary
  brake rotor part number is implausible even if that brand also sells
  $400 flagship rotors.
- Generic/no-name items with regular prices far above what such items sell
  for anywhere -> implausible.
- THE SIZE OF THE DISCOUNT IS NEVER EVIDENCE BY ITSELF, in either
  direction. Real price errors, deep clearance and open-box deals run to
  90%+ off, and those are exactly the deals this radar exists to catch.
  Judge only whether the claimed REGULAR price matches what the exact
  product genuinely sells for new: implausible means the regular price
  itself is inflated (a $460 "regular" for a part number that sells around
  $40-80 new) — never that the gap between regular and sale is huge.
- A used/open-box deal's regular price is the product's NEW-condition
  price; judge that new price's realism, not the depth of the used
  discount.
- Name brands at their usual list prices -> plausible, even when the
  discount is deep (real clearance and real price errors happen).
- When genuinely unsure, lean plausible with low confidence; only mark
  implausible when the inflation pattern is clear.
"""


def build_user_prompt(text: str, meta: dict) -> str:
    """Build the user prompt: source metadata plus the fenced cleaned post text."""
    return (
        f"Source: {meta.get('source_name', '')}\n"
        f"URL: {meta.get('source_url', '')}\n"
        f"Title: {meta.get('title', '')}\n\n"
        "Extract every distinct deal from the post between the markers.\n"
        "BEGIN POST\n"
        f"{text}\n"
        "END POST"
    )


def to_extracted(d: LLMDeal, post: RawPost, tz_name: str, now: datetime) -> ExtractedDeal:
    """Map a validated LLMDeal onto the frozen ExtractedDeal contract.

    ``resolve_expires`` is imported lazily from ``extractor`` to avoid an
    import cycle (extractor imports this module at load time).
    """
    from .extractor import resolve_expires  # lazy: avoids import cycle

    return ExtractedDeal(
        item=d.item,
        store=d.store,
        source_url=post.url,
        source_name=post.source_name,
        brand=d.brand,
        product_url=d.product_url,
        online_only=d.online_only,
        regular_price=d.regular_price,
        final_price=d.final_price,
        pct_off=d.pct_off,
        abs_savings=d.abs_savings,
        shipping_cost_or_threshold=d.shipping_cost_or_threshold,
        stack_steps=list(d.stack_steps),
        codes=list(d.codes),
        rebates=list(d.rebates),
        deferred_value=d.deferred_value,
        conditions=list(d.conditions),
        expires_at=resolve_expires(d.expires_at_raw, tz_name, now),
        quantity_or_unit=d.quantity_or_unit,
        category=d.category,
        regular_price_unit=d.regular_price_unit,
        final_price_unit=d.final_price_unit,
        region=d.region,
        currency=d.currency,
        evidence=Evidence(
            regular_price_quote=d.evidence.regular_price_quote,
            final_price_quote=d.evidence.final_price_quote,
            pct_quote=d.evidence.pct_quote,
            price_upper_bound_quote=d.evidence.price_upper_bound_quote,
        ),
        confidence=d.confidence,
        deal_type=d.deal_type,
        price_is_range=d.price_is_range,
        price_upper_bound=d.price_upper_bound,
    )
