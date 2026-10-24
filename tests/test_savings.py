"""Tests for the pure-code savings engine (dealradar/savings.py).

Every scenario carries the original real-world wording as a comment and builds
the ExtractedDeal the way the parse layer would: RAW VALUES ONLY (stated
prices, stated pct, stack steps as text, shipping text, deal-type markers).
All math is asserted against SAVINGS_RULES.md. Fully offline, no fixtures.
"""

from __future__ import annotations

import pytest

from dealradar.models import Evidence, ExtractedDeal
from dealradar.parse.extractor import verify_evidence
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


def test_evidenced_code_saves_dollars_after_sale_price():
    text = "reg $100; sale price $50; code TAKE10 saves $10"
    candidate = deal(regular_price=100,
                     stack_steps=["sale price $50", "code TAKE10 saves $10"],
                     codes=["TAKE10"],
                     evidence=Evidence(regular_price_quote="reg $100"))
    assert verify_evidence(candidate, text) == (True, "")
    result = compute_savings(candidate)
    assert result.computable
    assert result.final_price == 40
    assert result.pct_at_checkout == 60
    assert result.abs_savings == 60
    assert result.basis == "stack"


@pytest.mark.parametrize("step", ["Subscribe & Save $8.49", "Subscribe and Save $8.49"])
def test_subscribe_save_price_is_not_an_absolute_discount(step):
    assert parse_stack_step(step) == {"kind": "unknown", "value": None}
    result = compute_savings(deal(regular_price=12.99, stack_steps=[step]))
    assert not result.computable and result.exclude_reason == "no_basis"


@pytest.mark.parametrize("step", ["$5 off $25", "$5 off orders of $25+"])
@pytest.mark.parametrize("regular,eligible", [(12.99, False), (24.99, False),
                                               (25, True), (30, True)])
def test_coupon_minimum_spend_is_required(step, regular, eligible):
    result = compute_savings(deal(regular_price=regular, stack_steps=[step]))
    assert result.computable is eligible
    if eligible:
        assert result.final_price == approx(regular - 5)


def test_coupon_minimum_spend_uses_the_running_item_price():
    result = compute_savings(deal(regular_price=50,
                                 stack_steps=["sale price $20", "$5 off $25"]))
    assert not result.computable


def test_verified_range_upper_bound_is_stated_final_for_minimum_spend():
    text = "50% off; checkout prices under $60 with $10 off $50"
    candidate = deal(pct_off=50, price_is_range=True, price_upper_bound=60,
                     stack_steps=["$10 off $50"],
                     evidence=Evidence(pct_quote="50% off",
                                       price_upper_bound_quote="under $60"))
    assert verify_evidence(candidate, text) == (True, "")
    result = compute_savings(candidate)
    assert result.computable
    assert result.final_price == 60
    assert result.pct_at_checkout == 50
    assert result.abs_savings is None
    assert result.basis == "list_price_claim"
    assert "range" in result.flags
    assert candidate.final_price is None


@pytest.mark.parametrize("range_fields", [
    {}, {"price_is_range": True}, {"price_upper_bound": 60},
])
def test_minimum_spend_without_stated_final_or_flagged_upper_bound_stays_excluded(range_fields):
    result = compute_savings(deal(pct_off=50, stack_steps=["$10 off $50"],
                                 **range_fields))
    assert not result.computable
    assert result.exclude_reason == "minimum_spend"


@pytest.mark.parametrize("regular,eligible", [(50, True), (49.95, False)])
def test_coupon_minimum_spend_uses_cents_at_percentage_boundary(regular, eligible):
    result = compute_savings(deal(regular_price=regular,
                                 stack_steps=["80% off", "$2 off $10 order"]))
    assert result.computable is eligible
    if eligible:
        assert result.final_price == 8.00
        assert result.pct_at_checkout == 84.0
    else:
        assert result.exclude_reason == "minimum_spend"


