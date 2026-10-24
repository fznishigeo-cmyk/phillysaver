"""Offline regressions for the opt-in accuracy-lite savings rules."""

import logging
from dataclasses import astuple

import pytest

from dealradar.models import Evidence, ExtractedDeal
from dealradar.savings import compute_savings, parse_shipping, parse_stack_step


def deal(**kwargs):
    return ExtractedDeal(item="Widget", store="Test", source_name="test",
                         source_url="https://example.com/deal", **kwargs)


def test_s1_subscription_price_is_unknown_and_logged(caplog):
    with caplog.at_level(logging.DEBUG, logger="dealradar.savings"):
        assert parse_stack_step("Subscribe & Save $8.49", accuracy_lite=True) == {
            "kind": "unknown", "value": None}
    assert "unknown stack step" in caplog.text


def test_s2_minimum_spend_below_threshold_is_excluded():
    result = compute_savings(deal(regular_price=20, stack_steps=["$5 off $25"]), accuracy_lite=True)
    assert not result.computable
    assert result.exclude_reason == "minimum_spend"
    assert result.final_price is None


def test_s3_reward_in_rebates_is_deferred():
    result = compute_savings(deal(regular_price=100, final_price=50,
                                  rebates=["$10 Kohl's Cash"]), accuracy_lite=True)
    assert result.pct_with_rebates == 50
    assert result.flags == ["deferred_value"]


def test_s4_free_two_day_shipping_has_no_charge():
    assert parse_shipping("Free 2-day shipping", accuracy_lite=True) == (0.0, None)


@pytest.mark.parametrize(("wording", "kind", "value"), [
    ("Subscribe and save an extra $2", "abs_off", 2),
    ("Subscribe & Save an additional $5", "abs_off", 5),
    ("Subscribe & Save: an extra $5", "abs_off", 5),
    ("S&S save $2", "abs_off", 2),
    ("extra 5% off with Subscribe & Save", "pct_off", 5),
    ("Subscribe & Save 15%", "pct_off", 15),
])
def test_s1_explicit_subscription_discounts_apply(wording, kind, value):
    assert parse_stack_step(wording, accuracy_lite=True) == {"kind": kind, "value": value}
    result = compute_savings(deal(regular_price=100, stack_steps=[wording]),
                             accuracy_lite=True)
    assert result.final_price == 100 - value


@pytest.mark.parametrize("wording", ["Subscribe & Save $8.49", "Subscribe and Save $8.49",
                                     "S&S $8.49"])
def test_s1_subscription_price_does_not_supply_discount(wording):
    result = compute_savings(deal(regular_price=20, stack_steps=[wording]),
                             accuracy_lite=True)
    assert not result.computable and result.exclude_reason == "no_basis"
    assert result.final_price is None


@pytest.mark.parametrize(("wording", "minimum", "discount"), [
    ("$5 off $25", 25, 5),
    ("$5 off orders of $25+", 25, 5),
    ("$100 off orders of $1,000+", 1000, 100),
    ("$2 off $10 order", 10, 2),
    ("Save $10 on $50+", 50, 10),
    ("Save $10, minimum purchase $50", 50, 10),
])
@pytest.mark.parametrize("difference", [-0.01, 0, 0.01])
def test_s2_minimum_spend_boundaries(wording, minimum, discount, difference):
    price = minimum + difference
    result = compute_savings(deal(regular_price=price, stack_steps=[wording]),
                             accuracy_lite=True)
    if difference < 0:
        assert not result.computable and result.exclude_reason == "minimum_spend"
        assert result.final_price is None and result.pct_at_checkout is None
    else:
        assert result.computable
        assert result.final_price == round(price - discount, 2)


@pytest.mark.parametrize(("wording", "discount", "minimum"), [
    ("$100 off orders of $1,000+", 100, 1000),
    ("$1,000 off orders of $2,500+", 1000, 2500),
    ("Save $10, minimum purchase $50", 10, 50),
    ("$5 off $25, today only", 5, 25),
])
def test_s2_commas_in_amounts_and_punctuation(wording, discount, minimum):
    assert parse_stack_step(wording, accuracy_lite=True) == {
        "kind": "abs_off", "value": discount, "minimum_spend": minimum}


