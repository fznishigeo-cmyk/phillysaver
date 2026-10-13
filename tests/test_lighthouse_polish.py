"""Offline checks for published contrast, heading structure and paint work."""
import colorsys
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from dealradar.config import AppConfig
from tests.test_publish_site import make_deal
from tests.test_support import render


def rgb(value):
    value = value.lstrip('#')
    if len(value) == 3:
        value = ''.join(c * 2 for c in value)
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def contrast(fg, bg):
    """WCAG relative luminance, without rounding at the pass boundary."""
    def luminance(color):
        return sum(w * (v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4)
                   for w, v in zip((.2126, .7152, .0722), color))
    lo, hi = sorted((luminance(fg), luminance(bg)))
    return (hi + .05) / (lo + .05)


def composite(fg, bg, alpha):
    return tuple(alpha * f + (1 - alpha) * b for f, b in zip(fg, bg))


def themes(html):
    css = '\n'.join(re.findall(r'<style>(.*?)</style>', html, re.S))
    roots = re.findall(r':root\s*\{([^}]*)\}', css)
    read = lambda block: dict(re.findall(r'--([\w-]+)\s*:\s*(#[\da-fA-F]{3,6})\b', block))
    light = read(roots[0])
    dark = light | read(roots[1])
    return css, (light, dark)


def phone_blocks(css):
    blocks = []
    for match in re.finditer(r'@media \(max-width:(640|520|899|360)px\)\{', css):
        depth, end = 1, match.end()
        while depth:
            depth += (css[end] == '{') - (css[end] == '}')
            end += 1
        blocks.append((int(match[1]), css[match.end():end - 1]))
    return blocks


def phone_rule(css, selector):
    # Collect repeated phone declarations in cascade order, including the
    # shared Join/footer CSS appended by the publisher.
    rules = []
    for width, block in phone_blocks(css):
        if width != 640:
            continue
        block = re.sub(r'/\*.*?\*/', '', block, flags=re.S)
        for selectors, declarations in re.findall(r'([^{}]+)\{([^{}]*)\}', block):
            if selector in [s.strip() for s in selectors.split(',')]:
                rules.append(declarations)
    assert rules, selector
    return ';'.join(rules)


class Outline(HTMLParser):
    def __init__(self):
        super().__init__()
        self.headings = []
        self.ignored = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.ignored = True
        if re.fullmatch('h[1-6]', tag) and not self.ignored:
            self.headings.append(int(tag[1]))

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.ignored = False


def assert_outline(html, name):
    outline = Outline()
    outline.feed(html)
    levels = outline.headings
    assert levels.count(1) == 1, (name, levels)
    assert levels[0] == 1, (name, levels)
    assert all(b <= a + 1 for a, b in zip(levels, levels[1:])), (name, levels)


@pytest.fixture(params=['support', 'open', 'paywall', 'no-engagement'])
def published(tmp_path, request):
    cfg = AppConfig(support_enabled=request.param in ('support', 'no-engagement'),
                    support_payment_link='https://buy.stripe.com/test_support',
                    paywall_enabled=True, paywall_open_access=request.param == 'open',
                    paywall_teaser_count=12, seo_store_page_min_deals=1, paywall_api_url='https://api.example')
    if request.param == 'no-engagement':
        cfg.engagement = {}
    deals = [make_deal(dedupe_key=f'polish-{i}', store=('Target', 'Walmart')[i % 2],
                       category='electronics', final_price=9, conditions=['used'],
                       image_url=f'https://img.example/{i}.jpg') for i in range(18)]
    out, _, _, _ = render(tmp_path, cfg, deals)
    return out


def test_heading_order_in_every_rendered_page_including_join_prompt(published):
    files = list(published.rglob('*.html'))
    assert any('stores/' in str(p) for p in files)
    assert any('under-10' in str(p) for p in files)
    assert any('/deal/' in str(p) for p in files)
    for path in files:
        assert_outline(path.read_text(), path.relative_to(published))
    for name, level in (('index.html', 2), ('join.html', 1)):
        if not (published / name).exists():
            continue
        html = (published / name).read_text()
        if 'class="joincard"' in html:
            assert f'<h{level} id="join-title">' in html


