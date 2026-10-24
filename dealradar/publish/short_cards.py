"""Operator (Nishi) 2026-10-24 ~02:55Z, a laptop screenshot of /best-deals-today/: "can you
shorten the card slightly.. as you noted. the other websites are less wordy" (Slickdeals'
and Brad's cards: photo, a two-line name, price, store and time, button). ~03:30Z: "the get
deal button should be slightly smaller... what about the discount promo code. that is
important to have otherwise people will thing the price are wrong"; ~03:35Z: "I don't want
it to be hidden.. like it should show it Enter Code: _______at checkout".

Every deal card, laptop and phone: the home page's static cards and the ones its script
draws, and the cards of every hub.html page (best-deals-today, category, store, price and
event hubs, the related deals on deal pages):

- no headline sentence: it repeated the title, price and store (deals.json and the deal
  page keep it). Not drawn rather than hidden: the home page's HTML budget pays for the rest;
- the title takes two lines at most (the home page already clamped it; on phones the link
  is clamped, so its 44px tap target, which belongs to the heading, is not clipped);
- the card's steps and notes in one visible line, never clipped ("Clip the 20% coupon ->
  Subscribe & Save", "Price is per unit; ... -> Quantity: each, buy 6", "Price varies by
  option; ..."), whether or not the deal needs steps, phones included: the "How to get this
  price" link of phone_first_screen no longer stands in for them;
- every deal with a code: "Enter Code: SAVE20 at checkout" (several: "A, B"), a copy-on-tap
  button on the home page as before. Where a step names a code with a code word ("use code
  SAVE20"), it reads "the code"; a step that only enters it goes; nothing inside other words
  or numbers changes ("Spend $100", "the APP"), so a code can show twice rather than garbled.
  The How box no longer repeats the codes;
- a stated expiry (the headline's "Expires Oct 25.") as an "ends Oct 25" chip;
- the How box keeps only its disclosures, as on the home page (rebate later, price varies,
  shipping not included); on hub pages it was a closed box whose summary repeated the
  requirements ("Coupon required", "Promo code required") and hid the steps and codes.
  "Price not confirmed", the price age and the checkout-price line stay, as do chips, tier
  badges and the source credit;
- a smaller green Get deal (6px 12px); on phones its tap target stays 44px tall (an
  invisible ::after; card_click_1020 makes the whole card its target anyway).

Static and script-drawn home cards stay identical (tests/drop_card_parity.cjs). The phone
CSS budget had no room: with phone_first_screen off too, the home page leaves out the hidden
ticker's styles (phone_first_screen.drop_ticker_rules). Off or missing changes no byte.
"""
import hashlib
import json
import re
from html import escape
from pathlib import Path

from . import hide_timer_ticker, phone_first_screen
from .template_slots import replace_slot

# A code is touched only where a code word names it ("code SAVE20", "promo code: SAVE20", "the coupon
# SAVE20"), as a whole token: that reference reads "the code", unless another code follows ("code A, B"
# stays as written). A step that is only such a reference ("Enter code SAVE20 at checkout") goes. Never
# inside other words or numbers ("Spend $100", "the APP"). {0}: the card's codes, longest first.
KEY = r'''\b(?:the[ \t]+)?(?:(?:promo|coupon|discount)(?:[ \t]+code)?|code)[ \t]*:?[ \t]*["']?'''
MASK = KEY + r'''(?:{0})["']?(?![\w-])(?![ \t]*(?:,|&|\+|and\b|or\b)[ \t]*["']?(?:{0}))'''
DROP = r'''^[ \t]*(?:(?:use|enter|apply)[ \t]+)?''' + KEY + r'''(?:{0})["']?(?:[ \t]+at[ \t]+checkout)?[ \t]*[.!]?[ \t]*$'''
# The headline's last sentence when the deal states an expiry (site._headline): "... Expires Oct 25."
ENDS = r'(?:^| )Expires ([A-Z][a-z]{2} \d{2})\.$'
ANCHOR = '.title a{color:inherit}'  # once in index.html and in hub.html
CSS = ('.card .how-line{font-size:12.5px;line-height:1.4}.code-line{font-size:12.5px;font-weight:600}'
       '.card .actions>.getdeal{padding:6px 12px;border-radius:8px;min-height:0}.card .deal-steps{display:none}'
       # Its 44px tap target grows upward over the chips: the card's own edge (overflow:hidden) is 5px below.
       '@media(max-width:640px){.card .actions>.getdeal::after{content:"";position:absolute;left:0;right:0;'
       'bottom:-4px;height:44px}}')
HUB_CSS = CSS + '.card .title,.card .title a{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}'

