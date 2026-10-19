"""Task 303c: fresh deterministic heads and an honest clock-only moment."""
import json
import re
import socket
from datetime import timedelta

import httpx
import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import site, drop_fresh
from tests.history_pins import pre_drop_fresh
from tests.test_offscreen_cards import offers
from tests.test_pwa import tree_hash
from tests.test_search_amazon import run_node
from tests.test_support import NOW, render


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError('task303 is offline')
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', unavailable)
    monkeypatch.setattr(socket, 'create_connection', unavailable)
    monkeypatch.setattr(site, 'util_now', lambda: NOW)


@pytest.mark.parametrize('mode', ['support', 'membership', 'unstripped'])
def test_off_missing_back_off_bytes(tmp_path, mode):
    cfg = load_config()
    cfg.page_polish = False  # Isolate the separately shipped page styling.
    cfg.support_enabled = mode == 'support'
    cfg.paywall_open_access = mode != 'membership'
    cfg.strip_comments = mode != 'unstripped'
    # Task 302's reviewed identifier/regex changes supersede the base captured
    # in drop_fresh_off.json. Keep those hashes; compare this base's OFF bytes.
    baseline = None
    for value in (False, None, True, False):
        if value is None:
            del cfg.drop_fresh
        else:
            cfg.drop_fresh = value
        out, *_ = render(tmp_path, cfg, offers())
        baseline = baseline or tree_hash(out)
        assert (tree_hash(out) == baseline) is (not value)
        assert (out / 'drop-fresh.js').exists() is bool(value)
    assert pre_drop_fresh(cfg).drop_fresh is False


@pytest.mark.parametrize('value', [None, 0, 1, 'true', [], {}])
def test_strict_config(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'site': {'drop_fresh': value}}))
    with pytest.raises(ValueError, match='site.drop_fresh must be a boolean'):
        load_config(path)


def test_config(tmp_path):
    path = tmp_path / 'config.yaml'
    for value in (False, True):
        path.write_text(json.dumps({'site': {'drop_fresh': value}}))
        assert load_config(path).drop_fresh is value
    path.write_text('{}')
    assert load_config(path).drop_fresh is AppConfig().drop_fresh is False
    assert load_config().drop_fresh is True


@pytest.mark.parametrize('strip,spacing', [(False, False), (True, True)])
def test_emitted_fresh_logic(tmp_path, strip, spacing):
    cfg = load_config()
    cfg.tracking = {}
    cfg.strip_comments = strip
    cfg.same_product_spacing = spacing
    out, *_ = render(tmp_path, cfg)
    home = (out / 'index.html').read_text()
    asset = out / 'drop-fresh.js'
    # First-paint sizing cannot depend on controls inserted by the lazy module.
    reserve = '#content .card .title{min-height:2lh}'
    assert reserve in home.split('</head>', 1)[0]
    assert '#content:has(#fresh-all) .title' not in home
    assert 'min-height:2lh' not in asset.read_text()
    assert "import('/drop-fresh.js?v=" in home
    assert 'function freshRotation(' not in home and 'function startConfetti(' not in home
    for path in out.rglob('*.html'):
        if path.name != 'index.html' or path.parent != out:
            assert '/drop-fresh.js?v=' not in path.read_text()
    loader = run_node('drop_fresh_loader.cjs', out / 'index.html', asset)
    assert loader.returncode == 0, loader.stdout + loader.stderr
    confetti = run_node('drop_confetti_logic.cjs', asset)
    assert confetti.returncode == 0, confetti.stdout + confetti.stderr
    rows = inventory()
    pool = drop_fresh.rotation(rows, cfg.front_page)
    data = tmp_path / "fresh.json"
    stress = varied_inventory()
    stress_pool = drop_fresh.rotation(stress, cfg.front_page)
    data.write_text(json.dumps(dict(stress=stress, stress_pages=[
        [d["key"] for d in drop_fresh.page(stress_pool, mark, cfg.front_page, drop_fresh.rules(cfg.front_page))]
        for mark in range(20)], rows=rows, pool=[d["key"] for d in pool], pages=[
        [d["key"] for d in drop_fresh.page(pool, mark, cfg.front_page, drop_fresh.rules(cfg.front_page))]
        for mark in range(21)])))
    cfg.drop_fresh = False
    (tmp_path / 'off').mkdir()
    other, *_ = render(tmp_path / 'off', cfg)
    assert reserve not in (other / 'index.html').read_text()
    # The harness receives the ON files and a separate OFF page for other views.
    result = run_node('drop_fresh_logic.cjs', out / 'index.html', out / 'deals.json', data, other / 'index.html')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Fresh drops passed' in result.stdout


