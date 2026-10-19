"""Opt-in fresh selections and the clock-only drop moment."""
import hashlib
import json
import re
from html import escape
from pathlib import Path

from .same_product_spacing import rules, family_key, PACK, WORDS
from .template_slots import replace_slot

SCRIPT = Path(__file__).with_name('templates') / 'drop-fresh.js'
STYLE = Path(__file__).with_name('templates') / 'drop-fresh.css'
LOADER = SCRIPT.with_name('drop-fresh-loader.js')
CONFETTI = SCRIPT.with_name('drop-confetti.js')
ASSET = 'drop-fresh.js'


def enabled(cfg):
    return getattr(cfg, 'drop_fresh', False) and cfg.drop_shuffle


def pick_options(cfg):
    # Keep the existing pick rank. Fresh rotation also includes eligible rows
    # below those picks, ordered by the existing earning-aware score.
    return cfg.front_page


def hot_html(row, cfg):
    """Match engagement.js at the published facts' timestamp, before hydration."""
    if (not cfg.engagement.get('hot_meter') or row.get('status') == 'dead'
            or row.get('locked') or row.get('price_unconfirmed') or row.get('price_aged')):
        return ''
    signals = []
    pct = row.get('pct_at_checkout')
    if pct is not None and pct >= 60:
        signals.append(f'{pct:g}% off')
    if row.get('badge') == 'corroborated':
        sources = len({c['name'] for c in row.get('credits', []) if c.get('name')})
        signals.append(f'confirmed by {sources if sources >= 2 else "2+"} sources')
    age = row.get('age_hours')
    if age is not None and 0 <= age < 2:
        signals.append('first seen less than 2 hours ago')
    if len(signals) < 2:
        return ''
    label = escape('Hot: ' + ', '.join(signals))
    return (f'<span class="hotmeter" role="img" title="{label}" aria-label="{label}">'
            f'{"🔥" * len(signals)}</span>')


def women(row):
    import re
    return bool(row.get("allure") or row.get("category") in
                {"beauty", "home", "kitchen", "kids_baby", "toys_games"} or
                re.search(r"\b(?:women|womens|woman|ladies|girls|baby|kids|beauty|home|kitchen)\b",
                          str(row.get("item") or "").lower()))


def cheap(row):
    return type(row.get("final_price")) in (int, float) and 0 <= row["final_price"] <= 50


