"""Operator 2026-10-20: Deal Checker / Discount Finder sit outside the search bar, behind a switch."""
import json
import re

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import tool_buttons_outside as feature
from tests.history_pins import pre_buttons_to_tools, pre_tool_buttons_outside, pre_tools_new_tab
from tests.test_pwa import tree_hash
from tests.test_support import render
from tests.test_tool_pages import offline  # noqa: F401

PAGES = ('index.html', 'black-friday/index.html', 'cyber-monday/index.html')


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_boolean(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'tool_buttons_outside': value}))
    with pytest.raises(ValueError, match='^tool_buttons_outside must be a boolean$'):
        load_config(path)


def test_defaults_history_and_shipped(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).tool_buttons_outside is AppConfig().tool_buttons_outside is False
    assert load_config().tool_buttons_outside is True
    for helper in (pre_tool_buttons_outside, pre_tools_new_tab, pre_buttons_to_tools):
        assert helper(load_config()).tool_buttons_outside is False
    assert not feature.enabled(type('Config', (), {'tool_buttons_outside': 1})())
    assert feature.site_version('old', AppConfig()) == 'old'


def test_markup_moves_both_buttons_once():
    html = ('<head></head><div class="search" role="search"><div class="searchfield"><input id="q">\n'
            '        <a id="analyze-search" href="/amazon-deal-checker/" data-tool-link="deal-checker" target="_blank" '
            'rel="noopener"><span class="analyze-long">AI Analysis</span><span class="analyze-short">Analyze</span></a>'
            '<a class="all-discounts" id="search-discounts" href="/amazon-discount-finder/" data-tool-link="discount-finder" '
            'target="_blank" rel="noopener"><span class="discounts-all">All </span>Discounts</a>\n'
            '        <button id="clearsearch" type="button" hidden>✕</button>\n'
            '      </div><div class="discounts-mobile"><a class="all-discounts" id="search-discounts-mobile" '
            'href="/amazon-discount-finder/" data-tool-link="discount-finder">All Discounts →</a></div></div>')
    once = feature.markup(html)
    field = once.split('<div class="searchfield">')[1].split('</div>')[0]
    assert 'data-tool-link' not in field and 'id="clearsearch"' in field
    assert 'discounts-mobile' not in once and 'search-discounts-mobile' not in once
    assert '<div class="search tools-outside" role="search">' in once
    buttons = once.split('<div class="tool-buttons">')[1].split('</div>')[0]
    assert re.findall(r'id="([^"]+)"', buttons) == ['search-discounts', 'analyze-search']
    assert '>Discount Finder<span class="tool-arrow" aria-hidden="true"> →</span></a>' in buttons
    assert '>Deal Checker<span class="tool-arrow" aria-hidden="true"> →</span></a>' in buttons
    assert buttons.count('target="_blank" rel="noopener"') == 2
    assert f'/tool-buttons-outside.css?v={feature.VERSION}"></head>' in once
    assert feature.markup(once) == once


def build(tmp_path, name, cfg):
    (tmp_path / name).mkdir()
    return render(tmp_path / name, cfg)[0]


def test_off_keeps_previous_bytes_and_on_moves_every_page(tmp_path):
    cfg = pre_tool_buttons_outside(load_config())
    out = build(tmp_path, 'off', cfg)
    off = tree_hash(out)
    assert not (out / 'tool-buttons-outside.css').exists()
    del cfg.tool_buttons_outside
    assert tree_hash(build(tmp_path, 'missing', cfg)) == off
    cfg.tool_buttons_outside = True
    out = build(tmp_path, 'on', cfg)
    assert (out / 'tool-buttons-outside.css').read_text() == feature.CSS
    for name in PAGES:
        html = (out / name).read_text()
        assert 'class="tool-buttons"' in html and 'discounts-mobile' not in html, name
        assert html.count('data-tool-link=') == 2, name
        assert '>Deal Checker<' in html and '>Discount Finder<' in html, name
    cfg.tool_buttons_outside = False
    assert tree_hash(build(tmp_path, 'back', cfg)) == off
