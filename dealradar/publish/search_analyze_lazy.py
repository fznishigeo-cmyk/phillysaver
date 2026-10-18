"""Defer the final, composed Analyze script without changing the inline release."""

import hashlib
import json
import re
from pathlib import Path

from . import search_analyze, search_more
from .template_slots import replace_slot

LOADER = search_analyze.SCRIPT.with_name("search-analyze-loader.js")
ASSET = "search-analyze.js"


def enabled(cfg):
    return search_analyze.enabled(cfg) and cfg.search_analyze.get("lazy", False)


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes() + LOADER.read_bytes()).hexdigest()[:12]


def publish(out, cfg):
    asset = out / ASSET
    if not enabled(cfg):
        asset.unlink(missing_ok=True)
        return
    path = out / "index.html"
    html = path.read_text(encoding="utf-8")

    def swap(old, new):
        nonlocal html
        html = replace_slot(html, old, new, template_name="index.html", slot_name="lazy-analyze")

    # Extract after search_more's swaps: its shared cards, spelling, error key
    # and hint behavior must also apply inside the deferred module.
    start = html.index("  var analyzeTask =")
    end = html.index("  var searchMoreUI," if search_more.enabled(cfg) else "  function renderSearchCount(", start)
    source = html[start:end]
    script = source.replace("  var analyzeTask = null, analyzeSeen = new Map(), analyzeQueryPromise;",
                            "  var analyzeSeen = new Map();")
    script = script.replace('keepa_error:"Amazon', 'price_error:"Amazon')
    script = script.replace('  if (analyzeButton) analyzeButton.addEventListener("click", analyzeSubmit);\n', '')
    # The loader owns the button throughout loading; the original input handler
    # still cancels running analyses once the module has mounted.
    names = ("analyzeTask analyzeQueryPromise amazonResultsQuery amazonResultsRequest amazonResultsTimer "
             "searchTimer loadedOnce searchMoreUI searchAmazonQuery amazonResultsEnabled amazonResultsSettings "
             "searchInventory filtered track esc cardHtml dealKey searchWords updateSearch amazonResultsSeen "
             "amazonResultsCancel wpAndroidLimited amazonResultsPaint searchSpellingState searchMoreCall").split()
    script = re.sub(r"\b(" + "|".join(names) + r")\b", lambda m: "host." + m[0], script)
    # First-paint rules (search box, button, hint) stay inline: without them the
    # button and hint render unstyled below the search box until the module loads.
    style = search_analyze.STYLE.read_text()
    critical, deferred = split_style(style)
    script = ('export default function (host) {\n' + script +
              '  var style = document.createElement("style");\n  style.textContent = ' + json.dumps(deferred) + ';\n'
              '  document.head.appendChild(style);\n'
              '  if (document.activeElement === analyzeBox) analyzeHint.hidden = false;\n'
              '  return {input:analyzeInput, text:analyzeSearchText, current:analyzeCurrent,\n'
              '    render:analyzeRender, submit:analyzeSubmit, cancel:analyzeCancel};\n}\n')
    # Content addressing includes all composed hooks and CSS, independently of
    # the page version and comment-stripping setting.
    version = hashlib.sha256(script.encode()).hexdigest()[:12]
    loader = LOADER.read_text().replace("{{ANALYZE_VERSION}}", version)
    extra = ('get searchMoreUI() { return searchMoreUI; }, searchSpellingState, searchMoreCall'
             if search_more.enabled(cfg) else 'amazonResultsPaint')
    loader = loader.replace("{{ANALYZE_EXTRA}}", extra)
    swap(source, loader)
    swap(style, critical)
    swap('if (typeof analyzeInput === "function" && analyzeInput(',
         'if (!analyzeFailed && typeof analyzeInput === "function" && analyzeInput(')
    swap('    amazon.hidden = !amazon.innerHTML;',
         '    if (analyzeFailed && document.getElementById("q").value && !q)\n'
         '      amazon.innerHTML = \'<a class="getdeal" href="https://www.amazon.com/?tag=\' +\n'
         '        encodeURIComponent(amazonResultsSettings.tag) + \'" target="_blank" rel="sponsored nofollow noopener">Search Amazon</a>\';\n'
         '    amazon.hidden = !amazon.innerHTML;')
    asset.write_text(script, encoding="utf-8")
    path.write_text(html, encoding="utf-8")


DEFERRED_CSS = (".search-analysis", ".analyze-progress", ".analyze-error", ".analyze-steps", ".analyze-related",
                "@keyframes analyze-")


def split_style(css):
    """(first-paint rules, rules only the analysis panel needs), one rule or @media block per line."""
    critical, deferred = [], []
    for line in css.splitlines():
        if line.startswith("@media") and not line.startswith("@media(prefers-reduced-motion"):
            head, body = line.split("{", 1)
            rules = [r + "}" for r in body[:-1].split("}") if r]
            keep = [r for r in rules if not r.startswith(DEFERRED_CSS)]
            later = [r for r in rules if r.startswith(DEFERRED_CSS)]
            if keep:
                critical.append(head + "{" + "".join(keep) + "}")
            if later:
                deferred.append(head + "{" + "".join(later) + "}")
        elif line.startswith(DEFERRED_CSS) or "analyze-steps" in line:
            deferred.append(line)
        elif line:
            critical.append(line)
    return "\n".join(critical) + "\n", "\n".join(deferred) + "\n"
