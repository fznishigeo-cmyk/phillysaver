"""Page readers preserve per-card evidence and reuse the polite fetch contract."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from dealradar.config import AppConfig, load_sources
from dealradar.db import DB
from dealradar.fetch.pages import PageAdapter, parse_page
from dealradar.fetch.registry import get_adapter
from dealradar.fetch.validate import _validate_one
from dealradar.http_client import PoliteFetcher
from dealradar.models import SourceConfig

NOW = datetime(2026, 10, 11, 17, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / "fixtures" / "pages"


def source(name):
    return SourceConfig(name, "html", f"https://www.{name}.com/" +
                        ("deals" if name == "bradsdeals" else ""),
                        adapter=name, allowed_html=True)


def body(name):
    return (FIXTURES / f"{name}.html").read_bytes()


@pytest.mark.parametrize("name", ["bradsdeals", "dealsofamerica"])
def test_cards_preserve_evidence_identity_credit_and_ignore_chrome(name):
    src = source(name)
    posts = parse_page(src, body(name), NOW)
    assert len(posts) == 1
    p = posts[0]
    assert p.uid == p.url
    assert p.url.startswith(f"https://www.{name}.com/")
    assert p.source_name == name and p.fetched_at == NOW
    assert not p.image_url and not p.photos.images
    assert 'Widget & ' in p.title
    assert '$999' not in p.text and '$0.01' not in p.text
    assert 'days ago' not in p.text and 'Share' not in p.text
    assert parse_page(src, body(name).replace(b'5+ days ago', b'6+ days ago'), NOW)[0].content_hash == p.content_hash
    assert len(parse_page(src, body(name) * 2, NOW)) == 1
    if name == 'bradsdeals':
        assert '$30.96-$42.96' in p.text and 'BRADS26' in p.text
        assert p.published_at is None
    else:
        assert '$41.99' in p.text and '$70.99' in p.text
        assert '30OFF047' in p.text and '(Exp 10/18)' in p.text
        assert 'https://zdcs.link/example' in p.text
        assert p.url.endswith('/widget/817498.htm')
        assert p.published_at == datetime(2026, 10, 11, 15, 51, 49, tzinfo=timezone.utc)


@pytest.mark.parametrize('name', ['bradsdeals', 'dealsofamerica'])
def test_registry_and_validation_do_not_try_feed_discovery(name):
    src = source(name)
    calls = []
    def get(url, **kw):
        calls.append((url, kw))
        return SimpleNamespace(status=200, body=body(name), error='',
                               blocked_by_robots=False, not_modified=False)
    f = SimpleNamespace(get=get)
    assert isinstance(get_adapter(src, AppConfig()), PageAdapter)
    assert _validate_one(src, f, AppConfig(), 123) == ('live', src.url, src)
    assert len(calls) == 1 and calls[0][1]['deadline'] == 123
    assert _validate_one(replace(src, enabled=False), f, AppConfig())[0] == 'disabled'


@pytest.mark.parametrize('name', ['bradsdeals', 'dealsofamerica'])
@pytest.mark.parametrize('status,robots,content,error', [
    (403, False, b'Forbidden', 'http_403'),
    (200, False, b'<title>Just a moment...</title>', 'empty_or_changed_deal_page'),
    (200, False, b'<html>New layout</html>', 'empty_or_changed_deal_page'),
    (0, True, None, 'blocked_by_robots'),
])
def test_refusals_and_changed_layout_are_failures(name, status, robots, content, error):
    f = SimpleNamespace(get=lambda *a, **kw: SimpleNamespace(
        status=status, blocked_by_robots=robots, not_modified=False,
        body=content, error=''))
    with pytest.raises(RuntimeError, match=error):
        PageAdapter().fetch_posts(source(name), f, None, AppConfig(), NOW)
    assert _validate_one(source(name), f, AppConfig())[0] == 'dead'


@pytest.mark.parametrize('name', ['bradsdeals', 'dealsofamerica'])
def test_conditional_requests_permission_host_and_deadline(tmp_path, name):
    src = replace(source(name), crawl_permission='Written permission on file')
    requests = []
    def respond(req):
        requests.append(req)
        if req.headers.get('if-none-match'):
            return httpx.Response(304)
        return httpx.Response(200, content=body(name), headers={'ETag': 'cards-v1'})
    db = DB(tmp_path / 'state.db')
    f = PoliteFetcher(db, AppConfig(), transport=httpx.MockTransport(respond), sleep_fn=lambda _: None)
    try:
        adapter = PageAdapter()
        assert len(adapter.fetch_page(src, f, NOW)) == 1
        assert adapter.fetch_page(src, f, NOW) == []
        assert [str(r.url) for r in requests] == [src.url, src.url]
        assert requests[-1].headers['if-none-match'] == 'cards-v1'
        from dealradar.errors import DeadlineExceeded
        with pytest.raises(DeadlineExceeded):
            adapter.fetch_posts(src, f, db, AppConfig(), NOW, deadline=0)
    finally:
        f.close()


@pytest.mark.parametrize('name', ['bradsdeals', 'dealsofamerica'])
def test_without_permission_robots_still_apply(tmp_path, name):
    requests = []
    def respond(req):
        requests.append(str(req.url))
        return httpx.Response(200, text='User-agent: *\nDisallow: /')
    db = DB(tmp_path / 'state.db')
    f = PoliteFetcher(db, AppConfig(), transport=httpx.MockTransport(respond), sleep_fn=lambda _: None)
    try:
        with pytest.raises(RuntimeError, match='blocked_by_robots'):
            PageAdapter().fetch_posts(source(name), f, db, AppConfig(), NOW)
        assert requests == [f'https://www.{name}.com/robots.txt']
    finally:
        f.close()


def test_sources_are_enabled_with_explicit_page_readers():
    primary, backups = load_sources()
    for name in ('bradsdeals', 'dealsofamerica'):
        s = next(s for s in primary + backups if s.name == name)
        assert s.enabled and s.allowed_html and s.crawl_permission
        assert s.method == 'html' and s.adapter == name
        assert isinstance(get_adapter(s, AppConfig()), PageAdapter)


def test_brads_shopping_link_enrichment_is_scoped_and_hash_stable():
    src = replace(source('bradsdeals'), crawl_permission='Written grant')
    calls = []
    def get(url, **kw):
        calls.append((url, kw))
        content = body('bradsdeals') if url == src.url else (
            b'<a href="/go/d/bltOTHER-other">Related deal</a>'
            b'<a href="https://evil.example/go/d/blt123-shop">Offsite</a>'
            b'<a href="/go/d/blt123-shop">Shop</a>'
            b'<a href="/go/d/blt123-shop">Duplicate</a>')
        return SimpleNamespace(status=200, body=content, error='',
                               blocked_by_robots=False, not_modified=False)
    f = SimpleNamespace(get=get)
    db = SimpleNamespace(post_is_known=lambda _: False)
    post = PageAdapter().fetch_posts(src, f, db, AppConfig(), NOW, 123)[0]
    assert post.text.endswith('Buy: https://www.bradsdeals.com/go/d/blt123-shop')
    assert 'evil' not in post.text and 'OTHER' not in post.text
    assert post.content_hash == parse_page(src, body('bradsdeals'), NOW)[0].content_hash
    assert len(calls) == 2
    assert calls[1][1] == dict(conditional=False, deadline=123, robots_override_host='www.bradsdeals.com')
    calls.clear()
    PageAdapter().fetch_posts(src, f, SimpleNamespace(post_is_known=lambda _: True), AppConfig(), NOW)
    assert len(calls) == 1


def test_brads_missing_shopping_links_fail_without_parsing_wall_or_related_items():
    src = source('bradsdeals')
    f = SimpleNamespace(get=lambda url, **kw: SimpleNamespace(
        status=200, body=body('bradsdeals') if url == src.url else b'<title>Just a moment...</title>',
        error='', blocked_by_robots=False, not_modified=False))
    with pytest.raises(RuntimeError, match='missing_deal_shopping_links'):
        PageAdapter().fetch_posts(src, f, SimpleNamespace(post_is_known=lambda _: False), AppConfig(), NOW)


def test_two_cards_do_not_mix_evidence_or_exceed_cap():
    src = source('dealsofamerica')
    original = body('dealsofamerica')
    changed = original.replace(b'817498', b'123456').replace(b'41.99', b'12.50').replace(b'30OFF047', b'OTHER')
    posts = parse_page(src, original + changed, NOW)
    assert len(posts) == 2
    assert 'OTHER' not in posts[0].text and '30OFF047' not in posts[1].text
    many = b''.join(original.replace(b'817498', str(i).encode()) for i in range(100))
    assert len(parse_page(src, many, NOW)) == 60


def test_price_quotes_reach_unchanged_evidence_verifier():
    from dealradar.models import Evidence, ExtractedDeal
    from dealradar.parse.extractor import verify_evidence
    post = parse_page(source('dealsofamerica'), body('dealsofamerica'), NOW)[0]
    deal = ExtractedDeal(item='Widget & Light', store='Amazon',
                         source_url=post.url, source_name=post.source_name,
                         final_price=41.99, regular_price=70.99,
                         evidence=Evidence(final_price_quote='for a low $41.99',
                                           regular_price_quote='Normally $70.99'))
    assert verify_evidence(deal, post.text) == (True, '')
    assert verify_evidence(replace(deal, final_price=1.99), post.text)[0] is False
