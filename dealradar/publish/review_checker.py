"""Task 367: an isolated, reversible review-signal reader and public links."""
import hashlib
import json
import re
import shutil
from html import escape
from pathlib import Path

from ..config import validated_review_checker
from . import amazon_as_of, deal_spotter, search_boost, seo, shipping_note, tool_pages, tools_merge, tracking, words_analyzer

TEMPLATES = Path(__file__).with_name("templates")
PATHS = ("/review-checker/", "/reviewmeta-alternative/")
LINK = '<a href="/review-checker/">Review Checker</a>'
# The operator's provided wowpriced mark, kept inline; reference files stay untracked.
LOGO = ('<svg width="36" height="36" viewBox="0 0 32 32" aria-hidden="true">'
        '<rect x="1" y="1" width="30" height="30" rx="8" fill="#129486"/>'
        '<polyline points="6.5,11 11,22.5 16,14 21,22.5 25.5,12.5" fill="none" stroke="#fff" '
        'stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round"/>'
        '<circle cx="25.5" cy="6.3" r="2.4" fill="#ffc94d"/></svg>')
DISCLAIMER = ("Review Checker is independent of Amazon. We don't read or change reviews; "
              "we look at how a product's ratings and reviews have built up and show the checks it passes. "
              "Always read a few reviews before you buy.")
FAQ = [("Is it free?", "Yes. Review Checker is free on any device, with no sign-up needed."),
       ("Does it read reviews?", "No — it looks at rating and review signals over time. It does not read, remove or change reviews, or detect fake reviews."),
       ("What do A, B, C mean?", "A means a strong set of review signals. B means helpful signals to explore. C means okay — read a few reviews before you buy. Limited data can also lead to C. The grade is context for your shopping, not a verdict on product quality."),
       ("Is it part of Amazon?", "No. Review Checker is made by wowpriced and is independent of Amazon.")]
TOOL_MENU_CSS = """<style id="review-tool-menu-style">
.review-tools-menu{display:none;margin-left:auto;position:relative;z-index:20}
.review-tools-menu summary{cursor:pointer;min-height:44px;align-content:center;list-style:none}
.review-tools-menu summary:focus-visible{outline:3px solid var(--link);outline-offset:4px}
@media(max-width:960px){.tool-header .review-tools-desktop{display:none}
.review-tools-menu{display:block}.tool-header .review-tools-menu nav{position:absolute;right:0;top:50px;
background:var(--card);border:1px solid var(--line);border-radius:8px;min-width:220px;
box-shadow:0 8px 24px #0002;padding:10px;display:flex;flex-direction:column;align-items:stretch;gap:0}
.tool-header .review-tools-menu nav a{font-size:15px;min-height:44px;padding:8px 12px}}
</style>"""
# The desktop links row under the header, as on ReviewMeta (operator 2026-10-22).
NAV_ROW = [("/amazon-deal-checker/", "Deal Checker"), ("/amazon-discount-finder/", "Discount Finder"),
           ("/deal-spotter/", "Fake Deal Spotter"), ("/", "Today's Deals"), ("#how-it-works", "How it works"),
           ("#faq", "FAQ"), ("/about.html", "About")]
NAV = [("/", "Today's Deals"), ("/amazon-deal-checker/", "Deal Checker"),
       ("/amazon-discount-finder/", "Discount Finder"), ("/deal-spotter/", "Fake Deal Spotter"),
       ("/review-checker/", "Review Checker"), ("/about.html", "About"), ("/review-checker/#faq", "FAQ")]


def enabled(cfg):
    return getattr(cfg, "review_checker", {}).get("enabled") is True


def spotter_on(cfg):
    return getattr(cfg, "deal_spotter", {}).get("enabled") is True


def script(cfg):
    # Same Amazon URL validation and affiliate product-link builder as Deal Checker.
    shared = tool_pages._function((TEMPLATES / "search-analyze.js").read_text(), "analyzeInput")
    source = (TEMPLATES / "tool-pages.js").read_text()
    shared += re.search(r'function toolAmazon\(asin\) \{.*?\n\}', source, re.S)[0]
    # Same adult exclusion as Deal Checker; Deal Spotter links only while it is ON.
    settings = {"tag": cfg.affiliate_amazon_tag, "api": cfg.paywall_api_url,
                "disclaimer": amazon_as_of.DISCLAIMER, "spotter": spotter_on(cfg),
                "exclude": search_boost.adult_pattern(cfg.search_boost["exclude_words"])}
    # A typed name uses the checker's own word cleaning and adult filter (no lookup).
    source = tool_pages.script(cfg)
    names = deal_spotter.shared_functions(source, ("toolQuery", "searchAmazonQuery", "analyzeAdult"))
    script = ('"use strict";\nvar toolSettings=' + json.dumps(settings).replace("<", "\\u003c") + ';\n'
              + shared + '\n' + '\n'.join([deal_spotter.EXCLUDE, *names, (TEMPLATES / "tool-name-panel.js").read_text()])
              + (TEMPLATES / "review-checker.js").read_text())
    script = tools_merge.review_script(script, source) if tools_merge.enabled(cfg) else script
    script = words_analyzer.review_script(script, cfg)
    return shipping_note.review_script(script, cfg)


