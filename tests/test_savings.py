"""Tests for the pure-code savings engine (dealradar/savings.py).

Every scenario carries the original real-world wording as a comment and builds
the ExtractedDeal the way the parse layer would: RAW VALUES ONLY (stated
prices, stated pct, stack steps as text, shipping text, deal-type markers).
All math is asserted against SAVINGS_RULES.md. Fully offline, no fixtures.
"""

from __future__ import annotations

import pytest

from dealradar.models import ExtractedDeal
from dealradar.savings import compute_savings, parse_shipping, parse_stack_step


def deal(**kw) -> ExtractedDeal:
    base: dict = {
        "item": "Widget",
        "store": "MegaMart",
        "source_url": "https://example.com/post/1",
        "source_name": "test_source",
    }
    base.update(kw)
    return ExtractedDeal(**base)


def approx(x: float):
    # The brief asks for pct assertions to 0.1; the engine rounds to 1 decimal.
    return pytest.approx(x, abs=0.05)


# ---------------------------------------------------------------------------
# parser unit tests
# ---------------------------------------------------------------------------

def test_parse_shipping_all_forms():
    assert parse_shipping("") == (None, None)
    assert parse_shipping("5.99") == (5.99, None)
    assert parse_shipping("$5.99") == (5.99, None)
    assert parse_shipping("free") == (0.0, None)
    assert parse_shipping("Free Shipping") == (0.0, None)
    assert parse_shipping("free over $35") == (None, 35.0)
    assert parse_shipping("free on $25+") == (None, 25.0)
    assert parse_shipping("$6.99 or free over $50") == (6.99, 50.0)
    assert parse_shipping("FREE OVER $35") == (None, 35.0)  # case-insensitive


def test_parse_stack_step_all_kinds():
    assert parse_stack_step("20% off code SAVE20") == {"kind": "pct_off", "value": 20.0}
    assert parse_stack_step("extra $10 off coupon") == {"kind": "abs_off", "value": 10.0}
    assert parse_stack_step("sale price $39.99") == {"kind": "sale_price", "value": 39.99}
    assert parse_stack_step("15% subscribe & save") == {"kind": "pct_off", "value": 15.0}
    assert parse_stack_step("$5 mail-in rebate") == {"kind": "rebate_abs", "value": 5.0}
    assert parse_stack_step("10% rebate") == {"kind": "rebate_pct", "value": 10.0}
    assert parse_stack_step("clip $3 coupon") == {"kind": "abs_off", "value": 3.0}
    assert parse_stack_step("stack with cartwheel maybe") == {"kind": "unknown", "value": None}


# ---------------------------------------------------------------------------
# required traps (a)-(r)
# ---------------------------------------------------------------------------

def test_a_shipping_pushes_below_floor():
    # "Blender reg $100 now $35, shipping is $6"
    r = compute_savings(deal(regular_price=100.0, final_price=35.0,
                             shipping_cost_or_threshold="$6"))
    assert r.computable and r.exclude_reason == ""
    assert r.final_price == approx(41.0)          # 35 + 6 at checkout
    assert r.pct_at_checkout == approx(59.0)      # below a 70% floor
    assert r.basis == "quoted_regular"
    assert r.abs_savings == approx(59.0)


def test_b_per_unit_regular_vs_pack_final_excluded():
    # "Protein bars reg $12.99 each, 4-pack now $39.99"
    r = compute_savings(deal(regular_price=12.99, final_price=39.99,
                             regular_price_unit="per_unit",
                             final_price_unit="pack",
                             quantity_or_unit="4-pack"))
    assert not r.computable
    assert r.exclude_reason == "unit_mismatch"
    assert r.pct_at_checkout is None


def test_c_gift_card_back_is_deferred_not_pct():
    # "$50 Visa gift card purchase, get a $40 store gift card back!"
    r = compute_savings(deal(regular_price=50.0, final_price=50.0,
                             deferred_value="$40 store gift card back"))
    assert r.computable
    assert "deferred_value" in r.flags
    assert r.pct_at_checkout == approx(0.0)
    assert r.pct_at_checkout != approx(80.0)      # deferred value never enters pct


def test_d_bogo_alone_reports_50():
    # "BOGO free on all candles" — floor is applied at publish, not here
    r = compute_savings(deal(deal_type="bogo"))
    assert r.computable
    assert r.pct_at_checkout == approx(50.0)
    assert r.pct_with_rebates == approx(50.0)
    assert r.basis == "bogo"


