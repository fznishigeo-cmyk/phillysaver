"""Approved task 335 heroes, built only from the final public projection."""

import json
import math
import re
from html import escape
from pathlib import Path

from ..savings import TOOL_LIST_BADGE_JS, TOOL_METER_BADGE_JS, display_pct, tool_meter
from ..appeal import matches

TEMPLATES = Path(__file__).with_name("templates")
COPY = {
    "checker": ("Is it really a deal?", "Know in 10 seconds.",
                "Paste any Amazon link or type a product. We check today's price against Amazon's list price and our verified deals.", "No extension"),
    "finder": ("Every Amazon discount.", "One click.",
               "Type what you want or paste a link. We open Amazon's discounts for it and show our verified deals.", "Updated every hour"),
}


def enabled(cfg):
    return getattr(cfg, "tool_pages_hero", False) is True


def candidates(rows, cfg):
    seen, selected = set(), []
    for d in rows:
        key = d.get("key")
        if (not key or key in seen or d.get("locked") or d.get("status") != "alive"
                or d.get("badge") not in {"verified", "history-checked"}
                or any(d.get(k) for k in ("price_unconfirmed", "price_aged", "click_price_unknown"))
                or "not yet confirmed" in d.get("headline", "")
                or not re.fullmatch(r"/deal/[a-z0-9-]+/", d.get("deal_path", ""))
                or not re.match(r"https://[^\s<>\"']+$", d.get("image_url", ""))
                or matches(d.get("item", ""), cfg.search_boost["exclude_words"])):
            continue
        seen.add(key)
        selected.append(d)
    return selected


def example(rows):
    # Input order is the publisher's rank order. Do not invent or repair facts.
    for d in rows:
        if (not d.get("amazon") and d.get("store", "").lower() != "amazon"
                or d.get("regular_price_basis") != "List price"):
            continue
        if not all(type(d.get(k)) in {int, float} and math.isfinite(d[k]) and d[k] > 0
                   for k in ("final_price", "regular_price", "abs_savings")):
            continue
        meter = tool_meter(d["final_price"], d["regular_price"])
        if 40 <= meter["pct"] <= 80:
            return d, meter
    return None


def gauge(meter=None):
    values = meter or dict(pct=0, angle=0, label="Big discount", hasList=False)
    text = (f'{values["pct"]}% off' if values["hasList"] else
            "Big discount" if meter is None else "Live Amazon price")
    return (TEMPLATES / "tool-meter.svg").read_text().replace("{{ANGLE}}", str(values["angle"])).replace(
        "{{PCT}}", str(values["pct"])).replace("{{LABEL}}", escape(values["label"])).replace("{{TEXT}}", text)


def checker(rows):
    chosen = example(rows)
    if not chosen:
        return '<div class="meter-card illustration"><span class="example">EXAMPLE</span>' + gauge() + '</div>'
    d, meter = chosen
    return ('<div class="meter-card"><span class="example">EXAMPLE · today\'s deal</span>'
            '<div class="prod"><img src="' + escape(d["image_url"]) + '" alt="" width="64" height="64" '
            'fetchpriority="high" decoding="async" referrerpolicy="no-referrer"><b>' + escape(d["item"]) + '</b></div>'
            + gauge(meter) + f'<div class="price-row"><s>${d["regular_price"]:,.2f} list</s>'
            f'<strong>${d["final_price"]:,.2f}</strong></div><p class="save">You save ${d["abs_savings"]:,.2f}</p>'
            '<div class="center"><span class="badge ' + meter["kind"] + '">' + escape(meter["label"]) + '</span></div>'
            '<a class="amz" href="' + escape(d["deal_path"]) + '">View deal →</a></div>')


def cloud_selection(rows):
    pool = [d for d in rows if type(d.get("pct_at_checkout")) in {int, float}
            and math.isfinite(d["pct_at_checkout"]) and d["pct_at_checkout"] > 0]
    chosen, categories, stores = [], set(), set()
    while pool and len(chosen) < 6:
        # Prefer strong discounts, then broaden categories/stores without losing rank ties.
        d = max(pool, key=lambda d: (d["pct_at_checkout"] >= 25,
                                     d.get("category") not in categories, d.get("store") not in stores))
        pool.remove(d)
        chosen.append(d)
        categories.add(d.get("category"))
        stores.add(d.get("store"))
    return chosen if len(chosen) >= 4 else []


def finder(rows):
    chosen = cloud_selection(rows)
    if not chosen:
        return ''
    tiles = []
    for i, d in enumerate(chosen):
        tiles.append('<a class="tile tile-' + str(i) + '" href="' + escape(d["deal_path"]) + '">'
                     '<i>-' + str(display_pct(d["pct_at_checkout"])) + '%</i><img src="' + escape(d["image_url"])
                     + '" alt="' + escape(d["item"]) + '" width="186" height="186" decoding="async" '
                     + ('loading="lazy" ' if i >= 3 else 'fetchpriority="high" ')
                     + 'referrerpolicy="no-referrer"></a>')
    return '<div class="cloud-wrap"><div class="cloud">' + ''.join(tiles) + (
        '</div><p class="cloudnote">Today\'s top verified discounts · updated every hour</p></div>')


def template(base, page, rows):
    headline, accent, sub, last = COPY[page["mode"]]
    heading = ('<section class="hero"><div><h1><span class="eyebrow">{{NAME}}</span>'
               '<span class="htitle">' + headline + '<span>' + accent + '</span></span></h1>'
               '<p class="sub">' + sub + '</p>')
    base = base.replace('<h1>{{NAME}}</h1><p class="intro">{{INTRO}}</p>', heading)
    visual = checker(rows) if page["mode"] == "checker" else finder(rows)
    base = base.replace('</form>', '</form><ul class="trust"><li>Free</li><li>No sign-up</li><li>' + last
                        + '</li></ul></div>' + visual + '</section><div class="tool-body">', 1)
    return base.replace('</main>', '</div></main>')


def script(source):
    source = source.replace(TOOL_LIST_BADGE_JS, TOOL_METER_BADGE_JS)
    source = source.replace('return lead ? toolPhoto(item.image, item.title)',
                            'return lead ? toolGauge(toolBadge(item.price_cents, item.list_price_cents)) + toolPhoto(item.image, item.title)')
    source = source.replace('result.innerHTML = task.result; result.hidden = false;',
                            'result.innerHTML = task.result; result.hidden = false; toolObserveMeters();')
    source = source.replace("analyzer.hidden = false; analyzer.setAttribute('aria-busy', 'true');",
                            "analyzer.hidden = false; analyzer.setAttribute('aria-busy', 'true');\n"
                            "  analyzer.scrollIntoView({behavior:task.reduced ? 'auto' : 'smooth', block:'start'});")
    svg = (TEMPLATES / "tool-meter.svg").read_text()
    prefix = 'function toolGauge(badge) {\n  return ' + json.dumps(svg) + (
        '.replaceAll("{{ANGLE}}", badge.angle).replaceAll("{{PCT}}", badge.pct)'
        '.replaceAll("{{LABEL}}", esc(badge.label)).replaceAll("{{TEXT}}", '
        'badge.hasList ? badge.pct + "% off" : "Live Amazon price");\n}\n')
    observer = (TEMPLATES / 'tool-hero.js').read_text()
    return prefix + observer.removesuffix('toolObserveMeters();\n') + source + '\ntoolObserveMeters();\n'