def rotation(rows, options):
    """A balanced circular inventory, retaining rank within each mix group.

    Women/other lanes use cumulative apportionment, so every 90-slot window
    has at least 63 women-oriented rows when at least 70% of stock qualifies.
    Other mix groups use weighted fair queues to avoid exhausting basics first.
    """
    seen, pool = set(), []
    for row in sorted(rows, key=lambda d: (d.get("pick", float("inf")),
                      -d.get("ps", 0), not d.get("earns"), d.get("key", ""))):
        if (row.get("locked") or row.get("status") != "alive" or not row.get("image_url")
                or row.get("niche") or row.get("category") in options.get("niche_categories", [])
                or any(row.get(k) for k in ("click_price_unknown", "price_unconfirmed", "price_aged", "fresh_blocked"))
                or set(row.get("conditions") or []) & {"used", "open_box", "refurbished"}):
            continue
        key = family_key(row) or row["key"]
        if key not in seen:
            seen.add(key)
            pool.append(row)
    lanes = [{}, {}]
    for row in pool:
        signature = (row.get("category"), cheap(row), bool(row.get("cl")),
                     bool(row.get("pick_checkout")), bool(row.get("allure")))
        lanes[int(women(row))].setdefault(signature, []).append(row)
    groups = [[dict(rows=rs, at=0) for rs in lane.values()] for lane in lanes]
    total = len(pool)
    female = sum(women(d) for d in pool)
    out, served = [], [0, 0]
    totals = [total - female, female]
    for pos in range(total):
        lane = int((pos + 1) * female // total > pos * female // total)
        available = [g for g in groups[lane] if g["at"] < len(g["rows"])]
        g = max(available, key=lambda g: (served[lane] + 1) * len(g["rows"]) - g["at"] * totals[lane])
        out.append(g["rows"][g["at"]])
        g["at"] += 1
        served[lane] += 1
    return out


def _base_page(pool, mark, options, policy):
    """Consecutive circular slices never repeat until the inventory wraps.

    Only reorder inside a slice: spacing cannot secretly borrow a card from
    the previous mark. Impossible mixes degrade within eligible stock only.
    """
    size = options.get("fresh_page_size", 60)
    if not pool:
        return []
    start = mark * size % len(pool)
    pending = [pool[(start + i) % len(pool)] for i in range(min(size, len(pool)))]
    out = []
    target = min(19, sum(cheap(d) for d in pending), len(pending))
    def fits(d):
        pos = len(out)
        if d.get("allure") and policy["allure"] and (pos < policy["first"] or
                any(r.get("allure") for r in out[max(0, pos - policy["allure"] + 1):])):
            return False
        if d.get("cl") and policy["clothing"] and pos < policy["slots"] and sum(
                bool(r.get("cl")) for r in out[pos // policy["mix"] * policy["mix"]:]) >= policy["clothing"]:
            return False
        if d.get("pick_checkout") and sum(bool(r.get("pick_checkout")) for r in
                out[max(0, pos - policy["promo"] + 1):]) >= policy["cap"]:
            return False
        run = options.get("max_same_category_run", 2)
        return not (pos >= run and all(r.get("category") == d.get("category") for r in out[-run:]))
    from collections import Counter
    categories = Counter(d.get("category") for d in pending)
    total = len(pending)
    def room(d):
        pos = len(out)
        run = options.get("max_same_category_run", 2)
        for cat, count in categories.items():
            remaining = count - (d.get("category") == cat)
            other = total - pos - 1 - remaining
            streak = 0
            if d.get("category") == cat:
                streak = 1
                for r in reversed(out):
                    if r.get("category") != cat:
                        break
                    streak += 1
            if remaining > run * (other + 1) - streak:
                return False
        return True
    while pending:
        need = len(out) < 24 and 24 - len(out) <= target - sum(cheap(d) for d in out[:24])
        choices = sorted(range(len(pending)), key=lambda i: (
            -int(need and cheap(pending[i])),
            -int(bool(pending[i].get("allure"))) - int(bool(pending[i].get("pick_checkout"))),
            -int(len(out) < 24 and cheap(pending[i])), i))
        at = next((i for i in choices if fits(pending[i]) and room(pending[i])), None)
        if at is None:
            at = next((i for i in choices if fits(pending[i])), 0)
        d = pending.pop(at)
        out.append(d)
        categories[d.get("category")] -= 1
    return out


def page(pool, mark, options, policy):
    """Keep the ranked rotation; trade matching late slots for strong winners.

    The first 75% of each base page are never replaced, so previous positions
    are known without replaying visitor history or recursively rebuilding marks.
    Winners come from that stable prefix. Matching every mix attribute preserves
    the base page's spacing/caps, and looking ahead prevents a winner repeating
    into the next page's fresh prefix when the circular inventory wraps.
    """
    out = _base_page(pool, mark, options, policy)
    size = options.get("fresh_page_size", 60)
    if len(pool) < 2 * size:
        return out
    limit = size // 4
    boundary = size - limit
    previous = _base_page(pool, mark - 1, options, policy)
    unavailable = {d["key"] for d in out}
    start = (mark + 1) * size % len(pool)
    unavailable.update(pool[(start + i) % len(pool)]["key"] for i in range(size))
    def signature(d):
        return (d.get("category"), women(d), cheap(d), bool(d.get("cl")),
                bool(d.get("pick_checkout")), bool(d.get("allure")))
    # Existing pick order includes the private popularity signals; do not export
    # new click/save counts or consult a visitor's personal saved list.
    candidates = sorted(enumerate(previous[:boundary]), key=lambda pair: (
        pair[1].get("pick", float("inf")), -pair[1].get("ps", 0),
        not pair[1].get("earns"), pair[1]["key"]))
    available = set(range(boundary, size))
    for old, row in candidates:
        if row["key"] in unavailable:
            continue
        at = next((i for i in sorted(available) if abs(i - old) >= 12
                   and signature(row) == signature(out[i])), None)
        if at is not None:
            out[at] = row
            available.remove(at)
    return out


def template(html, cfg):
    if not enabled(cfg):
        return html

    def swap(old, new):
        nonlocal html
        html = replace_slot(html, old, new, template_name='index.html', slot_name='drop-fresh')

    # Reserve both title lines on homepage cards from first paint onward,
    # independent of the deferred module's controls or the current wall view.
    swap('</style>', '#content .card .title{min-height:2lh}</style>')

    # The old repair only acted on Trending/top, which now uses a complete
    # eligible slice. Its rotation/repair code is unreachable on other views.
    start, end = html.index('  var DROP_WINDOW = 24;'), html.index('  function topOrder(')
    swap(html[start:end], '')
    if 'spaceRotatedPromos(spaceAllure(rows))' in html:
        swap('spaceRotatedPromos(spaceAllure(rows))', 'spaceAllure(rows)')
    start, end = html.index('  function shuffleDropPicks('), html.index('  function refreshDropPicks(')
    swap(html[start:end], LOADER.read_text() + '\n')
    swap('    var focus = rememberFocus();\n    // No FLIP motion for a clock-only reorder.',
         '    if (!freshUI || freshUI.deferFreshDrop()) return;\n'
         '    freshUI.before();\n'
         '    var focus = rememberFocus();\n    // No FLIP motion for a clock-only reorder.')
    swap("      rows = shuffleDropPicks(rows, state.seed);",
         "      if (freshUI) rows = freshUI.shuffleDropPicks(state.deals.concat(state.spotlight || []), dropPickIndex());")
    if cfg.same_product_spacing:
        swap("    if (!searchText()) rows = spaceSameProducts(rows, spotlight);",
             "    if (!searchText() && !freshView()) rows = spaceSameProducts(rows, spotlight);")
    swap('  function activeSpotlight() {', '  function activeSpotlight() {\n    if (freshView()) return [];')
    swap('    var content = document.getElementById("content");\n    browseRows = [];',
         '    if (freshView() && !freshUI || freshUI && freshUI.hold()) return;\n'
         '    var content = document.getElementById("content");\n    browseRows = [];')
    swap('  function reorganize(reshuffle, sweep) {',
         '  function reorganize(reshuffle, sweep) {\n'
         '    if (document.getElementById("q").value.trim()) return false;\n'
         '    if (freshUI && freshUI.deferFreshDrop()) return false;')
    # Adopt background inventory without replacing the visitor's search DOM.
    # The next explicit search/browse action renders from the updated state.
    swap('    returnRefresh = fetchPublish().then(merge).then(function () {',
         '    returnRefresh = fetchPublish().then(merge).then(function () {\n'
         '      if (document.getElementById("q").value.trim()) return;')
    swap('remaining = state.shown;', 'remaining = freshView() ? freshUI.freshSize() : state.shown;')
    swap('    if (rows.length > state.shown) {',
         '    if (freshView()) html += freshUI.freshControls();\n    else if (rows.length > state.shown) {')
    swap('  function armMore(more) {', '  function armMore(more) {\n    if (freshView()) return;')
    swap('    browseRows = spotlight.concat(rows);',
         '    if (freshUI) freshUI.updateFreshDrop(rows);\n    browseRows = spotlight.concat(rows);')
    swap('    var media = mediaHtml(d);',
         '    var media = mediaHtml(d);\n'
         '    if (freshUI && freshUI.badge(d))\n'
         '      media += \'<span class="just-dropped">Just dropped</span>\';')
    swap('    content.innerHTML = html;\n    // Keep the persistent CTA panel',
         '    content.innerHTML = html;\n    if (freshUI) freshUI.paintFreshDrop();\n    // Keep the persistent CTA panel')
    swap('<span id="countdown"', '<button id="fresh-drop-pill" type="button" hidden></button><span id="fresh-drop-toast" role="status" hidden></span><span id="countdown"')
    return html


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + SCRIPT.read_bytes() + STYLE.read_bytes()
                          + LOADER.read_bytes() + CONFETTI.read_bytes()
                          + Path(__file__).read_bytes()).hexdigest()[:12]


def publish(out, cfg):
    asset = out / ASSET
    if not enabled(cfg):
        asset.unlink(missing_ok=True)
        return
    policy = rules(cfg.front_page, 8 if cfg.credit_links else 10)
    script = SCRIPT.read_text().replace('{{FRESH_RULES}}', json.dumps(policy, separators=(',', ':')))
    script = script.replace('{{FAMILY_PACK}}', PACK.replace('/', r'\/')).replace('{{FAMILY_WORDS}}', WORDS)
    names = ('state dealKey isLocked dropPickIndex reduceMotion '
             'cardEls render rememberBrowse renderTabs finishBrowseAction').split()
    script = re.sub(r'\b(' + '|'.join(names) + r')\b', lambda m: 'host.' + m[0], script)
    script = ('export default function (host) {\n' + CONFETTI.read_text() + script +
              '  var style = document.createElement("style"); style.textContent = ' + json.dumps(STYLE.read_text()) + ';\n'
              '  document.head.appendChild(style);\n'
              '  return {freshView, freshSize, shuffleDropPicks, deferFreshDrop, freshControls, updateFreshDrop, paintFreshDrop,\n'
              '    before() { freshDropBefore = new Set(freshDropHead); },\n'
              '    badge(d) { return freshView() && freshDropMark === host.dropPickIndex() && freshDropKeys.has(host.dealKey(d)); },\n'
              '    hold() { if (!freshView()) { freshPending = false; stopConfetti(); document.getElementById("fresh-drop-pill").hidden = true; } return freshPending; }\n'
              '  };\n}\n')
    version = hashlib.sha256(script.encode()).hexdigest()[:12]
    path = out / 'index.html'
    html = path.read_text(encoding='utf-8')
    # Like age_hours, heat describes the published snapshot. A visitor crossing
    # the two-hour boundary must not remove a flame (or the entire meter).
    if cfg.engagement:
        for old, new in (
            (', seen = Date.parse(d.first_seen_at || "")', ''),
            ('var age = Date.now() - seen;', 'var age = d.age_hours;'),
            ('age >= 0 && age < 2 * 60 * 60 * 1000', 'age != null && age >= 0 && age < 2'),
            # The named img already supplies the accessible text; its inner
            # span adds no semantics or styling. Keep both renderers compact.
            (''''<span aria-hidden="true">' + "🔥".repeat(signals.length) + "</span></span>"''',
             '''"🔥".repeat(signals.length) + "</span>"'''),
        ):
            html = replace_slot(html, old, new, template_name='index.html', slot_name='drop-fresh-heat')
    html = replace_slot(html, '{{FRESH_VERSION}}', version, template_name='index.html', slot_name='drop-fresh-asset')
    asset.write_text(script, encoding='utf-8')
    path.write_text(html, encoding='utf-8')
