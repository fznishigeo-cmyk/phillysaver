"""Task 366: publication rollback, public facts, shared helpers and browser QA."""
import json
import re
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config, validated_deal_spotter
from dealradar.publish import deal_spotter, site
from dealradar.publish.seo_audit import Page
from tests.history_pins import pre_deal_spotter, pre_review_checker
from tests.test_home_comment_strip import files
from tests.test_pwa import tree_hash
from tests.test_support import NOW, render


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(site, 'util_now', lambda: NOW)


def test_config_ships_on_defaults_off(tmp_path):
    assert load_config().deal_spotter == dict(enabled=True, a_max_avg_ratio=.85, b_max_avg_ratio=.97,
                                             b_min_list_pct=20, verified_max_ratio=1.02, single_source_ok=True)
    assert AppConfig().deal_spotter['single_source_ok'] is False  # previous rule: 2+ sources
    path = tmp_path / 'empty.yaml'; path.write_text('{}')
    assert load_config(path).deal_spotter == AppConfig().deal_spotter
    assert AppConfig().deal_spotter['enabled'] is False


@pytest.mark.parametrize('value', [False, None, [], 'on', {'enabled':'true'}, {'enabled':1}, {'typo':True},
    {'a_max_avg_ratio':float('nan')}, {'b_max_avg_ratio':float('inf')}, {'a_max_avg_ratio':True},
    {'a_max_avg_ratio':1.01}, {'a_max_avg_ratio':.99}, {'b_min_list_pct':-1}, {'verified_max_ratio':2},
    {'single_source_ok':'true'}, {'single_source_ok':1}])
def test_config_rejects_invalid_thresholds(value):
    with pytest.raises(ValueError, match='deal_spotter'):
        validated_deal_spotter(value)


@pytest.mark.parametrize('mode,expected', [
    ('shipped','dcccf93f1d7c69045ed7492697dcde499dd5369372d8eb6adc697a9a6751b037'),
    ('inline','533ce78cbd16b6a7fa8aeaf3b90cf24fdcfeb1675935808043ce440a425b0f54'),
    ('membership','5ab3af0ff2d595e95f14e36f5aff03a4b001552b9db6d393a1fe1ee63b2f218f')])
def test_off_missing_back_to_off_bytes_and_idempotence(tmp_path, mode, expected):
    # Review Checker (367) is pinned OFF too: these are the recorded pre-tool bytes.
    cfg = pre_review_checker(pre_deal_spotter(load_config()))
    if mode == 'inline':
        cfg.search_analyze['lazy'] = False; cfg.strip_comments = False
    if mode == 'membership':
        cfg.support_enabled = False
    out, *_ = render(tmp_path, cfg)
    assert tree_hash(out) == expected
    before = files(out)
    cfg.deal_spotter = validated_deal_spotter({'enabled':True})
    render(tmp_path, cfg); after = files(out)
    assert after != before
    # Existing home/deal cards and all assets remain exact; only footer discovery changes.
    footer_links = b' \xc2\xb7 <a href="/deal-spotter/">Fake Deal Spotter</a> \xc2\xb7 <a href="/fakespot-alternative/">Fakespot alternative</a>'
    assert after['index.html'].replace(footer_links,b'') == before['index.html']
    assert after['deals.json'] == before['deals.json']
    render(tmp_path, cfg); assert files(out) == after
    for setting in ({'enabled':False}, {}):
        cfg.deal_spotter = setting
        render(tmp_path, cfg); assert files(out) == before
    del cfg.deal_spotter
    render(tmp_path, cfg); assert files(out) == before


def test_both_pages_metadata_schema_copy_assets_and_menu(tmp_path):
    out, *_ = render(tmp_path, load_config())
    sitemap = ''.join(path.read_text() for path in out.glob('sitemap*.xml'))
    for slug in deal_spotter.SLUGS:
        html = (out / slug / 'index.html').read_text()
        page = Page(html, '/' + slug + '/')
        assert 'https://wowpriced.com/' + slug + '/' in sitemap
        assert page.indexable
        assert '<link rel="canonical" href="https://wowpriced.com/' + slug + '/">' in html
        assert '{{' not in html
        assert 'id="cookie-bar"' in html
        assert 'Microsoft may receive' in html
        assert all(url in html for url, _ in deal_spotter.NAV)
        assert 'As an Amazon Associate' in html
        assert not re.search(r'avg90|90.day|price history|lowest|overpriced', html, re.I)
        for img in re.findall(r'<img\b[^>]*>',html):
            assert 'width=' in img and 'height=' in img and 'alt=' in img
    html = (out / 'deal-spotter/index.html').read_text()
    page = Page(html,'/deal-spotter/')
    faq = next(schema for schema in page.schemas if schema['@type']=='FAQPage')
    assert [q['name'] for q in faq['mainEntity']] == [q for q,a in deal_spotter.FAQ]
    for q,a in deal_spotter.FAQ:
        assert a in page.root.text()
    seo = (out / 'fakespot-alternative/index.html').read_text()
    assert 'Fakespot Alternative: Free Amazon Deal Grader | wowpriced' in seo
    assert 'July 1, 2025' in seo and 'does not read reviews' in seo
    assert 'by Mozilla' not in html
    # Operator 2026-10-22: Fakespot's layout, our own words and no proof we can't back up.
    text = page.root.text()
    assert 'fakespot' not in text.casefold().replace('fakespot alternative', '')
    assert not re.search(r'as mentioned|as seen|trusted by|million|testimonial|★', text, re.I)
    assert text.index('How it works') < text.index('Want to see it in action?') < text.index('deal guide') < text.index('Step one') < text.index('Step two')
    config = json.loads((out / 'search-config.json').read_text())
    assert config['deal_spotter'] == load_config().deal_spotter
    assert config['tokens_per_day'] == 8000 and config['max_per_minute'] == 4
    for slug in ('amazon-deal-checker','amazon-discount-finder'):
        html = (out / slug / 'index.html').read_text()
        assert '<details class="spotter-menu">' in html
        assert '/deal-spotter/' in html and '/review-checker/' in html


