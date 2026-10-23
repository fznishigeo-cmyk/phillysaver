"""Operator 2026-10-22 ("yes great idea implement it"): on phones, each deal page pins a
slim bar to the bottom of the screen once its Get deal button has scrolled out
of view above: the deal's price and the same Get deal link.

The bar's link copies the main button's attributes (href, target, rel, data-*),
so deal-actions.js and click-signal.js count its taps as they do the button's.
Nothing opens without a tap. Laptops never show it. Off adds no bytes.
"""

import hashlib
import re
from pathlib import Path

# The page's main button: the detail block's Get deal, just before its disclosure.
MAIN = re.compile(r'<a class="getdeal" (href="[^"]*"[^>]*)>([^<]+)</a><p class="disclosure">')
PRICE = re.compile(r'<p class="detail-price">[^<]*<strong>([^<]+)</strong>')
HEIGHT = 'calc(56px + env(safe-area-inset-bottom,0px))'
# Operator 2026-10-22: "a bit more transparent ... less obtrusive": frosted glass,
# slimmer, a compact solid button (contrast unchanged). Without backdrop-filter
# the glass is nearly opaque so the text never sits on busy content.
GLASS, DARK = 'rgba(255,255,255,%s)', 'rgba(17,24,39,%s)'
STYLE = ('<style>.buybar{display:none}@media (max-width:640px){'
         '.buybar{position:fixed;left:0;right:0;bottom:0;z-index:10;display:flex;align-items:center;'
         'justify-content:space-between;gap:12px;box-sizing:border-box;'
         f'height:{HEIGHT};padding:6px 16px calc(6px + env(safe-area-inset-bottom,0px));'
         f'background:{GLASS % .72};-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px);'
         'color:var(--fg);border-top:1px solid var(--line);'
         'opacity:0;visibility:hidden;transition:opacity .2s,visibility .2s}'
         '.buybar strong{font-size:20px;line-height:1.25;white-space:nowrap}'
         '.buybar .getdeal{flex:0 0 auto;width:auto;min-height:44px;padding:8px 22px;font-size:17px;'
         'display:inline-flex;align-items:center;justify-content:center}'
         '.buybar-on .buybar{opacity:1;visibility:visible}'
         f'.buybar-on body{{padding-bottom:{HEIGHT}}}}}'
         f'@media (max-width:640px) and (prefers-color-scheme:dark){{.buybar{{background:{DARK % .7}}}}}'
         '@supports not ((-webkit-backdrop-filter:blur(1px)) or (backdrop-filter:blur(1px))){'
         f'.buybar{{background:{GLASS % .92}}}'
         f'@media (prefers-color-scheme:dark){{.buybar{{background:{DARK % .92}}}}}}}'
         '@media (prefers-reduced-motion:reduce){.buybar{transition:none}}</style>')
# Shown only while the main button is out of view above (scrolled past it): the
# root reaches far below the screen, so a jump past the button still crosses it.
SCRIPT = ('<script>(function(){var b=document.querySelector(".deal-detail>.getdeal");'
          'if(!b||!("IntersectionObserver" in window))return;'
          'new IntersectionObserver(function(e){document.documentElement.classList.toggle("buybar-on",'
          '!e[0].isIntersecting)},{rootMargin:"0px 0px 100000px 0px"}).observe(b)})()</script>')


def enabled(cfg):
    return getattr(cfg, 'sticky_buy_bar', False) is True


def bar(html):
    main = MAIN.search(html)
    if not main:
        return ''
    price = PRICE.search(html)
    return ('<div class="buybar" data-buybar>' + (f'<strong>{price[1]}</strong>' if price else '')
            + f'<a class="getdeal" {main[1]}>{main[2]}</a></div>')


def publish(out, cfg):
    if not enabled(cfg):
        return
    for path in (out / 'deal').glob('*/index.html'):
        html = path.read_text()
        add = bar(html)
        if add and 'data-buybar' not in html:
            html = html.replace('</head>', STYLE + '</head>', 1)
            path.write_text(html.replace('</main>', add + SCRIPT + '</main>', 1))


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]
