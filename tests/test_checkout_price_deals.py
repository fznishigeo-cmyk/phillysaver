"""Task 113: source-clock picks and paid checkout context, entirely offline."""

from datetime import timedelta
import hashlib
from pathlib import Path
import subprocess

import pytest
import yaml

from dealradar import buybox, checkout_prices, pipeline, rank, util
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import site
from tests.test_buybox_gap import record
from tests.test_e2e_offline import env, test_rerun_is_idempotent as check_rerun
from tests.test_price_accuracy_engine import NOW, api, bucket, cfg, db, seed
from tests.test_product_offer_pages import tree
from tests.test_publish_site import make_deal

ROOT = Path(__file__).resolve().parents[1]


def enable(cfg):
    cfg.front_page['checkout_price_deals'] = dict(enabled=True, top_pick_max_age_h=24,
        top_pick_window=5, top_pick_max_in_window=1)


def offer(i=0, **changes):
    return make_deal(dedupe_key=f'offer-{i}', item=f'Widget {i} Pro', brand=f'Brand {i}',
        store='Amazon', asin=f'B{i:09d}', canonical_url=f'https://amazon.com/dp/B{i:09d}',
        image_url='https://m.media-amazon.com/images/I/widget.jpg', category='home' if i % 2 else 'kitchen',
        price_rechecked_at=util.iso(NOW), first_seen_at=util.iso(NOW - timedelta(days=5)),
        sightings=[dict(source_name='slickdeals_frontpage', source_url='https://slickdeals.net/f/123',
                        seen_at=util.iso(NOW - timedelta(hours=1)), final_price=10)], **changes)


def remember(d, cfg, at=NOW, price=50):
    checkout_prices.remember(d, record(d['asin'], int(price * 100),
        lastBuyBoxUpdate=int(at.timestamp() / 60) - 21564000,
        buyBoxPrice=int(price * 100), buyBoxShipping=0), cfg, NOW)
    return d


def test_defaults_and_shipped_switch_are_off():
    assert not checkout_prices.enabled(AppConfig())
    shipped = load_config().front_page['checkout_price_deals']
    assert shipped == dict(enabled=False, top_pick_max_age_h=24, top_pick_window=5, top_pick_max_in_window=1)


def test_latest_repost_clock_and_skips_stay_on_wall(db, cfg, tmp_path, monkeypatch):
    enable(cfg)
    monkeypatch.setattr(site, 'util_now', lambda: NOW)
    fresh, stale, plain = offer(0), offer(1), offer(2, codes=[])
    stale['sightings'][0]['seen_at'] = util.iso(NOW - timedelta(hours=25))
    stale['last_seen_at'] = util.iso(NOW)  # neither DB touches nor paid reads are re-posts
    fresh['sightings'].append(dict(fresh['sightings'][0], seen_at=util.iso(NOW - timedelta(days=4))))
    cfg.support_enabled = True
    out = tmp_path / 'site'
    result = site.render_site(db, cfg, NOW, out_dir=out, deals=[stale, fresh, plain])
    rows = {d['dedupe_key']: d for d in result['public_deals']}
    assert rows[fresh['dedupe_key']]['pick']
    assert rows[plain['dedupe_key']]['pick']
    assert 'pick' not in rows[stale['dedupe_key']]
    assert rows[stale['dedupe_key']]['status'] == 'alive'
    assert all(d['item'] in (out / 'index.html').read_text() for d in (fresh, stale, plain))
    assert result['checkout_price_deals'] == dict(in_top_picks=1, skipped_age=1,
        skipped_window=0, labelled=0, unlabelled_no_reading=2)


@pytest.mark.parametrize('hours,eligible', [(24, True), (24.001, False)])
def test_age_boundary_and_missing_clock(cfg, hours, eligible):
    enable(cfg)
    d = offer()
    d['sightings'][0]['seen_at'] = util.iso(NOW - timedelta(hours=hours))
    assert bool(rank.top_picks([(d, 100)], cfg.front_page, NOW)) is eligible
    for sightings in ([], [{'seen_at': 'bad'}], [{'seen_at': util.iso(NOW + timedelta(hours=1))}]):
        d['sightings'] = sightings
        assert rank.top_picks([(d, 100)], cfg.front_page, NOW) == []


