"""Simple code deals earn at the store; complex instructions keep the post."""

import json
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.publish import newsletter
from dealradar.publish.site import _serialize_deal, link_mix, render_site
from tests.test_publish_site import NOW, make_deal
from tests.test_sovrn_links import KEY

TAG = "our-store-20"
POST = "https://slickdeals.net/f/1"
AMAZON = "https://www.amazon.com/dp/B012345678"


def publish(deal, **kwargs):
    return _serialize_deal(deal, NOW, TAG, sovrn_key=KEY, sovrn_enabled=True, **kwargs)


@pytest.mark.parametrize("steps", [
    [], ["Use code SAVE10"], ["Enter code SAVE10"], ["Apply code SAVE10"],
    ["Apply SAVE10"], ['use the coupon code "SAVE10" at checkout.'],
    ["Enter promo code: 'save10'!"],
    ["Use code SAVE10", "Apply discount code EXTRA5"],
])
def test_simple_walmart_code_earns_and_keeps_card_facts(steps):
    deal = make_deal(codes=["SAVE10", "EXTRA5"], stack_steps=steps)
    before = deepcopy(deal)
    baseline = _serialize_deal(deal, NOW)
    out = publish(deal)
    query = parse_qs(urlsplit(out["url"]).query)
    assert urlsplit(out["url"]).hostname == "redirect.viglink.com"
    assert query == {"key": [KEY], "u": [deal["canonical_url"]], "cuid": [out["key"]]}
    assert dict(out, url=baseline["url"]) == baseline
    assert out["needs_steps"] and out["codes"] == ["SAVE10", "EXTRA5"]
    assert out["credits"][0]["url"] == POST
    assert publish(deal) == out and deal == before


@pytest.mark.parametrize("enabled,key", [(True, KEY), (False, KEY), (True, "")])
def test_simple_amazon_code_keeps_tag_even_without_sovrn(enabled, key):
    deal = make_deal(canonical_url=AMAZON, codes=["SAVE10"], stack_steps=["Use code SAVE10"])
    out = _serialize_deal(deal, NOW, TAG, sovrn_key=key, sovrn_enabled=enabled)
    assert out["url"] == AMAZON + "?tag=" + TAG
    assert out["needs_steps"] and out["how"] == ["Use code SAVE10"]
    assert out["credits"][0]["url"] == POST
    assert _serialize_deal(deal, NOW, sovrn_key=key, sovrn_enabled=enabled)["url"] == POST


@pytest.mark.parametrize("url", [
    "https://www.amazon.co.uk/dp/B012345678", "https://a.co/short",
    "https://hip2save.com/product", "https://slickdeals.net/product",
    "http://www.walmart.com/ip/12345", "",
])
def test_simple_code_without_earning_store_link_keeps_post(url):
    assert publish(make_deal(canonical_url=url))["url"] == POST


@pytest.mark.parametrize("enabled,key", [(False, KEY), (True, ""), (True, "bad")])
def test_sovrn_off_or_invalid_keeps_walmart_post(enabled, key):
    assert _serialize_deal(make_deal(), NOW, TAG, sovrn_key=key,
                           sovrn_enabled=enabled)["url"] == POST


COMPLEX = [
    {"rebates": ["$5 mail-in rebate"]}, {"flags": ["rebate_dependent"]},
    {"stack_steps": ["Clip the coupon", "Apply code SAVE10"]},
    {"stack_steps": ["Join the rewards program", "Use code SAVE10"]},
    {"stack_steps": ["Use code SAVE10 and buy two"]},
    {"stack_steps": ["Enter code OTHER10"]},
    {"stack_steps": ["Apply code SAVE100"]},
    *({"conditions": [condition]} for condition in (
        "subscribe_save", "in_cart", "new_customer", "targeted", "ymmv",
        "multi_buy", "in_store_only", "clearance_aisle")),
]


