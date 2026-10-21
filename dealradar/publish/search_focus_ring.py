"""Operator 2026-10-21 (option A): no dark ring around the search box on click or tap;
keyboard (Tab) users keep it."""

import hashlib
from pathlib import Path

# Text inputs match :focus-visible on every focus, so CSS alone cannot tell a
# click from a Tab. The page marks Tab on <html>; a click or tap clears it.
SNIPPET = ('<style>html:not([data-kbd]) .searchfield #q:focus-visible{outline:0}</style>'
           '<script>(function(d){var r=d.documentElement;'
           'd.addEventListener("keydown",function(e){if(e.key==="Tab")r.setAttribute("data-kbd","")},true);'
           'd.addEventListener("pointerdown",function(){r.removeAttribute("data-kbd")},true)})(document)</script>')


def enabled(cfg):
    return getattr(cfg, 'search_focus_ring_keyboard_only', False) is True


def publish(out, cfg):
    if not enabled(cfg):
        return
    for path in out.rglob('*.html'):
        html = path.read_text()
        if 'class="searchfield"' in html and SNIPPET not in html:
            path.write_text(html.replace('</head>', SNIPPET + '</head>', 1))


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]
