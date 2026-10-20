"""Task 351: final store destinations, one publication gate, offline only."""
import json
import subprocess
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from dealradar import pipeline
from dealradar.config import AppConfig, load_config, load_sources
from dealradar.db import DB
from dealradar.publish import indexnow, site, social
from dealradar.publish.link_facts import routing_policy as link_policy
from dealradar.publish.link_facts import derive_link_facts
from dealradar.util import iso
from tests.history_pins import pre_hide_deal_site_links, pre_amazon_price_cap, pre_tools_new_tab
from tests.test_e2e_offline import env, _run
from tests.test_photoless_cutoff import cfg, db, offline  # noqa: F401
from tests.test_product_entities import insert
from tests.test_publish_site import NOW, make_deal
from tests.test_pwa import tree_hash
from tests.test_seo_deal_pages import _page, _path
from tests.test_support import render, NOW as SUPPORT_NOW
from tests.test_front_page import daily_deal

TAG = 'wowpriced-20'
POST = 'https://www.dealsofamerica.com/swisswell-rash-guard-shirts/844877.htm'
PIC = 'https://images.example.invalid/product.jpg'
TRACKERS = ['cc.dealsofamerica.com', 'go.dealmoon.com', 'links.southernsavers.com']


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_boolean(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump({'publish': {'hide_deal_site_links': value}}))
    with pytest.raises(ValueError, match='publish.hide_deal_site_links must be a boolean'):
        load_config(path)


def test_defaults_shipped_history_and_permissions(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).hide_deal_site_links is AppConfig().hide_deal_site_links is False
    cfg = load_config()
    assert cfg.hide_deal_site_links is cfg.affiliate_repair_competitor_trackers is True
    for pin in (pre_hide_deal_site_links, pre_amazon_price_cap):
        previous = pin(load_config())
        assert previous.hide_deal_site_links is previous.affiliate_repair_competitor_trackers is False
    primary, backup = load_sources()
    names = {'hip2save', 'hip2save_amazon', 'hip2save_freebies', 'kcl_wall_amazon',
             'moneysavingmom', 'southernsavers', 'southernsavers_online',
             'southernsavers_amazon', 'dealmoon_us', 'dealsofamerica', 'free_stuff_finder'}
    affected = [s for s in primary + backup if s.name in names]
    assert len(affected) == len(names)
    for source in affected:
        assert 'credit by name on each deal' in source.crawl_permission
        assert '2026-10-20' in source.crawl_permission


@pytest.mark.parametrize('canon', ['', POST, 'https://cc.dealsofamerica.com/click/123',
                                  'https://hip2save.com/another-post'])
@pytest.mark.parametrize('reuse', [False, True])
def test_asin_without_store_link_uses_tagged_dp_without_io(canon, reuse):
    cfg = AppConfig(affiliate_amazon_tag=TAG, affiliate_repair_competitor_trackers=True,
                    affiliate_competitor_tracker_hosts=TRACKERS, reuse_link_facts_enabled=reuse)
    row = make_deal(canonical_url=canon, asin='B012345678', codes=['SAVE'],
                    conditions=['subscribe_save'], sightings=[{'source_name': 'dealsofamerica',
                                                              'source_url': POST}])
    before = deepcopy(row)
    links = derive_link_facts(row, link_policy(TAG, competitor_tracker_hosts=TRACKERS,
                                               repair_competitor_trackers=True)) if reuse else None
    out = site._serialize_deal(row, NOW, TAG, cfg=cfg, links=links,
                                competitor_tracker_hosts=TRACKERS)
    assert out['url'] == 'https://www.amazon.com/dp/B012345678?tag=' + TAG
    assert out['earns'] and out['needs_steps']
    assert out['credits'] == [{'name': 'Deals of America', 'url': POST}]
    assert row == before


@pytest.mark.parametrize('canon', ['https://www.walmart.com/ip/123',
                                  'https://www.amazon.com/dp/B098765432'])
def test_asin_fallback_does_not_replace_a_store_link(canon):
    row = make_deal(canonical_url=canon, asin='B012345678', codes=[])
    out = site._serialize_deal(row, NOW, TAG)
    assert out['url'].startswith(canon)


@pytest.mark.parametrize('asin', ['', 'invalid', 'B01234567/', 'B01234567?'])
def test_invalid_asin_keeps_post_fallback(asin):
    out = site._serialize_deal(make_deal(canonical_url='', asin=asin, codes=[]), NOW, TAG)
    assert out['url'] == 'https://slickdeals.net/f/1'