@pytest.mark.parametrize("extra", COMPLEX)
def test_amazon_deals_with_any_steps_keep_our_tag(extra):
    """Operator, 2026-10-12 (a1294fe): an Amazon deal with steps goes to
    Amazon with our tag (or its override); the card keeps the steps."""
    deal = make_deal(canonical_url=AMAZON, **extra)
    out = publish(deal)
    assert out["url"] == AMAZON + "?tag=" + TAG and out["needs_steps"]
    assert out["credits"][0]["url"] == POST
    overrides = {"amazon.com/B012345678": "https://sovrn.co/amazon-override"}
    assert publish(deal, overrides=overrides)["url"] == overrides["amazon.com/B012345678"]
    assert _serialize_deal(deal, NOW, "", sovrn_key=KEY, sovrn_enabled=True)["url"] == POST


@pytest.mark.parametrize("url", ["https://www.walmart.com/ip/12345"])
@pytest.mark.parametrize("extra", COMPLEX)
def test_each_complex_reason_keeps_post_even_with_earning_link_or_override(url, extra):
    deal = make_deal(canonical_url=url, **extra)
    overrides = {"walmart.com/12345": "https://sovrn.co/override",
                 "amazon.com/B012345678": "https://sovrn.co/amazon-override"}
    assert publish(deal)["url"] == POST
    assert publish(deal, overrides=overrides)["url"] == POST


def test_clip_coupon_without_code_stays_on_post():
    assert publish(make_deal(codes=[], stack_steps=["Clip the coupon"]))["url"] == POST


def test_override_is_earning_and_wins_even_with_sovrn_off():
    overrides = {"walmart.com/12345": "https://sovrn.co/override"}
    for enabled in (False, True):
        out = _serialize_deal(make_deal(), NOW, TAG, overrides,
                              sovrn_key=KEY, sovrn_enabled=enabled)
        assert out["url"] == overrides["walmart.com/12345"]
        assert out["needs_steps"] and out["credits"][0]["url"] == POST


def test_link_mix_moves_simple_codes_from_posts_to_earning_stores():
    deals = [make_deal(), make_deal(canonical_url=AMAZON),
             make_deal(stack_steps=["Clip the coupon", "Use code SAVE10"])]
    baseline = link_mix([_serialize_deal(deal, NOW) for deal in deals], TAG)
    earned = link_mix([publish(deal) for deal in deals], TAG)
    assert baseline["source_post"] == 3
    assert earned["source_post"] == earned["sovrn"] == earned["amazon_tagged"] == 1


def test_render_keeps_locked_stubs_emails_code_copy_and_credit(tmp_path):
    deals = [make_deal(dedupe_key="visible", score=100),
             make_deal(dedupe_key="locked", canonical_url="https://www.walmart.com/ip/2", score=90)]
    before = deepcopy(deals)
    email_rows = [newsletter._row_html(deal) for deal in deals]
    payloads = []
    for enabled in (False, True):
        cfg = AppConfig(affiliate_sovrn_key=KEY, affiliate_sovrn_enabled=enabled,
                        paywall_enabled=True, paywall_open_access=False, paywall_teaser_count=1)
        out_dir = tmp_path / str(enabled)
        db = DB(tmp_path / f"{enabled}.db")
        result = render_site(db, cfg, NOW, out_dir=out_dir, deals=deals)
        db.close()
        payloads.append(json.loads((out_dir / "deals.json").read_text()))
        assert result["link_mix"]["sovrn"] == int(enabled)
        assert result["link_mix"]["source_post"] == int(not enabled)
    off, on = payloads
    assert on["deals"][1] == off["deals"][1] and on["deals"][1]["locked"]
    assert dict(on["deals"][0], url=off["deals"][0]["url"]) == off["deals"][0]
    home = (tmp_path / "True/index.html").read_text()
    assert '<code class="code">SAVE10</code>' in home
    assert 'navigator.clipboard.writeText' in home and POST in home
    assert [newsletter._row_html(deal) for deal in deals] == email_rows
    assert [newsletter._deal_url(deal) for deal in deals] == [deal["canonical_url"] for deal in deals]
    assert newsletter._deal_url(make_deal(canonical_url=AMAZON)) == AMAZON
    assert deals == before
