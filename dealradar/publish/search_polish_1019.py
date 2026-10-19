"""Task 318: search/header finishing pass; OFF preserves the task 317 bytes."""

import hashlib
import re
from html import unescape
from pathlib import Path

from . import all_discounts, header_polish

SMALL = '<small>' + all_discounts.DISCLOSURE + '</small>'
SORT = '&s=exact-aware-popularity-rank&'
TABS = ('#cattabs .cattab{min-height:44px;padding:8px 15px;border:1px solid var(--line);'
        'border-radius:999px;background:var(--card);color:var(--fg);font-size:14px}'
        '#cattabs .cattab:hover{border-color:var(--accent)}'
        '#cattabs .cattab.active{background:var(--warm);border-color:var(--accent);'
        'color:var(--warm-fg);font-weight:700}')


def enabled(cfg):
    return getattr(cfg, 'search_polish_1019', False) is True


def has_footer_disclosure(html):
    footer = re.search(r'<footer\b[^>]*>(.*?)</footer>', html, re.S)
    text = unescape(re.sub(r'<[^>]+>', '', footer[1])) if footer else ''
    return bool(re.search(r'As an Amazon Associate,\s*wowpriced earns from qualifying purchases\.', text))


def function(source, name, transform):
    """Change only a composed function, preserving secondary affiliate exits."""
    pattern = r'(  function ' + name + r'\([^\n]*\) \{\n)(.*?)(\n  \})'
    return re.sub(pattern, lambda m: m[1] + transform(m[2]) + m[3], source, flags=re.S)


def script(source, *, footer):
    def click(body):
        # Task 317 shares this handler with discounts. Its filter stays separate.
        body = body.replace(': "&") : "deals?")',
                            ': e.target.closest(".search-amazon-top") ? "' + SORT + '" : "&") : "deals?")')
        return body.replace('+ "&tag="', '+ (e.target.closest(".search-amazon-top") ? "' + SORT + 'tag=" : "&tag=")')
    source = function(source, 'searchAmazonClick', click)
    source = function(source, 'analyzeFallbackClick', lambda body: body.replace(
        '"&tag="', '(event.target.closest(".search-amazon-top") ? "' + SORT + 'tag=" : "&tag=")'))
    # The deferred module used to assume the hint exists when focused at mount.
    source = source.replace('if (document.activeElement === analyzeBox) analyzeHint.hidden',
                            'if (analyzeHint && document.activeElement === analyzeBox) analyzeHint.hidden')
    if footer:
        for name in ('searchMoreTopHtml', 'analyzeFallback'):
            source = function(source, name, lambda body: body.replace(SMALL, ''))
        source = function(source, 'searchAmazonHtml', lambda body: body.replace(
            SMALL + "';", "' + (primary ? '' : '" + SMALL + "');"))
    return source


def markup(html):
    footer = has_footer_disclosure(html)
    def hint(match):
        # With no footer, retain the disclosure formerly nested in the hint.
        return ('<p class="discounts-disclosure">' + all_discounts.DISCLOSURE + '</p>'
                if not footer and all_discounts.DISCLOSURE in match[0] else '')
    html = re.sub(r'<p\b[^>]*\bid="search-hint"[^>]*>.*?</p>', hint, html, flags=re.S)
    def described(match):
        ids = [value for value in match[1].split() if value != 'search-hint']
        return ' aria-describedby="' + ' '.join(ids) + '"' if ids else ''
    html = re.sub(r' aria-describedby="([^"]*)"', described, html)
    if footer:
        html = re.sub(r'(<div class="discounts-mobile">.*?)' + re.escape(SMALL) + r'(</div>)',
                      r'\1\2', html, flags=re.S)
        html = re.sub(r'<p class="discounts-disclosure">.*?</p>', '', html, flags=re.S)
    html = html.replace('<span class="join-why"> · price alerts</span>',
                        '<span class="join-why"> · Price Alerts</span>')
    return script(html, footer=footer)


