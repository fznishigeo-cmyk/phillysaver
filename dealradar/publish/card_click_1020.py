"""Task 342: stretch the native Get deal link without changing card markup."""

import hashlib
from pathlib import Path

CARD = '.card:not(.gone):has(.getdeal)'
CSS = (CARD + '{position:relative;cursor:pointer}'
       # The phone footer isolates actions and positions Get deal; both would
       # confine its overlay to that row instead of the card.
       + CARD + ' .actions{isolation:auto}'
       + CARD + ' .actions>.getdeal{position:static;z-index:auto;filter:none;transform:none}'
       + CARD + ' .getdeal::after{content:"";position:absolute;inset:0;z-index:3;'
       'width:auto;height:auto;min-width:0;min-height:0;transform:none}'
       # The transparent overlay also covers fresh badges (z-index:2).
       # Filters/transforms on the anchor would create a new containing block.
       # Raise controls themselves, not their full rows (which remain clickable).
       + CARD + ' :is(.title,a:not(.getdeal),button,summary,input){position:relative;z-index:4}'
       + CARD + ' .media .save-deal{position:absolute}'
       # Phone titles place the 44px tap extension on the heading, outside clamp.
       + '@media(max-width:640px){' + CARD + ' .title a{position:static}}')


def enabled(cfg):
    return getattr(cfg, 'card_click_1020', False) is True


def publish(out, cfg):
    if not enabled(cfg):
        (out / 'card-click.css').unlink(missing_ok=True)
        return
    asset = out / 'card-click.css'
    asset.write_text(CSS)
    version = hashlib.sha256(asset.read_bytes()).hexdigest()[:12]
    link = '<link rel="stylesheet" href="/card-click.css?v=' + version + '">'
    for path in out.rglob('*.html'):
        html = path.read_text()
        if 'class="card' in html:
            # After existing inline rules, before first paint; shared static
            # cards and the dynamic wall use exactly the same selectors.
            path.write_text(html.replace('</head>', link + '</head>', 1))


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]
