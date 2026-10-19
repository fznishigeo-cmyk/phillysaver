"""Task 304: exact rollback bytes, event entry, safe handoff and Support terms."""
import json
import re
import subprocess
from html import escape
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import page_polish
from tests.history_pins import pre_all_discounts_button, pre_page_polish, pre_task_295, pre_task_290_install_raw
from tests.test_pwa import tree_hash
from tests.test_search_more import offline
from tests.test_search_amazon import run_node
from tests.test_support import render

PINS = json.loads(Path('tests/fixtures/page_polish_off.json').read_text())
MONTHLY_NOTE = ('Once: any amount. Monthly: from $5 ($1 times the number you pick, 5 or more), renewing each month '
                'until you cancel (email hello@wowpriced.com anytime). Both through Stripe. '
                'wowpriced is a for-profit business: support is not tax-deductible and buys '
                'nothing extra; every deal stays free.')
ONCE_NOTE = ('One-time payment, any amount, through Stripe. wowpriced is a for-profit business: '
             'support is not tax-deductible and buys nothing extra; every deal stays free.')


def config(mode='current'):
    cfg = pre_all_discounts_button(load_config())
    if mode == 'inline':
        cfg.search_analyze['lazy'] = False
    if mode == 'membership':
        cfg.support_enabled = False
    return cfg


@pytest.mark.parametrize('mode', PINS)
def test_off_missing_back_off_bytes(tmp_path, mode):
    cfg = config(mode)
    cfg.drop_fresh = False  # Isolate the separate drop feature.
    # The recorded base predates task 302's reviewed identifier/regex changes.
    # Retain its hashes, and verify rollback against this config's own OFF bytes.
    baseline = None
    for value in (False, None, True, False):
        if value is None:
            del cfg.page_polish
        else:
            cfg.page_polish = value
        out, *_ = render(tmp_path, cfg)
        baseline = baseline or tree_hash(out)
        assert (tree_hash(out) == baseline) is (not value)
        assert (out / 'page-polish.css').exists() is bool(value)


@pytest.mark.parametrize('value', [None, 0, 1, 'true', [], {}])
def test_strict_config(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'page_polish': value}))
    with pytest.raises(ValueError, match='^page_polish must be a boolean$'):
        load_config(path)


def test_defaults_and_history_chain(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).page_polish is AppConfig().page_polish is False
    assert load_config().page_polish is True
    assert pre_page_polish(load_config()).page_polish is False
    assert pre_task_295(load_config()).page_polish is False
    assert 'page_polish' not in pre_task_290_install_raw({'page_polish': True})
    path.write_text('page_polish: false')
    assert load_config(path).page_polish is False


