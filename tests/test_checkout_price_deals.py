"""Tasks 113/113b: evidence-based picks and paid checkout context, entirely offline."""

from datetime import timedelta
import hashlib
from pathlib import Path
import subprocess

import pytest
import yaml

from dealradar import buybox, checkout_prices, dedupe, pipeline, rank, savings, util, verify
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
    cfg.front_page['checkout_price_deals'] = dict(enabled=True, max_stated_days=7,
        repost_window_h=24, max_silent_h=72,
        top_pick_window=5, top_pick_max_in_window=1)


def offer(i=0, **changes):
    return make_deal(dedupe_key=f'offer-{i}', item=f'Widget {i} Pro', brand=f'Brand {i}',
        store='Amazon', asin=f'B{i:09d}', canonical_url=f'https://amazon.com/dp/B{i:09d}',
        image_url='https://m.media-amazon.com/images/I/widget.jpg', category='home' if i % 2 else 'kitchen',
        price_rechecked_at=util.iso(NOW), first_seen_at=util.iso(NOW - timedelta(days=5)),
        sightings=[dict(source_name='slickdeals_frontpage', source_url='https://slickdeals.net/f/123',
                        seen_at=util.iso(NOW - timedelta(hours=1)), final_price=10)], **changes)


def remember(d, cfg, at=NOW, price=50, now=NOW):
    checkout_prices.remember(d, record(d['asin'], int(price * 100),
        lastBuyBoxUpdate=int(at.timestamp() / 60) - 21564000,
        buyBoxPrice=int(price * 100), buyBoxShipping=0), cfg, now)
    return d


def test_defaults_and_shipped_switch_are_off():
    assert not checkout_prices.enabled(AppConfig())
    shipped = load_config().front_page['checkout_price_deals']
    assert shipped == dict(enabled=False, max_stated_days=7, repost_window_h=24, max_silent_h=72,
                           top_pick_window=5, top_pick_max_in_window=1)


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
    assert result['checkout_price_deals'] == dict(in_top_picks=1, skipped_silent=1,
        skipped_expired=0, skipped_page_price_moved=0, kept_by=dict(stated_end=0, repost=1, page_price=0),
        skipped_window=0, labelled=0, unlabelled_no_reading=2)


@pytest.mark.parametrize('hours,eligible', [(24, True), (24.001, False)])
def test_repost_boundary_and_missing_clock(cfg, hours, eligible):
    enable(cfg)
    d = offer()
    d['sightings'][0]['seen_at'] = util.iso(NOW - timedelta(hours=hours))
    assert bool(rank.top_picks([(d, 100)], cfg.front_page, NOW)) is eligible
    for sightings in ([], [{'seen_at': 'bad'}], [{'seen_at': util.iso(NOW + timedelta(hours=1))}]):
        d['sightings'] = sightings
        assert rank.top_picks([(d, 100)], cfg.front_page, NOW) == []


def only_signal(cfg, signal):
    d = offer()
    d['price_rechecked_at'] = util.iso(NOW - timedelta(hours=4))
    d['sightings'] = []
    if signal == 'stated_end':
        d['expires_at'] = util.iso(NOW + timedelta(days=1))
    elif signal == 'repost':
        d['sightings'] = [dict(source_name='slickdeals_frontpage', source_url='https://slickdeals.net/f/new',
                              seen_at=util.iso(NOW - timedelta(hours=1)), final_price=d['final_price'])]
    elif signal == 'page_price':
        at = NOW - timedelta(days=4)
        remember(d, cfg, at=at, now=at)
        remember(d, cfg)
    return d


@pytest.mark.parametrize('signal', ['stated_end', 'repost', 'page_price'])
def test_each_live_signal_alone_keeps_old_deal_in_published_picks(db, cfg, tmp_path, signal):
    enable(cfg)
    cfg.support_enabled = True
    d = only_signal(cfg, signal)
    assert rank.pick_score(d, cfg, NOW) is not None
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path / signal, deals=[d])
    assert result['public_deals'][0]['pick'] == 1
    assert result['checkout_price_deals']['kept_by'] == {
        key: int(key == signal) for key in ('stated_end', 'repost', 'page_price')}


@pytest.mark.parametrize('gone', ['expires_at', 'post_says_over', 'stated_expiry',
                                  'gone_suspect_at', 'price_moved', 'page_up', 'page_down'])
