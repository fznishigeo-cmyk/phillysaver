"""Operator 2026-10-20 $50 cap on the Amazon discount exits: on/off switch, offline."""
import json
import re

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import amazon_price_cap as feature
from tests.history_pins import pre_amazon_price_cap, pre_buttons_to_tools
from tests.test_pwa import tree_hash
from tests.test_support import render
from tests.test_tool_pages import offline  # noqa: F401

FILTERS = re.compile(r'p_n_deal_type%3A(?:23566065011|210906366011)[^"\'&]*&')


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_boolean(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'amazon_price_cap': value}))
    with pytest.raises(ValueError, match='^amazon_price_cap must be a boolean$'):
        load_config(path)


@pytest.mark.parametrize('value', [True, '50', 0, 0.5, 10001, None])
def test_cap_amount_is_a_sane_number(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'amazon_price_cap_usd': value}))
    with pytest.raises(ValueError, match='^amazon_price_cap_usd must be a number from 1 to 10000$'):
        load_config(path)


def test_defaults_history_and_shipped(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).amazon_price_cap is AppConfig().amazon_price_cap is False
    assert load_config(path).amazon_price_cap_usd == 50
    shipped = load_config()
    assert shipped.amazon_price_cap is True and shipped.amazon_price_cap_usd == 50
    for helper in (pre_amazon_price_cap, pre_buttons_to_tools):
        assert helper(load_config()).amazon_price_cap is False
    assert not feature.enabled(type('Config', (), {'amazon_price_cap': 1})())
    assert feature.site_version('old', AppConfig()) == 'old'


def test_cap_adds_the_price_filter_once():
    cfg = AppConfig()
    urls = ('https://www.amazon.com/s?k=mug&rh=p_n_deal_type%3A23566065011&tag=t-20',
            'https://www.amazon.com/s?k=mug&rh=p_n_deal_type%3A210906366011&s=exact-aware-popularity-rank&tag=t-20',
            'https://www.amazon.com/s?k=mug&tag=t-20', 'https://www.amazon.com/dp/B000000001?tag=t-20')
    text = '\n'.join(urls)
    assert feature.cap(text, cfg) == text
    cfg.amazon_price_cap = True
    capped = feature.cap(text, cfg).split('\n')
    assert capped[0] == 'https://www.amazon.com/s?k=mug&rh=p_n_deal_type%3A23566065011%2Cp_36%3A-5000&tag=t-20'
    assert capped[1] == ('https://www.amazon.com/s?k=mug&rh=p_n_deal_type%3A210906366011%2Cp_36%3A-5000'
                         '&s=exact-aware-popularity-rank&tag=t-20')
    assert capped[2:] == list(urls[2:])  # plain searches and product pages are not discount exits
    assert feature.cap('\n'.join(capped), cfg) == '\n'.join(capped)
    cfg.amazon_price_cap_usd = 25.0
    assert '%2Cp_36%3A-2500&' in feature.cap(urls[0], cfg)


def build(tmp_path, name, cfg):
    (tmp_path / name).mkdir()
    return render(tmp_path / name, cfg)[0]


def published(out):
    found = {}
    for path in sorted(out.rglob('*')):
        if path.is_file() and path.suffix in ('.html', '.js'):
            hits = FILTERS.findall(path.read_text())
            if hits:
                found[str(path.relative_to(out))] = hits
    return found


def test_off_keeps_previous_bytes_and_on_caps_every_exit(tmp_path):
    cfg = pre_amazon_price_cap(load_config())
    out = build(tmp_path, 'off', cfg)
    off = tree_hash(out)
    exits = published(out)
    assert exits and {'index.html', 'tool-pages.js'} <= set(exits)
    assert not any('p_36' in hit for hits in exits.values() for hit in hits)
    del cfg.amazon_price_cap
    assert tree_hash(build(tmp_path, 'missing', cfg)) == off
    cfg.amazon_price_cap = True
    out = build(tmp_path, 'on', cfg)
    capped = published(out)
    assert set(capped) == set(exits)
    assert all(hit.startswith('p_n_deal_type%3A') and '%2Cp_36%3A-5000&' in hit
               for hits in capped.values() for hit in hits)
    # The tool pages load the rewritten script under a new version.
    page = (out / 'amazon-discount-finder' / 'index.html').read_text()
    assert re.search(r'tool-pages\.js\?v=([0-9a-f]+)', page)[1] != re.search(
        r'tool-pages\.js\?v=([0-9a-f]+)', (tmp_path / 'off' / 'site' / 'amazon-discount-finder' / 'index.html').read_text())[1]
    cfg.amazon_price_cap = False
    assert tree_hash(build(tmp_path, 'back', cfg)) == off
