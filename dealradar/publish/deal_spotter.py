"""Deal Spotter: opt-in pages and discovery, using only the public projection."""

import hashlib
import json
import re
import shutil
from collections import Counter
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from ..config import social_profiles
from ..savings import tool_meter
from ..util import parse_iso
from . import add_to_cart, amazon_as_of, seo, tool_pages, tools_merge, tracking

TEMPLATES = Path(__file__).with_name("templates")
SLUGS = ("deal-spotter", "fakespot-alternative")
ASSETS = ("deal-spotter.css", "deal-spotter.js", "deal-spotter-install.js", "deal-spotter-mark.svg")
NAV = (("/", "Today's Deals"), ("/amazon-deal-checker/", "Deal Checker"),
       ("/amazon-discount-finder/", "Discount Finder"), ("/deal-spotter/", "Fake Deal Spotter"),
       ("/review-checker/", "Review Checker"), ("/about.html", "About"), ("/deal-spotter/#faq", "FAQ"))
FAQ = (
    ("Is it free?", "Yes. This tool is free to use."),
    ("Do I need an extension?", "No. Fake Deal Spotter works in your browser on your phone or computer. No extension or sign-up needed."),
    ("What do the grades mean?", "A means Great price, B means Good price, and C means Fair price. A grade describes the price, not the quality of the product. When price information is limited, the grade is C."),
    ("Where do prices come from?", "We use Amazon's current product price, its list price when available, and our published verified deals. Prices can change; confirm the price on Amazon before buying."),
    ("Is Fake Deal Spotter part of Amazon?", "No. Fake Deal Spotter by wowpriced is independent of Amazon. As an Amazon Associate, wowpriced earns from qualifying purchases. Our links may earn a commission at no extra cost to you."),
)


def enabled(cfg):
    # script() reuses the checker's helpers (toolSimilar comes from buttons_to_tools);
    # without them there is no Deal Spotter to publish.
    from . import buttons_to_tools
    return getattr(cfg, "deal_spotter", {}).get("enabled") is True and buttons_to_tools.enabled(cfg)


def reviews_on(cfg):
    return getattr(cfg, "review_checker", {}).get("enabled") is True


def nav(cfg, links=NAV):
    # Review Checker ships separately; never link to it while it is OFF. With tools_merge
    # the menus name the two tools only (the old pages are keyword landing pages).
    return [(url, label) for url, label in links if (url != "/review-checker/" or reviews_on(cfg))
            and (tools_merge.keep(url) or not tools_merge.enabled(cfg))]


def menu(cfg):
    links = nav(cfg)
    return ('<details class="spotter-menu"><summary>Menu <span aria-hidden="true">☰</span></summary>'
            '<nav aria-label="Site menu">' + ''.join(f'<a href="{url}">{escape(label)}</a>' for url, label in links)
            + '</nav></details>')


# Tool pages keep their approved desktop links (plus Deal Spotter); phones get the Menu.
TOOL_PAGE_CSS = ('.tool-header .spotter-menu{display:none}@media(max-width:960px){'
                 '.tool-header nav[aria-label="Tools"]{display:none}.tool-header .spotter-menu{display:block}}')


EXCLUDE = 'var analyzeExclude = toolSettings.exclude ? new RegExp(toolSettings.exclude, "iu") : null;'


def shared_functions(source, names):
    # The checker's own emitted implementations, so both tools stay in step with it.
    functions = []
    for name in names:
        match = re.search(r"^function " + name + r"\([^\n]*\) \{[^\n]+\}", source, re.M)
        if not match:
            match = re.search(r"^function " + name + r"\([^\n]*\) \{\n[\s\S]*?^\}|"
                              r"^  function " + name + r"\([^\n]*\) \{\n[\s\S]*?^  \}", source, re.M)
        if not match:
            raise ValueError("Deal Spotter shared helper missing: " + name)
        functions.append(match[0])
    return functions


