"""Task 301: release pins, first-paint CSS, inline/lazy result placement."""
import json
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import search_box_glow
from tests.test_offscreen_cards import offers
from tests.test_pwa import tree_hash
from tests.test_search_more import config as more_config, offline
from tests.test_search_amazon import run_node
from tests.test_support import render

PINS = json.loads(Path('tests/fixtures/search_box_glow_off.json').read_text())
# Task 300 rewrote the related-results markup after these were recorded; they compare with their own OFF render.
MOVED = ['current', 'current-inline']


def config(mode):
    cfg = load_config() if mode.startswith('current') else more_config(mode)
    cfg.drop_shuffle = False
    cfg.card_tidy = False
    if mode.startswith('current'):
        cfg.search = dict(cfg.search, tokens_per_day=5000)  # recorded before the ad-day raise
    if mode.endswith('inline'):
        cfg.search_analyze['lazy'] = False
    if mode.endswith('more-off'):
        cfg.search_more['enabled'] = False
    return cfg


@pytest.mark.parametrize('mode', [*PINS, *MOVED])
def test_recorded_off_missing_back_off_bytes(tmp_path, mode):
    cfg = config(mode)
    pin = PINS.get(mode)
    for value in (False, None, True, False):
        if value is None:
            del cfg.search_box_glow
        else:
            cfg.search_box_glow = value
        out, *_ = render(tmp_path, cfg, offers())
        pin = pin or tree_hash(out)
        assert (tree_hash(out) == pin) is (not value)


@pytest.mark.parametrize('value', [None, 0, 1, 'true', [], {}])
def test_strict_config(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'search_box_glow': value}))
    with pytest.raises(ValueError, match='^search_box_glow must be a boolean$'):
        load_config(path)


def test_defaults_and_css(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config().search_box_glow is True
    assert load_config(path).search_box_glow is AppConfig().search_box_glow is False
    css = search_box_glow.CSS
    for value in ('border:3px solid transparent', 'wpspin 6s linear infinite',
                  ':focus-within::before', ':has(#q:not(:placeholder-shown))',
                  'animation-play-state:paused', 'prefers-reduced-motion:reduce',
                  '#34c759,#00bcd4,#7047eb', 'wpshine', 'wptwinkle'):
        assert value in css
    assert 'padding:' not in css and 'height:' not in css.replace('line-height:', '')


@pytest.mark.parametrize('lazy,more,boost', [(False, True, True), (True, True, True),
    (False, False, True), (True, False, True), (True, True, False)])
def test_emitted_behavior(tmp_path, lazy, more, boost):
    cfg = more_config()
    cfg.search_box_glow = True
    cfg.search_analyze['lazy'] = lazy
    cfg.search_more = {'enabled': more}
    cfg.search_boost['enabled'] = boost
    cfg.search_boost['exclude_words'].append('butt plug')
    out, *_ = render(tmp_path, cfg, offers())
    page = (out / 'index.html').read_text()
    assert search_box_glow.CSS in page
    source = (out / 'search-analyze.js').read_text() if lazy else page
    assert '<h2>Search results</h2>' in source
    assert 'id="search-result-top" class="search-amazon-top"' in source
    assert search_box_glow.BUDGET in source and 'limit.' not in source
    harnesses = ['search_analyze_lazy_logic.cjs'] if lazy else ([] if more else ['search_analyze_logic.cjs'])
    if more:
        harnesses += ['search_more_logic.cjs', 'search_box_glow_logic.cjs']
    for harness in harnesses:
        result = run_node(harness, out / 'index.html')
        assert result.returncode == 0, result.stdout + result.stderr


def test_native_fixture_without_launching_chromium(tmp_path):
    result = subprocess.run(['node', '-e',
        "const {buildFixture} = require('./tests/search_box_glow_browser.cjs'); "
        "buildFixture(process.argv[1], true, true);", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'wpspin 6s linear infinite' in (tmp_path / 'site/index.html').read_text()
    assert 'id="search-result-top"' in (tmp_path / 'site/search-analyze.js').read_text()
