"""Task 332: calm search buttons and a local coupon/bestseller exit; exact OFF bytes."""

import hashlib
import re
from pathlib import Path

from . import card_tidy, header_polish, search_analyze, search_polish_1019

COUPONS = '&rh=p_n_deal_type%3A210906366011&s=exact-aware-popularity-rank&'
CSS = ('@media(min-width:900px){'
       '.searchfield #analyze-search,.searchfield #search-discounts,#event-search #analyze-search{'
       'width:108px;padding:0;justify-content:center;text-align:center;font-size:14px;font-weight:800}'
       '.searchfield #analyze-search,#event-search #analyze-search{right:120px}'
       '.searchfield #q,#event-search #q{padding-left:36px;padding-right:274px}'
       # Event labels retain their existing 15px size and need a wider content box.
       '#event-search #analyze-search,#event-search #search-discounts{width:114px;font-size:15px;font-weight:800}'
       '#event-search #analyze-search{right:126px}'
       '#event-search #q{padding-left:14px;padding-right:250px}'
       '.searchfield svg{left:12px}'
       '.searchfield #clearsearch{right:230px}'
       '.searchfield .discounts-all{display:inline}'
       '.searchfield .analyze-long{display:inline}.searchfield .analyze-short{display:none}'
       '}@media(max-width:899px){'
       # card_tidy can leave its older phone labels in a later inline sheet.
       '.searchfield #analyze-search .analyze-long{display:none}'
       '.searchfield #analyze-search .analyze-short{display:inline}'
       '.searchfield #q:placeholder-shown{padding-right:96px}'
       '}@media(max-width:600px){.searchfield button#analyze-search{padding:0 11px}}'
       # Operator 2026-10-20: no star on either button; labels centred.
       'html :is(#analyze-search,.all-discounts)::before{content:none}')


def enabled(cfg):
    return getattr(cfg, 'search_buttons_1019', False) is True


def stylesheet(css):
    def calm(match):
        selector, body = match[1], match[2]
        if '#analyze-search' not in selector and '.all-discounts' not in selector:
            return match[0]
        if '::after' in selector:
            return ''  # Remove the sheen itself, including its reduced-motion duplicate.
        interactive = any(state in selector for state in (':hover', ':active', ':focus-visible'))
        declarations = []
        for declaration in body.split(';'):
            if ':' not in declaration:
                continue
            name, value = declaration.split(':', 1)
            name = name.strip()
            if name in ('transition', 'transform', 'filter') or (name == 'animation' and value != 'none'):
                continue
            if interactive and (name.startswith('background') or name in ('box-shadow', 'color')):
                continue
            if name == 'background' and 'repeat-x,' in value:
                value = value.split('repeat-x,', 1)[1]  # Preserve only the painted resting gradient.
            declarations.append(name + ':' + value)
        return selector + '{' + ';'.join(declarations) + '}' if declarations else ''
    css = re.sub(r'([^{}]+)\{([^{}]*)\}', calm, css)
    return re.sub(r'@keyframes wpcharge\{[^{}]*\{[^{}]*\}\}', '', css)


