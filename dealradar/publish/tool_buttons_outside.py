"""Operator 2026-10-20: Deal Checker / Discount Finder buttons sit outside the search bar."""

import hashlib
import re
from pathlib import Path

CSS = (Path(__file__).with_name('templates') / 'tool-buttons-outside.css').read_text()
VERSION = hashlib.sha256(CSS.encode()).hexdigest()[:12]
INSIDE = re.compile(r'\s*<a id="analyze-search"([^>]*)>.*?</a><a class="all-discounts" id="search-discounts"([^>]*)>.*?</a>',
                    re.S)
MOBILE = re.compile(r'<div class="discounts-mobile"><a class="all-discounts" id="search-discounts-mobile"[^>]*>.*?</a></div>',
                    re.S)
ARROW = '<span class="tool-arrow" aria-hidden="true"> →</span>'


def enabled(cfg):
    return getattr(cfg, 'tool_buttons_outside', False) is True


def markup(html):
    inside = INSIDE.search(html)
    if not inside or not MOBILE.search(html):
        return html
    # Phone order (Discount Finder, then Deal Checker); desktop CSS puts Deal Checker first.
    buttons = ('<div class="tool-buttons">'
               f'<a class="all-discounts" id="search-discounts"{inside[2]}>Discount Finder{ARROW}</a>'
               f'<a id="analyze-search"{inside[1]}>Deal Checker{ARROW}</a></div>')
    html = MOBILE.sub(lambda m: buttons, html[:inside.start()] + html[inside.end():], count=1)
    html = html.replace('<div class="search" role="search"', '<div class="search tools-outside" role="search"', 1)
    html = html.replace('<form id="event-search" class="hrow"', '<form id="event-search" class="hrow tools-outside"', 1)
    return html.replace('</head>', f'<link rel="stylesheet" href="/tool-buttons-outside.css?v={VERSION}"></head>', 1)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes() + CSS.encode()).hexdigest()[:12]


def publish(out, cfg):
    if not enabled(cfg):
        return
    changed = False
    for path in out.rglob('*.html'):
        html = path.read_text()
        if 'data-tool-link=' in html:
            new = markup(html)
            if new != html:
                path.write_text(new)
                changed = True
    if changed:
        (out / 'tool-buttons-outside.css').write_text(CSS)