def test_e_bogo_plus_half_off_code_compounds():
    # "BOGO free + extra 50% off with code TOYS"
    r = compute_savings(deal(deal_type="bogo",
                             stack_steps=["extra 50% off with code TOYS"],
                             codes=["TOYS"]))
    assert r.pct_at_checkout == approx(75.0)      # 1 - 0.5*0.5
    assert r.basis == "bogo"


def test_f_as_low_as_range_uses_upper_bound():
    # "Jackets as low as $29.99 (styles up to $59.99), reg $100"
    r = compute_savings(deal(regular_price=100.0, final_price=29.99,
                             price_is_range=True, price_upper_bound=59.99))
    assert r.pct_at_checkout == approx(40.0)      # from 59.99, not 29.99
    assert "range" in r.flags
    assert r.basis == "quoted_regular"
    assert r.final_price == approx(59.99)


def test_g_clearance_aisle_ymmv_excluded():
    # "Found in the clearance aisle, YMMV my store"
    r = compute_savings(deal(regular_price=40.0, final_price=4.0,
                             conditions=["Clearance aisle YMMV my store"]))
    assert not r.computable
    assert r.exclude_reason == "in_store"


def test_h_free_after_rebate_is_100_but_flagged():
    # "Software $9.99, FREE after rebate"
    r = compute_savings(deal(deal_type="free_after_rebate", final_price=9.99,
                             rebates=["$9.99 mail-in rebate"]))
    assert r.computable
    assert r.pct_at_checkout == approx(100.0)
    assert r.pct_with_rebates == approx(100.0)
    assert "rebate_dependent" in r.flags
    assert r.basis == "free_after_rebate"


def test_i_moneymaker_exceeds_100_when_numbers_allow():
    # "Razor $5 (reg $5), $8 mail-in rebate — moneymaker!"
    r = compute_savings(deal(deal_type="moneymaker", regular_price=5.0,
                             final_price=5.0, rebates=["$8 mail-in rebate"]))
    assert r.pct_at_checkout == approx(100.0)
    assert r.pct_with_rebates == approx(160.0)    # (5 - (5-8)) / 5
    assert "rebate_dependent" in r.flags
    assert r.basis == "free_after_rebate"


def test_i2_moneymaker_without_numbers_is_100():
    # "Moneymaker at CVS this week!" (no usable numbers)
    r = compute_savings(deal(deal_type="moneymaker"))
    assert r.pct_at_checkout == approx(100.0)
    assert r.pct_with_rebates == approx(100.0)
    assert "rebate_dependent" in r.flags


def test_j_penny_deal_without_regular():
    # "Penny item! Rings up $0.01"
    r = compute_savings(deal(deal_type="penny", final_price=0.01))
    assert r.computable
    assert r.pct_at_checkout == approx(100.0)
    assert r.basis == "penny"


def test_j2_penny_deal_with_regular_computes_truly():
    # "Nail polish $0.01 (reg $4.99)"
    r = compute_savings(deal(deal_type="penny", regular_price=4.99,
                             final_price=0.01))
    assert r.pct_at_checkout == approx(99.8)      # (4.99-0.01)/4.99
    assert r.basis == "penny"


def test_k_stack_order_sale_code_rebate():
    # "reg $80, sale $40, code SAVE25 (25% off), $5 rebate"
    r = compute_savings(deal(
        regular_price=80.0,
        stack_steps=["sale price $40", "25% off code SAVE25", "$5 mail-in rebate"],
        codes=["SAVE25"],
    ))
    assert r.computable
    assert r.final_price == approx(30.0)          # 40 * 0.75 at checkout
    assert r.pct_at_checkout == approx(62.5)
    assert r.pct_with_rebates == approx(68.8)     # 68.75 -> 1 decimal
    assert "rebate_dependent" in r.flags
    assert r.basis == "stack"


def test_l_pct_code_applies_to_running_total_not_regular():
    # "reg $100, sale price $50, extra 20% off code EXTRA20"
    r = compute_savings(deal(regular_price=100.0,
                             stack_steps=["sale price $50", "20% off code EXTRA20"]))
    assert r.final_price == approx(40.0)          # 50*0.8, NOT 100-50-20=30
    assert r.pct_at_checkout == approx(60.0)      # NOT 70.0
    assert r.basis == "stack"


