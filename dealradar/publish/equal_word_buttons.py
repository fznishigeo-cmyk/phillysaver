"""Operator (Nishi) 2026-10-24 ~03:55Z, screenshot of the typed-words box (the yellow "See
highly rated ... on Amazon" button wider than the blue "See ... discounts on Amazon" one above
it): "can you make the buttons the same size. length for both sites. push that through also".

On Fake Deal Spotter (and its keyword pages) and Review Checker the box's two buttons stack in
a column as wide as the wider one, both stretched to it with their text centered as before;
a box too narrow for it wraps the text in both alike. Phones already stretch both to the full
width (each tool's phone rule), which stays. CSS only: same text, colors, links and heights.
Needs tools_merge (the box is its own). Off changes no byte.
"""
import hashlib
from pathlib import Path

from . import tools_merge

# Above each tool's phone rule (tools-merge.css 760px, tools-merge-review.css 700px).
SPOTTER_CSS = "@media(min-width:761px){.words-buttons{flex-direction:column;width:fit-content}}"
REVIEW_CSS = "@media(min-width:701px){.words-buttons{flex-direction:column;width:fit-content}}"


def enabled(cfg):
    return getattr(cfg, "equal_word_buttons", False) is True and tools_merge.enabled(cfg)


def spotter_css(cfg):
    return SPOTTER_CSS if enabled(cfg) else ''


def review_css(cfg):
    return REVIEW_CSS if enabled(cfg) else ''


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]
