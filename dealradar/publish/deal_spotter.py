"""Deal Spotter: opt-in pages and discovery, using only the public projection."""

import hashlib
import json
import re
import shutil
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from ..config import social_profiles
from ..savings import tool_meter
from ..util import parse_iso
from . import amazon_as_of, seo, tool_pages, tracking

TEMPLATES = Path(__file__).with_name("templates")
SLUGS = ("deal-spotter", "fakespot-alternative")
ASSETS = ("deal-spotter.css", "deal-spotter.js", "deal-spotter-install.js", "deal-spotter-mark.svg")
NAV = (("/", "Today's Deals"), ("/amazon-deal-checker/", "Deal Checker"),
       ("/amazon-discount-finder/", "Discount Finder"), ("/deal-spotter/", "Deal Spotter"),
       ("/review-checker/", "Review Checker"), ("/about.html", "About"), ("/deal-spotter/#faq", "FAQ"))
FAQ = (
    ("Is it free?", "Yes. This tool is free to use."),
    ("Do I need an extension?", "No. Deal Spotter works in your browser on your phone or computer. No extension or sign-up needed."),
    ("What do the grades mean?", "A means Great price, B means Good price, and C means Fair price. A grade describes the price, not the quality of the product. When price information is limited, the grade is C."),
    ("Where do prices come from?", "We use Amazon's current product price, its list price when available, and our published verified deals. Prices can change; confirm the price on Amazon before buying."),
    ("Is Deal Spotter part of Amazon?", "No. Deal Spotter by wowpriced is independent of Amazon. As an Amazon Associate, wowpriced earns from qualifying purchases. Our links may earn a commission at no extra cost to you."),
)


def enabled(cfg):
    return getattr(cfg, "deal_spotter", {}).get("enabled") is True


def menu(cfg):
    # Review Checker ships separately; never link to it while it is OFF.
    links = [(url, label) for url, label in NAV
             if url != "/review-checker/" or getattr(cfg, "review_checker", {}).get("enabled") is True]
    return ('<details class="spotter-menu"><summary>Menu <span aria-hidden="true">☰</span></summary>'
            '<nav aria-label="Site menu">' + ''.join(f'<a href="{url}">{escape(label)}</a>' for url, label in links)
            + '</nav></details>')


# Tool pages keep their approved desktop links (plus Deal Spotter); phones get the Menu.
TOOL_PAGE_CSS = ('.tool-header .spotter-menu{display:none}@media(max-width:960px){'
                 '.tool-header nav[aria-label="Tools"]{display:none}.tool-header .spotter-menu{display:block}}')


def script(cfg):
    # Extract the same implementations the checker emits: fixes, tag, adult
    # exclusions and configured Amazon price cap travel with these functions.
    source = tool_pages.script(cfg)
    names = ("analyzeInput", "analyzeReason", "analyzeAdult", "analyzeItems", "toolAmazon",
             "toolQuery", "searchAmazonQuery", "toolSimilar", "monthlyBoughtLabel", "esc")
    functions = []
    for name in names:
        match = re.search(r"^function " + name + r"\([^\n]*\) \{[^\n]+\}", source, re.M)
        if not match:
            match = re.search(r"^function " + name + r"\([^\n]*\) \{\n[\s\S]*?^\}|"
                              r"^  function " + name + r"\([^\n]*\) \{\n[\s\S]*?^  \}", source, re.M)
        if not match:
            raise ValueError("Deal Spotter shared helper missing: " + name)
        functions.append(match[0])
    settings = re.search(r"var toolSettings = .*?;", source)[0]
    exclude = 'var analyzeExclude = toolSettings.exclude ? new RegExp(toolSettings.exclude, "iu") : null;'
    # toolSimilar receives an Amazon input, so its title path is the checker path.
    glue = ('var toolActive = null; function searchText() { return document.getElementById("q").value.trim(); }\n'
            'function discountsLink() { return null; }\n')
    return '\n'.join([settings, exclude, *functions, glue, (TEMPLATES / "deal-spotter.js").read_text()])