@pytest.mark.parametrize('reuse', [False, True])
@pytest.mark.parametrize('url', ['', POST, 'https://cc.dealsofamerica.com/click/123',
    'https://go.dealmoon.com/click/123', 'https://links.southernsavers.com/click/123',
    'https://slickdeals.net/f/123', 'https://sub.hip2save.com/post',
    'https://moneysavingmom.com/post', 'https://thekrazycouponlady.com/post',
    'https://www.freestufffinder.com/post'])
def test_final_source_destinations_hidden_on_shown_off(db, cfg, url, reuse):
    cfg.affiliate_competitor_tracker_hosts = TRACKERS
    cfg.affiliate_repair_competitor_trackers = True
    cfg.reuse_link_facts_enabled = reuse
    row = make_deal(canonical_url=url, codes=[], image_url=PIC, sightings=[
        {'source_name': 'dealsofamerica', 'source_url': POST}])
    for enabled in (False, True, False):
        cfg.hide_deal_site_links = enabled
        result = site.render_site(db, cfg, NOW, deals=[row])
        assert bool(result['public_deals']) is not enabled
        assert bool(result['full_payload']['deals']) is not enabled
        assert _page(Path(cfg.site_dir), _path(row)).exists() is not enabled
        assert result['hidden_deal_site_links'] == int(enabled)
        assert result['hidden_deal_site_links_by_credit'] == ({'Deals of America': 1} if enabled else {})


def test_any_credit_url_including_deduplicated_credit_is_a_source_post(db, cfg):
    url = 'https://independent.example/second-post'
    row = make_deal(canonical_url=url, codes=[], image_url=PIC, sightings=[
        {'source_name': 'Independent', 'source_url': 'https://independent.example/first-post'},
        {'source_name': 'Independent', 'source_url': url}])
    cfg.hide_deal_site_links = True
    result = site.render_site(db, cfg, NOW, deals=[row])
    assert result['hidden_deal_site_links'] == 1
    assert result['public_deals'] == []


def test_filter_uses_final_route_after_checkout_fallback_and_override(db, cfg):
    cfg.hide_deal_site_links = cfg.affiliate_repair_competitor_trackers = True
    cfg.affiliate_amazon_tag = TAG
    rows = [make_deal(dedupe_key='steps', canonical_url='https://walmart.com/ip/steps',
                      conditions=['in_cart'], image_url=PIC),
            make_deal(dedupe_key='override', canonical_url='https://walmart.com/ip/override',
                      codes=[], image_url=PIC),
            make_deal(dedupe_key='store', canonical_url='https://morningsave.com/product',
                      codes=[], image_url=PIC),
            make_deal(dedupe_key='asin', canonical_url='', asin='B012345678',
                      codes=[], image_url=PIC, price_rechecked_at=iso(NOW))]
    cfg.affiliate_link_overrides = {'walmart.com/override': POST}
    result = site.render_site(db, cfg, NOW, deals=rows)
    assert {d['dedupe_key'] for d in result['public_deals']} == {'store', 'asin'}
    assert result['hidden_deal_site_links'] == 2


def test_dead_handling_is_unchanged(db, cfg):
    cfg.hide_deal_site_links = True
    row = make_deal(canonical_url=POST, status='dead', death='confirmed', died_at=iso(NOW), image_url=PIC)
    result = site.render_site(db, cfg, NOW, deals=[row])
    assert result['hidden_deal_site_links'] == 0
    assert result['public_deals'][0]['gone']
    assert _page(Path(cfg.site_dir), _path(row)).exists()


def test_price_only_spotlight_uses_the_same_filter(db, cfg):
    cfg.front_page = load_config().front_page
    row = daily_deal(canonical_url=POST, codes=[], pct_at_checkout=None,
                     regular_price=None, abs_savings=None, image_url=PIC)
    result = site.render_site(db, cfg, NOW, deals=[row])
    assert result['public_deals'] and result['full_payload']['spotlight']
    cfg.hide_deal_site_links = True
    result = site.render_site(db, cfg, NOW, deals=[row])
    assert result['hidden_deal_site_links'] == 1
    assert not result['public_deals']
    assert not result['full_payload'].get('spotlight')
    assert row['item'] not in (Path(cfg.site_dir) / 'index.html').read_text()