def test_m_value_for_phrasing():
    # "$120 value for $29"
    r = compute_savings(deal(regular_price=120.0, final_price=29.0))
    assert r.pct_at_checkout == approx(75.8)
    assert r.basis == "quoted_regular"


def test_n_pct_claim_alone_is_list_price_claim():
    # "70% off sitewide" (no prices quoted)
    r = compute_savings(deal(pct_off=70.0))
    assert r.computable
    assert r.pct_at_checkout == approx(70.0)
    assert r.basis == "list_price_claim"
    assert r.final_price is None
    assert r.abs_savings is None


def test_o_missing_regular_and_pct_is_no_basis():
    # "Gadget only $25 today!" (no reg, no pct, no deal type)
    r = compute_savings(deal(final_price=25.0))
    assert not r.computable
    assert r.exclude_reason == "no_basis"


def test_p_eur_currency_excluded():
    # "Lego Technic €49.99 (was €99.99)" from a EU mirror
    r = compute_savings(deal(regular_price=99.99, final_price=49.99,
                             currency="EUR", region="DE"))
    assert not r.computable
    assert r.exclude_reason == "non_us"


def test_p2_non_us_region_excluded_even_in_usd():
    # "Canada only: USD pricing at checkout"
    r = compute_savings(deal(regular_price=100.0, final_price=30.0, region="CA"))
    assert not r.computable
    assert r.exclude_reason == "non_us"


def test_q_free_shipping_threshold_not_met_flags_unknown():
    # "Mug $20 (reg $50) — free shipping over $35"
    r = compute_savings(deal(regular_price=50.0, final_price=20.0,
                             shipping_cost_or_threshold="free over $35"))
    assert r.computable
    assert "unknown_shipping" in r.flags
    assert r.pct_at_checkout == approx(60.0)      # computed without shipping
    assert r.final_price == approx(20.0)


def test_r_stated_shipping_added_but_stays_at_floor():
    # "Headphones reg $100 now $25 + $4.99 shipping" — 75% drops to exactly 70%
    r = compute_savings(deal(regular_price=100.0, final_price=25.0,
                             shipping_cost_or_threshold="$4.99"))
    assert r.final_price == approx(29.99)
    assert r.pct_at_checkout == approx(70.0)      # (100-29.99)/100 = 70.01 -> 70.0


# ---------------------------------------------------------------------------
# further real-world phrasings
# ---------------------------------------------------------------------------

def test_was_now_phrasing():
    # "was $60 now $15"
    r = compute_savings(deal(regular_price=60.0, final_price=15.0))
    assert r.pct_at_checkout == approx(75.0)
    assert r.basis == "quoted_regular"
    assert r.abs_savings == approx(45.0)


def test_value_yours_for_phrasing():
    # "$199.99 value, yours for $49.99"
    r = compute_savings(deal(regular_price=199.99, final_price=49.99))
    assert r.pct_at_checkout == approx(75.0)
    assert r.basis == "quoted_regular"


def test_worth_for_phrasing():
    # "$45 worth of snacks for $18"
    r = compute_savings(deal(regular_price=45.0, final_price=18.0))
    assert r.pct_at_checkout == approx(60.0)
    assert r.basis == "quoted_regular"


def test_b2g1_free():
    # "B2G1 free on all books"
    r = compute_savings(deal(deal_type="b2g1"))
    assert r.pct_at_checkout == approx(33.3)
    assert r.basis == "b2g1"


def test_b1g1_50_off():
    # "B1G1 50% off shoes"
    r = compute_savings(deal(deal_type="b1g1_50"))
    assert r.pct_at_checkout == approx(25.0)
    assert r.basis == "b1g1_50"


def test_b2g3_free():
    # "Buy 2 get 3 free — stock up!"
    r = compute_savings(deal(deal_type="b2g3"))
    assert r.pct_at_checkout == approx(60.0)
    assert r.basis == "b2g3"


def test_claim_plus_extra_code_compounds():
    # "60% off + extra 25% off with code STACK" (no prices quoted)
    r = compute_savings(deal(pct_off=60.0,
                             stack_steps=["extra 25% off with code STACK"],
                             codes=["STACK"]))
    assert r.pct_at_checkout == approx(70.0)      # 1 - 0.4*0.75
    assert r.basis == "list_price_claim"


