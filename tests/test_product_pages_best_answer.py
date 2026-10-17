"""Task 118: stored facts, static answers and an indexable-only crawl path."""

import json
import re
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
import pytest

from dealradar.config import load_config
from dealradar.parse.claude_client import ClaudeParser
from dealradar.publish import product_pages, seo, seo_audit, site
from dealradar.savings import product_price_facts, price_difference_pct
from dealradar.util import iso
from tests.test_product_price_pages import db, cfg, clock, seed, prepare
from tests.test_product_offer_pages import tree
from tests.test_publish_site import NOW
from tests.test_seo_deal_pages import _page
from tests.history_pins import pre_history_removal


def render(db, cfg, out, rows):
    result = site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
    path = result['public_deals'][0]['product_path']
    return result, seo_audit.Page(_page(out, path).read_text(), path)


def test_facts_use_lifetime_checks_but_ninety_day_median_and_distinct_deal_dates():
    observations = [{'price': p, 'observed_at': iso(NOW - timedelta(days=d))}
                    for p, d in [(5, 120), (12, 90), (10, 7), (14, 0), (1, -1), (-1, 2)]]
    observations += [{'price': float('nan'), 'observed_at': iso(NOW)}, {'price': 3, 'observed_at': 'bad'}]
    offers = [{'date': iso(NOW - timedelta(days=d)), 'discount': pct}
              for d, pct in [(0, 20), (90, 40), (91, 99), (-1, 80), (3, None), (2, float('inf'))]]
    facts = product_price_facts(observations[::-1], offers, NOW)
    assert facts == dict(checks=4, median=12, median_checks=3, median_since=NOW - timedelta(days=90),
                        median_90d=True, low=5, low_at=NOW - timedelta(days=120),
                        high=14, high_at=NOW, first=NOW - timedelta(days=120), last=NOW,
                        deals_90d=4, latest_deal=NOW, average_discount=30, discount_samples=2)
    assert product_price_facts(observations, offers, NOW) == facts
    assert price_difference_pct(59.99, 72.99) == pytest.approx(17.81065899)
    assert price_difference_pct(15, 12) == -25
    assert price_difference_pct(1, 0) is None
    assert price_difference_pct(None, 12) is None
    assert product_price_facts([], [], NOW)['average_discount'] is None


@pytest.mark.parametrize('days,median', [([0, 1], None), ([0, 1, 1.99], None), ([0, 1, 2], 12)])
def test_sparse_checks_do_not_invent_a_typical_price(days, median):
    facts = product_price_facts([{'price': 10 + i * 2, 'observed_at': iso(NOW - timedelta(days=d))}
                                for i, d in enumerate(days)], [], NOW)
    assert facts['median'] == median


def test_all_sections_head_and_schema_match_stored_facts(db, cfg, tmp_path):
    rows = seed(db, 2)
    db.record_price(rows[0]['dedupe_key'], 'Amazon', 5, NOW - timedelta(days=120), 'old-check')
    result, page = render(db, cfg, tmp_path, rows)
    assert page.first(tag='title').text() == 'Kitchen Widget 0 price history: lowest $5.00 (Jun 2026) | wowpriced'
    facts = page.first(cls='product-facts')
    table = {n.children[0].text(): n.children[1].text() for n in facts.nodes() if n.tag == 'tr'}
    assert table['Current price (live deal)'] == '$10.00 USD'
    assert table['Lowest we’ve seen'] == '$5.00 (Jun 11, 2026)'
    assert table['Typical price (median of our 6 checks since Oct 2, 2026)'] == '$12.50 USD'
    assert table['Highest we’ve seen'] == '$15.00 (Oct 2, 2026)'
    assert table['Number of our checks'] == '7'
    assert table['First check'] == 'Jun 11, 2026'
    assert table['Last check'] == 'Oct 9, 2026'
    assert page.first(cls='last-check').text() == 'Oct 9, 2026, 14:00 UTC'
    assert table['Deals found in the last 90 days'] == '1'
    assert table['Average deal discount (90 days)'] == '80% across 1 deal'
    assert all(n.attrs.get('scope') == 'row' for n in facts.nodes() if n.tag == 'th')
    assert page.first(cls='price-verdict').text() == 'Yes — $10.00 is 20% below its usual $12.50 (our median since Oct 2, 2026).'
    assert 'On sale 1 time in the last 90 days; most recent Oct 9, 2026' in page.scope.text()
    assert not page.schema_mismatch()
    assert result['product_pages']['with_live_deal'] == 2
    assert result['product_pages']['related_links_avg'] == 1
    assert 'keepa' not in _page(tmp_path, page.path).read_text().lower()
    modified = next(s['dateModified'] for s in page.schemas if s['@type'] == 'WebPage')
    assert modified == iso(NOW)
    assert page.first(tag='img').attrs['width'] == page.first(tag='img').attrs['height'] == '320'
    assert all(s.get('@type') != 'Review' for s in page.schemas)


