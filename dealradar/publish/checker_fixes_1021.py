"""Operator 2026-10-21: the Deal Checker's "See similar deals" searches the typed words, and the
lead card's price age reads in minutes and hours ("44 min ago", not "2638 sec ago")."""

from . import checker_similar_heading

# checker_similar_heading put the product name first; a pasted link still has no words to use.
NAME_FIRST = ("  var words = toolQuery(shorten(item.title || '')) ||\n"
              "    (input.kind === 'query' ? toolQuery(raw) : toolQuery(shorten(link && link.words || '')));")
NAME_COMMENT = "  // The similar-deals search reads the product name, not the typed words.\n"
TYPED_COMMENT = "  // The similar-deals search reads the typed words; a pasted link uses the product name.\n"
AGE = ("function toolAge(asOf) {\n"
       "  var s = Math.max(0, Math.floor((Date.now() - Date.parse(asOf)) / 1000));\n"
       "  if (s < 60) return s + ' sec ago';\n"
       "  var m = Math.floor(s / 60);\n"
       "  if (m < 60) return m + ' min ago';\n"
       "  var h = Math.floor(m / 60);\n"
       "  return h + (h === 1 ? ' hr ago' : ' hrs ago');\n"
       "}\n")
CARD_AGE = "'\">price checked ' + time + ' sec ago on Amazon</p>'"
TICK_AGE = ("el.textContent = 'price checked ' + Math.max(0, Math.floor((Date.now() - "
            "Date.parse(el.dataset.priceTime)) / 1000)) + ' sec ago on Amazon';")


def typed_words(cfg):
    return getattr(cfg, 'checker_similar_typed_words', False) is True and checker_similar_heading.enabled(cfg)


def price_age(cfg):
    return getattr(cfg, 'price_age_minutes', False) is True


def script(source, cfg):
    if typed_words(cfg):
        source = source.replace(NAME_FIRST, checker_similar_heading.WORDS).replace(NAME_COMMENT, TYPED_COMMENT)
    if price_age(cfg) and CARD_AGE in source and TICK_AGE in source:
        source = source.replace(CARD_AGE, "'\">price checked ' + toolAge(item.as_of) + ' on Amazon</p>'")
        source = source.replace(TICK_AGE, "el.textContent = 'price checked ' + toolAge(el.dataset.priceTime) + ' on Amazon';")
        source = source.replace('function toolLiveCard(item, lead) {', AGE + 'function toolLiveCard(item, lead) {', 1)
    return source
