"""Task 335: approved, honest heroes; lazy shared Join; exact OFF publication."""
import json
import re
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import site, tool_hero, tool_pages
from dealradar.publish.seo_audit import Page
from dealradar.savings import TOOL_METER_BADGE_JS, tool_meter, display_pct
from tests.history_pins import pre_tool_pages_hero
from tests.test_support import NOW, render
from tests.test_tool_pages import offline  # noqa: F401
from tests.test_publish_site import make_deal
from tests.test_home_comment_strip import files
from tests.test_pwa import tree_hash


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_strict_bool(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(json.dumps({'tool_pages_hero': value}))
    with pytest.raises(ValueError, match='^tool_pages_hero must be a boolean$'):
        load_config(path)


def test_default_and_shipped(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    assert load_config(path).tool_pages_hero is AppConfig().tool_pages_hero is False
    assert load_config().tool_pages_hero is True
    for value in (True, False):
        path.write_text(json.dumps({'tool_pages_hero': value}))
        assert load_config(path).tool_pages_hero is value


@pytest.mark.parametrize('mode,expected', [
    ('shipped', '9c6d4dd9829e1c264dee5d3415cd61d3065a8a856c45433b655b204e99343ce3'),
    ('inline', '86bc019f149ec51fb1be29e95ad7e273d9d061aa7af09b382576d9edcdfebcce'),
    ('membership', '8de4c00d2f1e8b4321190b93b25eb7e861fbff829a9a2d6d8d2da19587e9084b'),
])
def test_off_previous_release_missing_and_back(tmp_path, mode, expected):
    cfg = pre_tool_pages_hero(load_config())
    if mode == 'inline':
        cfg.search_analyze['lazy'] = False
        cfg.strip_comments = False
    if mode == 'membership':
        cfg.support_enabled = False
    out, *_ = render(tmp_path, cfg)
    assert tree_hash(out) == expected  # Captured from untouched a69031d; never re-pin.
    before = files(out)
    del cfg.tool_pages_hero
    render(tmp_path, cfg)
    assert files(out) == before
    cfg.tool_pages_hero = True
    render(tmp_path, cfg)
    assert files(out) != before
    cfg.tool_pages_hero = False
    render(tmp_path, cfg)
    assert files(out) == before


def rows():
    return [make_deal(dedupe_key=f'hero-{i}', item=f'Kitchen kettle <{i}> & "tea"', store='Amazon',
        category=('kitchen', 'beauty', 'electronics')[i % 3], verify_tier='verified',
        final_price=15, regular_price=30, pct_at_checkout=50, abs_savings=15,
        regular_price_basis='List price on Amazon', basis='amazon_list_price',
        asin=f'B{i:09d}', canonical_url=f'https://amazon.com/dp/B{i:09d}',
        keepa_list_price={'asin':f'B{i:09d}', 'price':30, 'observed_at':NOW.isoformat()},
        price_rechecked_at=NOW.isoformat(), first_seen_at=NOW.isoformat(), codes=[], conditions=['new'],
        click_price_check={'asin':f'B{i:09d}', 'final':15, 'buybox':15, 'at':NOW.isoformat()},
        critic={'plausible':True,'confidence':.9,'reason':'Backed list'},
        image_url=f'https://m.media-amazon.com/images/I/hero{i}.jpg') for i in range(8)]


def test_published_hero_copy_h1_metadata_and_honest_visuals(tmp_path):
    expected_copy = {
        'checker': ('Is it really a deal?', 'Know in 10 seconds.',
            "Paste any Amazon link or type a product. We check today's price against Amazon's list price and our verified deals.", 'No extension'),
        'finder': ('Every Amazon discount.', 'One click.',
            "Type what you want or paste a link. We open Amazon's discounts for it and show our verified deals.", 'Updated every hour'),
    }
    cfg = load_config()
    out, doc, *_ = render(tmp_path, cfg, rows())
    before = files(out)
    published = tool_hero.candidates(doc['deals'], cfg)
    assert len(published) == 8
    d, meter = tool_hero.example(published)
    assert meter == tool_meter(d['final_price'], d['regular_price'])
    checker = (out / 'amazon-deal-checker/index.html').read_text()
    assert tool_hero.checker(published) in checker
    assert d['image_url'] in checker and d['deal_path'] in checker
    assert 'Kitchen kettle &lt;' in checker and '&amp; &quot;tea&quot;' in checker
    assert '$30.00 list' in checker and '$15.00' in checker and 'You save $15.00' in checker
    assert '50% off list price' in checker and '--meter-angle:112.5deg' in checker
    finder = (out / 'amazon-discount-finder/index.html').read_text()
    cloud = tool_hero.cloud_selection(published)
    assert len(cloud) == 6
    for i, d in enumerate(cloud):
        assert f'class="tile tile-{i}" href="{d["deal_path"]}"' in finder
        assert f'<i>-{display_pct(d["pct_at_checkout"])}%</i>' in finder
    cfg.tool_pages_hero = False
    render(tmp_path, cfg, rows())
    for slug, spec in tool_pages.PAGES.items():
        html = before[slug + '/index.html'].decode()
        page = Page(html, slug)
        h1s = [n for n in page.root.nodes() if n.tag == 'h1']
        assert len(h1s) == 1
        assert spec['name'] in h1s[0].text()
        headline, accent, sub, last = expected_copy[spec['mode']]
        assert headline in h1s[0].text() and accent in h1s[0].text()
        assert sub in html and '<li>' + last + '</li>' in html
        old = (out / slug / 'index.html').read_text()
        for pattern in (r'<title>.*?</title>', r'<meta name="description"[^>]+>',
                        r'<link rel="canonical"[^>]+>', r'<script type="application/ld\+json">.*?</script>'):
            assert re.findall(pattern, html, re.S) == re.findall(pattern, old, re.S)
        for ident in ('tool-form', 'analyzer', 'tool-result', 'top-sellers', 'verified-deals', 'faq-title'):
            assert 'id="' + ident + '"' in html
    for name in ('sitemap.xml', 'sitemap-pages.xml'):
        if name in before:
            assert before[name] == (out / name).read_bytes()


def candidate(**overrides):
    return dict(key='one', status='alive', badge='verified', final_price=15, regular_price=30,
        abs_savings=15, pct_at_checkout=50, amazon=True, store='Amazon', category='kitchen',
        regular_price_basis='List price', item='Kettle', deal_path='/deal/kettle-12345678/',
        image_url='https://m.media-amazon.com/kettle.jpg', **overrides)


@pytest.mark.parametrize('patch', [dict(status='dead'), dict(locked=True), dict(price_unconfirmed=True),
    dict(price_aged=True), dict(click_price_unknown=True), dict(badge='corroborated'),
    dict(image_url=''), dict(deal_path='https://evil.test/'), dict(item='vibrator'),
    dict(headline='Price not yet confirmed')])
def test_disqualifiers(patch):
    assert not tool_hero.candidates([candidate() | patch], load_config())


def test_example_fallback_and_selection():
    first = candidate() | dict(regular_price_basis='previous price')
    good = candidate() | dict(key='two')
    assert tool_hero.example([first, good])[0] is good
    for patch in (dict(regular_price_basis=''), dict(final_price=29), dict(regular_price=None),
                  dict(store='Walmart', amazon=False), dict(abs_savings=None)):
        html = tool_hero.checker([candidate() | patch])
        assert 'EXAMPLE</span>' in html and 'Big discount' in html
        assert not re.search(r'<img|<a |\$|today.s deal', html)


def test_cloud_diversity_published_rounding_and_fallback():
    deals = [candidate() | dict(key=str(i), category=str(i % 3), store=str(i % 2), pct_at_checkout=25.5 + i)
             for i in range(9)]
    chosen = tool_hero.cloud_selection(deals)
    assert len(chosen) == 6 and len({d['category'] for d in chosen[:3]}) == 3
    assert tool_hero.cloud_selection(deals) == chosen
    assert tool_hero.finder(deals[:3]) == ''
    assert len(tool_hero.cloud_selection(deals[:4])) == 4
    assert '<i>-26%</i>' in tool_hero.finder(deals)
    assert tool_hero.cloud_selection([d | dict(pct_at_checkout=None) for d in deals]) == []


def test_gauge_python_js_parity_and_thresholds(tmp_path):
    values = [(100,100), (85.01,100), (85,100), (60.01,100), (60,100), (20,100), (1,100), (15,30)]
    expected = [tool_meter(price, listed) for price, listed in values]
    code = TOOL_METER_BADGE_JS + '\nconsole.log(JSON.stringify(' + json.dumps(values)
    code += '.map(([p,l])=>toolBadge(Math.round(p*100),Math.round(l*100)))));'
    result = subprocess.run(['node', '-e', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected
    assert [d['kind'] for d in expected[:5]] == ['live','live','good','good','big']
    assert expected[0]['angle'] == 0 and expected[-1]['angle'] == 112.5
    assert expected[-2]['angle'] == 180
    assert '--meter-angle:0deg' in tool_hero.gauge(expected[0])
    assert '>Live Amazon price</text>' in tool_hero.gauge(expected[0])


def test_lazy_dialog_shared_card_endpoints_and_header_scope(tmp_path):
    cfg = load_config()
    out, *_ = render(tmp_path, cfg)
    payload = json.loads((out / 'join-dialog.json').read_text())['html']
    page = Page(payload, '/join-dialog')
    assert page.first(ident='join-email-choice') and 'id="join-google"' in payload
    assert 'The best verified deals in your inbox. No spam.' in payload
    assert 'https://accounts.google.com/gsi/client' in payload
    assert 'fetch(api + "/api/google"' in payload and 'fetch(api + "/subscribe"' in payload
    assert 'JSON.stringify({email: j.me.email})' in payload
    assert 'ResizeObserver' in payload and "e.key !== 'Tab'" in payload
    assert 'width:44px;height:44px' in payload
    module = (out / 'join-dialog.js').read_text()
    for text in ("'role', 'dialog'", "'aria-modal', 'true'", "'aria-labelledby'", "'Escape'",
                 "originalOverflow", 'node.inert = true', 'button.focus', "e.source !== frame.contentWindow"):
        assert text in module
    loader = (out / 'join-loader.js').read_text()
    assert "import('/join-dialog.js?v=" in loader
    assert 'e.ctrlKey||e.metaKey||e.shiftKey||e.altKey' in loader
    assert 'header #subscribe[href="/join"],.tool-header>.join[href="/join"]' in loader
    for path in ('index.html', 'amazon-deal-checker/index.html', 'amazon-discount-finder/index.html'):
        html = (out / path).read_text()
        assert '<script defer src="/join-loader.js?v=' in html and 'href="/join"' in html
        assert 'src="/join-dialog' not in html and 'id="header-join-dialog"' not in html
    for script in ('tool-pages.js', 'join-dialog.js', 'join-loader.js'):
        checked = subprocess.run(['node', '--check', str(out / script)], capture_output=True, text=True)
        assert checked.returncode == 0, checked.stderr
    for body in re.findall(r'<script>(.*?)</script>', payload, re.S):
        checked = subprocess.run(['node', '--check'], input=body, capture_output=True, text=True)
        assert checked.returncode == 0, checked.stderr
