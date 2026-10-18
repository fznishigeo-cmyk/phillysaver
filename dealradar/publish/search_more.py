"""Optional related Amazon pages and one local spelling correction per search."""

import hashlib
import json
from pathlib import Path

from ..config import validated_search_more
from . import search_analyze, search_boost, search_circle, search_results
from .template_slots import replace_slot

SCRIPT = Path(__file__).with_name("templates") / "search-more.js"
LOADER = SCRIPT.with_name("search-more-loader.js")
STYLE = SCRIPT.with_suffix(".css")
WORDS = SCRIPT.with_name("search-shopping-words.txt")
ASSET = "search-more.js"


# Operator 2026-10-17: "the urge to click it becomes irresistible" - heavier text, a golden shine
# sweeping across, a twinkling sparkle and a lift on hover; reduced motion keeps them still.
BOLD = ("#search-amazon-result [data-search-amazon],.search-amazon-top .getdeal{position:relative;overflow:hidden;"
        "isolation:isolate;font-weight:900;letter-spacing:-.01em;transition:transform .15s ease,box-shadow .15s ease}"
        "#search-amazon-result [data-search-amazon]{font-size:17px;min-height:50px}"
        "#search-amazon-result [data-search-amazon]:hover,#search-amazon-result [data-search-amazon]:focus-visible"
        "{transform:translateY(-2px);box-shadow:0 10px 26px rgba(255,140,0,.45)}"
        "#search-amazon-result [data-search-amazon]::after,.search-amazon-top .getdeal::after{content:\"\";position:absolute;"
        "inset:0;z-index:-1;pointer-events:none;background:linear-gradient(105deg,transparent 35%,rgba(255,196,0,.4) 50%,"
        "transparent 65%) 150% 0/250% 100% no-repeat;animation:wpshine 3.2s ease-in-out infinite}"
        "#search-amazon-result [data-search-amazon]::before,.search-amazon-top .getdeal::before{display:inline-block;"
        "animation:wptwinkle 1.6s ease-in-out infinite}"
        "@keyframes wpshine{0%,45%{background-position:150% 0}100%{background-position:-150% 0}}"
        "@keyframes wptwinkle{50%{transform:scale(1.3) rotate(18deg)}}"
        "@media (prefers-reduced-motion:reduce){#search-amazon-result [data-search-amazon]::after,"
        ".search-amazon-top .getdeal::after{display:none}#search-amazon-result [data-search-amazon]::before,"
        ".search-amazon-top .getdeal::before{animation:none}#search-amazon-result [data-search-amazon]{transition:none}}")


def enabled(cfg):
    return validated_search_more(getattr(cfg, "search_more", None))["enabled"] and search_results.enabled(cfg)