def test_css_text_pairs_pass_in_both_themes(published):
    # All these tokens are used for text, including Join descriptions/legal
    # copy, placeholders, card chips/credits, status messages and badges.
    for path in published.rglob('*.html'):
        css, palettes = themes(path.read_text())
        for palette in palettes:
            for text in ('fg', 'muted', 'link', 'accent', 'good', 'hist', 'unv', 'warn', 'bad', 'dead'):
                if text not in palette:
                    continue
                for background in ('bg', 'card', 'chip'):
                    if background in palette:
                        assert contrast(rgb(palette[text]), rgb(palette[background])) >= 4.5, (
                            path, text, background, palette)
            for background in ('accent', 'flag', 'cond', 'dead', 'warm'):
                if background + '-fg' in palette:
                    assert contrast(rgb(palette[background + '-fg']), rgb(palette[background])) >= 4.5
            if 'tile-fg' in palette:
                # All hashed store hues, including the smaller phone tile label.
                assert min(contrast(rgb(palette['tile-fg']), colorsys.hls_to_rgb(h / 360, .62, .42))
                           for h in range(360)) >= 4.5
                # Gone tiles are greyed by CSS; test that composite too.
                grey = lambda c: (sum(v * w for v, w in zip(c, (.2126, .7152, .0722))),) * 3
                assert min(contrast(grey(rgb(palette['tile-fg'])),
                                    grey(colorsys.hls_to_rgb(h / 360, .62, .42)))
                           for h in range(360)) >= 4.5
        assert 'input::placeholder{color:var(--muted);opacity:1}' in css
        for header in re.findall(r'header\{([^}]+)\}', css):
            palette = dict(re.findall(r'--([\w-]+):(#[\da-f]+)', header))
            surface = re.search(r'background:(#[\da-f]+|var\(--card\))', header)
            if not surface or 'fg' not in palette:
                continue
            background = palette['card'] if surface[1] == 'var(--card)' else surface[1]
            for text in ('fg', 'muted', 'accent', 'link', 'good'):
                if text in palette:
                    assert contrast(rgb(palette[text]), rgb(background)) >= 4.5, (path, text, background)
    home = (published / 'index.html').read_text()
    css, palettes = themes(home)
    headers = re.findall(r'header\{--card:([^}]+)\}', css)
    assert len(headers) == 2
    for header in headers:
        palette = dict(re.findall(r'--([\w-]+):(#[\da-f]+)', '--card:' + header))
        for text in ('fg', 'muted', 'accent', 'link', 'good'):
            assert contrast(rgb(palette[text]), rgb(palette['card'])) >= 4.5
        for text in ('search-fg', 'search-muted'):
            assert contrast(rgb(palette[text]), rgb(palette['search-bg'])) >= 4.5
        for background in ('search-bg', 'card'):
            assert contrast(rgb(palette['search-line']), rgb(palette[background])) >= 3
        assert contrast(rgb(palette['accent']), rgb(palette['search-bg'])) >= 3
    drop = {}
    for declarations in re.findall(r'header \.drop\{([^}]+)\}', css):
        drop.update(re.findall(r'--([\w-]+):(#[\da-f]+)', declarations))
        for text in ('fg', 'muted', 'good'):
            assert contrast(rgb(drop[text]), rgb(drop['chip'])) >= 4.5
        for background in ('chip', 'line'):
            assert contrast(rgb(palettes[0]['warm']), rgb(drop[background])) >= 3
    strip = re.search(r'\.amazon-strip\{([^}]+)\}', css)[1]
    strip_bg = re.search(r'background:(#[\da-f]+)', strip)[1]
    strip_fg = re.search(r'color:(#[\da-f]+)', strip)[1]
    strip_link = re.search(r'\.amazon-inner a\{color:(#[\da-f]+)', css)[1]
    for text in (strip_fg, strip_link):
        assert contrast(rgb(text), rgb(strip_bg)) >= 4.5
    for fg, bg in (('#f3f0e8', '#22303f'), ('#223140', '#eef1ec')):
        assert contrast(rgb(fg), rgb(bg)) >= 4.5
        assert contrast(rgb(fg), composite(rgb(fg), rgb(bg), .14)) >= 4.5
    for palette in palettes:
        hover = '#0f7b6e' if palette['bg'] == '#f3f5f7' else '#42c2b2'
        assert contrast(rgb(palette['accent-fg']), rgb(hover)) >= 4.5
    assert not re.search(r'\.card\.gone\{[^}]*opacity:', home)
    assert not re.search(r'\.freetag\{[^}]*opacity:', home)
    assert not re.search(r'\.tkitem(?: s)?\{[^}]*opacity:', home)


