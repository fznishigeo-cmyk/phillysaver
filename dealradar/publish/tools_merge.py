"""Operator 2026-10-23 ("CONFIRMED GO AHEAD, please use those reference videos"; "push it
through when its done"): four tools become two.

Fake Deal Spotter takes Fakespot's home bands (trusted-guide tabs, "Love" band, footer
columns) and its report layout (blue analyzer box with today's deals, product header
with the big grade box, titled rows, a Today's top deals sidebar, "Was this analysis
helpful?"). Review Checker keeps ReviewMeta's look and gains one Price check row.

/amazon-deal-checker/ and /amazon-discount-finder/ stay real, indexable pages (operator
~01:55Z: organic search), each with its own title, description, headline, intro and
FAQ, but the tool on them is the Fake Deal Spotter itself: same box, same grade, same
report. Menus and the homepage's two buttons name Fake Deal Spotter and Review Checker.

Typed words (not a link) never call /api/search: one box with the Discount Finder's
Amazon discounts link, Amazon's highly rated list and our matching live deals.
No new Keepa spend: the review page's price grade comes from the lookup it already makes.
No public past-price wording (operator 2026-10-17): the grade may use the hidden history
signal, but pages describe it by today's price, the list price and our verified deals.
tools_merge false/missing = previous bytes.
"""

import hashlib
import re
from html import escape
from pathlib import Path

from .template_slots import replace_slot

TEMPLATES = Path(__file__).with_name("templates")
SPOTTER_CSS = (TEMPLATES / "tools-merge.css").read_text()
REVIEW_CSS = (TEMPLATES / "tools-merge-review.css").read_text()
OLD_TOOLS = ("/amazon-deal-checker/", "/amazon-discount-finder/")

