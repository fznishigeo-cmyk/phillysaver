"""Operator 2026-10-22: "Add to Amazon cart" beside the Amazon button (ships OFF)."""
import json
import re
import subprocess

import pytest
import yaml

from dealradar.config import AppConfig, load_config, validated_add_to_cart
from dealradar.db import DB
from dealradar.publish import add_to_cart, deal_spotter, site
from dealradar.publish.site import render_site
from tests.history_pins import pre_add_to_cart, pre_hide_timer_ticker, pre_task_319_raw
from tests.test_home_comment_strip import files
from tests.test_publish_site import NOW, make_deal
from tests.test_support import NOW as SUPPORT_NOW, render

TAG = 'wowpriced-20'
CHILD = 'B0SHOE0008'
CART = f'https://www.amazon.com/gp/aws/cart/add.html?ASIN.1={CHILD}&Quantity.1=1&AssociateTag={TAG}'


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(site, 'util_now', lambda: NOW)


def amazon(**overrides):
    return make_deal(**{'dedupe_key': 'amazon|shoe', 'store': 'Amazon', 'item': 'Girls sneaker', 'codes': [],
                        'canonical_url': f'https://www.amazon.com/dp/{CHILD}', 'asin': CHILD, 'conditions': [],
                        'click_price_check': {'asin': CHILD, 'final': 10.0, 'at': '2026-10-09T13:00:00+00:00',
                                              'buybox': 10.0, 'amazon': None, 'new': 10.0, 'variation_child': True},
                        **overrides})


def spotter_rows():
    return [make_deal(dedupe_key=f'cart|{i}', asin=f'B0{i:08d}', item=f'Everyday wireless headphones {i}',
                      store='Amazon', codes=[], stack_steps=[], conditions=[],
                      canonical_url=f'https://www.amazon.com/dp/B0{i:08d}',
                      image_url='https://m.media-amazon.com/images/I/fixture.jpg', final_price=20 + i,
                      regular_price=50, pct_at_checkout=60, abs_savings=30 - i, regular_price_basis='List price',
                      verify_tier='verified', price_rechecked_at=SUPPORT_NOW.isoformat(),
                      critic={'plausible': True, 'confidence': .99, 'reason': 'Fixture'}) for i in range(3)]


def test_ships_off_default_off_and_strict(tmp_path):
    assert AppConfig().add_to_cart == load_config().add_to_cart == {'enabled': False}
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).add_to_cart == {'enabled': False}
    for value in (None, False, True, [], 'on', {'enabled': 'true'}, {'enabled': 1}, {'enabled': None},
                  {'typo': True}, {'enabled': True, 'extra': 1}):
        path.write_text(yaml.safe_dump({'add_to_cart': value}))
        with pytest.raises(ValueError, match='^add_to_cart'):
            load_config(path)
    assert validated_add_to_cart({'enabled': True}) == {'enabled': True}
    # Visible-change convention: recorded fixtures pin it OFF, raw configs drop the key.
    assert pre_add_to_cart(AppConfig(add_to_cart={'enabled': True})).add_to_cart == {'enabled': False}
    assert pre_hide_timer_ticker(AppConfig(add_to_cart={'enabled': True})).add_to_cart == {'enabled': False}
    assert 'add_to_cart' not in pre_task_319_raw({'add_to_cart': {'enabled': True}})


def test_link_is_amazons_add_to_cart_form_with_the_configured_tag():
    assert add_to_cart.cart_url(CHILD, TAG) == CART
    assert load_config().affiliate_amazon_tag == TAG  # read from config, never hard-coded
    cfg = AppConfig(affiliate_amazon_tag='', add_to_cart={'enabled': True})
    assert not add_to_cart.enabled(cfg)  # no tag, no button: nothing to credit
    assert add_to_cart.enabled(AppConfig(affiliate_amazon_tag=TAG, add_to_cart={'enabled': True}))