def script(cfg):
    # Extract the same implementations the checker emits: fixes, tag, adult
    # exclusions and configured Amazon price cap travel with these functions.
    source = tool_pages.script(cfg)
    functions = shared_functions(source, ("analyzeInput", "analyzeReason", "analyzeAdult", "analyzeItems", "toolAmazon",
                                          "toolQuery", "searchAmazonQuery", "toolSimilar", "monthlyBoughtLabel", "esc"))
    settings = re.search(r"var toolSettings = .*?;", source)[0]
    # toolSimilar receives an Amazon input, so its title path is the checker path.
    glue = ('var toolActive = null; function searchText() { return document.getElementById("q").value.trim(); }\n'
            'function discountsLink() { return null; }\n')
    result = '\n'.join([settings, EXCLUDE, *functions, glue, (TEMPLATES / "tool-name-panel.js").read_text(),
                        (TEMPLATES / "deal-spotter.js").read_text()])
    if tools_merge.enabled(cfg):
        result = tools_merge.spotter_script(result, cfg, source)
    return add_to_cart.spotter_script(result, cfg)


def live_rows(doc, now, single_source_ok=False):
    # Every published deal passed the price checks; "badge" only says how many
    # sources agreed (index.html: "No badge = single source"), so it is not a filter.
    # single_source_ok (operator 2026-10-22, A30: "1 source is fine.. if its a
    # legitimate source") also keeps deals one source reported; our own Amazon
    # price checks below still apply.
    rows = list(doc.get("deals", [])) + [d for d in doc.get("spotlight", [])
            if (until := parse_iso((d.get("spotlight") or {}).get("until"))) and until > now]
    seen, result = set(), []
    for d in rows:
        key = d.get("key")
        if (not key or key in seen or d.get("locked") or d.get("status") != "alive"
                or any(d.get(k) for k in ("price_unconfirmed", "price_aged", "click_price_unknown"))
                or (not single_source_ok and "not yet confirmed" in d.get("headline", ""))):
            continue
        seen.add(key)
        result.append(d)
    return result


def stores_row(rows, limit=5):
    """Where Fakespot listed press logos: the stores today's deals really come from (plain names)."""
    counts = Counter(d["store"].strip() for d in rows if isinstance(d.get("store"), str) and d["store"].strip())
    names = [name for name, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))[:limit]]
    if len(names) < 2:
        return ''
    return ('<section class="stores wrap" aria-label="Stores in today\'s deals"><p>Today\'s deals come from</p><div>'
            + ''.join('<span>' + escape(name) + '</span>' for name in names) + '</div></section>')


PRICES = "deal-spotter-prices.json"
PRICE_FIELDS = ("status", "final_price", "url", "headline")


def price_index(doc, single_source_ok=False, deal_path=False):
    """The few public fields /api/search?grade=deal reads, for Amazon rows only.

    The Pages Function has a 10 ms CPU budget on the free plan; parsing all of
    deals.json per request could exceed it. functions/api/deal-grade.js applies
    the same checks again to whatever it reads here.
    """
    def slim(d):
        row = {k: d[k] for k in PRICE_FIELDS + (("deal_path",) if deal_path else ()) if k in d}
        if d.get("spotlight"):
            row["spotlight"] = {"until": (d.get("spotlight") or {}).get("until")}
        return row

    def amazon(d):
        host = urlsplit(str(d.get("url", ""))).hostname or ""
        return (not d.get("locked") and d.get("status") == "alive"
                and not any(d.get(k) for k in ("price_unconfirmed", "price_aged", "click_price_unknown"))
                and (single_source_ok or "not yet confirmed" not in d.get("headline", ""))
                and re.fullmatch(r"(?:(?:www|smile|m)\.)?amazon\.com", host) is not None)
    index = {"deals": [slim(d) for d in doc.get("deals", []) if amazon(d)],
             "spotlight": [slim(d) for d in doc.get("spotlight", []) if amazon(d)]}
    if single_source_ok:
        index["single_source_ok"] = True  # deal-grade.js then skips its own one-source check
    return index