def test_shuffle_master_switch(tmp_path):
    cfg = load_config()
    cfg.drop_shuffle = False
    out, *_ = render(tmp_path, cfg)
    before = tree_hash(out)
    cfg.drop_fresh = False
    out, *_ = render(tmp_path, cfg)
    assert tree_hash(out) == before


def inventory():
    rows = [dict(key=f"fresh-{i}", pick=i+1, item=f"Product{i} useful set",
                 category=["kitchen", "home", "beauty", "kids_baby", "electronics", "toys_games",
                           "kitchen", "home", "beauty", "fashion"][i % 10],
                 store=["Amazon", "Walmart", "Target", "Woot", "Best Buy"][i % 5],
                 cl=i % 10 == 9, allure=i % 20 == 9, pick_checkout=i % 10 == 7,
                 final_price=90 if i % 5 == 0 else 10+i % 40,
                 status="alive", image_url="https://image.test/photo", conditions=[], earns=i % 3 != 0)
            for i in range(1200)]
    unsafe = [dict(rows[i], **dict(key=f"unsafe-{i}", **[
        dict(niche=True), dict(price_aged=True), dict(price_unconfirmed=True),
        dict(click_price_unknown=True), dict(conditions=["used"]), dict(conditions=["open_box"]),
        dict(image_url=""), dict(status="dead"), dict(locked=True), dict(fresh_blocked=True)
    ][i % 10])) for i in range(400)]
    return rows + unsafe


@pytest.mark.parametrize("value", [None, True, False, 23, 121, 60.0, "60", [], {}])
def test_invalid_page_size(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"publish": {"front_page": {"fresh_page_size": value}}}))
    with pytest.raises(ValueError, match="fresh_page_size must be an integer from 24 to 120"):
        load_config(path)


@pytest.mark.parametrize("value", [24, 60, 90, 120])
def test_page_size(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"publish": {"front_page": {"fresh_page_size": value}}}))
    assert load_config(path).front_page["fresh_page_size"] == value


def test_pick_rank_caps_unchanged():
    cfg = load_config()
    assert drop_fresh.pick_options(cfg) is cfg.front_page


@pytest.mark.parametrize('size', [24, 60, 90, 120])
def test_static_first_paint_matches_rotation(monkeypatch, size):
    from dealradar.publish import seo
    cfg = load_config()
    cfg.front_page['fresh_page_size'] = size
    rows = inventory()
    # Spotlight inventory joins the same rotation; it must not add cards beyond the configured page.
    spotlight = [dict(rows.pop(9), spotlight={'until': '2050-01-01T00:00:00Z'})]
    pool = drop_fresh.rotation(rows + spotlight, cfg.front_page)
    expected = drop_fresh.page(pool, int(NOW.timestamp()) // (cfg.drop_minutes * 60),
                               cfg.front_page, drop_fresh.rules(cfg.front_page))
    captured = []
    monkeypatch.setattr(seo, 'card_html', lambda row, *a, **k: captured.append(row) or '')
    html = seo.static_wall(rows, True, now=NOW, front_page=cfg.front_page, spotlight=spotlight,
                           static_photos=True, same_product_spacing=True, fresh_config=cfg)
    assert [d['key'] for d in captured] == [d['key'] for d in expected]
    assert len(captured) == size
    assert 'See all ' in html and 'Full rotation: ' in html
    assert 'spotlight-grid' not in html


