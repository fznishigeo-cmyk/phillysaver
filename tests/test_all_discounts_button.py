"""Task 317: rollback, emitted affiliate exits, shared styling and privacy."""
import json
import re
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import all_discounts, tracking
from tests.history_pins import pre_buttons_to_tools, pre_all_discounts_button, pre_search_polish_1019, pre_task_295, pre_task_290_install_raw
from tests.test_pwa import tree_hash
from tests.test_search_more import offline  # noqa: F401
from tests.test_support import render
from tests.test_tracking import HARNESS, SAMPLE, _script


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_bool(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'all_discounts_button': value}))
    with pytest.raises(ValueError, match='^all_discounts_button must be a boolean$'):
        load_config(path)


def test_defaults_and_history(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).all_discounts_button is AppConfig().all_discounts_button is False
    assert load_config().all_discounts_button is True
    assert pre_all_discounts_button(load_config()).all_discounts_button is False
    assert pre_task_295(load_config()).all_discounts_button is False
    assert 'all_discounts_button' not in pre_task_290_install_raw({'all_discounts_button': True})
    for value in (False, True):
        path.write_text(json.dumps({'all_discounts_button': value}))
        assert load_config(path).all_discounts_button is value


@pytest.mark.parametrize('mode', ['shipped', 'inline', 'membership'])
def test_off_missing_back_off_exact_bytes(tmp_path, mode):
    cfg = pre_search_polish_1019(load_config())
    if mode == 'inline':
        cfg.search_analyze['lazy'] = False
        cfg.strip_comments = False
    if mode == 'membership':
        cfg.support_enabled = False
    cfg.all_discounts_button = False
    render(tmp_path, cfg)
    hashes = []
    for value in (False, None, True, True, False):
        if value is None:
            del cfg.all_discounts_button
        else:
            cfg.all_discounts_button = value
        out, *_ = render(tmp_path, cfg)
        hashes.append(tree_hash(out))
        assert ('id="search-discounts"' in (out / 'index.html').read_text()) is bool(value)
    assert hashes[0] == hashes[1] == hashes[4] != hashes[2] == hashes[3]


@pytest.mark.parametrize('mode', ['shipped', 'inline', 'inline-css', 'no-header', 'no-analyze', 'no-search-amazon'])
def test_emitted_buttons_and_click_logic(tmp_path, mode):
    cfg = pre_search_polish_1019(load_config())
    cfg.affiliate_amazon_tag = 'example-20'
    cfg.search_boost['exclude_words'].append('butt plug')
    if mode == 'inline':
        cfg.search_analyze['lazy'] = False
    if mode == 'inline-css':
        cfg.page_polish = False
    if mode == 'no-header':
        cfg.header_polish = False
    if mode == 'no-analyze':
        cfg.search_analyze['enabled'] = False
    if mode == 'no-search-amazon':
        cfg.search_amazon = False
    out, *_ = render(tmp_path, cfg)
    for name in ['index.html'] + ([] if not cfg.page_polish else ['black-friday/index.html', 'cyber-monday/index.html']):
        html = (out / name).read_text()
        for suffix in ('', '-mobile'):
            link = re.search(r'<a class="all-discounts" id="search-discounts' + suffix + '"[^>]*>.*?</a>', html)[0]
            assert 'href="https://www.amazon.com/"' in link
            assert 'target="_blank" rel="sponsored noopener"' in link
            assert 'k=' not in link
        assert html.count(all_discounts.DISCLOSURE) >= 2
        if name != 'index.html':
            assert '/api/search' not in html
        result = subprocess.run(['node', 'tests/all_discounts_logic.cjs', str(out / name)],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
    css = (out / 'header-polish.css').read_text()
    assert ':is(#analyze-search,.all-discounts)' in css
    assert '--pill-colors:#2563eb,#0870b4 52%,#4f46e5' in css  # operator: blues, not Analyze's purple
    assert 'prefers-reduced-motion:reduce' in css
    assert 'discounts-typed:has(#q:not(:placeholder-shown))' in css
    assert '999px 10px' not in css and 'inset 0 0 0 2px rgba(255,255,255' not in css
    if cfg.header_polish:
        desktop = css[css.index(':root{--tab-card'):].split('@media(max-width:899px)', 1)[0]
        assert 'flex-wrap:wrap;justify-content:center' in desktop
        assert 'overflow-x:auto' not in desktop and 'mask-image' not in desktop
    # Every reference is content addressed, including pages outside the search flow.
    import hashlib
    for name in ('header-polish.css', 'page-polish.css'):
        asset = out / name
        if asset.exists():
            version = hashlib.sha256(asset.read_bytes()).hexdigest()[:12]
            for html_path in out.rglob('*.html'):
                for found in re.findall('/' + name + r'\?v=([a-f0-9]+)', html_path.read_text()):
                    assert found == version


def test_compact_css_preserves_values_selectors_and_escapes():
    source = r'''/* remove */ .a :hover {content: " a; b { c } ";
      background: url("https://example.test/a(b)?q=one two"); width: calc(100% - 4px);}
      .escaped\3a name {--value: "/* keep */"; color: red}'''
    compact = all_discounts.compact_css(source)
    assert 'remove' not in compact and '.a :hover{' in compact
    for literal in ('" a; b { c } "', 'url("https://example.test/a(b)?q=one two")',
                    'calc(100% - 4px)', r'.escaped\3a name', '"/* keep */"'):
        assert literal in compact
    assert all_discounts.compact_css(compact) == compact


def test_white_text_contrast_through_charge_and_shine():
    # Include all stops and the brightest point of the gold sheen, in both themes.
    css = all_discounts.shared_css()
    backgrounds = re.findall(r'(?:linear-gradient\([^;]+|--pill-colors:[^;]+)', css)
    colors = set(re.findall(r'#[a-f0-9]{6}', ''.join(backgrounds)))
    assert len(colors) >= 12
    assert 'inset 0 0' not in css and '999px 10px' not in css
    sheen = float(re.search(r'rgba\(255,196,0,([.\d]+)\)', css)[1])
    def luminance(rgb):
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))
    for color in colors:
        rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        for alpha in (0, sheen):
            painted = [c * (1 - alpha) + s * alpha for c, s in zip(rgb, (1, 196 / 255, 0))]
            assert 1.05 / (luminance(painted) + .05) >= 4.5, (color, alpha)


def test_tracking_counts_without_query_or_url(tmp_path):
    cfg = pre_buttons_to_tools(load_config())
    cfg.tracking = SAMPLE
    calls = [['search_discounts', {key: 'PRIVATE' for key in ('query', 'url', 'store', 'category', 'method')}]]
    scenarios = {name: dict(extra, calls=calls) for name, extra in (
        ('open', {}), ('gpc', {'gpc': True}), ('out', {'storage': {'wp.adsOptOut': '1'}}),
        ('internal', {'search': '?internal=1'}))}
    (tmp_path / 'snippet.js').write_text(_script(tracking.head_snippet(cfg)))
    (tmp_path / 'harness.cjs').write_text(HARNESS)
    (tmp_path / 'scenarios.json').write_text(json.dumps(scenarios))
    result = subprocess.run(['node', 'harness.cjs', 'snippet.js', 'scenarios.json'], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'PRIVATE' not in result.stdout
    for output in json.loads(result.stdout).values():
        assert output['error'] is None
        assert [e for e in output['dataLayer'] if e[:2] == ['event', 'search_discounts']] == [
            ['event', 'search_discounts', {'send_to': SAMPLE['ga4_measurement_id']}]]
    cfg.all_discounts_button = False
    assert 'search_discounts' not in tracking.head_snippet(cfg)
