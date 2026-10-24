"""Operator (Nishi) 2026-10-24 ~04:00Z: searched "vacuum" in the Fake Deal Spotter and its
analyzer box showed "wowpriced recommends" with a dress and a costume: "why would wowpriced
recommend clothing.. when im searching for vacuum. can you remove... ? can you remove that
part". The strip (tools_merge.recommends: today's top deals, unrelated to what was searched)
leaves the analyzer box on every page that draws it: its slot ({{RECOMMENDS}} in
tools-merge-modal.html) is filled with nothing. The box's progress, steps and its "See
today's deals" button stay; no script reads the strip. Needs tools_merge. Off changes no byte.
"""
import hashlib
from pathlib import Path

from . import tools_merge


def enabled(cfg):
    return getattr(cfg, 'hide_spotter_recommends', False) is True and tools_merge.enabled(cfg)


def tokens(tokens, cfg):
    """tools_merge.spotter_tokens with the strip's slot empty."""
    return {**tokens, 'RECOMMENDS': ''} if enabled(cfg) else tokens


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]
