"""Shorter cards and a livelier search; absent or false preserves release bytes.

Operator 2026-10-18 (before the 2pm ad): long "How to get this price" steps fold behind
"More details", the "in cart" chip says what it means, the Amazon button names the price
drops, Analyze looks like AI, and search results run on without an empty filler box.
"""

import hashlib
from pathlib import Path

from . import search_analyze, search_box_glow, search_more
from .template_slots import replace_slot

CSS = ('.how-more{display:inline-flex;align-items:center;min-height:44px;padding:0;border:0;background:none;'
       'color:var(--accent);font-size:12.5px;font-weight:700;text-decoration:underline;cursor:pointer}'
       '.how-more:focus-visible{outline:2px solid var(--accent);outline-offset:2px}'
       # AI colors at rest; on hover/focus/tap a rainbow "charge" rises from the bottom and keeps flowing.
       '#analyze-search{background:linear-gradient(90deg,#ef4444,#f59e0b,#22c55e,#06b6d4,#8b5cf6,#ec4899,#ef4444) '
       '0 100%/200% 0% repeat-x,linear-gradient(120deg,#5b21b6,#9333ea 50%,#db2777);color:#fff;'
       'text-shadow:0 1px 2px rgba(0,0,0,.5);box-shadow:0 3px 10px rgba(147,51,234,.35);'
       'transition:background-size 1s cubic-bezier(.2,.8,.2,1),box-shadow .3s}'
       '#analyze-search:hover,#analyze-search:focus-visible,#analyze-search:active{background-size:200% 100%,auto;'
       'box-shadow:inset 0 0 0 2px rgba(255,255,255,.9),0 0 16px 3px rgba(236,72,153,.55);animation:wpcharge 1.4s linear infinite}'
       # A smaller pill with space above and below; the transparent border keeps the 44 px tap area.
       '.searchfield #analyze-search{border:4px solid transparent;background-clip:padding-box;padding:0 14px}'
       '@keyframes wpcharge{to{background-position:-200% 100%,0 0}}'
       '@media(prefers-reduced-motion:reduce){#analyze-search{transition:none}'
       '#analyze-search:hover,#analyze-search:focus-visible,#analyze-search:active{animation:none}}'
       '#content:has(#search-more) .gridfill{display:none}'
       '.join-why{display:none}@media(min-width:1500px){.join-why{display:inline}}'
       # Operator 2026-10-18: phones say "Analyze" too (not "Go"); the clear button and text make room.
       '@media(max-width:600px){.searchfield .analyze-long{display:inline}.searchfield .analyze-short{display:none}'
       '.searchfield #analyze-search{padding:0 8px 0 15px;border-width:5px}.searchfield #q{padding-right:130px}'
       '.searchfield #clearsearch{right:101px}}')

STEPS = '      var steps = (d.how || []).map(esc).join(" &rarr; ");\n'
SHORT = ('      // At most two steps show; the rest (often repeats from several posts) open on tap.\n'
         '      var seenStep = {}, allSteps = (d.how || []).filter(function (s) {\n'
         '        var k = String(s).toLowerCase().replace(/\\W+/g, " ").trim();\n'
         '        return k && !seenStep[k] && (seenStep[k] = true);\n'
         '      });\n'
         '      var steps = allSteps.slice(0, 2).map(esc).join(" &rarr; ") + (allSteps.length > 2\n'
         '        ? \'<span class="how-rest" hidden> &rarr; \' + allSteps.slice(2).map(esc).join(" &rarr; ") +\n'
         '          \'</span><br><button type="button" class="how-more" aria-expanded="false">More details</button>\' : "");\n')
CHIP = 'metarow += \'<span class="chip">\' + esc(c).replace(/_/g, " ") + "</span>";'
CHIP_NEW = ('metarow += \'<span class="chip">\' + (c === "in_cart" ? "price drops in cart" : '
            'esc(c).replace(/_/g, " ")) + "</span>";')
CLICK = ('    if (button) recoverSearch(button.getAttribute("data-search-kind"), button.getAttribute("data-search-value"));\n'
         '  });\n')
TOGGLE = ('  document.getElementById("content").addEventListener("click", function (e) {\n'
          '    var more = e.target.closest && e.target.closest(".how-more");\n'
          '    if (!more) return;\n'
          '    var rest = more.parentNode.querySelector(".how-rest");\n'
          '    rest.hidden = !rest.hidden;\n'
          '    more.setAttribute("aria-expanded", String(!rest.hidden));\n'
          '    more.textContent = rest.hidden ? "More details" : "Fewer details";\n'
          '  });\n')
TOP = ('" target="_blank" rel="sponsored nofollow noopener">See all &quot;\' +\n'
       '      esc(q) + \'&quot; on Amazon\\u00a0→</a>')
TOP_NEW = ('" target="_blank" rel="sponsored nofollow noopener">Search all of Amazon for &quot;\' +\n'
           '      esc(q) + \'&quot; price drops\\u00a0→</a>')

# Analyze's top Amazon button (task 301): a reason to click through us, not just "Search Amazon".
ANALYZE_TOP = ('(task.reason ? "Search Amazon" : \'See all &quot;\' + esc(task.asin || searchAmazonQuery() || "products") + '
               '\'&quot; on Amazon →\')')
ANALYZE_TOP_NEW = ('(task.reason ? "See today\'s Amazon price drops →" : \'Search all of Amazon for &quot;\' + '
                   'esc(task.asin || searchAmazonQuery() || "products") + \'&quot; price drops →\')')

# Analyze's related deals: only cards with a product photo (operator 2026-10-18: "images not showing up").
RELATED = ('(task.related.length ? \'<div class="analyze-related"><h2>Related verified deals</h2><div class="grid">\' +\n'
           '          task.related.map(cardHtml).join("")')
RELATED_NEW = ('(task.related.some(function (d) { return d.image_url; }) ? '
               '\'<div class="analyze-related"><h2>Related verified deals</h2><div class="grid">\' +\n'
               '          task.related.filter(function (d) { return d.image_url; }).map(cardHtml).join("")')


def enabled(cfg):
    return bool(getattr(cfg, "card_tidy", False))


def swap(text, old, new):
    return replace_slot(text, old, new, template_name="index.html", slot_name="card-tidy")


def template(html, cfg):
    if not enabled(cfg):
        return html
    html = html.replace('</style>', CSS + '</style>', 1)
    html = swap(html, STEPS, SHORT)
    html = swap(html, CHIP, CHIP_NEW)
    html = swap(html, CLICK, CLICK + TOGGLE)
    html = swap(html, '<a id="subscribe" class="getdeal" href="/join">Join free</a>',
                '<a id="subscribe" class="getdeal" href="/join">Join free<span class="join-why"> · price alerts</span></a>')
    if search_more.enabled(cfg):
        html = swap(html, TOP, TOP_NEW)
    if search_box_glow.enabled(cfg) and search_analyze.enabled(cfg):
        html = swap(html, ANALYZE_TOP, ANALYZE_TOP_NEW)
    if search_analyze.enabled(cfg):
        html = swap(html, RELATED, RELATED_NEW)
    return html


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]