def test_under_price_range_with_regular():
    # "under $10, reg $45"
    r = compute_savings(deal(regular_price=45.0, price_is_range=True,
                             price_upper_bound=10.0))
    assert r.pct_at_checkout == approx(77.8)      # (45-10)/45
    assert "range" in r.flags
    assert r.basis == "quoted_regular"


def test_per_unit_on_both_sides_computes_with_flag():
    # "Yogurt $1.25/cup (reg $2.50/cup)"
    r = compute_savings(deal(regular_price=2.50, final_price=1.25,
                             regular_price_unit="per_unit",
                             final_price_unit="per_unit",
                             quantity_or_unit="per unit"))
    assert r.computable
    assert r.pct_at_checkout == approx(50.0)
    assert "per_unit" in r.flags


def test_prices_win_over_stated_pct():
    # "50% off! reg $100 now $40" (source's own pct is wrong; prices win)
    r = compute_savings(deal(regular_price=100.0, final_price=40.0, pct_off=50.0))
    assert r.pct_at_checkout == approx(60.0)
    assert r.basis == "quoted_regular"


def test_stated_pct_with_quoted_regular_no_final():
    # "60% off (reg $45)" — no final stated anywhere
    r = compute_savings(deal(regular_price=45.0, pct_off=60.0))
    assert r.computable
    assert r.pct_at_checkout == approx(60.0)
    assert r.basis == "stated_pct"
    assert r.final_price == approx(18.0)          # 45 * 0.4, arithmetic not a guess
    assert r.abs_savings == approx(27.0)


def test_plain_free_shipping_adds_nothing():
    # "Tumbler $20 (reg $40), free shipping"
    r = compute_savings(deal(regular_price=40.0, final_price=20.0,
                             shipping_cost_or_threshold="free shipping"))
    assert r.pct_at_checkout == approx(50.0)
    assert "unknown_shipping" not in r.flags
    assert r.final_price == approx(20.0)


def test_bare_number_shipping_cost():
    # "Toy $20 (reg $50), shipping 5.99"
    r = compute_savings(deal(regular_price=50.0, final_price=20.0,
                             shipping_cost_or_threshold="5.99"))
    assert r.final_price == approx(25.99)
    assert r.pct_at_checkout == approx(48.0)      # (50-25.99)/50 = 48.02


def test_threshold_met_means_free_shipping():
    # "Backpack $40 (reg $100), free shipping over $35" — $40 clears it
    r = compute_savings(deal(regular_price=100.0, final_price=40.0,
                             shipping_cost_or_threshold="free over $35"))
    assert r.pct_at_checkout == approx(60.0)
    assert "unknown_shipping" not in r.flags
    assert r.final_price == approx(40.0)


def test_cost_or_threshold_above_threshold():
    # "Vacuum $60 (reg $100), $6.99 shipping or free over $50" — free applies
    r = compute_savings(deal(regular_price=100.0, final_price=60.0,
                             shipping_cost_or_threshold="$6.99 or free over $50"))
    assert r.final_price == approx(60.0)
    assert r.pct_at_checkout == approx(40.0)


def test_cost_or_threshold_below_threshold():
    # "Kettle $30 (reg $100), $6.99 shipping or free over $50" — cost applies
    r = compute_savings(deal(regular_price=100.0, final_price=30.0,
                             shipping_cost_or_threshold="$6.99 or free over $50"))
    assert r.final_price == approx(36.99)
    assert r.pct_at_checkout == approx(63.0)      # (100-36.99)/100


def test_rebate_on_stated_final_only_affects_with_rebates():
    # "SSD reg $100 now $50 with a $10 mail-in rebate"
    r = compute_savings(deal(regular_price=100.0, final_price=50.0,
                             rebates=["$10 mail-in rebate"]))
    assert r.pct_at_checkout == approx(50.0)      # rebates never touch checkout
    assert r.pct_with_rebates == approx(60.0)
    assert "rebate_dependent" in r.flags
    assert r.basis == "quoted_regular"


def test_pct_rebate_in_stack_uses_checkout_base():
    # "reg $100, sale price $80, 10% rebate"
    r = compute_savings(deal(regular_price=100.0,
                             stack_steps=["sale price $80", "10% rebate"]))
    assert r.pct_at_checkout == approx(20.0)
    assert r.pct_with_rebates == approx(28.0)     # rebate = 10% of $80 = $8
    assert "rebate_dependent" in r.flags
    assert r.basis == "stack"


