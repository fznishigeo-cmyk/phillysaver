"""Task 317: a tagged Amazon discounts exit, with exact OFF rollback bytes."""

import hashlib
import json
import re
from pathlib import Path

from . import card_tidy, comments, header_polish, search_analyze, search_analyze_lazy, search_boost, search_box_glow
from .template_slots import replace_slot

TEMPLATES = Path(__file__).with_name('templates')
STYLE = TEMPLATES / 'all-discounts.css'
DISCLOSURE = 'Amazon links may earn us a commission.'
INPUT = '    this.parentNode.classList.add("discounts-typed");\n'


def enabled(cfg):
    return getattr(cfg, 'all_discounts_button', False) is True


def swap(html, old, new):
    return replace_slot(html, old, new, template_name='search', slot_name='all-discounts')


def compact_css(css):
    """Trim punctuation whitespace, preserving strings, URLs and escaped tokens."""
    quoted = r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' '''.strip()
    literal = r'(' + quoted + r'|url\((?:' + quoted + r'|\\.|[^)"\'])*\)|\\(?:[a-fA-F0-9]{1,6}\s?|.))'
    parts = re.split(literal, comments.css(css), flags=re.I)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r'([{}:;,])\s*|\s*([{};,])', lambda m: m[1] or m[2], parts[i])
    return ''.join(parts)


def button(mobile=False):
    label = 'All discounts on Amazon →' if mobile else '<span class="discounts-all">All </span>discounts'
    return ('<a class="all-discounts" id="search-discounts' + ('-mobile' if mobile else '')
            + '" href="https://www.amazon.com/" target="_blank" rel="sponsored noopener"'
            ' aria-label="All discounts on Amazon (opens in a new tab)">' + label + '</a>')


def shared_css():
    # Use Analyze's actual rules for both pills. Only palette/glow variables differ.
    base = search_analyze.STYLE.read_text().split('#analyze-search', 1)[1].split('#clearsearch', 1)[0]
    base = base.replace('right:6px;top:6px;', '').replace('padding:0 20px;', '')
    base = base.replace('background:#14292d;color:#fff;', '')
    glow = search_box_glow.CSS[search_box_glow.CSS.index('#analyze-search{'):]
    tidy = card_tidy.CSS[card_tidy.CSS.index('#analyze-search{'):card_tidy.CSS.index('#search-more:')]
    css = ('#analyze-search' + base + glow + tidy).replace('#analyze-search', ':is(#analyze-search,.all-discounts)')
    # Keep white text AA even while the rainbow charge and gold sheen cross it.
    css = css.replace('#ef4444,#f59e0b,#22c55e,#06b6d4,#8b5cf6,#ec4899,#ef4444',
                      '#991b1b,#92400e,#166534,#155e75,#6d28d9,#9d174d,#991b1b')
    css = css.replace('#5b21b6,#9333ea 50%,#db2777', 'var(--pill-colors,#5b21b6,#9333ea 50%,#cf236e)')
    css = css.replace('rgba(255,196,0,.4)', 'rgba(255,196,0,.05)')
    css = css.replace('rgba(147,51,234,.35)', 'var(--pill-glow,rgba(147,51,234,.35))')
    css = css.replace('rgba(236,72,153,.55)', 'var(--pill-glow,rgba(236,72,153,.55))')
    css = css.replace('inset 0 0 0 2px rgba(255,255,255,.9),', '')
    css = css.replace('inset:2px 2px 2px auto', 'inset:3px 3px 3px auto')
    css = css.replace('border-radius:999px 10px 10px 999px', 'border-radius:999px')
    css = css.replace('max-width:40%;', '')
    states = ','.join(':is(#analyze-search,.all-discounts):' + state for state in ('hover', 'focus-visible', 'active'))
    css = css.replace(states, ':is(#analyze-search,.all-discounts):is(:hover,:focus-visible,:active)')
    return css + STYLE.read_text()


