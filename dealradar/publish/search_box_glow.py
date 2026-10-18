"""Optional search attention and an early Amazon exit; absent preserves release bytes."""

import hashlib
from pathlib import Path

from . import search_analyze, search_circle, search_more
from .template_slots import replace_slot

# Paint behind the unchanged input border/padding, so even the text coordinates stay put.
CSS = ('.searchfield{isolation:isolate}'
       '.searchfield::before{content:"";position:absolute;inset:0;z-index:-1;pointer-events:none;'
       'border:3px solid transparent;border-radius:12px;background:linear-gradient(var(--search-bg),'
       'var(--search-bg)) padding-box,conic-gradient(from var(--wpspin),#e60000,#ff7a00,#ffd400,'
       '#34c759,#00bcd4,#7047eb,#e60000) border-box;animation:wpspin 6s linear infinite}'
       '.searchfield #q{background:transparent;border-color:transparent}'
       '.searchfield:focus-within::before,.searchfield:has(#q:not(:placeholder-shown))::before{animation-play-state:paused}'
       '#analyze-search{isolation:isolate;overflow:hidden}'
       '#analyze-search::after{content:"";position:absolute;inset:0;z-index:-1;pointer-events:none;'
       'background:linear-gradient(105deg,transparent 35%,rgba(255,196,0,.4) 50%,transparent 65%) '
       '150% 0/250% 100% no-repeat;animation:wpshine 3.2s ease-in-out infinite}'
       '#analyze-search::before{content:"\\2726";content:"\\2726"/"";position:absolute;left:4px;'
       'top:50%;margin-top:-5px;font-size:8px;line-height:10px;color:#ffcc00;animation:wptwinkle 1.6s ease-in-out infinite}'
       '@media(prefers-reduced-motion:reduce){.searchfield::before,#analyze-search::before{animation:none}'
       '#analyze-search::after{display:none}}')


BUDGET = "Get today's best Amazon price drops."


def enabled(cfg):
    return bool(getattr(cfg, "search_box_glow", False))


def swap(text, old, new):
    return replace_slot(text, old, new, template_name="index.html", slot_name="search-box-glow")


def template(html, cfg):
    if not enabled(cfg):
        return html
    css = CSS
    if not search_circle.enabled(cfg):
        css += search_circle.css()
    if not (search_circle.enabled(cfg) and search_more.enabled(cfg)):
        css += search_more.BOLD
    html = html.replace('</style>', css + '</style>', 1)
    if search_analyze.enabled(cfg):
        html = swap(html, '<p class="search-amazon"><a class="getdeal" data-analyze-fallback',
                    '<p id="search-result-top" class="search-amazon-top"><a class="getdeal" data-analyze-fallback')
        html = swap(html, 'noopener">Search Amazon</a><small>',
                    'noopener">\' + (task.reason ? "Search Amazon" : \'See all &quot;\' + esc(task.asin || searchAmazonQuery() || "products") + \'&quot; on Amazon →\') + \'</a><small>')
        html = swap(html, '      html = \'<h2>\' + (task.local ?',
                    '      html = \'<h2>Search results</h2>\' + analyzeFallback(task) + \'<h2>\' + (task.local ?')
        html = swap(html,
                    '(task.kind === "other" ? \'<p>Type a product name to search our verified deals.</p>\' : \'\') + analyzeFallback(task)',
                    'analyzeFallback(task) + (task.kind === "other" ? \'<p>Type a product name to search our verified deals.</p>\' : \'\')')
        # Operator 2026-10-18: visitor wording never says "limit".
        html = swap(html, 'budget_exhausted:"Today\'s live price checks have reached their limit."',
                    'budget_exhausted:"' + BUDGET + '"')
        # Operator 2026-10-18: no "busy", "unavailable" or "too long" either; the Amazon button sits right below.
        for text, count in (("Live price checks are busy. Please try again shortly.", 2),
                            ("Live price checks are unavailable right now.", 4),
                            ("The price check took too long. Please try again.", 1),
                            # Operator 2026-10-18: a gated product is never a dead end either.
                            ("This product is not shown in our results.", 1)):
            html = replace_slot(html, text, BUDGET, count, template_name="index.html", slot_name="search-box-glow")
    return html


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]