def test_body_links_have_underlines_and_buttons_keep_their_style(published):
    for path in published.rglob('*.html'):
        css, _ = themes(path.read_text())
        assert 'p:not(.brand) a:not(.getdeal):not(.btn):not(.pwbtn)' in css
        assert 'footer a:not(.getdeal):not(.btn):not(.pwbtn)' in css
        assert 'text-decoration:underline;text-underline-offset:2px;text-decoration-thickness:1px' in css
    for name in ('index.html', 'join.html'):
        if not (published / name).exists():
            continue
        html = (published / name).read_text()
        if 'class="joincard"' in html:
            assert '.joincard a{color:var(--accent);text-decoration:underline;' in html


def test_phone_controls_and_small_links_have_44px_hit_areas(published):
    css, _ = themes((published / 'index.html').read_text())
    for selector in ('button:not(.code)', 'input:not([type="radio"])',
                     'select', '.getdeal', '.cattab', '.zerotoday', '.plans label', '.toast'):
        declarations = phone_rule(css, selector)
        assert 'min-height:44px' in declarations and 'min-width:44px' in declarations, selector
    assert 'height:44px' in phone_rule(css, '#minpct')
    assert 'padding-block:0' in phone_rule(css, '#controls label')
    assert 'width:44px;height:44px;top:2px' in phone_rule(css, '#clearsearch')
    assert 'min-height:48px' in phone_rule(css, '.subbar.ready')
    assert 'min-height:36px' in phone_rule(css, '.code')
    assert 'margin:4px 4px 4px 0' in phone_rule(css, '.code')
    link = ':where(a:not(.getdeal):not(.pwbtn):not(.subbar):not(.cattab))'
    for selector in (link + '::after', '.code::after'):
        declarations = phone_rule(css, selector)
        assert 'content:"";position:absolute' in declarations
        assert 'left:50%;top:50%;transform:translate(-50%,-50%)' in declarations
        assert 'min-height:44px' in declarations and 'min-width:44px' in declarations
    assert 'min-height:44px' in phone_rule(css, '.tkband')
    assert 'min-height:44px' in phone_rule(css, '.tkitem')
    # A min-height on the 2-line clamp would show a sliver of line 3.
    assert 'min-height' not in phone_rule(css, '.title a')
    assert '-webkit-line-clamp:2' in phone_rule(css, '.title a')
    assert 'overflow:visible;white-space:normal' in phone_rule(css, '.credit')
    assert 'outline:2px solid var(--accent);outline-offset:3px' in css
    assert '@media (prefers-reduced-motion: reduce)' in css and '.tkstrip{animation:none}' in css


def test_phone_extensions_are_shared_without_reflow_and_neighbors_keep_their_taps(published):
    for path in published.rglob('*.html'):
        css, _ = themes(path.read_text())
        for selector in ('.brand a::after', '#amazon-deals::after',
                         '.card .title a::after', '.card .credit a::after',
                         '.detail-sources a::after', '.code::after'):
            declarations = phone_rule(css, selector)
            assert 'content:"";position:absolute' in declarations, (path, selector)
            assert 'min-height:44px' in declarations, (path, selector)
            assert 'min-width:44px' in declarations, (path, selector)
            assert not re.search(r'\b(?:background|color|font|padding|margin):', declarations)
        assert phone_rule(css, '.card .title:has(a)') == 'position:relative;overflow:visible'
        assert 'top:calc(50% + 2px)' in phone_rule(css, '#amazon-deals::after')
        assert '.card .title{' not in css  # ended titles keep their original clip
        assert phone_rule(css, '.card .title a') == 'position:static'
        assert phone_rule(css, '.card .credit') == 'padding:14px;margin:-14px'
        # Static credits keep their original ellipsis, whitespace and line height.
        if '<article class="card' in path.read_text():
            assert '.credit{font-size:11.5px;color:var(--muted);min-width:0;overflow:hidden;' in css
        assert phone_rule(css, '.card .credit a') == 'position:relative'
        for selector in ('.card .credit a::after', '.detail-sources a::after'):
            declarations = phone_rule(css, selector)
            assert 'left:auto;right:0;transform:translateY(-50%)' in declarations
            assert 'width:44px;min-width:100%' in declarations
            assert 'max-width:calc(100% + 24px);z-index:0' in declarations
        assert 'left:-8px;right:auto;max-width:calc(100% + 16px)' in phone_rule(css, '.detail-sources a::after')
        for selector in ('.card .credit a:first-of-type::after', '.detail-sources a:first-of-type::after'):
            assert 'max-width:none' in phone_rule(css, selector)
        assert 'left:auto;right:0' in phone_rule(css, '.detail-sources a:first-of-type::after')
        assert phone_rule(css, '.detail-sources a:not(:first-of-type):last-of-type::after') == 'left:0;right:auto;max-width:calc(100% + 16px)'
        for selector in ('.card .actions', '.detail-sources'):
            assert 'isolation:isolate' in phone_rule(css, selector)
        assert 'position:relative;z-index:1' in phone_rule(css, '.card .actions>.getdeal')
        for selector in ('.card .credit a::before', '.detail-sources a::before'):
            assert phone_rule(css, selector).strip() == 'content:"";position:absolute;inset:0;z-index:1'
        assert 'width:calc(100% + 3px);min-width:0;height:calc(100% + 3px)' in phone_rule(css, '.code::after')
    css, _ = themes((published / 'index.html').read_text())
    assert 'overflow:hidden' in phone_rule(css, '.title a')
    assert '-webkit-line-clamp:2' in phone_rule(css, '.title a')
    # Zero specificity lets the shared title use its heading as the
    # containing block instead of getting clipped by a positioned link.
    assert ':where(a:not(.getdeal):not(.pwbtn):not(.subbar):not(.cattab)),.code{position:relative}' in css


