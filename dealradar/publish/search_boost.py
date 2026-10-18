"""Operator 2026-10-17: on a search we have no deal for, a big full-width
"Compare all prices on Amazon" button leads, then the Amazon cards; adult
products never paint as cards; a phone's rate-limited final word retries.
OFF (missing/false) leaves every published byte unchanged."""

import hashlib
import json
import re
from pathlib import Path

from . import search_results
from .template_slots import replace_slot

CSS = (".search-recovery{max-width:1240px;border-radius:24px;padding:28px;background:var(--card);"
       "box-shadow:0 12px 40px rgba(0,0,0,.08)}"
       ".search-recovery h2{font-size:30px;line-height:1.15;letter-spacing:-.02em;margin:0 0 6px}"
       ".search-amazon-top{margin:16px 0 24px}"
       # Operator 2026-10-17 (Verizon "Let's Chat"): white pill, a red-yellow-green border that flows.
       ".search-amazon-top .getdeal,.search-amazon-top .getdeal:hover{display:inline-flex;align-items:center;"
       "justify-content:center;gap:10px;min-height:62px;padding:14px 34px;border-radius:999px;"
       "border:3px solid transparent;background:linear-gradient(var(--card),var(--card)) padding-box,"
       "linear-gradient(90deg,#ff3b30,#ffcc00,#34c759,#ffcc00,#ff3b30) 0 0/300% 100% border-box;"
       "color:var(--fg)!important;font-size:19px;font-weight:800;letter-spacing:-.01em;text-decoration:none;"
       "white-space:normal;box-shadow:0 10px 28px rgba(255,140,0,.28);animation:wpflow 3s linear infinite;"
       "transition:transform .15s,box-shadow .15s}"
       ".search-amazon-top .getdeal::before{content:\"\\2726\";content:\"\\2726\"/\"\";font-size:22px;line-height:1;color:#ff8a00}"
       ".search-amazon-top .getdeal:hover,.search-amazon-top .getdeal:focus-visible{transform:translateY(-2px);"
       "box-shadow:0 14px 34px rgba(255,140,0,.4)}"
       "@keyframes wpflow{to{background-position:0 0,300% 0}}"
       ".search-amazon-top small{display:block;margin:8px 0 0;font-size:11px;color:var(--muted)}"
       "#amazon-results h2{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}"
       "#amazon-results .grid{grid-template-columns:repeat(auto-fill,minmax(210px,1fr))}"
       "#amazon-results .getdeal{display:block;text-align:center;white-space:nowrap}"
       "@media (max-width:600px){.search-recovery{padding:20px}.search-recovery h2{font-size:24px}"
       ".search-amazon-top .getdeal,.search-amazon-top .getdeal:hover{display:flex;width:100%;padding:14px 20px}}"
       "@media (prefers-reduced-motion:reduce){.search-amazon-top .getdeal{animation:none;transition:none}"
       ".search-amazon-top .getdeal:hover,.search-amazon-top .getdeal:focus-visible{transform:none}}")


def enabled(cfg) -> bool:
    return bool(cfg.search_boost["enabled"]) and search_results.enabled(cfg)


def adult_pattern(words) -> str:
    """Whole words or phrases, any case; a space or hyphen matches either."""
    if not words:
        return ""
    return ("(?:^|[^\\p{L}\\p{N}])(?:" + "|".join(re.sub(r"[ -]+", "[ -]+", w) for w in words)
            + ")(?![\\p{L}\\p{N}])")


def template(html, cfg):
    if not enabled(cfg):
        return html

    def swap(old, new):
        nonlocal html
        html = replace_slot(html, old, new, template_name="index.html", slot_name="search-boost")

    pattern = json.dumps(adult_pattern(cfg.search_boost["exclude_words"])).replace("<", "\\u003c")
    swap("'<div id=\"amazon-results\" data-clarity-mask=\"true\"></div><div class=\"search-amazon\" data-clarity-mask=\"true\">' + searchAmazonHtml(true)",
         "'<div class=\"search-amazon search-amazon-top\" data-clarity-mask=\"true\">' + searchAmazonHtml(true)"
         " + '</div><div id=\"amazon-results\" data-clarity-mask=\"true\">'")
    swap("<h2 id=\"search-recovery-title\">Try a broader search or different filters</h2>'",
         "<h2 id=\"search-recovery-title\">' + (searchAmazonQuery() ? 'Live Amazon prices for &quot;' +"
         " esc(searchAmazonQuery()) + '&quot;' : 'Try a broader search or different filters') + '</h2>'")
    swap("rel=\"sponsored noopener\">Search Amazon for &quot;' + esc(q) +",
         "rel=\"sponsored noopener\">' + (primary ? 'Compare all &quot;' : 'Search Amazon for &quot;') + esc(q) +")
    swap("'&quot;</a><small>Amazon links may earn us a commission.</small>'",
         "'&quot;' + (primary ? ' prices on Amazon \\u2192' : '') + '</a><small>Amazon links may earn us a commission.</small>'")
    swap('  var amazonResultsTimer, amazonResultsRequest, amazonResultsQuery = "";\n',
         '  var amazonResultsTimer, amazonResultsRequest, amazonResultsQuery = "", amazonResultsRetries = 0;\n'
         '  var amazonResultsExclude = ' + pattern + ' ? new RegExp(' + pattern + ', "iu") : null;\n'
         '  function amazonResultsAdult(text) { return !!amazonResultsExclude && amazonResultsExclude.test(text); }\n')
    swap("        Date.now() - Date.parse(d.as_of) <= 86400000;\n    }).map(",
         "        Date.now() - Date.parse(d.as_of) <= 86400000 && !amazonResultsAdult(d.title);\n    }).map(")
    swap("  function queueAmazonResults(rows) {", "  function queueAmazonResults(rows, wait) {")
    swap(r"        !/^[\p{L}\p{N} -]{2,60}$/u.test(q)) { amazonResultsCancel(); return; }" "\n"
         "    if (q !== amazonResultsQuery) { amazonResultsCancel(); amazonResultsQuery = q; }",
         r"        !/^[\p{L}\p{N} -]{2,60}$/u.test(q) || amazonResultsAdult(q)) { amazonResultsCancel(); return; }" "\n"
         "    if (q !== amazonResultsQuery) { amazonResultsCancel(); amazonResultsQuery = q; amazonResultsRetries = 0; }")
    swap("          var items = Array.isArray(data.items) ? data.items.slice(0, 10) : [];\n"
         "          amazonResultsSeen.set(q, items);",
         "          var items = Array.isArray(data.items) ? data.items.slice(0, 10) : [];\n"
         "          // Typing on a phone sends partial words; a busy final word retries, twice at most.\n"
         "          if (!items.length && (data.reason === \"rate_limited\" || data.reason === \"timeout\") && amazonResultsRetries < 2) {\n"
         "            amazonResultsSeen.delete(q); amazonResultsRetries++;\n"
         "            amazonResultsRequest = null; queueAmazonResults([], 1500); return;\n"
         "          }\n"
         "          amazonResultsSeen.set(q, items);")
    swap("          if (amazonResultsRequest === controller) amazonResultsRequest = null;\n        });\n    }, 200);",
         "          if (amazonResultsRequest === controller) amazonResultsRequest = null;\n        });\n    }, wait || 600);")
    return html.replace("</style>", CSS + "</style>", 1)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()
                          + json.dumps(cfg.search_boost, sort_keys=True).encode()).hexdigest()[:12]