def picks(rows, limit=4):
    """Amazon list-price deals with a photo, in the site's pick order, with their list-price badge."""
    selected = []
    for d in rows:
        url = d.get("url", "")
        # The projection already contains the publisher's affiliate/variant link.
        if (urlsplit(url).hostname not in {"amazon.com", "www.amazon.com", "smile.amazon.com", "m.amazon.com"}
                or not url.startswith("https://") or not d.get("image_url")
                or d.get("regular_price_basis") != "List price"):
            continue
        price, regular = d.get("final_price"), d.get("regular_price")
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        badge = tool_meter(price, regular)
        if not badge["hasList"]:
            continue
        if d.get("needs_steps") and not re.fullmatch(r"/deal/[a-z0-9-]+/", d.get("deal_path", "")):
            continue
        selected.append((d, badge))
        if len(selected) == limit:
            break
    return selected


def cards(rows):
    selected = []
    for d, badge in picks(rows):
        url, price = d["url"], d["final_price"]
        steps = f'<a class="deal-steps" href="{escape(d["deal_path"])}">How to get this price</a>' if d.get("needs_steps") else ''
        selected.append('<article class="spotter-deal"><div class="deal-photo"><img src="'
            + escape(d["image_url"], quote=True) + '" alt="' + escape(d["item"], quote=True)
            + '" width="260" height="220" loading="lazy" decoding="async" referrerpolicy="no-referrer"></div>'
            + f'<div class="deal-copy"><span class="deal-off">{badge["pct"]}% off list price</span>'
            + '<h3>' + escape(d["item"]) + f'</h3><p class="deal-price">${price:.2f}</p>' + steps
            + '<a href="' + escape(url, quote=True)
            + '" target="_blank" rel="sponsored nofollow noopener">Get deal <span aria-hidden="true">↗</span></a></div></article>')
    return ''.join(selected)