def template(html, cfg):
    if not enabled(cfg):
        return html

    def swap(old, new):
        nonlocal html
        html = replace_slot(html, old, new, template_name="index.html", slot_name="search-more")

    start = html.index("  function amazonResultsPaint(q, items) {")
    end = html.index("  function queueAmazonResults(", start)
    swap(html[start:end], '')
    start = html.index('<h2 id="search-recovery-title">')
    end = html.index('</h2>', start) + len('</h2>')
    swap(html[start:end], '<h2 id="search-recovery-title">\' + esc(searchMoreTitle(false)) + \'</h2>')
    swap('    if (box) box.innerHTML = "";',
         '    if (box) box.innerHTML = "";\n'
         '    var title = document.getElementById("search-recovery-title");\n'
         '    if (title) title.textContent = searchMoreTitle(false);')
    swap('fetch("/api/search?q=" + encodeURIComponent(q),',
         'fetch("/api/search?q=" + encodeURIComponent(q) + "&page=0",')
    swap("          amazonResultsPaint(q, items);",
         '          if (await searchMoreCall("spell", q, data)) return;\n          searchMoreCall("paint", q, items);')
    swap('amazonResultsPaint(q, amazonResultsSeen.get(q));', 'searchMoreCall("cached", q, amazonResultsSeen.get(q));')
    swap('        .then(function (data) {\n          if (controller.signal.aborted',
         '        .then(async function (data) {\n          if (controller.signal.aborted')
    swap('      amazonResultsSeen.set(q, []);',
         '      amazonResultsSeen.set(q, []);\n      if (searchMoreUI) searchMoreUI.remember(q, {items:[], reason:"unavailable"});')
    if search_boost.enabled(cfg):
        swap("&& amazonResultsRetries < 2)", "&& amazonResultsRetries < 2 && !searchSpellingState())")
    delay = "wait || 600" if search_boost.enabled(cfg) else "200"
    swap("    }, " + delay + ");",
         "    }, Math.max(" + delay + ", searchSpellingState() ? searchSpellingState().ready - Date.now() : 0));")
    if search_analyze.enabled(cfg):
        swap('      amazonResultsPaint(task.key, task.items);', '      searchMoreCall("paint", task.key, task.items);')
        swap('  async function analyzeLookup(task) {',
             '  async function analyzeLookup(task) {\n'
             '    var spelling = searchSpellingState();\n'
             '    if (!task.asin && spelling && !task.pending) {\n'
             '      if (spelling.data) return spelling.data;\n'
             '      await analyzePause(Math.max(0, spelling.ready - Date.now()));\n'
             '      searchMoreUI.remember(decodeURIComponent(task.key.slice(2)), {items:[], reason:"unavailable"});\n'
             '    }')
        swap('fetch("/api/search?" + task.key,',
             'fetch("/api/search?" + task.key + (task.asin ? "" : "&page=0"),')
        swap('&& retry < 2)', '&& retry < 2 && !(searchSpellingState() && !task.asin))')
        swap('        if (data.reason) throw new Error(data.reason);',
             '        if (data.reason) throw new Error(data.reason);\n'
             '        if (!task.asin && await searchMoreCall("spell", decodeURIComponent(task.key.slice(2)), data)) return;')
        # The site never names its price supplier (site check); the API reason is price_error.
        swap('keepa_error:"Amazon', 'price_error:"Amazon')
        # Hiding the hint on blur moved the undo link, and every card below it, mid-click.
        swap('    analyzeBox.addEventListener("blur", function () { if (analyzeVisited) analyzeHint.hidden = true; });\n', '')
        swap('    return true;\n  }\n  function analyzePause',
             '    if (task.finished && !task.reason && !task.asin && task.local)\n'
             '      searchMoreCall("mount", [task.local].concat(task.related));\n'
             '    return true;\n  }\n  function analyzePause')
    if search_circle.enabled(cfg):
        swap("#search-amazon-result small{margin:0;font-size:11px}",
             "#search-amazon-result small{margin:0;font-size:11px}" + BOLD)
        # Keep the arrow on the button's last line on phones.
        swap("(primary ? ' prices on Amazon \\u2192' : ' \\u2192')",
             "(primary ? ' prices on Amazon\\u00a0\\u2192' : '\\u00a0\\u2192')")
    swap('  function render() {\n', '  function render() {\n    if (typeof searchMoreUI !== "undefined" && searchMoreUI) searchMoreUI.detach();\n')
    swap('  }\n\n  function armMore(more) {',
         '    if (rows.length <= state.shown && typeof searchMoreCall === "function") searchMoreCall("mount", rows);\n  }\n\n  function armMore(more) {')
    swap('    queueAmazonResults(rows);',
         '    if (typeof searchMoreCall === "function") searchMoreCall("queue", rows); else queueAmazonResults(rows);')
    swap('<p id="searchcount"',
         '<p id="search-spelling" data-clarity-mask="true" role="status" hidden></p>\n      <p id="searchcount"')
    script = LOADER.read_text().replace("{{SEARCH_MORE_VERSION}}", site_version("", cfg))
    swap("  function renderSearchCount(rows) {", script + "  function renderSearchCount(rows) {")
    return html


