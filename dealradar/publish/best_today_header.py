"""Operator (Nishi) 2026-10-24 ~02:00Z: "best deals today removes the search bar... please fix
and push it through". /best-deals-today/ is written from the hub template (seo._landing_page),
whose header is the old one: logo, "refreshed every 15 minutes" and plain category links. The
home header is built by many later passes over index.html alone (header_polish, tools_merge,
tool_buttons_outside, search_focus_ring, best_today_tab, phone_first_screen, ...), so this
last pass copies the finished one onto the page instead of rebuilding it by hand:

- markup: the home <header> as published. The logo is a paragraph (the page keeps its one h1),
  "Best today" is the current page (selected, not a link to itself) and Trending is not; the
  live-count line is drawn as the home script draws it, from the same deals.json facts. The
  drop timer stays home: the wall's script runs it, and it is hidden since 2026-10-22.
- styles: the home rules that can reach the header, in the home's order, after the page's own;
  the page's own rules stop at the header. Neither side's rules reach the other's elements, so
  the header matches the home's and the page's deals look exactly as before.
- search: Enter opens the home page with the typed words (?analyze=, the entry point the event
  pages already use), where the home's own search runs as if Enter were pressed there: same
  results, events and Amazon tag. The tool buttons carry the typed words like the home's do.
Other hub-template pages (category, store, price, deal pages) keep their header. Off changes
no byte; the home page and deals.json are only read.
"""
import json
import re

from ..util import parse_iso
from . import best_today_tab, phone_tagline_count, search_focus_ring
from .template_slots import replace_slot

PAGE = 'best-deals-today/index.html'
HEADER = re.compile(r'<header>.*?</header>\n', re.S)
INSIDE, OUTSIDE = ':where(header,header *)', ':where(:not(header,header *))'
_TOKEN = re.compile(r'([.#])(-?[_a-zA-Z][-\w]*)')
_GROUP = re.compile(r':(not|has|is|where)\(')
_ROOT = re.compile(r'(?:html|body|:root)(?![-\w])[^\s>+~]*')
_TYPE = re.compile(r'(?<![-\w.#:])header(?![-\w])')
_PSEUDO_ELEMENT = re.compile(r'(?:::?(?:before|after|first-line|first-letter)|::[-\w]+(?:\([^)]*\))?)(?::[-\w]+)*$')
# The home script's renderMeta sets the same placeholder and label.
SCRIPT = '''<script>
(function () {
  var q = document.getElementById("q"), clear = document.getElementById("clearsearch");
  var phone = matchMedia("(max-width:899px)");
  function hint() { q.placeholder = phone.matches ? "Search deals on Amazon" : "Search deals or any Amazon product"; }
  function tools() {
    document.querySelectorAll("[data-tool-link]").forEach(function (a) {
      a.href = "/" + a.dataset.toolLink + "/" + (q.value.trim() ? "?q=" + encodeURIComponent(q.value) : "");
    });
  }
  function reset() { q.value = ""; clear.hidden = true; tools(); q.focus(); }
  q.setAttribute("aria-label", "Search deals or any Amazon product");
  hint();
  phone.addEventListener("change", hint);
  q.addEventListener("input", function () { clear.hidden = !q.value; tools(); });
  q.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { e.preventDefault(); reset(); }
    if (e.key !== "Enter" || e.isComposing) return;
    e.preventDefault();
    var words = Array.from(q.value).slice(0, 200).join("").trim();
    if (words) location.assign("/?" + new URLSearchParams({analyze: words}));
  });
  clear.addEventListener("click", reset);
})();
</script>
'''


def enabled(cfg):
    # The copy needs the home passes it copies: header_polish puts the tabs inside <header>,
    # tools_merge names the tool links, page_polish handles /?analyze= (Codex review 10-24).
    # Without any of them the page keeps its own header instead of failing or misrouting.
    return getattr(cfg, 'best_today_header', False) is True and all(
        getattr(cfg, name, False) is True for name in ('header_polish', 'tools_merge', 'page_polish'))


def _end(text, i):
    """Index after the bracket or quote opened at text[i]."""
    pairs = {'(': ')', '[': ']', '{': '}'}
    if text[i] in '"\'':
        j = text.find(text[i], i + 1)
        return len(text) if j < 0 else j + 1
    depth, j = 0, i
    while j < len(text):
        if text[j] in '"\'':
            j = _end(text, j)
            continue
        depth += (text[j] == text[i]) - (text[j] == pairs[text[i]])
        j += 1
        if not depth:
            return j
    return len(text)