# Brand marks from Simple Icons 13.21.0 (CC0), drawn in the footer ink color.
SOCIAL_ICONS = (
    ("facebook", "Facebook", "M9.101 23.691v-7.98H6.627v-3.667h2.474v-1.58c0-4.085 1.848-5.978 5.858-5.978.401 0 .955.042 1.468.103a8.68 8.68 0 0 1 1.141.195v3.325a8.623 8.623 0 0 0-.653-.036 26.805 26.805 0 0 0-.733-.009c-.707 0-1.259.096-1.675.309a1.686 1.686 0 0 0-.679.622c-.258.42-.374.995-.374 1.752v1.297h3.919l-.386 2.103-.287 1.564h-3.246v8.245C19.396 23.238 24 18.179 24 12.044c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.628 3.874 10.35 9.101 11.647Z"),
    ("instagram", "Instagram", "M7.0301.084c-1.2768.0602-2.1487.264-2.911.5634-.7888.3075-1.4575.72-2.1228 1.3877-.6652.6677-1.075 1.3368-1.3802 2.127-.2954.7638-.4956 1.6365-.552 2.914-.0564 1.2775-.0689 1.6882-.0626 4.947.0062 3.2586.0206 3.6671.0825 4.9473.061 1.2765.264 2.1482.5635 2.9107.308.7889.72 1.4573 1.388 2.1228.6679.6655 1.3365 1.0743 2.1285 1.38.7632.295 1.6361.4961 2.9134.552 1.2773.056 1.6884.069 4.9462.0627 3.2578-.0062 3.668-.0207 4.9478-.0814 1.28-.0607 2.147-.2652 2.9098-.5633.7889-.3086 1.4578-.72 2.1228-1.3881.665-.6682 1.0745-1.3378 1.3795-2.1284.2957-.7632.4966-1.636.552-2.9124.056-1.2809.0692-1.6898.063-4.948-.0063-3.2583-.021-3.6668-.0817-4.9465-.0607-1.2797-.264-2.1487-.5633-2.9117-.3084-.7889-.72-1.4568-1.3876-2.1228C21.2982 1.33 20.628.9208 19.8378.6165 19.074.321 18.2017.1197 16.9244.0645 15.6471.0093 15.236-.005 11.977.0014 8.718.0076 8.31.0215 7.0301.0839m.1402 21.6932c-1.17-.0509-1.8053-.2453-2.2287-.408-.5606-.216-.96-.4771-1.3819-.895-.422-.4178-.6811-.8186-.9-1.378-.1644-.4234-.3624-1.058-.4171-2.228-.0595-1.2645-.072-1.6442-.079-4.848-.007-3.2037.0053-3.583.0607-4.848.05-1.169.2456-1.805.408-2.2282.216-.5613.4762-.96.895-1.3816.4188-.4217.8184-.6814 1.3783-.9003.423-.1651 1.0575-.3614 2.227-.4171 1.2655-.06 1.6447-.072 4.848-.079 3.2033-.007 3.5835.005 4.8495.0608 1.169.0508 1.8053.2445 2.228.408.5608.216.96.4754 1.3816.895.4217.4194.6816.8176.9005 1.3787.1653.4217.3617 1.056.4169 2.2263.0602 1.2655.0739 1.645.0796 4.848.0058 3.203-.0055 3.5834-.061 4.848-.051 1.17-.245 1.8055-.408 2.2294-.216.5604-.4763.96-.8954 1.3814-.419.4215-.8181.6811-1.3783.9-.4224.1649-1.0577.3617-2.2262.4174-1.2656.0595-1.6448.072-4.8493.079-3.2045.007-3.5825-.006-4.848-.0608M16.953 5.5864A1.44 1.44 0 1 0 18.39 4.144a1.44 1.44 0 0 0-1.437 1.4424M5.8385 12.012c.0067 3.4032 2.7706 6.1557 6.173 6.1493 3.4026-.0065 6.157-2.7701 6.1506-6.1733-.0065-3.4032-2.771-6.1565-6.174-6.1498-3.403.0067-6.156 2.771-6.1496 6.1738M8 12.0077a4 4 0 1 1 4.008 3.9921A3.9996 3.9996 0 0 1 8 12.0077"),
    ("pinterest", "Pinterest", "M12.017 0C5.396 0 .029 5.367.029 11.987c0 5.079 3.158 9.417 7.618 11.162-.105-.949-.199-2.403.041-3.439.219-.937 1.406-5.957 1.406-5.957s-.359-.72-.359-1.781c0-1.663.967-2.911 2.168-2.911 1.024 0 1.518.769 1.518 1.688 0 1.029-.653 2.567-.992 3.992-.285 1.193.6 2.165 1.775 2.165 2.128 0 3.768-2.245 3.768-5.487 0-2.861-2.063-4.869-5.008-4.869-3.41 0-5.409 2.562-5.409 5.199 0 1.033.394 2.143.889 2.741.099.12.112.225.085.345-.09.375-.293 1.199-.334 1.363-.053.225-.172.271-.401.165-1.495-.69-2.433-2.878-2.433-4.646 0-3.776 2.748-7.252 7.92-7.252 4.158 0 7.392 2.967 7.392 6.923 0 4.135-2.607 7.462-6.233 7.462-1.214 0-2.354-.629-2.758-1.379l-.749 2.848c-.269 1.045-1.004 2.352-1.498 3.146 1.123.345 2.306.535 3.55.535 6.607 0 11.985-5.365 11.985-11.987C23.97 5.39 18.592.026 11.985.026L12.017 0z"),
)


def social_links(cfg):
    # Operator 2026-10-22: an icon appears only when its profile URL is configured.
    profiles = social_profiles(cfg.social_profiles)
    return ''.join(f'<a class="social-{name}" href="{escape(profiles[name], quote=True)}" rel="noopener" '
                   f'aria-label="{label}"><svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true" '
                   f'focusable="false"><path fill="currentColor" d="{path}"/></svg></a>'
                   for name, label, path in SOCIAL_ICONS if name in profiles)


