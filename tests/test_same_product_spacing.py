"""Task 302: stable family deferral on first paint and every browser reorder."""
import json
import re
from html import unescape
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import same_product_spacing as spacing, seo
from tests.test_offscreen_cards import offers
from tests.test_pwa import tree_hash
from tests.test_search_amazon import run_node
from tests.test_search_more import offline  # noqa: F401
from tests.test_support import NOW, render

HOODIES = [
    "Blue 84 Los Angeles Clippers NBA Officially Licensed Pullover Hoodie (Black, Small)",
    "Blue 84 Los Angeles Clippers NBA Officially Licensed Pullover Hoodie - Navy / XL",
    "Blue 84 Los Angeles Clippers NBA Officially Licensed Pullover Hoodie [Heather Gray] XXL",
    "Blue 84 Los Angeles Clippers NBA Officially Licensed Pullover Hoodie, Red, Size Medium",
]


def row(title, key='a', **extra):
    return dict(item=title, store='Amazon', key=key, image_url='https://image.test/product.png', **extra)


def assert_spaced(rows):
    last, count = {}, {}
    for i, d in enumerate(rows):
        key = spacing.family_key(d)
        if not key:
            continue
        assert i - last.get(key, -9) > 8
        if i < 48:
            count[key] = count.get(key, 0) + 1
            assert count[key] <= 2
        last[key] = i


def test_family_stems():
    assert len({spacing.family_key(row(title)) for title in HOODIES}) == 1
    assert spacing.family_key(row(HOODIES[0])) != spacing.family_key(row(HOODIES[0]) | {'store': 'Walmart'})
    assert spacing.family_key(row('Adidas running shoes for women')) != spacing.family_key(row('Adidas training shorts for women'))
    assert spacing.family_key(row('Samsung Galaxy S24 phone')) != spacing.family_key(row('Samsung Galaxy S25 phone'))
    assert spacing.family_key(row('2-pack Acme Cotton Bath Towels [Blue] XL')) == spacing.family_key(row('Acme Cotton Bath Towels, Pack of 4, Red, Small'))
    assert spacing.family_key(row('Size 10 Acme Cotton Bath Towels')) == spacing.family_key(row('Acme Cotton Bath Towels'))
    different_stores = [row(HOODIES[0]), row(HOODIES[0], 'b') | {'store': 'Walmart'}]
    assert spacing.space_rows(different_stores) == different_stores
    assert spacing.family_key(row('')) == ''
    assert spacing.family_key(row('Blue 84 Clippers hoodie')).startswith('amazon|blue 84')


def inventory():
    return [row(title, f'hoodie-{i}', pick=i + 1) for i, title in enumerate(HOODIES)] + [
        row(f'Product {i} useful household item', f'unique-{i}', pick=i + 5) for i in range(80)]


def test_spacing_preserves_inventory_and_best_rank():
    rows = inventory()
    before = json.dumps(rows)
    out = spacing.space_rows(rows)
    assert_spaced(out)
    assert sorted(d['key'] for d in out) == sorted(d['key'] for d in rows)
    assert [out.index(d) for d in rows[:4]] == [0, 9, 48, 57]
    assert out[0] is rows[0]
    assert json.dumps(rows) == before
    assert spacing.space_rows(out) == out
    assert spacing.space_rows([]) == []


def test_insufficient_supply_keeps_every_row_and_prefix_counts():
    rows = inventory()[:4]
    assert spacing.space_rows(rows) == rows
    out = spacing.space_rows(inventory()[1:], rows[:1])
    assert_spaced(rows[:1] + out)
    assert spacing.space_rows([row('', 'a'), row('', 'b')]) == [row('', 'a'), row('', 'b')]


def bounded_cases():
    family = [row(HOODIES[i % 4], f'family-{i}') for i in range(61)]
    singles = [row(f'Unique product {i}', f'single-{i}') for i in range(320)]
    return [
        (family[:30] + singles[:10], []),
        (family + singles[:10], []),
        (family[:4] + singles, []),
        (family[:4] + singles, singles[:239]),
        (family[:4] + singles, singles[:240]),
        (singles[:238] + family[:6] + singles[238:], []),
    ]


def test_depleted_family_keeps_all_40_rows():
    rows, _ = bounded_cases()[0]
    before = list(rows)
    out = spacing.space_rows(rows)
    assert sorted(d['key'] for d in out) == sorted(d['key'] for d in rows)
    assert out == rows[:1] + rows[30:38] + rows[1:2] + rows[38:] + rows[2:30]
    assert_spaced(out[:12])  # all ten alternatives are used before falling back
    assert rows == before


def test_lookahead_and_visible_position_bounds():
    cases = bounded_cases()
    rows, _ = cases[1]
    out = spacing.space_rows(rows)
    # The first single starts just beyond the 60-row lookahead at position 1.
    assert out[:3] == rows[:2] + rows[61:62]
    for rows, prefix in cases[2:]:
        out = spacing.space_rows(rows, prefix)
        visible = 240 - len(prefix)
        selected = {d['key'] for d in out[:visible]}
        assert out[visible:] == [d for d in rows if d['key'] not in selected]
        assert sorted(d['key'] for d in out) == sorted(d['key'] for d in rows)
    rows, prefix = cases[2]
    out = spacing.space_rows(rows)
    assert_spaced(out[:240])
    assert out[:240] != rows[:240]


