"""Task 318: own-OFF rollback, footer-backed disclosures and emitted exits."""
import hashlib
import json
import re
import subprocess

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import all_discounts, search_polish_1019 as polish
from tests.history_pins import pre_all_discounts_button, pre_search_polish_1019, pre_task_290_install_raw
from tests.test_pwa import tree_hash
from tests.test_search_more import offline  # noqa: F401
from tests.test_support import render


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_bool(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'search_polish_1019': value}))
    with pytest.raises(ValueError, match='^search_polish_1019 must be a boolean$'):
        load_config(path)


def test_defaults_and_history(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).search_polish_1019 is AppConfig().search_polish_1019 is False
    assert load_config().search_polish_1019 is True
    assert pre_search_polish_1019(load_config()).search_polish_1019 is False
    assert pre_all_discounts_button(load_config()).search_polish_1019 is False
    assert 'search_polish_1019' not in pre_task_290_install_raw({'search_polish_1019': True})
    for value in (False, True):
        path.write_text(json.dumps({'search_polish_1019': value}))
        assert load_config(path).search_polish_1019 is value
    assert not polish.enabled(type('Config', (), {'search_polish_1019': 1})())


@pytest.mark.parametrize('mode', ['shipped', 'inline', 'membership'])
def test_off_missing_back_off_exact_bytes(tmp_path, mode):
    cfg = pre_search_polish_1019(load_config())
    if mode == 'inline':
        cfg.search_analyze['lazy'] = False
        cfg.strip_comments = False
    if mode == 'membership':
        cfg.support_enabled = False
    render(tmp_path, cfg)  # Initialise state before comparing the config's own OFF.
    hashes = []
    for value in (False, None, True, True, False):
        if value is None:
            del cfg.search_polish_1019
        else:
            cfg.search_polish_1019 = value
        out, *_ = render(tmp_path, cfg)
        hashes.append(tree_hash(out))
        home = (out / 'index.html').read_text()
        assert ('id="search-hint"' not in home) is bool(value)
    assert hashes[0] == hashes[1] == hashes[4] != hashes[2] == hashes[3]


@pytest.mark.parametrize('mode', ['shipped', 'inline', 'no-discounts', 'no-header', 'no-page', 'no-footer'])
def test_emitted_polish_and_exits(tmp_path, mode):
    cfg = load_config()
    cfg.affiliate_amazon_tag = 'example-20'
    if mode == 'inline':
        cfg.search_analyze['lazy'] = False
    if mode == 'no-discounts':
        cfg.all_discounts_button = False
    if mode == 'no-header':
        cfg.header_polish = False
    if mode == 'no-page':
        cfg.page_polish = False
    if mode == 'no-footer':
        cfg.affiliate_disclosure = 'Affiliate links may earn us a commission.'
    out, *_ = render(tmp_path, cfg)
    pages = []
    for path in out.rglob('*.html'):
        html = path.read_text()
        if 'id="q"' not in html:
            continue
        pages.append(path.relative_to(out).as_posix())
        assert 'id="search-hint"' not in html
        assert all('search-hint' not in ids.split() for ids in re.findall(r'aria-describedby="([^"]*)"', html))
        if mode == 'no-footer':
            assert not polish.has_footer_disclosure(html)
            if cfg.all_discounts_button:
                assert '<small>' + all_discounts.DISCLOSURE + '</small>' in html
                assert '<p class="discounts-disclosure">' + all_discounts.DISCLOSURE + '</p>' in html
        else:
            assert polish.has_footer_disclosure(html), path
            if cfg.all_discounts_button:
                assert '<small>' not in re.search(r'<div class="discounts-mobile">.*?</div>', html, re.S)[0]
            assert '<p class="discounts-disclosure">' not in html
        if path.name == 'index.html' and path.parent == out:
            assert '<span class="join-why"> · Price Alerts</span>' in html
            assert ' · price alerts</span>' not in html
            assert '.searchfield svg{top:14px}' not in html
            assert '#clearsearch{width:44px;height:44px;top:6px}' in html
    assert 'index.html' in pages
    if cfg.page_polish:
        assert 'black-friday/index.html' in pages and 'cyber-monday/index.html' in pages
    css = (out / 'header-polish.css').read_text()
    assert polish.TABS in css
    assert 'border-bottom:3px solid transparent;border-radius:0' not in css
    if cfg.all_discounts_button:
        assert '--pill-colors:#1265f4,#0061fe 52%,#0050d6' in css
        assert 'inset:6px 6px 6px auto' in css
        assert 'calc(var(--discount-width) + 12px);width:96px' in css
        assert '#q{height:56px}' in css
    if cfg.header_polish:
        width, header_width = (940, 1800) if cfg.all_discounts_button else (760, 1640)
        assert f'minmax(0,{width}px)' in css and f'max-width:{header_width}px' in css
        header = css[css.index(':root{--tab-card'):]
        desktop, phone = header.split('@media(max-width:899px)', 1)
        assert 'flex-wrap:wrap;justify-content:center' in desktop
        assert 'overflow-x:auto' not in desktop
        assert 'overflow-x:auto' in phone
    for name in ('header-polish.css', 'page-polish.css', 'search-analyze.js'):
        asset = out / name
        if asset.exists():
            version = hashlib.sha256(asset.read_bytes()).hexdigest()[:12]
            for path in out.rglob('*.html'):
                assert all(found == version for found in re.findall('/' + name + r'\?v=([a-f0-9]+)', path.read_text()))
    result = subprocess.run(['node', 'tests/search_polish_1019_logic.cjs', str(out / 'index.html'),
                             str(mode != 'no-footer').lower()], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_blue_stops_and_sheen_are_aa():
    # The suggested #1a73ff fails AA; retain #0061fe with a darker leading stop.
    for stop in ('1265f4', '0061fe', '0050d6'):
        rgb = [int(stop[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        for alpha in (0, .05):
            blend = [v * (1 - alpha) + s * alpha for v, s in zip(rgb, (1, 196 / 255, 0))]
            lum = sum((v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4) * weight
                      for v, weight in zip(blend, (.2126, .7152, .0722)))
            assert 1.05 / (lum + .05) >= 4.5


def test_missing_footer_and_describedby_tokens():
    source = '<input aria-describedby="before search-hint after"><p id="search-hint">Free price check · no sign-up</p>'
    assert polish.markup(source) == '<input aria-describedby="before after">'
    assert polish.markup('<input aria-describedby="search-hint">') == '<input>'
    # A disclosure outside a footer does not authorise removing the small line.
    source = '<p>As an Amazon Associate, wowpriced earns from qualifying purchases.</p>'
    assert not polish.has_footer_disclosure(source)
    assert polish.markup(source + polish.SMALL).endswith(polish.SMALL)
