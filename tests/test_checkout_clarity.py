"""Checkout facts stay complete, authoritative, private and usable offline."""

import dataclasses
import hashlib
import json
from copy import deepcopy
from html import unescape

import httpx
import pytest

from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.growth import google
from dealradar.models import ExtractedDeal
from dealradar.parse.extractor import verify_evidence
from dealradar.publish import seo, site
from dealradar.savings import compute_savings, known_shipping
from tests.test_publish_site import NOW, make_deal
from tests.test_tracking import SAMPLE, _run_node


def extracted(**changes):
    return ExtractedDeal(item="Widget", store="Shop", source_url="https://source.test/1",
                         source_name="Source", regular_price=100, final_price=20, **changes)


@pytest.mark.parametrize("shipping,price,known", [
    ("", 20, False), ("free shipping", 20, True), ("$5 shipping", 25, True),
    ("free over $35", 20, False), ("free over $15", 20, True),
    ("$5 shipping or free over $35", 25, True),
    ("shipping depends on location", 20, False),
])
def test_checkout_uses_savings_result_and_never_invents_delivered_price(shipping, price, known):
    savings = compute_savings(extracted(shipping_cost_or_threshold=shipping))
    raw = make_deal(codes=[], conditions=[], shipping_cost_or_threshold=shipping,
                    **{k: v for k, v in dataclasses.asdict(savings).items()
                       if k not in {"computable", "exclude_reason"}})
    public = site._serialize_deal(raw, NOW)
    assert public["final_price"] == price
    assert known_shipping(raw) is known
    assert public["checkout"]["shipping_known"] is known
    assert f"${price:.2f}" in public["checkout"]["now"]
    assert "before tax" in public["checkout"]["now"]
    assert ("shipping not confirmed" in public["checkout"]["now"]) is (not known)
    assert "delivered" not in json.dumps(public).lower()
    if shipping:
        assert shipping in public["checkout"]["shipping"]
    if shipping == "$5 shipping":
        assert "Shipping: $5 shipping (included)" in public["checkout"]["summary"]
    if shipping in ("", "free shipping"):
        assert not public["checkout"]["summary"]
        assert 'class="how"' not in seo.card_html(public, now=NOW)


@pytest.mark.parametrize("shipping", ["Free shipping.", "free standard shipping", "Shipping is free!", "$0.00"])
def test_unconditional_free_shipping_adds_no_card_height(shipping):
    public = site._serialize_deal(make_deal(codes=[], conditions=[], shipping_cost_or_threshold=shipping), NOW)
    assert public["checkout"]["shipping_known"] and not public["checkout"]["summary"]
    assert 'class="how"' not in seo.card_html(public, now=NOW)


@pytest.mark.parametrize("reward", ["$10 gift card back", "10% portal cashback", "$15 store credit"])
def test_later_reward_never_reduces_checkout_or_headline(reward):
    savings = compute_savings(extracted(deferred_value=reward, rebates=["$5 mail-in rebate"]))
    assert savings.final_price == 20 and savings.pct_at_checkout == 80
    assert savings.pct_with_rebates == 85
    d = site._serialize_deal(make_deal(codes=[], conditions=[], deferred_value=reward,
                            rebates=["$5 mail-in rebate"], final_price=savings.final_price,
                            pct_at_checkout=savings.pct_at_checkout, flags=savings.flags), NOW)
    assert "Pay now: $20.00" in d["checkout"]["now"]
    assert "Rebate after purchase" in d["checkout"]["summary"]
    assert "Reward after purchase" in d["checkout"]["summary"]
    assert any(reward in s and "not deducted at checkout" in s for s in d["how"])
    assert "Needs a rebate to reach this price" not in d["headline"]


@pytest.mark.parametrize("changes,text,valid", [
    ({"codes": ["SAVE10"]}, "Use code SAVE10", True),
    ({"codes": ["SAVE10"]}, "Use code save10 at checkout", True),  # codes are case-insensitive
    ({"codes": ["SAVE10"]}, "Use code SAVE100", False),
    ({"codes": ["SAVE10"]}, "Use code X-SAVE10", False),
    ({"codes": ["FAKE"]}, "No code", False),
    ({"deferred_value": "$5 store credit"}, "Get $5 store credit", True),
    ({"deferred_value": "$5 store credit"}, "Get $15 store credit", False),
    ({"deferred_value": "$5 store credit"}, "Pay $5", False),
    ({"deferred_value": "$5"}, "Pay $5", False),
    ({"deferred_value": "10% portal cashback"}, "Get 10% portal cashback", True),
])
def test_codes_and_rewards_require_verbatim_evidence(changes, text, valid):
    deal = ExtractedDeal(item="Widget", store="Shop", source_url="", source_name="", **changes)
    assert verify_evidence(deal, text)[0] is valid


