"""Task 339: exact rollback, honest tool copy/clouds, home discovery and timing."""
import json
import re
import subprocess
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import tool_hero, tool_pages, tool_pages_1020 as feature
from dealradar.publish.seo_audit import Page
from scripts.publication_budget import analyze
from tests.history_pins import pre_tool_pages_1020
from tests.phone_budget_fixture import publish_fixture, visible_offer_count
from tests.test_home_comment_strip import files
from tests.test_pwa import tree_hash
from tests.test_support import render
from tests.test_tool_pages import offline  # noqa: F401
from tests.test_tool_pages_hero import candidate, rows

# Pins = the 332 + 337b release (21d30cd) bytes; the first recording was taken on an
# unreleased 332 draft. Verified equal to 21d30cd's own output before re-pinning.
PINS = json.loads(Path('tests/fixtures/tool_pages_1020_off.json').read_text())


def test_defaults_and_shipped(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')
    for cfg in (AppConfig(), load_config(path)):
        assert cfg.tool_pages_1020 is False
        assert cfg.tool_pages_1020_phone_links == 'chips'
    assert load_config().tool_pages_1020 is True
    assert load_config().tool_pages_1020_phone_links == 'chips'
    assert not feature.enabled(type('Config', (), {'tool_pages_1020': 1})())
    for flag in (False, True):
        for variant in ('chips', 'line'):
            path.write_text(json.dumps(dict(tool_pages_1020=flag, tool_pages_1020_phone_links=variant)))
            cfg = load_config(path)
            assert cfg.tool_pages_1020 is flag and cfg.tool_pages_1020_phone_links == variant


@pytest.mark.parametrize('key,values', [('tool_pages_1020', [None, 0, 1, 'true', 'false', [], {}]),
    ('tool_pages_1020_phone_links', [None, 0, 1, True, False, 'CHIPS', '', 'other', [], {}]),
    ('tool_pages_1020_home_links', [None, 0, 1, 'true', 'false', [], {}])])
def test_strict_knobs(tmp_path, key, values):
    path = tmp_path / 'config.yaml'
    for value in values:
        path.write_text(json.dumps({key: value}))
        with pytest.raises(ValueError, match='^' + key + ' must be'):
            load_config(path)


@pytest.mark.parametrize('mode', PINS)
def test_off_missing_back_preserve_pre_edit_bytes(tmp_path, mode):
    cfg = pre_tool_pages_1020(load_config())
    if mode == 'inline':
        cfg.search_analyze['lazy'] = False
        cfg.strip_comments = False
    if mode == 'membership':
        cfg.support_enabled = False
    out, *_ = render(tmp_path, cfg, rows())
    assert tree_hash(out) == PINS[mode]  # Captured before this task's publisher edits; never re-pin.
    before = files(out)
    del cfg.tool_pages_1020
    render(tmp_path, cfg, rows())
    assert files(out) == before
    cfg.tool_pages_1020 = True
    render(tmp_path, cfg, rows())
    assert files(out) != before
    cfg.tool_pages_1020 = False
    render(tmp_path, cfg, rows())
    assert files(out) == before


@pytest.mark.parametrize('hero', [False, True])
def test_on_copy_no_meter_faq_and_published_clouds(tmp_path, hero):
    cfg = load_config()
    cfg.tool_pages_hero = hero
    out, doc, *_ = render(tmp_path, cfg, rows())
    css, js = ((out / ('tool-pages.' + ext)).read_text() for ext in ('css', 'js'))
    assert 'transition:none' in css and 'requestAnimationFrame' in js
    assert 'scrollIntoView' not in js and '.focus({preventScroll:true})' in js
    assert 'width:85%' in css
    picks = {}
    for slug, spec in tool_pages.PAGES.items():
        html = (out / slug / 'index.html').read_text()
        page = Page(html, '/' + slug + '/')
        assert page.first('title').text() == feature.page_spec(spec)['title']
        assert spec['name'] in page.first('h1').text()
        assert page.first('h1').text().startswith(feature.LEADS[spec['mode']])
        for text in (html, css, js):
            assert not re.search(r'Big discount|Good discount|gauge|meter-needle|meter-card|toolGauge|toolObserveMeters', text)
        visible = page.first(cls='faq')
        questions = next(s for s in page.schemas if s['@type'] == 'FAQPage')['mainEntity']
        assert len(questions) == 6
        assert [n.text() for n in visible.nodes() if n.tag == 'h3'] == [q['name'] for q in questions]
        paragraphs = [n.text() for n in visible.nodes() if n.tag == 'p']
        assert paragraphs[:-2] == [q['acceptedAnswer']['text'] for q in questions[:-1]]
        assert questions[-1]['name'] == 'Is it really AI?'
        # The visible four-step list and the JSON-LD answer carry the same words.
        assert paragraphs[-2:] == [feature.AI_LEAD, feature.AI_CLOSE[spec['mode']]]
        assert [n.text() for n in visible.nodes() if n.tag == 'li'] == list(feature.AI_STEPS)
        assert questions[-1]['acceptedAnswer']['text'] == feature.ai_answer(spec['mode'])
        assert 'thousands' not in questions[-1]['acceptedAnswer']['text']
        cloud = page.first(cls='cloud-wrap')
        picks[spec['mode']] = {n.attrs['href'] for n in cloud.nodes() if n.tag == 'a'}
        assert len(picks[spec['mode']]) == 6
        public = {d['deal_path']: d for d in tool_hero.candidates(doc['deals'], cfg)}
        for href in picks[spec['mode']]:
            assert href in public and public[href]['image_url'] in html
    assert picks['checker'] != picks['finder']
    assert len(picks['checker'] - picks['finder']) == 2


def test_distinct_cloud_and_sparse_hide():
    cfg = load_config()
    spec = tool_pages.PAGES['amazon-deal-checker']
    base = (tool_pages.TEMPLATES / 'tool-page.html').read_text()
    deals = [candidate() | dict(key=str(i), deal_path=f'/deal/kettle-{i:08d}/') for i in range(12)]
    checker = feature.template(base, spec, deals)
    finder = tool_hero.finder(deals)
    assert set(re.findall(r'href="(/deal/[^\"]+)"', checker)).isdisjoint(re.findall(r'href="(/deal/[^\"]+)"', finder))
    assert 'class="cloud"' not in feature.template(base, spec, deals[:3])
    assert feature.template(base, spec, deals[:4]).count('class="tile tile-') == 4


def test_shipped_home_has_no_tool_links(tmp_path):
    # Operator 2026-10-20: the homepage buttons lead to the tools; no extra links.
    cfg = load_config()
    assert cfg.tool_pages_1020 is True and cfg.tool_pages_1020_home_links is False
    assert AppConfig().tool_pages_1020_home_links is False
    out, *_ = render(tmp_path, cfg, rows())
    assert 'class="home-tools' not in (out / 'index.html').read_text()
    assert not (out / feature.ASSET).exists()


@pytest.mark.parametrize('variant', ['chips', 'line'])
def test_home_links_public_scope_variants_and_idempotence(tmp_path, variant):
    cfg = load_config()
    cfg.tool_pages_1020_home_links = True
    cfg.tool_pages_1020_phone_links = variant
    out, *_ = render(tmp_path, cfg, rows())
    html = (out / 'index.html').read_text()
    page = Page(html, '/')
    ticker = page.first(cls='ticker-tools')
    phone = page.first(cls='phone-tools')
    expected = {'/' + slug + '/': label for slug, label, _ in feature.LINKS}
    for nav in (ticker, phone):
        assert {n.attrs['href']: n.text() for n in nav.nodes() if n.tag == 'a'} == expected
    assert ticker in page.first(ident='tkband').nodes()
    if variant == 'chips':
        assert phone in page.first(ident='cattabs').nodes()
        # The SEO parser intentionally omits SVG subtrees.
        chips = re.search(r'<span role="group" class="home-tools phone-tools".*?</span>', html, re.S)[0]
        assert chips.count('<svg ') == 2
        assert '.join("") + "<span role=' in html
    else:
        assert 'tool-line' in html and '</header><nav class="home-tools phone-tools tool-line"' in html
    css = (out / feature.ASSET).read_text()
    assert '@media(max-width:899px)' in css and 'min-height:44px' in css
    assert 'height:28px' in css
    inline_css = ''.join(re.findall(r'<style\b[^>]*>(.*?)</style>', html, re.S))
    assert not re.search(r'\.(?:zerotoday|trialband)\b', inline_css)
    assert '.getdeal,.cattab,.plans label,.toast{min-width:44px;min-height:44px}' in inline_css
    # The 332 search handlers/labels survive this page-only addition.
    assert 'AI Analysis</span>' in html and 'rh=p_n_deal_type%3A210906366011' in html
    before = files(out)
    render(tmp_path, cfg, rows())
    assert files(out) == before
    cfg.tool_pages['public'] = False
    render(tmp_path, cfg, rows())
    assert not (out / feature.ASSET).exists()
    assert 'class="home-tools' not in (out / 'index.html').read_text()


@pytest.mark.parametrize('variant', ['chips', 'line'])
def test_phone_gzip_reserve(tmp_path, variant):
    cfg = load_config()
    cfg.phone_budget['enabled'] = True
    cfg.tool_pages_1020_home_links = True
    cfg.tool_pages_1020_phone_links = variant
    def inspect(db, out):
        report = analyze(out, cfg, expected_offers=visible_offer_count(cfg))
        assert report['ok'], report
        size = report['metrics']['home_html']['gzip']
        headroom = cfg.phone_budget['home_html_gzip_max_bytes'] - size
        print(f'{variant}: home HTML gzip {size}; headroom {headroom}')
        assert headroom >= 1000
    publish_fixture(tmp_path, cfg, inspect=inspect)


def test_emitted_timing_and_browser_syntax(tmp_path):
    cfg = load_config()
    cfg.affiliate_amazon_tag = 'example-20'
    out, *_ = render(tmp_path, cfg, rows())
    for args in (['node', '--check', str(out / 'tool-pages.js')],
                 ['node', 'tests/tool_pages_1020_logic.cjs', str(out / 'tool-pages.js')],
                 ['node', '--check', 'tests/tool_pages_browser.cjs'],
                 ['node', '--check', 'tests/tool_pages_1020_home_browser.cjs']):
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
