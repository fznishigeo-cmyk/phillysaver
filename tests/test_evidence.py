"""Tests for verify_evidence — the parse layer's evidence non-negotiable.

A parse whose price quote is not verbatim in the text sent to the model MUST
be rejected.
"""

from __future__ import annotations

import pytest

from dealradar.models import Evidence, ExtractedDeal
from dealradar.parse.extractor import verify_evidence
from dealradar.savings import compute_savings

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


@pytest.mark.parametrize("currency", ["$", "$ "])
@pytest.mark.parametrize("clipped", [False, True])
def test_pay_amount_for_stated_value_is_checkout_price(currency, clipped):
    text = f"Pay {currency}25 for $50 worth of food"
    candidate = make_deal(
        regular_price=50, final_price=25,
        evidence=Evidence(regular_price_quote="$50 worth of food",
                          final_price_quote="25" if clipped else text),
    )
    assert verify_evidence(candidate, text) == (True, "")
    result = compute_savings(candidate)
    assert result.computable and result.final_price == 25 and result.pct_at_checkout == 50


@pytest.mark.parametrize("text", [
    "Pringles Original $1.49 (was $2.99)",
    "Pringles Original $1.49 now at Target (was $2.99)",
])
@pytest.mark.parametrize("clipped", [False, True])
def test_original_product_variant_is_not_a_regular_price_label(text, clipped):
    candidate = make_deal(
        item="Pringles Original", regular_price=2.99, final_price=1.49,
        evidence=Evidence(regular_price_quote="was $2.99",
                          final_price_quote="1.49" if clipped else text),
    )
    assert verify_evidence(candidate, text) == (True, "")


@pytest.mark.parametrize("text", [
    "Original $2.99, now $1.49",
    "Pringles: original $2.99, now $1.49",
    "Pringles Original price $2.99, now $1.49",
    "Pringles original: $2.99, now $1.49",
    "Pringles originally $2.99, now $1.49",
    "Pringles original $2.99, now $1.49",
])
@pytest.mark.parametrize("clipped", [False, True])
def test_original_comparison_price_still_rejected_as_checkout(text, clipped):
    candidate = make_deal(
        final_price=2.99,
        evidence=Evidence(final_price_quote="2.99" if clipped else text),
    )
    assert not verify_evidence(candidate, text)[0]


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


@pytest.mark.parametrize("clipped", [False, True])
@pytest.mark.parametrize("regular_unit,final_unit,accepted", [
    ("per_unit", "per_unit", True),
    (" PER_UNIT ", "per_unit", True),
    ("", "", False),
    ("per_unit", "", False),
    ("", "per_unit", False),
    ("pack", "per_unit", False),
    ("per_unit", "pack", False),
    ("pack", "pack", False),
])
def test_per_unit_price_requires_both_matching_unit_fields(regular_unit, final_unit,
                                                          accepted, clipped):
    text = "Regular $7.99/lb, now $3.99/lb"
    candidate = make_deal(
        regular_price=7.99, final_price=3.99,
        regular_price_unit=regular_unit, final_price_unit=final_unit,
        evidence=Evidence(regular_price_quote="$7.99/lb",
                          final_price_quote="$3.99" if clipped else "$3.99/lb"),
    )
    assert verify_evidence(candidate, text)[0] is accepted
    if accepted:
        result = compute_savings(candidate)
        assert result.computable and result.final_price == 3.99
        assert "per_unit" in result.flags


@pytest.mark.parametrize("text,value", [
    ("$24/mo for 24 months", 24),
    ("40% off", 40),
    ("$5 off orders of $25+", 25),
    ("$10 Kohl’s Cash", 10),
    ("was $49.99 now $19.99", 49.99),
])
def test_per_unit_fields_do_not_allow_other_non_checkout_roles(text, value):
    candidate = make_deal(
        final_price=value, regular_price_unit="per_unit", final_price_unit="per_unit",
        evidence=Evidence(final_price_quote=text),
    )
    assert not verify_evidence(candidate, text)[0]


