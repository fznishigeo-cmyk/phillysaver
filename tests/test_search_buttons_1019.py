"""Task 332: exact rollback, emitted routing, calm styles and responsive copy."""
import hashlib
import json
import re
import subprocess

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import search_buttons_1019 as buttons
from tests.history_pins import (pre_phone_timer_raise, pre_search_buttons_1019, pre_task_290_install_raw,
                                pre_search_discounts_button, pre_card_click_1020)
from tests.test_pwa import tree_hash
from tests.test_search_more import offline  # noqa: F401
from tests.test_support import render


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_bool(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'search_buttons_1019': value}))
    with pytest.raises(ValueError, match='^search_buttons_1019 must be a boolean$'):
        load_config(path)


def test_defaults_history_and_off_noop(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).search_buttons_1019 is AppConfig().search_buttons_1019 is False
    assert load_config().search_buttons_1019 is True
    for value in (False, True):
        path.write_text(json.dumps({'search_buttons_1019': value}))
        assert load_config(path).search_buttons_1019 is value
    assert pre_phone_timer_raise(load_config()).search_buttons_1019 is False
    assert 'search_buttons_1019' not in pre_task_290_install_raw({'search_buttons_1019': True})
    assert not buttons.enabled(type('Config', (), {'search_buttons_1019': 1})())
    assert buttons.site_version('old', AppConfig()) == 'old'
    buttons.publish(tmp_path, AppConfig())
    assert path.read_text() == '{"search_buttons_1019": true}'


@pytest.mark.parametrize('mode', ['shipped', 'inline', 'membership'])
def test_off_missing_on_rerun_back_off_exact_bytes(tmp_path, mode):
    cfg = pre_search_buttons_1019(load_config())
    if mode == 'inline':
        cfg.strip_comments = False
        cfg.search_analyze['lazy'] = False
    if mode == 'membership':
        cfg.support_enabled = False
    render(tmp_path, cfg)  # Initialise this configuration's own OFF state.
    hashes = []
    for value in (False, None, True, True, False):
        if value is None:
            del cfg.search_buttons_1019
        else:
            cfg.search_buttons_1019 = value
        out, *_ = render(tmp_path, cfg)
        hashes.append(tree_hash(out))
        assert ('>AI Analysis</span>' in (out / 'index.html').read_text()) is bool(value)
    assert hashes[0] == hashes[1] == hashes[4] != hashes[2] == hashes[3]


@pytest.mark.parametrize('lazy', [False, True])
@pytest.mark.parametrize('more', [False, True])
def test_markup_styles_routing_and_content_addresses(tmp_path, lazy, more):
    cfg = pre_card_click_1020(pre_search_discounts_button(load_config()))
    cfg.search_analyze['lazy'] = lazy
    cfg.search_more['enabled'] = more
    cfg.affiliate_amazon_tag = 'fixture-coupons-20'
    out, *_ = render(tmp_path, cfg)
    home = (out / 'index.html').read_text()
    source = (out / 'search-analyze.js').read_text() if lazy else home
    assert 'aria-label="Analyze product"' not in home
    assert '<span class="analyze-long">AI Analysis</span><span class="analyze-short">Analyze</span>' in home
    assert '? "Search deals on Amazon" : "Search deals or any Amazon product"' in home
    assert 'matchMedia("(max-width:899px)")' in home
    assert 'if (e.key === "Enter") { e.preventDefault(); analyzeSubmit(); }' in home
    assert 'if (!task.reason) return "";' in source
    assert 'See this product on Amazon →' not in source
    assert 'Analyze &quot;' not in home + source
    assert 'id="search-result-top"' not in home + source
    assert 'Live Amazon prices' in source and 'data-analyze-fallback' in source
    assert 'Compare all &quot;' not in home and 'Search all of Amazon for &quot;' not in home
    assert 'See all &quot;' not in home + source
    assert 'Search Amazon for &quot;' in home
    assert 'class="getdeal" data-analyze-fallback' not in source
    assert 'searchAmazonHtml(true)' in home  # Zero-results exit remains.
    assert buttons.COUPONS in home
    assert '23566065011' in home  # All discounts keeps its original filter.
    assert '>All Discounts on Amazon →</a>' in home and '>All discounts on Amazon →<' not in home
    assert '<span class="discounts-all">All </span>Discounts</a>' in home
    css = (out / 'header-polish.css').read_text()
    assert 'width:108px;padding:0;justify-content:center;text-align:center;font-size:14px;font-weight:800' in css
    assert 'html :is(#analyze-search,.all-discounts)::before{content:none}' in css  # No stars.
    assert '@media(max-width:600px){.searchfield button#analyze-search{padding:0 11px}}' in css
    assert 'padding-left:36px;padding-right:274px' in css
    assert '#event-search #analyze-search,#event-search #search-discounts{width:114px;font-size:15px;font-weight:800}' in css
    assert '#event-search #analyze-search{right:126px}' in css
    assert '#event-search #q{padding-left:14px;padding-right:250px}' in css
    assert 'outline:3px solid var(--accent);outline-offset:2px' in css
    assert 'linear-gradient(120deg,var(--pill-colors,#5b21b6,#9333ea 50%,#cf236e))' in css
    assert '--pill-colors:#1265f4,#0061fe 52%,#0050d6' in css
    assert 'animation:wpcharge' not in css and 'repeat-x,linear-gradient' not in css
    for rule in re.finditer(r'([^{}]+)\{([^{}]*)\}', css):
        if '#analyze-search' in rule[1] or '.all-discounts' in rule[1]:
            assert not re.search(r'(?:animation|transition|transform|filter):(?!none)', rule[2])
            if ':hover' in rule[1] or ':active' in rule[1]:
                assert not re.search(r'background|box-shadow|color:', rule[2])
    for name in ('header-polish.css', 'page-polish.css', 'search-analyze.js'):
        asset = out / name
        if asset.exists():
            version = hashlib.sha256(asset.read_bytes()).hexdigest()[:12]
            for path in out.rglob('*.html'):
                assert all(found == version for found in re.findall('/' + name + r'\?v=([a-f0-9]+)', path.read_text()))
    result = subprocess.run(['node', 'tests/search_buttons_1019_logic.cjs', str(out / 'index.html')],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    for slug in ('black-friday', 'cyber-monday'):
        page = out / slug / 'index.html'
        html = page.read_text()
        assert '>AI Analysis</span><span class="analyze-short">Analyze</span>' in html
        assert 'function analyzeInput(value)' in html and 'Search deals on Amazon' in html
        result = subprocess.run(['node', 'tests/search_buttons_1019_logic.cjs', str(page)],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr


def test_browser_harness_syntax():
    for path in ('tests/search_box_glow_browser.cjs', 'tests/search_analyze_browser.cjs',
                 'tests/search_buttons_1019_logic.cjs', 'tests/page_polish_browser.cjs', 'tests/search_more_browser.cjs',
                 'tests/search_amazon_browser.cjs', 'scripts/live_first_screens.js'):
        result = subprocess.run(['node', '--check', path], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
