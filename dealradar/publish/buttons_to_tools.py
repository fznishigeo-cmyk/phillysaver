"""Task 344: native tool links, query handoff and tool-specific Amazon exits."""

import hashlib
import re
from pathlib import Path

from . import search_buttons_1019

TEMPLATES = Path(__file__).with_name('templates')


def enabled(cfg):
    return getattr(cfg, 'buttons_to_tools', False) is True


def tool_script(source, cfg):
    if not enabled(cfg):
        return source
    # Reuse task 332's coupon filter, without its bestseller ordering.
    coupons = search_buttons_1019.COUPONS.split('&s=', 1)[0] + '&'
    source = source.replace('&rh=p_n_deal_type%3A23566065011&', coupons)
    source = source.replace("toolButton(toolAmazon(item.asin), 'View at Amazon' + (lead ? ' →' : ''), 'search_amazon')",
        "(lead ? toolButton(toolSimilar(item), 'See similar deals on Amazon →', 'search_similar_deals') :\n"
        "      toolButton(toolAmazon(item.asin), 'View at Amazon', 'search_amazon'))")
    helper, mount = (TEMPLATES / 'buttons-to-tools-page.js').read_text().split('// TOOL_QUERY_HANDOFF\n')
    return source.replace('// TOOL_PAGE_MOUNT', helper + '// TOOL_PAGE_MOUNT') + '\n' + mount


def publish(out, cfg):
    if not enabled(cfg):
        return
    runtime = (TEMPLATES / 'buttons-to-tools.js').read_text()
    # Anchors need the same phone selector specificity and vertical alignment as buttons.
    versions = {}
    for name in ('header-polish.css', 'page-polish.css'):
        path = out / name
        if path.exists():
            css = path.read_text().replace('button#analyze-search', ':is(button,a)#analyze-search')
            css += '#analyze-search{display:inline-flex;align-items:center;justify-content:center;text-decoration:none}'
            path.write_text(css)
            versions[name] = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    for path in out.rglob('*.html'):
        html = path.read_text()
        if 'id="analyze-search"' in html:
            html = re.sub(r'<button id="analyze-search"([^>]*)>(.*?)</button>',
                lambda m: '<a id="analyze-search"' + re.sub(r' type="[^"]*"', '', m[1])
                + ' href="/amazon-deal-checker/" data-tool-link="deal-checker">' + m[2] + '</a>', html, flags=re.S)
            def finder(match):
                attrs = re.sub(r' (?:href|target|rel|aria-label)="[^"]*"', '', match[1])
                return '<a' + attrs + ' href="/amazon-discount-finder/" data-tool-link="discount-finder">'
            html = re.sub(r'<a([^>]*\bclass="all-discounts"[^>]*)>', finder, html)
            # It opens our Discount Finder now, so the phone label no longer promises Amazon.
            html = html.replace('>All Discounts on Amazon →<', '>All Discounts →<')
            html = html.replace('  function searchAmazonClick(e) {', runtime +
                '  function searchAmazonClick(e) {\n    if (e.target.closest && e.target.closest("[data-tool-link]")) return;')
            html = html.replace('  if (analyzeButton) analyzeButton.addEventListener("click", function () {\n'
                '    if (!analyzeSearchText()) return analyzeSubmit();\n  });\n', '')
            html = html.replace('  if (analyzeButton) analyzeButton.addEventListener("click", analyzeSubmit);\n', '')
            html = html.replace('button#analyze-search', ':is(button,a)#analyze-search')
        for name, version in versions.items():
            html = re.sub(r'(/' + re.escape(name) + r'\?v=)[a-f0-9]+', lambda m: m[1] + version, html)
        path.write_text(html)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes() + b''.join(
        (TEMPLATES / name).read_bytes() for name in ('buttons-to-tools.js', 'buttons-to-tools-page.js'))).hexdigest()[:12]
