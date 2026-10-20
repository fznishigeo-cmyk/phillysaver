"""Operator 2026-10-20: AI Analysis / All Discounts open the tools in a new tab, behind a switch."""
import json
import re

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import tools_new_tab as feature
from tests.history_pins import pre_amazon_price_cap, pre_buttons_to_tools, pre_tools_new_tab
from tests.test_pwa import tree_hash
from tests.test_support import render
from tests.test_tool_pages import offline  # noqa: F401

PAGES = ('index.html', 'black-friday/index.html', 'cyber-monday/index.html')


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_boolean(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'tools_new_tab': value}))
    with pytest.raises(ValueError, match='^tools_new_tab must be a boolean$'):
        load_config(path)


def test_defaults_history_and_shipped(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).tools_new_tab is AppConfig().tools_new_tab is False
    assert load_config().tools_new_tab is True
    for helper in (pre_tools_new_tab, pre_amazon_price_cap, pre_buttons_to_tools):
        assert helper(load_config()).tools_new_tab is False
    assert not feature.enabled(type('Config', (), {'tools_new_tab': 1})())
    assert feature.site_version('old', AppConfig()) == 'old'


def test_markup_only_touches_tool_links_once():
    html = ('<a id="analyze-search" class="x" href="/amazon-deal-checker/" data-tool-link="deal-checker">AI</a>'
            '<a class="all-discounts" href="/amazon-discount-finder/" data-tool-link="discount-finder">All</a>'
            '<a class="getdeal" href="https://www.amazon.com/dp/B0?tag=t-20" target="_blank" rel="sponsored">Get deal</a>')
    once = feature.markup(html)
    assert once.count('target="_blank" rel="noopener">') == 2
    assert '<a class="getdeal" href="https://www.amazon.com/dp/B0?tag=t-20" target="_blank" rel="sponsored">' in once
    assert feature.markup(once) == once


def build(tmp_path, name, cfg):
    (tmp_path / name).mkdir()
    return render(tmp_path / name, cfg)[0]


def tool_links(html):
    return re.findall(r'<a\b[^>]*\bdata-tool-link="[^"]*"[^>]*>', html)


def same(html):
    html = html.replace(' target="_blank" rel="noopener">', '>')
    return re.sub(r'VERSION = "[0-9a-f]+"', 'VERSION = ""', html)


def test_off_keeps_previous_bytes_and_on_opens_every_tool_link_in_a_new_tab(tmp_path):
    cfg = pre_tools_new_tab(load_config())
    out = build(tmp_path, 'off', cfg)
    off = tree_hash(out)
    before = {name: (out / name).read_text() for name in PAGES}
    for html in before.values():
        assert len(tool_links(html)) >= 3 and not any('target=' in link for link in tool_links(html))
    del cfg.tools_new_tab
    assert tree_hash(build(tmp_path, 'missing', cfg)) == off
    cfg.tools_new_tab = True
    out = build(tmp_path, 'on', cfg)
    for name in PAGES:
        html = (out / name).read_text()
        links = tool_links(html)
        assert links and all(link.endswith(' target="_blank" rel="noopener">') for link in links)
        # Nothing else on the page changes (the site version moves with any switch).
        assert same(html) == same(before[name])
    cfg.tools_new_tab = False
    assert tree_hash(build(tmp_path, 'back', cfg)) == off
