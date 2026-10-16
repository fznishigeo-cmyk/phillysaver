"""Photos-first and strict offline photo recovery, without new price evidence."""
from datetime import timedelta
import json
from pathlib import Path
import subprocess
from urllib.parse import quote

import httpx
import pytest

from dealradar import dedupe, photo_recovery as photos, pipeline, verify
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.errors import DeadlineExceeded
from dealradar.fetch.woot import _backfill_feed_photos
from dealradar.http_client import PoliteFetcher
from dealradar.keepa_budget import KeepaBudget
from dealradar.models import RawPost, ExtractedDeal
from dealradar.publish import site
from tests.test_amazon_as_of import NOW, config, render, digest, tree
from tests.test_e2e_offline import env, test_rerun_is_idempotent as check_rerun
from tests.test_dedupe import make_savings
from tests.test_publish_site import make_deal

A, B = 'B012345678', 'B087654321'
URL = 'https://www.amazon.com/dp/' + A
PIC = 'https://m.media-amazon.com/images/I/product.jpg'


def seed(db, key='a', **changes):
    row = make_deal(dedupe_key=key, store='Amazon', item='Acme Steel Kitchen Kettle',
                    brand='Acme', canonical_url='', asin='', codes=[], conditions=[],
                    flags=['post_link_tried'])
    row.update(changes)
    row.setdefault('image_url','')
    db.insert_deal(dedupe_key=key, store=row['store'], item=row['item'],
                   normalized_item=row['item'].lower(), canonical_url=row['canonical_url'],
                   payload=row, final_price=row['final_price'], pct_at_checkout=row['pct_at_checkout'],
                   abs_savings=row['abs_savings'], online_only="true", expires_at="", now=NOW)
    return db.get_deal(key)


def product(title='Acme Steel Kitchen Kettle', price=10000):
    return {'asin': A, 'title': title, 'imagesCSV': 'product.jpg',
            'stats': {'current': [price, -1, -1, -1, -1]}}


def test_config(tmp_path):
    assert not AppConfig().photoless_last
    assert load_config().photoless_last
    cfg = load_config()
    assert (cfg.images_keepa_search_per_run, cfg.images_keepa_search_per_day, cfg.images_keepa_match_min) == (10, 120, .85)
    path = tmp_path/'config.yaml'
    path.write_text('{}')
    assert not load_config(path).photoless_last
    path.write_text('images: {keepa_match_min: 0.84}')
    with pytest.raises(ValueError):
        load_config(path)


@pytest.mark.parametrize('mode,expected', [
    ('membership','fa520d3ca27d72ddd80f9705fdcc5599632bf13adc4efba210c0bad18ab33bc8'),
    ('open','eb25fa154d063471084bfe49fda095da3fc1af4b667f1f784ef8818a8f047291'),
    ('support','4f435a02d8b0fdd9a6c6dd239df486c4359e315788b90940008e3cecd4b464a1'),
    ('shipped','825f14089ee69311896502140d24f15c5df45e39873bd62f043785d5a937ee74'),
])
def test_off_all_bytes_match_untouched_head(tmp_path, monkeypatch, mode, expected):
    monkeypatch.setattr(site, 'util_now', lambda: NOW)
    cfg = config(mode)
    cfg.amazon_history = 'show'  # predates task 121b
    cfg.front_page.get('checkout_price_deals', {})['enabled'] = False  # predates task 121b
    cfg.photoless_last = False
    cfg.seo_event_pages_enabled = False  # predates event pages
    render(tmp_path/'site', cfg)
    assert digest(tree(tmp_path/'site')) == expected


def test_node_order_and_version(tmp_path, monkeypatch):
    monkeypatch.setattr(site, 'util_now', lambda: NOW)
    cfg = config('shipped')
    cfg.photoless_last = False
    render(tmp_path/'off', cfg)
    cfg.photoless_last = True
    render(tmp_path/'on', cfg)
    assert json.loads((tmp_path/'off/deals.json').read_text())['meta']['site_version'] != json.loads((tmp_path/'on/deals.json').read_text())['meta']['site_version']
    subprocess.run(['node', str(Path(__file__).with_name('photos_every_deal.cjs')), str(tmp_path/'on/index.html')], check=True, capture_output=True, text=True)


