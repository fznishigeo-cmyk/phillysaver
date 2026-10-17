"""Task 276: public List Price facts, private qualification, offline only."""

from copy import deepcopy
from datetime import timedelta
from html import unescape
import json
import re

import pytest

from dealradar import amazon_reference, dedupe, pipeline, rank, savings, util, verify
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.fetch.keepa import KeepaDealsAdapter, _FeedConfirmations
from dealradar.parse.extractor import verify_evidence
from dealradar.parse.schema import LLMDeal, to_extracted
from dealradar.publish import meta, pinterest, seo, site, telegram_pub
from dealradar.publish.email_policy import email_deal
from tests.test_publish_site import make_deal, NOW
from tests.test_seo_deal_pages import _path
from tests.test_keepa import SRC
from tests.test_price_accuracy_engine import api, bucket, product, seed, cfg, db, ASIN, NOW as PAID_NOW

BANNED = re.compile(r'90-day|average|usual|lowest|highest|history', re.I)


def offer(listed=25, **changes):
    d = make_deal(dedupe_key='url|https://www.amazon.com/dp/B012345678',
        asin='B012345678', item='Kitchen Widget', brand='Acme', store='Amazon',
        category='kitchen', canonical_url='https://www.amazon.com/dp/B012345678',
        regular_price=50, final_price=10, pct_at_checkout=80, abs_savings=40,
        regular_price_basis='90-day average on Amazon', verify_tier='history-checked',
        conditions=[], codes=[], price_rechecked_at=util.iso(NOW),
        keepa_list_price={'asin': 'B012345678', 'price': listed, 'observed_at': util.iso(NOW)},
        sightings=[{'source_name': 'keepa', 'source_url': 'https://keepa.com/#!product/1-B012345678',
                    'seen_at': util.iso(NOW), 'final_price': 10}],
        image_url='https://m.media-amazon.com/images/I/widget.jpg')
    d.update(changes)
    return d


def settings():
    return AppConfig(amazon_reference='list_price', custom_domain='wowpriced.com',
                     front_page=load_config().front_page,
                     paywall_enabled=False, seo_hub_min_items=1, seo_store_page_min_deals=1)


def visible(html):
    html = re.sub(r'<(script|style)\b.*?</\1>', '', html, flags=re.S | re.I)
    return unescape(re.sub(r'<[^>]+>', ' ', html))


def test_switch_ships_list_price_and_rejects_invalid_values(tmp_path):
    assert AppConfig().amazon_reference == 'history'
    assert load_config().amazon_reference == 'list_price'
    path = tmp_path / 'config.yaml'
    for value in ['history', 'list_price', '', True, None, [], {}]:
        path.write_text(json.dumps({'publish': {'amazon_reference': value}}))
        if value in ('history', 'list_price'):
            assert load_config(path).amazon_reference == value
        else:
            with pytest.raises(ValueError, match='publish.amazon_reference'):
                load_config(path)


@pytest.mark.parametrize('mode', ['history', 'list_price'])
@pytest.mark.parametrize('field', ['avg90', 'avg30', 'avg'])
@pytest.mark.parametrize('final,expected', [(10, True), (40, False)])
def test_cross_check_logs_keep_hidden_comparisons_private(monkeypatch, caplog, mode, field, final, expected):
    cfg = settings()
    cfg.amazon_reference = mode
    cfg.feature_flags['keepa'] = True
    monkeypatch.setenv('KEEPA_KEY', 'offline-key')
    monkeypatch.setattr(verify, '_keepa_product', lambda *args, **kwargs: {'stats': {field: [5000]}})
    with caplog.at_level('INFO', logger='dealradar.verify'):
        assert verify.keepa_check(offer(final_price=final), cfg) is expected
    if mode == 'list_price':
        assert not re.search(r'avg|average|history|50\.00', caplog.text, re.I)
        assert all(50 not in record.args for record in caplog.records)
    else:
        assert f'keepa check B012345678: final=${final:.2f} avg90=$50.00 -> {expected}' in caplog.text