def footer(cfg, year, disclosure, amazon_store):
    from .site import linked_disclosure
    if tools_merge.enabled(cfg):
        return tools_merge.spotter_footer(cfg, year, disclosure, amazon_store, social_links(cfg), nav(cfg))
    # Site-wide trust links (tests/test_established_look.py): About, How we verify, Contact,
    # Privacy and Disclosure (the disclosure line below links the last one).
    groups = (("wowpriced", (("/about.html", "About"), ("/how-it-works.html", "How we verify"),
                ("/deal-spotter/#how-it-works", "How to use Fake Deal Spotter"), ("/careers.html", "Careers"))),
              ("Shop smarter", nav(cfg, NAV[:5])),
              ("Here to help", (("/deal-spotter/#faq", "FAQ"), ("/contact.html", "Contact"),
                ("/subscribe.html", "Terms"), ("/privacy.html", "Privacy"))))
    content = ''.join('<div><h3>' + title + '</h3>' + ''.join(
        f'<a href="{url}">{escape(label)}</a>' for url, label in links) + '</div>' for title, links in groups)
    social = social_links(cfg)
    return ('<footer class="spotter-footer"><div class="wrap"><nav class="footer-columns" aria-label="Footer">'
            + content + '<div><h3>Stay connected</h3>' + ('<div class="social-icons">' + social + '</div>' if social else '')
            + '<a href="/fakespot-alternative/">A fresh way to check prices</a></div></nav>'
            '<nav class="seo-links" aria-label="Explore"><a href="/deal-spotter/">Grade Amazon deals</a>'
            + ('<a href="/review-checker/">Check Amazon reviews</a>' if reviews_on(cfg) else '')
            # The Amazon store page exists only with enough live Amazon deals (seo._store_pages).
            + ('<a href="/stores/amazon/">Today’s Amazon deals</a>' if amazon_store else '') + '</nav>'
            '<div class="footer-bottom"><a class="spotter-brand" href="/"><img src="/deal-spotter-mark.svg" '
            'alt="" width="34" height="34">wowpriced</a><span>© ' + str(year) + ' wowpriced</span></div>'
            '<p class="disclosure">' + linked_disclosure(disclosure) + '</p></div></footer>')


def discovery(out, cfg):
    links = ' · <a href="/deal-spotter/">Fake Deal Spotter</a> · <a href="/fakespot-alternative/">Fakespot alternative</a>'
    for path in out.rglob("*.html"):
        html = path.read_text()
        updated = re.sub(r'(<nav\b[^>]*\bid="footerlinks"[^>]*>)([\s\S]*?)(</nav>)',
                         lambda m: m[0] if 'href="/deal-spotter/"' in m[2] else m[1] + m[2] + links + m[3], html, count=1)
        if path.parent.name in tool_pages.PAGES and 'class="spotter-menu"' not in updated:
            tools = lambda m: (m[1] + m[2] + ('' if 'href="/deal-spotter/"' in m[2] else
                               '<a href="/deal-spotter/">Fake Deal Spotter</a>') + m[3] + menu(cfg))
            updated = re.sub(r'(<nav aria-label="Tools">)(.*?)(</nav>)', tools, updated, count=1, flags=re.S)
            updated = updated.replace('</head>', '<style>' + (TEMPLATES / 'deal-spotter-menu.css').read_text()
                                      + TOOL_PAGE_CSS + '</style></head>', 1)
        if updated != html:
            path.write_text(updated)


