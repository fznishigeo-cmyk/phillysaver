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
            for background in ('accent', 'flag', 'cond', 'dead'):
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
    home = (published / 'index.html').read_text()
    css, palettes = themes(home)
    header = re.search(r'header\{--card:(.*?)\}', css, re.S)[1]
    header_muted = rgb(re.search(r'--muted:(#[\da-f]+)', header)[1])
    for background in ('#163b40', '#0f1b22', '#0c151b'):
        assert contrast(header_muted, composite(rgb('#fff'), rgb(background), .065)) >= 4.5
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