def _split(text, sep):
    """text cut at sep outside quotes and brackets."""
    parts, start, i = [], 0, 0
    while i < len(text):
        if text[i] in '"\'([':
            i = _end(text, i)
            continue
        if text[i] == sep:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    return parts + [text[start:]]


def _rules(css):
    """A stylesheet as (prelude, body) pairs; @media/@supports bodies are nested lists."""
    css = re.sub(r'/\*.*?\*/|<!--|-->', '', css, flags=re.S)
    out, i = [], 0
    while (j := css.find('{', i)) >= 0:
        prelude, end = _split(css[i:j], ';')[-1].strip(), _end(css, j)
        body = css[j + 1:end - 1]
        out.append((prelude, _rules(body) if re.match(r'@(media|supports|container|layer)\b', prelude) else body))
        i = end
    return out


def _serialize(rules, keep, at_rules=True):
    """The rules again, each selector through keep(selector, declarations); None drops it."""
    out = []
    for prelude, body in rules:
        if isinstance(body, list):
            inner = _serialize(body, keep, at_rules)
            out.append(prelude + '{' + inner + '}' if inner else '')
        elif prelude.startswith('@'):
            out.append(prelude + '{' + body + '}' if at_rules else '')
        else:
            kept = [s for s in (keep(s.strip(), body) for s in _split(prelude, ',')) if s]
            out.append(','.join(kept) + '{' + body + '}' if kept else '')
    return ''.join(out)


def _tokens(selector, names):
    """The classes and ids an element needs to match: none under :not() or :has(), or in [attr];
    under :is() or :where(), those of the first alternative names can match."""
    while m := _GROUP.search(selector):
        end = _end(selector, m.end() - 1)
        alternatives = _split(selector[m.end():end - 1], ',') if m[1] in ('is', 'where') else ['']
        fit = next((a for a in alternatives if not _tokens(a, names) - names), alternatives[0])
        selector = selector[:m.start()] + fit + selector[end:]
    return set(_TOKEN.findall(re.sub(r'\[[^\]]*\]', '', selector)))


def _names(html):
    names = {('#', n) for n in re.findall(r'\sid="([^"]+)"', html)}
    return names | {('.', c) for v in re.findall(r'\sclass="([^"]+)"', html) for c in v.split()}


def _scoped(selector, scope):
    """selector, matching only elements scope matches (no added specificity)."""
    at = _PSEUDO_ELEMENT.search(selector)
    at = at.start() if at else len(selector)
    return selector[:at] + scope + selector[at:]


def _properties(decls):
    return [d.split(':', 1)[0].strip() for d in _split(decls, ';') if ':' in d]


def head_css(home, out):
    """The home page's head styles, inline and linked, in document order."""
    css = []
    for m in re.finditer(r'<style[^>]*>(.*?)</style>|<link rel="stylesheet" href="/([^"?]+)[^"]*">',
                         home[:home.index('</head>')], re.S):
        css.append(m[1] if m[1] is not None else (out / m[2]).read_text(encoding='utf-8'))
    return '\n'.join(css)


def header_rules(css, header, page):
    """The home rules that can style the header (page = the rest of this page's body).

    Root variables come back first, to go before the page's own (equal) ones; the root's other
    declarations land on the header at zero specificity, where inherited values would."""
    inside, own, rules = _names(header), _names(header) - _names(page), _rules(css)

    def variables(selector, decls):
        return _ROOT.fullmatch(selector) and all(
            p.startswith('--') or p == 'color-scheme' for p in _properties(decls))

    def keep(selector, decls):
        tokens = _tokens(selector, inside)
        if _ROOT.fullmatch(selector):
            return None if variables(selector, decls) else ':where(' + selector + ') :where(header)'
        if tokens - inside:
            return None
        if tokens & own or _TYPE.search(selector):
            return selector
        return _scoped(selector, INSIDE)  # generic, or shared with the page (Get deal)
    return (_serialize(rules, lambda s, d: s if variables(s, d) else None, at_rules=False),
            _serialize(rules, keep))


