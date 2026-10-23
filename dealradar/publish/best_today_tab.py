"""Operator 2026-10-23 ~20:00Z (A48): "yes add that button". /best-deals-today/ (today's
top 30) had no link on the homepage; a "Best today" pill now sits right after Trending
in the home category row.

It wears .cattab, so every tab rule (phone scroll row, desktop wrap, focus ring, dark
mode) styles it like its neighbours, but it is a link, not a filter tab: it has no
data-i, so renderTabs binds its filter click only to the tabs that have one and never
marks the pill active, and each redraw writes it where the server did (nothing moves
when the page takes over). Category and store pages keep their row. No GA4 event: no
generic nav-link event exists; the page's own page_view carries the home as referrer.
It costs ~60 gzip bytes of home HTML (bounded in tests/test_best_today_tab.py).
Off changes no byte.
"""
import hashlib
from pathlib import Path

from .template_slots import replace_slot

PILL = '<a class="cattab" href="/best-deals-today/">Best today</a>'
SLOTS = (
    # The server's row (seo.tabs_html): right after Trending.
    ('data-i="0">Trending</a>', 'data-i="0">Trending</a>' + PILL),
    # renderTabs draws the same row again after every category tap.
    ('esc(g[0]) + "</a>";', 'esc(g[0]) + "</a>" + (i ? "" : \'' + PILL + '\');'),
    # Only the filter tabs (data-i) filter in place; the pill navigates.
    ('nav.querySelectorAll(".cattab").forEach(', 'nav.querySelectorAll(".cattab[data-i]").forEach('),
)


def enabled(cfg):
    return getattr(cfg, 'best_today_tab', False) is True


def publish(out, cfg):
    if not enabled(cfg):
        return
    home = out / 'index.html'
    html = home.read_text(encoding='utf-8')
    for token, new in SLOTS:
        html = replace_slot(html, token, new, template_name='index.html', slot_name='best-today-tab')
    home.write_text(html, encoding='utf-8')


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]