def test_summary_does_not_count_rows_already_hidden_by_photos(db, cfg):
    cfg.hide_deal_site_links = True
    row = make_deal(canonical_url=POST, image_url='', first_seen_at=iso(NOW - timedelta(days=1)))
    result = site.render_site(db, cfg, NOW, deals=[row])
    assert result['unpublished_photoless'] == 1
    assert result['hidden_deal_site_links'] == 0


@pytest.mark.parametrize('posted', [False, True])
def test_removal_every_surface_store_recovery_and_idempotency(db, cfg, posted):
    out = Path(cfg.site_dir)
    cfg.front_page = load_config().front_page
    cfg.front_page['appeal'] = {}
    cfg.seo_posted_deal_redirects_enabled = True
    cfg.social_require_earning_link = cfg.social_require_stock_evidence = False
    hidden = insert(db, 'task351-hidden', now=NOW, item='Unique source kitchen fixture',
                    category='kitchen', canonical_url=POST, image_url=PIC, price_rechecked_at=iso(NOW),
                    sightings=[{'source_name': 'dealsofamerica', 'source_url': POST}])
    visible = insert(db, 'task351-store', now=NOW, item='Visible kitchen fixture', category='kitchen',
                     canonical_url='https://walmart.com/ip/351', image_url=PIC, price_rechecked_at=iso(NOW))
    site.render_site(db, cfg, NOW)
    path, url = _path(hidden), 'https://wowpriced.com' + _path(hidden)
    assert _page(out, path).exists()
    assert hidden['dedupe_key'] in {d['dedupe_key'] for d, _ in social._candidates(db, cfg, {})}
    if posted:
        db.record_alert(deal_key=hidden['dedupe_key'], channel='telegram',
                        dedup_token='task351-hidden|telegram', now=NOW)
    rows_before = [tuple(row) for row in db.conn.execute('SELECT * FROM deals ORDER BY id')]
    cfg.hide_deal_site_links = True
    result = site.render_site(db, cfg, NOW)
    assert result['hidden_deal_site_links'] == 1
    assert {d['dedupe_key'] for d in result['public_deals']} == {visible['dedupe_key']}
    assert not _page(out, path).exists()
    assert db.indexnow_pages()[url]['removed'] == 1
    assert json.loads(indexnow.manifest_path(out).read_text())['versions'][url] == 'removed'
    for file in out.rglob('*'):
        if file.is_file():
            assert hidden['item'] not in file.read_text()
    assert not any(url in p.read_text() for p in out.glob('sitemap*.xml'))
    assert [d['dedupe_key'] for d, _ in social._candidates(db, cfg, {})] == [visible['dedupe_key']]
    assert [tuple(row) for row in db.conn.execute('SELECT * FROM deals ORDER BY id')] == rows_before
    files = {p.relative_to(out): p.read_bytes() for p in out.rglob('*') if p.is_file()}
    state = list(db.conn.iterdump())
    site.render_site(db, cfg, NOW)
    assert {p.relative_to(out): p.read_bytes() for p in out.rglob('*') if p.is_file()} == files
    assert list(db.conn.iterdump()) == state
    target = 'https://walmart.com/ip/repaired-351'
    db.update_deal(hidden['dedupe_key'], payload=dict(db.get_deal(hidden['dedupe_key']), canonical_url=target),
                   canonical_url=target)
    result = site.render_site(db, cfg, NOW)
    assert result['hidden_deal_site_links'] == 0
    assert _page(out, path).exists()
    assert db.indexnow_pages()[url]['removed'] == 0
    assert any(url in p.read_text() for p in out.glob('sitemap*.xml'))


def baseline_rows():
    return [make_deal(dedupe_key='post-only', item='Post only kitchen fixture', codes=[], canonical_url='',
                      image_url='https://img.example/a.jpg', price_rechecked_at=SUPPORT_NOW.isoformat()),
            make_deal(dedupe_key='repaired', item='Repaired kitchen fixture', codes=[],
                      canonical_url='https://www.walmart.com/ip/123',
                      link_as_posted='https://cc.dealsofamerica.com/click/123',
                      image_url='https://img.example/a.jpg', price_rechecked_at=SUPPORT_NOW.isoformat())]


