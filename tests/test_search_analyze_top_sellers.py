"""Task 320: strict gate, frozen OFF bytes, live/local results and truthful proof."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import analyze_top_sellers, site
from tests.history_pins import pre_search_polish_1019, pre_task_319
from tests.test_offscreen_cards import offers
from tests.test_pwa import tree_hash
from tests.test_search_analyze import analyze_config
from tests.test_search_more import offline  # noqa: F401 (offline + fixed publish time)
from tests.test_support import render


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_flag(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'analyze_top_sellers': value}))
    with pytest.raises(ValueError, match='^analyze_top_sellers must be a boolean$'):
        load_config(path)


def test_default_and_shipped_flag(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).analyze_top_sellers is AppConfig().analyze_top_sellers is False
    assert load_config().analyze_top_sellers is True


PINS = json.loads(Path('tests/fixtures/analyze_top_sellers_off.json').read_text())


@pytest.mark.parametrize('mode', PINS)
def test_off_missing_back_off_keep_release_bytes(tmp_path, mode):
    cfg = pre_task_319(pre_search_polish_1019(load_config()))
    lazy, more = map(int, mode.split('-'))
    cfg.search_analyze['lazy'] = bool(lazy)
    cfg.search_more['enabled'] = bool(more)
    for flag in (False, None, True, False):
        if flag is None:
            del cfg.analyze_top_sellers
        else:
            cfg.analyze_top_sellers = flag
        out, *_ = render(tmp_path, cfg, offers())
        if flag:
            assert tree_hash(out) != PINS[mode]
            before = tree_hash(out)
            render(tmp_path, cfg, offers())
            assert tree_hash(out) == before
        else:
            assert tree_hash(out) == PINS[mode]
            assert 'analyze_top_sellers' not in (out / 'search-config.json').read_text()


@pytest.mark.parametrize('lazy', [False, True])
@pytest.mark.parametrize('more', [False, True])
@pytest.mark.parametrize('tidy', [False, True])
def test_composed_rendering_and_syntax(tmp_path, lazy, more, tidy):
    cfg = analyze_config()
    cfg.analyze_top_sellers = True
    cfg.search_analyze['lazy'] = lazy
    cfg.search_more['enabled'] = more
    cfg.card_tidy = tidy
    out, *_ = render(tmp_path, cfg)
    page = (out / 'index.html').read_text()
    settings = json.loads((out / 'search-config.json').read_text())
    assert settings['analyze_top_sellers'] is True
    assert settings['cache_hours'] == 6
    assert 'lubricant' in settings['analyze_exclude']
    asset = out / 'search-more.js'
    if more:
        script = asset.read_bytes()
        assert '/search-more.js?v=' + hashlib.sha256(script).hexdigest()[:12] in page
    for i, code in enumerate([*re.findall(r'<script>(.*?)</script>', page, re.S),
                             *(p.read_text() for p in (out / 'search-analyze.js', asset) if p.exists())]):
        script = tmp_path / f'check-{i}.js'
        script.write_text(code)
        result = subprocess.run(['node', '--check', script], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    result = subprocess.run(['node', 'tests/analyze_top_sellers_logic.cjs', str(out / 'index.html')],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Analyze top sellers rendering passed' in result.stdout


def test_shared_matcher_is_the_existing_search_matcher():
    # A common implementation for the page and Function; do not quietly fork
    # typo/model handling while adding the best-seller ordering.
    html = (site.TEMPLATES_DIR / 'index.html').read_text()
    start = html.index('  function normalizeSearch(')
    expected = html[start:html.index('/*search-amazon*/', start)].strip()
    source = analyze_top_sellers.PRODUCTS.read_text()
    shared = source[source.index('  function normalizeSearch('):source.index('export function monthlyBoughtLabel')]
    assert shared.replace('export ', '').strip() == expected