def _asin(row):
    url = row.get("url", "")
    match = re.search(r'^https://(?:(?:www|smile|m)\.)?amazon\.com/(?:[^?#]*/)?(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?#]|$)', url, re.I)
    return match[1].upper() if match else ""


def cards(rows):
    result = []
    for row in rows[:6]:
        asin = _asin(row)
        photo = row.get("image_url") or row.get("image") or ""
        if not photo.startswith("https://"):
            photo = ""
        title = escape(row.get("item", "Amazon product"))
        price = row.get("final_price")
        pct = row.get("pct_at_checkout") if row.get("regular_price_basis") == "List price" else None
        price_text = f'${price:.2f}' if isinstance(price, (float, int)) else "See today's price"
        discount = f'<span class="discount">{pct:g}% off</span>' if isinstance(pct, (float, int)) and pct > 0 else ''
        check = f'data-check-asin="{asin}" data-title="{title}"'
        # Report-card layout: title link first, then the photo, price and a full-width View Report bar.
        result.append(f'<article class="deal-card"><h3><a class="card-title" href="/review-checker/?asin={asin}" {check}>{title}</a></h3>'
            f'<p class="card-from">From <span>{escape(row.get("store") or "Amazon")}</span></p><div class="deal-photo">' +
            (f'<img src="{escape(photo, quote=True)}" alt="{title}" width="240" height="180" loading="lazy" decoding="async" referrerpolicy="no-referrer">' if photo else '<span aria-hidden="true">◇</span>') +
            f'</div><div class="card-body"><p class="deal-price">{price_text} {discount}</p>'
            + (f'<p class="card-as-of">Price as of {escape(row["amazon_as_of"])}</p>' if row.get("amazon_as_of") else '')
            + (f'<p class="card-steps">{escape(row.get("checkout", {}).get("summary", ""))}</p>' if row.get("needs_steps") else '') +
            f'<a class="get-deal" href="{escape(row["url"], quote=True)}" rel="sponsored noopener" target="_blank">Get deal <span aria-hidden="true">↗</span></a></div>'
            f'<button type="button" class="view-report" {check}>View Report</button></article>')
    return ''.join(result) or '<p>Explore <a href="/">today’s deals</a>, or paste an Amazon product link above.</p>'


