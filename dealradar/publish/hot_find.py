"""Operator 2026-10-24: "I don't like the word Price drop (I think its better to put the
percentage of the discount..) it looks kind of spamy" -> picked "Hot Find" ("go with Hot
Find........ push it through").

Amazon deals without a List Price show no percentage (comparisons stay private,
amazon_reference); their orange flag, deal-page chip and screen-reader text read
publish.price_drop_label instead of "Price drop". Same spot, no price comparison claimed.
The default ("Price drop") changes no byte.
"""

import hashlib
import html
import json
import re
from pathlib import Path

DEFAULT = "Price drop"


def label(cfg):
    return getattr(cfg, "price_drop_label", DEFAULT)


def enabled(cfg):
    return label(cfg) != DEFAULT


def relabel(text, cfg):
    """The flag, the chip and the browser card code's stamp/plain strings."""
    new = label(cfg)
    for markup in ('<span class="flag">', '<span class="chip">'):
        text = text.replace(markup + DEFAULT + '</span>', markup + html.escape(new) + '</span>')
    text = text.replace('d.price_drop ? "Price drop " :', 'd.price_drop ? ' + json.dumps(new + " ") + ' :')
    return text.replace('d.price_drop ? "Price drop" :', 'd.price_drop ? ' + json.dumps(new) + ' :')


def publish(out, cfg):
    if not enabled(cfg):
        return
    versions = {}
    for path in out.glob('*.js'):
        js = path.read_text()
        if relabel(js, cfg) != js:
            path.write_text(relabel(js, cfg))
            versions[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    for path in out.rglob('*.html'):
        page = path.read_text()
        new = relabel(page, cfg)
        for name, version in versions.items():
            new = re.sub(r'(/' + re.escape(name) + r'\?v=)[a-f0-9]+', lambda m: m[1] + version, new)
        if new != page:
            path.write_text(new)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + label(cfg).encode() + Path(__file__).read_bytes()).hexdigest()[:12]