@pytest.mark.parametrize("price", [None, 24.99, 25, 30])
@pytest.mark.parametrize("is_range", [False, True])
def test_s2_stated_price_and_evidenced_range_preserve_price(price, is_range):
    kwargs = {"regular_price": 100, "stack_steps": ["$5 off $25"]}
    if is_range:
        kwargs.update(price_is_range=True, price_upper_bound=price,
                      evidence=Evidence(price_upper_bound_quote=f"up to ${price}"))
    else:
        kwargs.update(final_price=price)
    result = compute_savings(deal(**kwargs), accuracy_lite=True)
    if is_range and price is None:
        assert not result.computable and result.exclude_reason == "range_no_upper"
    elif price is not None and price < 25:
        assert not result.computable and result.exclude_reason == "minimum_spend"
        assert result.final_price is None
    else:
        assert result.computable
        assert result.final_price == (95 if price is None else price)
        assert result.flags == (["range"] if is_range else [])


@pytest.mark.parametrize("kwargs", [{}, {"pct_off": 50}, {"deal_type": "bogo"}])
def test_s2_minimum_without_price_cannot_establish_eligibility(kwargs):
    result = compute_savings(deal(stack_steps=["$5 off $25"], **kwargs),
                             accuracy_lite=True)
    assert not result.computable and result.exclude_reason == "minimum_spend"
    assert result.final_price is None


def test_s2_minimum_uses_running_total_in_stated_order():
    result = compute_savings(deal(regular_price=100,
                                  stack_steps=["sale price $20", "$5 off $25"]),
                             accuracy_lite=True)
    assert not result.computable and result.exclude_reason == "minimum_spend"


def test_s2_minimum_rounds_running_total_to_cents():
    result = compute_savings(deal(regular_price=50,
                                  stack_steps=["80% off", "$2 off $10 order"]),
                             accuracy_lite=True)
    assert result.computable and result.final_price == 8
    assert result.pct_at_checkout == 84


@pytest.mark.parametrize(("field", "wording"), [
    ("rebates", "$10 Kohl's Cash"), ("rebates", "$5 gift card back"),
    ("rebates", "10% back in rewards"), ("rebates", "$10 store credit"),
    ("rebates", "store credit"), ("stack_steps", "$5 gift card back"),
    ("stack_steps", "$10 store credit"), ("stack_steps", "store credit"),
])
def test_s3_reward_wording_never_reduces_checkout_or_rebate_pct(wording, field):
    result = compute_savings(deal(regular_price=100, final_price=50, **{field: [wording]}),
                             accuracy_lite=True)
    assert result.final_price == 50
    assert result.pct_at_checkout == result.pct_with_rebates == 50
    assert result.flags == ["deferred_value"]


def test_s3_cash_rebate_keeps_its_existing_math():
    result = compute_savings(deal(regular_price=100, final_price=50,
                                  rebates=["$10 mail-in rebate"]), accuracy_lite=True)
    assert result.pct_at_checkout == 50 and result.pct_with_rebates == 60
    assert result.flags == ["rebate_dependent"]


@pytest.mark.parametrize(("wording", "expected"), [
    ("extra $5", {"kind": "unknown", "value": None}),
    ("additional $5", {"kind": "unknown", "value": None}),
    ("$10 Kohl's Cash", {"kind": "unknown", "value": None}),
    ("10% back in rewards", {"kind": "pct_off", "value": 10}),
])
def test_on_keeps_legacy_classification_outside_requested_context(wording, expected):
    assert parse_stack_step(wording, accuracy_lite=True) == expected


@pytest.mark.parametrize("wording", ["Free 2-day shipping", "FREE One-Day Delivery"])
def test_s4_free_delivery_never_adds_speed_to_price(wording):
    assert parse_shipping(wording, accuracy_lite=True) == (0, None)
    result = compute_savings(deal(regular_price=100, final_price=50,
                                  shipping_cost_or_threshold=wording), accuracy_lite=True)
    assert result.final_price == 50 and result.pct_at_checkout == 50


def test_s4_free_delivery_retains_minimum_shipping_threshold():
    assert parse_shipping("Free 2-day shipping over $35", accuracy_lite=True) == (None, 35)
    result = compute_savings(deal(regular_price=100, final_price=20,
                                  shipping_cost_or_threshold="Free 2-day shipping over $35"),
                             accuracy_lite=True)
    assert result.final_price == 20 and result.flags == ["unknown_shipping"]


