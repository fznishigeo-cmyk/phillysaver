"""Task 326: derive discount words locally, preserving the previous OFF bytes."""

import hashlib
import re
from pathlib import Path

from . import all_discounts

SCRIPT = Path(__file__).with_name('templates') / 'discounts-from-link.js'


def enabled(cfg):
    return all_discounts.enabled(cfg) and getattr(cfg, 'discounts_from_link', False) is True


def script(html):
    # Reuse the typed-query sanitizer without changing any existing caller.
    html = html.replace('function searchAmazonQuery() {\n    var q = searchText()',
                        'function searchAmazonQuery(value) {\n    var q = (value === undefined ? searchText() : value.trim())')
    start = html.index('  function searchAmazonClick(')
    end = html.index('  document.addEventListener("click", searchAmazonClick);', start)
    click = html[start:end]
    click = click.replace('    var q = searchAmazonQuery();',
                          '    var link = discounts && discountsLink();\n'
                          '    var q = link ? searchAmazonQuery(link.words).split(/\\s+/).slice(0, 8).join(" ") : searchAmazonQuery();')
    click = click.replace('.test(document.getElementById("q").value)) q = "";',
                          '.test(link ? link.words : document.getElementById("q").value)) { q = ""; link = null; }')
    click = click.replace(': "deals?")', ': link && link.asin ? "dp/" + link.asin + "?" : "deals?")')
    return html[:start] + SCRIPT.read_text() + click + html[end:]


def publish(out, cfg):
    if not enabled(cfg):
        return
    for path in out.rglob('*.html'):
        html = path.read_text()
        if '  function searchAmazonClick(' in html:
            if path == out / 'index.html':
                if cfg.search_amazon and cfg.affiliate_amazon_tag:
                    # renderMeta immediately replaces this with the Amazon prompt.
                    html = html.replace('    document.getElementById("q").placeholder = live ? "Search " + roughCount(live) + (live === 1 ? " deal" : " deals") : "Search deals";', '')
                if cfg.support_enabled:
                    # Support uses Join's own styles; the membership dialog and
                    # account email have no markup or script consumer in this mode.
                    unused = (r'(?<![^{}])\s*(?:\.modal|\.mcard(?: [^{}]*)?|\.gsi|\.or|\.or::before,\.or::after|'
                              r'#authstep2(?: \.autherr)?|#cofb,#comanage|\.acct \.email)\{[^{}]*\}')
                    html = re.sub(r'(<style\b[^>]*>)(.*?)(</style>)',
                                  lambda m: m[1] + re.sub(unused, '', m[2]) + m[3], html, flags=re.S)
            path.write_text(script(html))


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes() + SCRIPT.read_bytes()).hexdigest()[:12]