@pytest.mark.parametrize('host,param', [('slickdeals.net/click','u2'),('redirect.viglink.com','u'),
    ('sovrn.co','url'),('redirectingat.com','url'),('go.skimresources.com','url'),
    ('howl.link','url'),('click.linksynergy.com','murl'),('cc.dealsofamerica.com','url')])
def test_all_unwrappers(host, param):
    link = f'https://{host}?{param}=' + quote(URL, safe='')
    assert dedupe.asin_from_links([link]) == A
    assert dedupe._asin_from_text('buy ' + link) == A
    assert dedupe.asin_from_links(['https://go.example?url='+quote(link, safe='')]) == A
    assert not dedupe.asin_from_links([link, 'https://amazon.com/dp/'+B])


@pytest.mark.parametrize('store', ['Amazon', 'amazon.com', 'Amazon Warehouse'])
def test_existing_asin_backfill_once_and_cap(store):
    db = DB(':memory:')
    cfg = AppConfig(images_asin_backfill_per_run=1)
    seed(db, 'a', store=store, link_as_posted='https://slickdeals.net/click?u2='+quote(URL, safe=''))
    seed(db, 'b', canonical_url=URL)
    fetcher = type('Fetcher', (), {})()
    assert photos.backfill_asins(db, db.all_deals(), fetcher, cfg, None) == dict(tried=1, found=1)
    assert db.get_deal('a')['canonical_url'] == ''  # credit and click target stay intact
    assert db.get_deal('a')['asin'] == A
    assert photos.backfill_asins(db, db.all_deals(), fetcher, cfg, None) == dict(tried=1, found=1)
    assert photos.backfill_asins(db, db.all_deals(), fetcher, cfg, None) == dict(tried=0, found=0)
    db.close()


@pytest.mark.parametrize('blocked,status', [(False,302),(False,405),(True,302)])
def test_short_links_real_polite_head_only(blocked, status):
    db = DB(':memory:')
    cfg = AppConfig(images_short_links_per_run=1)
    calls=[]
    def handler(req):
        calls.append((req.method, req.url.host, req.url.path))
        if req.url.path == '/robots.txt':
            return httpx.Response(200, text='User-agent: *\n'+('Disallow: /' if blocked else 'Allow: /'))
        return httpx.Response(status, headers={'Location': URL})
    fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handler), sleep_fn=lambda _:None)
    cache={}
    links=['https://amzn.to/one','https://a.co/two']
    targets=photos.short_targets(links, fetcher, cfg, None, cache)
    assert bool(dedupe.asin_from_links(targets)) == (not blocked and status == 302)
    assert len(cache) == 1
    before=list(calls)
    photos.short_targets(links, fetcher, cfg, None, cache)
    assert calls == before
    assert all(method == 'HEAD' for method,host,path in calls if path != '/robots.txt')
    assert all(host != 'www.amazon.com' for _,host,_ in calls)
    fetcher.close();db.close()


def test_parse_time_short_links_and_tracker_credit(monkeypatch):
    cfg=AppConfig()
    post=RawPost('source','id','https://source.example/post','Title',
                 'Price $20 https://amzn.to/a',NOW,NOW)
    extracted=ExtractedDeal(source_url=post.url,source_name=post.source_name,store='Amazon Warehouse', item='Kettle', product_url='https://sovrn.co/credit')
    calls=[]
    def follow(fetcher,url,hops,**kw):
        calls.append(kw)
        return URL
    monkeypatch.setattr(photos,'resolve_redirects',follow)
    photos.resolve_posts([(post,extracted)],type('Fetcher',(),{})(),cfg,None)
    assert dedupe.asin_from_links(post.resolved_links) == A
    assert calls == [{'deadline':None,'head_only':True}]
    assert extracted.product_url == 'https://sovrn.co/credit'
    assert post.text == 'Price $20 https://amzn.to/a'


