"""Task 305: exact rollback bytes, strict switch, shared pages and native fixture."""
import json
import re
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import card_tidy, header_polish, page_polish, search_box_glow
from tests.history_pins import pre_all_discounts_button, pre_drop_fresh, pre_page_polish
from tests.test_offscreen_cards import offers
from tests.test_pwa import tree_hash
from tests.test_search_more import offline  # noqa: F401 (fixed clock, no network)
from tests.test_support import NOW, render

PINS = json.loads(Path('tests/fixtures/header_polish_off.json').read_text())


def config(mode):
    cfg = pre_all_discounts_button(load_config())
    if mode == 'inline':
        cfg.search_analyze['lazy'] = False
        cfg.strip_comments = False
    if mode == 'membership':
        cfg.support_enabled = False
        cfg.paywall_open_access = False
    return cfg


@pytest.mark.parametrize('mode', PINS)
def test_off_missing_back_off_bytes(tmp_path, mode):
    # These pins predate the independently shipped drop and page features.
    cfg = pre_page_polish(pre_drop_fresh(config(mode)))
    # Task 310 changed the enabled card_tidy output used by the recorded base.
    # Keep those hashes untouched; compare this config's own OFF render instead.
    cfg.header_polish = False
    render(tmp_path, cfg, offers())  # Initialize state before comparing deals.json.
    hashes = []
    for value in (False, None, True, False):
        if value is None:
            del cfg.header_polish
        else:
            cfg.header_polish = value
        out, *_ = render(tmp_path, cfg, offers())
        hashes.append(tree_hash(out))
        assert (out / header_polish.ASSET).exists() is bool(value)
    assert hashes[0] == hashes[1] == hashes[3] != hashes[2]


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_config(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'header_polish': value}))
    with pytest.raises(ValueError, match='^header_polish must be a boolean$'):
        load_config(path)


def test_defaults_and_boolean_config(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).header_polish is AppConfig().header_polish is False
    assert load_config().header_polish is True
    for value in (True, False):
        path.write_text(json.dumps({'header_polish': value}))
        assert load_config(path).header_polish is value


@pytest.mark.parametrize('seasonal', [False, True])
@pytest.mark.parametrize('shared_css', [False, True])
def test_only_home_layout_changes_and_rerun_is_stable(tmp_path, seasonal, shared_css):
    cfg = pre_all_discounts_button(load_config())
    cfg.page_polish = shared_css
    if seasonal:
        cfg.seo_event_pages_events[0]['date'] = NOW.date().isoformat()
    rows = [dict(d, price_rechecked_at=NOW.isoformat(), first_seen_at=NOW.isoformat(),
                 last_seen_at=NOW.isoformat()) for d in offers()[:12]]
    cfg.header_polish = False
    out, _, full, _ = render(tmp_path, cfg, rows)
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob('*') if p.is_file()}
    version = full['meta']['site_version'].encode()
    cfg.header_polish = True
    out, _, full, _ = render(tmp_path, cfg, rows)
    updated = full['meta']['site_version'].encode()
    assert updated != version
    assert {p.relative_to(out) for p in out.rglob('*') if p.is_file()} == before.keys() | {Path(header_polish.ASSET)}
    assert (out / header_polish.ASSET).read_text() == header_polish.CSS
    assert any(str(p).startswith('deal/') for p in before)
    assert all(Path(event['slug']) / 'index.html' in before for event in cfg.seo_event_pages_events)
    for name, body in before.items():
        if name != Path('index.html'):
            assert (out / name).read_bytes() == body.replace(version, updated), name
    page = (out / 'index.html').read_text()
    head = re.search(r'<header>.*?</header>', page, re.S)[0]
    assert 'id="cattabs"' in head and page.count('id="cattabs"') == 1
    assert head.count('id="tagline"') == head.count('id="q"') == 1
    assert 'event-links' not in head
    if seasonal:
        assert '<nav class="hrow event-links"' in page.split('</header>', 1)[1]
    if shared_css:
        assert page_polish.search_style() in page
        assert (out / 'page-polish.css').read_text() == search_box_glow.CSS + card_tidy.CSS
    else:
        assert search_box_glow.CSS in page and card_tidy.CSS in page
    assert '<div class="hnav"><nav id="cattabs"' in head
    assert head.count('id="fresh-drop-pill"') == head.count('id="fresh-drop-toast"') == 1
    assert "import('/drop-fresh.js?v=" in page
    # Layout does not add/change executable code, including lazy Analyze and tab handlers.
    scripts = lambda html: re.findall(r'<script\b[^>]*>.*?</script>', html, re.S)
    assert scripts(page) == scripts(before[Path('index.html')].replace(version, updated).decode())
    pin = tree_hash(out)
    render(tmp_path, cfg, rows)
    assert tree_hash(out) == pin


def test_native_fixture_without_launching_chromium(tmp_path):
    result = subprocess.run(['node', '-e',
        "const {buildFixture} = require('./tests/header_polish_browser.cjs'); "
        "buildFixture(process.argv[1]);", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    page = (tmp_path / 'site/index.html').read_text()
    assert 'grid-template-columns:auto minmax(0,940px) auto auto' in (tmp_path / 'site' / header_polish.ASSET).read_text()
    assert 'id="cattabs"' in re.search(r'<header>.*?</header>', page, re.S)[0]
