"""Task 320: single-page Amazon best sellers alongside our matching deals."""

import hashlib
import json
from pathlib import Path

from . import card_tidy, search_analyze, search_boost, search_more, search_results
from .template_slots import replace_slot

PRODUCTS = Path(__file__).with_name("templates") / "search-products.js"


def enabled(cfg):
    return getattr(cfg, "analyze_top_sellers", False) is True and search_analyze.enabled(cfg)


def swap(text, old, new):
    return replace_slot(text, old, new, template_name="index.html", slot_name="analyze-top-sellers")


def product_script():
    return PRODUCTS.read_text().split("export function monthlyBoughtLabel", 1)[1]


def social_proof(script, function):
    script = swap(script, "  function " + function + "(",
                  "  function monthlyBoughtLabel" + product_script() + "\n  function " + function + "(")
    return swap(script, '        \'</p><a class="getdeal" href="https://www.amazon.com/dp/\' +',
                '        \'</p>\' + (monthlyBoughtLabel(d.monthlySold) ? \'<p class="headline">\' +\n'
                '          monthlyBoughtLabel(d.monthlySold) + \'</p>\' : \'\') + \'<a class="getdeal" href="https://www.amazon.com/dp/\' +')


def template(html, cfg):
    if not enabled(cfg):
        return html
    # Both consumers use the existing matcher, including exact model numbers,
    # camel-case brands, prefixes and the one-edit allowance for ordinary words.
    shared = PRODUCTS.read_text().split("  function normalizeSearch(", 1)[1].split("export function monthlyBoughtLabel", 1)[0]
    start, end = html.index("  function normalizeSearch("), html.index("  function searchAmazonQuery()")
    html = swap(html, html[start:end], "  function normalizeSearch(" + shared.replace("export ", ""))
    if not search_more.enabled(cfg):
        html = social_proof(html, "amazonResultsPaint")
    # ASIN/link analysis retains the local-first, one-product path.
    html = swap(html, '        filtered(candidates)[0];',
                '        null;\n'
                '      if (!task.asin) task.related = filtered(candidates).slice(0, 6);')
    html = swap(html, '      task.related = await analyzeCompare(inventory, task.local ? task.local.item : task.items[0].title, task.asin, task.local);',
                '      task.related = task.asin ? await analyzeCompare(inventory, task.local ? task.local.item : task.items[0].title, task.asin, task.local)\n'
                '        : filtered(inventory).filter(function (d) { return !analyzeAdult(d.item); }).slice(0, 6);')
    html = swap(html, '    }).slice(0, asin ? 1 : 10);', '    }).slice(0, asin ? 1 : 5);')
    # Detach ordinary search's paging observer/timer before painting Analyze.
    # Analyze itself uses only the existing page-zero lookup/adopted request.
    if search_more.enabled(cfg):
        html = swap(html, '    analyzeTask = task;',
                    '    if (searchMoreUI) searchMoreUI.detach();\n    analyzeTask = task;')
    shown = 'task.related.filter(function (d) { return d.image_url; })' if card_tidy.enabled(cfg) else 'task.related'
    helper = ('' if card_tidy.enabled(cfg) else card_tidy.VERIFIED) + '''  function analyzeDeals(task) {
    var live = searchInventory();
    task.related = task.related.map(function (d) {
      return live.find(function (current) { return dealKey(current) === dealKey(d) && !analyzeAdult(current.item); });
    }).filter(Boolean);
    var shown = ''' + shown + ''';
    return shown.length ? '<div class="analyze-related"><h2>' +
      (shown.every(analyzeVerified) ? "Verified deals" : "Deals") + '</h2><div class="grid">' +
      shown.map(cardHtml).join("") + '</div></div>' : '';
  }
'''
    html = swap(html, '  function analyzeRender() {', helper + '  function analyzeRender() {')
    related = card_tidy.RELATED_NEW if card_tidy.enabled(cfg) else card_tidy.RELATED
    html = swap(html, related + " + '</div></div>' : '')", 'analyzeDeals(task)')
    # Keep our matching deals available when live prices cannot be confirmed.
    html = swap(html, " + '</div>';\n    } else {\n      html = ",
                " + '</div>' + (!task.asin ? analyzeDeals(task) : '');\n    } else {\n      html = ")
    return html


def publish(out, cfg):
    if not enabled(cfg):
        return
    path = out / search_results.CONFIG
    settings = json.loads(path.read_text())
    settings.update(analyze_top_sellers=True, analyze_exclude=search_boost.adult_pattern(cfg.search_boost["exclude_words"]))
    path.write_text(json.dumps(settings, separators=(",", ":")), encoding="utf-8")
    if search_more.enabled(cfg):
        asset = out / search_more.ASSET
        script = social_proof(asset.read_text(), "amazonResultsCards")
        asset.write_text(script, encoding="utf-8")
        version = hashlib.sha256(script.encode()).hexdigest()[:12]
        home = out / "index.html"
        home.write_text(swap(home.read_text(), '/search-more.js?v=' + search_more.site_version("", cfg),
                             '/search-more.js?v=' + version), encoding="utf-8")


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes() + PRODUCTS.read_bytes()).hexdigest()[:12]