@pytest.mark.parametrize('price,expected', [(10, 'Yes — $10.00 is 20% below'),
                                           (12.50, 'About usual — $12.50 is about'),
                                           (20, 'Higher than usual — $20.00 is 60% above')])
def test_answers_follow_verdict_and_savings_math(db, cfg, tmp_path, price, expected):
    rows = seed(db, 1)
    rows[0]['final_price'] = price
    _, page = render(db, cfg, tmp_path, rows)
    assert page.first(cls='price-verdict').text().startswith(expected)


def test_missing_data_is_omitted_and_no_live_deal_has_no_product_offer(db, cfg, tmp_path):
    rows = seed(db, 1, checks=0)
    _, page = render(db, cfg, tmp_path, rows)
    assert page.first(cls='price-verdict') is None
    assert 'Typical price' not in page.first(cls='product-facts').text()
    assert 'Lowest we’ve seen' not in page.first(cls='product-facts').text()
    assert 'dateModified' not in json.dumps(page.schemas)
    assert page.first(cls='related-products') is None
    assert not page.schema_mismatch()
    for i in range(6):
        db.record_price(rows[0]['dedupe_key'], 'Amazon', 10 + i, NOW - timedelta(days=i * 2), f'new-{i}')
    render(db, cfg, tmp_path, rows)
    db.conn.execute('DELETE FROM deals')
    db.conn.commit()
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[])
    page = seo_audit.Page(_page(tmp_path, page.path).read_text(), page.path)
    assert page.first(cls='price-verdict').text() == 'A current offer is not listed for this product. The lowest we’ve seen is $10.00 (Oct 9, 2026).'
    assert 'Current price' not in page.first(cls='product-facts').text()
    assert not page.products() and not page.first(cls='getdeal')
    assert not page.schema_mismatch()
    assert result['product_pages']['with_live_deal'] == 0


def test_old_snapshots_do_not_invent_discounts_or_recent_sales(db, cfg, tmp_path):
    rows = seed(db, 1)
    pages, _, _ = prepare(db, cfg, rows)
    page = next(iter(pages.values()))
    for offer in page['offers'].values():
        offer.pop('discount', None)
        offer['date'] = iso(NOW - timedelta(days=91))
    facts = product_price_facts(page['checks'], list(page['offers'].values()), NOW)
    html = product_pages.facts_html(facts, None)
    assert 'Average deal discount' not in html
    assert facts['deals_90d'] == 0 and facts['latest_deal'] is None


def test_related_only_indexable_same_category_then_brand_deterministic():
    page = dict(path='/p/original/', category='kitchen', brand='Acme')
    rows = [dict(path=f'/p/{i}/', item=f'Item {i}', category=cat, brand=brand, indexable=indexable)
            for i, cat, brand, indexable in [('z', 'kitchen', 'Other', True),
                ('a', 'electronics', 'ACME', True), ('b', 'kitchen', 'Acme', False),
                ('c', 'pets', 'Other', True), ('y', 'kitchen', '', True)]]
    pages = {r['path']: r for r in rows + [dict(page, indexable=True)]}
    expected = ['/p/y/', '/p/z/', '/p/a/']
    assert [p['path'] for p in product_pages.related(page, pages)] == expected
    assert [p['path'] for p in product_pages.related(page, dict(reversed(list(pages.items()))))] == expected
    more = {str(i): dict(page, path=f'/p/extra-{i}/', indexable=True) for i in range(10)}
    assert len(product_pages.related(page, more)) == 6
    assert not product_pages.related(dict(page, category='', brand=''), pages)