def test_each_gone_signal_wins_over_all_live_signals(cfg, gone):
    enable(cfg)
    d = only_signal(cfg, 'page_price')
    d['sightings'] = only_signal(cfg, 'repost')['sightings']
    d['expires_at'] = util.iso(NOW + timedelta(days=1))
    if gone == 'expires_at':
        d['expires_at'] = util.iso(NOW)
    elif gone in {'post_says_over', 'stated_expiry'}:
        d['death'] = gone
    elif gone == 'gone_suspect_at':
        d['gone_suspect_at'] = util.iso(NOW - timedelta(hours=1))
    elif gone == 'price_moved':
        d['flags'].append(gone)
    else:
        remember(d, cfg, price=50.51 if gone == 'page_up' else 49.49)
    counts = checkout_prices.summary()
    assert rank.top_picks([(d, 100)], cfg.front_page, NOW, cfg=cfg, summary=counts) == []
    reason = 'skipped_page_price_moved' if gone.startswith('page_') else 'skipped_expired'
    assert counts[reason] == 1
    assert sum(counts['kept_by'].values()) == 0


@pytest.mark.parametrize('wording', ['Expired', 'Sold out', 'Price went up'])
def test_existing_source_verdict_removes_pick_but_preserves_alive_row(db, cfg, wording):
    from dealradar.models import RawPost
    enable(cfg)
    d = seed(db, cfg, codes=['SAVE'])
    post = RawPost('slickdeals_frontpage', 'ended', 'https://slickdeals.net/f/123',
                   wording + ' Widget Pro', '', NOW, NOW)
    verify.mark_ended_post(db, post, NOW, cfg)
    saved = db.get_deal(d['dedupe_key'])
    assert saved['status'] == 'alive' and saved['death'] == 'post_says_over'
    assert saved['checkout_source_ended_at'] == util.iso(NOW)
    verify.live_price_reading(saved, True, NOW + timedelta(hours=1))
    assert not saved['death'] and not saved['gone_suspect_at']
    saved['expires_at'] = util.iso(NOW + timedelta(days=1))
    assert checkout_prices.pick_evidence(saved, cfg, NOW)[0] == 'skipped_expired'


@pytest.mark.parametrize('source,title,kept', [('slickdeals_frontpage', 'Widget Pro', True),
    ('slickdeals_frontpage', 'Expired Widget Pro', False), ('keepa', 'Widget Pro', False)])
def test_only_later_backed_live_source_post_supersedes_ended_verdict(db, cfg, source, title, kept):
    from dealradar.models import Evidence, ExtractedDeal, RawPost
    enable(cfg)
    d = seed(db, cfg, codes=['SAVE'])
    verify.mark_unconfirmed_dead(db, d, 'post_says_over', NOW, cfg)
    saved = db.get_deal(d['dedupe_key'])
    later = NOW + timedelta(hours=1)
    verify.live_price_reading(saved, True, later)
    db.update_deal(d['dedupe_key'], payload=saved)
    text = 'On sale for $25.00. Regular price $100.00. Use code SAVE.'
    post = RawPost(source, 'repost', 'https://slickdeals.net/f/new', title, text, later, later)
    extracted = ExtractedDeal(d['item'], 'Amazon', post.url, source, product_url=d['canonical_url'],
        online_only='true', final_price=25, regular_price=100, codes=['SAVE'], category='home',
        evidence=Evidence(final_price_quote='On sale for $25.00.', regular_price_quote='Regular price $100.00.'))
    dedupe.upsert_extracted(db, extracted, savings.compute_savings(extracted), post, cfg, later)
    after = db.get_deal(d['dedupe_key'])
    assert bool(after['checkout_source_ended_at']) is not kept
    assert bool(rank.top_picks([(after, 100)], cfg.front_page, later, cfg=cfg)) is kept


def test_paid_recheck_preserves_source_verdict_after_generic_confirmation(db, cfg, monkeypatch):
    enable(cfg)
    d = seed(db, cfg, codes=['SAVE'])
    verify.mark_unconfirmed_dead(db, d, 'post_says_over', NOW, cfg)
    later = NOW + timedelta(hours=1)
    product = record(d['asin'], 5000)
    product['lastUpdate'] += 60
    calls = api(monkeypatch, {d['asin']: product})
    bucket(db, 1000)
    pipeline._recheck_prices(db, cfg, later, image_backfill=False)
    after = db.get_deal(d['dedupe_key'])
    assert not after['death'] and not after['gone_suspect_at']
    assert after['status'] == 'alive' and after['checkout_source_ended_at'] == util.iso(NOW)
    assert after['checkout_price_first_reading'] == after['checkout_price_reading']
    assert checkout_prices.pick_evidence(after, cfg, later)[0] == 'skipped_expired'
    pipeline._recheck_prices(db, cfg, later, image_backfill=False)
    assert len(calls) == 1 and db.get_deal(d['dedupe_key']) == after