@pytest.mark.parametrize('row,expected', [
    ({'url': f'https://www.amazon.com/dp/{CHILD}?tag={TAG}'}, CHILD),
    ({'url': f'https://www.amazon.com/dp/{CHILD}?th=1&psc=1&tag={TAG}'}, CHILD),  # the exact size/colour
    ({'url': f'https://www.amazon.com/Girls-Sneaker/dp/{CHILD.lower()}/ref=sr_1_3?tag={TAG}'}, CHILD),
    ({'url': f'https://amazon.com/gp/product/{CHILD}?th=1&psc=1&tag={TAG}'}, CHILD),
    ({'url': f'https://www.amazon.com/dp/{CHILD}'}, ''),  # untagged
    ({'url': f'https://www.amazon.com/dp/{CHILD}?tag=other-20'}, ''),  # another site's credit stays theirs
    ({'url': f'https://www.amazon.com/dp/{CHILD}?tag={TAG}&tag=other-20'}, ''),
    ({'url': f'http://www.amazon.com/dp/{CHILD}?tag={TAG}'}, ''),
    ({'url': f'https://www.amazon.com/s?k={CHILD}&tag={TAG}'}, ''),  # a search, no ASIN
    ({'url': f'https://www.amazon.com.evil.example/dp/{CHILD}?tag={TAG}'}, ''),
    ({'url': 'https://www.walmart.com/ip/12345'}, ''),
    ({'url': 'https://slickdeals.net/f/1'}, ''),
    ({'url': ''}, ''),
    ({'url': 'https://[bad'}, ''),
    ({'conditions': ['used']}, ''),
    ({'conditions': ['open_box']}, ''),
    ({'conditions': ['refurbished']}, ''),
    ({'store': 'Amazon Warehouse'}, ''),
    ({'store': 'Amazon Resale'}, ''),
    ({'item': 'Blender (Used - Like New)'}, ''),
    ({'item': 'Amazon Renewed laptop'}, ''),
])
def test_only_new_condition_amazon_items_with_an_asin(row, expected):
    d = {'store': 'Amazon', 'item': 'Girls sneaker', 'conditions': [],
         'url': f'https://www.amazon.com/dp/{CHILD}?tag={TAG}', **row}
    assert add_to_cart.deal_asin(d, TAG) == expected


def deal_site(tmp_path, on=True, deals=None, **settings):
    cfg = AppConfig(site_dir=str(tmp_path / 'unused'), min_pct=70.0, min_abs_savings=5.0,
                    custom_domain='wowpriced.com', seo_hub_min_items=3, paywall_enabled=True,
                    paywall_teaser_count=8, paywall_teaser_pool=48, paywall_open_access=True,
                    affiliate_amazon_tag=TAG, affiliate_exact_variant_links=True,
                    tracking={'ga4_measurement_id': 'G-ABC123XYZ9'}, add_to_cart={'enabled': on}, **settings)
    db = DB(tmp_path / 'state.db')
    try:
        render_site(db, cfg, NOW, out_dir=tmp_path / 'site', deals=deals or [
            make_deal(dedupe_key='walmart|a', codes=[]), amazon(),
            amazon(dedupe_key='amazon|used', item='Used blender', canonical_url='https://www.amazon.com/dp/B0USED0001',
                   asin='B0USED0001', conditions=['used'], click_price_check=None)])
    finally:
        db.close()
    out = tmp_path / 'site'
    return out, cfg, {p.parent.name: p.read_text() for p in (out / 'deal').glob('*/index.html')}


def test_deal_pages_new_amazon_only_right_after_get_deal(tmp_path):
    out, cfg, pages = deal_site(tmp_path)
    shoe = next(html for name, html in pages.items() if name.startswith('girls-sneaker'))
    links = re.findall(r'<a [^>]*gp/aws/cart/add\.html[^>]*>[^<]*</a>', shoe)
    assert links == [f'<a class="addcart" href="{CART.replace("&", "&amp;")}" target="_blank" '
                     'rel="sponsored nofollow noopener" data-deal-outbound data-store="Amazon" '
                     'data-category="other" data-cart>Add to Amazon cart</a>']
    # Directly after Get deal, which keeps its own exact-size tagged link.
    get = (f'<a class="getdeal" href="https://www.amazon.com/dp/{CHILD}?th=1&amp;psc=1&amp;tag={TAG}" '
           'target="_blank" rel="sponsored noopener" data-deal-outbound data-store="Amazon" data-category="other">Get deal</a>')
    assert get + links[0] + '<p class="disclosure">' in shoe
    assert shoe.count(add_to_cart.STYLE) == 1
    # Nothing automatic: a plain link, no form, script submit or prefetch.
    assert not re.search(r'<form[^>]*amazon|\.submit\(|prefetch|prerender', shoe.replace(add_to_cart.STYLE, ''))
    for name, html in pages.items():
        if not name.startswith('girls-sneaker'):  # Walmart and the used Amazon offer
            assert 'cart/add.html' not in html and add_to_cart.STYLE not in html and 'data-cart' not in html, name
    for path in out.rglob('*.html'):
        if path.parent.parent.name != 'deal':
            assert 'cart/add.html' not in path.read_text(), path