def header_css(css):
    css = css.replace('minmax(0,720px)', 'minmax(0,900px)').replace('max-width:1600px', 'max-width:1760px')
    # Compact the timer/install through laptop widths to leave 300px of typing room at 1280px.
    css = css.replace('(max-width:1199px)', '(max-width:1599px)').replace('minmax(320px,1fr)', 'minmax(320px,900px)')
    css = css.replace('.hnav{display:flex;align-items:center;gap:16px;',
                      '.hnav{display:flex;flex-direction:column;align-items:center;gap:4px;')
    css = css.replace('#search-hint{display:none!important}', '')
    css = css.replace('flex:1;min-width:0;margin:0;padding:0 28px 0 0;'
                      'flex-wrap:nowrap;justify-content:flex-start;overflow-x:auto;scrollbar-width:thin;'
                      'mask-image:linear-gradient(90deg,#000 calc(100% - 24px),transparent)',
                      'min-width:0;width:100%;margin:0;padding:0;flex-wrap:wrap;justify-content:center')
    return css


def search_script(cfg):
    """Derive the sanitizer and click handling from the existing Amazon exit."""
    source = (TEMPLATES / 'index.html').read_text()
    query = source[source.index('  function searchAmazonQuery()'):source.index('  function searchAmazonHtml(')]
    click = source[source.index('  function searchAmazonClick('):source.index('/*/search-amazon*/  function renderSearchCount')]
    click = click.replace('    if ((e.type',
                          '    var discounts = e.target.closest && e.target.closest(".all-discounts");\n    if ((e.type')
    click = click.replace('!e.target.closest("[data-search-amazon]")',
                          '(!discounts && !e.target.closest("[data-search-amazon]"))')
    pattern = json.dumps(search_boost.adult_pattern(cfg.search_boost['exclude_words'])).replace('<', '\\u003c')
    if cfg.search_boost['exclude_words']:
        click = click.replace('    if (!q) return;',
                              '    if (discounts && new RegExp(' + pattern + ', "iu").test(document.getElementById("q").value)) q = "";\n'
                              '    if (!q && !discounts) return;')
    else:
        click = click.replace('    if (!q) return;', '    if (!q && !discounts) return;')
    click = click.replace('"https://www.amazon.com/s?k=" + encodeURIComponent(q) + "&tag="',
                          '"https://www.amazon.com/" + (q ? "s?k=" + encodeURIComponent(q) +\n'
                          '      (discounts ? "&rh=p_n_deal_type%3A23566065011&" : "&") : "deals?") + "tag="')
    click = click.replace('track("search_amazon")', 'track(discounts ? "search_discounts" : "search_amazon")')
    return query, click.replace('{{SEARCH_AMAZON_TAG}}', json.dumps(cfg.affiliate_amazon_tag).replace('<', '\\u003c'))


def markup(html):
    html = re.sub(r'(<button id="analyze-search"[^>]*>.*?</button>)',
                  lambda m: m[1] + button(), html, count=1, flags=re.S)
    # If Analyze is independently disabled, keep the affiliate exit usable.
    if 'id="search-discounts"' not in html:
        html = swap(html, '<button id="clearsearch"', button() + '<button id="clearsearch"')
    mobile = '<div class="discounts-mobile">' + button(True) + '<small>' + DISCLOSURE + '</small></div>'
    if 'id="event-search"' in html:
        html = swap(html, '  </div>\n</form>', '  </div>' + mobile + '<p class="discounts-disclosure">' + DISCLOSURE + '</p>\n</form>')
    else:
        html = swap(html, '      </div>\n      <p id="search', '      </div>' + mobile + '\n      <p id="search')
        if 'id="search-hint"' in html:
            html = swap(html, 'Free price check · no sign-up</p>',
                        'Free price check · no sign-up<span class="discounts-disclosure"> · ' + DISCLOSURE + '</span></p>')
        else:
            html = swap(html, mobile, mobile + '<p id="search-hint" class="discounts-disclosure">' + DISCLOSURE + '</p>')
    return html


