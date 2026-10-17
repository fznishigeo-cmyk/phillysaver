"""Rotate aligned pick windows while preserving checkout and clothing slots.

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
from ..rank import is_clothing
from .template_slots import replace_slot, validate_slot


def enabled(cfg) -> bool:
    return cfg.drop_rotation_windows > 0 and checkout_prices.enabled(cfg)


def active(cfg) -> bool:
    """Clothing slots also protect the legacy checkout/rotation OFF paths."""
    return enabled(cfg) or (cfg.front_page.get("top_picks") or {}).get("max_clothing_per_window", 0) > 0


def prepare_picks(picks, cfg, boosted):
    """Give rotating server windows enough variety to lead without relaxing caps.

    Exchange only within the bounded ordinary-pick prefix, retaining every
    selected offer. The caller excludes active spotlight keys before selection.
    Matching checkout kind, boost and clothing class preserves their window
    shares and every checkout/clothing position. Prefer equal store/price exchanges; store/brand/title
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

    mix_options = cfg.front_page.get("top_picks") or {}

    def signature(d):
        clothing = is_clothing(d, mix_options) if mix_options.get("max_clothing_per_window", 0) > 0 else False
        return bool(checkout_prices.kind(d)), d["dedupe_key"] in boosted, clothing

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
    if not active(cfg):
        return html
    script = (Path(__file__).with_name("templates") / "drop-rotation.js").read_text()
    from ..appeal import spacing_template
    script = spacing_template(script, cfg.front_page.get("appeal") or {})
    script = script.replace("{{ROTATION_WINDOWS}}", str(cfg.drop_rotation_windows))
    clothing = (cfg.front_page.get("top_picks") or {}).get("max_clothing_per_window", 0) > 0
    script = re.sub(r"/\*clothing\*/(.*?)/\*/clothing\*/", lambda m: m[1] if clothing else "", script, flags=re.S)
    if clothing:
        script = script.replace("opts = options.checkout_price_deals;", "opts = options.checkout_price_deals || {};")
    if cfg.drop_rotation_windows == 0:
        script = script.replace("    if (!repair) {", "    if (false) {", 1)
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
    ordering = ("      var spotlightKeys = new Set(activeSpotlight().map(dealKey));\n"
                "      return rotatePicks(picks.filter(d => !spotlightKeys.has(dealKey(d))), state.seed).concat(rest);")
    for old, new in (
        (old_rotation, "  var DROP_WINDOW = 24;\n" + script),
        ("      // Keep the server's sliding-window spacing through browser adoption and drops.\n"
         "      return picks.concat(rest);",
         ordering),
        ("      return blockDeal(rotatePicks(picks, state.seed)).concat(rest);\n",
         "" if checkout_prices.enabled(cfg) else ordering + "\n"),
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
    if not active(cfg) or not (cfg.front_page.get("appeal", {}).get("want") or {}).get("enabled"):
        return html
    return replace_slot(html, "rows = spaceAllure(rows);", "rows = spaceRotatedPromos(spaceAllure(rows));",
                        template_name="index.html", slot_name="drop-rotation-allure")


def clothing_order(rows, front_page):
    """Static mirror of the enabled browser's final, bounded window repair.

    Allure may compress the greedy pick slots. Retain window membership and
    every existing spacing rule; use the browser's safe deferral on exhaustion.
    """
    mix = front_page.get("top_picks") or {}
    clothing_cap = int(mix.get("max_clothing_per_window") or 0)
    if clothing_cap <= 0:
        return rows
    mix_width = max(1, int(mix.get("mix_window", 6)))
    mix_slots = int(mix.get("mix_slots", 24))
    checkout = front_page.get("checkout_price_deals") or {}
    width = max(1, int(checkout.get("top_pick_window", 5)))
    cap = max(0, int(checkout.get("top_pick_max_in_window", 1)))
    max_run = front_page.get("max_same_category_run") or 24
    # The browser fills these from the same knobs ({{ALLURE_*}}, task 282).
    from ..appeal import allure_spacing
    allure_first, every = allure_spacing(front_page.get("appeal") or {})

    def mix_fits(out, row):
        pos = len(out)
        if pos < 8 and pos >= max_run and all((d.get("category") or "other") == (row.get("category") or "other")
                                              for d in out[-max_run:]):
            return False
        return (not row.get("cl") or pos >= mix_slots
                or sum(bool(d.get("cl")) for d in out[pos // mix_width * mix_width:]) < clothing_cap)

    end = len(rows)
    while end and rows[end - 1].get("pick") is None:
        end -= 1
    picks = rows[:end]
    if not any(not mix_fits(picks[:i], d)
               or sum(bool(r.get("pick_checkout")) for r in picks[i:i + width]) > cap
               for i, d in enumerate(picks)):
        return rows
    pools = {i: picks[i:i + 24] for i in range(0, end, 24)}
    next_allure = next((i for i in range(end, len(rows)) if rows[i].get("allure")), float("inf"))
    out, budget = [], 1024

    def fits(pos, recent, left):
        for pos in range(pos, end):
            if pos in pools:
                left = sum(bool(d.get("pick_checkout")) for d in pools[pos])
            recent = [n for n in recent if pos - n < width]
            if left and len(recent) < cap and (picks[pos].get("pick") is not None or picks[pos].get("pick_checkout")):
                left -= 1
                recent.append(pos)
            if left and (pos + 1 in pools or pos + 1 == end):
                return False
        return True

    def allure_fits(pos, recent, pool):
        last = next((i for i in range(len(out) - 1, -1, -1) if out[i].get("allure")), -1)
        ready = max(pos, allure_first, 0 if last < 0 else last + every)
        deadline = min(end - 1, (pos // 24 + 1) * 24 - 1, next_allure - every)
        if sum(bool(d.get("allure")) for d in pool) > max(0, (deadline - ready) // every + 1):
            return False
        both = sum(bool(d.get("allure") and d.get("pick_checkout")) for d in pool)
        if cap != 1 or not both:
            return True
        plain = sum(bool(d.get("pick_checkout")) for d in pool) - both
        first = max(pos, recent[-1] + width if recent else pos)
        for lead in range(plain + 1):
            first_allure = max(ready, first + lead * width)
            for tail in range(plain - lead + 1):
                between = plain - lead - tail
                last_allure = first_allure + (both - 1 + between) * width + max(0, both - 1 - between) * max(0, every - width)
                if last_allure <= deadline and last_allure + tail * width < end:
                    return True
        return False

    def slots(pos, recent, last, run, pool):
        nonlocal budget
        if not budget:
            return False
        budget -= 1
        if pos == end:
            return True
        if pos in pools:
            pool = pools[pos].copy()
        recent = [n for n in recent if pos - n < width]
        if not fits(pos, recent, sum(bool(d.get("pick_checkout")) for d in pool)) or not allure_fits(pos, recent, pool):
            return False
        want, tried = bool(picks[pos].get("pick_checkout")), set()
        choices = sorted(pool, key=lambda d: (-bool(d.get("allure")), bool(d.get("pick_checkout")) != want))
        for d in choices:
            if not budget:
                break
            cat, promo = d.get("category") or "other", bool(d.get("pick_checkout"))
            key = (cat if pos < 8 else "", promo, bool(d.get("allure")), bool(d.get("cl")))
            if (not mix_fits(out, d) or len(recent) + promo > cap or key in tried
                    or (pos < 8 and cat == last and run >= max_run)
                    or ((d.get("pick") is None or picks[pos].get("pick") is None) and d is not picks[pos])
                    or d.get("status") != picks[pos].get("status")
                    or bool(d.get("click_price_unknown")) != bool(picks[pos].get("click_price_unknown"))
                    or (d.get("allure") and (pos < allure_first or next_allure - pos < every
                        or (every > 1 and any(r.get("allure") for r in out[-(every - 1):]))
                        or any(r.get("pick") is None and r.get("allure") for r in picks[pos + 1:pos + every])))):
                continue
            tried.add(key)
            i = pool.index(d)
            pool.pop(i)
            out.append(d)
            if slots(pos + 1, recent + [pos] if promo else recent, cat, run + 1 if cat == last else 1, pool):
                return True
            out.pop()
            pool.insert(i, d)
        return False

    if slots(0, [], "", 0, []):
        return out + rows[end:]
    safe, recent, last_allure, next_gone = [], [], -every, 8
    end = len(rows)
    for i, d in enumerate(rows):
        if i >= end:
            break
        pos = len(safe)
        recent = [n for n in recent if pos - n < width]
        if (not mix_fits(safe, d) or (d.get("pick_checkout") and len(recent) >= cap)
                or (d.get("allure") and (pos < allure_first or pos - last_allure < every))
                or (d.get("click_price_unknown") and pos < 120) or (d.get("status") == "dead" and pos < next_gone)):
            end = min(end, (i // 24 + 1) * 24)
            continue
        if d.get("pick_checkout"):
            recent.append(pos)
        if d.get("allure"):
            last_allure = pos
        if d.get("status") == "dead":
            next_gone = pos + 8
        safe.append(d)
    return safe
