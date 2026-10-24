"""Tests for verify_evidence — the parse layer's evidence non-negotiable.

A parse whose price quote is not verbatim in the text sent to the model MUST
be rejected.
"""

from __future__ import annotations

import pytest

from dealradar.models import Evidence, ExtractedDeal
from dealradar.parse.extractor import verify_evidence

TEXT = (
    "Anker 735 Charger at Amazon\n"
    "Was   $29.99, now only  $14.99 with code ANKER50 — that's 50% off!\n"
    "Grab the 2-pack for $1,299.99 or a single for $15 while it lasts."
)


def make_deal(**over) -> ExtractedDeal:
    kw = dict(
        item="Anker 735 Charger",
        store="Amazon",
        source_url="https://example.test/p",
        source_name="hip2save",
        evidence=Evidence(),
    )
    kw.update(over)
    return ExtractedDeal(**kw)


class TestVerifyEvidence:
    def test_REQUIRED_final_price_quote_absent_from_text_rejected(self):
        deal = make_deal(
            final_price=14.99,
            evidence=Evidence(final_price_quote="now just $14.99"),  # not in TEXT
        )
        ok, reason = verify_evidence(deal, TEXT)
        assert ok is False
        assert "final_price" in reason

    def test_quote_present_but_number_mismatched_rejected(self):
        # The quote IS verbatim in the text, but doesn't contain 12.99.
        deal = make_deal(
            final_price=12.99,
            evidence=Evidence(final_price_quote="now only $14.99"),
        )
        ok, reason = verify_evidence(deal, TEXT)
        assert ok is False
        assert "final_price" in reason
        assert "12.99" in reason

    def test_whitespace_differing_quote_accepted(self):
        # Text has "Was   $29.99" (run of spaces) and a newline mid-sentence;
        # the quote uses single spaces. Only whitespace runs are normalized.
        deal = make_deal(
            regular_price=29.99,
            evidence=Evidence(regular_price_quote="Charger at Amazon Was $29.99,"),
        )
        assert verify_evidence(deal, TEXT) == (True, "")

    def test_pct_with_percent_sign_accepted(self):
        deal = make_deal(
            pct_off=50.0,
            evidence=Evidence(pct_quote="that's 50% off"),
        )
        assert verify_evidence(deal, TEXT) == (True, "")

    def test_pct_without_percent_sign_accepted(self):
        text = "Take 30 percent off everything today"
        deal = make_deal(
            pct_off=30.0,
            evidence=Evidence(pct_quote="Take 30 percent off"),
        )
        assert verify_evidence(deal, text) == (True, "")

    def test_null_prices_need_no_quotes(self):
        deal = make_deal()  # all prices None, evidence empty
        assert verify_evidence(deal, TEXT) == (True, "")

    def test_empty_quote_with_non_null_price_rejected(self):
        deal = make_deal(regular_price=29.99)  # evidence left empty
        ok, reason = verify_evidence(deal, TEXT)
        assert ok is False
        assert "regular_price" in reason

    def test_all_three_fields_verified_together(self):
        deal = make_deal(
            regular_price=29.99,
            final_price=14.99,
            pct_off=50.0,
            evidence=Evidence(
                regular_price_quote="Was $29.99",
                final_price_quote="now only $14.99",
                pct_quote="50% off",
            ),
        )
        assert verify_evidence(deal, TEXT) == (True, "")

    def test_one_bad_quote_among_good_ones_rejects(self):
        deal = make_deal(
            regular_price=29.99,
            final_price=14.99,
            evidence=Evidence(
                regular_price_quote="Was $29.99",
                final_price_quote="only $9.99",  # not in text
            ),
        )
        ok, reason = verify_evidence(deal, TEXT)
        assert ok is False
        assert "final_price" in reason

    def test_integer_written_form_accepted_for_integral_price(self):
        deal = make_deal(
            final_price=15.0,
            evidence=Evidence(final_price_quote="a single for $15"),
        )
        assert verify_evidence(deal, TEXT) == (True, "")

    def test_thousands_commas_accepted(self):
        deal = make_deal(
            final_price=1299.99,
            evidence=Evidence(final_price_quote="2-pack for $1,299.99"),
        )
        assert verify_evidence(deal, TEXT) == (True, "")

    def test_number_present_without_commas_also_accepted(self):
        text = "Monster TV now 1299.99 at checkout"
        deal = make_deal(
            final_price=1299.99,
            evidence=Evidence(final_price_quote="now 1299.99"),
        )
        assert verify_evidence(deal, text) == (True, "")

    @pytest.mark.parametrize("field,quote_field", [
        ("regular_price", "regular_price_quote"),
        ("final_price", "final_price_quote"),
        ("pct_off", "pct_quote"),
    ])
    def test_each_field_is_independently_checked(self, field, quote_field):
        deal = make_deal(**{field: 42.0,
                            "evidence": Evidence(**{quote_field: "not in the text 42"})})
        ok, reason = verify_evidence(deal, TEXT)
        assert ok is False
        assert field in reason