@pytest.mark.parametrize('products', [[], ['Kitchen Kettle'], ['Kitchen Kettle', 'KITCHEN KETTLE'],
                                    ['Kitchen Kettle', 'Wireless Headphones']])
@pytest.mark.parametrize('where', ['text', 'photos', 'resolved'])
def test_post_wide_asin_requires_at_most_one_parsed_product(products, where):
    db = DB(':memory:')
    post = RawPost('source', 'id', 'https://source.example/post', 'Deals', '', NOW, NOW,
                   parsed=[(item, '') for item in products])
    tracker = 'https://sovrn.co?url=' + quote(URL, safe='')
    if where == 'text':
        post.text = tracker
    elif where == 'photos':
        post.photos.links.add(tracker)
    else:
        post.resolved_links = [URL]
    items = products or ['Kitchen Kettle']
    for item in items:
        extracted = ExtractedDeal(source_url=post.url, source_name=post.source_name,
                                  store='Amazon', item=item)
        dedupe.upsert_extracted(db, extracted, make_savings(), post, AppConfig(), NOW)
    rows = db.all_deals()
    single = len(set(item.lower() for item in items)) == 1
    assert len(rows) == (1 if single else 2)
    assert all(row['asin'] == (A if single else '') for row in rows)
    assert all(row['canonical_url'] == (URL if single else '') for row in rows)
    db.close()


@pytest.mark.parametrize('cutoff', ['cap', 'deadline', 'raised_deadline', 'unresolved'])
def test_partial_post_short_links_are_never_used(monkeypatch, cutoff):
    cfg = AppConfig(images_short_links_per_run=1 if cutoff == 'cap' else 10)
    links = ['https://amzn.to/a', 'https://amzn.to/b', 'https://a.co/c']
    post = RawPost('source', 'id', 'https://source.example/post', 'Deals',
                   ' '.join(links), NOW, NOW,
                   parsed=[('Kitchen Kettle', links[0]), ('Wireless Headphones', links[1])])
    pairs = [(post, ExtractedDeal(source_url=post.url, source_name=post.source_name,
                                  store='Amazon', item=item, product_url=link))
             for item, link in post.parsed]
    calls = []
    def follow(fetcher, link, *args, **kwargs):
        calls.append(link)
        if cutoff == 'raised_deadline' and len(calls) == 2:
            raise DeadlineExceeded('offline deadline')
        return link if cutoff == 'unresolved' and len(calls) > 1 else URL
    monkeypatch.setattr(photos, 'resolve_redirects', follow)
    monkeypatch.setattr(photos.time, 'monotonic', lambda: 100 if calls else 0)
    deadline = 50 if cutoff == 'deadline' else None
    fetcher = type('Fetcher', (), {})()
    photos.resolve_posts(pairs, fetcher, cfg, deadline)
    assert post.resolved_links == []
    db = DB(':memory:')
    for _, extracted in pairs:
        dedupe.upsert_extracted(db, extracted, make_savings(), post, cfg, NOW)
    assert len(db.all_deals()) == 2
    assert all(not row['asin'] for row in db.all_deals())
    db.close()


@pytest.mark.parametrize('cutoff', ['cap', 'deadline', 'raised_deadline'])
def test_short_targets_report_every_deferred_link(monkeypatch, cutoff):
    cfg = AppConfig(images_short_links_per_run=1 if cutoff == 'cap' else 10)
    links = ['https://amzn.to/a', 'https://a.co/b', 'https://amzn.to/c']
    calls = []
    def follow(fetcher, link, *args, **kwargs):
        calls.append(link)
        if cutoff == 'raised_deadline' and len(calls) == 2:
            raise DeadlineExceeded('offline deadline')
        return URL
    monkeypatch.setattr(photos, 'resolve_redirects', follow)
    monkeypatch.setattr(photos.time, 'monotonic', lambda: 100 if calls else 0)
    deferred = set()
    cache = {}
    targets = photos.short_targets(links, object(), cfg, 50 if cutoff == 'deadline' else None,
                                   cache, deferred)
    assert targets == [URL]
    assert deferred == set(links[1:])
    if cutoff == 'raised_deadline':
        deferred = set()
        assert photos.short_targets(links[:2], object(), cfg, None, cache, deferred) == [URL]
        assert deferred == {links[1]} and calls == links[:2]