# Keyword landing pages: unique copy per page; the tool below it is the spotter.
LANDING = {
    "amazon-deal-checker": {
        "name": "Amazon Deal Checker",
        "title": "Amazon Deal Checker – Is This Price a Good Deal? | wowpriced",
        "description": ("Free Amazon deal checker: paste a product link to see today's price with shipping, "
                        "Amazon's list price and a simple A, B or C price grade. No sign-up."),
        "h1": "Amazon Deal Checker",
        "sub": ("Is this Amazon price a good deal? Paste a product link for today’s price and a simple A, B or C grade."
                "<br>Free on your phone and computer. No extension needed."),
        "button": "Check price",
        "intro": ("How to check an Amazon deal before you buy", (
            "A big “was” price can make any discount look exciting. The Amazon Deal Checker looks past it. Paste a "
            "product link and it checks the price on Amazon today, including shipping when it is charged, against "
            "Amazon’s list price when Amazon shows one and against our verified deals.",
            "You get a simple grade for the price: A for a great price, B for a good price and C for a fair price. "
            "The report shows the plain facts behind it: today’s price, Amazon’s list price as information, and "
            "whether wowpriced has a verified deal for the same product, with its price checked against its source.",
            "The grade describes the price, not the product. It does not read reviews; for that, our free Review "
            "Checker looks at a product’s review signals. Prices on Amazon can change at any time, so confirm the "
            "price on Amazon before you buy.",
            "No link yet? Type the product name instead. We open Amazon’s current discounts and its highly rated "
            "list for those words, and show any matching verified deals on wowpriced.",
            "The Amazon Deal Checker is free on your phone and computer, with no extension and no sign-up."),
            ("Look at the price you pay today, not only the “was” price.",
             "Count shipping in the price you compare.",
             "Check the price on Amazon again right before you buy.")),
        "faq": (
            ("Is the Amazon Deal Checker free?", "Yes. It is free on your phone and computer, with no extension and no sign-up."),
            ("How does the deal checker grade a price?",
             "It looks at today’s Amazon price, including shipping when it is charged, Amazon’s list price when it "
             "shows one, and our published verified deals. A means a great price, B a good price and C a fair price. "
             "When price information is limited, the grade is C."),
            ("Why not just go by the percent off?",
             "A percent off is measured against a list or “was” price, so a big percent off alone does not earn an A. "
             "The grade also weighs today’s price against our verified deals."),
            ("Can I check a product without a link?",
             "Yes. Type the product name to open Amazon’s discounts and highly rated list for those words. For an A, "
             "B or C grade, paste the product’s Amazon link."),
            ("Is the Amazon Deal Checker part of Amazon?",
             "No. It is made by wowpriced and is independent of Amazon. As an Amazon Associate, wowpriced earns from "
             "qualifying purchases. Our links may earn a commission at no extra cost to you.")),
    },
    "amazon-discount-finder": {
        "name": "Amazon Discount Finder",
        "title": "Amazon Discount Finder – Find Discounts for Any Product | wowpriced",
        "description": ("Free Amazon discount finder: type what you want to open Amazon's current discounts and see "
                        "matching verified deals, or paste a product link for a price grade."),
        "h1": "Amazon Discount Finder",
        "sub": ("Type what you are shopping for to see Amazon’s discounts and our matching verified deals."
                "<br>Paste a product link for a price grade. Free, no sign-up."),
        "button": "Find discounts",
        "intro": ("How to find real discounts on Amazon", (
            "Type what you are shopping for, like “running shoes” or “air fryer”. The Amazon Discount Finder opens "
            "Amazon’s current discounts for those words and Amazon’s highly rated list for the same words. It also "
            "shows matching deals published on wowpriced, where every price was checked against its source.",
            "Typed words never start a price lookup, so the buttons appear right away. When you find a product you "
            "like, paste its Amazon link here for a price check: today’s price with shipping, Amazon’s list price "
            "when it shows one, and a simple A, B or C grade.",
            "A big percent off is not always a great price. That is why our grade also checks today’s price against "
            "our verified deals, not only against the list price.",
            "Discounts on Amazon depend on what is available today and can end at any time. Always confirm the "
            "final price on Amazon before you buy.",
            "The Amazon Discount Finder is free on your phone and computer, with no extension and no sign-up."),
            ("Start with two or three words, like a product type and a brand.",
             "Open Amazon’s discounts, then paste the link of the product you like.",
             "Look for a B or better before you buy.")),
        "faq": (
            ("Is the Amazon Discount Finder free?", "Yes. It is free to use in your browser, with no extension and no sign-up."),
            ("How do I find discounts for a product?",
             "Type the product you want, like “wireless earbuds”, and tap Find discounts. We open Amazon’s discount "
             "results for those words and show matching verified deals from wowpriced."),
            ("Are the discounts real?",
             "Amazon shows its own current discounts for your words. Deals published on wowpriced had their price "
             "checked against the source before they went live. Prices can change, so confirm the price on Amazon "
             "before you buy."),
            ("What if I paste an Amazon link instead?",
             "You get a price check for that product: today’s price with shipping, Amazon’s list price when it "
             "shows one, and an A, B or C price grade."),
            ("Does wowpriced earn money from these links?",
             "Yes. As an Amazon Associate, wowpriced earns from qualifying purchases. Our links may earn a commission "
             "at no extra cost to you.")),
    },
}

# The two homepage buttons keep their place and look (operator 2026-10-20 layout):
# the blue one (phone: first) opens Fake Deal Spotter, the purple one Review Checker.
HOME_LINKS = (('href="/amazon-discount-finder/" data-tool-link="discount-finder"',
               'href="/deal-spotter/" data-tool-link="deal-spotter"'),
              ('href="/amazon-deal-checker/" data-tool-link="deal-checker"',
               'href="/review-checker/" data-tool-link="review-checker"'))
HOME_LABELS = (('>Discount Finder<span class="tool-arrow"', '>Fake Deal Spotter<span class="tool-arrow"'),
               ('>Deal Checker<span class="tool-arrow"', '>Review Checker<span class="tool-arrow"'))
HOME_HREF = '    return "/amazon-" + tool + "/" + (raw.trim() ? "?q=" + encodeURIComponent(raw) : "");\n'
HOME_TRACK = '      track(link.dataset.toolLink === "deal-checker" ? "search_analyze" : "search_discounts", {to_tool:true});\n'


