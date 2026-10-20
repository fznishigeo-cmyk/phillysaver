"""Operator 2026-10-20: the homepage AI Analysis / All Discounts links open our tools in a new tab."""

import hashlib
import re
from pathlib import Path

LINK = re.compile(r'<a\b([^>]*\bdata-tool-link="[^"]*")>')


def enabled(cfg):
    return getattr(cfg, 'tools_new_tab', False) is True


def markup(html):
    # The search page stays open behind the tool; noopener keeps the tabs independent.
    return LINK.sub(lambda m: m[0] if ' target=' in m[1] else
                    '<a' + m[1] + ' target="_blank" rel="noopener">', html)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]


def publish(out, cfg):
    if not enabled(cfg):
        return
    for path in out.rglob('*.html'):
        html = path.read_text()
        if 'data-tool-link=' in html:
            path.write_text(markup(html))