@pytest.mark.parametrize('listed,expected', [(10.5, 4.8), (None, None)])
def test_shipping_category_copy_does_not_advertise_hidden_discount_floors(tmp_path, monkeypatch, listed, expected):
    monkeypatch.setattr(site, 'util_now', lambda: NOW)
    cfg = load_config()
    cfg.seo_hub_min_items = 1
    database = DB(':memory:')
    try:
        row = offer(listed)
        row['click_price_check'] = dict(asin=row['asin'], final=row['final_price'],
                                       at=util.iso(NOW), buybox=row['final_price'])
        result = site.render_site(database, cfg, NOW, out_dir=tmp_path, deals=[row])
        assert result['public_deals'][0]['pct_at_checkout'] == expected
        hub = (tmp_path / 'deals/home-kitchen/index.html').read_text()
        assert 'Kitchen Widget' in hub
        assert ('List price <s>$10.50</s>' if listed else 'Price drop') in hub
        for _label, _group, slug in seo.HUB_GROUPS:
            html = (tmp_path / 'deals' / slug / 'index.html').read_text()
            description = re.search(r'<meta name="description" content="([^"]*)">', html)[1]
            intro = re.search(r'<p class="lede">(.*?)</p>', html, re.S)[1]
            assert '%' not in description + intro, slug
            assert '{site_floor}' not in description + intro and '{used_floor}' not in description + intro
        # The old copy still follows both configured floors when the mode is OFF.
        cfg.amazon_reference = 'history'
        cfg.min_pct = cfg.site_show_from_pct = 73
        cfg.used_min_pct = 81
        copy = seo._hub_copy(cfg)
        for slug, entry in copy.items():
            floor = 81 if slug == 'used-open-box' else 73
            assert f'{floor}% off or more' in entry['description']
            assert f'at least {floor}% off' in entry['intro']
    finally:
        database.close()


def test_current_shipping_publication_keeps_phone_budgets(tmp_path):
    from scripts.publication_budget import analyze
    from tests.phone_budget_fixture import publish_fixture

    cfg = load_config()
    cfg.phone_budget['enabled'] = True
    def inspect(database, out):
        result = analyze(out, cfg, expected_offers=1393)  # task 283: seven older photo-less offers
        assert result['ok'], result['metrics']
    publish_fixture(tmp_path, cfg, inspect=inspect)


@pytest.mark.parametrize('listed,expected', [(25, 60), (10, None), (9, None), (None, None)])
def test_card_detail_and_json_use_only_display_reference(tmp_path, monkeypatch, listed, expected):
    monkeypatch.setattr(site, 'util_now', lambda: NOW)
    raw, cfg = offer(listed), settings()
    before = deepcopy(raw)
    database = DB(':memory:')
    try:
        result = site.render_site(database, cfg, NOW, out_dir=tmp_path, deals=[raw])
        public = result['public_deals'][0]
        assert public['pct_at_checkout'] == expected
        assert public['badge'] == 'verified'
        assert public['ps'] == 1  # Public ordering never exports the private score magnitude.
        assert rank.pick_score(raw, cfg, NOW, require_confirmation=False) is not None
        assert public['primary_offer'] is True
        assert public['regular_price'] == (25 if expected is not None else None)
        assert public['abs_savings'] == (15 if expected is not None else None)
        assert not BANNED.search(json.dumps(public))
        card = seo.card_html(public, now=NOW)
        page = (tmp_path / _path(raw).strip('/') / 'index.html').read_text()
        if expected is None:
            assert 'Price drop' in card and 'Price drop' in page
            assert '<s>' not in card and '% off' not in page and 'Regular price:' not in page
            assert result['amazon_reference'] == {'list_price': 0, 'price_drop': 1}
        else:
            assert 'List price <s>$25.00</s>' in card
            assert '<p>List price $25.00</p>' in page
            assert '60% off' in page and 'save $15.00' in card
            assert result['amazon_reference'] == {'list_price': 1, 'price_drop': 0}
        for path in tmp_path.rglob('*.html'):
            html = path.read_text()
            assert not BANNED.search(visible(html)), path
            for data in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
                assert not BANNED.search(data), path
            for attr in re.findall(r'(?:alt|title|content|aria-label)="([^"]*)"', html):
                assert not BANNED.search(attr), (path, attr)
        assert raw == before
        assert not BANNED.search((tmp_path / 'deals.json').read_text())
        assert 'Price drop' in (tmp_path / 'index.html').read_text()
    finally:
        database.close()