@pytest.mark.parametrize('price,kept', [(50, True), (50.5, True), (49.5, True),
                                     (50.51, False), (49.49, False)])
def test_page_price_threshold_in_both_directions_uses_first_reading(cfg, price, kept):
    enable(cfg)
    d = only_signal(cfg, 'page_price')
    first = dict(d['checkout_price_first_reading'])
    remember(d, cfg, price=price)
    assert d['checkout_price_first_reading'] == first
    assert bool(rank.top_picks([(d, 100)], cfg.front_page, NOW, cfg=cfg)) is kept
    # A stale latest reading still vetoes a fresh repost after movement.
    if not kept:
        d['sightings'] = only_signal(cfg, 'repost')['sightings']
        later = NOW + timedelta(hours=13)
        assert checkout_prices.pick_evidence(d, cfg, later)[0] == 'skipped_page_price_moved'


@pytest.mark.parametrize('hours,kept', [(3, True), (3.001, False)])
def test_page_price_uses_pick_freshness_instead_of_label_freshness(cfg, hours, kept):
    enable(cfg)
    d = only_signal(cfg, 'page_price')
    at = NOW - timedelta(hours=hours)
    d['checkout_price_reading']['at'] = util.iso(at)
    assert checkout_prices.label(d, cfg, NOW)
    assert bool(rank.top_picks([(d, 100)], cfg.front_page, NOW, cfg=cfg)) is kept


def test_runtime_keepa_knobs_control_page_evidence(cfg):
    enable(cfg)
    d = only_signal(cfg, 'page_price')
    remember(d, cfg, price=50.75)
    assert checkout_prices.pick_evidence(d, cfg, NOW)[0] == 'skipped_page_price_moved'
    cfg.keepa_reprice_min_pct = 2
    assert rank.top_picks([(d, 100)], cfg.front_page, NOW, cfg=cfg) == [d]
    cfg.keepa_pick_max_unconfirmed_h = .5
    assert checkout_prices.pick_evidence(d, cfg, NOW + timedelta(minutes=31))[0] == 'skipped_silent'


@pytest.mark.parametrize('days,kept', [(6.999, True), (7, False), (7.001, False)])
def test_stated_end_cap_is_anchored_to_its_statement(cfg, days, kept):
    enable(cfg)
    d = only_signal(cfg, 'stated_end')
    d['expires_at'] = util.iso(NOW + timedelta(days=30))
    d['expires_published_at'] = util.iso(NOW - timedelta(days=days))
    d['last_seen_at'] = d['price_published_at'] = util.iso(NOW)
    assert bool(rank.top_picks([(d, 100)], cfg.front_page, NOW, cfg=cfg)) is kept
    cfg.front_page['checkout_price_deals']['max_stated_days'] = 8
    assert rank.top_picks([(d, 100)], cfg.front_page, NOW, cfg=cfg) == [d]


@pytest.mark.parametrize('hours,kept', [(71.999, True), (72, False), (73, False)])
def test_silence_backstop_caps_extended_observation_windows(cfg, hours, kept):
    enable(cfg)
    cfg.front_page['checkout_price_deals']['repost_window_h'] = 96
    d = only_signal(cfg, 'repost')
    d['sightings'][0]['seen_at'] = util.iso(NOW - timedelta(hours=hours))
    d['last_seen_at'] = d['price_rechecked_at'] = util.iso(NOW)
    assert bool(rank.top_picks([(d, 100)], cfg.front_page, NOW, cfg=cfg)) is kept