@pytest.mark.parametrize('window,maximum', [(5, 1), (3, 2), (1, 0)])
def test_window_twelve_codes_fills_slots_and_preserves_caps(cfg, window, maximum):
    enable(cfg)
    cfg.front_page['checkout_price_deals'].update(top_pick_window=window, top_pick_max_in_window=maximum)
    cfg.front_page['top_picks']['count'] = 30
    rows = [offer(i, codes=['SAVE'] if i % 3 == 0 and i < 36 else []) for i in range(90)]
    summary = checkout_prices.summary()
    picks = rank.top_picks([(d, 100) for d in rows], cfg.front_page, NOW, summary=summary)
    assert len(picks) == 30  # ties keep input order; skipped slots go to the next eligible row
    assert [rows.index(d) for d in picks] == sorted(rows.index(d) for d in picks)
    for i in range(len(picks)):
        assert sum(bool(d['codes']) for d in picks[i:i + window]) <= maximum
    if maximum < 2:
        assert summary['skipped_window'] > 0
    assert summary['in_top_picks'] == sum(bool(d['codes']) for d in picks)
    assert max(sum(d['category'] == cat for d in picks) for cat in ('home', 'kitchen')) <= 24
    # Clustered promos cannot block filling later positions with plain offers.
    clustered = [offer(i) for i in range(12)] + [offer(i, codes=[]) for i in range(12, 80)]
    summary = checkout_prices.summary()
    selected = rank.top_picks([(d, 100) for d in clustered], cfg.front_page, NOW, summary=summary)
    assert len(selected) == 30
    assert summary['skipped_window'] > 0
    assert sum(bool(d['codes']) for d in selected[:window]) <= maximum


@pytest.mark.parametrize('changes,kind,suffix', [
    ({'codes': ['SAVE']}, 'code', 'until you enter the code'),
    ({'codes': [], 'stack_steps': ['Enter promo code SAVE']}, 'code', 'until you enter the code'),
    ({'codes': [], 'stack_steps': ['Clip the coupon']}, 'coupon', 'before the coupon'),
    ({'codes': [], 'stack_steps': ['Extra 20% at checkout']}, 'coupon', 'before the coupon'),
    ({'codes': [], 'conditions': ['subscribe_save']}, 'subscribe_save', 'before Subscribe & Save'),
    ({'codes': [], 'stack_steps': ['Subscribe & Save']}, 'subscribe_save', 'before Subscribe & Save'),
])
def test_label_each_kind_in_json_static_live_card_and_detail(db, cfg, tmp_path, changes, kind, suffix):
    enable(cfg)
    d = remember(offer(**changes), cfg, price=50.25)
    cfg.support_enabled = True
    out = tmp_path / 'site'
    result = site.render_site(db, cfg, NOW, out_dir=out, deals=[d])
    public = result['public_deals'][0]
    assert public['checkout_price'] == {'price': 50.25, 'kind': kind}
    expected = 'Amazon shows $50.25 ' + suffix
    assert checkout_prices.text(public) == expected
    from html import unescape
    home = (out / 'index.html').read_text()
    detail = (out / public['deal_path'].strip('/') / 'index.html').read_text()
    assert expected in unescape(home) and expected in unescape(detail)
    assert 'How to get this price' in home and '>Get deal</a>' in home and 'price checked ' in home
    assert result['checkout_price_deals']['labelled'] == 1
    assert result['checkout_price_deals']['unlabelled_no_reading'] == 0
    data = (out / 'deals.json').read_text()
    assert all(private not in data for private in ('checkout_price_reading', 'buybox_check', 'sightings', 'keepa'))
    subprocess.run(['node', str(ROOT / 'tests/checkout_price_logic.cjs'), str(out), expected],
                   check=True, capture_output=True, text=True)


@pytest.mark.parametrize('change', [
    {'checkout_price_reading': None},
    {'checkout_price_reading': {'price': 60}},
    {'asin': 'B999999999'}, {'final_price': 9},
    {'store': 'Walmart', 'canonical_url': 'https://walmart.com/item'},
    {'conditions': ['used']}, {'status': 'dead'}, {'codes': [], 'stack_steps': []},
    {'price_since': util.iso(NOW + timedelta(minutes=1))},
])
def test_no_label_for_missing_or_unrelated_reading(cfg, change):
    enable(cfg)
    d = remember(offer(), cfg)
    d.update(change)
    assert checkout_prices.label(d, cfg, NOW) is None


@pytest.mark.parametrize('hours,price,expected', [(12, 50, True), (12.01, 50, False),
    (-1, 50, False), (0, 10, False), (0, 9, False)])