@pytest.mark.parametrize('changes', [
    {'keepa_list_price': None},
    {'keepa_list_price': {'asin': 'B012345678', 'price': 25, 'observed_at': util.iso(NOW - timedelta(minutes=1))}},
    {'price_since': util.iso(NOW + timedelta(minutes=1))},
    {'price_rechecked_at': util.iso(NOW - timedelta(hours=13)), 'keepa_list_price': {
        'asin': 'B012345678', 'price': 25, 'observed_at': util.iso(NOW - timedelta(hours=13))}},
])
def test_missing_stale_or_other_price_reading_becomes_chip(changes):
    public = site._serialize_deal(offer(**changes), NOW, cfg=settings())
    assert public['price_drop'] and public['pct_at_checkout'] is None
    assert public['regular_price'] is public['abs_savings'] is None


def test_source_stated_pairs_keep_facts_and_attribution():
    cfg = settings()
    raw = offer(regular_price_basis='', sightings=make_deal()['sightings'])
    public = site._serialize_deal(raw, NOW, cfg=cfg)
    assert public['regular_price'] == 50 and public['pct_at_checkout'] == 80
    assert seo._basis_label(public) == 'as stated by Slickdeals'
    assert 'usual' not in public['headline']
    assert 'as stated by Slickdeals' in seo._deal_page(
        '{{DEALS}}', public, '/deal/widget/', 'https://wowpriced.com', '', [], {}, [], NOW)


def test_all_stores_hide_history_and_ended_pages_reproject(tmp_path, monkeypatch):
    monkeypatch.setattr(site, 'util_now', lambda: NOW)
    cfg = settings()
    cfg.seo_product_pages_enabled = True
    cfg.product_entities_lowest_seen_enabled = True
    cfg.checker['enabled'] = True
    non_amazon = make_deal(verify_tier='history-checked', codes=[], conditions=[])
    database = DB(':memory:')
    try:
        result = site.render_site(database, cfg, NOW, out_dir=tmp_path, deals=[offer(), non_amazon])
        assert not list((tmp_path / 'p').rglob('*.html'))
        for d in result['public_deals']:
            assert d['badge'] == 'verified' and not BANNED.search(json.dumps(d))
        for path in tmp_path.rglob('*.html'):
            assert not BANNED.search(visible(path.read_text())), path
    finally:
        database.close()


def test_projected_and_raw_social_email_formatters_do_not_leak(monkeypatch):
    monkeypatch.setattr(util, 'now_utc', lambda: NOW)
    cfg = settings()
    cfg.meta = load_config().meta
    for listed in (25, None):
        raw = offer(listed)
        public = site._serialize_deal(raw, NOW, cfg=cfg)
        for d in (raw, public):
            messages = [telegram_pub.format_message(d, '/deal/widget/', cfg=cfg),
                        telegram_pub.format_message(d, cfg=cfg),
                        meta.caption(d, '/deal/widget/', cfg, 'facebook'),
                        meta.caption(d, '/deal/widget/', cfg, 'instagram'),
                        json.dumps(pinterest.pin_payload(d, '/deal/widget/', 'board', '#ad')),
                        json.dumps(email_deal(d, cfg))]
            for message in messages:
                assert not BANNED.search(message), message
                assert '80%' not in message and '$50' not in message
            if listed is None:
                assert all('%' not in message for message in messages)
            else:
                assert '60% OFF' in messages[0]