@pytest.mark.parametrize("condition,text", [
    ("prime_only", "Prime members only"), ("membership_only", "Costco membership required"),
    ("subscribe_save", "Choose Subscribe & Save"), ("in_cart", "See price in cart"),
    ("new_customer", "New customers only"), ("targeted", "Selected accounts only"),
    ("multi_buy", "Buy two for this price"), ("multi_buy", "2 for $10 on select snacks"),
])
def test_requirement_conditions_need_supporting_source_wording(condition, text):
    deal = ExtractedDeal(item="Widget", store="Shop", source_url="", source_name="", conditions=[condition])
    assert verify_evidence(deal, text) == (True, "")
    assert not verify_evidence(deal, "Widget on sale")[0]


@pytest.mark.parametrize("condition,wording", [
    ("prime_only", "Prime membership required"), ("membership_only", "Membership required"),
    ("subscribe_save", "Choose Subscribe & Save"), ("in_cart", "The price drops in the cart"),
    ("new_customer", "New customers only"), ("targeted", "Price may vary by account"),
    ("multi_buy", "Multi-buy price"), ("in_store_only", "In store only"),
])
def test_conditions_survive_even_with_explicit_steps(condition, wording):
    raw = make_deal(conditions=[condition], stack_steps=["Use code SAVE10"])
    before = deepcopy(raw)
    public = site._serialize_deal(raw, NOW)
    assert wording in public["checkout"]["summary"] and wording in public["how"]
    assert "Use code SAVE10" in public["how"]
    assert site._serialize_deal(raw, NOW) == public and raw == before


def test_complete_requirements_on_cards_and_detail_pages_and_escaping(tmp_path):
    steps = [f"Step {i}: " + "follow the stated instructions " * 5 + '<last & "important">' for i in range(6)]
    codes = ["SAVE1", "SAVE2", "SAVE3", "SAVE4<&\""]
    raw = make_deal(stack_steps=steps, codes=codes, conditions=["prime_only", "subscribe_save"],
                    shipping_cost_or_threshold="free over $35", rebates=["$5 mail-in rebate"],
                    deferred_value="$10 store credit")
    db = DB(tmp_path / "test.db")
    cfg = AppConfig(support_enabled=True, seo_store_page_min_deals=1)
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=[raw])
    public = result["full_payload"]["deals"][0]
    card = seo.card_html(public, now=NOW)
    detail = (tmp_path / "site" / public["deal_path"].lstrip("/") / "index.html").read_text()
    assert all(s in public["how"] for s in steps)
    for html in (card, detail):
        visible = unescape(html)
        for text in (*steps, *codes, "Prime membership required", "Choose Subscribe & Save",
                     "free over $35", "$5 mail-in rebate", "$10 store credit", "Pay now: $10.00"):
            assert text in visible
        assert '<last & "important">' not in html and 'SAVE4<&"' not in html
        assert "not deducted at checkout" in html and "shipping not confirmed" in html
    assert '<details class="how" data-requirements><summary>' in card
    assert '<div class="how"><h2>' in detail  # full terms, no extra tap on a deal page
    assert result["checkout"]["requirement_coverage"] == 1
    assert "checkout" not in json.loads((tmp_path / "site/deals.json").read_text())["meta"]
    db.close()


def test_plain_static_card_keeps_original_markup():
    # Captured before task 078: no requirements, same content and geometry.
    plain = site._serialize_deal(make_deal(codes=[], conditions=[]), NOW)
    card = seo.card_html(plain, now=NOW)
    # A fixed hash below makes this check portable; no external fixture is needed.
    assert hashlib.sha256(card.encode()).hexdigest() == "961cfa4e3a30fc52a0195128491c35a771a5d23b0d92b45de7bbe73e65f8cf4b"


def test_metrics_exclude_locked_dead_and_flag_missing_terms():
    raw = [make_deal(codes=[], conditions=[], shipping_cost_or_threshold="free shipping"),
           make_deal(conditions=["prime_only"]),
           make_deal(codes=[], conditions=[], flags=["rebate_dependent"]),
           make_deal(status="dead", shipping_cost_or_threshold="free shipping")]
    deals = [site._serialize_deal(d, NOW) for d in raw] + [{"locked": True}]
    assert site.checkout_metrics(deals) == {"public_live": 3, "known_shipping": 1,
        "with_requirements": 2, "covered": 1, "known_shipping_share": 1 / 3,
        "requirement_coverage": .5}
    assert site.checkout_metrics([])["known_shipping_share"] is None
    assert site.checkout_metrics(deals[:1])["requirement_coverage"] is None
    partial = site._serialize_deal(make_deal(rebates=["$5 mail-in rebate"], flags=["deferred_value"]), NOW)
    assert site.checkout_metrics([partial])["requirement_coverage"] == 0


