"""Task 328: isolated preview tools; public discovery is a separate switch."""

import hashlib
import json
import re
import shutil
from html import escape
from pathlib import Path

from ..savings import TOOL_LIST_BADGE_JS, TOOL_FACT_BADGE_JS
from . import search_analyze, search_boost, seo, tracking, tool_hero, tool_pages_1020, buttons_to_tools

TEMPLATES = Path(__file__).with_name("templates")
PAGES = {
    "amazon-deal-checker": {
        "mode": "checker", "name": "Amazon Deal Checker",
        "title": "Amazon Deal Checker – Is This Price a Good Deal? | wowpriced",
        "intro": "Is this Amazon price a good deal? Paste a link or type a product — free, no sign-up.",
        "button": "Analyze",
        "steps": ["Found the product", "Checked today's Amazon price", "Compared with our verified deals",
                  "Finding top sellers", "Your verdict"],
    },
    "amazon-discount-finder": {
        "mode": "finder", "name": "Amazon Discount Finder",
        "title": "Amazon Discount Finder – Every Discount for Any Product | wowpriced",
        "intro": "Find Amazon discounts for any product. Paste a link or type what you want — free.",
        "button": "Find discounts",
        "steps": ["Found the product", "Searched Amazon discounts", "Matching our verified deals", "Ready"],
    },
}


def faq(mode):
    return [
        ("Is it free?", "Yes. Both tools are free to use."),
        ("Do I need an extension or sign-up?", "No. Paste an Amazon link or type a product here in your browser."),
        ("Where do prices come from?",
         "The Deal Checker checks the live Amazon price in seconds and compares it with Amazon's current list price and our verified deals. Prices can change; confirm at Amazon before buying."
         if mode == "checker" else
         "Our deal cards use published, verified prices. Amazon shows current prices when you open its results. The Discount Finder does not run a live price check."),
        ("How are discounts found?",
         "We compare the current Amazon price with Amazon's current list price when provided, and match the product with our verified deals."
         if mode == "checker" else
         "We use your product words to open Amazon's discount results and match our verified deals. A link without a product name opens that product on Amazon. Discounts depend on availability."),
        ("Does wowpriced earn money?",
         "As an Amazon Associate, wowpriced earns from qualifying purchases. Our links may earn a commission at no extra cost to you."),
    ]


def _function(source, name):
    match = re.search(r"  (?:async )?function " + name + r"\([^\n]*\) \{\n[\s\S]*?\n  \}", source)
    if not match:
        raise ValueError("tool page shared function missing: " + name)
    return match[0] + "\n"


def script(cfg):
    # Reuse the shipped input checks, sanitizer, matcher, monthly labels,
    # link-word derivation and honest fallback copy without editing their callers.
    home = (TEMPLATES / "index.html").read_text()
    analyze = search_analyze.SCRIPT.read_text()
    shared = (TEMPLATES / "search-products.js").read_text().replace("export ", "")
    shared = shared.replace("var CATGROUPS = [];", "var CATGROUPS = " + json.dumps(seo.CATGROUPS) + ";")
    shared += "\n" + (TOOL_FACT_BADGE_JS if tool_pages_1020.enabled(cfg) else TOOL_LIST_BADGE_JS)
    shared += _function(home, "searchAmazonQuery")
    shared = shared.replace('function searchAmazonQuery() {', 'function searchAmazonQuery(value) {')
    shared = shared.replace('var q = searchText().replace',
                            'var q = (value === undefined ? searchText() : value.trim()).replace')
    for name in ("analyzeInput", "analyzeReason", "analyzeDealAsin", "analyzeCompare", "analyzeItems"):
        shared += _function(analyze, name).replace("keepa_error:", "price_error:")
    shared += (TEMPLATES / "discounts-from-link.js").read_text()
    exclude = search_boost.adult_pattern(cfg.search_boost["exclude_words"])
    shared += "\nvar toolSettings = " + json.dumps({"tag": cfg.affiliate_amazon_tag, "exclude": exclude,
        "live": search_analyze.enabled(cfg)}, separators=(",", ":")).replace("<", "\\u003c") + ";\n"
    result = shared + (TEMPLATES / "tool-pages.js").read_text()
    if tool_pages_1020.enabled(cfg):
        result = tool_pages_1020.script(result)
    elif tool_hero.enabled(cfg):
        result = tool_hero.script(result)
    return buttons_to_tools.tool_script(result, cfg)


def _tracking(cfg):
    snippet = tracking.head_snippet(cfg)
    # These events/fields apply only to the tool pages. Existing pages, including
    # their tracking bytes, stay identical in preview mode.
    snippet = snippet.replace('  var EVENTS = {', '  var EVENTS = {\n'
        '    page_view: {ga4Only:true},\n    analyze: {ga4Only:true},')
    snippet = snippet.replace('    var p = {};', '    var p = {};\n'
        '    if (name === "page_view") p.source = "tool_page";')
    snippet = snippet.replace('  if (internal) ga4Config.traffic_type = "internal";',
        '  if (internal) ga4Config.traffic_type = "internal";\n  ga4Config.send_page_view = false;', 1)
    return snippet