def test_readings_capture_current_list_and_csv_without_guessing():
    for record in ({'asin': 'B012345678', 'current': [1000, -1, -1, -1, 2500]},
                   {'asin': 'B012345678', 'stats': {'current': [1000, -1, -1, -1, 2500]}},
                   {'asin': 'B012345678', 'csv': [None] * 4 + [[1, 3000, 2, 2500]]}):
        assert amazon_reference.list_reading(record, NOW) == offer()['keepa_list_price']
    record['stats'] = {'current': [1000, -1, -1, -1, -1]}
    assert amazon_reference.list_reading(record, NOW)['price'] is None


def test_feed_ingest_keeps_display_reference_independent_of_backend(db, cfg):
    from tests.test_free_confirmations import row
    reading = row(cents=2500, at=PAID_NOW)
    reading['current'][4] = 50000  # Too high for the old backend basis; still Amazon's List Price.
    post = KeepaDealsAdapter._map_row(reading, SRC, PAID_NOW)
    extracted = to_extracted(LLMDeal.model_validate(post.structured), post, 'UTC', PAID_NOW)
    assert verify_evidence(extracted, post.text)[0]
    dedupe.upsert_extracted(db, extracted, savings.compute_savings(extracted), post, cfg, PAID_NOW)
    d = db.all_deals()[0]
    assert d['regular_price'] == 100 and d['pct_at_checkout'] == 75
    assert d['keepa_list_price']['price'] == 500
    reading['current'][4] = -1
    _FeedConfirmations(db, PAID_NOW, cfg).note([reading], used=False)
    assert db.get_deal(d['dedupe_key'])['keepa_list_price']['price'] is None


def test_paid_confirmation_refreshes_list_without_new_calls(db, cfg, monkeypatch):
    deal = seed(db, cfg, regular_price_basis='90-day average on Amazon')
    reading = product(2500)
    reading['stats']['current'] = [2500, -1, -1, -1, 4000]
    calls = api(monkeypatch, {ASIN: reading})
    bucket(db, 500)
    pipeline._recheck_prices(db, cfg, PAID_NOW, image_backfill=False)
    stored = db.get_deal(deal['dedupe_key'])
    assert len(calls) == 1 and stored['keepa_list_price']['price'] == 40
    assert stored['keepa_list_price']['observed_at'] == util.iso(verify.price_confirmed_at(stored))


def test_same_render_is_idempotent_without_backend_price_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(site, 'util_now', lambda: NOW)
    database, cfg = DB(':memory:'), settings()
    rows = [offer(), offer(None, dedupe_key='chip')]
    try:
        first = site.render_site(database, cfg, NOW, out_dir=tmp_path, deals=rows)
        files = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        state = list(database.conn.iterdump())
        second = site.render_site(database, cfg, NOW, out_dir=tmp_path, deals=rows)
        assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == files
        assert list(database.conn.iterdump()) == state
        assert first['public_deals'] == second['public_deals']
    finally:
        database.close()