@pytest.mark.parametrize('raised', [False, True])
def test_backfill_deadline_does_not_mark_unfinished_deal_tried(monkeypatch, raised):
    db = DB(':memory:')
    seed(db, canonical_url='https://amzn.to/a', link_as_posted='https://a.co/b')
    calls = []
    def follow(fetcher, link, *args, **kwargs):
        calls.append(link)
        if raised:
            raise DeadlineExceeded('offline deadline')
        return URL
    # The first loop starts in time, but the chain (or next link) hits the deadline.
    clock = iter([0, 0, 100, 100])
    monkeypatch.setattr(photos.time, 'monotonic', lambda: next(clock, 100))
    monkeypatch.setattr(photos, 'resolve_redirects', follow)
    result = photos.backfill_asins(db, db.all_deals(), type('Fetcher', (), {})(), AppConfig(), 50)
    assert result == dict(tried=0, found=0)
    assert not db.get_deal('a').get('amazon_asin_tried')
    assert not db.get_deal('a')['asin']
    db.close()


@pytest.mark.parametrize('wrapped', [False, True])
def test_newsletter_short_links_never_fetch_partner_servers(wrapped):
    db = DB(':memory:')
    link = 'https://amzn.to/email'
    if wrapped:
        link = 'https://partner.example/click?url=' + quote(link, safe='')
    cfg = AppConfig()
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={'Location': URL})
    fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handler), sleep_fn=lambda _: None)
    post = RawPost('partner', 'email', 'https://partner.example/post', 'Kettle', link,
                   NOW, NOW, via_email=True)
    extracted = ExtractedDeal(source_url=post.url, source_name=post.source_name,
                              store='Amazon', item='Kettle', product_url=link)
    photos.resolve_posts([(post, extracted)], fetcher, cfg, None)
    seed(db, canonical_url=link, flags=['newsletter'])
    assert photos.backfill_asins(db, db.all_deals(), fetcher, cfg, None) == dict(tried=0, found=0)
    assert not post.resolved_links and not db.get_deal('a').get('amazon_asin_tried')
    assert calls == []
    fetcher.close()
    db.close()


@pytest.mark.parametrize('changes,title,price,accepted', [
    ({},'Acme Steel Kitchen Kettle',10000,True),
    ({'brand':'Other'},'Acme Steel Kitchen Kettle',10000,False),
    ({},'Acme Travel Cup',10000,False),
    ({},'Acme Steel Kitchen Kettle',6000,True),
    ({},'Acme Steel Kitchen Kettle',14000,True),
    ({},'Acme Steel Kitchen Kettle',5999,False),
    ({},'Acme Steel Kitchen Kettle',14001,False),
    ({},'Acme Steel Kitchen Kettle',-1,False),
    ({'item':'Acme Steel Kitchen Kettle 2'},'Acme Steel Kitchen Kettle 3',10000,False),
])
def test_match_gates(changes,title,price,accepted):
    deal=dict(item='Acme Steel Kitchen Kettle',brand='Acme',regular_price=100,final_price=10)
    deal.update(changes)
    assert bool(photos.title_match(deal,{A:product(title,price)},.85)) == accepted


