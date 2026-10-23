"""Quality loop 2026-10-23 (phone LCP / taps): the home wall keeps the server's cards.

Phones tapped Get deal on 1.9% of home views (laptops 35.7%), and Lighthouse put the
phone LCP on card text seconds after first paint: the drop module's first render
replaced the whole server wall (content.innerHTML) with the same cards in the same
order. With keep_first_cards on:

- a fresh-view render that shows the cards already on screen, in the same order,
  patches them in place (keep-first-cards.js in drop-fresh.js), leaving exactly the
  markup the replacement wrote, so nothing looks different;
- a publication whose drop mark is behind the visitor's clock keeps the drop it
  painted (already spaced, mixed and filtered) until the clock's next mark, where the
  usual drop takes over (behind the pill once the visitor has scrolled);
- the home wall's static cards draw their How box as the module does (open, with the
  copy-code button) instead of the collapsed no-JS <details>, so hydration paints no
  new, larger text on the first screen (in the 390x844 probe that box was the LCP
  element). Category and store pages keep their <details>; the home page's gzip size
  stays about the same (the open box repeats, the per-price "Pay now" lines go).

Off or missing changes no byte.
"""
import hashlib
import re
from html import escape
from pathlib import Path

from . import card_tidy, drop_fresh
from .template_slots import replace_slot

SCRIPT = Path(__file__).with_name('templates') / 'keep-first-cards.js'
RENDER = '    content.innerHTML = html;\n    if (freshUI) freshUI.paintFreshDrop();'
KEEP = '    if (!freshUI || !freshUI.keep(content, html)) content.innerHTML = html;\n    if (freshUI) freshUI.paintFreshDrop();'


def enabled(cfg):
    return getattr(cfg, 'keep_first_cards', False) is True and drop_fresh.enabled(cfg)


def how_html(d, cfg):
    """index.html cardHtml's How box (two steps with card_tidy), for the home wall's static cards."""
    extra = (d.get('checkout') or {}).get('card') or ''
    if not (d.get('needs_steps') or extra) or d.get('status') == 'dead':
        return ''
    e = lambda v: escape(str(v if v is not None else ''))
    codes = ''.join(f'<button type="button" class="code" data-code="{e(c)}" aria-label="Copy code {e(c)}">{e(c)}</button>'
                    for c in d.get('codes') or [])
    steps = list(d.get('how') or [])
    if card_tidy.enabled(cfg):
        seen, unique = set(), []
        for step in steps:
            k = re.sub(r'\W+', ' ', str(step).lower(), flags=re.ASCII).strip()
            if k and k not in seen:
                seen.add(k)
                unique.append(step)
        text = ' &rarr; '.join(map(e, unique[:2])) + (
            '<span class="how-rest" hidden> &rarr; ' + ' &rarr; '.join(map(e, unique[2:])) +
            '</span><br><button type="button" class="how-more" aria-expanded="false">More details</button>'
            if len(unique) > 2 else '')
    else:
        text = ' &rarr; '.join(map(e, steps))
    return ('<div class="how" data-requirements><b>How to get this price:</b> '
            + (e(extra) + ('<br>' if text or codes else '') if extra else '')
            + (text or ('' if codes or extra else 'the steps are in the deal post'))
            + (('<br>' if text else '') + 'Code ' + codes if codes else '') + '</div>')


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + SCRIPT.read_bytes() + Path(__file__).read_bytes()).hexdigest()[:12]


def publish(out, cfg):
    """Patch the published drop-fresh.js, its versioned import and the wall's one replacement."""
    if not enabled(cfg):
        return
    asset = out / drop_fresh.ASSET
    script = old = asset.read_text(encoding='utf-8')
    slot = dict(template_name=drop_fresh.ASSET, slot_name='keep-first-cards')
    for token, new in (
            ('export default function (host) {\n',
             'export default function (host) {\n  var freshClock = host.dropPickIndex;\n'
             '  host = Object.create(host, {dropPickIndex: {value: function () { return freshMark(); }}});\n'),
            ('  var style = document.createElement("style");',
             SCRIPT.read_text() + '  var style = document.createElement("style");'),
            ('  return {freshView, freshSize, shuffleDropPicks, ',
             '  return {freshView, freshSize, keep: freshKeep,\n'
             '    shuffleDropPicks: function (rows, seed) {\n'
             '      freshDecide(rows); return shuffleDropPicks(rows, seed === freshClock() ? freshMark() : seed);\n    }, ')):
        script = replace_slot(script, token, new, **slot)
    home = out / 'index.html'
    version = lambda text: '/drop-fresh.js?v=' + hashlib.sha256(text.encode()).hexdigest()[:12]
    html = replace_slot(home.read_text(encoding='utf-8'), version(old), version(script),
                        template_name='index.html', slot_name='keep-first-cards-version')
    html = replace_slot(html, RENDER, KEEP, template_name='index.html', slot_name='keep-first-cards')
    asset.write_text(script, encoding='utf-8')
    home.write_text(html, encoding='utf-8')
