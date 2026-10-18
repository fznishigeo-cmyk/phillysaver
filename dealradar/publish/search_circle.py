"""Operator 2026-10-17 (Verizon "Let's Chat" video): the Amazon buttons get a
red-orange-yellow border that circles them, and "Not what you're looking for?"
becomes a "Search all of Amazon for <q>" button. Applied on top of
search_boost; OFF (missing/false) leaves every published byte unchanged."""

import hashlib
import json
from pathlib import Path

from . import search_boost
from .template_slots import replace_slot

SPIN = "conic-gradient(from var(--wpspin),#e60000,#ff7a00,#ffd400,#a8e063,#ffd400,#ff7a00,#e60000) border-box"
FLOW = "linear-gradient(90deg,#ff3b30,#ffcc00,#34c759,#ffcc00,#ff3b30) 0 0/300% 100% border-box"
SECONDARY = ("#search-amazon-result{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;font-size:14px}"
             "#search-amazon-result[hidden]{display:none}"
             "#search-amazon-result [data-search-amazon]{display:inline-flex;align-items:center;gap:8px;min-height:46px;"
             "padding:10px 22px;border-radius:999px;border:3px solid transparent;background:linear-gradient(var(--card),"
             "var(--card)) padding-box," + SPIN + ";"
             "color:var(--fg)!important;font-size:16px;font-weight:800;text-decoration:none;"
             "box-shadow:0 6px 18px rgba(255,140,0,.25);animation:wpspin 3s linear infinite}"
             "#search-amazon-result [data-search-amazon]::before{content:\"\\2726\";content:\"\\2726\"/\"\";font-size:18px;color:#ff8a00}"
             "#search-amazon-result small{margin:0;font-size:11px}")


def enabled(cfg) -> bool:
    return bool(cfg.search_circle) and search_boost.enabled(cfg)


def css() -> str:
    old = search_boost.CSS
    new = (old.replace(FLOW, SPIN)
           .replace("animation:wpflow 3s linear infinite", "animation:wpspin 3s linear infinite")
           .replace("@keyframes wpflow{to{background-position:0 0,300% 0}}",
                    "@property --wpspin{syntax:\"<angle>\";inherits:false;initial-value:0deg}"
                    "@keyframes wpspin{to{--wpspin:360deg}}")
           .replace("#amazon-results h2{", SECONDARY + "#amazon-results h2{", 1)
           .replace("@media (prefers-reduced-motion:reduce){.search-amazon-top .getdeal{animation:none;transition:none}",
                    "@media (prefers-reduced-motion:reduce){.search-amazon-top .getdeal,"
                    "#search-amazon-result [data-search-amazon]{animation:none;transition:none}"))
    assert new.count(SPIN) == 2 and "wpflow" not in new, "search_boost.CSS changed; update search_circle"
    return new


def template(html, cfg):
    if not enabled(cfg):
        return html

    def swap(old, new):
        nonlocal html
        html = replace_slot(html, old, new, template_name="index.html", slot_name="search-circle")

    swap(search_boost.CSS, css())
    swap("(primary ? 'Compare all &quot;' : 'Search Amazon for &quot;')",
         "(primary ? 'Compare all &quot;' : 'Search all of Amazon for &quot;')")
    swap("(primary ? ' prices on Amazon \\u2192' : '')", "(primary ? ' prices on Amazon \\u2192' : ' \\u2192')")
    return html


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()
                          + json.dumps(cfg.search_circle).encode()).hexdigest()[:12]