def test_similarity_and_runner_gap_boundaries():
    deal={'item':'aa bb cc dd ee ff gg hh ii jj', 'final_price':100}
    # 9/10 shared tokens: Dice .90; competitor .80 -> exactly .10 ahead.
    best=product('aa bb cc dd ee ff gg hh ii kk')
    runner=product('aa bb cc dd ee ff gg hh kk ll')
    assert photos.title_match(deal,{A:best,B:runner},.9) == A
    assert not photos.title_match(deal,{A:best,B:runner},.90001)
    assert not photos.title_match(deal,{A:best,B:best},.85)
    assert photos.title_match(deal,{A:product('aa bb cc dd ee ff gg hh ii')},.85) == A


@pytest.fixture
def search(monkeypatch):
    monkeypatch.setenv('KEEPA_KEY','offline-test-key')
    db=DB(':memory:')
    cfg=AppConfig(images_keepa_search_per_run=1, images_keepa_search_per_day=2,
                  feature_flags={'keepa':True})
    budget=KeepaBudget(1000, 0, NOW, db)
    budget.save(db)
    calls=[]
    answers={}
    def handler(req):
        calls.append(req.url.path)
        if req.url.path in answers:
            answer=answers[req.url.path]
            if isinstance(answer,Exception):raise answer
            return answer
        if req.url.path == '/query':
            assert json.loads(req.url.params['selection'])['perPage'] == 50
            return httpx.Response(200,json={'asinList':[A], 'tokensConsumed':11,'tokensLeft':989})
        return httpx.Response(200,json={'products':[product()], 'tokensConsumed':1,'tokensLeft':988})
    fetcher=PoliteFetcher(db,cfg,transport=httpx.MockTransport(handler),sleep_fn=lambda _:None)
    yield db,cfg,fetcher,calls,answers
    fetcher.close();db.close()


def search_run(setup, key='run', now=NOW, **kw):
    db,cfg,fetcher,*_=setup
    return photos.search_photos(db,db.all_deals(),fetcher,cfg,now,None,key,**kw)


@pytest.mark.parametrize('newsletter', [False, True])
def test_paid_search_waits_for_free_post_link_attempt(search, newsletter):
    db, cfg, fetcher, calls, _ = search
    seed(db, regular_price=100, final_price=10, flags=['newsletter'] if newsletter else [])
    if not newsletter:
        assert search_run(search)['searched'] == 0
        assert calls == []
        assert not db.get_deal('a').get('photo_search_tried')
        db.update_deal('a', payload=dict(db.get_deal('a'), flags=['post_link_tried']))
    assert search_run(search)['matched'] == 1
    assert calls == ['/query', '/product']


def test_search_caps_rerun_and_photo_only_identity(search, monkeypatch):
    db,cfg,fetcher,calls,_=search
    seed(db,'a',regular_price=100,final_price=10)
    seed(db,'b',regular_price=100,final_price=10)
    before=db.get_deal('a')
    assert search_run(search)['matched'] == 1
    after=db.get_deal('a')
    assert after['photo_asin'] == A and after['asin_source'] == 'title_match'
    assert after['image_url'] == PIC and not after['asin']
    assert {k:after[k] for k in before if k in dedupe._DEAL_FIELDS and k not in ('photo_asin','asin_source','image_url','photo_search_tried')} == {k:v for k,v in before.items() if k in dedupe._DEAL_FIELDS and k not in ('photo_asin','asin_source','image_url','photo_search_tried')}
    assert site._serialize_deal(before,NOW,cfg=cfg)['url'] == site._serialize_deal(after,NOW,cfg=cfg)['url']
    assert not verify.recheck_amazon_prices([after],cfg,fetcher=fetcher)
    assert search_run(search)['searched'] == 0
    assert search_run(search,'next')['matched'] == 1
    seed(db,'c',regular_price=100,final_price=10)
    assert search_run(search,'third')['searched'] == 0
    assert search_run(search,'tomorrow',NOW+timedelta(days=1))['matched'] == 1
    assert calls == ['/query','/product']*3


