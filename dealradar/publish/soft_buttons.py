"""Operator 2026-10-23 ("CONFIRMED GO AHEAD"): the two buttons beside the search are a
little softer: "they are so bright alot of people said they hurt their eyes.. same
color is fine just softer less bright.. subtle change".

Second step (operator 2026-10-24, Option 1 of a preview: "same blue and purple just
lower the brightness (the pop of it)"): the same hues again a little less saturated
and bright, glows about 65-80% weaker than the originals. The
shared pill rules read --pill-colors/--pill-glow (all_discounts.shared_css), so only
those values change, in place, in the stylesheets that carry them (no added bytes:
the phone CSS budget is nearly full), on every page with the buttons (home, event
pages, their tools_merge labels). White text stays AA: 5.4:1 or more on every
gradient stop. The yellow Amazon buttons are untouched. Off changes no byte.
"""

import hashlib
import re
from pathlib import Path

# Deal Checker / Review Checker (purple) is the rules' fallback; Discount Finder /
# Fake Deal Spotter (blue) sets the variables on .all-discounts.
SOFTER = (("var(--pill-colors,#5b21b6,#9333ea 50%,#cf236e)", "var(--pill-colors,#5c359e,#8540cc 50%,#a7396e)"),
          ("var(--pill-glow,rgba(147,51,234,.35))", "var(--pill-glow,rgba(147,51,234,.12))"),
          ("var(--pill-glow,rgba(236,72,153,.55))", "var(--pill-glow,rgba(236,72,153,.19))"),
          ("--pill-colors:#1265f4,#0061fe 52%,#0050d6;--pill-glow:rgba(59,130,246,.5)",
           "--pill-colors:#2a63cb,#2060ca 52%,#1c51af;--pill-glow:rgba(59,130,246,.1)"))
SHEETS = ("header-polish.css", "page-polish.css")


def enabled(cfg):
    return getattr(cfg, 'soft_buttons', False) is True


def soften(text):
    for old, new in SOFTER:
        text = text.replace(old, new)
    return text


def publish(out, cfg):
    if not enabled(cfg):
        return
    versions = {}
    for name in SHEETS:
        path = out / name
        if path.exists():
            css = path.read_text()
            if soften(css) != css:
                path.write_text(soften(css))
                versions[name] = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    for path in out.rglob('*.html'):
        html = path.read_text()
        new = soften(html)  # any inline copy of the same rules
        for name, version in versions.items():
            new = re.sub(r'(/' + re.escape(name) + r'\?v=)[a-f0-9]+', lambda m: m[1] + version, new)
        if new != html:
            path.write_text(new)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]