def test_unknown_stack_step_ignored_for_checkout():
    # "reg $100, sale price $50, stacks with store loyalty points"
    r = compute_savings(deal(regular_price=100.0,
                             stack_steps=["sale price $50",
                                          "stacks with store loyalty points"]))
    assert r.pct_at_checkout == approx(50.0)      # unknown step skipped
    assert r.final_price == approx(50.0)
    assert r.basis == "stack"


def test_abs_off_steps_subtract_in_order():
    # "reg $50, extra $10 off coupon, clip $3 coupon"
    r = compute_savings(deal(regular_price=50.0,
                             stack_steps=["extra $10 off coupon", "clip $3 coupon"]))
    assert r.final_price == approx(37.0)
    assert r.pct_at_checkout == approx(26.0)
    assert r.basis == "stack"


def test_subscribe_and_save_pct_step():
    # "reg $40, sale price $30, 15% subscribe & save"
    r = compute_savings(deal(regular_price=40.0,
                             stack_steps=["sale price $30", "15% subscribe & save"]))
    assert r.final_price == approx(25.5)          # 30 * 0.85
    # (40-25.5)/40 = 36.25, a rounding tie -> accept either 1-decimal neighbor
    assert r.pct_at_checkout == pytest.approx(36.25, abs=0.06)
    assert r.basis == "stack"


def test_claim_with_stated_final_price():
    # "Now $30 after 70% off" (no regular quoted)
    r = compute_savings(deal(final_price=30.0, pct_off=70.0))
    assert r.pct_at_checkout == approx(70.0)
    assert r.basis == "list_price_claim"
    assert r.final_price == approx(30.0)


def test_stated_final_trusted_over_stack_steps():
    # "reg $100, final $30 after all codes (20% off code applied)"
    r = compute_savings(deal(regular_price=100.0, final_price=30.0,
                             stack_steps=["20% off code APPLIED"]))
    assert r.pct_at_checkout == approx(70.0)      # final is trusted, not recompounded
    assert r.basis == "quoted_regular"


def test_in_store_only_token_excluded():
    # conditions normalized by the parse layer to the marker token
    r = compute_savings(deal(regular_price=20.0, final_price=5.0,
                             conditions=["in_store_only"]))
    assert not r.computable
    assert r.exclude_reason == "in_store"


def test_pct_rebate_on_claim_compounds_into_with_rebates():
    # "70% off (list), plus 10% rebate" (no prices quoted)
    r = compute_savings(deal(pct_off=70.0, rebates=["10% rebate"]))
    assert r.pct_at_checkout == approx(70.0)
    assert r.pct_with_rebates == approx(73.0)     # 1 - 0.3*0.9
    assert "rebate_dependent" in r.flags
    assert r.basis == "list_price_claim"


def test_claim_with_deferred_portal_cashback():
    # "20% off + 10% portal cashback"
    r = compute_savings(deal(pct_off=20.0,
                             deferred_value="10% portal cashback"))
    assert r.pct_at_checkout == approx(20.0)      # cashback never enters pct
    assert "deferred_value" in r.flags


def test_completely_empty_deal_is_no_basis():
    # "Check out this deal!" (nothing extractable)
    r = compute_savings(deal())
    assert not r.computable
    assert r.exclude_reason == "no_basis"


def test_no_rebates_means_equal_pcts_and_no_flag():
    # "reg $100 now $30", nothing else
    r = compute_savings(deal(regular_price=100.0, final_price=30.0))
    assert r.pct_at_checkout == approx(70.0)
    assert r.pct_with_rebates == approx(70.0)
    assert "rebate_dependent" not in r.flags
    assert r.flags == []


def test_stated_abs_savings_is_never_trusted():
    # "70% off — save $35!" (no prices). The LLM's abs_savings carries no
    # verbatim-quote evidence, so the engine must NOT pass it through: it
    # feeds the min_abs_savings publish gate and the ranking score.
    r = compute_savings(deal(pct_off=70.0, abs_savings=35.0))
    assert r.pct_at_checkout == approx(70.0)
    assert r.abs_savings is None                  # derived-or-nothing
    assert r.basis == "list_price_claim"
