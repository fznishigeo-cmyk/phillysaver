"""Operator 2026-10-20: the Deal Checker's yellow button opens the product itself, and the
"Top sellers like this on Amazon" heading becomes the yellow "See similar deals" button."""

from . import buttons_to_tools

LEAD = ("(lead ? toolButton(toolSimilar(item), 'See similar deals on Amazon →', 'search_similar_deals') :\n"
        "      toolButton(toolAmazon(item.asin), 'View at Amazon', 'search_amazon'))")
WORDS = ("  var words = input.kind === 'query' ? toolQuery(raw) :\n"
         "    toolQuery(shorten(item.title || '')) || toolQuery(shorten(link && link.words || ''));")
HEADING = ("function toolSimilarHeading(top, lead) {\n"
           "  // The similar-deals search reads the product name, not the typed words.\n"
           "  var title = document.getElementById('top-title');\n"
           "  if (lead && title) title.innerHTML = toolButton(toolSimilar(lead), 'See similar deals on Amazon →', 'search_similar_deals');\n"
           "  return !lead;\n"
           "}\n")
CSS = '#top-title .amazon-button{margin-top:0}'


def enabled(cfg):
    return (getattr(cfg, 'checker_similar_heading', False) is True
            and buttons_to_tools.enabled(cfg))


def script(source, cfg):
    if not enabled(cfg):
        return source
    for old, new in ((LEAD, "toolButton(toolAmazon(item.asin), lead ? 'See it on Amazon →' : 'View at Amazon', 'search_amazon')"),
                     (WORDS, "  var words = toolQuery(shorten(item.title || '')) ||\n"
                             "    (input.kind === 'query' ? toolQuery(raw) : toolQuery(shorten(link && link.words || '')));"),
                     ("function toolSimilar(item) {", HEADING + "function toolSimilar(item) {"),
                     ("    top.hidden = !task.sellers.length;",
                      "    top.hidden = toolSimilarHeading(top, task.lead) && !task.sellers.length;"),
                     ("      top.hidden = !sellers.length;",
                      "      top.hidden = toolSimilarHeading(top, lead) && !sellers.length;")):
        if old in source:
            source = source.replace(old, new)
    return source
