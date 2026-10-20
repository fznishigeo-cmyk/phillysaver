"""Task 342: exact rollback and the yellow All Discounts results exit."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import search_discounts_button as button
from tests.history_pins import pre_card_click_1020, pre_search_discounts_button, pre_search_buttons_1019
from tests.test_pwa import tree_hash
from tests.test_search_more import offline  # noqa: F401
from tests.test_support import render

PINS = json.loads(Path('tests/fixtures/task342_off.json').read_text())


@pytest.mark.parametrize('flag', ['search_discounts_button', 'card_click_1020'])
@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_bool(tmp_path, flag, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({flag: value}))
    with pytest.raises(ValueError, match='^' + flag + ' must be a boolean$'):
        load_config(path)


@pytest.mark.parametrize('flag', ['search_discounts_button', 'card_click_1020'])
def test_defaults_and_pins(tmp_path, flag):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert getattr(load_config(path), flag) is getattr(AppConfig(), flag) is False
    assert getattr(load_config(), flag) is True
    assert getattr(pre_search_buttons_1019(load_config()), flag) is False
    for value in (False, True):
        path.write_text(json.dumps({flag: value}))
        assert getattr(load_config(path), flag) is value


@pytest.mark.parametrize('flag', ['search_discounts_button', 'card_click_1020'])
@pytest.mark.parametrize('mode', PINS)
def test_sibling_off_missing_rerun_back_off_bytes(tmp_path, flag, mode):
    cfg = pre_card_click_1020(pre_search_discounts_button(load_config()))
    cfg.tool_pages_1020 = cfg.fresh_more_plain = False  # Siblings released together.
    if mode == 'inline':
        cfg.strip_comments = False
        cfg.search_analyze['lazy'] = False
    if mode == 'membership':
        cfg.support_enabled = False
    render(tmp_path, cfg)
    hashes = []
    for value in (False, None, True, True, False):
        if value is None:
            delattr(cfg, flag)
        else:
            setattr(cfg, flag, value)
        out, *_ = render(tmp_path, cfg)
        hashes.append(tree_hash(out))
    assert hashes[0] == hashes[1] == hashes[4] == PINS[mode]
    assert hashes[2] == hashes[3] != hashes[0]


@pytest.mark.parametrize('lazy', [False, True])
@pytest.mark.parametrize('more', [False, True])
def test_emitted_markup_css_urls_and_recovery(tmp_path, lazy, more):
    cfg = load_config()
    cfg.search_analyze['lazy'] = lazy
    cfg.search_more['enabled'] = more
    cfg.affiliate_amazon_tag = 'fixture-coupons-20'
    out, *_ = render(tmp_path, cfg)
    html = (out / 'index.html').read_text()
    assert html.index('</header>') < html.index('id="search-amazon-result"') < html.index('id="content"')
    assert 'See Amazon discounts →</a>' in html
    assert 'Search Amazon for &quot;' not in html
    assert 'amazon.innerHTML = q && count ? searchAmazonHtml(false) : "";' in html
    css = (out / 'header-polish.css').read_text()
    assert button.CSS in css
    tool = Path('dealradar/publish/templates/tool-pages.css').read_text()
    for declaration in ('background:#ffc83d', 'color:#111', 'font-weight:800', 'border-radius:999px',
                        'min-height:50px', 'padding:14px 24px', 'font-size:18px', 'line-height:1.25'):
        assert declaration in css and declaration in tool
    assert '@media(max-width:899px){a.search-discounts{width:100%}}' in css
    # #111 on the shared yellow exceeds AA in both themes.
    def luminance(rgb):
        values = [x / 255 for x in rgb]
        return sum((v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4) * w
                   for v, w in zip(values, (.2126, .7152, .0722)))
    assert (luminance((255, 200, 61)) + .05) / (luminance((17, 17, 17)) + .05) >= 4.5
    for name in ('header-polish.css', 'page-polish.css'):
        version = hashlib.sha256((out / name).read_bytes()).hexdigest()[:12]
        for page in out.rglob('*.html'):
            assert all(v == version for v in re.findall('/' + name + r'\?v=([a-f0-9]+)', page.read_text()))
    for name in ('index.html', 'black-friday/index.html', 'cyber-monday/index.html'):
        result = subprocess.run(['node', 'tests/search_discounts_button_logic.cjs', str(out / name)],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