@pytest.mark.parametrize('mode', ['current', 'inline'])
def test_emitted_search_and_query_logic(tmp_path, mode):
    out, *_ = render(tmp_path, config(mode))
    home = (out / 'index.html').read_text()
    css = (out / 'page-polish.css').read_text()
    assert page_polish.search_style() in home
    assert 'if (!applyPagePolishQuery()) { render(); restoreInitialBrowse(); }' in home
    for slug in ('black-friday', 'cyber-monday'):
        event = (out / slug / 'index.html').read_text()
        assert 'role="search" action="/" method="get"' in event
        assert '<label class="sr-only" for="q">Search any product or paste an Amazon link</label>' in event
        assert 'maxlength="200"' in event
        assert '<button id="analyze-search" type="submit">Analyze</button>' in event
        assert page_polish.search_style() in event
        assert 'conic-gradient(from var(--wpspin),#e60000' in css
        assert 'linear-gradient(120deg,#5b21b6,#9333ea 50%,#db2777)' in css
        assert 'min-height:46px;min-width:96px' in event
        assert 'prefers-reduced-motion:reduce' in css
        assert '/api/search' not in event
    result = subprocess.run(['node', 'tests/page_polish_logic.cjs', str(out)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    result = run_node('page_polish_drop_logic.cjs', out / 'index.html', out / 'deals.json')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('monthly', [False, True])
def test_support_exact_buttons_and_legal_text(tmp_path, monthly):
    cfg = config()
    if not monthly:
        cfg.support_monthly_link = ''
    cfg.page_polish = False
    out, *_ = render(tmp_path, cfg)
    before = (out / 'subscribe.html').read_text()
    cfg.page_polish = True
    out, *_ = render(tmp_path, cfg)
    after = (out / 'subscribe.html').read_text()
    buttons = r'<a class="pwbtn btn" data-support(?:-monthly)? href="[^"]+"[^>]*>[^<]+</a>'
    assert re.findall(buttons, after) == re.findall(buttons, before)
    assert len(re.findall(buttons, after)) == (2 if monthly else 1)
    assert f'href="{escape(cfg.support_payment_link, quote=True)}"' in after
    if monthly:
        assert f'href="{escape(cfg.support_monthly_link, quote=True)}"' in after
        assert '>Support once</a>' in after and '>Support monthly</a>' in after
    assert (MONTHLY_NOTE if monthly else ONCE_NOTE) in after
    assert re.search(r'<p>wowpriced is free for everyone\..*?</p>', before)[0] in after
    assert '<main class="brand-page"><section class="brand-hero">' in after
    assert '<div class="brand-collage support-visual" aria-hidden="true">' in after
    assert '<section class="brand-story support-join">' in after
    assert 'prefers-color-scheme:dark' in after
    assert after.count('<main ') == after.count('</main>') == 1


def test_membership_support_page_unchanged(tmp_path):
    cfg = config('membership')
    cfg.page_polish = False
    out, *_ = render(tmp_path, cfg)
    before = (out / 'subscribe.html').read_bytes()
    cfg.page_polish = True
    out, *_ = render(tmp_path, cfg)
    assert (out / 'subscribe.html').read_bytes() == before


def test_independent_home_style_switches(tmp_path):
    cfg = config()
    cfg.search_box_glow = cfg.card_tidy = False
    out, *_ = render(tmp_path, cfg)
    home = (out / 'index.html').read_text()
    assert page_polish.search_style() not in home
    assert 'var pagePolishQuery =' in home
    assert page_polish.search_style() in (out / 'black-friday/index.html').read_text()


def test_active_search_defers_automatic_join(tmp_path):
    out, *_ = render(tmp_path, config())
    result = run_node('page_polish_prompt_logic.cjs', out / 'index.html')
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_fixture_without_launching_chromium(tmp_path):
    result = subprocess.run(['node', '-e',
        "require('./tests/page_polish_browser.cjs').buildFixture(process.argv[1]);", str(tmp_path)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'id="event-search"' in (tmp_path / 'site/black-friday/index.html').read_text()
    assert 'support-visual' in (tmp_path / 'site/subscribe.html').read_text()


@pytest.mark.parametrize('mode', ['support', 'membership', 'unstripped'])
def test_older_recorded_hashes_still_hold(tmp_path, mode):
    from tests.test_drop_shuffle import config as old_config
    from tests.test_offscreen_cards import offers

    pins = json.loads(Path('tests/fixtures/drop_shuffle_off.json').read_text())
    out, *_ = render(tmp_path, old_config(mode), offers())
    assert tree_hash(out) == pins[mode]


def test_home_payload_headroom(tmp_path):
    from dealradar.rank import photoless_expired
    from scripts.publication_budget import analyze
    from tests.phone_budget_fixture import NOW, fixture_offers, publish_fixture

    cfg = config()
    cfg.all_discounts_button = True
    cfg.phone_budget['enabled'] = True

    def inspect(db, out):
        expected = sum(not photoless_expired(d, cfg, NOW) for d in fixture_offers())
        report = analyze(out, cfg, expected_offers=expected)
        assert report['ok'], report
        assert report['metrics']['home_html']['gzip'] < cfg.phone_budget['home_html_gzip_max_bytes'] - 1000

    publish_fixture(tmp_path, cfg, inspect=inspect)