def test_shipping_only_has_no_invented_steps_and_ended_price_is_historical():
    raw = make_deal(codes=[], conditions=[], shipping_cost_or_threshold="free over $35")
    public = site._serialize_deal(raw, NOW)
    assert public["how"] == [] and public["checkout"]["summary"] == "Shipping: free over $35"
    assert "steps are in the deal post" not in seo.card_html(public, now=NOW)
    ended = site._serialize_deal(dict(raw, status="dead"), NOW)
    assert "Pay now" not in ended["checkout"]["now"]
    assert "Last checkout price" in ended["checkout"]["now"]


def test_hyphenated_gift_card_step_is_not_a_checkout_discount():
    deal = extracted(stack_steps=["10% gift-card back"])
    deal.final_price = None
    deal.pct_off = 50
    savings = compute_savings(deal)
    assert savings.final_price == 50 and savings.pct_at_checkout == 50


@pytest.mark.parametrize("flag,summary,label", [
    ("range", "Price varies by option", "Price varies; up to $10.00"),
    ("per_unit", "Price is per unit", "Price per unit: $10.00"),
])
def test_range_and_unit_flags_never_promise_an_exact_basket_total(flag, summary, label):
    public = site._serialize_deal(make_deal(codes=[], conditions=[], flags=[flag],
                                          quantity_or_unit="2-pack"), NOW)
    assert public["checkout"]["summary"] == summary
    assert public["checkout"]["now"].startswith(label)
    assert "Quantity: 2-pack" in public["how"]


@pytest.mark.parametrize("ga4", [True, False])
def test_requirement_events_are_ga4_only_and_drop_all_private_params(tmp_path, ga4):
    ids = dict(SAMPLE)
    if not ga4:
        ids.pop("ga4_measurement_id")
    private = {"code": "PRIVATE", "email": "PRIVATE@example.test", "store": "PRIVATE",
               "category": "PRIVATE", "price": 99, "url": "https://PRIVATE.test"}
    calls = [[event, {**private, "method": method}] for event in
             ("requirements_open", "requirements_deal_click") for method in ("home", "deal", "category")]
    invalid = [["requirements_open", {"method": "PRIVATE"}], ["requirements_deal_click", {}]]
    result = _run_node(tmp_path, ids, {"baseline": {}, "actions": {"calls": calls + invalid},
                                     "gpc": {"gpc": True, "calls": calls},
                                     "out": {"storage": {"wp.adsOptOut": "1"}, "calls": calls}})
    for name in ("actions", "gpc", "out"):
        assert result[name]["error"] is None
        events = [row for row in result[name]["dataLayer"] if row[0] == "event"]
        assert events == ([["event", event, {"method": params["method"], "send_to": ids["ga4_measurement_id"]}]
                           for event, params in calls] if ga4 else [])
        for tag in ("fbq", "ttq", "pintrk", "uetq"):
            assert result[name][tag] == (result["baseline"][tag] if name == "actions" else None)
    assert "PRIVATE" not in json.dumps(result)


def test_step_offer_session_rate_uses_ga4_session_metric_not_click_division():
    metric = "sessionKeyEventRate:requirements_deal_click"
    def handler(request):
        body = json.loads(request.content)["requests"][0]
        assert body["metrics"] == [{"name": "sessions"}, {"name": metric}]
        return httpx.Response(200, json={"reports": [{
            "dimensionHeaders": [{"name": "deviceCategory"}, {"name": "dateRange"}],
            "metricHeaders": [{"name": "sessions"}, {"name": metric}],
            "rows": [{"dimensionValues": [{"value": "mobile"}, {"value": "yesterday"}],
                      "metricValues": [{"value": "100"}, {"value": "0.2"}]}]}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = google.ga4_deal_sessions(client, "fake", "123", 7, requirements=True)
    assert result["yesterday"]["mobile"] == {"sessions": 100, "rate": .2}
    assert result["window"] == {}
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400))) as client:
        result = google.ga4_deal_sessions(client, "fake", "123", 7, requirements=True)
    assert "register requirements_deal_click as a key event" in result["unavailable"]



def test_card_box_never_says_a_step_twice():
    """Live card 2026-10-14: "Price is per unit" above "Price is per unit; the
    checkout total depends on quantity." The step already says it."""
    from dealradar.publish import site
    from tests.test_publish_site import make_deal
    deal = make_deal(flags=["per_unit"], codes=[])
    checkout = site._checkout(deal, True)
    assert "Price is per unit" in checkout["summary"]
    assert "Price is per unit" not in checkout["card"]
    assert any(s.startswith("Price is per unit;") for s in site._how(deal))