@pytest.mark.parametrize('engagement', [True, False])
def test_static_card_metarow_matches_client(tmp_path, engagement):
    from dealradar.publish import seo
    from tests.test_publish_site import make_deal
    cfg = load_config()
    cfg.strip_comments = engagement
    cfg.static_photos = engagement
    cfg.card_tidy = engagement
    cfg.non_amazon_freshness_enabled = False
    cfg.engagement['hot_meter'] = engagement
    deals = [make_deal(dedupe_key=f'parity-{i}', item=f'Product{i} kitchen set',
                      category='kitchen', store='Walmart', codes=[], conditions=[],
                      image_url='https://images.example.invalid/card.png',
                      first_seen_at=NOW.isoformat()) for i in range(4)]
    out, doc, *_ = render(tmp_path, cfg, deals)
    home = (out / 'index.html').read_text()
    wall = re.sub(r'<script\b.*?</script>', '', home, flags=re.S)
    metas = re.findall(r'<div class="metarow">(.*?)</div>', wall, re.S)
    pool = drop_fresh.rotation(doc['deals'], cfg.front_page)
    rows = drop_fresh.page(pool, int(NOW.timestamp()) // (cfg.drop_minutes * 60),
                           cfg.front_page, drop_fresh.rules(cfg.front_page))
    assert len(metas) == len(rows) == 4
    cases = [dict(row=row, meta=meta) for row, meta in zip(rows, metas)]
    assert all('just in' in meta for meta in metas)
    assert all(('hotmeter' in meta) is engagement for meta in metas)
    # Null age, JS rounding, day boundaries, two-hour heat cutoff, conditions,
    # escaping and disabled heat all exercise the actual emitted card renderer.
    for i, age in enumerate([None, 0, .99, 1, 1.5, 2, 23.9, 24, 36]):
        row = dict(rows[0], key=f'case-{i}', age_hours=age, select_sizes=True,
                   conditions=['in_cart', 'new'], store='A & B',
                   first_seen_at=(NOW - timedelta(hours=age or 0)).isoformat())
        card = seo.card_html(row, now=NOW, fresh_config=cfg)
        cases.append(dict(row=row, meta=re.search(r'<div class="metarow">(.*?)</div>', card, re.S)[1]))
    path = tmp_path / 'parity.json'
    path.write_text(json.dumps(cases))
    result = run_node('drop_card_parity.cjs', out / 'index.html', out / 'deals.json', path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_adult_exclusion_clothing_and_checkout_cover_unranked_inventory():
    from tests.test_publish_site import make_deal
    cfg = load_config()
    cfg.keepa_click_price_rule = 'off'
    def serialized(title):
        return site._serialize_deal(make_deal(item=title, category="fashion", conditions=[], codes=[],
            price_rechecked_at=NOW.isoformat()), NOW, cfg=cfg)
    for title in ['Adult sex toy', 'Rose toy vibrator', 'Personal massager']:
        row = serialized(title)
        assert row['fresh_blocked'] is True
        assert drop_fresh.rotation([row], cfg.front_page) == []
    for title in ['Kids toy kitchen', "Women's bralette", 'Baby stroller']:
        row = serialized(title)
        assert not row.get('fresh_blocked')
    assert serialized("Women's bralette").get('cl')


def test_earning_ties_and_rank_within_mix_group():
    cfg = load_config()
    rows = inventory()[:3]
    for d in rows:
        d.update(pick=1, ps=1, category='kitchen', final_price=10, cl=False, allure=False, pick_checkout=False)
    rows[0]['earns'] = False
    rows[1]['earns'] = rows[2]['earns'] = True
    assert [d['key'] for d in drop_fresh.rotation(rows, cfg.front_page)] == ['fresh-1', 'fresh-2', 'fresh-0']


def varied_inventory():
    import random
    rng = random.Random(0)
    rows = inventory()[:1200]
    for row in rows:
        row.update(category=rng.choice(['home', 'beauty', 'kitchen', 'kids_baby', 'toys_games', 'electronics']),
                   cl=rng.random() < .16, allure=rng.random() < .1, pick_checkout=rng.random() < .1,
                   final_price=30 if rng.random() < .85 else 90)
    return rows


def test_varied_mix_reserves_later_slots():
    cfg = load_config()
    pool = drop_fresh.rotation(varied_inventory(), cfg.front_page)
    for mark in range(20):
        page = drop_fresh.page(pool, mark, cfg.front_page, drop_fresh.rules(cfg.front_page))
        assert sum(drop_fresh.women(d) for d in page) >= 63
        assert sum(drop_fresh.cheap(d) for d in page[:24]) >= 19
        assert not any(d['allure'] for d in page[:2])
        for i in range(87):
            assert sum(d['allure'] for d in page[i:i+4]) <= 1
        for i in range(86):
            assert sum(d['pick_checkout'] for d in page[i:i+5]) <= 1
        for i in range(0, 24, 6):
            assert sum(d['cl'] for d in page[i:i+6]) <= 2
        for i in range(88):
            assert len({d['category'] for d in page[i:i+3]}) > 1


def test_fresh_page_stays_within_phone_budget(tmp_path):
    from dealradar.rank import photoless_expired
    from scripts.publication_budget import analyze
    from tests.phone_budget_fixture import NOW as fixture_now, fixture_offers, publish_fixture
    cfg = load_config()
    cfg.phone_budget['enabled'] = True
    expected = sum(not photoless_expired(d, cfg, fixture_now) for d in fixture_offers())
    def inspect(db, out):
        report = analyze(out, cfg, expected_offers=expected)
        assert report['ok'], report
        print(json.dumps({k: v['gzip'] for k, v in report['metrics'].items()}, sort_keys=True))
    publish_fixture(tmp_path, cfg, inspect=inspect)