def test_live_counts_and_cards_use_only_published_facts():
    rows = [dict(key=str(i),status='alive',badge='verified',final_price=20+i,regular_price=50,
             regular_price_basis='List price',item='Published item '+str(i),store='Amazon',
             image_url='https://m.media-amazon.com/images/I/photo.jpg',
             url='https://www.amazon.com/dp/B0'+str(i).zfill(8)+'?tag=example-20',
             deal_path='/deal/fixture-'+str(i)+'/') for i in range(5)]
    rows[0].update(needs_steps=True)
    invalid = [{**rows[0], 'key':'invalid', **change} for change in
               ({'status':'dead'},{'locked':True},{'price_unconfirmed':True},
                {'price_aged':True},{'click_price_unknown':True})]
    live = deal_spotter.live_rows({'deals':rows+invalid,'spotlight':[
        {**rows[0],'key':'expired','spotlight':{'until':'2020-01-01'}},
        {**rows[0],'key':'just-ended','spotlight':{'until':NOW.isoformat().replace('+00:00','Z')}}]},NOW)
    assert live == rows
    # Every published deal is price-checked; the badge only counts sources (live 2026-10-22: 0 rows).
    for badge in ('unverified', 'corroborated', None):
        assert deal_spotter.live_rows({'deals': [{**rows[1], 'badge': badge}]}, NOW) == [{**rows[1], 'badge': badge}]
    cards = deal_spotter.cards(live)
    assert cards.count('<article') == 4
    assert cards.count('loading="lazy"') == 4
    assert cards.count('?tag=example-20') == 4
    assert 'How to get this price' in cards and '$20.00' in cards
    assert '60% off list price' in cards
    rows[0]['regular_price_basis']='Private basis'
    assert 'Published item 0' not in deal_spotter.cards(rows)


def test_price_index_keeps_only_qualifying_amazon_rows_and_the_fields_the_grade_reads():
    ok = {'key': 'k1', 'status': 'alive', 'badge': 'verified', 'final_price': 19.99, 'headline': 'Deal',
          'url': 'https://www.amazon.com/dp/B0D8KZHN1Q?tag=x-20', 'item': 'Headphones', 'image_url': 'https://x/y.jpg'}
    rows = [ok, {**ok, 'url': 'https://www.walmart.com/ip/1'}, {**ok, 'price_unconfirmed': True},
            {**ok, 'status': 'dead'}, {**ok, 'locked': True},
            {**ok, 'url': 'https://amazon.com.evil.test/dp/B0D8KZHN1Q'}, {**ok, 'headline': 'Price not yet confirmed'}]
    spot = {**ok, 'spotlight': {'until': '2026-10-22T05:00:00Z', 'title': 'x'}}
    index = deal_spotter.price_index({'deals': rows, 'spotlight': [spot, {**spot, 'price_aged': True}]})
    assert deal_spotter.price_index({'deals': [{**ok, 'badge': 'unverified'}]})['deals'] == [{k: ok[k] for k in ('status', 'final_price', 'url', 'headline')}]
    assert index == {'deals': [{k: ok[k] for k in ('status', 'final_price', 'url', 'headline')}],
                     'spotlight': [{**{k: ok[k] for k in ('status', 'final_price', 'url', 'headline')},
                                    'spotlight': {'until': '2026-10-22T05:00:00Z'}}]}


def test_prices_file_is_published_on_and_removed_off(tmp_path, monkeypatch):
    monkeypatch.setattr(site, 'util_now', lambda: NOW)
    cfg = load_config()
    out, *_ = render(tmp_path, cfg)
    doc = json.loads((out / 'deal-spotter-prices.json').read_text())
    assert set(doc) == {'deals', 'spotlight', 'single_source_ok'} and doc['single_source_ok'] is True  # shipped (A30)
    assert len((out / 'deal-spotter-prices.json').read_bytes()) < len((out / 'deals.json').read_bytes())
    cfg.deal_spotter = {'enabled': False}
    render(tmp_path, cfg)
    assert not (out / 'deal-spotter-prices.json').exists()