@pytest.mark.parametrize("text", [
    "Sony WH-1000XM5 — $199.99",
    "Sony WH-1000XM5 - $199.99",
    "Sony WH-1000XM5 – $199.99",
    "Sony WH-1000XM5 to $199.99",
    "$199.99 — Sony WH-1000XM5",
    "$199.99 - 1000XM5 headphones",
])
@pytest.mark.parametrize("clipped", [False, True])
def test_model_number_does_not_create_a_price_range(text, clipped):
    candidate = make_deal(
        final_price=199.99,
        evidence=Evidence(final_price_quote="$199.99" if clipped else text),
    )
    assert verify_evidence(candidate, text) == (True, "")


@pytest.mark.parametrize("text", ["$9.99-19.99", "$9.99 to 19.99",
                                  "9.99-$19.99", "9.99 to $19.99",
                                  "Sale 9.99-19.99", "Sale 9.99 to 19.99"])
@pytest.mark.parametrize("value", [9.99, 19.99])
@pytest.mark.parametrize("clipped", [False, True])
def test_money_range_with_optional_currency_symbols_still_requires_flag(text, value, clipped):
    candidate = make_deal(final_price=value,
                          evidence=Evidence(final_price_quote=f"{value:g}" if clipped else text))
    assert not verify_evidence(candidate, text)[0]
    candidate.price_is_range = True
    assert verify_evidence(candidate, text) == (True, "")


@pytest.mark.parametrize("ending", [".", ", depending on size", "..."])
@pytest.mark.parametrize("value", [9.99, 19.99])
def test_money_range_with_trailing_punctuation_still_requires_flag(ending, value):
    text = f"Sale $9.99-$19.99{ending}"
    candidate = make_deal(final_price=value,
                          evidence=Evidence(final_price_quote=f"${value:g}"))
    assert not verify_evidence(candidate, text)[0]
    candidate.price_is_range = True
    assert verify_evidence(candidate, text) == (True, "")


@pytest.mark.parametrize("text,value", [
    ("Now $19.99 (was $49.99)", 19.99),
    ("$19.99 (reg. $29.99)", 19.99),
    ("Sale: $12.99 + free shipping over $35", 12.99),
    ("$5 each", 5),
    ("2-pack for $9.99", 9.99),
])
def test_checkout_copy_variations_keep_valid_amount(text, value):
    candidate = make_deal(final_price=value, evidence=Evidence(final_price_quote=text))
    assert verify_evidence(candidate, text) == (True, "")


@pytest.mark.parametrize("text,value", [("Under $20 roundup", 20), ("from $9.99", 9.99)])
@pytest.mark.parametrize("range_flag", [False, True])
def test_teaser_wording_requires_range_flag(text, value, range_flag):
    candidate = make_deal(final_price=value, price_is_range=range_flag,
                          evidence=Evidence(final_price_quote=text))
    assert verify_evidence(candidate, text)[0] is range_flag


@pytest.mark.parametrize("quote", [
    "$100 off orders of $1,000+",
    "$100 off orders of $1,000.00+",
    "$100 off orders of $1000+",
    "$100 off orders of $1,000",
])
def test_coupon_minimum_accepts_whole_thousands_amount(quote):
    candidate = make_deal(regular_price=1200, stack_steps=[quote],
                          evidence=Evidence(regular_price_quote="$1,200"))
    assert verify_evidence(candidate, f"Regular $1,200. Coupon: {quote}") == (True, "")
    result = compute_savings(candidate)
    assert result.computable and result.final_price == 1100


@pytest.mark.parametrize("source_minimum", ["$1,000", "$1,000.99", "$10,000"])
def test_coupon_minimum_rejects_clipped_thousands_amount(source_minimum):
    candidate = make_deal(stack_steps=["$100 off orders of $1"])
    assert not verify_evidence(candidate, f"Coupon: $100 off orders of {source_minimum}+")[0]
