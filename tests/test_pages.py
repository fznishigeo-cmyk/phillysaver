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
    if name in {"dealmoon_us", "morningsave", "jungle_deals"}:
        urls = {"dealmoon_us": "https://www.dealmoon.com/en",
                "morningsave": "https://morningsave.com/",
                "jungle_deals": "https://www.jungle.deals/api/get-top-deals"}
        return SourceConfig(name, "api" if name == "jungle_deals" else "html",
                            urls[name], adapter=name, allowed_html=True)
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


@pytest.mark.parametrize('name', ['bradsdeals', 'dealsofamerica', 'dealmoon_us', 'morningsave'])
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


@pytest.mark.parametrize('name', ['bradsdeals', 'dealsofamerica', 'dealmoon_us', 'morningsave'])
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


@pytest.mark.parametrize('name', ['bradsdeals', 'dealsofamerica', 'dealmoon_us', 'morningsave'])
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
        assert len(adapter.fetch_page(src, f, NOW)) == (2 if name == "morningsave" else 1)
        assert adapter.fetch_page(src, f, NOW) == []
        assert [str(r.url) for r in requests] == [src.url, src.url]
        assert requests[-1].headers['if-none-match'] == 'cards-v1'
        from dealradar.errors import DeadlineExceeded
        with pytest.raises(DeadlineExceeded):
            adapter.fetch_posts(src, f, db, AppConfig(), NOW, deadline=0)
    finally:
        f.close()


@pytest.mark.parametrize('name', ['bradsdeals', 'dealsofamerica', 'dealmoon_us', 'morningsave'])
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
        from urllib.parse import urlsplit
        assert requests == [f'https://{urlsplit(source(name).url).netloc}/robots.txt']
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


@pytest.mark.parametrize('name,count', [('dealmoon_us', 1), ('morningsave', 2)])
def test_new_html_cards_preserve_exact_prices_conditions_and_credit(name, count):
    src = source(name)
    posts = parse_page(src, body(name), NOW)
    assert len(posts) == count
    assert len(parse_page(src, body(name) * 2, NOW)) == count
    for p in posts:
        assert p.uid == p.url and p.source_name == name
        assert not p.image_url and not p.photos.images
        assert '$999' not in p.text and '$0.01' not in p.text
        assert p.fetched_at == NOW and p.published_at is None
    p = posts[0]
    assert 'Widget & Case' in p.title and 'SAVE30' in p.text
    if name == 'dealmoon_us':
        assert p.url == 'https://www.dealmoon.com/en/widget/123456.html'
        assert 'Now $4.07 (list price $9.49).' in p.text
        assert 'subscribe & save' in p.text
        assert 'https://go.dealmoon.com/exec/j?d=123456&lang=en' in p.text
    else:
        assert p.url == 'https://morningsave.com/deals/widget'
        assert 'Sale price 2 for $19.99. List price $60.' in p.text
        assert 'Sale price $27.99 to $29.99.' in posts[1].text
        assert 'SAVE30' not in posts[1].text


@pytest.mark.parametrize('name', ['dealmoon_us', 'morningsave'])
def test_new_cards_fail_on_missing_title_or_offsite_permalink(name):
    src = source(name)
    original = body(name)
    if name == 'dealmoon_us':
        broken = original.replace(b'zoom-title', b'changed-title')
        foreign = original.replace(b'www.dealmoon.com/en/widget', b'evil.example/en/widget')
    else:
        broken = original.replace(b'h2', b'h4')
        foreign = original.replace(b'href="/deals/widget"', b'href="https://evil.example/deals/widget"')
    for markup in [broken, foreign]:
        with pytest.raises(RuntimeError, match='changed_deal_card|invalid_deal_permalink'):
            parse_page(src, markup, NOW)


def test_new_sources_are_enabled_only_with_explicit_readers():
    from dealradar.fetch.jungle import JungleAdapter
    primary, backups = load_sources()
    for name in ('dealmoon_us', 'morningsave', 'jungle_deals'):
        src = next(s for s in primary + backups if s.name == name)
        assert src.enabled and src.crawl_permission
        assert src.adapter == name
        assert isinstance(get_adapter(src, AppConfig()),
                          JungleAdapter if name == 'jungle_deals' else PageAdapter)


@pytest.mark.parametrize('name', ['dealmoon_us', 'morningsave'])
def test_new_html_cards_remain_isolated_and_capped(name):
    original = body(name)
    changed = original.replace(b'123456', b'654321').replace(b'/deals/widget', b'/deals/other')
    changed = changed.replace(b'hero-widget', b'other-hero').replace(b'SAVE30', b'OTHER')
    posts = parse_page(source(name), original + changed, NOW)
    assert 'OTHER' not in posts[0].text
    assert 'SAVE30' not in posts[2 if name == 'morningsave' else 1].text
    many = b''.join(original.replace(b'123456', str(i).encode())
                   .replace(b'/deals/widget', f'/deals/widget-{i}'.encode())
                   .replace(b'hero-widget', f'hero-widget-{i}'.encode())
                   for i in range(100))
    assert len(parse_page(source(name), many, NOW)) == 60


def test_morningsave_sales_counters_do_not_change_identity():
    original = parse_page(source('morningsave'), body('morningsave'), NOW)
    changed = parse_page(source('morningsave'),
                         body('morningsave').replace(b'100+ Sold Today', b'200+ Sold Today'), NOW)
    assert [p.content_hash for p in original] == [p.content_hash for p in changed]
    with pytest.raises(RuntimeError, match='changed_deal_card'):
        parse_page(source('morningsave'), body('morningsave').replace(b'sale-price', b'new-price'), NOW)