def test_emitted_browser_cards_and_floor_split(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path
    monkeypatch.setattr(site, 'util_now', lambda: NOW)
    database = DB(':memory:')
    try:
        site.render_site(database, settings(), NOW, out_dir=tmp_path,
                         deals=[offer(), offer(None, dedupe_key='chip')])
        result = subprocess.run(['node', str(Path(__file__).with_name('amazon_reference_logic.cjs')),
                                 str(tmp_path)], text=True, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert 'hidden tier: passed' in result.stdout
    finally:
        database.close()


@pytest.mark.parametrize('floor,primary', [(70, True), (80, False)])
def test_hidden_percentage_still_clears_site_floor_and_keeps_pick_score(db, cfg, tmp_path, monkeypatch, floor, primary):
    monkeypatch.setattr(site, 'util_now', lambda: PAID_NOW)
    cfg.min_pct, cfg.site_show_from_pct = floor, 40
    deal = seed(db, cfg, regular_price_basis='90-day average on Amazon',
                keepa_list_price=None, price_rechecked_at=util.iso(PAID_NOW))
    old_cfg = deepcopy(cfg)
    old_cfg.amazon_reference = 'history'
    assert [d['dedupe_key'] for d in rank.site_deals(db, cfg, PAID_NOW)] == [deal['dedupe_key']]
    assert rank.pick_score(deal, cfg, PAID_NOW) == rank.pick_score(deal, old_cfg, PAID_NOW)
    result = site.render_site(db, cfg, PAID_NOW, out_dir=tmp_path)
    public = result['public_deals'][0]
    assert public['price_drop'] and public['pct_at_checkout'] is None
    assert public['primary_offer'] is primary
    assert public.get('pick') == 1
    assert db.get_deal(deal['dedupe_key'])['pct_at_checkout'] == 75


def test_legacy_keepa_url_also_hides_unlabelled_average():
    raw = offer(None, regular_price_basis='', sightings=[{
        'source_name': 'historical', 'source_url': 'https://keepa.com/#!product/1-B012345678'}])
    public = site._serialize_deal(raw, NOW, cfg=settings())
    assert public['price_drop'] and public['regular_price'] is None
    assert not BANNED.search(json.dumps(public))


def test_list_fact_still_passes_unchanged_evidence_gate(monkeypatch):
    checked = []
    def reject(fact, text):
        checked.append((fact, text))
        return False, 'fixture rejects evidence'
    monkeypatch.setattr('dealradar.parse.extractor.verify_evidence', reject)
    reading = amazon_reference.list_reading({'asin': 'B012345678', 'current': [1000, -1, -1, -1, 2500]}, NOW)
    assert reading['price'] is None and len(checked) == 1
    assert checked[0][0].evidence.regular_price_quote == checked[0][1]


def test_all_email_subjects_and_bodies_use_displayed_facts(monkeypatch):
    from dealradar.publish.email_digest import build_digest
    from dealradar.publish.members import build_member_email
    monkeypatch.setattr(util, 'now_utc', lambda: NOW)
    raw = make_deal(regular_price_basis='90-day average', verify_tier='history-checked')
    for message in (*build_digest([raw], settings(), NOW), *build_member_email([raw], settings())):
        assert not BANNED.search(message) and '%' not in message and '$50' not in message


@pytest.mark.parametrize('basis,expected,label', [('', 80, 'as stated by Slickdeals'),
                                               ('90-day average on Amazon', 60, 'List price')])
def test_later_source_pair_and_api_pair_keep_their_own_basis(basis, expected, label):
    raw = offer(regular_price_basis=basis)
    raw['sightings'].append({'source_name': 'slickdeals_frontpage',
                            'source_url': 'https://slickdeals.net/f/new',
                            'seen_at': util.iso(NOW), 'final_price': 10})
    before = deepcopy(raw)
    public = site._serialize_deal(raw, NOW, cfg=settings())
    assert public['pct_at_checkout'] == expected
    assert public['regular_price_basis'] == label
    assert public['regular_price'] == (50 if not basis else 25)
    assert not BANNED.search(json.dumps(public))
    assert raw == before


@pytest.mark.parametrize('basis,expected', [('lowest new price (current offer)', 80),
                                          ('lowest new price (90-day average)', None)])
def test_used_comparison_only_keeps_contemporaneous_new_price(basis, expected):
    public = site._serialize_deal(offer(conditions=['used'], regular_price_basis=basis), NOW, cfg=settings())
    assert public['pct_at_checkout'] == expected
    assert not BANNED.search(json.dumps(public))
    if expected is not None:
        assert public['regular_price_basis'] == 'new price on Amazon'