def test_deal_click_tells_the_two_buttons_apart(tmp_path):
    _out, _cfg, pages = deal_site(tmp_path)
    shoe = next(html for name, html in pages.items() if name.startswith('girls-sneaker'))
    assert shoe.count(add_to_cart.DEAL_CLICK_CART) == 1 and add_to_cart.DEAL_CLICK not in shoe
    assert shoe.count(add_to_cart.DISPATCH + add_to_cart.DISPATCH_CART) == 1
    for name, html in pages.items():
        if not name.startswith('girls-sneaker'):
            assert add_to_cart.DISPATCH_CART not in html and add_to_cart.DEAL_CLICK_CART not in html


def test_publish_is_idempotent_and_rerun_adds_nothing(tmp_path):
    out, cfg, _pages = deal_site(tmp_path)
    before = files(out)
    add_to_cart.publish(out, cfg)
    assert files(out) == before
    deal_site(tmp_path)
    assert files(out) == before


def test_off_missing_and_back_to_off_are_byte_identical(tmp_path, monkeypatch):
    monkeypatch.setattr(site, 'util_now', lambda: SUPPORT_NOW)
    cfg = load_config()
    out, *_ = render(tmp_path, cfg, spotter_rows())
    off = files(out)
    cfg.add_to_cart = validated_add_to_cart({'enabled': True})
    render(tmp_path, cfg, spotter_rows())
    on = files(out)
    changed = {name for name in on.keys() | off.keys() if on.get(name) != off.get(name)}
    deal_pages = {name for name in changed if name.startswith('deal/')}
    assert len(deal_pages) == 3 and changed - deal_pages == {
        'deal-spotter/index.html', 'deal-spotter.js', 'fakespot-alternative/index.html'}
    # The alternative page only loads the new script version.
    version = re.compile(rb'deal-spotter\.js\?v=[0-9a-f]{12}')
    assert version.sub(b'', on['fakespot-alternative/index.html']) == version.sub(b'', off['fakespot-alternative/index.html'])
    render(tmp_path, cfg, spotter_rows())
    assert files(out) == on
    for setting in ({'enabled': False}, {}):
        cfg.add_to_cart = setting
        render(tmp_path, cfg, spotter_rows())
        assert files(out) == off
    del cfg.add_to_cart
    render(tmp_path, cfg, spotter_rows())
    assert files(out) == off


def test_spotter_secondary_button_and_script(tmp_path):
    cfg = load_config()
    assert add_to_cart.SPOTTER_CART_HREF not in deal_spotter.script(cfg)
    cfg.add_to_cart = {'enabled': True}
    script = deal_spotter.script(cfg)
    assert script.count(add_to_cart.SPOTTER_HREF + add_to_cart.SPOTTER_CART_HREF) == 1
    path = tmp_path / 'deal-spotter.js'
    path.write_text(script)
    result = subprocess.run(['node', '--check', str(path)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    # The emitted line builds the official link from the graded ASIN and the configured tag.
    run = ("var toolSettings = " + json.dumps({'tag': TAG}) + "; var item = {asin: '" + CHILD + "'};"
           "var link = {}; function $(id) { return id === 'result-cart' ? link : null; }\n"
           + add_to_cart.SPOTTER_CART_HREF + "console.log(link.href);")
    out = subprocess.run(['node', '-e', run], capture_output=True, text=True, check=True, timeout=60).stdout.strip()
    assert out == CART
    template = (deal_spotter.TEMPLATES / 'deal-spotter.html').read_text()
    assert add_to_cart.spotter_page(template, cfg).count(add_to_cart.SPOTTER_BUY + add_to_cart.SPOTTER_CART) == 1
    assert add_to_cart.SPOTTER_CART == ('<a id="result-cart" class="secondary" target="_blank" '
                                        'rel="sponsored nofollow noopener">Add to Amazon cart</a>')


def test_real_chromium_deal_page_and_spotter(tmp_path):
    result = subprocess.run(['node', 'tests/add_to_cart_browser.cjs', str(tmp_path)],
                            capture_output=True, text=True, timeout=300)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
    assert {p.name for p in tmp_path.glob('add-to-cart-*.png')} == {
        f'add-to-cart-{page}-{width}.png' for page in ('deal', 'spotter') for width in (390, 1280)}