@pytest.mark.parametrize("text,value", [
    ("$24/mo for 24 months", 24),
    ("Save 40%", 40),
    ("40% off", 40),
    ("2 for $10", 2),
    ("$0.31/count", .31),
    ("$12.99 ($0.54 / Count)", .54),
    ("$5 off $25", 25),
    ("$5 off orders of $25+", 25),
    ("$10 Kohl’s Cash", 10),
    ("was $49.99 now $19.99", 49.99),
    ("from $9.99", 9.99),
    ("$9.99 - $19.99", 9.99),
    ("$9.99 - $19.99", 19.99),
])
@pytest.mark.parametrize("clipped", [False, True])
def test_final_price_requires_checkout_context(text, value, clipped):
    quote = f"{value:g}" if clipped else text
    candidate = make_deal(final_price=value, evidence=Evidence(final_price_quote=quote))
    ok, reason = verify_evidence(candidate, text)
    assert not ok
    assert "final_price" in reason


@pytest.mark.parametrize("text,value", [
    ("was $49.99 now $19.99", 19.99),
    ("2 for $10", 10),
    ("$12.99 ($0.54 / Count)", 12.99),
    ("Save 40%: now $40", 40),
    ("$24/mo for 24 months; outright price $240", 240),
    ("Now $20, receive $10 Kohl’s Cash", 20),
    ("Subscribe & Save $8.49", 8.49),
    ("A 24-count pack for $24", 24),
])
def test_checkout_amount_still_passes_among_other_numbers(text, value):
    candidate = make_deal(final_price=value, evidence=Evidence(final_price_quote=text))
    assert verify_evidence(candidate, text) == (True, "")


@pytest.mark.parametrize("text", ["from $9.99 up to $19.99", "$9.99 - $19.99"])
def test_explicitly_flagged_range_keeps_verified_bounds(text):
    candidate = make_deal(final_price=9.99, price_is_range=True, price_upper_bound=19.99,
                          evidence=Evidence(final_price_quote="$9.99",
                                            price_upper_bound_quote="$19.99"))
    assert verify_evidence(candidate, text) == (True, "")


@pytest.mark.parametrize("unit", ["ml", "g", "kg", "ea"])
def test_unit_comparison_context_never_becomes_checkout_price(unit):
    text = f"$12.99 ($0.25/{unit})"
    candidate = make_deal(final_price=.25, evidence=Evidence(final_price_quote="$0.25"))
    assert not verify_evidence(candidate, text)[0]
    actual = make_deal(final_price=12.99, evidence=Evidence(final_price_quote="$12.99"))
    assert verify_evidence(actual, text) == (True, "")


@pytest.mark.parametrize("value", [9.99, 19.99])
def test_written_range_separator_needs_explicit_range_flag(value):
    text = "$9.99 to $19.99"
    candidate = make_deal(final_price=value, evidence=Evidence(final_price_quote=text))
    assert not verify_evidence(candidate, text)[0]
    candidate.price_is_range = True
    assert verify_evidence(candidate, text) == (True, "")


@pytest.mark.parametrize("source_minimum", ["$250", "$25.99"])
def test_coupon_minimum_requires_the_whole_source_amount(source_minimum):
    candidate = make_deal(stack_steps=["$5 off $25"])
    assert not verify_evidence(candidate, f"Coupon: $5 off {source_minimum}")[0]