def page_rules(css, header, page):
    """The page's own rules, kept off the header; and a reset of what its root would pass down."""
    inside, own, inherited = _names(header), _names(header) - _names(page), []

    def keep(selector, decls):
        tokens = _tokens(selector, inside)
        if _ROOT.fullmatch(selector):
            inherited.extend(p for p in _properties(decls) if not p.startswith('--') and p != 'color-scheme')
            return selector
        if tokens & own or _TYPE.search(selector):
            return None
        return selector if tokens - inside else _scoped(selector, OUTSIDE)
    rules = _serialize(_rules(css), keep)
    return rules, ':where(header){' + ';'.join(p + ':initial' for p in dict.fromkeys(inherited)) + '}'


def _live(out, now):
    """The home script's live count: public offers not ended, plus price-only spotlights."""
    doc = json.loads((out / 'deals.json').read_text(encoding='utf-8'))
    return (sum(d.get('status') != 'dead' for d in doc['deals'])
            + sum(d.get('pct_at_checkout') is None and (until := parse_iso((d.get('spotlight') or {}).get('until')))
                  is not None and until > now for d in doc.get('spotlight', [])))


def tagline(live, short):
    """The home tagline as its script draws it (rough counts from 100, "1,000+" at most)."""
    n = str(live) if live < 100 else '1,000+' if live >= 1000 else f'{live // 100 * 100:,}+'
    plural = '' if live == 1 else 's'
    count = f'{n} live deal{plural}' if live else 'Offers checked before publication'
    dot = '<span class="live-dot" aria-hidden="true"></span>'
    since = '<span id="tagsince"></span>'
    if not short:
        return f'<span id="tagline" class="tag">verified deals · {dot}{count}{since}</span>'
    full = ' class="tag-full tag-lead"' if live else ''
    return (f'<span id="tagline" class="tag"><span{full}>verified deals · </span>{dot}'
            f'<span{full.replace("lead", "count")}>{count}</span>'
            + (f'<span class="tag-short">{n} verified deal{plural}</span>' if live else '') + since + '</span>')


def header(home, cfg, live):
    """The finished home header, for this page."""
    html = HEADER.search(home)[0]

    def swap(old, new):
        nonlocal html
        html = replace_slot(html, old, new, template_name='index.html', slot_name='best-today-header')

    swap('<h1 class="brand"><a href="./"', '<p class="brand"><a href="/"')
    swap('<span class="word">wow<em>priced</em></span></a></h1>', '<span class="word">wow<em>priced</em></span></a></p>')
    swap('<a class="cattab active" href="/" data-i="0">', '<a class="cattab" href="/" data-i="0">')
    if best_today_tab.enabled(cfg):
        swap(best_today_tab.PILL, '<a class="cattab active" aria-current="page">Best today</a>')
    swap('<span id="tagline" class="tag">verified deals</span>', tagline(live, phone_tagline_count.enabled(cfg)))
    drop = re.search(r'\n *<div id="drop"[^>]*>.*?</div>', html, re.S)
    if drop is None or '<div' in drop[0][drop[0].index('>'):]:
        raise ValueError('index.html: drop timer markup changed (best-today-header)')
    return html.replace(drop[0], '', 1)


def publish(out, cfg, now):
    if not enabled(cfg):
        return
    path = out / PAGE
    home, html = (out / 'index.html').read_text(encoding='utf-8'), path.read_text(encoding='utf-8')
    old, new = HEADER.search(html)[0], header(home, cfg, _live(out, now))
    page = html[html.index('<body'):].replace(old, '')
    own = re.search(r'<style>(.*?)</style>', html, re.S)  # the hub template's
    rules, reset = page_rules(own[1], new, page)
    early, late = header_rules(head_css(home, out), new, page)
    # Phones hang the tab row in the header's bottom margin: the heading keeps its own below it.
    html = (html[:own.start()] + '<style>' + early + reset + rules + 'header+.wrap{display:flow-root}</style>'
            + html[own.end():])
    html = replace_slot(html, old, new, template_name='hub.html', slot_name='best-today-header')
    extra = '<style id="home-header">' + late + '</style>'
    if search_focus_ring.SNIPPET in home:  # what that pass gives every page with a search box
        extra += search_focus_ring.SNIPPET
    html = html.replace('</head>', extra + '</head>', 1)
    loader = re.search(r'<script defer src="/join-loader\.js\?v=[0-9a-f]+"></script>', home)
    html = html.replace('</body>', SCRIPT + (loader[0] if loader else '') + '</body>', 1)
    path.write_text(html, encoding='utf-8')
