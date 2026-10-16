"""Optional public follow links; an empty configuration changes no bytes."""

import hashlib
import json
import re
from html import escape
from pathlib import Path

from ..config import social_profiles
from .seo import _json_ld

_BLOCK = re.compile(r"<!-- wp-profiles -->.*?<!-- /wp-profiles -->\n?", re.S)
_CSS = ('.social-follow{display:flex;flex-wrap:wrap;align-items:center;gap:0 12px;'
        'font-size:13px;color:var(--muted)}'
        '.social-follow a{display:inline-flex;align-items:center;min-height:44px}'
        '@media(min-width:961px){footer .social-follow{justify-content:center}}')


def site_version(version, cfg):
    profiles = social_profiles(cfg.social_profiles)
    if not profiles:
        return version
    return hashlib.sha256(version.encode() + json.dumps(profiles, sort_keys=True).encode()
                          + Path(__file__).read_bytes()).hexdigest()[:12]


def publish(out, cfg, site_url):
    profiles = social_profiles(cfg.social_profiles)
    row = '<nav class="social-follow" aria-label="Follow wowpriced"><span>Follow wowpriced</span>' + ''.join(
        f'<a href="{escape(url, quote=True)}" rel="noopener">{network.title()}</a>'
        for network, url in profiles.items()) + '</nav>'

    def block(text):
        return '<!-- wp-profiles -->' + text + '<!-- /wp-profiles -->\n'

    for path in sorted(out.rglob("*.html")):
        original = path.read_text(encoding="utf-8")
        page = _BLOCK.sub("", original)
        if profiles:
            page = page.replace('</head>', block('<style>' + _CSS + '</style>') + '</head>', 1)
            page = page.replace('</footer>', block(row) + '</footer>', 1)
            if path == out / 'join.html':
                page = page.replace('</main>', block(row) + '</main>', 1)
            if path == out / 'index.html':
                page = page.replace('</head>', block(_json_ld({
                    '@context': 'https://schema.org', '@type': 'Organization',
                    'name': 'wowpriced', 'url': site_url + '/',
                    'sameAs': list(profiles.values())})) + '</head>', 1)
        if page != original:
            path.write_text(page, encoding="utf-8")
