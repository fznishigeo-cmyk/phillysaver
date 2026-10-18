"""Optional private Amazon-link analysis; OFF preserves the preceding release."""

import hashlib
import json
from pathlib import Path

from . import search_boost, search_results
from .template_slots import replace_slot

SCRIPT = Path(__file__).with_name("templates") / "search-analyze.js"
STYLE = SCRIPT.with_suffix(".css")


def enabled(cfg):
    return bool(getattr(cfg, "search_analyze", {}).get("enabled")) and search_results.enabled(cfg)


def template(html, cfg):
    if not enabled(cfg):
        return html

    def swap(old, new):
        nonlocal html
        html = replace_slot(html, old, new, template_name="index.html", slot_name="search-analyze")

    script = SCRIPT.read_text().replace("{{ANALYZE_EXCLUDE}}", json.dumps(
        search_boost.adult_pattern(cfg.search_boost["exclude_words"])).replace("<", "\\u003c"))
    swap('placeholder="Search deals" aria-label="Search deals" aria-describedby="searchcount"',
         'placeholder="Search any product or paste an Amazon link" '
         'aria-label="Search any product or paste an Amazon link" aria-describedby="search-hint searchcount"')
    swap('        <button id="clearsearch"',
         '        <button id="analyze-search" type="button" aria-label="Analyze product">'
         '<span class="analyze-long">Analyze</span><span class="analyze-short">Go</span></button>\n'
         '        <button id="clearsearch"')
    swap('      <p id="searchcount"',
         '      <p id="search-hint">Free price check · no sign-up</p>\n      <p id="searchcount"')
    swap('  function renderSearchCount(rows) {', script + '  function renderSearchCount(rows) {')
    signature = '  function queueAmazonResults(rows' + (', wait' if search_boost.enabled(cfg) else '') + ') {'
    swap(signature, signature + '\n    if (typeof analyzeTask !== "undefined" && analyzeTask && analyzeCurrent(analyzeTask)) return;')
    swap('      fetch("/api/search?q=" + encodeURIComponent(q),',
         '      analyzeQueryPromise = fetch("/api/search?q=" + encodeURIComponent(q),')
    swap('        .then(function (r) { return r.ok ? r.json() : {items:[]}; })',
         '        .then(function (r) { return r.ok ? r.json() : {items:[]}; });\n      analyzeQueryPromise')
    swap('  function searchText() { return document.getElementById("q").value.trim(); }',
         '  function searchText() { return typeof analyzeSearchText === "function" ? analyzeSearchText() : document.getElementById("q").value.trim(); }')
    swap('    if (moreObserver) moreObserver.disconnect();\n    paintTicker();',
         '    if (moreObserver) moreObserver.disconnect();\n    if (typeof analyzeRender === "function" && analyzeRender()) return;\n    paintTicker();')
    swap('  function updateSearch() {\n    clearTimeout(searchTimer);',
         '  function updateSearch() {\n    clearTimeout(searchTimer);\n'
         '    if (typeof analyzeInput === "function" && analyzeInput(document.getElementById("q").value).kind !== "query") { analyzeSubmit(); return; }')
    swap('if (e.key === "Enter") { e.preventDefault(); updateSearch(); }',
         'if (e.key === "Enter") { e.preventDefault(); analyzeSubmit(); }')
    return html.replace("</style>", STYLE.read_text() + "</style>", 1)


def browse_template(html, cfg):
    if not enabled(cfg):
        return html
    # A pasted URL never enters browser history or persisted browse snapshots.
    for old, new in (
        ('query: document.getElementById("q").value.slice(0, 500)', 'query: analyzeSearchText().slice(0, 500)'),
        ('document.getElementById("minpct").value, document.getElementById("q").value]',
         'document.getElementById("minpct").value, analyzeSearchText()]'),
    ):
        html = replace_slot(html, old, new, template_name="index.html", slot_name="analyze-browse")
    return html


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes() + SCRIPT.read_bytes()
                          + STYLE.read_bytes() + json.dumps(cfg.search_boost, sort_keys=True).encode()).hexdigest()[:12]