@pytest.mark.parametrize('checks,indexable', [(0, False), (6, True)])
def test_index_and_split_sitemap_only_list_indexable_products(db, cfg, tmp_path, checks, indexable):
    rows = seed(db, 2, checks=checks)
    db.conn.execute("DELETE FROM price_history WHERE canonical_key='product-1'")
    db.conn.commit()
    result, product = render(db, cfg, tmp_path, rows)
    index = seo_audit.Page(_page(tmp_path, '/p/').read_text(), '/p/')
    assert index.indexable is indexable
    product_links = [h for h in index.hrefs if h.startswith('/p/') and h != '/p/']
    assert product_links == ([product.path] if indexable else [])
    root = ET.parse(tmp_path / 'sitemap.xml').getroot()
    assert root.tag.endswith('sitemapindex')
    children = [n.text for n in root.findall('{*}sitemap/{*}loc')]
    assert children == ['https://wowpriced.com/sitemap-pages.xml', 'https://wowpriced.com/sitemap-products.xml']
    listed = [n.text for n in ET.parse(tmp_path / 'sitemap-products.xml').findall('{*}url/{*}loc')]
    assert listed == (['https://wowpriced.com' + product.path] if indexable else [])
    assert ('https://wowpriced.com/p/' in seo_audit.sitemap_urls(tmp_path, 'https://wowpriced.com')) is indexable
    robots = (tmp_path / 'robots.txt').read_text()
    assert robots.count('Sitemap:') == 1 and '/sitemap.xml' in robots
    for path in ['/', '/about.html', '/deal/' + result['public_deals'][0]['deal_path'].split('/')[2] + '/']:
        file = tmp_path / path.lstrip('/') if path.endswith('.html') else _page(tmp_path, path)
        footer = seo_audit.Page(file.read_text(), path).first(tag='footer')
        assert any(n.attrs.get('href') == '/p/' for n in footer.nodes())
    for _, _, slug in seo.HUB_GROUPS:
        assert 'href="/p/">Browse all price history' in _page(tmp_path, f'/deals/{slug}/').read_text()
    assert all(result['seo_audit'][k] == 0 for k in ('schema_mismatches', 'broken_internal_links', 'sitemap_missing', 'sitemap_extra'))
    before = tree(tmp_path)
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    assert tree(tmp_path) == before
    cfg.seo_product_pages_enabled = False
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    assert not (tmp_path / 'sitemap-products.xml').exists()
    assert not (tmp_path / 'sitemap-pages.xml').exists()
    assert not _page(tmp_path, '/p/').exists()
    assert ET.parse(tmp_path / 'sitemap.xml').getroot().tag.endswith('urlset')


@pytest.mark.parametrize('mutation', [
    lambda h: h.replace('property="og:title"', 'property="lost:title"'),
    lambda h: h.replace('name="twitter:card" content="summary_large_image"', 'name="twitter:card" content="invalid"'),
    lambda h: h.replace('"dateModified": "', '"dateModified": "broken'),
    lambda h: h.replace('class="last-check" datetime="', 'class="last-check" datetime="broken'),
    lambda h: h.replace('"@type": "BreadcrumbList"', '"@type": "MissingList"'),
    lambda h: h.replace('width="320"', 'width="0"'),
])
def test_audit_rejects_missing_or_invalid_head_dates_breadcrumb_and_image(db, cfg, tmp_path, mutation):
    _, page = render(db, cfg, tmp_path, seed(db, 1))
    assert not page.schema_mismatch()
    bad = mutation(_page(tmp_path, page.path).read_text())
    assert seo_audit.Page(bad, page.path).schema_mismatch()


