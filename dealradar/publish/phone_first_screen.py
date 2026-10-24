"""Operator (Nishi) 2026-10-23 ~21:50Z: "The fix: the first 3 deals and their buttons fit
on the first screen, with fewer words per card." On computers about 36 of 100 home
visitors tap a deal, on phones about 2; most visitors (all Facebook ad visitors) are on
phones, and at 390x844 no deal card and no Get deal button was on the first screen.

Phones only (<= 640px), the home page only; laptops and every other page unchanged:

- above the deals: less padding, a 48px search box, the two tool buttons side by side
  (44px), no empty search-status line, a shorter tagline row, tighter tabs, Amazon strip
  and filter row (the search box and the category tabs stay);
- one deal per row, photo on the left: price, title (two lines), store and verification
  chips, Get deal and the source credit on the right;
- fewer words: the headline sentence, which repeats the title, price and store, is left
  out where the card's tier badge already says how the price was verified (cards without
  a badge keep it); a step deal whose How box holds only steps and codes shows the tool
  pages' "How to get this price" link to its deal page (steps, codes, copy button) in
  place of the box. Price, store, "Price not confirmed", the Amazon checkout-price line,
  the How box's own disclosures (rebate later, price varies, shipping) and the credit stay.

The static cards draw the link exactly as the page script does (keep_first_cards keeps the
server's cards, so nothing is redrawn). The phone CSS budget was nearly full, so the
home page also leaves out the "Live deals" ticker's styles while that ticker is hidden
(hide_timer_ticker_confetti: display:none on every width since 2026-10-22).

Off or missing changes no byte.
"""
import hashlib
import re
from html import escape
from pathlib import Path

from . import hide_timer_ticker, keep_first_cards, tool_pages_1020
from .template_slots import replace_slot

STEPS = '<a class="deal-steps" href="{}">How to get this price</a>'
# index.html cardHtml: the end of the How box; page and extra are cardHtml's own variables.
HOW_END = '(codes ? (steps ? "<br>" : "") + "Code " + codes : "") + "</div>";'
HOW_LINK = HOW_END[:-1] + (' + (d.needs_steps && page && !extra ? \'<a class="deal-steps" href="\' + esc(page) + '
            '\'">How to get this price</a>\' : "");')
CSS = ('<style>.deal-steps{display:none}@media(max-width:640px){'
       'header>.hrow{padding-top:6px}.searchfield #q{height:48px}.searchfield svg{top:14px}'
       '.searchfield #clearsearch{top:2px}.search .tool-buttons{flex-flow:row wrap;margin-top:6px}'
       ':is(.search,#event-search) .tool-buttons :is(#analyze-search,#search-discounts)'
       '{flex:1 1 0;min-width:max-content;min-height:44px;padding:0 4px;font-size:14px}'
       '#searchcount:empty{min-height:0;margin:0}.hnav>.tag{min-height:24px}header{margin-bottom:48px}'
       '.hrow #cattabs{padding-block:2px}.amazon-inner{padding-block:5px}#controls{margin:4px 0 0}'
       '#content{--offscreen-card-height:152px}'
       '#content>.grid:not(.spotlight-grid){grid-template-columns:minmax(0,1fr);gap:8px;margin-top:4px}'
       '#content>.grid>.card:not(.spotlight){flex-direction:row}'
       '#content>.grid>.card:not(.spotlight)>.media{flex:0 0 96px;height:auto;min-height:96px;'
       'border-bottom:0;border-right:1px solid var(--line)}'
       '#content>.grid>.card:not(.spotlight)>.body{padding:5px 8px;gap:3px;min-width:0}'
       '#content>.grid>.card:not(.spotlight) .condtag{inset:34px auto auto 6px;max-width:84px;white-space:normal}'
       '#content>.grid>.card:not(.spotlight) .gonetag{left:6px}'
       '#content>.grid>.card .pricerow{line-height:22px}#content>.grid>.card .actions{margin:0}'
       '#content>.grid>.card .metarow{gap:4px}#content>.grid>.card .chip{padding-inline:5px}'
       '#content>.grid>.card:has(.badge) .headline,.card:has(.deal-steps) .how{display:none}'
       '.deal-steps{display:block;width:fit-content;font-size:12.5px;line-height:16px;font-weight:700;color:var(--accent);'
       'text-decoration:underline}}</style>')


def enabled(cfg):
    return getattr(cfg, 'phone_first_screen', False) is True and keep_first_cards.enabled(cfg)


def steps_html(d, page, cfg):
    """The static card's link, as cardHtml draws it after the How box."""
    if not enabled(cfg) or not page or not d.get('needs_steps') or d.get('status') == 'dead':
        return ''
    return '' if (d.get('checkout') or {}).get('card') else STEPS.format(escape(page))


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]


def drop_ticker_rules(out, html):
    """The hidden ticker's rules (the block tool_pages_1020 moves): room in the phone budget."""
    start = html.find('.tk{--tk-teal')
    end = html.find('.card.flash{', start)
    if 0 <= start < end:
        return html[:start] + html[end:]
    asset = out / tool_pages_1020.ASSET  # moved there by the home tool links: drop them there
    tools = (tool_pages_1020.TEMPLATES / tool_pages_1020.ASSET).read_text()
    if asset.exists() and asset.read_text().startswith('.tk{--tk-teal') and asset.read_text().endswith(tools):
        asset.write_text(tools)
        version = hashlib.sha256(tools.encode()).hexdigest()[:12]
        html = re.sub(r'(/' + re.escape(tool_pages_1020.ASSET) + r'\?v=)[0-9a-f]+', lambda m: m[1] + version, html)
    return html


def publish(out, cfg):
    if not enabled(cfg):
        return
    home = out / 'index.html'
    html = home.read_text(encoding='utf-8')
    html = replace_slot(html, HOW_END, HOW_LINK, template_name='index.html', slot_name='phone-first-steps')
    if hide_timer_ticker.enabled(cfg):
        html = drop_ticker_rules(out, html)
    home.write_text(html.replace('</head>', CSS + '</head>', 1), encoding='utf-8')
