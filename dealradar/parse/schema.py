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
from typing import Literal

from pydantic import BaseModel, Field

from ..models import Evidence, ExtractedDeal, RawPost

log = logging.getLogger("dealradar.parse.schema")


class LLMEvidence(BaseModel):
    """Exact verbatim quotes backing each extracted price field."""

    regular_price_quote: str = ""
    final_price_quote: str = ""
    pct_quote: str = ""


class LLMDeal(BaseModel):
    """One deal as extracted by the LLM. Mirrors models.ExtractedDeal 1:1."""

    item: str = Field(min_length=1)
    store: str = Field(min_length=1)
    brand: str = ""
    product_url: str = ""
    online_only: Literal["true", "false", "unknown"] = "unknown"
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
    regular_price_unit: Literal["per_unit", "pack", ""] = ""
    final_price_unit: Literal["per_unit", "pack", ""] = ""
    currency: str = "USD"
    evidence: LLMEvidence = Field(default_factory=LLMEvidence)
    confidence: float = 0.0
    deal_type: Literal[
        "", "bogo", "b2g1", "b2g3", "b1g1_50",
        "free_after_rebate", "moneymaker", "penny",
    ] = ""
    price_is_range: bool = False
    price_upper_bound: float | None = None


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
  in_store_only, ymmv, clearance_aisle.
- online_only: 'false' when the wording is in-store (clearance aisle, YMMV,
  'my store', shelf or tag photos); 'true' only when the deal is clearly
  orderable online; otherwise 'unknown'.
- expires_at_raw: copy the expiry wording exactly as written ('ends today',
  'through 10/12', an ISO date) WITHOUT interpreting it. Empty if none.
- deal_type: one of '', 'bogo', 'b2g1', 'b2g3', 'b1g1_50', 'free_after_rebate',
  'moneymaker', 'penny' — tag from the wording only.
- 'as low as $X' and price ranges: set price_is_range=true and
  price_upper_bound to the highest price mentioned.
- Units: regular_price_unit / final_price_unit are 'per_unit' when a price is
  per item/each, 'pack' when it covers a multi-pack bundle, '' when unstated.
- currency: as stated in the post; default 'USD'.
- stack_steps: the stacking steps in the order the post states them.
- codes / rebates: promo codes and rebates exactly as written.
- confidence: your 0-1 confidence that this is a real, correctly copied deal.
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
        regular_price_unit=d.regular_price_unit,
        final_price_unit=d.final_price_unit,
        region="US",
        currency=d.currency,
        evidence=Evidence(
            regular_price_quote=d.evidence.regular_price_quote,
            final_price_quote=d.evidence.final_price_quote,
            pct_quote=d.evidence.pct_quote,
        ),
        confidence=d.confidence,
        deal_type=d.deal_type,
        price_is_range=d.price_is_range,
        price_upper_bound=d.price_upper_bound,
    )