def live_rows(doc, now):
    rows = list(doc.get("deals", [])) + [d for d in doc.get("spotlight", [])
            if (until := parse_iso((d.get("spotlight") or {}).get("until"))) and until > now]
    seen, result = set(), []
    for d in rows:
        key = d.get("key")
        if (not key or key in seen or d.get("locked") or d.get("status") != "alive"
                or d.get("badge") not in {"verified", "history-checked"}
                or any(d.get(k) for k in ("price_unconfirmed", "price_aged", "click_price_unknown"))
                or "not yet confirmed" in d.get("headline", "")):
            continue
        seen.add(key)
        result.append(d)
    return result


def cards(rows):
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
        steps = ''
        if d.get("needs_steps"):
            if not re.fullmatch(r"/deal/[a-z0-9-]+/", d.get("deal_path", "")):
                continue
            steps = f'<a class="deal-steps" href="{escape(d["deal_path"])}">How to get this price</a>'
        selected.append('<article class="spotter-deal"><div class="deal-photo"><img src="'
            + escape(d["image_url"], quote=True) + '" alt="' + escape(d["item"], quote=True)
            + '" width="260" height="220" loading="lazy" decoding="async" referrerpolicy="no-referrer"></div>'
            + f'<div class="deal-copy"><span class="deal-off">{badge["pct"]}% off list price</span>'
            + '<h3>' + escape(d["item"]) + f'</h3><p class="deal-price">${price:.2f}</p>' + steps
            + '<a href="' + escape(url, quote=True)
            + '" target="_blank" rel="sponsored nofollow noopener">Get deal <span aria-hidden="true">↗</span></a></div></article>')
        if len(selected) == 4:
            break
    return ''.join(selected)


def footer(cfg, year, disclosure):
    from .site import linked_disclosure
    groups = (("wowpriced", (("/about.html", "About"), ("/deal-spotter/#how-it-works", "How to use Deal Spotter"),
                ("/careers.html", "Careers"))),
              ("Shop smarter", NAV[:5]),
              ("Here to help", (("/deal-spotter/#faq", "FAQ"), ("/contact.html", "Contact"),
                ("/subscribe.html", "Terms"), ("/privacy.html", "Privacy"))))
    content = ''.join('<div><h3>' + title + '</h3>' + ''.join(
        f'<a href="{url}">{escape(label)}</a>' for url, label in links) + '</div>' for title, links in groups)
    profiles = social_profiles(cfg.social_profiles)
    # Do not invent account URLs when the operator has not configured profiles.
    social = ''.join(f'<a href="{escape(profiles[name])}" aria-label="{name.title()}" rel="noopener">'
                     f'<span aria-hidden="true">{symbol}</span></a>' for name, symbol in
                     (("facebook", "f"), ("instagram", "◎"), ("pinterest", "p")) if name in profiles)
    if not social:
        social = ('<span role="img" aria-label="Facebook">f</span><span role="img" aria-label="Instagram">◎</span>'
                  '<span role="img" aria-label="Pinterest">p</span></div><div><a href="/contact.html">Connect with wowpriced →</a>')
    return ('<footer class="spotter-footer"><div class="wrap"><nav class="footer-columns" aria-label="Footer">'
            + content + '<div><h3>Stay connected</h3><div class="social-icons">' + social + '</div>'
            '<a href="/fakespot-alternative/">A fresh way to check prices</a></div></nav>'
            '<nav class="seo-links" aria-label="Explore"><a href="/deal-spotter/">Grade Amazon deals</a>'
            '<a href="/review-checker/">Check Amazon reviews</a><a href="/stores/amazon/">Today’s Amazon deals</a></nav>'
            '<div class="footer-bottom"><a class="spotter-brand" href="/"><img src="/deal-spotter-mark.svg" '
            'alt="" width="34" height="34">wowpriced</a><span>Copyright ' + str(year) + ' wowpriced</span></div>'
            '<p class="disclosure">' + linked_disclosure(disclosure) + '</p></div></footer>')