def stylesheet(css):
    # The hint has no consumer with this switch ON, including on fallback pages.
    css = re.sub(r'(?:\.search )?#search-hint(?:\[hidden\])?\{[^}]*\}', '', css)
    css = css.replace('minmax(0,900px)', 'minmax(0,940px)')
    css = css.replace('minmax(0,720px)', 'minmax(0,760px)').replace('max-width:1600px', 'max-width:1640px')
    css = css.replace('minmax(320px,900px)', 'minmax(320px,940px)').replace('max-width:1760px', 'max-width:1800px')
    css = css.replace('.hnav{display:flex;align-items:center;gap:16px;',
                      '.hnav{display:flex;flex-direction:column;align-items:center;gap:4px;')
    css = css.replace('flex:1;min-width:0;margin:0;padding:0 28px 0 0;'
                      'flex-wrap:nowrap;justify-content:flex-start;overflow-x:auto;scrollbar-width:thin;'
                      'mask-image:linear-gradient(90deg,#000 calc(100% - 24px),transparent)',
                      'min-width:0;width:100%;margin:0;padding:0;flex-wrap:wrap;justify-content:center')
    css = css.replace('#event-search .searchfield{max-width:900px}', '#event-search .searchfield{max-width:940px}')
    css = css.replace('--pill-colors:#2563eb,#0870b4 52%,#4f46e5', '--pill-colors:#1265f4,#0061fe 52%,#0050d6')
    css = css.replace('inset:3px 3px 3px auto', 'inset:6px 6px 6px auto')
    css = css.replace('inset:2px 2px 2px auto', 'inset:6px 6px 6px auto')
    css = css.replace('border-radius:999px 10px 10px 999px', 'border-radius:999px')
    css = css.replace('--discount-width:142px', '--discount-width:138px').replace('--discount-width:112px', '--discount-width:108px')
    css = css.replace('calc(var(--discount-width) + 7px);width:100px', 'calc(var(--discount-width) + 12px);width:96px')
    css = css.replace('calc(var(--discount-width) + 109px)', 'calc(var(--discount-width) + 110px)')
    css = css.replace('calc(var(--discount-width) + 152px)', 'calc(var(--discount-width) + 154px)')
    css = css.replace('#event-search #analyze-search{inset:', '#event-search #analyze-search{min-height:44px;inset:')
    # Fit a 44px pill plus the two 6px gaps on phones and event pages too.
    css = css.replace('#q{height:48px}', '#q{height:56px}')
    css = css.replace('.searchfield svg{top:14px}', '.searchfield svg{top:18px}')
    css = css.replace('#clearsearch{top:2px}', '#clearsearch{top:8px}').replace('#clearsearch{top:4px}', '#clearsearch{top:8px}')
    css = css.replace('#clearsearch{width:44px;height:44px;top:2px}', '#clearsearch{width:44px;height:44px;top:6px}')
    css = css.replace('#cattabs .cattab{min-height:44px;padding:8px;border:0;border-bottom:3px solid transparent;'
                      'border-radius:0;background:none;color:var(--fg)}'
                      '#cattabs .cattab.active,#cattabs .cattab:hover{border-bottom-color:var(--fg)}', '')
    return css


def publish(out, cfg):
    if not enabled(cfg):
        return
    header = out / header_polish.ASSET
    # Reuse the established render-blocking assets, including the event cascade.
    for path in (header, out / 'page-polish.css'):
        if path.exists():
            path.write_text(stylesheet(path.read_text()))
    extra = '#event-search #q{height:56px}'
    if not all_discounts.enabled(cfg):
        extra += ('.searchfield #analyze-search,#event-search #analyze-search{inset:6px 6px 6px auto;'
                  'min-height:44px;border-radius:999px}'
                  '@media(max-width:899px){.searchfield #q{height:56px}}'
                  '#event-search .searchfield{max-width:720px}')
    header.write_text((header.read_text() if header.exists() else '') + TABS + extra)
    page = out / 'page-polish.css'
    if page.exists():
        page.write_text(page.read_text() + extra)
    home = out / 'index.html'
    footer = has_footer_disclosure(home.read_text())
    analyze = out / 'search-analyze.js'
    versions = {}
    if analyze.exists():
        analyze.write_text(script(analyze.read_text(), footer=footer))
        versions[analyze.name] = hashlib.sha256(analyze.read_bytes()).hexdigest()[:12]
    for path in (header, page):
        if path.exists():
            versions[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    for path in out.rglob('*.html'):
        html = markup(path.read_text())
        if path == home:
            # Covers configurations with the preceding shared-style flags OFF.
            html = stylesheet(html)
            if 'href="/header-polish.css?' not in html:
                html = html.replace('</head>', '<link rel="stylesheet" href="/header-polish.css?v='
                                    + versions[header.name] + '"></head>', 1)
        for name, version in versions.items():
            html = re.sub(r'(/' + re.escape(name) + r'\?v=)[a-f0-9]+', lambda m: m[1] + version, html)
        path.write_text(html)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]
