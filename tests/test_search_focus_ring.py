"""Operator 2026-10-21 option A: no search ring on click or tap; keyboard Tab keeps it."""
import json
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import search_focus_ring as feature
from tests.history_pins import pre_search_focus_ring, pre_task_290_install_raw, pre_timer_center
from tests.test_pwa import tree_hash
from tests.test_search_more import offline  # noqa: F401
from tests.test_support import render

SEARCH_PAGES = ('index.html', 'black-friday/index.html', 'cyber-monday/index.html')


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_bool(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'search_focus_ring_keyboard_only': value}))
    with pytest.raises(ValueError, match='^search_focus_ring_keyboard_only must be a boolean$'):
        load_config(path)


def test_defaults_and_history(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).search_focus_ring_keyboard_only is AppConfig().search_focus_ring_keyboard_only is False
    assert load_config().search_focus_ring_keyboard_only is True
    for helper in (pre_search_focus_ring, pre_timer_center):
        assert helper(load_config()).search_focus_ring_keyboard_only is False
    assert 'search_focus_ring_keyboard_only' not in pre_task_290_install_raw({'search_focus_ring_keyboard_only': True})
    assert not feature.enabled(type('Config', (), {'search_focus_ring_keyboard_only': 1})())
    assert feature.site_version('old', AppConfig()) == 'old'


def test_off_missing_on_rerun_back_off_bytes(tmp_path):
    cfg = pre_search_focus_ring(load_config())
    hashes = []
    for value in (False, None, True, True, False):
        if value is None:
            del cfg.search_focus_ring_keyboard_only
        else:
            cfg.search_focus_ring_keyboard_only = value
        out, *_ = render(tmp_path, cfg)
        hashes.append(tree_hash(out))
        for name in SEARCH_PAGES:
            assert (out / name).read_text().count(feature.SNIPPET) == int(bool(value)), name
        for path in out.rglob('*.html'):
            if 'class="searchfield"' not in path.read_text():
                assert 'data-kbd' not in path.read_text(), path
    assert hashes[0] == hashes[1] == hashes[4] != hashes[2] == hashes[3]


def test_click_has_no_ring_and_tab_keeps_it_in_real_chromium(tmp_path):
    out, *_ = render(tmp_path, load_config())
    result = subprocess.run(['node', str(Path(__file__).with_name('search_focus_ring_browser.cjs')), str(out)],
                            capture_output=True, text=True, timeout=180)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count('click/tap none, Tab solid, click again none') == 4
