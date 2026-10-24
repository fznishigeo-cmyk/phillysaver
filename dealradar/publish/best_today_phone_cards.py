"""Operator 2026-10-24: the home page's phone cards (phone_first_screen) on /best-deals-today/
too, the page the Facebook ad visitors land on.

Phones only (<= 640px), that page only, its card grid only (not its header): one deal per
row, the photo on the left (96px), price, title (two lines), chips, Get deal and the source
credit on the right, with phone_first_screen's gaps; the headline sentence is left out where
the card's tier badge already says how the price was verified (short_cards draws it on no
card). On phones the title's link is clamped, so its 44px tap target is not clipped. Laptops
unchanged. Off changes no byte.
"""
import hashlib
from pathlib import Path

from .template_slots import replace_slot

PAGE = 'best-deals-today/index.html'
# In the head after the page's own styles; the grid wins over hub.html's two-column phone grid by
# specificity too (best_today_header's later styles reach only the header).
CSS = ('<style>@media(max-width:640px){'
       '.grid:has(>.card){grid-template-columns:minmax(0,1fr);gap:8px;margin-top:4px}'
       '.grid>.card{flex-direction:row}'
       '.grid>.card>.media{flex:0 0 96px;height:auto;min-height:96px;border-bottom:0;border-right:1px solid var(--line)}'
       '.grid>.card>.body{padding:5px 8px;gap:3px;min-width:0}'
       '.grid>.card .condtag{inset:34px auto auto 6px;max-width:84px;white-space:normal}'
       '.grid>.card .pricerow{line-height:22px}.grid>.card .actions{margin:0}'
       '.grid>.card .metarow{gap:4px}.grid>.card .chip{padding-inline:5px}'
       '.grid>.card .title a{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}'
       '.grid>.card:has(.badge) .headline{display:none}}</style>')


def enabled(cfg):
    return getattr(cfg, 'best_today_phone_cards', False) is True


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]


def publish(out, cfg):
    if not enabled(cfg):
        return
    page = out / PAGE
    html = replace_slot(page.read_text(encoding='utf-8'), '</head>', CSS + '</head>',
                        template_name='hub.html', slot_name='best-today-phone-cards')
    page.write_text(html, encoding='utf-8')
