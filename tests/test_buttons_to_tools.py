"""Task 344: native navigation and exact sibling rollback, entirely offline."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import buttons_to_tools as feature, tracking
from dealradar.publish.seo_audit import Page
from scripts.publication_budget import analyze
from tests.history_pins import pre_buttons_to_tools, pre_card_click_1020, pre_search_discounts_button
from tests.phone_budget_fixture import publish_fixture, visible_offer_count
from tests.test_home_comment_strip import files
from tests.test_pwa import tree_hash
from tests.test_support import render
from tests.test_tool_pages import offline  # noqa: F401
from tests.test_tracking import HARNESS, SAMPLE, _script

PINS = json.loads(Path('tests/fixtures/buttons_to_tools_off.json').read_text())


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_boolean(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'buttons_to_tools': value}))
    with pytest.raises(ValueError, match='^buttons_to_tools must be a boolean$'):
        load_config(path)


def test_defaults_history_and_shipped(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).buttons_to_tools is AppConfig().buttons_to_tools is False
    assert load_config().buttons_to_tools is True
    for helper in (pre_buttons_to_tools, pre_card_click_1020, pre_search_discounts_button):
        assert helper(load_config()).buttons_to_tools is False
    assert not feature.enabled(type('Config', (), {'buttons_to_tools': 1})())
    assert feature.site_version('old', AppConfig()) == 'old'


@pytest.mark.parametrize('mode', PINS)
def test_off_missing_rerun_back_preserves_previous_bytes(tmp_path, mode):
    cfg = pre_buttons_to_tools(load_config())
    if mode == 'inline':
        cfg.strip_comments = False
        cfg.search_analyze['lazy'] = False
    if mode == 'membership':
        cfg.support_enabled = False
    out, *_ = render(tmp_path, cfg)
    assert tree_hash(out) == PINS[mode]  # Recorded before task 344; never re-pin.
    before = files(out)
    on = None
    for value in (None, True, True, False):
        if value is None:
            del cfg.buttons_to_tools
        else:
            cfg.buttons_to_tools = value
        render(tmp_path, cfg)
        after = files(out)
        assert (after == before) is (value is not True)
        if value is True:
            if on is not None:
                assert after == on
            on = after


@pytest.mark.parametrize('lazy', [False, True])
@pytest.mark.parametrize('more', [False, True])
def test_native_links_emitted_logic_and_tool_handoff(tmp_path, lazy, more):
    cfg = load_config()
    cfg.amazon_price_cap = False  # exact exit URLs; the cap has its own test
    cfg.tools_new_tab = False  # same-tab links; the new tab has its own test
    cfg.checker_similar_heading = False  # 344 lead button; the heading button has its own test
    cfg.tool_buttons_outside = False  # buttons inside the bar; the outside layout has its own test
    cfg.search_analyze['lazy'] = lazy
    cfg.search_more['enabled'] = more
    cfg.affiliate_amazon_tag = 'example-20'
    out, *_ = render(tmp_path, cfg)
    for name in ('index.html', 'black-friday/index.html', 'cyber-monday/index.html'):
        html = (out / name).read_text()
        page = Page(html, '/' + name)
        for ident, slug in [('analyze-search', 'amazon-deal-checker'),
                            ('search-discounts', 'amazon-discount-finder'),
                            ('search-discounts-mobile', 'amazon-discount-finder')]:
            link = page.first(ident=ident)
            assert link.tag == 'a' and link.attrs['href'] == '/' + slug + '/'
            assert not any(key in link.attrs for key in ('target', 'rel', 'type'))
            assert 'tag=' not in str(link.attrs)
        if name == 'index.html':
            assert 'See Amazon discounts →' in html
            assert 'if (e.key === "Enter") { e.preventDefault(); analyzeSubmit(); }' in html
            assert 'analyzeButton.addEventListener("click"' not in html
        else:
            assert 'form.addEventListener("submit"' in html
        for asset in ('header-polish.css', 'page-polish.css'):
            version = hashlib.sha256((out / asset).read_bytes()).hexdigest()[:12]
            assert all(v == version for v in re.findall('/' + asset + r'\?v=([a-f0-9]+)', html))
        run_node('tests/buttons_to_tools_logic.cjs', str(out / name))
    js = (out / 'tool-pages.js').read_text()
    assert 'new URLSearchParams(window.location.search).get(\'q\')' in js
    assert 'toolInitialQuery.length <= 300' in js
    assert 'scrollIntoView' not in js
    assert '(10 + 15 * Math.random()) * 1000' in js
    for slug in ('amazon-deal-checker', 'amazon-discount-finder'):
        html = (out / slug / 'index.html').read_text()
        assert '<link rel="canonical" href="https://wowpriced.com/' + slug + '/">' in html
        assert '<meta name="robots"' not in html
    run_node('tests/buttons_to_tools_page_logic.cjs', str(out / 'tool-pages.js'))
    run_node('--check', str(out / 'tool-pages.js'))


def run_node(*args):
    result = subprocess.run(['node', *args], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_private_tracking(tmp_path):
    cfg = load_config()
    cfg.tracking = SAMPLE
    (tmp_path / 'snippet.js').write_text(_script(tracking.head_snippet(cfg)))
    (tmp_path / 'harness.cjs').write_text(HARNESS)
    scenarios = {"plain": {"calls": [
        ["search_analyze", {"to_tool": True, "query": "PRIVATE"}],
        ["search_discounts", {"to_tool": True, "url": "PRIVATE"}],
        ["search_similar_deals", {"query": "PRIVATE"}]]}}
    (tmp_path / 'scenarios.json').write_text(json.dumps(scenarios))
    result = subprocess.run(['node', 'harness.cjs', 'snippet.js', 'scenarios.json'], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    result = json.loads(result.stdout)['plain']
    events = [c for c in result["dataLayer"] if c[0] == "event"]
    assert {c[1] for c in events} >= {"search_analyze", "search_discounts", "search_similar_deals"}
    assert sum(c[2].get("to_tool") is True for c in events) == 2
    assert "PRIVATE" not in json.dumps(events)


def test_phone_gzip_reserve(tmp_path):
    cfg = load_config()
    cfg.phone_budget['enabled'] = True
    def inspect(db, out):
        report = analyze(out, cfg, expected_offers=visible_offer_count(cfg))
        assert report['ok'], report
        size = report['metrics']['home_html']['gzip']
        reserve = cfg.phone_budget['home_html_gzip_max_bytes'] - size
        print(f'task344 home HTML gzip {size}; limit {cfg.phone_budget["home_html_gzip_max_bytes"]}; headroom {reserve}')
        assert reserve >= 1000
    publish_fixture(tmp_path, cfg, inspect=inspect)


def test_browser_harness_syntax():
    for name in ('tests/buttons_to_tools_logic.cjs', 'tests/buttons_to_tools_page_logic.cjs',
                 'tests/tool_pages_browser.cjs', 'tests/buttons_to_tools_browser.cjs'):
        run_node('--check', name)
