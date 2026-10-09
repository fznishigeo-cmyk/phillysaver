"""Tests for dealradar.parse.schema: LLM schema, prompts, and mapping."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from dealradar import util
from dealradar.models import RawPost
from dealradar.parse.schema import (
    SYSTEM_PROMPT,
    LLMDeal,
    LLMDeals,
    LLMEvidence,
    build_user_prompt,
    to_extracted,
)

NOW = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)
TZ = "America/New_York"


def make_post(**over) -> RawPost:
    kw = dict(
        source_name="hip2save",
        uid="p1",
        url="https://hip2save.com/deal/1",
        title="Big deal",
        text="Widget was $20.00 now $10.00",
        published_at=NOW,
        fetched_at=NOW,
    )
    kw.update(over)
    return RawPost(**kw)


class TestLLMModels:
    def test_minimal_deal_defaults(self):
        d = LLMDeal(item="Widget", store="Amazon")
        assert d.brand == ""
        assert d.online_only == "unknown"
        assert d.regular_price is None
        assert d.final_price is None
        assert d.pct_off is None
        assert d.abs_savings is None
        assert d.stack_steps == []
        assert d.codes == []
        assert d.rebates == []
        assert d.conditions == []
        assert d.expires_at_raw == ""
        assert d.regular_price_unit == ""
        assert d.final_price_unit == ""
        assert d.currency == "USD"
        assert d.evidence == LLMEvidence()
        assert d.confidence == 0.0
        assert d.deal_type == ""
        assert d.price_is_range is False
        assert d.price_upper_bound is None

    def test_item_and_store_required_nonempty(self):
        with pytest.raises(ValidationError):
            LLMDeal(item="", store="Amazon")
        with pytest.raises(ValidationError):
            LLMDeal(item="Widget", store="")
        with pytest.raises(ValidationError):
            LLMDeal(store="Amazon")  # item missing entirely

    def test_literal_fields_rejected_outside_vocabulary(self):
        with pytest.raises(ValidationError):
            LLMDeal(item="x", store="y", online_only="maybe")
        with pytest.raises(ValidationError):
            LLMDeal(item="x", store="y", deal_type="flash_sale")
        with pytest.raises(ValidationError):
            LLMDeal(item="x", store="y", final_price_unit="each")

    def test_llmdeals_wrapper(self):
        assert LLMDeals().deals == []
        deals = LLMDeals.model_validate(
            {"deals": [{"item": "a", "store": "b"}, {"item": "c", "store": "d"}]}
        )
        assert [d.item for d in deals.deals] == ["a", "c"]

    def test_two_instances_do_not_share_list_defaults(self):
        a = LLMDeal(item="a", store="s")
        a.codes.append("SAVE10")
        b = LLMDeal(item="b", store="s")
        assert b.codes == []


class TestSystemPrompt:
    def test_core_invariants_present(self):
        low = SYSTEM_PROMPT.lower()
        assert "arithmetic" in low            # extraction only, no math
        assert "verbatim" in low              # quotes must be exact
        assert "roundup" in low               # split roundup posts
        assert "null" in low                  # unquotable price stays null

    def test_vocabularies_present(self):
        for word in ("new_customer", "prime_only", "targeted", "subscribe_save",
                     "in_cart", "limited_qty", "pricing_error", "in_store_only",
                     "ymmv", "clearance_aisle"):
            assert word in SYSTEM_PROMPT
        for word in ("bogo", "b2g1", "b2g3", "b1g1_50", "free_after_rebate",
                     "moneymaker", "penny"):
            assert word in SYSTEM_PROMPT
        assert "price_is_range" in SYSTEM_PROMPT
        assert "price_upper_bound" in SYSTEM_PROMPT


class TestBuildUserPrompt:
    def test_fences_and_meta(self):
        meta = {
            "source_name": "hip2save",
            "source_url": "https://hip2save.com/deal/1",
            "title": "Big deal",
        }
        prompt = build_user_prompt("Widget now $10.00", meta)
        assert "BEGIN POST\nWidget now $10.00\nEND POST" in prompt
        assert "hip2save" in prompt
        assert "https://hip2save.com/deal/1" in prompt
        assert "Big deal" in prompt

    def test_missing_meta_keys_tolerated(self):
        prompt = build_user_prompt("text", {})
        assert "BEGIN POST\ntext\nEND POST" in prompt


class TestToExtracted:
    def test_full_field_mapping(self):
        llm = LLMDeal(
            item="Anker Charger",
            store="Amazon",
            brand="Anker",
            product_url="https://amazon.example/x",
            online_only="true",
            regular_price=29.99,
            final_price=14.99,
            pct_off=50.0,
            abs_savings=15.0,
            shipping_cost_or_threshold="free over $35",
            stack_steps=["clip coupon", "code at checkout"],
            codes=["ANKER50"],
            rebates=["$5 mail-in"],
            deferred_value="$5 gift card back",
            conditions=["prime_only"],
            expires_at_raw="ends today",
            quantity_or_unit="2-pack",
            regular_price_unit="pack",
            final_price_unit="pack",
            currency="USD",
            evidence=LLMEvidence(
                regular_price_quote="was $29.99",
                final_price_quote="now only $14.99",
                pct_quote="50% off",
            ),
            confidence=0.9,
            deal_type="bogo",
            price_is_range=True,
            price_upper_bound=19.99,
        )
        post = make_post()
        deal = to_extracted(llm, post, TZ, NOW)

        assert deal.item == "Anker Charger"
        assert deal.store == "Amazon"
        assert deal.source_url == post.url
        assert deal.source_name == post.source_name
        assert deal.brand == "Anker"
        assert deal.product_url == "https://amazon.example/x"
        assert deal.online_only == "true"
        assert deal.regular_price == 29.99
        assert deal.final_price == 14.99
        assert deal.pct_off == 50.0
        assert deal.abs_savings == 15.0
        assert deal.shipping_cost_or_threshold == "free over $35"
        assert deal.stack_steps == ["clip coupon", "code at checkout"]
        assert deal.codes == ["ANKER50"]
        assert deal.rebates == ["$5 mail-in"]
        assert deal.deferred_value == "$5 gift card back"
        assert deal.conditions == ["prime_only"]
        assert deal.quantity_or_unit == "2-pack"
        assert deal.regular_price_unit == "pack"
        assert deal.final_price_unit == "pack"
        assert deal.region == "US"
        assert deal.currency == "USD"
        assert deal.evidence.regular_price_quote == "was $29.99"
        assert deal.evidence.final_price_quote == "now only $14.99"
        assert deal.evidence.pct_quote == "50% off"
        assert deal.confidence == 0.9
        assert deal.deal_type == "bogo"
        assert deal.price_is_range is True
        assert deal.price_upper_bound == 19.99
        # "ends today" resolved to end of day in the source tz, as ISO UTC
        assert deal.expires_at == util.iso(util.end_of_day(TZ, NOW))

    def test_unresolvable_expiry_maps_to_empty(self):
        llm = LLMDeal(item="x", store="y", expires_at_raw="while supplies last")
        deal = to_extracted(llm, make_post(), TZ, NOW)
        assert deal.expires_at == ""

    def test_lists_are_copies_not_shared(self):
        llm = LLMDeal(item="x", store="y", codes=["A"])
        deal = to_extracted(llm, make_post(), TZ, NOW)
        deal.codes.append("B")
        assert llm.codes == ["A"]