def publish(out, cfg, now, site_url, disclosure):
    # This pass runs after the existing writers. OFF changes neither their source
    # hashes nor their output; it only removes assets owned by this feature.
    for path in PATHS:
        shutil.rmtree(out / path.strip("/"), ignore_errors=True)
    for suffix in ("js", "css"):
        (out / ("review-checker." + suffix)).unlink(missing_ok=True)
    if not enabled(cfg):
        return []
    from .site import linked_disclosure

    settings = validated_review_checker(cfg.review_checker)
    config_path = out / "search-config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text())
        config["review_checker"] = settings
        config_path.write_text(json.dumps(config, separators=(",", ":")))
    doc = json.loads((out / "deals.json").read_text())
    live = [d for d in doc["deals"] if not d.get("locked") and d.get("status") == "alive"
            and not d.get("price_unconfirmed")]
    # Published = price-checked; "badge" only counts agreeing sources, so it is not a filter.
    amazon = [d for d in live if _asin(d)
              and not any(d.get(k) for k in ("price_aged", "click_price_unknown"))]
    stores = {d["store"].casefold() for d in live if d.get("store")}
    assets = {}
    merged = tools_merge.enabled(cfg)
    css = (TEMPLATES / "review-checker.css").read_text() + (tools_merge.REVIEW_CSS if merged else '') + shipping_note.review_css(cfg)
    for suffix, content in (("js", script(cfg)), ("css", css)):
        (out / ("review-checker." + suffix)).write_text(content)
        assets[suffix] = hashlib.sha256(content.encode()).hexdigest()[:12]
    # Deal Spotter ships separately; never link to it while it is OFF.
    # tools_merge: menus name the two tools; the old addresses are keyword landing pages.
    nav = ''.join(f'<a href="{url}">{escape(label)}</a>' for url, label in NAV
                  if (url != "/deal-spotter/" or spotter_on(cfg)) and (not merged or tools_merge.keep(url)))
    nav_row = ''.join(f'<a href="{url}">{escape(label)}</a>' for url, label in NAV_ROW
                      if (url != "/deal-spotter/" or spotter_on(cfg)) and (not merged or tools_merge.keep(url)))
    faq = ''.join(f'<details><summary>{escape(q)}</summary><p>{escape(a)}</p></details>' for q, a in FAQ)
    schema = seo._json_ld({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in FAQ]})
    socials = deal_spotter.social_links(cfg)
    follow = '<h2>Follow us:</h2><div class="socials">' + socials + '</div>' if socials else ''
    signup = (TEMPLATES / "footer-signup.html").read_text().replace('aria-labelledby="footer-signup-title" hidden', 'aria-labelledby="footer-signup-title"')
    signup = signup.replace('>Sign up for the latest deals<', '>Newsletter sign-up<').replace(
        '<button type="submit" aria-label="Sign up"><span aria-hidden="true">→</span></button>', '<button type="submit">Subscribe</button>')
    install = (TEMPLATES / "pwa-install-button.html").read_text().replace('Install app', 'Install the free app')
    install_script = (TEMPLATES / "pwa-install.js").read_text().replace('phoneDelay = 180000', 'phoneDelay = 0')
    if cfg.pwa_android["enabled"]:
        install_script += "\nif ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(function () {});"
    base = (TEMPLATES / "review-checker.html").read_text()
    if merged:
        base = tools_merge.review_template(base)
    for path in PATHS:
        alternative = path == PATHS[1]
        title = "ReviewMeta Alternative: Free Amazon Review Checker | wowpriced" if alternative else "Review Checker: Free Amazon Review Signals | wowpriced"
        intro = ('<section class="alternative"><p class="eyebrow">A FREE TOOL FOR AMAZON SHOPPERS</p>'
                 '<h1>Looking for a ReviewMeta alternative?</h1><p>ReviewMeta is no longer available. '
                 'Review Checker by wowpriced is a free tool that grades the review signals of an Amazon product. '
                 'It does not read or remove reviews.</p><a class="button" href="/review-checker/">Try Review Checker →</a></section>') if alternative else ''
        html = seo._fill(base, TITLE=escape(title), CANONICAL=site_url + path, LOGO=LOGO, NAV=nav, NAV_ROW=nav_row,
                         CSS_VERSION=assets["css"], JS_VERSION=assets["js"], SCHEMA=schema,
                         DISCLAIMER=escape(DISCLAIMER), AMAZON_COUNT=str(len(amazon)), STORE_COUNT=str(len(stores)),
                         CARDS=cards(amazon), FAQ=faq, SIGNUP=signup, FOLLOW=follow,
                         INSTALL=install if cfg.pwa_enabled else '',
                         INSTALL_HELP=(TEMPLATES / "pwa-install-help.html").read_text() if cfg.pwa_enabled else '',
                         INSTALL_SCRIPT='<script>' + install_script + '</script>' if cfg.pwa_enabled else '',
                         YEAR=str(now.year), DISCLOSURE=linked_disclosure(disclosure), ALTERNATIVE=intro,
                         PRICE_NOTE=escape(amazon_as_of.DISCLAIMER),
                         SPOTTER_ABOUT='<a href="/deal-spotter/">Fake Deal Spotter</a> for price context and ' if spotter_on(cfg) else '',
                         SPOTTER_TILE=('<a href="/deal-spotter/"><span class="tile-art" aria-hidden="true">◇<i>✓</i></span>'
                                       '<span>Fake Deal Spotter →</span></a>' if spotter_on(cfg) else
                                       '<a href="/amazon-discount-finder/"><span class="tile-art" aria-hidden="true">%<i>✓</i></span>'
                                       '<span>Discount Finder →</span></a>'),
                         SPOTTER_FOOTER='<a href="/deal-spotter/">Fake Deal Spotter</a>' if spotter_on(cfg) else '')
        html = tracking.inject(html, tool_pages._tracking(cfg))
        seo._write_page(out, path, html)
    # Only footer links change on home/deal pages. Existing tool navigation gains
    # this tool; the standalone tool's own Menu is already complete.
    for path in out.rglob("*.html"):
        original = html = path.read_text()
        def footer(match):
            return match[0] if '/review-checker/' in match[0] else match[1] + ' · ' + LINK + match[2]
        html = re.sub(r'(<nav\b[^>]*\bid="footerlinks"[^>]*>[\s\S]*?)(</nav>)', footer, html, count=1)
        if path.parent.name in ("amazon-deal-checker", "amazon-discount-finder") and 'class="spotter-menu"' in html:
            # Deal Spotter already gave these pages a phone Menu (with this tool); add the desktop link only.
            html = re.sub(r'(<nav aria-label="Tools">)(.*?)(</nav>)', lambda m: m[0] if '/review-checker/' in m[2]
                          else m[1] + m[2] + LINK + m[3], html, count=1, flags=re.S)
        elif path.parent.name in ("amazon-deal-checker", "amazon-discount-finder", "deal-spotter"):
            if 'review-tools-menu' not in html:
                def tool_menu(match):
                    desktop = match[1].replace('<nav ', '<nav class="review-tools-desktop" ', 1)
                    links = match[2] if '/review-checker/' in match[2] else match[2] + LINK
                    return (desktop + links + match[3] + '<details class="review-tools-menu"><summary>☰ Menu</summary>'
                            '<nav aria-label="Tools menu">' + nav + '</nav></details>')
                updated = re.sub(r'(<nav\b[^>]*aria-label="Tools"[^>]*>)([\s\S]*?)(</nav>)', tool_menu, html, count=1)
                if updated != html:
                    html = updated.replace('</head>', TOOL_MENU_CSS + '</head>', 1)
        if html != original:
            path.write_text(html)
    return list(PATHS)