@pytest.mark.parametrize('balance', [None,710,60,0])
def test_search_reserve_and_unknown_balance(search,balance):
    db,cfg,fetcher,calls,_=search
    seed(db,regular_price=100,final_price=10)
    if balance is None:
        db.meta_set('keepa_budget','')
    else:
        KeepaBudget(balance,0,NOW,db).save(db)
    assert search_run(search)['searched'] == 0
    assert calls == []


@pytest.mark.parametrize('endpoint', ['/query','/product'])
def test_first_429_stops_and_same_run_cannot_retry(search,endpoint):
    db,cfg,fetcher,calls,answers=search
    cfg.images_keepa_search_per_run=10
    seed(db,'a',regular_price=100,final_price=10)
    seed(db,'b',regular_price=100,final_price=10)
    answers[endpoint]=httpx.Response(429,json={'tokensLeft':0,'tokensConsumed':0})
    assert search_run(search)['rate_limited']
    count=len(calls)
    assert search_run(search)['rate_limited']
    assert len(calls) == count


def test_prior_429_and_disabled_flag(search):
    db,cfg,fetcher,calls,_=search
    seed(db)
    assert not search_run(search,rate_limited=True)['searched']
    cfg.feature_flags['keepa']=False
    assert not search_run(search)['searched']
    assert calls == []


def test_lost_response_debit_and_no_repeat(search):
    db,cfg,fetcher,calls,answers=search
    seed(db,regular_price=100,final_price=10)
    answers['/query']=httpx.ReadTimeout('offline')
    assert search_run(search)['tokens'] == 11
    assert KeepaBudget.load(db).tokens_left == 989
    assert search_run(search,'other')['searched'] == 0


def test_missing_runner_up_blocks_match(search):
    db,cfg,fetcher,calls,answers=search
    seed(db,regular_price=100,final_price=10)
    answers['/query']=httpx.Response(200,json={'asinList':[A,B],'tokensLeft':989,'tokensConsumed':11})
    assert search_run(search)['matched'] == 0
    assert not db.get_deal('a')['image_url']


def test_woot_match_url_tracker_id_exact_title_and_ambiguity():
    db=DB(':memory:')
    seed(db,'url',store='Woot',canonical_url='https://home.woot.com/offers/kettle?ref=credit')
    seed(db,'tracker',store='Woot',link_as_posted='https://go.example?url='+quote('https://woot.com/offers/kettle',safe=''))
    seed(db,'exact',store='Woot',item='ACME: Steel Kettle')
    seed(db,'id',store='Woot',item='Different partner title',canonical_url='https://woot.com/offers/offer')
    seed(db,'near',store='Woot',item='Acme Steel Kettle 2')
    seed(db,'wrong-url',store='Woot',item='Acme Steel Kettle',canonical_url='https://woot.com/offers/other')
    items=[dict(OfferId='offer',Url='https://woot.com/offers/kettle',Title='Acme Steel Kettle',Photo=PIC)]
    assert _backfill_feed_photos(db,items) == 4
    assert not db.get_deal('near')['image_url'] and not db.get_deal('wrong-url')['image_url']
    assert _backfill_feed_photos(db,items) == 0
    seed(db,'ambiguous',store='Woot',item='Acme Steel Kettle')
    assert _backfill_feed_photos(db,items+[dict(items[0],Photo='https://img.example/other.jpg')]) == 0
    db.close()


def test_coverage_counts_only_live_public_causes(monkeypatch):
    from dealradar import source_policy
    from dealradar.models import SourceConfig
    monkeypatch.setattr(source_policy,'_sources',lambda:[SourceConfig(name='grant',method='rss',url='https://grant.example',image_permission=True)])
    rows=[dict(status='alive',store='Amazon',asin=''),
          dict(status='alive',store='amazon.com',asin=A),
          dict(status='alive',store='Amazon Warehouse',asin=A,photo_lookup_asin=A,photo_lookup_found_none=True),
          dict(status='alive',store='Woot'),dict(status='alive',store='Walmart'),
          dict(status='alive',store='Other',sightings=[{'source_name':'grant'}]),
          dict(status='alive',store='Other',image_url=PIC),dict(status='dead',store='Amazon')]
    counts=photos.coverage(rows)
    assert counts['live_public'] == 7 and counts['with_photo'] == 1 and counts['without_photo'] == 6
    assert set(counts['by_cause'].values()) == {1}