def enabled(cfg):
    # Two tools need both tools: with either one off the four keep their old pages and links.
    from . import deal_spotter
    return getattr(cfg, "tools_merge", False) is True and deal_spotter.enabled(cfg) and deal_spotter.reviews_on(cfg)


def landing_on(cfg):
    return enabled(cfg) and getattr(cfg, "tool_pages", {}).get("enabled") is True


def keep(url):
    return url not in OLD_TOOLS


def site_version(version, cfg):
    if not enabled(cfg):
        return version
    return hashlib.sha256(version.encode() + Path(__file__).read_bytes()).hexdigest()[:12]


# ---- Fake Deal Spotter -------------------------------------------------------------

def _section(template, start, end, name):
    """The one block from start up to (not including) end."""
    match = re.search(re.escape(start) + r'.*?(?=' + re.escape(end) + ')', template, re.S)
    if not match or template.count(start) != 1:
        raise ValueError("deal-spotter.html: missing slot " + name)
    return match[0]


def spotter_template(base, landing=None):
    """The spotter page with Fakespot's order and bands, the report and the analyzer box."""
    story = {name: _section(base, start, end, name) for name, start, end in (
        ("how", '<section class="how-section', '<section class="demo-section'),
        ("demo", '<section class="demo-section', '{{STORES}}'),
        ("guide", '<section class="guide-section', '<section class="safe-section'),
        ("safe", '<section class="safe-section', '<section class="love-section'),
        ("love", '<section class="love-section', '<section class="stats-band'))}
    old = ''.join(story[k] for k in ("how", "demo")) + '{{STORES}}\n' + ''.join(story[k] for k in ("guide", "safe", "love"))
    love = replace_slot(story["love"], '<div class="install-space" id="pwa-actions" hidden>',
                        '<a class="primary love-cta" href="/best-deals-today/">See today\'s best deals <span aria-hidden="true">→</span></a>'
                        '<div class="install-space" id="pwa-actions" hidden>', template_name="deal-spotter.html", slot_name="love-cta")
    # Fakespot's order: How it works, stores, the dark video band, Love, the grey guide, A safe feeling.
    new = (story["how"] + '{{STORES}}\n' + story["demo"] + love
           + (TEMPLATES / "tools-merge-guide.html").read_text() + story["safe"])
    base = replace_slot(base, old, new, template_name="deal-spotter.html", slot_name="story-order")
    result = _section(base, '<section id="spotter-result"', '<div id="spotter-story">', "result")
    base = replace_slot(base, result, (TEMPLATES / "tools-merge-report.html").read_text(),
                        template_name="deal-spotter.html", slot_name="report")
    modal = _section(base, '<dialog id="analyze-modal"', '{{INSTALL_HELP}}', "modal")
    base = replace_slot(base, modal, (TEMPLATES / "tools-merge-modal.html").read_text(),
                        template_name="deal-spotter.html", slot_name="analyzer")
    if landing:
        base = replace_slot(base, '<h1>Spot great deals<br>before you buy<span class="blue">.</span></h1>\n'
                            '<p class="hero-sub">Works with any Amazon.com product link or product name.<br>'
                            'Free on your phone and computer. No extension needed.</p>',
                            '<h1>' + escape(landing["h1"]) + '</h1>\n<p class="hero-sub">' + landing["sub"] + '</p>',
                            template_name="deal-spotter.html", slot_name="landing-hero")
        base = replace_slot(base, '<button class="primary" type="submit">Analyze<span>– it\'s free</span></button>',
                            '<button class="primary" type="submit">' + escape(landing["button"]) + '<span>– it\'s free</span></button>',
                            template_name="deal-spotter.html", slot_name="landing-button")
        base = replace_slot(base, '<div id="spotter-story">\n', '<div id="spotter-story">\n' + intro(landing),
                            template_name="deal-spotter.html", slot_name="landing-intro")
    return base


def intro(landing):
    heading, paragraphs, tips = landing["intro"]
    return ('<section class="landing-intro wrap" aria-labelledby="landing-title"><h2 id="landing-title">'
            + escape(heading) + '</h2>' + ''.join('<p>' + escape(p) + '</p>' for p in paragraphs)
            + '<h3>Three quick checks</h3><ul>' + ''.join('<li>' + escape(t) + '</li>' for t in tips) + '</ul></section>\n')