@pytest.mark.parametrize("step,expected", [
    ("Subscribe and save an extra $2", 10.99),
    ("Subscribe & Save an extra $2", 10.99),
    ("Subscribe and save $2 off", 10.99),
    ("Subscribe & Save: save $2", 10.99),
    ("Save $2 with Subscribe & Save", 10.99),
    ("$2 off with Subscribe & Save", 10.99),
    ("Extra 20% off with Subscribe & Save", 10.39),
    ("Subscribe & Save extra 20% off", 10.39),
])
def test_subscription_explicit_extra_discount_is_applied(step, expected):
    candidate = deal(regular_price=12.99, stack_steps=[step], conditions=["subscribe_save"])
    result = compute_savings(candidate)
    assert result.computable
    assert result.final_price == expected
    assert result.basis == "stack"
    assert candidate.conditions == ["subscribe_save"]


@pytest.mark.parametrize("regular,eligible", [(999.99, False), (1000, True), (1200, True)])
def test_coupon_minimum_spend_keeps_thousands_separator(regular, eligible):
    step = "$100 off orders of $1,000+"
    assert parse_stack_step(step) == {"kind": "abs_off", "value": 100.0,
                                      "min_spend": 1000.0}
    result = compute_savings(deal(regular_price=regular, stack_steps=[step]))
    assert result.computable is eligible
    if eligible:
        assert result.final_price == regular - 100
    else:
        assert result.exclude_reason == "minimum_spend"


def test_coupon_discount_keeps_thousands_separator():
    assert parse_stack_step("$1,000 off orders of $2,000+") == {
        "kind": "abs_off", "value": 1000.0, "min_spend": 2000.0}


def test_clip_percentage_coupon_then_subscription_name():
    result = compute_savings(deal(regular_price=50,
                                 stack_steps=["Clip 20% coupon, then Subscribe & Save"]))
    assert result.computable
    assert result.final_price == 40
    assert result.pct_at_checkout == 20


def test_order_coupon_on_item_above_minimum():
    result = compute_savings(deal(regular_price=60, stack_steps=["$10 off $50 order"]))
    assert result.computable
    assert result.final_price == 50
    assert result.pct_at_checkout == 16.7


@pytest.mark.parametrize("step", ["Subscribe & Save $2 off", "save $2", "$2 off"])
def test_explicit_subscription_discount_still_subtracts(step):
    result = compute_savings(deal(regular_price=12.99, stack_steps=[step]))
    assert result.final_price == approx(10.99)


@pytest.mark.parametrize("reward", ["$10 gift card back", "10% portal cashback",
                                   "$15 store credit", "$10 gift card rebate"])
def test_deferred_reward_misfiled_as_rebate_never_changes_savings(reward):
    result = compute_savings(deal(regular_price=50, final_price=50, rebates=[reward]))
    assert result.final_price == 50
    assert result.pct_at_checkout == result.pct_with_rebates == 0
    assert "deferred_value" in result.flags
    assert "rebate_dependent" not in result.flags


@pytest.mark.parametrize("shipping,cost,threshold", [
    ("Free 2-day shipping", 0, None),
    ("Free 2 day shipping", 0, None),
    ("Free 2 business day shipping", 0, None),
    ("Free 2–3 day shipping", 0, None),
    ("Free 2-day shipping over $35", None, 35),
    ("2-day shipping $6.99", 6.99, None),
    ("$6.99 2-day shipping or free over $50", 6.99, 50),
])
def test_delivery_duration_is_not_a_shipping_charge(shipping, cost, threshold):
    assert parse_shipping(shipping) == (cost, threshold)
    result = compute_savings(deal(regular_price=100, final_price=20,
                                 shipping_cost_or_threshold=shipping))
    assert result.final_price == approx(20 + (cost or 0))


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