@pytest.mark.parametrize('audit_mode',['shadow','enforce'])
def test_pipeline_rerun_and_summary(env, capsys, audit_mode):
    # This fixture's fake services never access the internet.
    results=[]
    original=pipeline.run
    def wrapped(**kw):
        result=original(**kw);results.append(result);return result
    from unittest.mock import patch
    with patch.object(pipeline,'run',wrapped):
        check_rerun(env,capsys,audit_mode)
    assert all('photo_coverage' in r for r in results)
    for result in results:
        c=result['photo_coverage']
        assert c['live_public'] == c['with_photo']+c['without_photo']
        assert sum(c['by_cause'].values()) == c['without_photo']


def test_paid_empty_product_records_missing_photo_without_price_verdict(search):
    db,cfg,fetcher,calls,answers=search
    answers['/product']=httpx.Response(200,json={'products':[],'tokensConsumed':1,'tokensLeft':999})
    row=seed(db,asin=A,regular_price=100,final_price=10)
    verdicts=verify.recheck_amazon_prices([row],cfg,fetcher=fetcher)
    assert not verdicts
    assert row['photo_lookup_asin'] == A and row['photo_lookup_found_none']
    assert not row.get('price_rechecked_at')


def test_quota_survives_interleaved_run_keys(search):
    db,cfg,*_=search
    cfg.images_keepa_search_per_day=10
    for key in ('a','b','c'):seed(db,key,regular_price=100,final_price=10)
    assert search_run(search,'one')['searched'] == 1
    assert search_run(search,'two')['searched'] == 1
    assert search_run(search,'one')['searched'] == 0


def test_exact_85_percent_similarity():
    words=[chr(97+i)*2 for i in range(20)]
    deal={'item':' '.join(words),'final_price':100}
    candidate=product(' '.join(words[:17]+['extra','spare','different']))
    assert photos.title_match(deal,{A:candidate},.85) == A
    assert not photos.title_match(deal,{A:candidate},.850001)


def test_short_link_cap_defers_instead_of_memoizing_unattempted_deals(monkeypatch):
    db=DB(':memory:')
    cfg=AppConfig(images_short_links_per_run=1)
    seed(db,'a',canonical_url='https://amzn.to/a')
    seed(db,'b',canonical_url='https://a.co/b')
    monkeypatch.setattr(photos,'resolve_redirects',lambda *a,**kw:URL)
    assert photos.backfill_asins(db,db.all_deals(),type('Fetcher',(),{})(),cfg,None)['tried'] == 1
    assert not db.get_deal('b').get('amazon_asin_tried')
    assert photos.backfill_asins(db,db.all_deals(),type('Fetcher',(),{})(),cfg,None)['found'] == 1
    db.close()


def test_rendered_wall_keeps_photos_first_after_fresh_mix(tmp_path):
    from tests.test_front_page import browser
    cfg=AppConfig(photoless_last=True,unconfirmed_label_enabled=True)
    db=DB(':memory:')
    rows=[dict(key=str(i),dedupe_key=str(i),status='alive',store='Store'+str(i%3),
               category='home',pct_at_checkout=80,first_seen_at='2026-10-09T12:00:00Z',
               image_url=PIC if i%2 else '') for i in range(30)]
    cases=[dict(rows=rows,options={'prefer_photos':True,'broad_categories':['home'],
            'max_niche_top':5,'max_same_store_top':4,'max_same_category_run':2})]
    result=browser(tmp_path,cfg,db,cases)[0]
    rendered=result['rendered']
    assert set(rendered[:15]) == {r['key'] for r in rows if r['image_url']}
    db.close()
