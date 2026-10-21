"""Operator 2026-10-20: center the countdown digits in the timer pill on phones and laptops."""

import hashlib
import re
from pathlib import Path

from . import header_polish
from .template_slots import replace_slot

SLOT = '  .dropt{font-size:16px;width:50px;line-height:23px}'
# Laptop widths hide the dial and label, so the pill holds only the digits.
# Wider screens keep dial, label and digits reading left to right.
DESKTOP_CSS = '.hrow>.drop{justify-content:center}.hrow .dropt{text-align:center}'


def enabled(cfg):
    return getattr(cfg, 'timer_center', False) is True


def template(html, cfg):
    if not enabled(cfg):
        return html
    # The fixed 50px box keeps the pill from jittering as digits change; the
    # digits sat at its start, so narrow times ("3:45") hung left.
    return replace_slot(html, SLOT, SLOT[:-1] + ';text-align:center}',
                        template_name='index.html', slot_name='timer-center')


def css(header_style):
    # The same dial-hiding query timer_pill_ring reads, after all header passes.
    hidden = re.search(r'(@media[^{}]+)\{(?:[^{}]+\{[^{}]*\})*?'
                       r'\.hrow \.drop \.dial[^{}]*\{display:none\}', header_style)
    return hidden[1] + '{' + DESKTOP_CSS + '}' if hidden else ''


def publish(out, cfg):
    if not enabled(cfg):
        return
    asset = out / header_polish.ASSET
    if not asset.exists():
        return
    header_style = asset.read_text()
    rule = css(header_style)
    if not rule:
        return
    asset.write_text(header_style + rule)
    version = hashlib.sha256(asset.read_bytes()).hexdigest()[:12]
    for path in out.rglob('*.html'):
        html = path.read_text()
        new = re.sub(r'(/header-polish\.css\?v=)[a-f0-9]+', lambda m: m[1] + version, html)
        if new != html:
            path.write_text(new)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]