def picks(rows, limit):
    from .deal_spotter import picks as spotter_picks
    return spotter_picks(rows, limit)


def _deal_link(d):
    return ('href="' + escape(d["url"], quote=True) + '" target="_blank" rel="sponsored nofollow noopener"')


def _photo(d, size):
    return ('<img src="' + escape(d["image_url"], quote=True) + '" alt="" width="' + str(size) + '" height="'
            + str(size) + '" loading="lazy" decoding="async" referrerpolicy="no-referrer">')


def _steps(d):
    # As the spotter's own cards: a coupon/code/Subscribe & Save price links to its how-to page.
    return f'<a class="deal-steps" href="{escape(d["deal_path"])}">How to get this price</a>' if d.get("needs_steps") else ''


def sidebar(rows):
    """Today's top deals in the site's pick order: photo, name, price, Get deal (our tagged link)."""
    cards = ''.join('<article class="side-deal">' + _photo(d, 72) + '<div><h3>' + escape(d["item"]) + '</h3>'
                    f'<p><strong>${d["final_price"]:.2f}</strong> <span>Verified deal</span></p>' + _steps(d)
                    + '<a ' + _deal_link(d) + '>Get deal <span aria-hidden="true">↗</span></a></div></article>'
                    for d, _ in picks(rows, 5))
    return cards or '<p class="side-empty">See today’s verified deals on <a href="/">wowpriced</a>.</p>'


def recommends(rows):
    """Fakespot's "Recommends" strip in the analyzer box: today's deals, chip "Today's deal"."""
    cards = ''.join('<article class="rec-card"><span class="rec-chip">TODAY’S DEAL</span>' + _photo(d, 64)
                    + '<div><h3>' + escape(d["item"]) + f'</h3><p><strong>${d["final_price"]:.2f}</strong></p>'
                    + _steps(d) + '<a ' + _deal_link(d) + '>Get deal <span aria-hidden="true">↗</span></a></div></article>'
                    for d, _ in picks(rows, 3))
    return ('<div class="recommends" aria-labelledby="recommends-title"><h2 id="recommends-title">'
            '<img src="/deal-spotter-mark.svg" alt="" width="20" height="20"> wowpriced recommends</h2>'
            '<div class="rec-cards">' + cards + '</div></div>') if cards else ''


def review_row(on):
    return ('<section class="report-row" aria-labelledby="row-review-title"><div class="row-title"><h2 id="row-review-title">'
            'Review signals</h2><p>From our Review Checker</p></div><div class="row-content"><p>This grade is about the '
            'price. To see how the product’s ratings and reviews have built up, check its review signals.</p>'
            '<a id="review-link" class="secondary">Check the review signals <span aria-hidden="true">→</span></a></div></section>'
            ) if on else ''


def spotter_tokens(rows, reviews_on):
    return {"SIDEBAR": sidebar(rows), "RECOMMENDS": recommends(rows), "REVIEW_ROW": review_row(reviews_on)}


def words_script(source, target):
    """Shared by both tools: the checker's own discount link, inventory and word matching."""
    from .deal_spotter import shared_functions
    block = source[:source.index("function monthlyBoughtLabel(")]
    names = ("toolDiscount", "toolVerified", "toolInventory", "toolLink") + (() if 'function esc(' in target else ("esc",))
    stub = '' if 'function discountsLink()' in target else 'function discountsLink() { return null; }\n'
    return '\n'.join([block, *shared_functions(source, names), 'var toolInventoryPromise;', stub,
                      (TEMPLATES / "tools-merge-words.js").read_text()])


def name_panel(script, name):
    # The typed-words box (tools-merge-words.js) keeps the highly rated button as its second exit.
    return replace_slot(script, 'function namePanel(panel, raw) {', 'function namePanelRated(panel, raw) {',
                        template_name=name, slot_name="name-panel")