def publish(out, cfg):
    if not enabled(cfg):
        return
    css = shared_css()
    # Append to the established shared assets; no extra stylesheet request.
    header = out / header_polish.ASSET
    header_style = header_css(header.read_text()) if header.exists() else ''
    # Replace the original pill rules rather than shipping two sets of them.
    glow_at = search_box_glow.CSS.index('#analyze-search{')
    tidy_start, tidy_end = card_tidy.CSS.index('#analyze-search{'), card_tidy.CSS.index('#search-more:')
    old_pills = (search_box_glow.CSS[glow_at:], card_tidy.CSS[tidy_start:tidy_end])
    def without_pills(style):
        for old in old_pills:
            style = style.replace(old, '')
        return style
    # Share only one copy on home; event pages use the page stylesheet.
    common = without_pills(search_box_glow.CSS + card_tidy.CSS)
    common = common.replace('.searchfield .analyze-long{display:inline}.searchfield .analyze-short{display:none}', '')
    common = common.replace('.searchfield #analyze-search{padding:0 8px 0 15px}', '')
    header.write_text(header_style + css)
    page = out / 'page-polish.css'
    if page.exists():
        page.write_text(common + css)
    home_path = out / 'index.html'
    html = without_pills(home_path.read_text())
    # Keep these rules at their original cascade position, but transfer them in
    # the same cached asset as the new header instead of growing phone HTML.
    search_start = html.index('.searchfield{position:relative}')
    search_end = html.index('.hrow>.tag{', search_start) if '.hrow>.tag{' in html[search_start:] else html.index('#controls select', search_start)
    search_css = html[search_start:search_end]
    html = html[:search_start] + '</style>{{DISCOUNTS_STYLE}}<style>' + html[search_end:]
    analyze_css = search_analyze.STYLE.read_text()
    critical, deferred = search_analyze_lazy.split_style(analyze_css)
    if critical in html or analyze_css in html:
        html = html.replace(analyze_css, deferred).replace(critical, '')
        # Its old pill rules are superseded by the shared twin selectors.
        critical = re.sub(r'#analyze-search[^{}]*\{[^}]*\}', '', critical)
        critical = critical.replace('.analyze-long{display:none}.analyze-short{display:inline}', '')
        header.write_text(critical + header.read_text())
    page_link = re.search(r'<link rel="stylesheet" href="/page-polish.css\?v=[a-f0-9]+">', html)
    if page_link:
        html = html.replace(page_link[0], '')
        header.write_text(common + header.read_text())
    header.write_text(compact_css(search_css + header.read_text()))
    if page.exists():
        page.write_text(compact_css(page.read_text()))
    html = re.sub(r'(<style\b[^>]*>)(.*?)(</style>)',
                  lambda m: m[1] + compact_css(m[2]) + m[3], html, flags=re.S)
    html = re.sub(r'<link rel="stylesheet" href="/header-polish.css\?v=[a-f0-9]+">', '', html)
    version = hashlib.sha256(header.read_bytes()).hexdigest()[:12]
    html = swap(html, '{{DISCOUNTS_STYLE}}', '<link rel="stylesheet" href="/header-polish.css?v=' + version + '">')
    home_path.write_text(html)
    versions = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:12] for p in (header, page) if p.exists()}
    query, click = search_script(cfg)
    for path in out.rglob('*.html'):
        html = path.read_text()
        home, event = path == out / 'index.html', 'id="event-search"' in html
        if home or event:
            html = markup(html)
            if home:
                if '  function searchAmazonClick(' in html:
                    start = html.index('  function searchAmazonClick(')
                    end = html.index('  document.addEventListener("auxclick", searchAmazonClick);', start)
                    end += len('  document.addEventListener("auxclick", searchAmazonClick);\n')
                    html = html[:start] + click + html[end:]
                else:
                    html = swap(html, '  function renderSearchCount(', query + click + '  function renderSearchCount(')
                html = swap(html, '    document.getElementById("clearsearch").hidden = !this.value;\n',
                            INPUT + '    document.getElementById("clearsearch").hidden = !this.value;\n')
                if 'href="/header-polish.css?' not in html:
                    html = html.replace('</head>', '<link rel="stylesheet" href="/header-polish.css?v=' + versions[header.name] + '"></head>', 1)
            else:
                source = (TEMPLATES / 'index.html').read_text()
                track = source[source.index('  function track(name, params)'):source.index('  function sortVal(')]
                html = swap(html, '  form.addEventListener("submit",',
                            '  function searchText() { return input.value.trim(); }\n' + track + query + click
                            + '  input.addEventListener("input", function () {\n' + INPUT + '  });\n'
                            + '  form.addEventListener("submit",')
                # Event geometry lives in its inline sheet; shared twin rules follow it.
                link = re.search(r'<link rel="stylesheet" href="/page-polish.css\?v=[a-f0-9]+">', html)[0]
                html = html.replace(link, '', 1).replace('</head>', link + '</head>', 1)
        # Shared CSS also appears on pages without a search form.
        for name, version in versions.items():
            html = re.sub(r'(/' + re.escape(name) + r'\?v=)[a-f0-9]+', lambda m: m[1] + version, html)
        path.write_text(html)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes() + STYLE.read_bytes()).hexdigest()[:12]
