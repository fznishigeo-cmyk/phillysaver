"""The opt-in Deal Check reader; lookups use published same-origin shards only."""

import json
import re

from . import amazon_as_of, android_app, checker, product_pages

TITLE = "Is This a Good Deal? Free Price Checker for Amazon, Best Buy, Target & More | wowpriced"
INTRO = ("Paste a product link from Amazon, Best Buy, Target, Walmart, eBay, Woot and more. "
         "Our AI-powered deal scanner checks it against prices we've verified.")
DESCRIPTION = "Check a product link against prices we've verified. Free price checks for Amazon, Best Buy, Target, Walmart, eBay, Woot and more."
LINK = '<a href="/check/">Deal Check</a>'
STYLE = """<style>
.checker{max-width:760px;margin:0 auto}.checker h1{font-size:clamp(32px,6vw,48px);letter-spacing:-1px}
.check-form{margin:28px 0 16px}.check-form label{display:block;font-weight:700;margin-bottom:8px}
.check-fields{display:flex;flex-wrap:wrap;gap:10px}
.check-fields input{flex:1 1 240px;min-width:0;min-height:56px;padding:12px 16px;border:1px solid var(--muted);
border-radius:12px;font:inherit;background:var(--card);color:var(--fg)}
.check-fields button{min-height:56px;padding:12px 28px;border:0;font:700 17px system-ui,sans-serif;
border-radius:12px;background:var(--accent);color:var(--accent-fg);cursor:pointer}
.check-stores{display:flex;flex-wrap:wrap;gap:8px;padding:0;list-style:none;color:var(--muted);font-size:13px}
.check-stores li{padding:4px 12px;border:1px solid var(--line);border-radius:999px;background:var(--card)}
.check-result:not(:empty){margin:28px 0;padding:24px;border:1px solid var(--line);border-radius:16px;background:var(--card)}
.check-verdict{font-size:clamp(24px,4vw,32px);line-height:1.35;margin:0 0 16px;font-weight:750}
.check-item{font-size:18px;margin:12px 0}.check-meta{color:var(--muted)}
.check-actions{display:flex;flex-wrap:wrap;gap:8px 16px}.check-actions a{padding:10px 16px;border:1px solid var(--line);border-radius:10px}
.check-result summary{min-height:44px;cursor:pointer;align-content:center}
.check-faq{margin-top:40px;border-top:1px solid var(--line)}.check-faq h3{font-size:17px;margin-bottom:4px}
.check-faq p{color:var(--muted)}
</style>"""

CONTENT = """<form id="check-form" class="check-form" data-clarity-mask="true">
<label for="product-url">Product link</label>
<div class="check-fields"><input id="product-url" name="u" type="url" required
 placeholder="https://www.amazon.com/dp/…" autocomplete="off" spellcheck="false"
 inputmode="url" enterkeyhint="go" aria-describedby="check-privacy">
<button type="submit">Check</button></div>
<p id="check-privacy" class="check-meta">We look up only our own verified records. Your link is never sent to stores or other sites.</p>
</form>
<ul class="check-stores" aria-label="Supported stores"><li>Amazon</li><li>Best Buy</li><li>Target</li>
<li>Walmart</li><li>eBay</li><li>Woot</li><li>And more</li></ul>
<section id="check-result" class="check-result" aria-label="Deal check result" aria-live="polite" aria-atomic="true"></section>
<noscript><p>Turn on JavaScript to check a link, or <a href="/">browse our verified deals</a>.</p></noscript>
<section class="check-faq" aria-labelledby="check-faq-title">
<h2 id="check-faq-title">About Deal Check</h2>
<h3>Where do the prices come from?</h3>
<p>We use prices verified for our deal pages, from stores, price data and credited deal sources.
The answer compares our recorded checks when we have enough history. Conditions and steps stay on the deal page.</p>
<h3>How often do you check?</h3>
<p>We check throughout the day. Each result shows when its price was verified; some products are checked more often than others.
Prices can change before your next visit, so check the store before buying.</p>
<h3>Why are some products not found?</h3>
<p>We may not have verified that product, or its link may use a different address.
Short links need the full product address. eBay results cover live deals only, because listings end.</p>
</section>"""


def publish(out, cfg, now, site_url, disclosure):
    from .. import amazon_reference
    from . import seo
    from .site import copy_variant, site_footer

    template = (seo.TEMPLATES_DIR / "hub.html").read_text(encoding="utf-8")
    template = copy_variant(template, True, cfg.support_enabled)
    template = site_footer(template, now, disclosure, cfg.support_enabled,
        amazon_as_of_enabled=cfg.amazon_as_of_enabled,
        product_pages_enabled=cfg.seo_product_pages_enabled and not amazon_reference.enabled(cfg),
        event_links=seo.event_pages.links(cfg), checker_enabled=True)
    template = product_pages.reader_template(template).replace(product_pages.CLICK_SCRIPT, "")
    template = template.replace("{{TABS}}", "")
    template = re.sub(r'{{DEALS}}.*?</main>', '{{DEALS}}</main>', template, count=1, flags=re.S)
    template = template.replace('<main data-clarity-unmask="true">', '<main class="checker">')
    schema = {"@context": "https://schema.org", "@type": "WebApplication", "name": "wowpriced Deal Check",
              "url": site_url + "/check/", "applicationCategory": "ShoppingApplication",
              "operatingSystem": "Any", "description": DESCRIPTION}
    # No analytics/pixels: a shared ?u= link must never become a tracking URL.
    # Tracking skips this page; shared footer and install passes still apply.
    head = (seo._json_ld(schema) + STYLE
            + '<meta name="referrer" content="no-referrer">'
            + '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
              'script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; img-src data:; '
              'connect-src \'self\'; manifest-src \'self\'; form-action \'none\'; base-uri \'none\'">')
    rules = json.dumps(checker.js_rules(), separators=(",", ":"))
    options = json.dumps({"shards": cfg.checker["shards"], "disclaimer": amazon_as_of.DISCLAIMER},
                         separators=(",", ":"))
    script = (seo.TEMPLATES_DIR / "checker.js").read_text(encoding="utf-8")
    script = android_app.checker_script(script, cfg)
    content = (CONTENT + '<script id="check-rules" type="application/json">' + rules + '</script>'
               + '<script id="check-options" type="application/json">' + options + '</script>'
               + '<script>' + script + '</script>')
    content = amazon_reference.template(content, cfg)
    html = seo._landing_page(template, TITLE, "Is this a good deal?", DESCRIPTION, "/check/", site_url,
                             content, intro=INTRO, extra_head=head)
    html = seo._fill(html, DROP_MINUTES=str(int(cfg.drop_minutes)))
    seo._write_page(out, "/check/", html)