def indexnow_exclusions(cfg, site_url):
    settings = getattr(cfg, "tool_pages", {})
    return set() if settings.get("enabled") and settings.get("public") else {
        site_url + "/" + slug + "/" for slug in PAGES}


def footer_links(out):
    links = ' · '.join(f'<a href="/{slug}/">{page["name"]}</a>' for slug, page in PAGES.items())
    for path in out.rglob("*.html"):
        html = path.read_text()
        updated = re.sub(r'(<nav\b[^>]*\bid="footerlinks"[^>]*>)([\s\S]*?)(</nav>)',
                         lambda m: m[1] + m[2] + ' · ' + links + m[3], html, count=1)
        if updated != html:
            path.write_text(updated)


def publish(out, cfg, now, site_url, disclosure, rows=()):
    from .site import linked_disclosure

    settings = getattr(cfg, "tool_pages", {})
    # Clean stale previews when switched off or republishing into the same folder.
    for slug in PAGES:
        if (out / slug).is_dir():
            shutil.rmtree(out / slug)
    for name in ("tool-pages.js", "tool-pages.css"):
        (out / name).unlink(missing_ok=True)
    if not settings.get("enabled"):
        return []
    css, js = (TEMPLATES / "tool-pages.css").read_text(), script(cfg)
    if tool_pages_1020.enabled(cfg) or tool_hero.enabled(cfg):
        css += (TEMPLATES / ("tool-hero-1020.css" if tool_pages_1020.enabled(cfg) else "tool-hero.css")).read_text()
        rows = tool_hero.candidates(rows, cfg)
    assets = {}
    for suffix, content in (("css", css), ("js", js)):
        (out / ("tool-pages." + suffix)).write_text(content)
        assets[suffix] = hashlib.sha256(content.encode()).hexdigest()[:12]
    base = (TEMPLATES / "tool-page.html").read_text()
    hub = (TEMPLATES / "hub.html").read_text()
    brand = re.search(r'<p class="brand">.*?</p>', hub)[0]
    favicon = re.search(r'<link rel="icon"[^>]+>', hub)[0]
    for slug, page in PAGES.items():
        if tool_pages_1020.enabled(cfg):
            page = tool_pages_1020.page_spec(page)
        url = site_url + "/" + slug + "/"
        questions = faq(page["mode"])
        if tool_pages_1020.enabled(cfg):
            questions.append(("Is it really AI?", tool_pages_1020.ai_answer(page["mode"])))
        schemas = [{"@context": "https://schema.org", "@type": "WebApplication", "name": page["name"],
                    "url": url, "applicationCategory": "ShoppingApplication", "operatingSystem": "Any",
                    "description": page["intro"], "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"}},
                   {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
                       {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
                       for q, a in questions]}]
        head = ('<link rel="canonical" href="' + escape(url) + '">\n'
                + '<meta property="og:type" content="website">\n'
                + '<meta property="og:url" content="' + escape(url) + '">\n'
                + '<meta name="twitter:card" content="summary">\n')
        for key, attr in (("og", "property"), ("twitter", "name")):
            for name, content in (("title", page["title"]), ("description", page["intro"])):
                head += f'<meta {attr}="{key}:{name}" content="{escape(content)}">\n'
        head += ''.join(seo._json_ld(schema) for schema in schemas)
        if not settings.get("public"):
            head += '<meta name="robots" content="noindex,nofollow">\n'
        nav = ''.join(f'<a href="/{other}/"' + (' aria-current="page"' if other == slug else '')
                      + '>' + p["name"].replace("Amazon ", "") + '</a>' for other, p in PAGES.items())
        template = (tool_pages_1020.template(base, page, rows) if tool_pages_1020.enabled(cfg) else
                    tool_hero.template(base, page, rows) if tool_hero.enabled(cfg) else base)
        html = seo._fill(template, TITLE=escape(page["title"]), NAME=page["name"], INTRO=escape(page["intro"]),
            HEAD=head, BRAND=brand, FAVICON=favicon, NAV=nav, MODE=page["mode"], BUTTON=page["button"],
            CSS_VERSION=assets["css"], JS_VERSION=assets["js"],
            STEPS=''.join('<li data-state="pending">' + escape(label) + '</li>' for label in page["steps"]),
            FAQ=''.join('<h3>' + escape(q) + '</h3>' + (tool_pages_1020.ai_html(page["mode"])
                if tool_pages_1020.enabled(cfg) and q == "Is it really AI?" else '<p>' + escape(a) + '</p>') for q, a in questions),
            YEAR=str(now.year), DISCLOSURE=linked_disclosure(disclosure))
        seo._write_page(out, "/" + slug + "/", tracking.inject(html, _tracking(cfg)))
    if settings.get("public"):
        footer_links(out)
        return ["/" + slug + "/" for slug in PAGES]
    return []
