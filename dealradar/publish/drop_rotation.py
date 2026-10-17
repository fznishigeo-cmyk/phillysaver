"""Rotate aligned pick windows only on the checkout-price home path.

Search keeps window membership intact and greedily checks promo feasibility
before exploring first-eight category choices. A shared 1,024-state budget
bounds all branches; exhaustion unwinds to the untouched server order.
Spotlights retain their existing banner slots and never enter rotation.
The final Allure pass can compress checkout gaps. Its repair reuses the bounded
search without rotating again, retaining the policy-filtered inventory when
feasible. An
optimistic promo/Allure packing bound prunes impossible suffixes; the first
six, Allure gaps, gone/unknown slots and the untouched tail remain constrained.
Repair keeps the policy-filtered 24-card window membership, including boost
shares and the rotation boundary. Impossible windows or search exhaustion
use a validated, order-preserving subset. Stop at the first shortened window
so a later window cannot move forward; deferred cards remain searchable.
"""

import re
from collections import Counter
from pathlib import Path

from .. import checkout_prices
from .template_slots import replace_slot, validate_slot


def enabled(cfg) -> bool:
    return cfg.drop_rotation_windows > 0 and checkout_prices.enabled(cfg)


def prepare_picks(picks, cfg, boosted):
    """Give rotating server windows enough variety to lead without relaxing caps.

    Exchange only within the bounded ordinary-pick prefix, retaining every
    selected offer. The caller excludes active spotlight keys before selection.
    Matching checkout kind and boost preserves their window shares and every
    checkout position. Prefer equal store/price exchanges; store/brand/title
    caps already bound the entire selection. Leave irreparable inventories in
    their server order.
    """
    if not enabled(cfg):
        return picks
    end = min(cfg.drop_rotation_windows, len(picks) // 24) * 24
    rows = picks.copy()
    minority = 8 // (max(1, cfg.front_page["max_same_category_run"]) + 1)

    def deficit(page):
        counts = Counter(d.get("category") or "other" for d in page)
        return max(0, minority - (len(page) - max(counts.values())))

    def signature(d):
        return bool(checkout_prices.kind(d)), d["dedupe_key"] in boosted

    def mix(d):
        return str(d.get("store") or "").strip().lower(), d.get("final_price")

    for start in range(0, end, 24):
        while deficit(rows[start:start + 24]):
            dominant = Counter(d.get("category") or "other" for d in rows[start:start + 24]).most_common(1)[0][0]
            for i in range(start + 23, start - 1, -1):
                if (rows[i].get("category") or "other") != dominant:
                    continue
                candidates = sorted(range(end - 1, -1, -1), key=lambda j: mix(rows[i]) != mix(rows[j]))
                for j in candidates:
                    if start <= j < start + 24 or (rows[j].get("category") or "other") == dominant \
                            or signature(rows[i]) != signature(rows[j]):
                        continue
                    donor = j // 24 * 24
                    before = deficit(rows[donor:donor + 24])
                    rows[i], rows[j] = rows[j], rows[i]
                    if deficit(rows[donor:donor + 24]) <= before:
                        break
                    rows[i], rows[j] = rows[j], rows[i]
                else:
                    continue
                break
            else:
                return picks
    return rows


def template(html: str, cfg) -> str:
    if not enabled(cfg):
        return html
    script = (Path(__file__).with_name("templates") / "drop-rotation.js").read_text()
    script = script.replace("{{ROTATION_WINDOWS}}", str(cfg.drop_rotation_windows))
    slot = dict(template_name="index.html", slot_name="drop-rotation")
    # Row filling runs after render and must never borrow into/from a banner.
    # Resolve this home-only variant before versioning; OFF/shared pages keep bytes.
    actions = (Path(__file__).with_name("templates") / "deal-actions.js").read_text()
    actions = replace_slot(actions, 'scope.querySelectorAll(".grid")',
                           'scope.querySelectorAll(".grid:not(.spotlight-grid)")', **slot)
    html = replace_slot(html, "{{DEAL_ACTIONS_SCRIPT}}", actions, **slot)
    start = "  // Each drop brings a different window of picks to the top (operator,"
    end = "  function topOrder(rows) {"
    for token in (start, end):
        validate_slot(html, token, **slot)
    old_rotation = html[html.index(start):html.index(end)]
    for old, new in (
        (old_rotation, "  var DROP_WINDOW = 24;\n" + script),
        ("      // Keep the server's sliding-window spacing through browser adoption and drops.\n"
         "      return picks.concat(rest);",
         "      var spotlightKeys = new Set(activeSpotlight().map(dealKey));\n"
         "      return rotatePicks(picks.filter(d => !spotlightKeys.has(dealKey(d))), state.seed).concat(rest);"),
        ("      return blockDeal(rotatePicks(picks, state.seed)).concat(rest);\n", ""),
    ):
        html = replace_slot(html, old, new, **slot)
    # Keep the enabled phone payload within its frozen budget; retain comments
    # in the source and every OFF byte. Only the ordering block shrinks.
    start, end = "  function cmp(", "  function storeHue("
    for token in (start, end):
        validate_slot(html, token, **slot)
    block = html[html.index(start):html.index(end)]
    html = replace_slot(html, block, re.sub(r"(?m)^ +//[^\n]*\n", "", block), **slot)
    return html


def final_order(html: str, cfg) -> str:
    """Allure can move a checkout offer after rotation's window repair."""
    if not enabled(cfg) or not (cfg.front_page.get("appeal", {}).get("want") or {}).get("enabled"):
        return html
    return replace_slot(html, "rows = spaceAllure(rows);", "rows = spaceRotatedPromos(spaceAllure(rows));",
                        template_name="index.html", slot_name="drop-rotation-allure")