SPOTTER_JS = (
    ("  document.addEventListener('click', event => {\n"
     "    var a = event.target.closest && event.target.closest('#start a[href], #spotter-result a[href]');\n",
     "  document.addEventListener('click', event => {\n"
     "    var a = event.target.closest && event.target.closest('#start a[href], #spotter-result a[href], #analyze-modal a[href]');\n",
     "deal-click"),
    ("    $('result-facts').innerHTML = facts.map(fact => '<li>' + esc(fact) + '</li>').join('');\n",
     "    $('result-facts').innerHTML = facts.map(fact => '<li>' + esc(fact) + '</li>').join('');\n"
     "    mergeReport(item, grade);\n", "report"),
    # A landing page keeps its own address when the result or a share link is made.
    ("    var shareURL = new URL('/deal-spotter/', location.origin);", "    var shareURL = new URL(location.pathname, location.origin);",
     "share-url"),
    ("    var url = new URL('/deal-spotter/', location.origin);", "    var url = new URL(location.pathname, location.origin);",
     "share-button"),
    ("    else submit(initial, $('q'));\n  }\n",
     "    else submit(initial, $('q'));\n  } else mergeQuery(params, submit);\n", "query"),
)


def spotter_script(script, cfg, source):
    for old, new, slot in SPOTTER_JS:
        script = replace_slot(script, old, new, template_name="deal-spotter.js", slot_name=slot)
    # The phone/computer carousel left with the old guide band; the tabs replace it.
    carousel = _section(script, "  var slide = 0, names = [", "  window.addEventListener('pagehide'", "carousel")
    script = replace_slot(script, carousel, "  mergeGuide();\n", template_name="deal-spotter.js", slot_name="carousel")
    script = name_panel(script, "deal-spotter.js")
    return script + '\n' + words_script(source, script) + '\n' + (TEMPLATES / "tools-merge.js").read_text()


def spotter_footer(cfg, year, disclosure, amazon_store, social, nav):
    """Fakespot's footer: columns, a rule, the search links, then the centred mark and fine print."""
    from .site import linked_disclosure
    groups = (("wowpriced", (("/about.html", "About"), ("/how-it-works.html", "How we verify"),
                             ("/deal-spotter/#how-it-works", "How to use Fake Deal Spotter"), ("/careers.html", "Careers"))),
              ("Tools", [link for url in ("/deal-spotter/", "/review-checker/", "/") for link in nav if link[0] == url]
               + [("/best-deals-today/", "Today's best deals")]),
              ("Support", (("/deal-spotter/#faq", "FAQ"), ("/contact.html", "Contact"),
                           ("/subscribe.html", "Terms"), ("/privacy.html", "Privacy"))))
    columns = ''.join('<div><h3>' + title + '</h3>' + ''.join(
        f'<a href="{url}">{escape(label)}</a>' for url, label in links) + '</div>' for title, links in groups)
    explore = [("/deal-spotter/", "Grade Amazon deals"), ("/amazon-deal-checker/", "Amazon deal checker"),
               ("/amazon-discount-finder/", "Amazon discount finder")]
    if any(url == "/review-checker/" for url, _ in nav):
        explore.append(("/review-checker/", "Check Amazon reviews"))
    if amazon_store:
        explore.append(("/stores/amazon/", "Today’s Amazon deals"))
    if not landing_on(cfg):
        explore = [link for link in explore if keep(link[0])]
    return ('<footer class="spotter-footer merged-footer"><div class="wrap"><nav class="footer-columns" aria-label="Footer">'
            + columns + '<div><h3>Social</h3>' + ('<div class="social-icons">' + social + '</div>' if social else '')
            + '<a href="/fakespot-alternative/">A fresh way to check prices</a></div></nav>'
            '<nav class="seo-links" aria-label="Explore">' + ''.join(f'<a href="{url}">{escape(label)}</a>' for url, label in explore)
            + '</nav><div class="footer-mark"><a class="spotter-brand" href="/"><img src="/deal-spotter-mark.svg" alt="" '
            'width="34" height="34">wowpriced</a><p class="footer-fine"><a href="/subscribe.html">Terms of use</a>'
            '<span aria-hidden="true">•</span><a href="/privacy.html">Privacy notice</a></p>'
            '<p class="footer-copy">© ' + str(year) + ' wowpriced</p></div>'
            '<p class="disclosure">' + linked_disclosure(disclosure) + '</p></div></footer>')


