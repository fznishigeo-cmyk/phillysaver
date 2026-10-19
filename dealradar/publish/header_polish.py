"""Optional two-row home header; no runtime script or changes to other headers."""

import hashlib
import re
from pathlib import Path

from .template_slots import replace_slot

CSS = (
    ':root{--tab-card:var(--card);--tab-fg:var(--fg);--tab-line:var(--line);--tab-accent:var(--accent)}'
    '.hnav{display:contents}.hrow>.brand{display:flex}.hrow #q{display:block}'
    '@media(min-width:900px){'
    'header>.hrow{display:grid;grid-template-columns:auto minmax(0,720px) auto auto;'
    'justify-content:center;align-items:start;gap:8px 16px;max-width:1600px;padding:12px 16px 8px}'
    # Centre controls on the 56px field, even when in-flow search helpers grow.
    '.hrow>.brand{translate:0 calc(28px - 50%);grid-area:1/1;white-space:nowrap}'
    '.hrow>.search{grid-area:1/2;min-width:0}'
    '.hrow>.drop{grid-area:1/3;margin:0;height:56px}'
    '.hrow>.pwa-header-actions,.hrow>#subscribe{translate:0 calc(28px - 50%);grid-area:1/-2;white-space:nowrap}'
    '.hrow .pwa-header-actions{gap:16px}'
    '.hrow:has(>.acct:not([hidden])){grid-template-columns:auto minmax(0,720px) auto auto auto}'
    '.hrow>.acct{translate:0 calc(28px - 50%);grid-area:1/4}'
    '.hnav{display:flex;align-items:center;gap:16px;grid-column:1/-1;grid-row:2;min-width:0}'
    '.hnav>.tag{flex:none;white-space:nowrap;border:0;padding:0;letter-spacing:.03em}'
    '#search-hint{display:none!important}'
    '#searchcount{position:absolute;width:1px;height:1px;min-height:0;padding:0;margin:-1px;'
    'overflow:hidden;clip:rect(0,0,0,0)}'
    '.hrow #cattabs{flex:1;min-width:0;margin:0;padding:0 28px 0 0;'
    'flex-wrap:nowrap;justify-content:flex-start;overflow-x:auto;scrollbar-width:thin;'
    'mask-image:linear-gradient(90deg,#000 calc(100% - 24px),transparent)}'
    '#cattabs .cattab{min-height:44px;padding:8px;border:0;border-bottom:3px solid transparent;'
    'border-radius:0;background:none;color:var(--fg)}'
    '#cattabs .cattab.active,#cattabs .cattab:hover{border-bottom-color:var(--fg)}'
    '#cattabs .cattab::before{display:none}'
    '}'
    '@media(min-width:900px) and (max-width:1199px){'
    'header>.hrow{grid-template-columns:auto minmax(320px,1fr) auto auto}'
    '.hrow .drop .dial,.hrow .droplbl,.hrow .pwa-label-full,.hrow .pwa-label-compact{display:none}'
    '.hrow #pwa-header-slot{width:52px}'
    '.hrow>.drop{padding:5px 12px}.hrow .dropt{width:62px}'
    '}'
    # Leave the phone's categories below the bar, with their original body palette.
    # Reserving the row outside the header also preserves the mobile-audit height.
    '@media(max-width:899px){'
    'header{margin-bottom:52px}'
    '.hrow #cattabs{position:absolute;top:100%;left:0;right:0;max-width:none;padding:4px 12px;'
    'flex-wrap:nowrap;justify-content:flex-start;overflow-x:auto;scrollbar-width:none;'
    '--card:var(--tab-card);--fg:var(--tab-fg);--line:var(--tab-line);--accent:var(--tab-accent)}'
    '.hrow .cattab{min-height:44px}.hrow .brand a,.hrow>.drop{min-height:44px}'
    '.hnav>.tag{grid-area:3/1/auto/-1;border:0;padding:0;min-width:0;'
    'white-space:nowrap;overflow:hidden;text-overflow:ellipsis}'
    '.hrow:has(.pwa-header-actions) .tag{grid-column:1}'
    '.hrow:has(.trialcta) .tag{grid-row:4}'
    'header>.hrow{padding-bottom:0}'
    '}'
)

TABS = '<nav id="cattabs" aria-label="category tabs">{{CAT_TABS}}</nav>'
TAG = '<span id="tagline" class="tag">verified deals</span>'
ASSET = 'header-polish.css'


def enabled(cfg):
    return getattr(cfg, "header_polish", False) is True


def template(html, cfg):
    if not enabled(cfg):
        return html

    def swap(old, new):
        nonlocal html
        html = replace_slot(html, old, new, template_name="index.html", slot_name="header-polish")

    swap(TABS + '\n', '')
    swap('    ' + TAG + '\n', '')
    swap('<!--/support-->  </div>\n  <p id="sincevisit"',
         '<!--/support-->    <div class="hnav">' + TABS + TAG + '</div>\n  </div>\n  <p id="sincevisit"')
    # These flex/direct-child rules no longer have a consumer in the new grid.
    for rule in ('.search{flex:1 1 320px;max-width:680px;min-width:240px}',
                 '.hrow>.tag{order:8;flex-basis:100%;border:0;padding:0}',
                 '.hrow>.tag{grid-column:1 / -1;grid-row:3}',
                 '.hrow:has(.trialcta)>.tag{grid-row:4}'):
        swap(rule, '')
    swap('#cattabs{max-width:1200px;margin:0 auto;padding:12px 16px 10px;display:flex;\n'
         '         flex-wrap:wrap;justify-content:center;gap:7px}', '#cattabs{display:flex;gap:7px}')
    swap('.cattab:focus-visible{outline:2px solid var(--accent);outline-offset:3px}',
         '.cattab:focus-visible{outline:2px solid var(--accent);outline-offset:-3px}')
    version = hashlib.sha256(CSS.encode()).hexdigest()[:12]
    swap('</style>', '</style><link rel="stylesheet" href="/' + ASSET + '?v=' + version + '">')
    return html


def publish(out, cfg):
    path = out / ASSET
    if enabled(cfg):
        path.write_text(CSS, encoding='utf-8')
        # Seasonal chips are inserted by SEO after template composition. Keep
        # that optional strip below the header, outside the two-row grid.
        home = out / 'index.html'
        html = home.read_text(encoding='utf-8')
        event = re.search(r'<nav class="hrow event-links"[^>]*>.*?</nav>', html, re.S)
        if event:
            html = html.replace(event[0], '', 1).replace('</header>', '</header>' + event[0], 1)
            home.write_text(html, encoding='utf-8')
    else:
        path.unlink(missing_ok=True)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]