def discovery(out, cfg):
    links = ' · <a href="/deal-spotter/">Deal Spotter</a> · <a href="/fakespot-alternative/">Fakespot alternative</a>'
    for path in out.rglob("*.html"):
        html = path.read_text()
        updated = re.sub(r'(<nav\b[^>]*\bid="footerlinks"[^>]*>)([\s\S]*?)(</nav>)',
                         lambda m: m[0] if 'href="/deal-spotter/"' in m[2] else m[1] + m[2] + links + m[3], html, count=1)
        if path.parent.name in tool_pages.PAGES and 'class="spotter-menu"' not in updated:
            tools = lambda m: (m[1] + m[2] + ('' if 'href="/deal-spotter/"' in m[2] else
                               '<a href="/deal-spotter/">Deal Spotter</a>') + m[3] + menu(cfg))
            updated = re.sub(r'(<nav aria-label="Tools">)(.*?)(</nav>)', tools, updated, count=1, flags=re.S)
            updated = updated.replace('</head>', '<style>' + (TEMPLATES / 'deal-spotter-menu.css').read_text()
                                      + TOOL_PAGE_CSS + '</style></head>', 1)
        if updated != html:
            path.write_text(updated)


def publish(out, cfg, now, site_url, disclosure):
    for slug in SLUGS:
        shutil.rmtree(out / slug, ignore_errors=True)
    for name in ASSETS:
        (out / name).unlink(missing_ok=True)
    if not enabled(cfg):
        return []
    # Extend the same configuration read by /api/search. No independent limits.
    config_path = out / "search-config.json"
    if config_path.exists():
        settings = json.loads(config_path.read_text())
        settings["deal_spotter"] = cfg.deal_spotter
        config_path.write_text(json.dumps(settings, separators=(",", ":")))
    rows = live_rows(json.loads((out / "deals.json").read_text()), now)
    versions = {}
    for name in ASSETS:
        content = (script(cfg) if name == "deal-spotter.js" else
                   (TEMPLATES / "pwa-install.js").read_text().replace('phoneDelay = 180000',
                       f'phoneDelay = {cfg.pwa_install_phone_delay_s * 1000}', 1)
                   if name == "deal-spotter-install.js" else (TEMPLATES / name).read_text())
        if name == 'deal-spotter.css':
            content += (TEMPLATES / 'deal-spotter-menu.css').read_text()
        (out / name).write_text(content)
        versions[name] = hashlib.sha256(content.encode()).hexdigest()[:12]
    questions = ''.join('<details><summary>' + escape(q) + '</summary><p>' + escape(a) + '</p></details>' for q, a in FAQ)
    faq_schema = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in FAQ]}
    for slug in SLUGS:
        title = ("Deal Spotter by wowpriced | Free Amazon Deal Grader" if slug == "deal-spotter" else
                 "Fakespot Alternative: Free Amazon Deal Grader | wowpriced")
        description = "Paste any Amazon link and get a deal grade in seconds. Free, no extension needed."
        url = site_url + '/' + slug + '/'
        head = ('<link rel="canonical" href="' + escape(url) + '"><meta property="og:type" content="website">'
                '<meta property="og:url" content="' + escape(url) + '"><meta property="og:title" content="'
                + escape(title) + '"><meta property="og:description" content="' + description + '">'
                '<meta name="twitter:card" content="summary">')
        if slug == 'deal-spotter':
            head += seo._json_ld(faq_schema) + seo._json_ld({"@context": "https://schema.org", "@type": "WebApplication",
                "name": "Deal Spotter by wowpriced", "url": url, "applicationCategory": "ShoppingApplication",
                "operatingSystem": "Any", "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"}})
        install = ('<link rel="manifest" href="/manifest.webmanifest"><script defer src="/deal-spotter-install.js?v='
                   + versions['deal-spotter-install.js'] + '"></script>') if cfg.pwa_enabled else ''
        html = seo._fill((TEMPLATES / (slug + '.html')).read_text(), TITLE=escape(title), DESCRIPTION=description,
            HEAD=head + install, MENU=menu(cfg), FOOTER=footer(cfg, now.year, disclosure), FAQ=questions,
            CSS_VERSION=versions['deal-spotter.css'], JS_VERSION=versions['deal-spotter.js'],
            DEAL_COUNT=f'{len(rows):,}', STORE_COUNT=str(len({d["store"].casefold() for d in rows if d.get("store")})),
            TOP_DEALS=cards(rows), DISCLAIMER=amazon_as_of.DISCLAIMER,
            INSTALL_HELP=(TEMPLATES / 'pwa-install-help.html').read_text() if cfg.pwa_enabled else '')
        seo._write_page(out, '/' + slug + '/', tracking.inject(html, tracking.head_snippet(cfg)))
    discovery(out, cfg)
    return ['/' + slug + '/' for slug in SLUGS]