@pytest.mark.parametrize('value', [None, 0, 1, 'true', [], {}])
def test_strict_config(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'same_product_spacing': value}))
    with pytest.raises(ValueError, match='^same_product_spacing must be a boolean$'):
        load_config(path)


def test_config(tmp_path):
    path = tmp_path / 'config.yaml'
    for value in (True, False):
        path.write_text(json.dumps({'same_product_spacing': value}))
        assert load_config(path).same_product_spacing is value
    path.write_text('{}')
    assert load_config(path).same_product_spacing is AppConfig().same_product_spacing is False
    assert load_config().same_product_spacing is True


@pytest.mark.parametrize('mode', ['support', 'membership', 'unstripped'])
def test_recorded_off_missing_back_off_bytes(tmp_path, mode):
    cfg = load_config()
    cfg.support_enabled = mode != 'membership'
    cfg.paywall_open_access = mode != 'membership'
    cfg.strip_comments = mode != 'unstripped'
    # The shipped config moves with every release (card_tidy wording landed after task 302 was
    # recorded), so OFF and missing compare with this config's own OFF render.
    pin = None
    for value in (False, None, True, False):
        if value is None:
            del cfg.same_product_spacing
        else:
            cfg.same_product_spacing = value
        out, *_ = render(tmp_path, cfg, offers())
        pin = pin or tree_hash(out)
        assert (tree_hash(out) == pin) is (not value)


def test_first_paint(monkeypatch):
    rows = inventory()
    captured = []
    monkeypatch.setattr(seo, 'card_html', lambda row, *a, **k: captured.append(row) or '')
    seo.static_wall(rows, same_product_spacing=True)
    assert_spaced(captured)
    assert captured[0]['key'] == 'hoodie-0'
    assert len(captured) == seo._STATIC_WALL_MAX


@pytest.mark.parametrize('shuffle,strip', [(False, False), (True, False), (True, True)])
def test_emitted_logic(tmp_path, shuffle, strip):
    cfg = load_config()
    cfg.drop_fresh = False  # Test the full-wall spacing contract; fresh pages deduplicate families.
    cfg.drop_shuffle = shuffle
    cfg.strip_comments = strip
    out, *_ = render(tmp_path, cfg)
    data = inventory()
    fixture = tmp_path / 'families.json'
    fixture.write_text(json.dumps({'rows': data, 'keys': [spacing.family_key(d) for d in data],
                                   'order': [d['key'] for d in spacing.space_rows(data)],
                                   'bounded': [dict(rows=rows, prefix=prefix,
                                                    order=[d['key'] for d in spacing.space_rows(rows, prefix)])
                                               for rows, prefix in bounded_cases()]}))
    result = run_node('same_product_spacing_logic.cjs', out / 'index.html', out / 'deals.json', fixture)
    assert result.returncode == 0, result.stdout + result.stderr


def test_publication_keeps_all_listings_searchable(tmp_path):
    from tests.test_publish_site import make_deal
    data = inventory()
    deals = [make_deal(dedupe_key=d['key'], item=d['item'], image_url=d['image_url'],
                       store=d['store'], codes=[], conditions=[],
                       price_rechecked_at=NOW.isoformat(), first_seen_at=NOW.isoformat()) for d in data]
    cfg = AppConfig(same_product_spacing=True, support_enabled=True, paywall_open_access=True)
    out, doc, full, _ = render(tmp_path, cfg, deals)
    assert len(doc['deals']) == len(full['deals']) == len(data)
    page = re.sub(r'<script\b.*?</script>', '', (out / 'index.html').read_text(), flags=re.S)
    titles = [unescape(re.sub('<[^>]+>', '', title)) for title in re.findall(r'<h3 class="title">(.*?)</h3>', page)]
    assert titles
    assert_spaced([row(title) for title in titles])
    assert sum('Clippers' in title for title in titles) <= 2


def test_spacing_keeps_protected_limits():
    data = inventory()
    data[8]['status'] = 'dead'
    data[18]['status'] = 'dead'
    for i in (3, 7, 11, 15):
        data[i]['allure'] = True
    for i in (6, 12, 18):
        data[i]['pick_checkout'] = True
    for i in (1, 3, 8, 9):
        data[i]['cl'] = True
    policy = spacing.rules(load_config().front_page, 10)
    out = spacing.space_rows(data, rules=policy)
    assert_spaced(out)
    assert len(out) == len(data)
    for i, d in enumerate(out):
        if d.get('status') == 'dead':
            assert i >= 8
            assert not any(r.get('status') == 'dead' for r in out[max(0, i-9):i])
        if d.get('allure'):
            assert i >= policy['first']
            assert not any(r.get('allure') for r in out[max(0, i-policy['allure']+1):i])
        if d.get('pick_checkout'):
            assert sum(bool(r.get('pick_checkout')) for r in out[max(0,i-policy['promo']+1):i+1]) <= policy['cap']
    for i in range(0,24,policy['mix']):
        assert sum(bool(r.get('cl')) for r in out[i:i+policy['mix']]) <= policy['clothing']