# index.html cardHtml, the drawn twin of lines_html and how_html below.
LINES = (r'''    var codeList = (d.codes || []).map(String).filter(Boolean), lineSteps = (d.how || []).map(String);
    if (codeList.length) {
      var codeAlt = codeList.slice().sort(function (a, b) { return b.length - a.length; }).map(function (c) {
        return c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      }).join("|");
      var codeDrop = new RegExp(''' + json.dumps(DROP) + r'''.split("{0}").join(codeAlt), "i");
      var codeMask = new RegExp(''' + json.dumps(MASK) + r'''.split("{0}").join(codeAlt), "gi");
      lineSteps = lineSteps.filter(function (s) { return !codeDrop.test(s); }).map(function (s) {
        return s.replace(codeMask, "the code");
      });
    }
    var lines = dead ? "" : (lineSteps.length ? '<div class="how-line">' + lineSteps.map(esc).join(" &rarr; ") + "</div>" : "") +
'''
         '''      (codeList.length ? '<div class="code-line">Enter Code: ' + codeList.map(function (c) {
        return '<button type="button" class="code" data-code="' + esc(c) + '" aria-label="Copy code ' + esc(c) + '">' +
          esc(c) + "</button>";
      }).join(", ") + " at checkout</div>" : "");
''')
HOW = '''      how = '<div class="how" data-requirements><b>How to get this price:</b> ' +
        (extra ? esc(extra) + (steps || codes ? "<br>" : "") : "") +
        (steps || (codes || extra ? "" : "the steps are in the deal post")) +
        (codes ? (steps ? "<br>" : "") + "Code " + codes : "") + "</div>"'''
SHORT_HOW = ('''      how = (extra || !lines ? '<div class="how" data-requirements><b>How to get this price:</b> ' +'''
             '''\n        (extra ? esc(extra) : "the steps are in the deal post") + "</div>" : "")''')
# The script card's expiry chip, after "select sizes", where card_html draws it.
COUNTDOWN = '    var countdown = d.spotlight && spotlightTime(d.spotlight.ends_at, Date.now());\n'
ENDS_JS = ('    var ends = !dead && !d.spotlight && /' + ENDS + '/.exec(d.headline || "");\n'
           '    if (ends) metarow += \'<span class="chip">ends \' + ends[1] + "</span>";\n')
SLOTS = (
    ('      (d.headline ? \'<div class="headline">\' + esc(d.headline) + "</div>" : "") +\n', ''),
    ('    var how = "";\n', LINES + '    var how = "";\n'),
    (HOW, SHORT_HOW),
    ('      how +\n      \'<div class="metarow">\'', '      lines + how +\n      \'<div class="metarow">\''),
    (COUNTDOWN, ENDS_JS + COUNTDOWN),
)


def enabled(cfg):
    return getattr(cfg, 'short_cards', False) is True


def steps(d):
    """Every step and note of the card (per unit, price varies, quantity...); where a code word names
    one of its codes, the code line says it (MASK and DROP above)."""
    shown = [str(step) for step in d.get('how') or []]
    codes = [str(c) for c in d.get('codes') or [] if str(c)]
    if not codes:
        return shown
    alt = '|'.join(re.escape(c) for c in sorted(codes, key=len, reverse=True))
    drop, mask = (re.compile(pattern.replace('{0}', alt), re.I | re.A) for pattern in (DROP, MASK))
    return [mask.sub('the code', s) for s in shown if not drop.fullmatch(s)]


def ends_html(d):
    """The headline's expiry as a chip ("ends Oct 25"): the headline is not on the card."""
    m = re.search(ENDS, str(d.get('headline') or ''), re.A)
    return f'<span class="chip">ends {m[1]}</span>' if m and d.get('status') != 'dead' and not d.get('spotlight') else ''


def lines_html(d, buttons):
    """The steps line and the code line (buttons: the home page's copy-on-tap codes)."""
    if d.get('status') == 'dead':
        return ''
    e = lambda v: escape(str(v))
    shown = steps(d)
    html = '<div class="how-line">' + ' &rarr; '.join(map(e, shown)) + '</div>' if shown else ''
    codes = [f'<button type="button" class="code" data-code="{e(c)}" aria-label="Copy code {e(c)}">{e(c)}</button>'
             if buttons else f'<code class="code">{e(c)}</code>' for c in map(str, d.get('codes') or []) if c]
    return html + ('<div class="code-line">Enter Code: ' + ', '.join(codes) + ' at checkout</div>' if codes else '')


def how_html(d, lines):
    """The open How box of every card (the home wall's keep_first_cards.how_html): only its disclosures."""
    extra = (d.get('checkout') or {}).get('card') or ''
    if not (d.get('needs_steps') or extra) or d.get('status') == 'dead' or (lines and not extra):
        return ''
    return ('<div class="how" data-requirements><b>How to get this price:</b> '
            + (escape(extra) if extra else 'the steps are in the deal post') + '</div>')


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]


def publish(out, cfg):
    if not enabled(cfg):
        return
    for path in out.rglob('*.html'):
        html = path.read_text(encoding='utf-8')
        home = path == out / 'index.html'
        if not home and '<article class="card' not in html:
            continue
        slot = dict(template_name='index.html' if home else 'hub.html', slot_name='short-cards')
        for token, new in SLOTS if home else ():
            html = replace_slot(html, token, new, **slot)
        if home and hide_timer_ticker.enabled(cfg):  # room in the phone CSS budget, as phone_first_screen makes
            html = phone_first_screen.drop_ticker_rules(out, html)
        html = replace_slot(html, ANCHOR, ANCHOR + (CSS if home else HUB_CSS), **slot)
        path.write_text(html, encoding='utf-8')