def test_phone_type_never_drops_below_12px(published):
    css, _ = themes((published / 'index.html').read_text())
    blocks = phone_blocks(css)
    assert {width for width, _ in blocks} == {640, 520, 899, 360}
    for width, block in blocks:
        sizes = re.findall(r'\bfont(?:-size)?\s*:\s*[^;{}]*?([\d.]+)px\b', block)
        assert all(float(size) >= 12 for size in sizes), (width, sizes)
    for selector in ('.tag', '.secth', '.chip', '.badge', '.credit', '.newtag', '.condtag',
                     '.gonetag', '.freetag', '.visit-new', '.hotmeter', '.tklabel', '.tkitem s',
                     '#subscribe[data-zero]::after', 'body footer .meta',
                     'body footer .footerfine', 'body footer #acctfoot'):
        assert 'font-size:12px' in phone_rule(css, selector)
    # Specificity must beat the shared footer's later, desktop 11px rules.
    assert 'footer .footerfine{' in css and 'font-size:11px' in css


def test_phone_spacing_above_the_first_deal_is_tight(published):
    # Daily quality loop 2026-10-13: desktop margins cost a phone ~24px above
    # the first deal (iPhone 14: 382 -> 358px). Desktop rules stay as they are.
    home = (published / 'index.html').read_text()
    css, _ = themes(home)
    small = ''.join(block for width, block in phone_blocks(css) if width == 520)
    assert '#controls{margin:8px 0 4px}' in small
    assert '.tk{padding:3px 8px}' in small
    assert 'gap:10px;margin-top:8px}' in small
    assert phone_rule(css, '#cattabs').startswith('padding:4px 12px;')
    assert '#controls{display:flex;flex-wrap:nowrap;overflow-x:auto;gap:8px;align-items:center;margin:16px 0 6px}' in css


def test_phone_native_controls_and_safe_area_clearance(published):
    home = (published / 'index.html').read_text()
    css, _ = themes(home)
    # Safe-area padding lives in the phone rules; cover would put a
    # landscape iPhone's content (wider than 640px) under the notch.
    assert 'viewport-fit=cover' not in home
    assert '-webkit-text-size-adjust:100%' in phone_rule(css, 'html')
    assert 'padding-top:env(safe-area-inset-top)' in phone_rule(css, 'body')
    for selector in ('a', 'button', 'input', 'select', '[role="button"]', '[role="tab"]',
                     '.card.locked', '.tkitem', '.toast'):
        declarations = phone_rule(css, selector)
        assert 'touch-action:manipulation' in declarations
        assert '-webkit-tap-highlight-color:rgba(14,117,105,.18)' in declarations
    for selector in ('.subbar.ready', '.paywall'):
        declarations = phone_rule(css, selector)
        assert re.search(r'padding:[^;]*env\(safe-area-inset-bottom\)', declarations)
        for edge in ('left', 'right'):
            assert f'env(safe-area-inset-{edge})' in declarations
    assert 'env(safe-area-inset-bottom)' in phone_rule(css, '.wrap')
    assert 'top:calc(14px + env(safe-area-inset-top))' in phone_rule(css, '.toast')
    for edge in ('left', 'right'):
        assert f'env(safe-area-inset-{edge})' in phone_rule(css, '.toast')
    if 'class="joincard"' in home:
        assert 'env(safe-area-inset-bottom)' in phone_rule(css, '.joinoverlay .joincard')
    search = re.search(r'<input id="q"[^>]+>', home)[0]
    minimum = re.search(r'<input id="minpct"[^>]+>', home)[0]
    assert 'enterkeyhint="search"' in search and 'autocomplete="off"' in search
    assert 'inputmode="numeric"' in minimum and 'autocomplete="off"' in minimum
    assert 'font-size:16px' in phone_rule(css, '#controls #minpct')
    tabs = phone_rule(css, '#cattabs')
    assert 'flex-wrap:nowrap;overflow-x:auto' in tabs
    assert 'scroll-snap-type:x proximity' in tabs and 'mask-image:' in tabs