def asset(cfg):
    # Share the existing card builder's evidence clocks, escaping, image checks
    # and affiliate attributes. List Price markup stays in this ON-only asset.
    source = search_results.SCRIPT.read_text()
    start = source.index("  function amazonResultsPaint(q, items) {")
    paint = source[start:source.index("  function queueAmazonResults(", start)]
    builder = paint.split("    var cards = ", 1)[1].split("    box.innerHTML =", 1)[0]
    builder = builder.replace("    }).map(function (d) {", """    }).filter(function (d) {
      if (searchMoreAdult(d.title) || seen.has(d.asin)) return false;
      seen.add(d.asin);
      return true;
    }).map(function (d) {""")
    builder = builder.replace('      return \'<article class="card">\' +', '''      var discount = Number.isSafeInteger(d.list_price_cents) && d.list_price_cents > d.price_cents
        ? Math.round((1 - d.price_cents / d.list_price_cents) * 100) : 0;
      var badge = discount >= 5 ? '<span class="flag" aria-label="' + discount + '% off List Price">-' + discount + '%</span>' : '';
      var list = discount >= 5 ? ' <s><span class="sr-only">List Price </span>$' + (d.list_price_cents / 100).toFixed(2) + '</s>' : '';
      return '<article class="card">' +''')
    builder = builder.replace('(image ? \'<div class="media"><img src="\' +',
                              '(image || badge ? \'<div class="media">\' + (image ? \'<img src="\' +')
    builder = builder.replace('referrerpolicy="no-referrer"></div>\' : \'\')',
                              'referrerpolicy="no-referrer">\' : \'\') + badge + \'</div>\' : \'\')')
    builder = builder.replace("+ '</span></p><p class=", "+ '</span>' + list + '</p><p class=")
    cards = "  function amazonResultsCards(items, seen) {\n    return " + builder + "  }\n"
    paint = (paint[:paint.index("    var cards = ")] +
             '    var cards = amazonResultsCards(items, searchMoreAsins("#amazon-results"));\n' +
             '    var title = document.getElementById("search-recovery-title");\n'
             '    if (title) title.textContent = searchMoreTitle(cards.length);\n' +
             "    box.innerHTML =" + paint.split("    box.innerHTML =", 1)[1])
    paint = paint.replace("amazonResultsQuery !== q", "getResultsQuery() !== q")
    script = SCRIPT.read_text().replace("{{SEARCH_MORE}}", json.dumps(validated_search_more(cfg.search_more)))
    script = script.replace("{{SEARCH_MORE_EXCLUDE}}", json.dumps(
        search_boost.adult_pattern(cfg.search_boost["exclude_words"])).replace("<", "\\u003c"))
    script = script.replace("{{SHOPPING_WORDS}}", json.dumps(" ".join(WORDS.read_text().split())))
    return ('export default function ({searchAmazonQuery, searchMoreTitle, amazonResultsEnabled, amazonResultsSettings,\n'
            '  getResultsQuery, queueAmazonResults, track, searchInventory, searchWords, oneSearchEdit,\n'
            '  filtered, esc, amazonResultsCancel, updateSearch, analyzeCancel}) {\n' + cards + paint + script +
            '  var style = document.createElement("style");\n  style.textContent = ' + json.dumps(STYLE.read_text()) + ';\n'
            '  document.head.appendChild(style);\n'
            '  return {mount:searchMoreMount, detach:searchMoreDetach, paint:amazonResultsPaint, spell:searchSpellingTry,\n'
            '    remember:searchSpellingRemember, cached:searchSpellingCached, suggest:searchSpellingSuggest, settings:searchMoreSettings,\n'
            '    get spelling() { return searchSpellingState; },\n'
            '    queue:function (rows) {\n'
            '      if (searchMoreAdult(searchMoreQuery())) amazonResultsCancel(); else queueAmazonResults(rows);\n'
            '      searchSpellingPaint();\n    }};\n}\n')


def publish(out, cfg):
    if not enabled(cfg):
        (out / ASSET).unlink(missing_ok=True)
        return
    (out / ASSET).write_text(asset(cfg), encoding="utf-8")
    path = out / search_results.CONFIG
    settings = json.loads(path.read_text())
    settings["search_more"] = validated_search_more(cfg.search_more)
    path.write_text(json.dumps(settings, separators=(",", ":")), encoding="utf-8")


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes() + SCRIPT.read_bytes() + LOADER.read_bytes()
                          + STYLE.read_bytes() + WORDS.read_bytes()
                          + json.dumps(validated_search_more(cfg.search_more), sort_keys=True).encode()
                          + json.dumps(cfg.search_boost, sort_keys=True).encode()).hexdigest()[:12]
