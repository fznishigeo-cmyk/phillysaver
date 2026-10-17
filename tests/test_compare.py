"""Comparison refresh and publishing use recorded API facts, never the network."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import re
import socket
import shutil
import subprocess
import time

import httpx
import pytest
import yaml

from dealradar import compare, util, verify
from dealradar.config import AppConfig, load_config, validated_compare
from dealradar.db import DB
from dealradar.keepa_budget import KeepaBudget
from dealradar.publish import amazon_as_of, compare_pages, seo_audit, site
from dealradar.savings import pack_quantity, unit_price
from dealradar.util import iso
from tests.test_public_copy import INTERNAL

FIXTURES = Path(__file__).parent / 'fixtures'
NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
TERM = dict(slug='laundry-pods', name='Laundry detergent pods', keywords=['laundry', 'pods'],
            unit='count', unit_words=['Count', 'ct', 'pods'], display_unit='pod')


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    send = httpx.Client.send
    def unavailable(*args, **kwargs):
        raise AssertionError('task256 forbids network requests')
    monkeypatch.setattr(httpx.Client, 'send', unavailable)
    monkeypatch.setattr(socket, 'create_connection', unavailable)
    monkeypatch.setattr(site, 'util_now', lambda: NOW)
    monkeypatch.setattr(util, 'now_utc', lambda: NOW)
    return send


@pytest.fixture
def db():
    database = DB(':memory:')
    yield database
    database.close()


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / 'terms.yaml'
    path.write_text(yaml.safe_dump([TERM]))
    cfg = AppConfig(custom_domain='wowpriced.com', paywall_open_access=True,
                    affiliate_amazon_tag='fixture-20')
    cfg.compare.update(refresh=True, publish='preview', terms_file=str(path))
    cfg.request_timeout_s = 1
    cfg.feature_flags['keepa'] = True
    monkeypatch.setenv('KEEPA_KEY', 'offline-fixture-only')
    return cfg


@pytest.fixture
def products():
    return json.loads((FIXTURES / 'compare-keepa.json').read_text())['products']


class Fetcher:
    def __init__(self, cfg, products, *, status=200, finder_tokens=11, missing_receipt=False):
        self.cfg, self.products, self.status = cfg, products, status
        self.finder_tokens, self.missing_receipt = finder_tokens, missing_receipt
        self.calls = []

    def api_get(self, url, **kwargs):
        assert url in {compare.QUERY_URL, 'https://api.keepa.com/product'}
        assert kwargs['respect_robots'] is False and kwargs['max_attempts'] == 1
        assert kwargs['follow_redirects'] is False
        assert kwargs['conditional'] is False
        assert kwargs['timeout_s'] == min(self.cfg.request_timeout_s, self.cfg.compare['request_timeout_s'])
        assert kwargs['deadline'] > time.monotonic()
        params = kwargs['params']
        self.calls.append((url, deepcopy(params)))
        is_finder = url == compare.QUERY_URL
        cost = self.finder_tokens if is_finder else len(params['asin'].split(','))
        body = dict(tokensLeft=3500, refillRate=60, tokensConsumed=0 if self.status == 429 else cost)
        if is_finder:
            body.update(asinList=[p['asin'] for p in self.products], totalResults=1234)
        else:
            assert params['history'] == 0 and params['stats'] == 90
            assert not set(params) & {'offers', 'rating', 'buybox', 'update'}
            body['products'] = [p for p in self.products if p['asin'] in params['asin'].split(',')]
        if self.missing_receipt:
            body.pop('tokensConsumed')
        return httpx.Response(self.status, json=body, request=httpx.Request('GET', url))


def refresh(db, cfg, fetcher, key='run-1', *, now=NOW, budget=None, deadline=None, rate_limited=False):
    budget = budget or KeepaBudget(3600, 60, NOW, db=db)
    return compare.refresh(db, cfg, now, fetcher, deadline, key, budget=budget, rate_limited=rate_limited)


def seed(db, products, term=TERM):
    data = dict(terms={term['slug']: dict(asins=[p['asin'] for p in products], finder_at=iso(NOW), read_at=iso(NOW))},
                products={p['asin']: compare.product_facts(p, NOW) for p in products})
    db.meta_set(compare.STATE, json.dumps(data))
    return data


def publish(tmp_path, db, cfg, products=None):
    if products is not None:
        seed(db, products)
    out = tmp_path / 'site'
    site.render_site(db, cfg, NOW, out_dir=out, deals=[])
    return out


def files(out):
    return {str(p.relative_to(out)): p.read_bytes() for p in out.rglob('*') if p.is_file()}


def test_terms_and_validation(tmp_path, caplog):
    terms = compare.load_terms()
    assert len(terms) == 60 and len({t['slug'] for t in terms}) == 60
    for bad, message in (([TERM, TERM], 'duplicate slug'),
                         ([dict(TERM, unit='gallon')], 'unknown unit'),
                         ([dict(TERM, keywords=[])], 'keywords')):
        path = tmp_path / 'bad.yaml'
        path.write_text(yaml.safe_dump(bad))
        with pytest.raises(ValueError, match=message):
            compare.load_terms(path)
        assert len(compare.load_terms(path, strict=False)) == (1 if len(bad) == 2 else 0)
    assert 'skipped' in caplog.text


def test_config_defaults_and_shipped_switches(tmp_path):
    assert AppConfig().compare == validated_compare({})
    assert {k: v for k, v in load_config().compare.items() if k not in ('refresh', 'publish')} == \
        {k: v for k, v in AppConfig().compare.items() if k not in ('refresh', 'publish')}
    # Operator 2026-10-17: preview with real prices (noindex, no menu link, not in the sitemap).
    assert load_config().compare['refresh'] and load_config().compare['publish'] == 'preview'
    assert AppConfig().compare['publish'] == 'off' and not AppConfig().compare['refresh']
    path = tmp_path / 'cfg.yaml'
    path.write_text('compare: {publish: preview, refresh: true, tokens_per_run: 12}')
    cfg = load_config(path)
    assert cfg.compare['publish'] == 'preview' and cfg.compare['tokens_per_run'] == 12


@pytest.mark.parametrize('key,value', [('refresh', 'false'), ('publish', False), ('publish', 'bad'),
    ('prime_only', 1), ('terms_file', []), ('candidates_per_term', 0), ('candidates_per_term', 51),
    ('max_terms', 121), ('tokens_per_day', True), ('min_reviews', 1.5), ('min_rating', 51),
    ('min_bucket_share', float('nan')), ('max_price_age_h', 25), ('refresh_hours', '24'),
    ('finder_days', None), ('max_seconds_per_run', 0), ('request_timeout_s', True),
    ('request_timeout_s', float('inf')), ('request_timeout_s', 0), ('unsupported', 1)])
def test_bad_config(key, value):
    with pytest.raises(ValueError, match='compare'):
        validated_compare({key: value})


@pytest.mark.parametrize('price,quantity,expected', [(21.87, 81, .27), (1, 3, .3333),
    (1, 10000, .0001), (0, 1, None), (1, 0, None), (-1, 3, None), (1, -1, None),
    (float('nan'), 1, None), (1, float('inf'), None), (True, 2, None), (None, 2, None)])
def test_unit_price(price, quantity, expected):
    assert unit_price(price, quantity) == expected


@pytest.mark.parametrize('title,unit,words,unit_count,expected', [
    ('Tide PODS Laundry 81 Count', 'count', ['count'], {'unitType': 'count', 'unitValue': 81}, (81, '81 Count')),
    ('Laundry 81-Count', 'count', ['count'], None, (81, '81-Count')),
    ('Laundry 81 ct', 'count', ['ct'], None, (81, '81 ct')),
    ('Soap 100 fl oz', 'fl_oz', ['fl oz'], None, (100, '100 fl oz')),
    ('Cable 6 ft', 'ft', ['ft'], None, (6, '6 ft')),
    ('Pods Pack of 3, 20 Count Each', 'count', ['count'], None, (None, '')),
    ('Pods 20 Count Each, Pack of 3', 'count', ['count'], {'unitType': 'count', 'unitValue': 60}, (None, '')),
    ('Soap model 100', 'fl_oz', ['fl oz'], {'unitType': 'fluid ounce', 'unitValue': 100}, (None, '')),
    ('81 Count and 42 Count', 'count', ['count'], None, (None, '')),
    ('Soap 100 oz', 'fl_oz', ['fl oz'], {'unitType': 'ounce', 'unitValue': 100}, (None, '')),
    ('Soap 100 fl oz', 'fl_oz', ['fl oz'], {'unitType': 'count', 'unitValue': 900}, (100, '100 fl oz')),
    ('Pods 81 Count', 'count', ['count'], {'unitType': 'count', 'unitValue': 42}, (None, '')),
    ('Pods Pack of 3, 20 Count', 'count', ['count'], None, (None, '')),
    ('Pods Pack of 3', 'count', ['count'], None, (None, '')),
    ('Cable 6 ft, 2 pack', 'ft', ['ft'], None, (None, '')),
    ('Towels 110 sheets per roll', 'count', ['sheets'], None, (None, '')),
    ('Pods 20 Count Each', 'count', ['count'], None, (None, '')),
    ('Soap 1/2 oz', 'oz', ['oz'], None, (None, '')),
    ('Pods - 3 Count', 'count', ['count'], None, (None, '')),
    ('Batteries 6 Pack', 'count', ['pack'], None, (6, '6 Pack')),
])
def test_quantity_evidence(title, unit, words, unit_count, expected):
    term = dict(TERM, unit=unit, unit_words=words)
    result = compare.quantity_with_evidence(dict(title=title, unitCount=unit_count), term)
    assert result == expected
    if result[0]:
        assert result[1] in title


@pytest.mark.parametrize('description', [
    'Pack of Two', 'Pack of Twelve', 'Bundle of Two', 'Bundle', 'Set of Two',
    'Two Packs', 'Multipack', 'Multi-Pack', 'Pair', 'Pack of 2, Gift Set',
])
@pytest.mark.parametrize('metadata', [{}, {'numberOfItems': -1, 'packageQuantity': 0}])
def test_unresolved_pack_words_cannot_claim_best_unit_price(db, cfg, products, description, metadata):
    term = dict(TERM, unit='fl_oz', unit_words=['fl oz'], display_unit='fl oz')
    candidate = dict(products[0], title=f'Soap 12 fl oz ({description})',
                     unitCount=None, numberOfItems=None, packageQuantity=None) | metadata
    single = dict(products[1], title='Soap 12 fl oz', unitCount=None,
                  numberOfItems=1, packageQuantity=1)
    candidate['stats'] = dict(current=[600, 600])
    single['stats'] = dict(current=[400, 400])
    assert compare.quantity_with_evidence(candidate, term) == (None, '')
    rows = compare.ranked_rows(term, seed(db, [candidate, single], term), cfg, NOW)
    assert rows[0]['asin'] == single['asin'] and rows[0]['unit_price'] == .3333
    assert len(rows) == 1


@pytest.mark.parametrize('title', [
    'USB C Cable 6 ft, 2 Cables', 'USB C Cable 6 ft, 2 Cases',
    'USB C Cable 6 ft, 3 Cans', 'USB C Cable 6 ft, 2 Unfamiliar Containers',
    'USB C Cable 6 ft, 2Cases', 'USB C Cable 6 ft, Two Cables',
    'USB C Cable 6 ft, Case of 2', 'USB C Cable 6 ft, Case of Four',
    'USB C Cable 6 ft, Fifteen Cables', 'USB C Cable 6 ft, Twenty-Four Cases',
    'USB C Cable 6 ft, Set of 4', 'USB C Cable 6 ft, Pack of Two',
    'USB C Cable 6 ft x2', 'USB C Cable 6 ft, Twin pack',
    'USB C Cable 6 ft, 6 ft',
])
@pytest.mark.parametrize('metadata', [{}, {'numberOfItems': -1, 'packageQuantity': 0}])
def test_missing_pack_metadata_rejects_every_extra_count(db, cfg, products, title, metadata):
    term = next(t for t in compare.load_terms() if t['slug'] == 'usb-c-cables')
    candidate = dict(products[0], title=title, stats={'current': [-1, 800]},
                     unitCount={'unitType': 'ft', 'unitValue': 6},
                     numberOfItems=None, packageQuantity=None) | metadata
    single = dict(products[1], title='USB C Cable 6 ft', stats={'current': [-1, 500]},
                  unitCount=None, numberOfItems=None, packageQuantity=None)
    assert compare.quantity_with_evidence(candidate, term) == (None, '')
    rows = compare.ranked_rows(term, seed(db, [candidate, single], term), cfg, NOW)
    assert [(r['asin'], r['unit_price'], r['quote']) for r in rows] == [
        (single['asin'], .8333, '6 ft')]


@pytest.mark.parametrize('metadata,expected', [({}, None), ({'packageQuantity': 3}, 60)])
def test_pack_multiplier_requires_pack_metadata(metadata, expected):
    product = dict(title='Pods Pack of 3, 20 Count Each', **metadata)
    value, quote = compare.quantity_with_evidence(product, TERM)
    assert value == expected
    assert quote == ('Pack of 3, 20 Count Each' if expected else '')


@pytest.mark.parametrize('cap', ['tokens_per_run', 'tokens_per_day'])
def test_finder_upper_bound_blocks_small_quotas(db, cfg, products, cap):
    cfg.compare[cap] = 11
    fetcher = Fetcher(cfg, products, finder_tokens=15)
    budget = KeepaBudget(3600, 60, NOW, db=db)
    assert refresh(db, cfg, fetcher, budget=budget)['tokens'] == 0
    assert fetcher.calls == [] and budget.available(NOW) == 3600
    assert json.loads(db.meta_get(compare.LEDGER))['tokens'] == 0


@pytest.mark.parametrize('cap', ['tokens_per_run', 'tokens_per_day'])
def test_finder_upper_bound_is_reserved_again_before_each_call(db, cfg, products, cap):
    cfg.compare[cap] = 30
    Path(cfg.compare['terms_file']).write_text(yaml.safe_dump([TERM, dict(TERM, slug='second')]))
    fetcher = Fetcher(cfg, products, finder_tokens=15)
    result = refresh(db, cfg, fetcher)
    assert result['finders'] == 1 and result['products'] == 6
    assert result['tokens'] == 21 and result['finder_costs'] == [15]
    assert sum(url == compare.QUERY_URL for url, _ in fetcher.calls) == 1
    assert json.loads(db.meta_get(compare.LEDGER))['tokens'] == 21


@pytest.mark.parametrize('share', [0, .8])
def test_finder_upper_bound_preserves_bucket_reserves(db, cfg, products, share):
    cfg.compare['min_bucket_share'] = share
    floor = max(cfg.keepa_reserve_tokens, cfg.keepa_recheck_surplus_reserve, share * 3600)
    budget = KeepaBudget(floor + 29, 60, NOW, db=db)
    fetcher = Fetcher(cfg, products)
    result = refresh(db, cfg, fetcher, budget=budget)
    assert result['tokens'] == 0 and fetcher.calls == []
    assert budget.available(NOW) == floor + 29


def test_oversized_finder_receipt_halts_only_compare_until_next_utc_day(db, cfg, products, caplog):
    cfg.compare.update(finder_reserve_tokens=11, tokens_per_run=11, tokens_per_day=11)
    Path(cfg.compare['terms_file']).write_text(yaml.safe_dump([TERM, dict(TERM, slug='second')]))
    fetcher = Fetcher(cfg, products, finder_tokens=15)
    budget = KeepaBudget(3600, 60, NOW, db=db)
    result = refresh(db, cfg, fetcher, budget=budget)
    assert result['halted'] is True and result['tokens'] == 15
    assert result['receipts'] == result['finder_costs'] == [15]
    ledger = json.loads(db.meta_get(compare.LEDGER))
    assert ledger['tokens'] == ledger['runs']['run-1']['tokens'] == 15
    assert ledger['halted_day'] == NOW.date().isoformat()
    assert len(fetcher.calls) == 1 and result['products'] == 0
    assert [r.getMessage() for r in caplog.records if r.name == 'dealradar.compare'] == [
        'compare refresh stopped (FinderReserveExceeded)']
    assert refresh(db, cfg, fetcher, budget=budget) == result  # same-key replay
    cfg.compare.update(tokens_per_run=300, tokens_per_day=6000)  # caps cannot clear the stop
    restarted_budget = KeepaBudget.load(db)
    stopped = refresh(db, cfg, fetcher, 'later', now=NOW + timedelta(hours=1), budget=restarted_budget)
    assert stopped['halted'] is True and len(fetcher.calls) == 1
    # A recheck still uses the shared bucket and ordinary product path.
    assert restarted_budget.available(NOW) >= max(cfg.keepa_reserve_tokens, cfg.keepa_recheck_surplus_reserve)
    rechecked = verify.keepa_products('offline-fixture-only', [products[0]['asin']], 90,
        restarted_budget, fetcher=fetcher, follow_redirects=False, record_shared_facts=False,
        deadline=time.monotonic() + 20, timeout_s=cfg.request_timeout_s)
    assert products[0]['asin'] in rechecked and len(fetcher.calls) == 2
    fetcher.finder_tokens = 11
    next_day = refresh(db, cfg, fetcher, 'tomorrow', now=NOW + timedelta(hours=12), budget=restarted_budget)
    assert not next_day.get('halted') and next_day['finders'] > 0
    assert json.loads(db.meta_get(compare.LEDGER)).get('halted_day') != NOW.date().isoformat()


@pytest.mark.parametrize('receipt', [11, None])
def test_finder_reservation_reconciles_without_bucket_receipt(db, cfg, products, receipt):
    fetcher = Fetcher(cfg, products)
    original = fetcher.api_get
    def receipt_only(url, **kwargs):
        response = original(url, **kwargs)
        payload = response.json()
        payload.pop('tokensLeft')
        if receipt is None:
            payload.pop('tokensConsumed')
        if url == compare.QUERY_URL:
            payload['asinList'] = []
        return httpx.Response(200, json=payload, request=response.request)
    fetcher.api_get = receipt_only
    budget = KeepaBudget(3600, 60, NOW, db=db)
    result = refresh(db, cfg, fetcher, budget=budget)
    cost = 30 if receipt is None else receipt
    assert result['tokens'] == cost and budget.available(NOW) == 3600 - cost
    assert KeepaBudget.load(db).tokens_left == 3600 - cost
    assert result['estimated_tokens'] == (30 if receipt is None else 0)


@pytest.mark.parametrize('value', [0, -1, True, 1.5, '30', None, float('inf')])
def test_finder_reserve_config_validation(value):
    assert validated_compare({})['finder_reserve_tokens'] == 30
    with pytest.raises(ValueError, match='compare.finder_reserve_tokens'):
        validated_compare({'finder_reserve_tokens': value})


def test_refresh_receipts_filters_facts_and_dedup(db, cfg, products):
    path = Path(cfg.compare['terms_file'])
    second = dict(TERM, slug='second', root_category=123, max_price=50)
    path.write_text(yaml.safe_dump([TERM, second]))
    fetcher = Fetcher(cfg, products, finder_tokens=15)
    result = refresh(db, cfg, fetcher)
    assert result == dict(finders=2, products=6, tokens=36, estimated_tokens=0,
                         receipts=[15, 15, 6], finder_costs=[15, 15], finder_results=[1234, 1234], rate_limited=False)
    assert len(fetcher.calls) == 3
    find = json.loads(fetcher.calls[0][1]['selection'])
    assert find == dict(title='laundry pods', productType=[0], singleVariation=True,
                        current_RATING_gte=40, current_COUNT_REVIEWS_gte=50, current_NEW_gte=1,
                        sort=[['current_SALES', 'asc']], perPage=50, page=0, buyBoxIsPrimeEligible=True)
    assert json.loads(fetcher.calls[1][1]['selection'])['current_NEW_lte'] == 5000
    saved = compare.state(db)
    assert saved['terms'][TERM['slug']]['asins'] == [p['asin'] for p in products]
    assert saved['products'][products[-1]['asin']]['price'] is None
    assert saved['products'][products[0]['asin']]['image'] == 'product0.jpg'
    for word in ('rating', 'review', 'monthlySold', 'salesRank', '837291', '93457', '76321'):
        assert word not in json.dumps(saved)
    before = deepcopy(saved)
    assert refresh(db, cfg, fetcher) == result and len(fetcher.calls) == 3
    assert compare.state(db) == before
    refresh(db, cfg, fetcher, 'new-run')
    assert len(fetcher.calls) == 3


@pytest.mark.parametrize('prime', [False, True])
def test_stock_filter(prime):
    options = validated_compare({'prime_only': prime})
    selection = compare.selection(TERM, options)
    assert selection.get('availabilityAmazon') == (None if prime else [0])
    assert selection.get('buyBoxIsPrimeEligible') == (True if prime else None)


@pytest.mark.parametrize('change,available,expected_calls', [
    ({'tokens_per_run': 10}, 3600, 0), ({'tokens_per_day': 10}, 3600, 0),
    ({'tokens_per_run': 13}, 3600, 0), ({'tokens_per_run': 30}, 3600, 2),
    ({}, 2879, 0), ({}, 2880, 0), ({}, 2890, 0), ({}, 2891, 0),
    ({}, 2909, 0), ({}, 2910, 2),
    ({}, None, 0), ({'min_bucket_share': 0}, 660, 0),
    ({'min_bucket_share': 0}, 661, 0), ({'min_bucket_share': 0}, 679, 0),
    ({'min_bucket_share': 0}, 680, 2),  # the response updates the bucket to 3500
])
def test_budget_gates(db, cfg, products, change, available, expected_calls):
    cfg.compare.update(change)
    budget = KeepaBudget(available, 60, NOW, db=db)
    fetcher = Fetcher(cfg, products)
    result = refresh(db, cfg, fetcher, budget=budget)
    assert len(fetcher.calls) == expected_calls
    assert result['tokens'] <= cfg.compare['tokens_per_run']
    assert result['tokens'] <= cfg.compare['tokens_per_day']


def test_daily_cap_across_runs(db, cfg, products):
    cfg.compare.update(tokens_per_day=30)
    Path(cfg.compare['terms_file']).write_text(yaml.safe_dump([TERM, dict(TERM, slug='second')]))
    fetcher = Fetcher(cfg, products)
    assert refresh(db, cfg, fetcher)['tokens'] == 17
    count = len(fetcher.calls)
    refresh(db, cfg, fetcher, 'run-2')
    assert len(fetcher.calls) == count
    refresh(db, cfg, fetcher, 'run-3', now=NOW + timedelta(days=1))
    assert len(fetcher.calls) > count


@pytest.mark.parametrize('phase', ['finder', 'product', 'inherited', 'deadline', 'seconds', 'disabled'])
def test_early_stops_and_replay(db, cfg, products, phase, monkeypatch):
    fetcher = Fetcher(cfg, products, status=429 if phase in {'finder', 'product'} else 200)
    if phase == 'product':
        seed(db, products)
        data = compare.state(db)
        data['terms'][TERM['slug']].pop('read_at')
        data['products'] = {}
        db.meta_set(compare.STATE, json.dumps(data))
    if phase == 'disabled':
        cfg.compare['refresh'] = False
    if phase == 'seconds':
        clock = iter([0, 21, 22])
        monkeypatch.setattr(compare.time, 'monotonic', lambda: next(clock, 25))
    deadline = -1 if phase == 'deadline' else None
    result = refresh(db, cfg, fetcher, deadline=deadline, rate_limited=phase == 'inherited')
    assert len(fetcher.calls) == (1 if phase in {'finder', 'product'} else 0)
    assert result['rate_limited'] == (phase in {'finder', 'product', 'inherited'})
    refresh(db, cfg, fetcher, deadline=deadline, rate_limited=phase == 'inherited')
    assert len(fetcher.calls) == (1 if phase in {'finder', 'product'} else 0)


def test_bounded_state_and_oldest_first(db, cfg, products):
    path = Path(cfg.compare['terms_file'])
    a, b = dict(TERM, slug='a'), dict(TERM, slug='b')
    path.write_text(yaml.safe_dump([a, b]))
    data = seed(db, products, b)
    data['terms']['removed'] = dict(asins=['B999999999'])
    data['terms']['a'] = dict(asins=[products[0]['asin']], finder_at=iso(NOW), read_at=iso(NOW - timedelta(days=3)))
    data['terms']['b']['read_at'] = iso(NOW - timedelta(days=2))
    data['products'] = {'B999999999': {}}
    db.meta_set(compare.STATE, json.dumps(data))
    cfg.compare['tokens_per_run'] = 1
    fetcher = Fetcher(cfg, products)
    refresh(db, cfg, fetcher)
    assert fetcher.calls[0][1]['asin'] == products[0]['asin']
    data = compare.state(db)
    assert set(data['terms']) == {'a', 'b'} and 'B999999999' not in data['products']


def test_rows_ranking_and_stale_prices(db, cfg, products):
    data = seed(db, products)
    rows = compare.ranked_rows(TERM, data, cfg, NOW)
    assert [r['asin'] for r in rows] == [products[i]['asin'] for i in (0, 2, 1, 3)]
    assert [r['unit_price'] for r in rows] == [.27, .3, .41, None]


@pytest.mark.parametrize('mode', ['off', 'preview', 'on'])
def test_publish_modes_and_replay(tmp_path, db, cfg, products, mode):
    cfg.compare['publish'] = mode
    out = publish(tmp_path, db, cfg, products)
    content = files(out)
    if mode == 'off':
        assert not (out / 'compare').exists()
        assert all(b'Compare prices' not in p for p in content.values())
        return
    page = (out / 'compare/laundry-pods/index.html').read_text()
    assert '<h2>Best value right now</h2>' in page
    assert '27¢ per pod' in page and '41¢ per pod' in page
    assert 'Best value among items that state their size:' in page
    assert 'title="81 Count"' in page and '<blockquote>81 Count</blockquote>' in page
    assert 'Stale Laundry' not in page and 'Coupon Laundry' not in page
    assert 'tag=fixture-20' in page
    assert amazon_as_of.DISCLAIMER in page and 'Amazon price as of' in page and 'Checked 0 min ago' in page
    listing = page[page.index('<ol class="compare-list"'):]
    assert listing.index('Tide PODS') < listing.index('Pack of 3') < listing.index('Other Laundry') < listing.index('model 1234')
    assert all(INTERNAL.search(p.decode()) is None for name, p in content.items() if name.startswith('compare/'))
    for word in ('stars', 'reviews', 'bought', 'monthlySold', 'salesRank', '93457', '76321', '837291'):
        assert all(word.encode().lower() not in p.lower() for name, p in content.items() if name.startswith('compare/'))
    in_sitemap = 'https://wowpriced.com/compare/laundry-pods/' in seo_audit.sitemap_urls(out, 'https://wowpriced.com')
    if mode == 'preview':
        assert 'name="robots" content="noindex,follow"' in page
        assert not in_sitemap
        assert all(b'/compare/' not in p for name, p in content.items() if name.endswith('.html') and not name.startswith('compare/'))
    else:
        assert in_sitemap and '"@type":"ItemList"' in page.replace(' ', '')
        assert (out / 'index.html').read_text().count(compare_pages.LINK) == 1
    publish(tmp_path, db, cfg)
    assert files(out) == content
    site.render_site(db, cfg, NOW + timedelta(hours=25), out_dir=out, deals=[])
    assert not (out / 'compare/laundry-pods').exists()
    cfg.compare['publish'] = 'off'
    site.render_site(db, cfg, NOW, out_dir=out, deals=[])
    assert not (out / 'compare').exists()


# The shipped config now previews the finder; its OFF bytes were recorded on an
# older base, and shipped bytes stay pinned by the pre-task helpers elsewhere.
@pytest.mark.parametrize('mode', ['membership', 'open', 'support'])
def test_off_tree_matches_untouched_output(tmp_path, monkeypatch, mode):
    from tests.test_pwa import baseline_config, sample_deals, tree_hash
    from tests.test_support import NOW as baseline_now, render
    monkeypatch.setattr(site, 'util_now', lambda: baseline_now)
    cfg = load_config() if mode == 'shipped' else baseline_config(mode)
    out, *_ = render(tmp_path, cfg, sample_deals())
    assert tree_hash(out) == json.loads((FIXTURES / 'compare-off-tree.json').read_text())[mode]


def test_search_script_offline_closest_five(tmp_path):
    if not shutil.which('node'):
        pytest.skip('node is unavailable')
    terms = [dict(name=t['name'], words=re.findall('[a-z0-9]+', t['name'].lower()), path='/compare/'+t['slug']+'/')
             for t in compare.load_terms()]
    script = compare_pages.SEARCH.removeprefix('<script>').removesuffix('</script>')
    harness = '''const assert=require('node:assert/strict');
const terms=TERMS;
let input={value:'',addEventListener:(event,fn)=>input.update=fn};
let list={items:[],replaceChildren(){this.items=[]},appendChild(li){this.items.push(li)}};
let status={textContent:''};
global.document={getElementById:id=>({'compare-terms':{textContent:JSON.stringify(terms)},
'compare-search':input,'compare-results':list,'compare-status':status}[id]),
createElement:tag=>({tag,textContent:'',setAttribute(k,v){this[k]=v},appendChild(v){this.child=v}})};
SCRIPT
assert.equal(list.items.length,60);
input.value='laundry pods';input.update();assert.equal(list.items.length,1);
assert.match(list.items[0].child.textContent,/Laundry/);
input.value='laundry unicorn';input.update();assert.equal(list.items.length,5);
assert.match(status.textContent,/Not covered yet/);
assert.match(list.items[0].child.textContent,/Laundry/);
'''.replace('TERMS', json.dumps(terms)).replace('SCRIPT', script)
    result = subprocess.run(['node', '-e', harness], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_lost_receipts_are_debits_not_measurements(db, cfg, products):
    fetcher = Fetcher(cfg, products, missing_receipt=True)
    result = refresh(db, cfg, fetcher)
    assert result['tokens'] == result['estimated_tokens'] == 36
    assert result['receipts'] == [None, None] and result['finder_costs'] == [None]
    assert refresh(db, cfg, fetcher) == result and len(fetcher.calls) == 2


@pytest.mark.parametrize('left,share', [(2879, .8), (650, 0)])
def test_each_response_rechecks_share_and_reserve(db, cfg, products, left, share):
    cfg.compare['min_bucket_share'] = share
    fetcher = Fetcher(cfg, products)
    original = fetcher.api_get
    def declining(url, **kwargs):
        response = original(url, **kwargs)
        payload = response.json()
        payload['tokensLeft'] = left
        return httpx.Response(200, json=payload, request=response.request)
    fetcher.api_get = declining
    result = refresh(db, cfg, fetcher)
    assert result['finders'] == 1 and result['products'] == 0 and len(fetcher.calls) == 1


def test_partial_terms_progress_without_rebuying_same_products(db, cfg, products):
    seed(db, products)
    data = compare.state(db)
    data['terms'][TERM['slug']].pop('read_at')
    data['products'] = {}
    db.meta_set(compare.STATE, json.dumps(data))
    cfg.compare['tokens_per_run'] = 2
    fetcher = Fetcher(cfg, products)
    for i in range(3):
        assert refresh(db, cfg, fetcher, f'run-{i}')['products'] == 2
    assert len(fetcher.calls) == 3
    assert len(set(','.join(params['asin'] for _, params in fetcher.calls).split(','))) == 6
    assert compare.state(db)['terms'][TERM['slug']]['read_at'] == iso(NOW)
    refresh(db, cfg, fetcher, 'finished')
    assert len(fetcher.calls) == 3


def test_finder_weekly_products_daily(db, cfg, products):
    fetcher = Fetcher(cfg, products)
    assert refresh(db, cfg, fetcher)['finders'] == 1
    day_two = refresh(db, cfg, fetcher, 'day-2', now=NOW + timedelta(days=1))
    assert day_two['finders'] == 0 and day_two['products'] == 6
    week_two = refresh(db, cfg, fetcher, 'week-2', now=NOW + timedelta(days=7))
    assert week_two['finders'] == 1 and week_two['products'] == 6


def test_batches_and_candidate_cap(db, cfg, products):
    terms = [dict(TERM, slug=f'term-{i}', keywords=[f'term{i}']) for i in range(6)]
    Path(cfg.compare['terms_file']).write_text(yaml.safe_dump(terms))
    many = [dict(products[0], asin=f'B{i:09d}') for i in range(150)]
    fetcher = Fetcher(cfg, many)
    original = fetcher.api_get
    def finder_page(url, **kwargs):
        if url == compare.QUERY_URL:
            term = int(json.loads(kwargs['params']['selection'])['title'].removeprefix('term'))
            response = original(url, **kwargs)
            payload = response.json()
            payload['asinList'] = [p['asin'] for p in many[term*25:(term+1)*25]]
            return httpx.Response(200, json=payload, request=response.request)
        return original(url, **kwargs)
    fetcher.api_get = finder_page
    result = refresh(db, cfg, fetcher)
    assert result['finders'] == 6 and result['products'] == 120 and result['tokens'] == 186
    batches = [p['asin'].split(',') for u, p in fetcher.calls if u.endswith('/product')]
    assert list(map(len, batches)) == [100, 20]
    data = compare.state(db)
    for i in range(6):
        assert data['terms'][f'term-{i}']['asins'] == [p['asin'] for p in many[i*25:i*25+20]]


@pytest.mark.parametrize('title', ['Pods 81 Count 4.7 stars', 'Pods 81 Count 76321 bought in past month',
                                    'Pods 81 Count Keepa', 'Pods 81 Count ★★★★★'])
def test_title_cannot_smuggle_restricted_copy(db, cfg, products, title):
    products[0]['title'] = title
    data = seed(db, products[:1])
    assert compare.ranked_rows(TERM, data, cfg, NOW) == []


def test_fresh_row_cap_and_term_cap(tmp_path, db, cfg, products):
    cfg.compare['max_terms'] = 1
    Path(cfg.compare['terms_file']).write_text(yaml.safe_dump([TERM, dict(TERM, slug='extra')]))
    many = [dict(products[0], asin=f'B{i:09d}') for i in range(25)]
    data = seed(db, many)
    assert len(compare.ranked_rows(TERM, data, cfg, NOW)) == 10
    data['terms']['extra'] = dict(data['terms'][TERM['slug']])
    db.meta_set(compare.STATE, json.dumps(data))
    out = publish(tmp_path, db, cfg)
    assert (out / 'compare/laundry-pods/index.html').exists()
    assert not (out / 'compare/extra/index.html').exists()


def test_rerun_makes_no_calls_and_identical_pages(tmp_path, db, cfg, products):
    fetcher = Fetcher(cfg, products)
    result = refresh(db, cfg, fetcher)
    out = publish(tmp_path, db, cfg)
    before = files(out)
    count = len(fetcher.calls)
    assert refresh(db, cfg, fetcher) == result
    publish(tmp_path, db, cfg)
    assert len(fetcher.calls) == count and files(out) == before


@pytest.mark.parametrize('mode', ['preview', 'on'])
@pytest.mark.parametrize('history_setting', ['show', 'hide'])
def test_finder_publishes_only_current_prices(tmp_path, db, cfg, products, mode, history_setting):
    cfg.compare['publish'] = mode
    cfg.amazon_history = history_setting
    cfg.seo_product_pages_enabled = True
    cfg.store_policy.update(amazon='history', walmart='history', ebay='history')
    data = seed(db, products[:1])
    data['products'][products[0]['asin']]['low90'] = 2187
    db.meta_set(compare.STATE, json.dumps(data))
    db.conn.execute('INSERT INTO products VALUES (1, ?, ?, ?)', ('fixture', iso(NOW), iso(NOW)))
    db.conn.execute('INSERT INTO product_keys VALUES (?, ?, 1)', ('asin', products[0]['asin']))
    out = tmp_path / 'site'
    history = out / 'p/fixture/index.html'
    history.parent.mkdir(parents=True)
    history.write_text('Price history: lowest $1; highest $100; 90 days; chart')
    pages = {'1': {'path': '/p/fixture/'}}
    compare_pages.publish(out, db, cfg, NOW, 'https://wowpriced.com', 'Disclosure.', pages)
    def check_public_files():
        for name, content in files(out / 'compare').items():
            if name.endswith(('.html', '.json')):
                # Shared analytics scrubs the internal-visit query parameter
                # with the browser History API; it contains no price facts.
                public = (content.decode().replace('history.replaceState(history.state', '')
                          .replace('/* URL or history unavailable */', ''))
                forbidden = re.search(r'lowest|90[ -]days?|highest|history|chart|avg90|low90', public, re.I)
                assert forbidden is None, (name, public[max(0, forbidden.start() - 80):forbidden.end() + 80])
                assert b'/p/fixture/' not in content
    check_public_files()
    page = (out / 'compare/laundry-pods/index.html').read_text()
    assert '$21.87 USD' in page and '27¢ per pod' in page
    assert compare.state(db)['products'][products[0]['asin']]['avg90'] == 25
    assert history.read_text().startswith('Price history:')
    site.render_site(db, cfg, NOW, out_dir=out, deals=[])
    check_public_files()


def test_finder_schema_links_to_current_row(tmp_path, db, cfg, products):
    cfg.compare['publish'] = 'on'
    seed(db, products[:1])
    db.conn.execute('INSERT INTO products VALUES (1, ?, ?, ?)', ('fixture', iso(NOW), iso(NOW)))
    db.conn.execute('INSERT INTO product_keys VALUES (?, ?, 1)', ('asin', products[0]['asin']))
    out = tmp_path / 'site'
    history = out / 'p/fixture/index.html'
    history.parent.mkdir(parents=True)
    history.write_text('fixture')
    pages = {'1': {'path': '/p/fixture/'}}
    compare_pages.publish(out, db, cfg, NOW, 'https://wowpriced.com', 'Disclosure.', pages)
    page = out / 'compare/laundry-pods/index.html'
    assert 'href="/p/fixture/"' not in page.read_text()
    schema = next(s for s in seo_audit.Page(page.read_text(), '/compare/laundry-pods/').schemas
                  if s.get('@type') == 'ItemList')
    assert schema['itemListElement'][0]['url'] == 'https://wowpriced.com/compare/laundry-pods/#' + products[0]['asin']
    history.unlink()
    compare_pages.publish(out, db, cfg, NOW, 'https://wowpriced.com', 'Disclosure.', pages)
    assert 'href="/p/fixture/"' not in page.read_text()


def test_transport_failure_keeps_conservative_debit_and_no_replay(db, cfg, products):
    fetcher = Fetcher(cfg, products)
    def disconnected(url, **kwargs):
        fetcher.calls.append((url, kwargs['params']))
        raise httpx.ReadError('fixture disconnected')
    fetcher.api_get = disconnected
    result = refresh(db, cfg, fetcher)
    assert result['tokens'] == result['estimated_tokens'] == 30
    assert result['receipts'] == [None]
    assert refresh(db, cfg, fetcher) == result and len(fetcher.calls) == 1


def test_deadline_after_finder_stops_products(db, cfg, products, monkeypatch):
    tick = [100.0]
    monkeypatch.setattr(compare.time, 'monotonic', lambda: tick[0])
    fetcher = Fetcher(cfg, products)
    original = fetcher.api_get
    def slow_response(url, **kwargs):
        response = original(url, **kwargs)
        tick[0] += 21
        return response
    fetcher.api_get = slow_response
    result = refresh(db, cfg, fetcher)
    assert result['finders'] == 1 and result['products'] == 0 and len(fetcher.calls) == 1


@pytest.mark.parametrize('raw', ['null', '[]', '{"runs": []}', '{"runs": {}, "day": "2026-10-17", "tokens": true}'])
def test_unreadable_quota_cannot_authorize_calls(db, cfg, products, raw):
    db.meta_set(compare.LEDGER, raw)
    fetcher = Fetcher(cfg, products)
    refresh(db, cfg, fetcher)
    assert fetcher.calls == []


@pytest.mark.parametrize('reserve', [11, 30, 47])
def test_finder_reservation_is_durable_before_request(db, cfg, products, reserve):
    cfg.compare['finder_reserve_tokens'] = reserve
    fetcher = Fetcher(cfg, products)
    original = fetcher.api_get
    def reserved(url, **kwargs):
        if url == compare.QUERY_URL:
            persisted = KeepaBudget.load(db)
            assert persisted.tokens_left == 3600 - reserve
            ledger = json.loads(db.meta_get(compare.LEDGER))
            assert ledger['tokens'] == ledger['runs']['run-1']['tokens'] == reserve
        return original(url, **kwargs)
    fetcher.api_get = reserved
    result = refresh(db, cfg, fetcher)
    assert result['products'] == 6


@pytest.mark.parametrize('slug,title,metadata,expected', [
    ('laundry-pods', 'Pods 81 Count', {}, 81),
    ('laundry-pods', 'Pods 81 Count', {'numberOfItems': 2}, None),
    ('laundry-pods', 'Pods 81 Count', {'packageQuantity': 3}, None),
    ('laundry-pods', 'Pods Pack of 3, 20 Count Each', {'numberOfItems': 3, 'packageQuantity': 3}, 60),
    ('laundry-pods', 'Pods Pack of 3, 20 Count Each', {'numberOfItems': 2}, None),
    ('laundry-pods', 'Pods 20 Count Each, Pack of 3', {}, None),
    ('laundry-pods', 'Pods Pack of 0, 20 Count Each', {}, None),
    ('laundry-pods', 'Pods 81 Count (Pack of 3)', {}, None),
    ('laundry-pods', 'Pods 3 in 1, 2 Pack', {}, None),
    ('laundry-pods', 'Laundry Pods Container with 100 Bags', {}, None),
    ('laundry-pods', 'Pods 12 Pods x 6 Bags', {}, None),
    ('laundry-pods', 'Pods 6 bags of 12 pods', {}, None),
    ('laundry-pods', 'Pods 2 x 36 Count', {}, None),
    ('laundry-pods', 'Pods 36 Count x 2', {}, None),
    ('laundry-pods', 'Pods 40 to 50 Count', {}, None),
    ('laundry-pods', 'Pods 40-50 Count', {}, None),
    ('laundry-pods', 'Pods 81 Count and 42 Count', {}, None),
    ('laundry-pods', 'Pods 81 Count, 81 Count', {}, None),
    ('dish-soap', 'Soap 100 fl oz', {}, 100),
    ('dish-soap', 'Soap 12 fl oz, 2 Count', {}, None),
    ('dish-soap', 'Soap 12 fl oz, 2 bottles', {}, None),
    ('dish-soap', 'Soap 12 fl oz, Twin Pack', {}, None),
    ('dish-soap', 'Soap 12 fl oz, Five Pack', {}, None),
    ('dish-soap', 'Soap 3 bottles x 33.8 fl oz', {}, None),
    ('dish-soap', 'Soap 33.8 fl oz x 3', {}, None),
    ('dish-soap', 'Soap Pack of 3, 33.8 fl oz Each', {'packageQuantity': 3}, 101.39999999999999),
    ('sticky-notes', 'Sticky Notes 100 Sheets/Pad', {}, None),
    ('sticky-notes', 'Sticky Notes 24 Pads, 100 Sheets/Pad', {}, None),
    ('sticky-notes', 'Sticky Notes 100 Sheets per Pad', {}, None),
    ('sticky-notes', 'Sticky Notes 1200 Sheets', {}, 1200),
    ('aluminum-foil', 'Foil 75 ft, 3 Rolls', {}, None),
    ('nitrile-gloves', 'Gloves 100 Count, 10 Boxes', {}, None),
    ('nitrile-gloves', 'Gloves 100 Count', {}, 100),
    ('usb-c-cables', 'Cable 6 ft, Five Pack', {}, None),
    ('usb-c-cables', 'Cable 6 ft', {}, 6),
    ('power-banks', 'Power Bank 10000mAh', {}, 1),
    ('power-banks', '10000mAh Power Bank', {}, 1),
    ('power-banks', 'Power Bank 10000mAh', {'numberOfItems': 1, 'packageQuantity': -1}, 1),
    ('power-banks', 'Power Bank 10000mAh', {'packageQuantity': 2}, None),
    ('power-banks', 'Power Bank Twin Pack', {}, None),
    ('power-banks', 'Power Bank 2 Pack', {'packageQuantity': 2}, 2),
    ('microsd-cards', 'microSD Card 128GB', {}, 1),
    ('microsd-cards', '128GB microSD Card', {}, 1),
    ('usb-c-wall-chargers', '65W USB C Wall Charger', {}, 1),
    ('power-strips', 'Power Strip 12 Outlets 4 USB, 6 ft Cord', {}, None),
    ('aa-batteries', 'AA Batteries 48 Count', {}, 48),
    ('aa-batteries', 'AA Batteries 2 Pack', {}, None),
    ('paper-towels', 'Paper Towels 6 Rolls, 100 Sheets', {}, None),
])
def test_review_quantities_use_real_term_words(slug, title, metadata, expected):
    term = next(t for t in compare.load_terms() if t['slug'] == slug)
    value, quote = compare.quantity_with_evidence(dict(title=title, **metadata), term)
    assert value == expected
    assert (quote in title and quote) if expected else quote == ''
    if expected and 'Pack of 3' in title:
        assert 'Pack of 3' in quote and 'Each' in quote


@pytest.mark.parametrize('slug,title,metadata,quantity', [
    ('microsd-cards', 'microSD Cards, 2 Cards', {}, 2),
    ('usb-c-wall-chargers', 'USB C Wall Charger, 2 Chargers', {}, 2),
    ('power-strips', 'Power Strips, 2 Strips', {'packageQuantity': 2}, 2),
    ('power-banks', 'Power Banks, 2 Banks', {}, 2),
    ('smart-plugs', 'Smart Plugs, 2 Plugs', {}, 2),
    ('microsd-cards', 'microSD Cards, 2 Cards', {'numberOfItems': 3}, None),
    ('microsd-cards', 'microSD Cards, 2 Memory Cards', {}, None),
    ('usb-c-wall-chargers', 'USB C Wall Charger, 2 Compact Chargers', {}, None),
    ('toilet-paper', 'Toilet Paper, 18 Mega Rolls, 224 Sheets', {}, None),
    ('paper-towels', 'Paper Towels, 6 Double Rolls, 100 Sheets', {}, None),
    ('sticky-notes', 'Sticky Notes, 3 Large Pads, 100 Sheets', {}, None),
    ('dish-soap', 'Dish Soap, 2 Refill Bottles, 12 fl oz', {}, None),
])
def test_item_nouns_and_modified_packs_do_not_invent_best_value(db, cfg, products, slug, title, metadata, quantity):
    term = next(t for t in compare.load_terms() if t['slug'] == slug)
    value, quote = compare.quantity_with_evidence(dict(title=title, **metadata), term)
    assert value == quantity
    assert (quote and quote in title) if quantity else quote == ''
    single_title = {'each': 'Single product', 'count': 'Product 100 Sheets',
                    'fl_oz': 'Product 12 fl oz'}[term['unit']]
    rows = [dict(products[0], title=single_title, stats={'current': [-1, 2000]},
                 unitCount=None, numberOfItems=1, packageQuantity=1),
            dict(products[1], title=title, stats={'current': [-1, 3000]},
                 unitCount=None, numberOfItems=1, packageQuantity=1) | metadata]
    data = seed(db, rows, term)
    ranked = compare.ranked_rows(term, data, cfg, NOW)
    target = next(row for row in ranked if row['title'] == title)
    assert target['unit_price'] == unit_price(30, quantity)
    assert ranked[0]['title'] == (title if quantity else single_title)


@pytest.mark.parametrize('value', [0, -1, None, 'junk', float('nan')])
def test_junk_unit_count_is_absent(value):
    assert compare.quantity_with_evidence(dict(title='Pods 81 Count', unitCount={
        'unitType': 'count', 'unitValue': value}), TERM) == (81, '81 Count')


@pytest.mark.parametrize('quantity,packs,expected', [(20, 3, 60), (1.5, 2, 3), (1, 0, None),
    (1, 1.5, None), (True, 2, None), (float('inf'), 2, None), (1e308, 10, None)])
def test_pack_math(quantity, packs, expected):
    assert pack_quantity(quantity, packs) == expected


@pytest.mark.parametrize('title,price', [('Cartridges 10 Count', 21.87), ('Carton 10 Count', 21.87),
    ('Clips 10 Count', 21.87), ('Pods Subscribe & Save 81 Count', 21.87),
    ('Pods clip coupon 81 Count', None), ('Pods Promo Code 81 Count', None),
    ('Pods in cart 81 Count', None)])
def test_promo_title_boundaries(products, title, price):
    assert compare.product_facts(dict(products[0], title=title), NOW)['price'] == price


@pytest.mark.parametrize('images,csv,expected', [([{'l': 'new.jpg'}], 'old.jpg', 'new.jpg'),
    ([{'m': 'medium.jpg'}], None, 'medium.jpg'), (['string.jpg'], None, 'string.jpg'),
    (None, 'legacy.jpg,second.jpg', 'legacy.jpg'), ([{'l': '../bad.jpg'}], '', '')])
def test_both_image_formats(products, images, csv, expected):
    assert compare.product_facts(dict(products[0], images=images, imagesCSV=csv), NOW)['image'] == expected


def test_missing_observation_hides_price(db, cfg, products):
    products[0].pop('lastUpdate')
    data = seed(db, products[:1])
    assert data['products'][products[0]['asin']]['price'] is None
    data['products'][products[0]['asin']].update(price=10, observed_at=iso(NOW))
    assert compare.ranked_rows(TERM, data, cfg, NOW) == []


@pytest.mark.parametrize('contents', [None, ': invalid: [', '{}'])
def test_terms_load_failure_preserves_all_facts(db, cfg, products, contents):
    seed(db, products)
    before = db.meta_get(compare.STATE)
    path = Path(cfg.compare['terms_file'])
    if contents is None:
        path.unlink()
    else:
        path.write_text(contents)
    fetcher = Fetcher(cfg, products)
    refresh(db, cfg, fetcher)
    assert not fetcher.calls and db.meta_get(compare.STATE) == before


@pytest.mark.parametrize('settings', [dict(refresh_hours=24), dict(refresh_hours=25), dict(max_price_age_h=12)])
def test_refresh_must_precede_expiry(settings):
    with pytest.raises(ValueError, match='refresh_hours.*max_price_age_h'):
        validated_compare(settings)


def test_max_price_rounds_cents():
    assert compare.selection(dict(TERM, max_price=19.995), validated_compare({}))['current_NEW_lte'] == 2000


@pytest.mark.parametrize('mode', ['400', 'transport', 'missing-list'])
def test_bad_finder_backs_off_and_allows_next_term(db, cfg, products, mode, caplog):
    terms = [dict(TERM, slug='a-bad', keywords=['bad']), dict(TERM, slug='b-good', keywords=['good'])]
    Path(cfg.compare['terms_file']).write_text(yaml.safe_dump(terms))
    fetcher = Fetcher(cfg, products)
    original = fetcher.api_get
    def response(url, **kwargs):
        if url == compare.QUERY_URL and json.loads(kwargs['params']['selection'])['title'] == 'bad':
            response = original(url, **kwargs)
            if mode == 'transport':
                raise httpx.ReadError('private payload must not be logged')
            body = response.json()
            body.pop('asinList')
            return httpx.Response(400 if mode == '400' else 200, json=body, request=response.request)
        return original(url, **kwargs)
    fetcher.api_get = response
    result = refresh(db, cfg, fetcher)
    assert result['products'] == len(products)
    data = compare.state(db)
    assert data['terms']['a-bad']['finder_at'] == iso(NOW)
    assert data['terms']['a-bad']['retry_at'] > iso(NOW)
    assert data['terms']['b-good']['read_at'] == iso(NOW)
    count = len(fetcher.calls)
    refresh(db, cfg, fetcher, 'next', now=NOW + timedelta(minutes=10))
    assert len(fetcher.calls) == count and 'private payload' not in caplog.text


def test_bad_product_batch_backs_off_and_allows_next_term(db, cfg, products, monkeypatch):
    terms = [dict(TERM, slug='a'), dict(TERM, slug='b')]
    Path(cfg.compare['terms_file']).write_text(yaml.safe_dump(terms))
    data = seed(db, products[:1], terms[0])
    data['terms']['b'] = dict(asins=[products[1]['asin']], finder_at=iso(NOW))
    data['terms']['a'].pop('read_at')
    data['products'] = {}
    db.meta_set(compare.STATE, json.dumps(data))
    monkeypatch.setattr(verify, 'KEEPA_BATCH', 1)
    fetcher = Fetcher(cfg, products)
    original = fetcher.api_get
    def response(url, **kwargs):
        response = original(url, **kwargs)
        return httpx.Response(400, json=response.json(), request=response.request) if kwargs['params'].get('asin') == products[0]['asin'] else response
    fetcher.api_get = response
    result = refresh(db, cfg, fetcher)
    assert result['products'] == 1
    data = compare.state(db)
    assert data['terms']['a']['retry_at'] > iso(NOW)
    assert data['terms']['b']['read_at'] == iso(NOW)
    refresh(db, cfg, fetcher, 'next', now=NOW + timedelta(minutes=10))
    assert len(fetcher.calls) == 2


def test_oldest_source_observation_brings_refresh_forward(db, cfg, products):
    lag = int((NOW - timedelta(hours=3)).timestamp() // 60) - verify.KEEPA_EPOCH_MINUTES
    products = [dict(products[0], lastUpdate=lag)]
    seed(db, products)
    fetcher = Fetcher(cfg, products)
    refresh(db, cfg, fetcher, 'early', now=NOW + timedelta(hours=8))
    assert not fetcher.calls
    refresh(db, cfg, fetcher, 'due', now=NOW + timedelta(hours=9))
    assert len(fetcher.calls) == 1 and fetcher.calls[0][0].endswith('/product')
    assert compare.ranked_rows(TERM, compare.state(db), cfg, NOW + timedelta(hours=9))
    refresh(db, cfg, fetcher, 'backoff', now=NOW + timedelta(hours=9, minutes=10))
    assert len(fetcher.calls) == 1


@pytest.mark.parametrize('phase', ['finder', 'product'])
def test_unsent_deadline_refunds_both_ledgers(db, cfg, products, monkeypatch, phase):
    from dealradar.errors import DeadlineExceeded
    if phase == 'product':
        data = seed(db, products)
        data['terms'][TERM['slug']].pop('read_at')
        data['products'] = {}
        db.meta_set(compare.STATE, json.dumps(data))
    fetcher = Fetcher(cfg, products)
    def unsent(url, **kwargs):
        fetcher.calls.append(url)
        raise DeadlineExceeded('not sent')
    fetcher.api_get = unsent
    budget = KeepaBudget(3600, 60, NOW, db=db)
    result = refresh(db, cfg, fetcher, budget=budget)
    assert len(fetcher.calls) == 1
    assert result['tokens'] == result['estimated_tokens'] == 0 and result['receipts'] == []
    assert json.loads(db.meta_get(compare.LEDGER))['tokens'] == 0
    assert KeepaBudget.load(db).tokens_left == 3600 and budget.usage.requests == 0
    refresh(db, cfg, fetcher, budget=budget)
    assert len(fetcher.calls) == 1


@pytest.mark.parametrize('phase', ['finder', 'product'])
@pytest.mark.parametrize('encoding', ['identity', 'gzip'])
def test_stream_deadline_retains_sent_debit_and_stops_replay(db, cfg, products, monkeypatch, offline, phase, encoding):
    from dealradar.http_client import PoliteFetcher
    from tests.http_helpers import HostClock
    clock, calls, closed = HostClock(), [], []
    monkeypatch.setattr(compare.time, 'monotonic', clock)
    monkeypatch.setattr(httpx.Client, 'send', offline)
    cfg.request_timeout_s = 5
    if phase == 'product':
        data = seed(db, products[:1])
        data['terms'][TERM['slug']].pop('read_at')
        data['products'] = {}
        db.meta_set(compare.STATE, json.dumps(data))

    class SlowBody(httpx.SyncByteStream):
        def __iter__(self):
            compressed = gzip.compress(b' ')
            chunks = ([compressed[i:i+1] for i in range(5)] + [compressed[5:]]
                      if encoding == 'gzip' else [b' '] * 6)
            for chunk in chunks:
                clock.sleep(4)
                yield chunk

        def close(self):
            closed.append(True)

    def handle(request):
        assert request.url.host == 'api.keepa.com'
        calls.append(request.url.path)
        return httpx.Response(200, stream=SlowBody(), headers={'Content-Encoding': encoding})

    budget = KeepaBudget(3600, 60, NOW, db=db)
    fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handle),
                            clock=clock, sleep_fn=clock.sleep)
    try:
        result = refresh(db, cfg, fetcher, budget=budget, deadline=20)
        assert clock() == 20 and closed == [True]
        cost = 30 if phase == 'finder' else 1
        assert result['tokens'] == result['estimated_tokens'] == cost
        assert result['receipts'] == [None] and budget.usage.requests == 1
        assert KeepaBudget.load(db).tokens_left == 3600 - cost
        assert json.loads(db.meta_get(compare.LEDGER))['tokens'] == cost
        assert compare.state(db)['products'] == {}
        assert refresh(db, cfg, fetcher, budget=budget, deadline=40) == result
        assert calls == ['/query' if phase == 'finder' else '/product']
    finally:
        fetcher.close()


@pytest.mark.parametrize('headroom,expected', [(1.99, 0), (2, 0), (2.01, 2)])
def test_request_needs_timeout_plus_host_spacing(db, cfg, products, monkeypatch, headroom, expected):
    monkeypatch.setattr(compare.time, 'monotonic', lambda: 100)
    fetcher = Fetcher(cfg, products)
    refresh(db, cfg, fetcher, deadline=100 + headroom)
    assert len(fetcher.calls) == expected


def test_comparison_timeout_fits_without_changing_global_timeout(db, cfg, products):
    cfg.request_timeout_s = AppConfig().request_timeout_s
    fetcher = Fetcher(cfg, products)
    assert refresh(db, cfg, fetcher)['products'] == len(products)
    assert cfg.request_timeout_s == 20 and cfg.compare['request_timeout_s'] == 5


def test_product_reads_do_not_change_other_keepa_ledgers(db, cfg, products, monkeypatch):
    from dealradar import accuracy, keepa_categories, keepa_rechecks
    def forbidden(*args, **kwargs):
        raise AssertionError('comparison polluted a shared sample/cache')
    monkeypatch.setattr(keepa_categories, 'remember_roots', forbidden)
    monkeypatch.setattr(accuracy, 'published', forbidden)
    monkeypatch.setattr(keepa_rechecks.CallHistory, 'record', forbidden)
    fetcher = Fetcher(cfg, products)
    assert refresh(db, cfg, fetcher)['products'] == len(products)


@pytest.mark.parametrize('value,unit,expected', [(19.995, 'count', '$20.00 per item'),
    (1.5, 'fl_oz', '$1.50 per fl oz'), (.2, 'load', '20¢ per load'), (.2727, 'count', '27.27¢ per item')])
def test_unit_text_money_and_display_words(value, unit, expected):
    assert compare_pages.unit_text(value, unit) == expected
    assert compare_pages.unit_text(.2, 'count', 'roll') == '20¢ per roll'


@pytest.mark.parametrize('mode', ['preview', 'on'])
def test_no_compare_index_or_links_without_rows(tmp_path, db, cfg, products, mode):
    cfg.compare['publish'] = mode
    out = publish(tmp_path, db, cfg, products)
    assert (out / 'compare/index.html').is_file()
    site.render_site(db, cfg, NOW + timedelta(hours=25), out_dir=out, deals=[])
    assert not (out / 'compare').exists()
    assert all(b'/compare/' not in content for name, content in files(out).items() if name.endswith('.html'))


def test_all_sized_banner_and_same_site_schema(tmp_path, db, cfg, products):
    cfg.compare['publish'] = 'on'
    out = publish(tmp_path, db, cfg, products[:3])
    page = (out / 'compare/laundry-pods/index.html').read_text()
    assert 'Best value:' in page and 'Best value among' not in page
    parsed = seo_audit.Page(page, '/compare/laundry-pods/')
    schema = next(s for s in parsed.schemas if s.get('@type') == 'ItemList')
    assert [r['url'] for r in schema['itemListElement']] == [
        'https://wowpriced.com/compare/laundry-pods/#' + products[i]['asin'] for i in (0, 2, 1)]


def test_fact_lastmod_and_indexnow_ignore_clock_changes(tmp_path, db, cfg, products, monkeypatch):
    cfg.compare['publish'] = 'on'
    cfg.seo_indexnow_key = 'offline256bkey'
    out = publish(tmp_path, db, cfg, products[:3])
    url = 'https://wowpriced.com/compare/laundry-pods/'
    before = db.indexnow_pages()[url]
    old_sitemap = (out / 'sitemap.xml').read_text()
    data = compare.state(db)
    for p in data['products'].values():
        p['observed_at'] = iso(NOW + timedelta(minutes=5))
        p['lastUpdate'] += 5
    db.meta_set(compare.STATE, json.dumps(data))
    later = NOW + timedelta(minutes=15)
    monkeypatch.setattr(site, 'util_now', lambda: later)
    site.render_site(db, cfg, later, out_dir=out, deals=[])
    assert db.indexnow_pages()[url] == before
    assert (out / 'sitemap.xml').read_text() == old_sitemap
    assert 'Checked 10 min ago' in (out / 'compare/laundry-pods/index.html').read_text()
    data['products'][products[0]['asin']]['price'] += 1
    db.meta_set(compare.STATE, json.dumps(data))
    site.render_site(db, cfg, later, out_dir=out, deals=[])
    assert db.indexnow_pages()[url]['content_hash'] != before['content_hash']


def test_optional_page_failure_logs_type_and_finishes(tmp_path, db, cfg, products, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise ValueError('private exception payload')
    monkeypatch.setattr(compare_pages, 'publish', broken)
    out = publish(tmp_path, db, cfg, products)
    assert (out / 'index.html').exists() and (out / 'sitemap.xml').exists()
    assert 'compare pages unavailable (ValueError)' in caplog.text
    assert 'private exception payload' not in caplog.text


def test_browser_clock_expires_rows_and_banner():
    if not shutil.which('node'):
        pytest.skip('node is unavailable')
    script = compare_pages.CLOCK.removeprefix('<script>').removesuffix('</script>')
    harness = r'''const assert=require('node:assert/strict');
let now=Date.parse('2026-10-17T12:00:00Z');Date.now=()=>now;
let timer,events={};
function row(stamp){return {hidden:false,clock:{textContent:'',getAttribute:()=>stamp},
querySelector(){return this.clock}}}
let rows=[row('2026-10-17T12:00:00Z'),row('2026-10-17T11:00:00Z')];
let list={dataset:{maxAgeHours:'24'},querySelectorAll:s=>s==='.compare-row'?rows:[]};
let banner={hidden:false},empty={hidden:true};
global.document={querySelector:s=>s==='.compare-list'?list:banner,
getElementById:()=>empty,addEventListener:(name,fn)=>events[name]=fn};
global.window={addEventListener:(name,fn)=>events[name]=fn};
global.setInterval=(fn,delay)=>{assert.equal(delay,60000);timer=fn};
SCRIPT
assert.equal(rows[0].clock.textContent,'Checked 0 min ago');
assert.equal(rows[1].clock.textContent,'Checked 60 min ago');
now+=10*60000;timer();assert.equal(rows[0].clock.textContent,'Checked 10 min ago');
assert.equal(banner.hidden,false);
now+=23*3600000;events.pageshow();assert.equal(rows[0].hidden,false);
assert.equal(rows[1].hidden,true);assert.equal(banner.hidden,true);assert.equal(empty.hidden,true);
now+=3600000;events.visibilitychange();assert.equal(rows[0].hidden,true);assert.equal(empty.hidden,false);
'''.replace('SCRIPT', script)
    result = subprocess.run(['node', '-e', harness], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('change,badge', [({}, True), ({'status': 'dead'}, False),
    ({'regular_price_basis': '90-day average price on Amazon'}, False),
    ({'regular_price_basis': 'Lowest historical price'}, False),
    ({'credits': [{'name': 'Amazon price drop'}]}, False),
    ({'regular_price_basis': 'List price, as stated by Amazon'}, True),
    ({'price_unconfirmed': True}, False), ({'price_aged': True}, False),
    ({'price_rechecked_at': ''}, False), ({'gone_suspect_at': iso(NOW)}, False),
    ({'price_rechecked_at': iso(NOW - timedelta(days=10))}, False),
    ({'price_rechecked_at': iso(NOW + timedelta(minutes=1))}, False),
    ({'pct_at_checkout': None}, False), ({'store': 'Walmart', 'canonical_url': 'https://www.walmart.com/ip/1'}, False)])
def test_sale_badge_requires_live_confirmed_published_deal(tmp_path, db, cfg, products, change, badge):
    import hashlib
    from tests.test_publish_site import make_deal
    seed(db, products[:3])
    deal = make_deal(**(dict(dedupe_key='sale', store='Amazon', asin=products[0]['asin'],
        canonical_url='https://www.amazon.com/dp/' + products[0]['asin'],
        price_rechecked_at=iso(NOW), first_seen_at=iso(NOW), price_since=iso(NOW),
        pct_at_checkout=71.3) | change))
    out = tmp_path / 'site'
    page = out / 'deal/sale/index.html'
    page.parent.mkdir(parents=True)
    page.write_text('published deal')
    paths = {hashlib.sha256(b'sale').hexdigest()[:8]: '/deal/sale/'}
    compare_pages.publish(out, db, cfg, NOW, 'https://wowpriced.com', 'Disclosure.', {}, deals=[deal], deal_paths=paths)
    html = (out / 'compare/laundry-pods/index.html').read_text()
    assert ('On sale now — 71.3% off' in html) == badge
    if badge:
        assert 'class="sale-badge" href="/deal/sale/"' in html
    listing = html[html.index('<ol class="compare-list"'):]
    assert listing.index('Tide PODS') < listing.index('Pack of 3') < listing.index('Other Laundry')
    page.unlink()
    compare_pages.publish(out, db, cfg, NOW, 'https://wowpriced.com', 'Disclosure.', {}, deals=[deal], deal_paths=paths)
    assert 'On sale now' not in (out / 'compare/laundry-pods/index.html').read_text()


def test_site_passes_published_deals_to_sale_badges(tmp_path, db, cfg, products):
    from tests.test_publish_site import make_deal
    seed(db, products[:1])
    deal = make_deal(store='Amazon', asin=products[0]['asin'],
        canonical_url='https://www.amazon.com/dp/' + products[0]['asin'],
        price_rechecked_at=iso(NOW), first_seen_at=iso(NOW), price_since=iso(NOW),
        pct_at_checkout=71.3)
    out = tmp_path / 'site'
    site.render_site(db, cfg, NOW, out_dir=out, deals=[deal])
    assert 'On sale now — 71.3% off' in (out / 'compare/laundry-pods/index.html').read_text()


@pytest.mark.parametrize('used', [False, True])
def test_sale_badge_expires_on_its_own_confirmation_clock(tmp_path, db, cfg, products, used):
    from html.parser import HTMLParser
    from tests.test_publish_site import make_deal
    cfg.keepa_show_max_unconfirmed_h = 12
    cfg.keepa_show_max_unconfirmed_used_h = 6
    hours = 6 if used else 12
    confirmed = NOW - timedelta(hours=hours) + timedelta(minutes=1)
    deal = make_deal(store='Amazon', asin=products[0]['asin'],
        canonical_url='https://www.amazon.com/dp/' + products[0]['asin'],
        conditions=['used'] if used else [], price_rechecked_at=iso(confirmed),
        first_seen_at=iso(confirmed), price_since=iso(confirmed), pct_at_checkout=71.3)
    seed(db, products[:1])
    out = tmp_path / 'site'
    site.render_site(db, cfg, NOW, out_dir=out, deals=[deal])
    page = (out / 'compare/laundry-pods/index.html').read_text()

    class Badges(HTMLParser):
        def __init__(self):
            super().__init__()
            self.badges = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if attrs.get('class') == 'sale-badge':
                self.badges.append(attrs)

    parser = Badges()
    parser.feed(page)
    assert len(parser.badges) == 1
    expiry = parser.badges[0]['data-confirmed-until']
    assert expiry == iso(NOW + timedelta(minutes=1))
    if not shutil.which('node'):
        pytest.skip('node is unavailable')
    script = compare_pages.CLOCK.removeprefix('<script>').removesuffix('</script>')
    harness = r'''const assert=require('node:assert/strict');
let now=Date.parse('2026-10-17T12:00:00Z');Date.now=()=>now;
let timer,events={};
let badge={hidden:false,dataset:{confirmedUntil:EXPIRY}};
let fresh={hidden:false,dataset:{confirmedUntil:'2026-10-18T00:00:00Z'}};
let clock={textContent:'',getAttribute:()=> '2026-10-17T12:00:00Z'};
let row={hidden:false,querySelector:()=>clock};
let list={dataset:{maxAgeHours:'24'},querySelectorAll:s=>s==='.compare-row'?[row]:[badge,fresh]};
let banner={hidden:false},empty={hidden:true};
global.document={querySelector:s=>s==='.compare-list'?list:banner,
getElementById:()=>empty,
addEventListener:(name,fn)=>events[name]=fn};
global.window={addEventListener:(name,fn)=>events[name]=fn};
global.setInterval=(fn,delay)=>{assert.equal(delay,60000);timer=fn};
SCRIPT
assert.equal(badge.hidden,false);assert.equal(row.hidden,false);
now+=60000;timer();assert.equal(badge.hidden,false);
now+=60000;timer();assert.equal(badge.hidden,true);
assert.equal(fresh.hidden,false);assert.equal(row.hidden,false);assert.equal(banner.hidden,false);
badge.hidden=false;events.pageshow();assert.equal(badge.hidden,true);
badge.hidden=false;events.visibilitychange();assert.equal(badge.hidden,true);
badge.dataset.confirmedUntil='invalid';events.pageshow();assert.equal(badge.hidden,true);
'''.replace('EXPIRY', json.dumps(expiry)).replace('SCRIPT', script)
    result = subprocess.run(['node', '-e', harness], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert '.sale-badge[hidden]' in compare_pages.STYLE


@pytest.mark.parametrize("list_price,public_pct", [(None, None), (40, 50)])
@pytest.mark.parametrize("omit_pct", [False, True])
def test_sale_badge_copies_public_projection_not_private_history(tmp_path, db, cfg, products, list_price, public_pct, omit_pct, monkeypatch):
    from tests.test_publish_site import make_deal

    cfg.amazon_reference = 'list_price'
    cfg.amazon_history = 'hide'
    if omit_pct:
        serialize = site._serialize_deal
        def without_pct(*args, **kwargs):
            public = serialize(*args, **kwargs)
            public.pop('pct_at_checkout', None)
            return public
        monkeypatch.setattr(site, '_serialize_deal', without_pct)
        public_pct = None
    seed(db, products[:1])
    asin = products[0]['asin']
    deal = make_deal(store='Amazon', asin=asin, source_name='keepa', codes=[],
        canonical_url='https://www.amazon.com/dp/' + asin,
        price_rechecked_at=iso(NOW), first_seen_at=iso(NOW), price_since=iso(NOW),
        final_price=20, regular_price=100, pct_at_checkout=80, abs_savings=80,
        sightings=[dict(source_name='keepa', source_url='https://keepa.com/#!product/1-' + asin,
                        seen_at=iso(NOW), final_price=20)],
        keepa_list_price=dict(asin=asin, price=list_price, observed_at=iso(NOW)))
    out = tmp_path / 'site'
    result = site.render_site(db, cfg, NOW, out_dir=out, deals=[deal])
    published, = result['public_deals']
    assert published.get('pct_at_checkout') == public_pct
    assert ('pct_at_checkout' not in published) == omit_pct
    html = (out / 'compare/laundry-pods/index.html').read_text()
    assert '80% off' not in html
    assert ('On sale now — 50% off' in html) == (public_pct == 50)
    assert ('class="sale-badge"' in html) == (public_pct is not None)
    assert deal['pct_at_checkout'] == 80