def test_phone_chips_clip_to_one_row_while_store_and_credit_stay_visible(published):
    home = (published / 'index.html').read_text()
    css, _ = themes(home)
    row = phone_rule(css, '.metarow')
    assert 'flex-wrap:nowrap;overflow:hidden' in row
    assert '-webkit-mask-image:linear-gradient(' in row and 'mask-image:linear-gradient(' in row
    assert 'flex:0 0 auto' in phone_rule(css, '.metarow>*')
    assert 'max-width:100%' in phone_rule(css, '.metarow .storechip')
    assert 'display:none' not in phone_rule(css, '.metarow .storechip')
    # The original desktop rule still wraps; only the phone blocks clip it.
    assert '.metarow{display:flex;flex-wrap:wrap;' in css
    static = home.split('<div id="content" data-clarity-unmask="true">')[1].split('<div id="paywall"')[0]
    cards = re.findall(r'<article class="card">(.*?)</article>', static, re.S)
    assert cards
    for card in cards:
        assert re.search(r'<div class="metarow"><span class="chip storechip">(Target|Walmart)</span>', card)
        for required in ('class="now"', 'class="flag"', 'class="getdeal"', 'class="credit"', 'via '):
            assert required in card
    assert 'var metarow = \'<span class="chip storechip">\'' in home


def test_static_and_live_media_share_reserved_geometry(published):
    home = (published / 'index.html').read_text()
    css, _ = themes(home)
    # Fixed heights shared by the static wall and live cards: no shift, no taller cards.
    assert '.media{position:relative;height:150px;' in css
    assert '.media{height:118px}' in css
    assert '.media img{max-width:88%;max-height:88%;object-fit:contain}' in css
    static = re.search(r'<div id="content" data-clarity-unmask="true">(.*?)</div>\s*<div id="paywall"', home, re.S)[1]
    assert 'class="media"' in static and '<img' not in static
    assert '<h2 class="secth sr-only">Verified deals right now</h2>' in static
    # External head scripts, if configured later, must not block the header.
    for attrs in re.findall(r'<script([^>]*)>', home[:home.index('<header>')]):
        assert 'src=' not in attrs or 'async' in attrs or 'defer' in attrs


def test_live_image_priority_and_ticker_after_paint(published, tmp_path):
    if not shutil.which('node'):
        pytest.skip('node unavailable')
    result = subprocess.run(['node', str(Path(__file__).with_suffix('.cjs')),
                             str(published / 'index.html')], check=True,
                            capture_output=True, text=True)
    rendered = json.loads(result.stdout)
    assert_outline('<h1>Home</h1>' + rendered['wall'], 'live wall')
    assert rendered['wall'].count('<div class="metarow"><span class="chip storechip">Target</span>') == 18
    images = re.findall(r'<img\s+([^>]+)>', rendered['wall'])
    assert len(images) == 18
    for i, attrs in enumerate(images):
        if i < 4:
            assert 'fetchpriority="high"' in attrs and 'loading="eager"' in attrs
        else:
            assert 'fetchpriority=' not in attrs and 'loading="lazy"' in attrs and 'decoding="async"' in attrs
    assert rendered['secondRenderHigh'] == 4
    assert rendered['idle'] == ['frame', 'timeout', 'idle', 'paint']
    assert rendered['fallback'] == ['frame', 'timeout', 'timeout', 'paint']


@pytest.mark.parametrize('live,dead', [(0, 0), (2, 4), (3, 0), (18, 1)])
def test_ticker_reserves_space_only_when_it_will_be_shown(tmp_path, live, dead):
    deals = [make_deal(dedupe_key=f'live-{i}') for i in range(live)]
    deals += [make_deal(dedupe_key=f'dead-{i}', status='dead', death='confirmed',
                        died_at='2026-10-11T23:00:00+00:00') for i in range(dead)]
    out, _, _, _ = render(tmp_path, AppConfig(support_enabled=True), deals)
    html = (out / 'index.html').read_text()
    tag = re.search(r'<section id="ticker"[^>]*>', html)[0]
    assert 'class="tk pending"' in tag
    assert ('hidden' in tag) is (live < 3)
    assert '.tk.pending .tkband{visibility:hidden}' in html
