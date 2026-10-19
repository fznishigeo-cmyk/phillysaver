"""card_tidy: folded checkout steps, cart chip wording, AI Analyze colors, no search filler."""
import json
import subprocess
from datetime import timedelta

import pytest

from dealradar.config import load_config
from dealradar.publish import card_tidy
from dealradar.publish.site import _serialize_deal
from tests.test_offscreen_cards import offers
from tests.test_publish_site import make_deal
from tests.test_pwa import tree_hash
from tests.test_search_analyze_lazy import config as analyze_config
from tests.test_search_more import offline  # noqa: F401 (autouse: no network, fixed clock)
from tests.test_support import NOW, render


def test_off_and_missing_keep_bytes_on_changes_them(tmp_path):
    cfg = load_config()
    hashes = []
    render(tmp_path, cfg, offers())  # the first render into a fresh state differs in deals.json
    for value in (False, None, True, False):
        if value is None:
            del cfg.card_tidy
        else:
            cfg.card_tidy = value
        out, *_ = render(tmp_path, cfg, offers())
        hashes.append(tree_hash(out))
    assert hashes[0] == hashes[1] == hashes[3] != hashes[2]


@pytest.mark.parametrize('lazy', [False, True])
@pytest.mark.parametrize('enabled', [False, True])
def test_product_labels_and_global_filler(tmp_path, lazy, enabled):
    cfg = load_config()
    cfg.card_tidy = enabled
    cfg.search_analyze['lazy'] = lazy
    rows = [dict(d, first_seen_at=NOW.isoformat(), last_seen_at=NOW.isoformat(),
                 price_rechecked_at=NOW.isoformat()) for d in offers()[:12]]
    out, *_ = render(tmp_path, cfg, rows)
    pages = []
    for path in out.rglob('*.html'):
        html = path.read_text()
        if 'function fillRows()' in html:
            pages.append(path.relative_to(out).as_posix())
            assert ('Looking for more?' not in html) is enabled, path
            assert ('.gridfill' not in html) is enabled, path
    assert 'index.html' in pages and 'best-deals-today/index.html' in pages
    assert any(p.startswith('deal/') for p in pages)
    assert any(p.startswith('deals/') for p in pages)
    assert any(p.startswith('black-friday/') for p in pages)
    result = subprocess.run(['node', 'tests/card_tidy_logic.cjs', str(out), str(enabled).lower()],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('lazy', [False, True])
@pytest.mark.parametrize('glow', [False, True])
@pytest.mark.parametrize('tidy', [False, True])
def test_analyze_verification_headings(tmp_path, lazy, glow, tidy):
    cfg = analyze_config('current')
    cfg.search_analyze['lazy'] = lazy
    cfg.search_box_glow = glow
    cfg.card_tidy = tidy
    cfg.affiliate_amazon_tag = 'example-20'
    out, *_ = render(tmp_path, cfg)
    # Use the real card projection: an eight-hour check clock must not turn
    # the single-source headline into a claim of verification.
    cards = {}
    for tier in ('unverified', 'verified', 'history-checked', 'corroborated'):
        cards[tier] = _serialize_deal(make_deal(
            item='Wireless headphones', store='Amazon', verify_tier=tier,
            asin='B000000001',
            canonical_url='https://www.amazon.com/dp/B000000001',
            image_url='https://m.media-amazon.com/images/I/photo.jpg',
            price_rechecked_at=(NOW - timedelta(hours=8)).isoformat(), codes=[],
            click_price_check={'asin': 'B000000001', 'final': 10.0,
                               'buybox': 10.0, 'at': NOW.isoformat()}), NOW, cfg=cfg)
    assert 'not yet confirmed' in cards['unverified']['headline']
    assert cards['unverified']['checked_at']
    assert cards['verified']['badge'] == 'verified'
    assert 'not yet confirmed' not in cards['verified']['headline']
    fixtures = tmp_path / 'cards.json'
    fixtures.write_text(json.dumps(cards))
    result = subprocess.run(['node', 'tests/card_tidy_analyze_logic.cjs', str(out / 'index.html'),
                             str(fixtures), str(tidy).lower()], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'verification headings passed' in result.stdout


@pytest.mark.parametrize('value', [None, 0, 1, 'true', [], {}])
def test_strict_config(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'card_tidy': value}))
    with pytest.raises(ValueError, match='^card_tidy must be a boolean$'):
        load_config(path)


def test_shipped_on_and_emitted(tmp_path):
    cfg = load_config()
    assert cfg.card_tidy is True
    out, *_ = render(tmp_path, cfg, offers())
    page = (out / 'index.html').read_text()
    analyze = (out / 'search-analyze.js').read_text()
    assert card_tidy.CSS in (out / 'page-polish.css').read_text()
    assert 'inset:2px 2px 2px auto;border:0;border-radius:999px 10px 10px 999px' in card_tidy.CSS
    assert 'background-clip:padding-box' not in card_tidy.CSS
    assert 'border-width:5px' not in card_tidy.CSS
    assert 'More details' in page and 'price drops in cart' in page
    assert 'Analyze &quot;\' +\n      esc(q) + \'&quot; on Amazon\\u00a0→' in page and 'See all &quot;\' +\n      esc(q)' not in page
    for phrase in ('busy. Please', 'unavailable right now', 'took too long', 'reached their limit', 'not shown in our results'):
        assert phrase not in analyze
    assert 'task.related.filter(function (d) { return d.image_url; })' in analyze and "See today's Amazon price drops →" in analyze
    assert "Get today's best Amazon price drops." in analyze and 'Analyze</span>' in page


def test_steps_fold_after_two_and_toggle():
    js = card_tidy.SHORT + r'''
      console.log(JSON.stringify({steps}));'''
    script = ('function esc(s){return String(s).replace(/</g,"&lt;");}\n'
              'var d = {how:["Shipping: Free on $35+", "Use code A", "use code a", "Extra 50% off", "In cart"]};\n' + js)
    out = json.loads(subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True).stdout)
    steps = out['steps']
    assert steps.startswith('Shipping: Free on $35+ &rarr; Use code A<span class="how-rest" hidden>')
    assert steps.count('Use code A') == 1 and 'use code a' not in steps
    assert 'Extra 50% off &rarr; In cart</span><br><button type="button" class="how-more"' in steps
    short = ('function esc(s){return s;}\nvar d = {how:["One", "Two"]};\n' + card_tidy.SHORT +
             'console.log(JSON.stringify({steps}));')
    assert json.loads(subprocess.run(['node', '-e', short], capture_output=True, text=True,
                                     check=True).stdout)['steps'] == 'One &rarr; Two'
