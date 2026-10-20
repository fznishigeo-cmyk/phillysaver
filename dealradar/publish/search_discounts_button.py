"""Task 342: yellow results exit using the existing All Discounts handler."""

import hashlib
import re
from pathlib import Path

from . import search_polish_1019

CSS = ('#search-amazon-result.search-amazon{margin:12px 0;text-align:left}'
       'a.search-discounts,a.search-discounts:hover{display:inline-flex;align-items:center;justify-content:center;'
       'background:#ffc83d;color:#111!important;text-decoration:none!important;font-weight:800;font-size:18px;'
       'line-height:1.25;border-radius:999px;min-height:50px;padding:14px 24px;text-align:center;'
       'animation:none;transition:none;filter:none;transform:none;box-shadow:none}'
       'a.search-discounts::before,a.search-discounts::after{content:none}'
       'a.search-discounts:focus-visible{outline:3px solid var(--accent);outline-offset:3px}'
       '@media(max-width:899px){a.search-discounts{width:100%}}')


def enabled(cfg):
    return getattr(cfg, 'search_discounts_button', False) is True


def script(source):
    source = search_polish_1019.function(source, 'searchAmazonHtml', lambda body:
        '    if (!searchAmazonQuery() && !(typeof discountsLink === "function" && discountsLink())) return "";\n'
        '    return \'<a class="search-discounts" data-search-amazon data-search-discounts data-clarity-mask="true"\' +\n'
        '      \' href="https://www.amazon.com/" target="_blank" rel="sponsored noopener">See Amazon discounts →</a>\';')
    source = source.replace('amazon.innerHTML = "";', 'amazon.innerHTML = q && count ? searchAmazonHtml(false) : "";')
    source = source.replace('          searchAmazonQuery, amazonResultsEnabled,',
                            '          searchAmazonHtml, searchAmazonQuery, amazonResultsEnabled,')
    source = search_polish_1019.function(source, 'analyzeFallback', lambda body:
        '    return \'<p class="search-amazon">\' + ' +
        ('host.' if 'host.' in body else '') + 'searchAmazonHtml() + \'</p>\';')
    source = source.replace("'<h2>Search results</h2><h2>'",
                            "'<h2>Search results</h2>' + analyzeFallback(task) + '<h2>'")
    # The existing discounts branch owns sanitizing, pasted-product names, tag,
    # navigation and search_discounts tracking. This button is another caller.
    return source.replace('e.target.closest(".all-discounts")',
                          'e.target.closest(".all-discounts,[data-search-discounts]")')


def stylesheet(css):
    # These removed pills have no consumer; remove their old paint rules too.
    def rule(match):
        selectors = [s for s in match[1].split(',') if '.search-amazon-top' not in s and
                     '#search-amazon-result [data-search-amazon]' not in s]
        return ','.join(selectors) + '{' + match[2] + '}' if selectors else ''
    return re.sub(r'([^{}]+)\{([^{}]*)\}', rule, css)


def publish(out, cfg):
    if not enabled(cfg):
        return
    versions = {}
    for name in ('header-polish.css', 'page-polish.css'):
        path = out / name
        if path.exists():
            path.write_text(stylesheet(path.read_text()) + CSS)
            versions[name] = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    analyze = out / 'search-analyze.js'
    if analyze.exists():
        analyze.write_text(script(analyze.read_text()))
        versions[analyze.name] = hashlib.sha256(analyze.read_bytes()).hexdigest()[:12]
    for path in out.rglob('*.html'):
        html = script(path.read_text())
        if path == out / 'index.html':
            # The previous placeholder lived under the search field. Results
            # belong with the cards, without expanding the approved header.
            html, count = re.subn(r'<p\b[^>]*id="search-amazon-result"[^>]*>.*?</p>', '', html, flags=re.S)
            if count:
                html = html.replace('<div id="content"',
                    '<p id="search-amazon-result" class="search-amazon" data-clarity-mask="true" hidden></p>'
                    '<div id="content"', 1)
        html = re.sub(r'(<style\b[^>]*>)(.*?)(</style>)',
                      lambda m: m[1] + stylesheet(m[2]) + m[3], html, flags=re.S)
        if path == out / 'index.html' and not (out / 'header-polish.css').exists():
            html = html.replace('</style>', CSS + '</style>', 1)
        for name, version in versions.items():
            html = re.sub(r'(/' + re.escape(name) + r'\?v=)[a-f0-9]+', lambda m: m[1] + version, html)
        path.write_text(html)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]