@pytest.mark.parametrize(("wording", "kwargs", "final", "pct", "reason", "flags"), [
    ("Now $19.99 (was $49.99)", dict(regular_price=49.99, final_price=19.99),
     19.99, 60, "", []),
    ("$19.99 (reg. $29.99)", dict(regular_price=29.99, final_price=19.99),
     19.99, 33.3, "", []),
    ("Sale $12.99 + free shipping over $35", dict(final_price=12.99,
                                                shipping_cost_or_threshold="free shipping over $35"),
     None, None, "no_basis", []),
    ("$5 each", dict(final_price=5, quantity_or_unit="each"), None, None, "no_basis", []),
    ("2-pack for $9.99", dict(final_price=9.99, quantity_or_unit="2-pack"),
     None, None, "no_basis", []),
    ("Pay $25 for $50 worth", dict(regular_price=50, final_price=25), 25, 50, "", []),
    ("Pringles Original $1.49 (was $2.99)", dict(regular_price=2.99, final_price=1.49),
     1.49, 50.2, "", []),
    ("reg $100 sale price $50 code TAKE10 saves $10", dict(regular_price=100,
            stack_steps=["sale price $50", "code TAKE10 saves $10"]), 40, 60, "", []),
    ("$3.99/lb", dict(regular_price=7.99, final_price=3.99,
                      regular_price_unit="per_unit", final_price_unit="per_unit"),
     3.99, 50.1, "", ["per_unit"]),
    ("from $9.99", dict(regular_price=30, final_price=9.99, price_is_range=True),
     None, None, "range_no_upper", []),
    ("Clip 20% coupon then Subscribe & Save", dict(regular_price=50,
              stack_steps=["Clip 20% coupon", "Subscribe & Save"]), 40, 20, "", []),
    ("$10 off $50 order on $60", dict(regular_price=60, stack_steps=["$10 off $50 order"]),
     50, 16.7, "", []),
    ("$5 off coupon", dict(regular_price=20, stack_steps=["$5 off coupon"]), 15, 25, "", []),
])
def test_must_not_regress_corpus(wording, kwargs, final, pct, reason, flags):
    result = compute_savings(deal(**kwargs), accuracy_lite=True)
    assert result.computable == (not reason), wording
    assert (result.final_price, result.pct_at_checkout, result.exclude_reason, result.flags) == (
        final, pct, reason, flags)


# Full SavingsResult tuples captured from pristine commit 4841f40, before this
# feature. OFF and omitted switches must reproduce those exact legacy outputs.
@pytest.mark.parametrize(("kwargs", "expected"), [
    (dict(regular_price=20, stack_steps=["Subscribe & Save $8.49"]),
     (True, 11.51, 20, 42.4, 42.4, 8.49, "stack", [], "")),
    (dict(regular_price=20, stack_steps=["$5 off $25"]),
     (True, 15, 20, 25, 25, 5, "stack", [], "")),
    (dict(regular_price=20, stack_steps=["$5 off orders of $25+"]),
     (True, 15, 20, 25, 25, 5, "stack", [], "")),
    (dict(regular_price=500, stack_steps=["$100 off orders of $1,000+"]),
     (True, 400, 500, 20, 20, 100, "stack", [], "")),
    (dict(regular_price=9, stack_steps=["$2 off $10 order"]),
     (True, 7, 9, 22.2, 22.2, 2, "stack", [], "")),
    (dict(regular_price=20, stack_steps=["Save $10 on $50+"]),
     (True, 10, 20, 50, 50, 10, "stack", [], "")),
    (dict(regular_price=100, stack_steps=["Save $10, minimum purchase $50"]),
     (True, 90, 100, 10, 10, 10, "stack", [], "")),
    (dict(regular_price=100, final_price=50, rebates=["$10 Kohl's Cash"]),
     (True, 50, 100, 50, 60, 50, "quoted_regular", ["rebate_dependent"], "")),
    (dict(regular_price=100, final_price=50, rebates=["$5 gift card back"]),
     (True, 50, 100, 50, 55, 50, "quoted_regular", ["rebate_dependent"], "")),
    (dict(regular_price=100, final_price=50, rebates=["10% back in rewards"]),
     (True, 50, 100, 50, 55, 50, "quoted_regular", ["rebate_dependent"], "")),
    (dict(regular_price=100, final_price=50, rebates=["$10 store credit"]),
     (True, 50, 100, 50, 60, 50, "quoted_regular", ["rebate_dependent"], "")),
    (dict(regular_price=100, final_price=50, shipping_cost_or_threshold="Free 2-day shipping"),
     (True, 52, 100, 48, 48, 48, "quoted_regular", [], "")),
    (dict(regular_price=100, final_price=50, shipping_cost_or_threshold="FREE One-Day Delivery"),
     (True, 50, 100, 50, 50, 50, "quoted_regular", [], "")),
])
def test_switch_off_preserves_pristine_legacy_snapshot(kwargs, expected):
    assert astuple(compute_savings(deal(**kwargs), accuracy_lite=False)) == expected
    assert astuple(compute_savings(deal(**kwargs))) == expected
