"""Operator (Nishi) 2026-10-24 ~04:00Z, screenshot of "See highly rated vacuum on Amazon →":
"shouldnt this have a s. like vacuums". The yellow button (tool-name-panel.js, in both Fake
Deal Spotter's and Review Checker's scripts) names the typed words with the last word in the
plural, only where that is safe; anything else stays exactly as typed. Its link keeps the
typed words. No other wording changes (the new wording is picked on Oct 26). Off changes no
byte: the template stays as it is and both scripts read it unpatched.
"""
import hashlib
from pathlib import Path

from .template_slots import replace_slot

TEMPLATES = Path(__file__).with_name("templates")
PANEL = "// Fills the panel and returns its button, or null (no button) for adult or unusable words.\n"
BUTTON = "  button.append('See highly rated ' + words + ' on Amazon', arrow);\n"
PLURAL = r"""// plural_highly_rated (operator 2026-10-24 ~04:00Z, "like vacuums"): the last word in the
// plural only where that is safe; anything else as typed. The link keeps the typed words.
function ratedWords(words) {
  var list = words.split(' '), last = list[list.length - 1];
  // At most 3 words, no joining word, a plain lowercase last word of 3+ letters.
  if (list.length > 3 || list.some(word => /^(?:for|with|without|under|over|to|of|in|on|and|or|by|from|&|\+)$/i.test(word)) ||
      !/^[a-z]{3,}$/.test(last)) return words;
  if (/(?:knife|shelf)$/.test(last)) last = last.replace(/fe?$/, 'ves');
  // Not by these endings: a plural already (one s), -less/-ness, f/fe/o, mass nouns, irregulars;
  else if (/(?:[^s]s|less|ness|f|fe|o|ware|wear|paper|foil|furniture|jewelry|clothing|makeup|luggage|bedding|skincare|coffee|equipment|gear|lingerie|decor|storage|food|litter|man|men|mouse|child|person|foot|tooth|fish|sheep)$/.test(last) ||
           // nor these words: more of them, colours, sizes and one-word brands.
           /^(?:tea|rice|hair|mice|feet|teeth|people|children|black|white|red|blue|green|pink|purple|gold|silver|gray|grey|brown|beige|large|small|mini|max|ultra|air|apple|bose|dyson|ninja|nike|samsung|sony|shark)$/.test(last)) return words;
  else last = /(?:ch|sh|x|z|ss)$/.test(last) ? last + 'es' : /[^aeiou]y$/.test(last) ? last.slice(0, -1) + 'ies' : last + 's';
  list[list.length - 1] = last;
  return list.join(' ');
}
"""


def enabled(cfg):
    return getattr(cfg, "plural_highly_rated", False) is True


def name_panel(cfg):
    """tool-name-panel.js as both tools' scripts read it; ON: the button's words in the plural."""
    text = (TEMPLATES / "tool-name-panel.js").read_text()
    if not enabled(cfg):
        return text
    text = replace_slot(text, PANEL, PLURAL + PANEL, template_name="tool-name-panel.js", slot_name="plural")
    return replace_slot(text, BUTTON, BUTTON.replace("' + words + '", "' + ratedWords(words) + '"),
                        template_name="tool-name-panel.js", slot_name="plural-button")


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]