@pytest.mark.parametrize('shipped', [False, True])
def test_static_reader_is_small_even_with_many_deals_and_checks(db, cfg, tmp_path, monkeypatch, shipped):
    if shipped:
        cfg = pre_history_removal(load_config())
        cfg.amazon_history = "show"  # predates task 121b; exercise the full Amazon reader
        cfg.seo_product_pages_enabled = cfg.paywall_open_access = True
    rows = seed(db, 1, checks=100)
    pages, _, _ = prepare(db, cfg, rows)
    page = next(iter(pages.values()))
    original = next(iter(page['offers'].values()))
    for i in range(100):
        page['offers'][f'old-{i}'] = dict(original, date=iso(NOW - timedelta(days=i)))
    state = json.loads(db.meta_get(product_pages.META))
    state['pages'][str(rows[0]['product_id'])] = {k: v for k, v in page.items() if k not in {'live', 'checks', 'statuses'}}
    db.meta_set(product_pages.META, json.dumps(state))
    def forbidden(*args, **kwargs):
        raise AssertionError('Product publishing must not call the network or LLM')
    monkeypatch.setattr(httpx.Client, 'send', forbidden)
    monkeypatch.setattr(httpx.AsyncClient, 'send', forbidden)
    monkeypatch.setattr(ClaudeParser, '_request', forbidden)
    result, parsed = render(db, cfg, tmp_path, rows)
    html = _page(tmp_path, parsed.path).read_bytes()
    # Permanent event links in the footer/related navigation add under 256 bytes.
    from dealradar.publish import dark
    dark_bytes = len((dark.META + dark.STYLE).encode()) if cfg.dark_polish else 0  # task 254's dark-only block
    assert len(html) < 40_000 + (256 if cfg.seo_event_pages_enabled else 0) + dark_bytes
    assert 'Showing the 20 most recent deals' in parsed.scope.text()
    assert parsed.first(cls='product-facts') and parsed.first(cls='price-verdict')
    assert b'wp.sup.' not in html and b'fetch("/deals.json' not in html
    assert not parsed.schema_mismatch()
    assert result['product_pages']['with_live_deal'] == 1


def test_sitemap_audit_follows_local_children_and_detects_missing_or_extra_urls(db, cfg, tmp_path):
    _, page = render(db, cfg, tmp_path, seed(db, 1))
    path = tmp_path / 'sitemap-products.xml'
    path.write_text(path.read_text().replace(page.path, '/p/invented/'))
    audit = seo_audit.audit(tmp_path, cfg, 'https://wowpriced.com')
    assert audit['sitemap_missing'] == audit['sitemap_extra'] == 1
    path.unlink()
    assert seo_audit.audit(tmp_path, cfg, 'https://wowpriced.com')['sitemap_urls'] is None