@pytest.mark.parametrize('mode,expected', [
    ('shipped', 'fc1042a5bd2e3d63b75b4bd82b87ffa39eac319064682aa8ec9dcd2874e8d5c0'),
    ('inline', 'f16255b6b2d66de7928ec3612c20058c75a9e67f4796c64f53488d2301371100'),
    ('membership', 'afcbfa9752bd1ac7b4a0cce01464afc35f74f9918c4aa1cbebe42e20aaf6ddb0')])
def test_off_keeps_pre_edit_whole_publication_bytes(tmp_path, monkeypatch, mode, expected):
    monkeypatch.setattr(site, 'util_now', lambda: SUPPORT_NOW)
    # Recorded at e733b00, before this switch and the same day's tool-page switches.
    cfg = pre_tools_new_tab(load_config())
    if mode == 'inline':
        cfg.search_analyze['lazy'] = False
    if mode == 'membership':
        cfg.support_enabled = cfg.paywall_open_access = False
    assert tree_hash(render(tmp_path, cfg, baseline_rows())[0]) == expected


def test_production_repair_does_not_restore_trackers(db):
    cfg = load_config()
    for i, host in enumerate(TRACKERS):
        pipeline_key = f'repaired-{i}'
        insert(db, pipeline_key, canonical_url='https://amazon.com/dp/B012345678',
               link_as_posted=f'https://{host}/click/123', codes=[])
        assert site.keeps_source_tracker(f'https://{host}/click/123', cfg) is False
    before = list(db.conn.iterdump())
    assert pipeline._restore_source_credit_links(db, cfg) == 0
    assert list(db.conn.iterdump()) == before


def test_hourly_check_uses_the_same_host_policy():
    script = """const fs=require('fs'), logic=require('./scripts/site_check_logic.cjs');
    process.stdout.write(JSON.stringify(logic.dealSourceHosts(
      fs.readFileSync('config.yaml','utf8'), fs.readFileSync('sources.yaml','utf8'))));"""
    actual = json.loads(subprocess.check_output(['node', '-e', script], text=True,
                                                cwd=Path(__file__).resolve().parents[1]))
    expected = site._deal_source_hosts() | set(load_config().affiliate_competitor_tracker_hosts)
    assert set(actual) == expected


def test_pipeline_summary_is_private_and_counts_hidden(env):
    db = DB(env['db'])
    try:
        insert(db, 'task351-pipeline', now=NOW, item='Pipeline source fixture', canonical_url=POST,
               category='kitchen', image_url=PIC, price_rechecked_at=iso(NOW),
               flags=['post_link_tried', 'link_resolve_tried'],
               critic={'plausible': True, 'confidence': .9, 'reason': 'Fixture basis'},
               sightings=[{'source_name': 'dealsofamerica', 'source_url': POST}])
        db.meta_set('recategorized_v2', json.dumps({'keys': ['task351-pipeline']}))
    finally:
        db.close()
    first, second = _run(env), _run(env)
    assert first['hidden_deal_site_links'] == second['hidden_deal_site_links']
    assert first['hidden_deal_site_links'] >= 1
    assert first['hidden_deal_site_links_by_credit']['Deals of America'] == 1
    assert 'hidden_deal_site_links' not in (env['site'] / 'deals.json').read_text()


def test_store_sources_keep_their_own_product_pages(tmp_path, monkeypatch):
    # Woot's own feed credits the product page itself: never a deal-site post.
    monkeypatch.setattr(site, 'util_now', lambda: SUPPORT_NOW)
    url = 'https://www.woot.com/offers/fixture-air-fryer'
    row = make_deal(dedupe_key='woot', item='Woot fixture air fryer', store='Woot', codes=[],
                    canonical_url=url, image_url=PIC, price_rechecked_at=SUPPORT_NOW.isoformat(),
                    sightings=[{'source_name': 'Woot', 'source_url': url,
                                'seen_at': SUPPORT_NOW.isoformat(), 'final_price': 20.0}])
    out = render(tmp_path, load_config(), [row])[0]
    deals = json.loads((out / 'deals.json').read_text())['deals']
    assert [d['url'].split('?')[0] for d in deals] == [url]


def test_retailer_source_list_matches_credits():
    import inspect
    from dealradar.publish import credits, link_facts
    source = inspect.getsource(credits._deal_source_hosts)
    assert all(f'"{host}"' in source for host in link_facts.RETAILER_SOURCE_HOSTS)
