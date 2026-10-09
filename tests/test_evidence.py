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