def publish(out, cfg, now, site_url, disclosure):
    for slug in SLUGS:
        shutil.rmtree(out / slug, ignore_errors=True)
    for name in (*ASSETS, PRICES):
        (out / name).unlink(missing_ok=True)
    if not enabled(cfg):
        return []
    merged = tools_merge.enabled(cfg)
    # Extend the same configuration read by /api/search. No independent limits.
    config_path = out / "search-config.json"
    if config_path.exists():
        settings = json.loads(config_path.read_text())
        settings["deal_spotter"] = cfg.deal_spotter
        if merged:
            settings["tools_merge"] = True  # search.js: 90-day comparison, review page's price grade
        config_path.write_text(json.dumps(settings, separators=(",", ":")))
    doc = json.loads((out / "deals.json").read_text())
    single = cfg.deal_spotter.get("single_source_ok") is True
    (out / PRICES).write_text(json.dumps(price_index(doc, single, merged), separators=(",", ":")))
    rows = live_rows(doc, now, single)
    versions = {}
    for name in ASSETS:
        content = (script(cfg) if name == "deal-spotter.js" else
                   (TEMPLATES / "pwa-install.js").read_text().replace('phoneDelay = 180000',
                       f'phoneDelay = {cfg.pwa_install_phone_delay_s * 1000}', 1)
                   if name == "deal-spotter-install.js" else (TEMPLATES / name).read_text())
        if name == 'deal-spotter.css':
            content += (TEMPLATES / 'deal-spotter-menu.css').read_text() + (tools_merge.SPOTTER_CSS if merged else '')
        (out / name).write_text(content)
        versions[name] = hashlib.sha256(content.encode()).hexdigest()[:12]
    amazon_store = (out / "stores" / "amazon" / "index.html").exists()
    # tools_merge: the Deal Checker and Discount Finder addresses are keyword landing pages
    # with their own copy; the tool on them is this one (operator 2026-10-23 ~01:55Z).
    landing = tools_merge.LANDING if tools_merge.landing_on(cfg) else {}
    for slug in SLUGS + tuple(landing):
        page = landing.get(slug)
        faq = page["faq"] if page else tools_merge.spotter_faq(FAQ) if merged else FAQ
        questions = ''.join('<details><summary>' + escape(q) + '</summary><p>' + escape(a) + '</p></details>' for q, a in faq)
        faq_schema = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq]}
        title = (page["title"] if page else "Fake Deal Spotter by wowpriced | Free Amazon Deal Grader" if slug == "deal-spotter" else
                 "Fakespot Alternative: Free Amazon Deal Grader | wowpriced")
        description = (escape(page["description"]) if page else
                       "Paste any Amazon link and get a deal grade in seconds. Free, no extension needed.")
        url = site_url + '/' + slug + '/'
        head = ('<link rel="canonical" href="' + escape(url) + '"><meta property="og:type" content="website">'
                '<meta property="og:url" content="' + escape(url) + '"><meta property="og:title" content="'
                + escape(title) + '"><meta property="og:description" content="' + description + '">'
                '<meta name="twitter:card" content="summary">')
        if slug == 'deal-spotter' or page:
            head += seo._json_ld(faq_schema) + seo._json_ld({"@context": "https://schema.org", "@type": "WebApplication",
                "name": (page["name"] if page else "Fake Deal Spotter") + " by wowpriced", "url": url,
                "applicationCategory": "ShoppingApplication",
                "operatingSystem": "Any", "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"}})
        if page and not cfg.tool_pages.get("public"):
            head += '<meta name="robots" content="noindex,nofollow">'
        install = ('<link rel="manifest" href="/manifest.webmanifest"><script defer src="/deal-spotter-install.js?v='
                   + versions['deal-spotter-install.js'] + '"></script>') if cfg.pwa_enabled else ''
        template = (TEMPLATES / (('deal-spotter' if page else slug) + '.html')).read_text()
        if merged and slug != 'fakespot-alternative':
            template = tools_merge.spotter_template(template, page)
        html = seo._fill(template, TITLE=escape(title), DESCRIPTION=description,
            HEAD=head + install, MENU=menu(cfg), FOOTER=footer(cfg, now.year, disclosure, amazon_store), FAQ=questions,
            CSS_VERSION=versions['deal-spotter.css'], JS_VERSION=versions['deal-spotter.js'],
            REVIEW_LINK=('<a id="review-link" class="review-link">Check the reviews →</a>' if reviews_on(cfg) else ''),
            REVIEW_NOTE=('<p>It does not read reviews. For reviews, visit our <a href="/review-checker/">Review Checker</a>.</p>'
                         if reviews_on(cfg) else '<p>It does not read reviews.</p>'),
            STORE_LINK=('<a class="text-link" href="/stores/amazon/">Explore today\'s Amazon deals →</a>' if amazon_store
                        else '<a class="text-link" href="/">Explore today\'s deals →</a>'),
            STORES=stores_row(rows), DEAL_COUNT=f'{len(rows):,}', STORE_COUNT=str(len({d["store"].casefold() for d in rows if d.get("store")})),
            TOP_DEALS=cards(rows), DISCLAIMER=amazon_as_of.DISCLAIMER,
            INSTALL_HELP=(TEMPLATES / 'pwa-install-help.html').read_text() if cfg.pwa_enabled else '',
            **(tools_merge.spotter_tokens(rows, reviews_on(cfg)) if merged else {}))
        if slug == 'deal-spotter' or page:
            html = add_to_cart.spotter_page(html, cfg)
        seo._write_page(out, '/' + slug + '/', tracking.inject(html, tracking.head_snippet(cfg)))
    discovery(out, cfg)
    return ['/' + slug + '/' for slug in SLUGS]