def test_h_free_after_rebate_is_100_with_rebates_only():
    # "Software $9.99, FREE after rebate": you still PAY $9.99 at checkout,
    # so the checkout pct is unknown without a regular price — 100% lives in
    # pct_with_rebates alone. (Live bug: a $254.98 phone published "100% off".)
    r = compute_savings(deal(deal_type="free_after_rebate", final_price=9.99,
                             rebates=["$9.99 mail-in rebate"]))
    assert r.computable
    assert r.pct_at_checkout is None
    assert r.pct_with_rebates == approx(100.0)
    assert "rebate_dependent" in r.flags
    assert r.basis == "free_after_rebate"


def test_h2_free_after_rebate_checkout_pct_from_stated_prices():
    # Regular stated -> checkout pct is the honest paid-at-register math.
    r = compute_savings(deal(deal_type="free_after_rebate", final_price=9.99,
                             regular_price=19.99,
                             rebates=["$9.99 mail-in rebate"]))
    assert r.pct_at_checkout == approx((19.99 - 9.99) / 19.99 * 100.0)
    assert r.pct_with_rebates == approx(100.0)


def test_i_moneymaker_exceeds_100_when_numbers_allow():
    # "Razor $5 (reg $5), $8 mail-in rebate — moneymaker!"
    r = compute_savings(deal(deal_type="moneymaker", regular_price=5.0,
                             final_price=5.0, rebates=["$8 mail-in rebate"]))
    assert r.pct_at_checkout == approx(0.0)       # you pay full price today
    assert r.pct_with_rebates == approx(160.0)    # (5 - (5-8)) / 5
    assert "rebate_dependent" in r.flags
    assert r.basis == "free_after_rebate"


def test_i2_moneymaker_without_numbers_is_100():
    # "Moneymaker at CVS this week!" (no usable numbers)
    r = compute_savings(deal(deal_type="moneymaker"))
    assert r.pct_at_checkout is None              # nothing checkout-computable
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


@pytest.mark.parametrize('final,listed,pct,saved', [(9.99, 24.99, 60.0, 15.0),
                                                 (37.5, 100, 62.5, 62.5),
                                                 (0, 25, 100, 25)])
def test_amazon_list_price_rounding(final, listed, pct, saved):
    from datetime import datetime, timezone
    from dealradar.savings import amazon_list_price, display_pct
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    reading = dict(asin='B012345678', price=listed, observed_at=now.isoformat())
    result = amazon_list_price(final, reading, asin='B012345678', confirmed_at=now,
                               now=now, max_age_h=12)
    assert result.computable and result.basis == 'amazon_list_price'
    assert result.regular_price == listed and result.final_price == final
    assert result.pct_at_checkout == result.pct_with_rebates == pct
    assert result.abs_savings == saved
    assert display_pct(result.pct_at_checkout) == int(pct + .5)


@pytest.mark.parametrize('listed,offset,age,asin', [
    (10, 0, 0, 'B012345678'), (9.99, 0, 0, 'B012345678'),
    (None, 0, 0, 'B012345678'), (float('nan'), 0, 0, 'B012345678'),
    (float('inf'), 0, 0, 'B012345678'), (True, 0, 0, 'B012345678'),
    (25, -1, 0, 'B012345678'), (25, 0, 13, 'B012345678'),
    (25, 0, -1, 'B012345678'), (25, 0, 0, 'B000000002'),
])
def test_amazon_list_price_requires_above_final_and_same_fresh_reading(listed, offset, age, asin):
    from datetime import datetime, timedelta, timezone
    from dealradar.savings import amazon_list_price
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    confirmed = now - timedelta(hours=age)
    reading = dict(asin=asin, price=listed, observed_at=(confirmed + timedelta(minutes=offset)).isoformat())
    result = amazon_list_price(10, reading, asin='B012345678', confirmed_at=confirmed,
                               now=now, max_age_h=12)
    assert not result.computable and result.exclude_reason == 'no_basis'
    assert result.regular_price is result.pct_at_checkout is result.abs_savings is None