def script(source):
    # The recovery pill also served as the zero-zero exit; keep it as plain text.
    source = re.sub(r'  function searchMoreTopHtml\([^\n]*\) \{\n.*?\n  \}\n', '', source, flags=re.S)
    source = source.replace('amazon.innerHTML = q && count ? searchMoreTopHtml() : "";', 'amazon.innerHTML = "";')
    source = source.replace('amazon.innerHTML = q && count ? searchAmazonHtml(false) : "";', 'amazon.innerHTML = "";')
    source = source.replace('class="search-amazon search-amazon-top" data-clarity-mask',
                            'class="search-amazon" data-clarity-mask')
    source = search_polish_1019.function(source, 'searchAmazonHtml', lambda body:
        '    var q = searchAmazonQuery();\n'
        '    if (!q) return "";\n'
        '    return \'<a data-search-amazon data-clarity-mask="true" href="https://www.amazon.com/"\' +\n'
        '      \' target="_blank" rel="sponsored noopener">Search Amazon for &quot;\' + esc(q) +\n'
        '      \'&quot;</a><small>Amazon links may earn us a commission.</small>\';')
    source = source.replace("'<h2>Search results</h2>' + analyzeFallback(task) + '<h2>'",
                            "'<h2>Search results</h2><h2>'")
    def fallback(body):
        body = '    return ' + body.split('return ', 1)[1]
        body = re.sub(r'\(task.reason \? ("[^"]*") : .*? : "See this product on Amazon →"\)', r'\1', body)
        return '    if (!task.reason) return "";\n' + body.replace(
            'id="search-result-top" class="search-amazon-top"', 'class="search-amazon"').replace(
            'class="search-amazon-top"', 'class="search-amazon"').replace('class="getdeal" data-analyze-fallback', 'data-analyze-fallback')
    source = search_polish_1019.function(source, 'analyzeFallback', fallback)
    source = search_polish_1019.function(source, 'analyzeFallbackClick', lambda body:
        body.replace('.search-amazon-top', '[data-analyze-fallback]'))
    # Analyze and its lazy loader both bind this listener; Enter still calls analyzeSubmit.
    source = source.replace('analyzeButton.addEventListener("click", analyzeSubmit)',
        'analyzeButton.addEventListener("click", function () {\n'
        '    if (!analyzeSearchText()) return analyzeSubmit();\n  })')
    if '  function searchAmazonClick(' in source and 'id="analyze-search"' in source:
        source = search_polish_1019.function(source, 'searchAmazonClick', lambda body: body.replace(
            '    var discounts =',
            '    var analysis = e.target.closest && e.target.closest("#analyze-search") &&\n'
            '      analyzeSearchText();\n    var discounts =').replace(
            '(!discounts && !e.target.closest', '(!analysis && !discounts && !e.target.closest').replace(
            'if (!q && !discounts) return;', 'if (!q && !discounts && !analysis) return;').replace(
            'if (discounts && new RegExp', 'if ((analysis || discounts) && new RegExp').replace(
            '(discounts ? "&rh=', '(analysis ? "' + COUPONS + '" : discounts ? "&rh=').replace(
            'track(discounts ?', 'track(analysis || discounts ?'))
    source = source.replace('document.getElementById("q").placeholder = "Search deals or any Amazon product";',
        'document.getElementById("q").placeholder = typeof matchMedia == "function" && matchMedia("(max-width:899px)").matches\n'
        '      ? "Search deals on Amazon" : "Search deals or any Amazon product";')
    # Resizing across the same breakpoint also keeps the prompt in sync.
    source = source.replace('  function renderMeta() {',
        '  if (typeof matchMedia == "function") matchMedia("(max-width:899px)").addEventListener("change", renderMeta);\n'
        '  function renderMeta() {')
    return source


def publish(out, cfg):
    if not enabled(cfg):
        return
    versions = {}
    for name in (header_polish.ASSET, 'page-polish.css', 'search-analyze.js'):
        path = out / name
        if path.exists():
            text = path.read_text()
            text = stylesheet(text) + CSS if name.endswith('.css') else script(text)
            path.write_text(text)
            versions[name] = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    for path in out.rglob('*.html'):
        html = path.read_text()
        if 'id="event-search"' in html:
            # Event forms previously delegated Analyze to Home. Reuse Home's
            # actual link/ASIN parser so only ordinary words bypass that submit.
            source = search_analyze.SCRIPT.read_text()
            start = source.index('  function analyzeInput(')
            parser = source[start:source.index('  function analyzeKey(', start)]
            parser = card_tidy.analyze_input(parser, cfg)
            html = html.replace('  function searchAmazonClick(', parser + '  function searchAmazonClick(', 1)
            html = html.replace('type="submit">Analyze</button>',
                'type="submit"><span class="analyze-long">AI Analysis</span><span class="analyze-short">Analyze</span></button>')
            html = html.replace('  form.addEventListener("submit",',
                '  var phonePrompt = typeof matchMedia == "function" ? matchMedia("(max-width:899px)") : {matches: false, addEventListener: function () {}};\n'
                '  function searchPrompt() { input.placeholder = phonePrompt.matches ? "Search deals on Amazon" : "Search any product or paste an Amazon link"; }\n'
                '  phonePrompt.addEventListener("change", searchPrompt); searchPrompt();\n'
                '  form.addEventListener("submit",', 1)
        html = script(html)
        html = re.sub(r'(<button id="analyze-search"[^>]*?) aria-label="[^"]*"', r'\1', html)
        html = html.replace('<span class="analyze-long">Analyze</span><span class="analyze-short">Go</span>',
                            '<span class="analyze-long">AI Analysis</span><span class="analyze-short">Analyze</span>')
        # Operator 2026-10-20: "Discounts should be capitalized".
        html = html.replace('<span class="discounts-all">All </span>discounts<', '<span class="discounts-all">All </span>Discounts<')
        html = html.replace('>All discounts on Amazon →<', '>All Discounts on Amazon →<')
        html = re.sub(r'(<style\b[^>]*>)(.*?)(</style>)',
                      lambda m: m[1] + stylesheet(m[2]) + m[3], html, flags=re.S)
        if path == out / 'index.html' and not (out / header_polish.ASSET).exists():
            html = html.replace('</style>', CSS + '</style>', 1)
        for name, version in versions.items():
            html = re.sub(r'(/' + re.escape(name) + r'\?v=)[a-f0-9]+', lambda m: m[1] + version, html)
        path.write_text(html)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]