# ---- Review Checker -----------------------------------------------------------------

REVIEW_ABOUT = ('Make it part of your shopping routine. Use {{SPOTTER_ABOUT}}the <a href="/amazon-deal-checker/">Deal Checker</a> '
                'to explore today’s price and verified deals.')
REVIEW_ABOUT_NEW = ('Make it part of your shopping routine. Use <a href="/deal-spotter/">Fake Deal Spotter</a> to check '
                    'today’s price and our verified deals.')
REVIEW_TILE = ('<a href="/amazon-deal-checker/"><span class="tile-art" aria-hidden="true">$<i>✓</i></span>'
               '<span>Deal Checker →</span></a>\n')
REVIEW_JS = (
    ("    if (toolSettings.spotter) actions.append(link('Is the price right? → Fake Deal Spotter', '/deal-spotter/?asin=' + asin, 'secondary'));\n",
     "", "spotter-button"),
    ("    var actions = element('div', undefined, 'report-actions');\n",
     "    mergePriceRow(report, data, asin);\n    var actions = element('div', undefined, 'report-actions');\n", "price-row"),
    ("    else {message.textContent = 'Paste an Amazon.com product link above, or type a product name.'; input.focus();}\n  }\n",
     "    else {message.textContent = 'Paste an Amazon.com product link above, or type a product name.'; input.focus();}\n"
     "  } else {\n    var typed = params.get('q');\n"
     "    if (typed && typed.trim() && typed.length <= 300) {input.value = heroInput.value = typed; submit(typed.trim(), undefined, heroInput);}\n  }\n",
     "query"),
)


def review_template(base):
    base = replace_slot(base, REVIEW_ABOUT, REVIEW_ABOUT_NEW, template_name="review-checker.html", slot_name="about")
    return replace_slot(base, REVIEW_TILE, '', template_name="review-checker.html", slot_name="tile")


def review_script(script, source):
    for old, new, slot in REVIEW_JS:
        script = replace_slot(script, old, new, template_name="review-checker.js", slot_name=slot)
    script = name_panel(script, "review-checker.js")
    return script + '\n' + words_script(source, script) + '\n' + (TEMPLATES / "tools-merge-review.js").read_text()


# ---- Site-wide ----------------------------------------------------------------------

def home_buttons(html):
    """Same place, size and look; Fake Deal Spotter and Review Checker, carrying the typed words."""
    for old, new in HOME_LINKS + HOME_LABELS:
        html = html.replace(old, new)
    html = html.replace(HOME_HREF, HOME_HREF.replace('"/amazon-" + tool + "/"', '"/" + tool + "/"'))
    return html.replace(HOME_TRACK, '      track(link.dataset.toolLink === "review-checker" ? "search_analyze" : "search_discounts",'
                                    ' {to_tool:true, tool:link.dataset.toolLink});\n')


def tracking(dispatch):
    dispatch = dispatch.replace("  var EVENTS = {", "  var EVENTS = {\n"
                                '    tool_feedback: { ga4Only: true, methods: ["helpful", "not_helpful"] },', 1)
    return dispatch.replace('window.gtag("event", name, {to_tool:true, send_to:on.ga4});',
                            'window.gtag("event", name, {to_tool:true, tool:["deal-spotter", "review-checker"].indexOf(params.tool) !== -1'
                            ' ? params.tool : "", send_to:on.ga4});', 1)


def publish(out, cfg):
    """The homepage's two buttons (and the event pages' copies) name the two tools."""
    if not enabled(cfg):
        return
    for path in out.rglob('*.html'):
        html = path.read_text()
        # Only the operator's outside-the-bar pair (tool_buttons_outside) is renamed; inside the
        # bar the buttons keep their own labels and open the keyword pages, which still work.
        if 'data-tool-link=' in html and '<div class="tool-buttons">' in html:
            new = home_buttons(html)
            if new != html:
                path.write_text(new)
