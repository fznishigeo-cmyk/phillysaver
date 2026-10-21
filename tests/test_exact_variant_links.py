"""Operator 2026-10-21: a size/colour child opens on Amazon as itself."""
from copy import deepcopy
from urllib.parse import parse_qsl, urlsplit

import pytest
import yaml

from dealradar.config import AppConfig, load_config
from dealradar.publish import site
from dealradar.publish.link_facts import derive_link_facts, exact_variant, routing_key
from dealradar.publish.link_facts import routing_policy as link_policy
from tests.history_pins import pre_checker_fixes_1021, pre_exact_variant_links, pre_tools_new_tab
from tests.test_publish_site import NOW, make_deal

TAG = 'wowpriced-20'
ASIN = 'B0SHOE0008'
CHILD = {'asin': ASIN, 'final': 5.91, 'at': '2026-10-09T13:00:00+00:00', 'buybox': 5.91,
         'amazon': None, 'new': 5.91, 'variation_child': True}


def shoe(**overrides):
    return make_deal(**{'dedupe_key': 'amazon|shoe', 'store': 'Amazon', 'item': 'Girls sneaker',
                        'canonical_url': f'https://www.amazon.com/dp/{ASIN}', 'asin': ASIN,
                        'final_price': 5.91, 'codes': [], 'click_price_check': dict(CHILD),
                        **overrides})


def url_for(row, on=True, **policy):
    return derive_link_facts(row, link_policy(TAG, exact_variant_links=on, **policy)).url


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_boolean(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump({'affiliate': {'exact_variant_links': value}}))
    with pytest.raises(ValueError, match='affiliate.exact_variant_links must be a boolean'):
        load_config(path)


def test_default_off_shipped_on_and_history_pins(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).affiliate_exact_variant_links is AppConfig().affiliate_exact_variant_links is False
    assert load_config().affiliate_exact_variant_links is True
    for pin in (pre_exact_variant_links, pre_checker_fixes_1021, pre_tools_new_tab):
        assert pin(load_config()).affiliate_exact_variant_links is False


def test_variation_child_opens_exact_size_with_our_tag_once():
    row = shoe()
    before = deepcopy(row)
    url = url_for(row)
    assert url == f'https://www.amazon.com/dp/{ASIN}?th=1&psc=1&tag={TAG}'
    query = parse_qsl(urlsplit(url).query)
    assert [v for k, v in query if k == 'tag'] == [TAG]
    assert derive_link_facts(row, link_policy(TAG, exact_variant_links=True)).earns
    assert row == before


@pytest.mark.parametrize('check', [None, {}, dict(CHILD, variation_child=False),
                                   dict(CHILD, asin='B0OTHER000')])
def test_other_amazon_deals_keep_todays_link(check):
    assert url_for(shoe(click_price_check=check)) == f'https://www.amazon.com/dp/{ASIN}?tag={TAG}'


def test_switch_off_keeps_todays_link():
    assert url_for(shoe(), on=False) == f'https://www.amazon.com/dp/{ASIN}?tag={TAG}'


@pytest.mark.parametrize('canon,expected', [
    (f'https://www.amazon.com/Girls-Sneaker/dp/{ASIN}/ref=sr_1_3?th=0&psc=0&tag=other-20',
     f'https://www.amazon.com/Girls-Sneaker/dp/{ASIN}/ref=sr_1_3?th=1&psc=1&tag={TAG}'),
    (f'https://amazon.com/gp/product/{ASIN}', f'https://amazon.com/gp/product/{ASIN}?th=1&psc=1&tag={TAG}'),
    ('https://www.amazon.com/dp/B0OTHER000', f'https://www.amazon.com/dp/B0OTHER000?tag={TAG}'),
    (f'https://www.amazon.com/s?k={ASIN}', f'https://www.amazon.com/s?k={ASIN}&tag={TAG}'),
    (f'https://www.walmart.com/ip/{ASIN}', f'https://www.walmart.com/ip/{ASIN}'),
])
def test_only_the_deals_own_amazon_product_page_changes(canon, expected):
    assert url_for(shoe(canonical_url=canon)) == expected


def test_asin_fallback_link_gets_exact_size_too():
    post = 'https://hip2save.com/girls-sneaker-deal'
    row = shoe(canonical_url=post, sightings=[{'source_name': 'hip2save', 'source_url': post}])
    assert url_for(row, repair_competitor_trackers=True) == \
        f'https://www.amazon.com/dp/{ASIN}?th=1&psc=1&tag={TAG}'


def test_protected_tracker_is_never_touched():
    tracker = 'https://cc.dealsofamerica.com/click/123'
    row = shoe(canonical_url=tracker)
    assert url_for(row, competitor_tracker_hosts=['cc.dealsofamerica.com']) == tracker


def test_unsafe_asin_and_bad_urls_are_left_alone():
    assert exact_variant('https://www.amazon.com/dp/B0SHOE0008', shoe(asin='B0SHOE.*08')) == \
        'https://www.amazon.com/dp/B0SHOE0008'
    assert exact_variant('http://www.amazon.com/dp/B0SHOE0008', shoe()) == 'http://www.amazon.com/dp/B0SHOE0008'
    assert exact_variant('https://[bad', shoe()) == 'https://[bad'


def test_routing_key_sees_the_variation_fact():
    assert routing_key(shoe()) != routing_key(shoe(click_price_check=None))


@pytest.mark.parametrize('reuse', [False, True])
def test_public_json_carries_the_exact_size_link(reuse):
    cfg = AppConfig(affiliate_amazon_tag=TAG, affiliate_exact_variant_links=True,
                    reuse_link_facts_enabled=reuse)
    row = shoe()
    links = derive_link_facts(row, link_policy(TAG, exact_variant_links=True)) if reuse else None
    out = site._serialize_deal(row, NOW, TAG, cfg=cfg, links=links)
    assert out['url'] == f'https://www.amazon.com/dp/{ASIN}?th=1&psc=1&tag={TAG}'
    assert out['earns']
    off = site._serialize_deal(row, NOW, TAG, cfg=AppConfig(affiliate_amazon_tag=TAG))
    assert off['url'] == f'https://www.amazon.com/dp/{ASIN}?tag={TAG}'