def test_site_check_reads_split_sitemap_and_preserves_footer_validation():
    if not shutil.which('node'):
        pytest.skip('node unavailable')
    script = r'''
const assert = require('node:assert/strict'), fs = require('node:fs');
const source = fs.readFileSync('scripts/site_check.js', 'utf8');
const code = 'let dealPage; await step(8,' + source.split('await step(8,')[1].split('await step(9,')[0];
const run = new (Object.getPrototypeOf(async function(){}).constructor)(
  'step', 'readSite', 'check', 'SITE', 'SITE_URL', 'checkFooter', 'doc', code);
const site = 'https://wowpriced.com/';
const children = ['sitemap-pages.xml', 'sitemap-products.xml'];
const root = '<sitemapindex>' + children.map(p => `<sitemap><loc>${site}${p}</loc></sitemap>`).join('') + '</sitemapindex>';
const paths = ['deals/electronics/', 'deals/home-kitchen/', 'deals/pets/', 'best-deals-today/'];
const pages = '<urlset>' + paths.map(p => `<url><loc>${site}${p}</loc></url>`).join('') + '</urlset>';
(async () => {
  const requested = [], problems = [];
  await run(async (n, label, fn) => {try {await fn();} catch(e) {problems.push(e.message);}},
    async p => {
      const path = new URL(p, site).pathname;
      requested.push(path);
      const body = path === '/sitemap.xml' ? root : path === '/sitemap-pages.xml' ? pages :
        path === '/sitemap-products.xml' ? '<urlset><url><loc>https://wowpriced.com/p/widget/</loc></url></urlset>' :
        path === '/robots.txt' ? 'User-agent: *' : '<title>Public page</title>';
      return {status:200, text:async () => body};
    }, (ok, why) => {if (!ok) throw new Error(why);}, site, new URL(site), () => {},
    {meta:{}, deals:[{deal_path:'/deal/widget-12345678/'}]});
  assert.deepEqual(problems, []);
  for (const path of children) assert.equal(requested.filter(p => p === '/' + path).length, 1);
  for (const path of paths.filter(p => p.startsWith('deals/'))) assert(requested.includes('/' + path));
  assert(!requested.includes('/p/widget/'));
  const footerCode = source.slice(source.indexOf('function checkFooter('), source.indexOf('\nasync function ', source.indexOf('function checkFooter(')));
  const checkFooter = new Function('check', footerCode + ';return checkFooter;')((ok, why) => {if (!ok) throw new Error(why);});
  const links = [['/about.html','About'],['/how-it-works.html','How we verify'],['/hacks.html','Deal hacks'],
    ['/careers.html','Work with us'],['/contact.html','Contact'],['/subscribe.html','Join &amp; support'],
    ['/privacy.html','Privacy'],['/disclosure.html','Disclosure']];
  const footer = (extra, at = 3) => '<footer><nav id="footerlinks">© 2026 wowpriced' +
    [...links.slice(0, at), ...extra, ...links.slice(at)].map(([u,t]) => `<a href="${u}">${t}</a>`).join('') +
    '</nav><p><span id="metaline">Disclosure <a href="/disclosure.html">Disclosure</a></span></p></footer>';
  const meta = {generated_at:'2026-10-09', disclosure:'Disclosure'};
  for (const extra of [[], [['/p/','Price history']]]) checkFooter(footer(extra), meta);  // after Deal hacks
  assert.throws(() => checkFooter(footer([['/bad/','Price history']]), meta));
  assert.throws(() => checkFooter(footer([['/p/','Price history']], 0), meta));
})().catch(e => {console.error(e); process.exitCode = 1;});
'''
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 0, result.stdout + result.stderr


def test_truncated_history_cannot_supply_an_invisible_photo_credit(db, cfg, tmp_path, monkeypatch):
    rows = seed(db, 1)
    pages, _, _ = prepare(db, cfg, rows)
    model = next(iter(pages.values()))
    owner = 'https://owner.test/post'
    original = next(iter(model['offers'].values()))
    model['offers']['older-owner'] = dict(original, date=iso(NOW - timedelta(days=50)),
                                         credits=[dict(name='Owner', url=owner)])
    state = json.loads(db.meta_get(product_pages.META))
    state['pages'][str(rows[0]['product_id'])]['offers'] = model['offers']
    db.meta_set(product_pages.META, json.dumps(state))
    cfg.seo_product_pages_deal_history_limit = 1
    monkeypatch.setattr(product_pages, 'source_image_allowed',
                        lambda image, credits, cfg: any(c['source_url'] == owner for c in credits))
    _, page = render(db, cfg, tmp_path, rows)
    assert page.first(tag='img') is None
    assert 'og:image' not in _page(tmp_path, page.path).read_text()
    assert not page.schema_mismatch()


def test_reader_text_and_controls_meet_aa_in_both_themes():
    def luminance(color):
        if len(color) == 4:
            color = '#' + ''.join(c * 2 for c in color[1:])
        rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        return sum(w * (v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4)
                   for v, w in zip(rgb, [.2126, .7152, .0722]))
    roots = re.findall(r':root\{([^}]+)\}', product_pages.READER_STYLE)
    assert len(roots) == 2
    for root in roots:
        colors = dict(re.findall(r'--([\w-]+):(#[\da-f]+)', root))
        for foreground, background in [('fg', 'bg'), ('muted', 'bg'), ('link', 'bg'),
                                      ('fg', 'card'), ('accent-fg', 'accent')]:
            values = sorted([luminance(colors[foreground]), luminance(colors[background])])
            assert (values[1] + .05) / (values[0] + .05) >= 4.5
    assert 'a,button{min-height:44px;min-width:44px}' in product_pages.READER_STYLE