def test_label_reading_clock_and_price_boundaries(cfg, hours, price, expected):
    enable(cfg)
    cfg.keepa_show_max_unconfirmed_h = 12
    d = remember(offer(), cfg, at=NOW - timedelta(hours=hours), price=price)
    assert bool(checkout_prices.label(d, cfg, NOW)) is expected


@pytest.mark.parametrize('bad', [None, -1, True, 'now'])
def test_no_borrowed_clock_for_buybox_or_amazon(cfg, bad):
    enable(cfg)
    d = offer()
    product = record(d['asin'], 5000, buyBoxPrice=5000, buyBoxShipping=0, lastBuyBoxUpdate=bad)
    checkout_prices.remember(d, product, cfg, NOW)
    assert checkout_prices.label(d, cfg, NOW) is None
    product['stats'] = {'current': [5000, 900]}
    product['lastUpdate'] = bad
    checkout_prices.remember(d, product, cfg, NOW)
    assert checkout_prices.label(d, cfg, NOW) is None


def test_paid_recheck_persists_only_facts_and_rerun_buys_nothing(db, cfg, monkeypatch, tmp_path):
    enable(cfg)
    d = seed(db, cfg, codes=['SAVE'])
    calls = api(monkeypatch, {d['asin']: record(d['asin'], 5000)})
    bucket(db, 1000)
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    saved = db.get_deal(d['dedupe_key'])
    assert saved['checkout_price_reading'] == dict(asin=d['asin'], final=25.0, price=50.0, at=util.iso(NOW))
    assert checkout_prices.label(saved, cfg, NOW) == dict(price=50.0, kind='code')
    before = {t: db.conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
              for t in ('deals', 'sightings', 'price_history')}
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert len(calls) == 1
    assert before == {t: db.conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in before}
    assert db.get_deal(d['dedupe_key'])['checkout_price_reading'] == saved['checkout_price_reading']


def test_enabled_pipeline_summary_and_same_run_idempotency(env, capsys, monkeypatch):
    path = Path(env['cfg'])
    config = yaml.safe_load(path.read_text())
    config['publish']['front_page']['checkout_price_deals']['enabled'] = True
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    results, run = [], pipeline.run
    def observed_run(*args, **kwargs):
        result = run(*args, **kwargs)
        results.append(result)
        return result
    monkeypatch.setattr(pipeline, 'run', observed_run)
    check_rerun(env, capsys, 'shadow')
    assert len(results) == 2
    assert all(set(result['checkout_price_deals']) == set(checkout_prices.summary()) for result in results)


@pytest.mark.parametrize('support,opened,expected', [
    (False, False, 'ed1888774a82b9034e75175e87ca048e93410ad14ab93fe7faf5cbbcd82b158d'),
    (False, True, 'fd85b288f18927f1ce03d351e4f95b18aa0f888b3b23a26af12f8e876b2e9baf'),
    (True, False, 'f2a204a8d79e79b199c384cc7acc72979c06667945378a0058119cd22824e9e1'),
    (True, True, 'f2a204a8d79e79b199c384cc7acc72979c06667945378a0058119cd22824e9e1'),
])
def test_off_matches_untouched_head_every_published_byte(tmp_path, monkeypatch, support, opened, expected):
    # Captured from untouched HEAD with the same eight paid-reading fixtures.
    monkeypatch.setattr(site, 'util_now', lambda: NOW)
    snapshots = []
    for mode in ('omitted', 'off'):
        config = load_config()
        config.front_page['checkout_price_deals']['enabled'] = False  # predates the checkout-price switch
        if mode == 'omitted':
            config.front_page.pop('checkout_price_deals')
        config.support_enabled, config.paywall_open_access = support, opened
        config.paywall_teaser_count = 1
        database = DB(':memory:')
        for i in range(8):
            d = seed(database, config, asin=f'B{i:09d}', codes=['SAVE'] if i % 2 else [],
                     price_rechecked_at=NOW.isoformat())
            buybox.remember(d, record(d['asin'], 5000), NOW, now=NOW)
            database.update_deal(d['dedupe_key'], payload=d)
        out = tmp_path / mode
        site.render_site(database, config, NOW, out_dir=out)
        snapshots.append(tree(out))
        database.close()
    assert snapshots[0] == snapshots[1]
    digest = hashlib.sha256()
    for name, content in sorted(snapshots[0].items()):
        digest.update(name.encode() + b'\0' + content)
    assert digest.hexdigest() == expected