def test_three_day_silence_drops_only_pick_and_render_rerun_is_identical(db, cfg, tmp_path, monkeypatch):
    enable(cfg)
    cfg.support_enabled = True
    monkeypatch.setattr(site, 'util_now', lambda: NOW)
    d = only_signal(cfg, 'page_price')
    d['checkout_price_reading']['at'] = util.iso(NOW - timedelta(days=3))
    d['sightings'] = [dict(source_name='slickdeals_frontpage', source_url='https://slickdeals.net/f/old',
                          seen_at=util.iso(NOW - timedelta(days=3)), final_price=d['final_price'])]
    # The old generic confirmation clock cannot substitute for page evidence.
    d['price_rechecked_at'] = util.iso(NOW)
    out = tmp_path / 'site'
    first = site.render_site(db, cfg, NOW, out_dir=out, deals=[d])
    before = tree(out)
    second = site.render_site(db, cfg, NOW, out_dir=out, deals=[d])
    assert tree(out) == before
    assert first['checkout_price_deals'] == second['checkout_price_deals']
    assert first['checkout_price_deals']['skipped_silent'] == 1
    assert first['public_deals'][0]['status'] == 'alive'
    assert 'pick' not in first['public_deals'][0]
    assert d['item'] in (out / 'index.html').read_text()


def test_only_current_price_sightings_are_live_and_publication_touches_do_not_count(cfg):
    enable(cfg)
    d = only_signal(cfg, 'repost')
    d['sightings'][0]['final_price'] = 999
    d['last_seen_at'] = d['price_rechecked_at'] = d['price_published_at'] = util.iso(NOW)
    assert checkout_prices.pick_evidence(d, cfg, NOW)[0] == 'skipped_silent'


def test_first_reading_is_immutable_and_old_responses_cannot_replace_latest(cfg):
    enable(cfg)
    d = only_signal(cfg, 'page_price')
    first = dict(d['checkout_price_first_reading'])
    remember(d, cfg, price=60)
    latest = dict(d['checkout_price_reading'])
    remember(d, cfg, at=NOW - timedelta(hours=1), price=50, now=NOW + timedelta(hours=24))
    assert d['checkout_price_reading'] == latest  # latest is beyond the label window too
    assert d['checkout_price_first_reading'] == first
    d['final_price'] = 9
    remember(d, cfg)
    assert d['checkout_price_first_reading'] == first
    d['sightings'] = []
    assert checkout_prices.pick_evidence(d, cfg, NOW)[0] == 'skipped_silent'


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
    assert all(private not in data for private in ('checkout_price_reading', 'checkout_price_first_reading',
                                                  'checkout_source_ended_at',
                                                  'buybox_check', 'sightings', 'keepa'))
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
    assert saved['checkout_price_first_reading'] == saved['checkout_price_reading']
    assert checkout_prices.label(saved, cfg, NOW) == dict(price=50.0, kind='code')
    before = {t: db.conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
              for t in ('deals', 'sightings', 'price_history')}
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert len(calls) == 1
    assert before == {t: db.conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in before}
    assert db.get_deal(d['dedupe_key'])['checkout_price_reading'] == saved['checkout_price_reading']
    assert db.get_deal(d['dedupe_key'])['checkout_price_first_reading'] == saved['checkout_price_first_reading']


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
    (False, False, '2e8343e6f9e5e2d93018f0389d6de8adc08198d745324d2179b76147d6eb57ad'),
    (False, True, '5214705ef83a5a5fa51e7d61f6a944608563cce6e56bda50cfe76cb967c194aa'),
    (True, False, 'ca75fb9cd7d8c8da7156dab22cddd6ff23b99f0004e617376104400a0d2a8270'),
    (True, True, 'ca75fb9cd7d8c8da7156dab22cddd6ff23b99f0004e617376104400a0d2a8270'),
])
def test_off_matches_untouched_head_every_published_byte(tmp_path, monkeypatch, support, opened, expected):
    # Recaptured on untouched HEAD: its four old pins already fail there.
    # Adding private checkout facts below must preserve every published byte.
    monkeypatch.setattr(site, 'util_now', lambda: NOW)
    snapshots = []
    for mode in ('omitted', 'off'):
        config = load_config()
        config.amazon_as_of_enabled = False  # predates the Amazon as-of switch
        config.photoless_last = False  # predates photos-first
        config.seo_product_pages_enabled = False  # predates product pages
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
            d['checkout_price_reading'] = dict(asin=d['asin'], final=25., price=50., at=util.iso(NOW))
            d['checkout_price_first_reading'] = dict(d['checkout_price_reading'], price=55.)
            d['checkout_source_ended_at'] = util.iso(NOW)
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
