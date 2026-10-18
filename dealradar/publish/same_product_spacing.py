"""Final browse-only family spacing; inventory and search retain every listing."""

import hashlib
import json
import re
from pathlib import Path

from .template_slots import replace_slot

# Shared with the emitted JS. Keep numbers in model names (and Blue 84), but
# remove explicit pack counts, sizes, colors and title filler before the stem.
PACK = r"\b(?:sizes?\s*[:=-]?\s*\d+(?:\s*[-/]\s*\d+)?|\d+\s*[- ]?\s*(?:packs?|pk|counts?|ct|pcs|pieces?)|(?:packs?|sets?)\s+of\s+\d+)\b"
WORDS = r"\b(?:x{0,3}s|m|x{0,3}l|[2-6]xl|small|medium|large|sizes?|black|white|red|blue|green|yellow|orange|purple|pink|gr[ae]y|navy|beige|brown|ivory|teal|maroon|multicolor|heather|an?|the|and|for|with|of|in|by|(?:wo)?mens?|unisex|officially|licensed)\b(?!\s+\d)"
SCRIPT = Path(__file__).with_name("templates") / "same-product-spacing.js"


def family_key(row):
    title = str(row.get("item") or "").lower().replace("'s", "").replace("’s", "")
    title = re.sub(PACK, " ", title)
    title = re.sub(r"[^a-z0-9]+", " ", title)
    title = re.sub(WORDS, " ", title)
    stem = " ".join(title.split()[:5])
    return str(row.get("store") or "").strip().lower() + "|" + stem if stem else ""


def rules(front_page, gone_every=8):
    from ..appeal import allure_spacing
    options = front_page.get("appeal") or {}
    first, every = allure_spacing(options) if (options.get("want") or {}).get("enabled") else (0, 0)
    picks = front_page.get("top_picks") or {}
    checkout = front_page.get("checkout_price_deals") or {}
    return dict(gone=gone_every, first=first, allure=every,
                clothing=picks.get("max_clothing_per_window", 0),
                mix=picks.get("mix_window", 6), slots=picks.get("mix_slots", 24),
                promo=checkout.get("top_pick_window", 5), cap=checkout.get("top_pick_max_in_window", 1))


def _fits(row, earliest, out, policy):
    if not policy:
        return True
    pos = len(out)
    # Family deferral must not pull protected offers up into the space it opens.
    if pos < earliest:
        return False
    if row.get("status") == "dead" and (pos < 8 or any(d.get("status") == "dead" for d in out[max(0, pos - policy["gone"] + 1):])):
        return False
    if row.get("allure") and policy["allure"] and (pos < policy["first"] or any(d.get("allure") for d in out[max(0, pos - policy["allure"] + 1):])):
        return False
    if row.get("cl") and policy["clothing"] and pos < policy["slots"] and sum(bool(d.get("cl")) for d in out[pos // policy["mix"] * policy["mix"]:]) >= policy["clothing"]:
        return False
    if row.get("pick_checkout") and sum(bool(d.get("pick_checkout")) for d in out[max(0, pos - policy["promo"] + 1):]) >= policy["cap"]:
        return False
    return True


def space_rows(rows, prefix=(), rules=None):
    """Best-effort spacing in the first 240 positions, looking ahead 60 rows.

    When no candidate fits, retain the first pending row. Preserve all rows,
    leaving the remaining tail in order. Empty titles cannot collide.
    """
    if len(prefix) >= 240:
        return list(rows)
    window = rows[:240 - len(prefix) + 59]
    rules = rules or {}
    def flags(row):
        return [key for key in ("niche", "locked", "click_price_unknown", "price_aged",
                                "price_unconfirmed", "cl", "allure", "pick_checkout") if row.get(key)] + (
            ["photo"] if not row.get("image_url") else []) + (["gone"] if row.get("status") == "dead" else [])

    first = {}
    for i, row in enumerate(list(prefix) + window):
        for flag in flags(row):
            first.setdefault(flag, i)
    pending = [(row, family_key(row), max((first[flag] for flag in flags(row)), default=0)) for row in window]
    moved = False
    out, last, counts = list(prefix), {}, {}
    for pos, row in enumerate(out):
        key = family_key(row)
        if key:
            last[key] = pos
            counts[key] = counts.get(key, 0) + 1
    while pending and len(out) < 240:
        pos = len(out)
        at = 0
        for i, (row, key, earliest) in enumerate(pending[:60]):
            if key and (pos - last.get(key, -9) <= 8 or (pos < 48 and counts.get(key, 0) >= 2)):
                continue
            if (moved or i) and not _fits(row, earliest, out, rules):
                continue
            at = i
            break
        moved = moved or at > 0
        row, key, _ = pending.pop(at)
        out.append(row)
        if key:
            last[key] = pos
            counts[key] = counts.get(key, 0) + 1
    return out[len(prefix):] + [entry[0] for entry in pending] + rows[len(window):]


def template(html, cfg):
    if not cfg.same_product_spacing:
        return html

    def swap(old, new):
        nonlocal html
        html = replace_slot(html, old, new, template_name="index.html", slot_name="same-product-spacing")

    script = SCRIPT.read_text().replace("{{FAMILY_PACK}}", PACK.replace("/", r"\/")).replace("{{FAMILY_WORDS}}", WORDS)
    script = script.replace("{{FAMILY_RULES}}", json.dumps(rules(cfg.front_page, 8 if cfg.credit_links else 10), separators=(",", ":")))
    swap("  function storeHue(", script + "\n  function storeHue(")
    # This is after every policy pass and the clock shuffle, before pagination.
    swap("    browseRows = spotlight.concat(rows);",
         "    if (!searchText()) rows = spaceSameProducts(rows, spotlight);\n"
         "    browseRows = spotlight.concat(rows);")
    swap("var spotlight = activeSpotlight(), spotlightKeys",
         "var spotlight = searchText() ? activeSpotlight() : spaceSameProducts(activeSpotlight()), spotlightKeys")
    if cfg.drop_shuffle:
        swap("  function shuffleDropPicks(rows, seed) {",
             "  function shuffleDropPicks(rows, seed) {\n"
             "    var prefix = spaceSameProducts(activeSpotlight());\n    rows = spaceSameProducts(rows, prefix);")
        # Repeated families (including spotlights and the tail past 120) stay
        # in their spaced slots. Trading only unique families cannot close a gap.
        swap("    var options = state.meta.front_page || {}, groups = new Map(), limit = Math.min(120, rows.length);",
             "    var families = new Map();\n"
             "    rows.concat(prefix).forEach(d => { var k = productFamily(d); families.set(k, (families.get(k) || 0) + 1); });\n"
             "    var options = state.meta.front_page || {}, groups = new Map(), limit = Math.min(120, rows.length);")
        swap("      var d = rows[i];",
             "      var d = rows[i];\n"
             "      if (productFamily(d) && families.get(productFamily(d)) > 1) continue;")
    return html


def site_version(version, cfg):
    if not cfg.same_product_spacing:
        return version
    return hashlib.sha256(version.encode() + SCRIPT.read_bytes() + Path(__file__).read_bytes()).hexdigest()[:12]
