"""Clock-driven pick permutations after every existing homepage policy pass."""

import hashlib
from pathlib import Path

from .template_slots import replace_slot

SCRIPT = Path(__file__).with_name("templates") / "drop-shuffle.js"


def enabled(cfg):
    return getattr(cfg, "drop_shuffle", False)


def template(html, cfg):
    if not enabled(cfg):
        return html

    def swap(old, new):
        nonlocal html
        html = replace_slot(html, old, new, template_name="index.html", slot_name="drop-shuffle")

    # A late publish must not move a mark or roll its seed back. Keep the
    # configured cadence, anchored to UTC rather than each publish's seconds.
    swap("  function dropPhase(m, step) {\n"
         "    var pub = publishTime(m);\n"
         "    return pub == null ? 0 : ((pub % step) + step) % step;\n  }",
         "  function dropPhase(m, step) { return 0; }")
    # Start from a stable, policy-repaired ranking. Arrival promotion and the
    # old whole-window fallback must not change the permutation's input on reload.
    if "return rotatePicks(picks.filter" in html:
        swap("return rotatePicks(picks.filter(d => !spotlightKeys.has(dealKey(d))), state.seed).concat(rest);",
             "return picks.filter(d => !spotlightKeys.has(dealKey(d))).concat(rest);")
    elif "return picks.concat(rest);" not in html:
        swap("return blockDeal(rotatePicks(picks, state.seed)).concat(rest);", "return picks.concat(rest);")
    swap("  function topOrder(rows) {", "  function topOrder(rows) {\n"
         "    if (frontPageView()) state.seed = dropPickIndex();")
    swap("  function storeHue(", SCRIPT.read_text() + "\n  function storeHue(")
    swap("    browseRows = spotlight.concat(rows);",
         "    if (frontPageView() && state.sortKey === 'top') {\n"
         "      rows = shuffleDropPicks(rows, state.seed);\n"
         "    }\n    browseRows = spotlight.concat(rows);")
    swap("    var guard = setTimeout(function () { finish(null, null); }, DROP_MAX_MS - 250);",
         "    refreshDropPicks();\n"
         "    var guard = setTimeout(function () { finish(null, null); }, DROP_MAX_MS - 250);")
    return html


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + SCRIPT.read_bytes() + Path(__file__).read_bytes()).hexdigest()[:12]