def test_emitted_javascript_parsing_and_shared_links(tmp_path):
    cfg = load_config(); cfg.affiliate_amazon_tag='example-20'
    path = tmp_path / 'deal-spotter.js'; path.write_text(deal_spotter.script(cfg))
    for args in (['node','--check',str(path)],['node','tests/deal_spotter_logic.cjs',str(path)],
                 ['node','--check','tests/deal_spotter_browser.cjs']):
        result = subprocess.run(args,capture_output=True,text=True,timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr


def test_shipped_phone_budget(tmp_path):
    from scripts.publication_budget import analyze
    from tests.phone_budget_fixture import publish_fixture, visible_offer_count
    cfg = load_config(); cfg.phone_budget['enabled']=True
    def inspect(db,out):
        report = analyze(out,cfg,expected_offers=visible_offer_count(cfg))
        assert report['ok'],report
    publish_fixture(tmp_path,cfg,inspect=inspect)


def test_real_chromium_all_states_and_sizes(tmp_path):
    result = subprocess.run(['node','tests/deal_spotter_browser.cjs',str(tmp_path)],
                            capture_output=True,text=True,timeout=240)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0,result.stdout+result.stderr
    assert len(list(tmp_path.rglob('*.png'))) >= 56


def test_one_legitimate_source_counts_only_when_switched_on():
    """A30 (2026-10-22): "1 source is fine.. if its a legitimate source". Our own
    Amazon price checks still decide; only the 2-source headline rule is relaxed."""
    ok = {'key': 'k1', 'status': 'alive', 'badge': 'unverified', 'final_price': 19.99,
          'headline': 'Was $40. Reported by one source — not yet confirmed.',
          'url': 'https://www.amazon.com/dp/B0D8KZHN1Q?tag=x-20'}
    checked_out = [{**ok, 'key': 'k' + k, k: True} for k in ('price_unconfirmed', 'price_aged', 'click_price_unknown')]
    doc = {'deals': [ok, *checked_out]}
    assert deal_spotter.live_rows(doc, NOW) == []
    assert deal_spotter.live_rows(doc, NOW, single_source_ok=True) == [ok]
    assert deal_spotter.price_index(doc)['deals'] == []
    index = deal_spotter.price_index(doc, single_source_ok=True)
    assert index['single_source_ok'] is True
    assert index['deals'] == [{k: ok[k] for k in ('status', 'final_price', 'url', 'headline')}]
    assert 'single_source_ok' not in deal_spotter.price_index(doc)


def test_store_row_names_only_stores_in_todays_deals():
    from dealradar.publish.deal_spotter import stores_row
    assert stores_row([{'store': 'Amazon'}] * 3) == ''  # one store: no row
    row = stores_row([{'store': 'Amazon'}] * 3 + [{'store': 'Woot'}, {'store': 'Walmart'}] * 2
                     + [{'store': 'A&B <x>'}, {'store': ' '}, {'store': None}, {}], limit=3)
    assert row.count('<span>') == 3 and row.index('Amazon') < row.index('Walmart') < row.index('Woot')
    assert 'A&B' not in row and '<x>' not in row and "Today's deals come from" in row
    assert '&lt;x&gt;' in stores_row([{'store': 'A&B <x>'}, {'store': 'Amazon'}])


def test_footer_icons_are_real_marks_and_only_for_configured_profiles(tmp_path):
    # Operator 2026-10-22: the text glyphs "didn't work and looked fake"; never show an unlinked icon.
    for name, profiles in (('none', {}), ('facebook', {'facebook': 'https://www.facebook.com/Wowpriced'})):
        cfg = load_config(); cfg.social_profiles = profiles; (tmp_path / name).mkdir()
        out, *_ = render(tmp_path / name, cfg)
        for page in ('deal-spotter', 'review-checker'):
            html = (out / page / 'index.html').read_text()
            footer = re.search(r'<footer class="(?:spotter|review)-footer".*?</footer>', html, re.S)[0]
            assert '◎' not in html and 'coming soon' not in footer
            assert 'aria-label="Instagram"' not in footer and 'aria-label="Pinterest"' not in footer
            if profiles:
                assert re.search(r'<a class="social-facebook" href="https://www.facebook.com/Wowpriced" rel="noopener" '
                                 r'aria-label="Facebook"><svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true"', footer)
            else:
                assert 'class="social-' not in footer and 'aria-label="Facebook"' not in footer
                assert 'social-icons' not in footer and 'Follow us:' not in footer
            if page == 'deal-spotter':
                assert 'A fresh way to check prices' in footer
