"""Operator 2026-10-22: remove the countdown timer, the "Live deals" banner and
the drop confetti from the phone and desktop site.

The page scripts still find their elements (nothing else changes); one style
rule hides all three on every page that has them, phone and desktop alike.
"""

import hashlib
from pathlib import Path

# #drop = the "Next drop in" timer, #ticker = the "Live deals" banner,
# #drop-confetti = the canvas drop-confetti.js adds (its inline style loses to !important).
SNIPPET = '<style>#ticker,#drop,#drop-confetti{display:none!important}</style>'
# Operator 2026-10-22 (A31, "TAGLINE OKAY"): with the timer hidden, the phone
# tagline no longer shares its row, so it spans it instead of being cut off
# ("1,000+ VERIFIED DE…"). Needs the timer hidden: it would overlap the timer.
TAGLINE = '<style>@media(max-width:899px){.hrow:has(.pwa-header-actions) #tagline{grid-column:1/-1}}</style>'


def enabled(cfg):
    return getattr(cfg, 'hide_timer_ticker_confetti', False) is True


def publish(out, cfg):
    if not enabled(cfg):
        return
    tagline = getattr(cfg, 'tagline_full_row', False) is True
    for path in out.rglob('*.html'):
        html = path.read_text()
        if ('id="ticker"' in html or 'id="drop"' in html) and SNIPPET not in html:
            extra = TAGLINE if tagline and 'id="tagline"' in html else ''
            path.write_text(html.replace('</head>', SNIPPET + extra + '</head>', 1))


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]
