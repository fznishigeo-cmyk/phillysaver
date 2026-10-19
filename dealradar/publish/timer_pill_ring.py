"""Task 329: paint the compact desktop pill from the existing dial fraction."""

import hashlib
import re
from pathlib import Path

from . import header_polish
from .template_slots import replace_slot

# A masked background paints inside the existing border without adding a flex
# item, accessible content, or geometry reads. Conic gradients start at 12 o'clock
# and sweep clockwise; the content-box mask leaves a crisp 3px rounded outline.
CSS = (
    '.hrow:has(>.hnav)>.drop::before{content:"";position:absolute;inset:0;padding:3px;'
    'border-radius:inherit;pointer-events:none;'
    'background:conic-gradient(var(--dial,var(--warm)) calc(var(--pill-progress,0)*1turn),var(--line) 0);'
    'mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);mask-composite:exclude}'
    '.drop[data-phase="warm"]::before{--dial:#ffc94d}'
    '.drop[data-phase="hot"]::before,.drop[data-phase="final"]::before{--dial:#ff9870}'
    '.drop.dropping::before{--dial:var(--good)}'
)


def enabled(cfg):
    return getattr(cfg, 'timer_pill_ring', False) is True


def css(header_style):
    # Read the dial-hiding query after all header composition passes, including
    # task 317's laptop range. No independent breakpoint can drift from the dial.
    hidden = re.search(r'(@media[^{}]+)\{(?:[^{}]+\{[^{}]*\})*?'
                       r'\.hrow \.drop \.dial[^{}]*\{display:none\}', header_style)
    return hidden[1] + '{' + CSS + '}' if hidden else ''


def template(html, cfg):
    if not enabled(cfg):
        return html
    # The existing one-second tick calls paintClock: update alongside the digits
    # and pass that exact fraction to the dial, including f=1 at the drop.
    return replace_slot(html, '    paintDial(f, clock.dropping ? null : left);',
                        '    box.style.setProperty("--pill-progress", f);\n'
                        '    paintDial(f, clock.dropping ? null : left);',
                        template_name='index.html', slot_name='timer-pill-ring')


def publish(out, cfg):
    if not enabled(cfg):
        return
    # Reuse the shared header asset after its existing composition passes.
    asset = out / header_polish.ASSET
    header_style = asset.read_text() if asset.exists() else ''
    outline = css(header_style)
    if not outline:
        return
    asset.write_text(header_style + outline)
    version = hashlib.sha256(asset.read_bytes()).hexdigest()[:12]
    for path in out.rglob('*.html'):
        html = path.read_text()
        html = re.sub(r'(/header-polish\.css\?v=)[a-f0-9]+', lambda m: m[1] + version, html)
        if path == out / 'index.html' and 'href="/header-polish.css?' not in html:
            html = html.replace('</head>', '<link rel="stylesheet" href="/header-polish.css?v='
                                + version + '"></head>', 1)
        path.write_text(html)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]