def test_rerun_with_double_digit_product_ids_keeps_every_published_byte(db, cfg, tmp_path):
    rows = seed(db, 12)
    first = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    before, state = tree(tmp_path), db.meta_get(product_pages.META)
    again = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    assert tree(tmp_path) == before
    assert db.meta_get(product_pages.META) == state
    assert first['product_pages'] == again['product_pages']
    assert first['product_pages']['related_links_avg'] == 6


def test_history_list_limit_loads_from_config(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('seo:\n  product_pages:\n    deal_history_limit: 4\n')
    assert load_config(path).seo_product_pages_deal_history_limit == 4


def test_lifetime_extremes_do_not_relax_the_existing_ninety_day_gate(db, cfg):
    rows = seed(db, 1, checks=5)
    db.record_price(rows[0]['dedupe_key'], 'Amazon', 1, NOW - timedelta(days=120), 'outside-gate')
    pages, _, report = prepare(db, cfg, rows)
    page = next(iter(pages.values()))
    assert len(page['checks']) == 6
    assert report['eligible'] == report['indexable'] == 0
    assert not page['indexable']


def test_related_index_matches_a_full_scan():
    """The per-publish index (review 2026-10-15: the full scan was quadratic)
    picks exactly what scanning every page would."""
    import random
    rng = random.Random(118)

    def scan(page, pages):
        brand = str(page.get('brand') or '').strip().casefold()
        rows = [p for p in pages.values() if p['path'] != page['path'] and p['indexable']
                and ((page.get('category') and page['category'] == p.get('category'))
                     or (brand and brand == str(p.get('brand') or '').strip().casefold()))]
        return sorted(rows, key=lambda p: (p.get('category') != page.get('category'), p['path']))[:6]

    for _ in range(200):
        pages = {str(i): {'path': f'/p/x-{rng.randrange(1000):03d}-{i}/', 'indexable': rng.random() < .7,
                          'category': rng.choice([None, '', 'home', 'kitchen', 'toys']),
                          'brand': rng.choice([None, '', 'Acme', ' acme ', 'Zed'])}
                 for i in range(rng.randrange(1, 30))}
        index = product_pages.related_index(pages)
        for page in pages.values():
            assert product_pages.related(page, pages, index) == scan(page, pages)
            assert product_pages.related(page, pages) == scan(page, pages)


def test_reader_pages_keep_get_deal_analytics(db, cfg, tmp_path):
    """The static reader drops page scripts but keeps the deal_click key event."""
    _, page = render(db, cfg, tmp_path, seed(db, 1))
    html = _page(tmp_path, page.path).read_text()
    assert html.count(product_pages.CLICK_SCRIPT) == 1 and 'data-deal-outbound' in html
    if not shutil.which('node'):
        pytest.skip('node unavailable')
    script = '''
var events = [], handlers = {};
global.wpTrack = (n, p) => events.push([n, p]);
global.document = {addEventListener: (t, f) => { handlers[t] = f; }};
''' + product_pages.CLICK_SCRIPT.removeprefix('<script>').removesuffix('</script>') + '''
const a = {getAttribute: n => ({'data-store': 'Amazon', 'data-category': 'kitchen'})[n],
           hasAttribute: n => n === 'data-step-offer'};
const ev = (button, type) => ({type, button, target: {closest: () => a}});
handlers.click(ev(0, 'click')); handlers.auxclick(ev(1, 'auxclick')); handlers.click(ev(2, 'click'));
console.log(JSON.stringify(events));
'''
    out = json.loads(subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True).stdout)
    click = ['deal_click', {'store': 'Amazon', 'category': 'kitchen', 'method': 'home'}]
    step = ['requirements_deal_click', {'method': 'home'}]
    assert out == [click, step, click, step]
