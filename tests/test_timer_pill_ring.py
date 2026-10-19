"""Task 329: release rollback, shared-asset isolation and the real clock painter."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import timer_pill_ring as ring
from tests.history_pins import pre_phone_tagline_count, pre_task_290_install_raw, pre_timer_pill_ring, pre_tool_pages
from tests.test_pwa import tree_hash
from tests.test_search_more import offline  # noqa: F401
from tests.test_support import render

PINS = json.loads(Path('tests/fixtures/timer_pill_ring_off.json').read_text())


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_bool(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'timer_pill_ring': value}))
    with pytest.raises(ValueError, match='^timer_pill_ring must be a boolean$'):
        load_config(path)


def test_defaults_and_history(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).timer_pill_ring is AppConfig().timer_pill_ring is False
    assert load_config().timer_pill_ring is True
    for value in (True, False):
        path.write_text(json.dumps({'timer_pill_ring': value}))
        assert load_config(path).timer_pill_ring is value
    assert pre_timer_pill_ring(load_config()).timer_pill_ring is False
    assert pre_phone_tagline_count(load_config()).timer_pill_ring is False
    assert 'timer_pill_ring' not in pre_task_290_install_raw({'timer_pill_ring': True})
    assert not ring.enabled(type('Config', (), {'timer_pill_ring': 1})())
    assert ring.template('unchanged', AppConfig()) == 'unchanged'


@pytest.mark.parametrize('mode', PINS)
def test_original_release_off_missing_rerun_and_back_off_bytes(tmp_path, mode):
    # Task 328 ran in parallel; these pins predate its four preview files too.
    cfg = pre_tool_pages(pre_timer_pill_ring(load_config()))
    if mode == 'inline':
        cfg.strip_comments = False
        cfg.search_analyze['lazy'] = False
    if mode == 'membership':
        cfg.support_enabled = False
    hashes = []
    for value in (False, None, True, True, False):
        if value is None:
            del cfg.timer_pill_ring
        else:
            cfg.timer_pill_ring = value
        out, *_ = render(tmp_path, cfg)
        hashes.append(tree_hash(out))
        assert ('--pill-progress' in (out / 'index.html').read_text()) is bool(value)
    assert hashes[0] == hashes[1] == hashes[4] == PINS[mode]
    assert hashes[2] == hashes[3] != PINS[mode]


@pytest.mark.parametrize('shared_header,discounts', [(False, False), (True, False), (True, True)])
def test_only_painter_and_header_asset_change(tmp_path, shared_header, discounts):
    cfg = pre_timer_pill_ring(load_config())
    cfg.all_discounts_button = discounts
    if not shared_header:
        cfg.header_polish = cfg.all_discounts_button = cfg.search_polish_1019 = False
        cfg.discounts_from_link = False
    out, _, full, _ = render(tmp_path, cfg)
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob('*') if p.is_file()}
    version = full['meta']['site_version'].encode()
    cfg.timer_pill_ring = True
    out, _, full, _ = render(tmp_path, cfg)
    updated = full['meta']['site_version'].encode()
    asset = Path('header-polish.css')
    assert {p.relative_to(out) for p in out.rglob('*') if p.is_file()} == before.keys()
    old_css = before.get(asset, b'')
    css = (out / asset).read_bytes() if (out / asset).exists() else b''
    if shared_header:
        # Match the emitted dial range with and without task 317's wider field.
        limit = 1599 if discounts else 1199
        query = f'@media(min-width:900px) and (max-width:{limit}px)'
        assert query.encode() in old_css
        assert css == old_css + (query + '{' + ring.CSS + '}').encode()
    else:
        assert css == old_css == b''
    old_hash = hashlib.sha256(old_css).hexdigest()[:12].encode()
    new_hash = hashlib.sha256(css).hexdigest()[:12].encode()
    for name, body in before.items():
        if name not in (Path('index.html'), asset):
            assert (out / name).read_bytes() == body.replace(version, updated).replace(old_hash, new_hash), name
    home = (out / 'index.html').read_text()
    assert home.count('style.setProperty("--pill-progress", f)') == 1
    # The pseudo-element contributes no DOM, accessible name or live region.
    header = lambda html: re.search(r'<header>.*?</header>', html, re.S)[0]
    assert header(home) == header(before[Path('index.html')].decode())
    for page in out.rglob('*.html'):
        assert all(found == new_hash.decode() for found in re.findall(
            r'/header-polish.css\?v=([a-f0-9]+)', page.read_text()))
    result = subprocess.run(['node', 'tests/timer_pill_ring_logic.cjs', str(out / 'index.html')],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_browser_harness_syntax():
    result = subprocess.run(['node', '--check', 'tests/header_polish_browser.cjs'],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
