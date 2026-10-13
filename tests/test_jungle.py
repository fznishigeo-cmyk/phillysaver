"""Official Jungle API mapping and the same polite transport contract as pages."""
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.errors import DeadlineExceeded
from dealradar.fetch.jungle import JungleAdapter, parse_jungle
from dealradar.fetch.registry import get_adapter
from dealradar.fetch.validate import _validate_one
from dealradar.http_client import PoliteFetcher
from dealradar.models import SourceConfig, Evidence, ExtractedDeal
from dealradar.parse.extractor import verify_evidence

NOW = datetime(2026, 10, 11, 17, tzinfo=timezone.utc)
BODY = (Path(__file__).parent / 'fixtures/pages/jungle_deals.json').read_bytes()
SOURCE = SourceConfig('jungle_deals', 'api',
                      'https://www.jungle.deals/api/get-top-deals',
                      adapter='jungle_deals', site='https://www.jungle.deals/',
                      crawl_permission='Written grant')


def result(status=200, body=BODY, robots=False, not_modified=False):
    return SimpleNamespace(status=status, body=body, blocked_by_robots=robots,
                           not_modified=not_modified, error='')


def test_api_preserves_prices_conditions_credit_and_stable_identity():
    post = parse_jungle(SOURCE, BODY, NOW)[0]
    assert post.uid == post.url == 'https://www.jungle.deals/deal/B012345678'
    assert post.source_name == SOURCE.name and post.fetched_at == NOW
    assert not post.image_url and not post.photos.images
    assert 'Final price: $13.09' in post.text and 'List price: $24.99' in post.text
    assert '"couponPercentage": 30' in post.text and '"maxSs": 15' in post.text
    assert '"ss": 1' in post.text and 'SAVE30' in post.text
    assert 'Save $3.16 on 2 select items' in post.text
    assert 'unlicensed' not in post.text and 'lastChecked' not in post.text
    changed = BODY.replace(b'07:09:54', b'08:09:54')
    assert parse_jungle(SOURCE, changed, NOW)[0].content_hash == post.content_hash
    deal = ExtractedDeal(item=post.title, store='Amazon', source_url=post.url,
                         source_name=SOURCE.name, final_price=13.09, regular_price=24.99,
                         evidence=Evidence(final_price_quote='Final price: $13.09',
                                           regular_price_quote='List price: $24.99'))
    assert verify_evidence(deal, post.text) == (True, '')
    assert not verify_evidence(replace(deal, final_price=1.99), post.text)[0]


@pytest.mark.parametrize('body', [b'[]', b'{}', b'<title>Just a moment...</title>',
                                  b'[{"ASIN":"bad"}]',
                                  BODY.replace(b'13.09', b'null'),
                                  BODY.replace(b'13.09', b'true'),
                                  BODY.replace(b'13.09', b'NaN')])
def test_changed_api_fails_loudly(body):
    with pytest.raises(RuntimeError, match='changed_jungle'):
        parse_jungle(SOURCE, body, NOW)


def test_dedupe_card_isolation_and_cap():
    first = json.loads(BODY)[0]
    second = dict(first, ASIN='B987654321', finalPrice=1.99, promoCode='OTHER')
    posts = parse_jungle(SOURCE, json.dumps([first, first, second]), NOW)
    assert len(posts) == 2
    assert 'OTHER' not in posts[0].text and 'SAVE30' not in posts[1].text
    many = [dict(first, ASIN=f'B{i:09d}') for i in range(100)]
    assert len(parse_jungle(SOURCE, json.dumps(many), NOW)) == 60


def test_registry_validation_and_conditional_fetch_contract(tmp_path):
    requests = []
    def respond(req):
        requests.append(req)
        return httpx.Response(304) if req.headers.get('if-none-match') else httpx.Response(
            200, content=BODY, headers={'ETag': 'jungle-v1'})
    db = DB(tmp_path / 'state.db')
    f = PoliteFetcher(db, AppConfig(), transport=httpx.MockTransport(respond), sleep_fn=lambda _: None)
    try:
        adapter = get_adapter(SOURCE, AppConfig())
        assert isinstance(adapter, JungleAdapter)
        assert len(adapter.fetch_posts(SOURCE, f, db, AppConfig(), NOW)) == 1
        assert adapter.fetch_posts(SOURCE, f, db, AppConfig(), NOW) == []
        assert _validate_one(SOURCE, f, AppConfig()) == ('live', SOURCE.url, SOURCE)
        assert all(str(r.url) == SOURCE.url for r in requests)
        assert requests[-1].headers['if-none-match'] == 'jungle-v1'
        assert all(r.headers['user-agent'] == AppConfig().resolved_user_agent() for r in requests)
        with pytest.raises(DeadlineExceeded):
            adapter.fetch_posts(SOURCE, f, db, AppConfig(), NOW, deadline=0)
    finally:
        f.close()


@pytest.mark.parametrize('res,error', [(result(403), 'http_403'),
                                      (result(200, b'{}'), 'changed_jungle'),
                                      (result(0, None, True), 'blocked_by_robots')])
def test_refusals_and_validation_failures(res, error):
    f = SimpleNamespace(get=lambda *a, **kw: res)
    with pytest.raises(RuntimeError, match=error):
        JungleAdapter().fetch_posts(SOURCE, f, None, AppConfig(), NOW)
    assert _validate_one(SOURCE, f, AppConfig())[0] == 'dead'


def test_no_permission_means_robots_are_honored(tmp_path):
    requests = []
    def respond(req):
        requests.append(str(req.url))
        return httpx.Response(200, text='User-agent: *\nDisallow: /')
    f = PoliteFetcher(DB(tmp_path / 'state.db'), AppConfig(),
                      transport=httpx.MockTransport(respond), sleep_fn=lambda _: None)
    try:
        with pytest.raises(RuntimeError, match='blocked_by_robots'):
            JungleAdapter().fetch_posts(replace(SOURCE, crawl_permission=''),
                                        f, None, AppConfig(), NOW)
        assert requests == ['https://www.jungle.deals/robots.txt']
    finally:
        f.close()
