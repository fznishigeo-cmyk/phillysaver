"""Permanent exact-product pages built only from our published facts and checks."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from datetime import datetime, timedelta, timezone
from html import escape

from ..savings import display_pct, price_difference_pct, price_verdict, product_price_facts
from ..source_policy import source_image_allowed
from ..util import iso, parse_iso
from . import amazon_as_of, amazon_history, price_history

META = "seo_product_pages"
BLOCK = re.compile(r"/\*product-pages\*/(.*?)/\*/product-pages\*/", re.S)
STYLE = """<style>
.product-detail img{display:block;max-width:min(100%,320px);max-height:320px;object-fit:contain}
.product-detail li{margin:12px 0}
.product-detail a,.price-index a{display:inline-flex;align-items:center;min-height:44px;min-width:44px}
.product-detail a:focus-visible,.price-index a:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.product-facts{width:100%;max-width:640px;border-collapse:collapse;background:var(--card);color:var(--fg)}
.product-facts caption{text-align:left;font-weight:700}
.product-facts th,.product-facts td{text-align:left;padding:10px;border:1px solid var(--line)}
.product-facts th{width:50%}
.product-detail .breadcrumbs{display:flex;flex-wrap:wrap;gap:0 8px;align-items:center}
</style>"""
LINK_STYLE = """.actions{flex-wrap:wrap}
.product-history-link{display:inline-flex;align-items:center;min-height:44px}
.actions>.product-history-link{flex-basis:100%}
"""
READER_STYLE = """<style>
:root{color-scheme:light dark;--bg:#f3f5f7;--card:#ffffff;--fg:#1a1d21;--muted:#5c6570;
--line:#e2e6eb;--accent:#0e7569;--accent-fg:#fff;--link:#0b63c4;--hist:#1d5fd1}
@media(prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161c23;--fg:#e7eaee;
--muted:#a4adb7;--line:#2a313a;--accent:#2fb3a4;--accent-fg:#07211d;--link:#6cb1ff;--hist:#79a8ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.6 system-ui,sans-serif;overflow-wrap:anywhere}
a{color:var(--link);text-decoration:underline;text-underline-offset:2px}
a,button{min-height:44px;min-width:44px}a{display:inline-flex;align-items:center}
:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
header{background:var(--card);border-bottom:1px solid var(--line)}
.hrow{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;max-width:1000px;margin:auto;padding:12px 16px}
.brand{font-size:22px;font-weight:800;margin:0}.brand a{gap:8px;color:var(--fg);text-decoration:none}
.mark{width:32px;height:32px;transform:rotate(-8deg)}.word em{font-style:normal;color:var(--accent)}
.tag,.lede,.disclosure{color:var(--muted)}.tag{font-size:13px}
.wrap{max-width:900px;margin:auto;padding:0 16px 32px}h1{font-size:28px;line-height:1.25;margin:24px 0 12px}
h2{font-size:21px;margin:24px 0 8px}p{margin:12px 0}ul,ol{padding-left:24px}
.getdeal{padding:10px 20px;background:var(--accent);color:var(--accent-fg);border-radius:10px;font-weight:700}
.detail-price{font-size:22px}.detail-price strong{font-weight:800}table{font-size:15px}
footer{margin-top:32px;padding:20px 0;border-top:1px solid var(--line);color:var(--muted);font-size:13px}
footer nav{display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center}.footerfine{font-size:12px}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
</style>"""


# deal-actions.js is stripped with the other scripts; keep its Get deal
# analytics (deal_click is a GA4 key event the growth report counts).
CLICK_SCRIPT = ("""<script>(function(){function c(e){if(e.type==="auxclick"?e.button!==1:e.button>0)return;"""
                """var a=e.target.closest&&e.target.closest('a[data-deal-outbound][rel~="sponsored"]');"""
                """if(!a||typeof wpTrack!=="function")return;try{wpTrack("deal_click",{store:a.getAttribute("data-store")||"","""
                """category:a.getAttribute("data-category")||"other",method:"home"});"""
                """if(a.hasAttribute("data-step-offer"))wpTrack("requirements_deal_click",{method:"home"})}catch(x){}}"""
                """document.addEventListener("click",c);document.addEventListener("auxclick",c)})()</script>""")


def reader_template(text):
    """Small static reader: retain navigation; Join links lead to the Join page."""
    text = re.sub(r'<script\b[^>]*>.*?</script>', '', text, flags=re.S)
    text = re.sub(r'<section id="footer-signup"[^>]*>.*?</section>', '', text, flags=re.S)
    text = text.replace('</body>', CLICK_SCRIPT + '</body>', 1)
    return re.sub(r'<style>.*?</style>', lambda _: READER_STYLE, text, count=1, flags=re.S)


def template(text, enabled):
    return BLOCK.sub(lambda m: m[1] if enabled else "", text)


def empty_summary():
    return dict.fromkeys(("eligible", "indexable", "new_today", "noindex",
                          "with_live_deal", "related_links_avg"), 0)


def _price(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value >= 0


def prepare(db, cfg, now, public, source):
    """Remember public facts, paths and admissions; a retry spends no extra slots.

    Product anchors survive deal deletion. Only previously published offer keys
    contribute checks, so a locked or rejected offer cannot leak into a history.
    """
    if not cfg.seo_product_pages_enabled:
        return {}, {}, empty_summary()
    from . import seo

    now = now.astimezone(timezone.utc)
    state = json.loads(db.meta_get(META) or '{"pages":{},"day":"","admitted":[]}')
    pages = state["pages"]
    today = now.date().isoformat()
    if state["day"] != today:
        state.update(day=today, admitted=[])
    raw = {d["dedupe_key"]: d for d in db.all_deals() + source}
    # Read existing exact identities only. Titles and bare brand/model pairs
    # cannot create a product price page.
    anchors = {}
    for row in db.conn.execute(
            "SELECT p.product_id,p.signature,k.kind,k.value FROM products p"
            " JOIN product_keys k USING(product_id)"
            " WHERE k.kind='asin' OR k.kind='gtin' OR k.kind LIKE 'sku:%'"
            " ORDER BY CASE k.kind WHEN 'asin' THEN 0 WHEN 'gtin' THEN 1 ELSE 2 END,k.kind,k.value"):
        anchors.setdefault(row["product_id"], dict(row))
    live = {}
    for d in public:
        if d.get("locked"):
            continue
        facts = raw.get(d["dedupe_key"], {})
        product = facts.get("product_id")
        if (product not in anchors or d.get("price_unconfirmed")
                or str(facts.get("currency") or "USD").upper() != "USD"
                or {"range", "per_unit"} & set(d.get("flags") or [])
                or not _price(d.get("final_price"))):
            continue
        key = str(product)
        if d.get("status") not in {"alive", "dead"}:
            continue
        anchor = anchors[product]
        identifier = hashlib.sha256(
            f'{anchor["kind"]}|{anchor["value"]}|{anchor["signature"]}'.encode()).hexdigest()[:16]
        page = pages.setdefault(key, {"path": seo.deal_path(d["item"], identifier).replace("/deal/", "/p/", 1),
                                      "offers": {}, "indexable": False})
        page.update({field: d.get(field) for field in
                     ("item", "store", "image_url", "category", "conditions", "brand")})
        previous = page["offers"].get(d["dedupe_key"], {})
        published = iso(now) if d["status"] == "alive" else previous.get("published_at") or d.get("first_seen_at") or iso(now)
        price_date = (facts.get("price_since") or (previous.get("date") if previous.get("price") == d["final_price"]
                      else iso(now) if previous else d.get("first_seen_at")) or iso(now))
        page["offers"][d["dedupe_key"]] = {
            "date": price_date, "price": d["final_price"],
            "credits": d.get("credits") or [], "published_at": published,
            "discount": d.get("pct_at_checkout")}
        if d["status"] == "alive" and not amazon_history.hidden(cfg, d):
            live.setdefault(key, []).append(d)

    current = {key: min(rows, key=lambda d: (d["final_price"], d["key"])) for key, rows in live.items()}
    for key, d in current.items():
        pages[key].update({field: d.get(field) for field in
                           ("item", "store", "image_url", "category", "conditions", "brand")})
    if cfg.amazon_history == "hide":
        # product_offers retains the original store even after a deal is pruned.
        stores = {r["dedupe_key"]: {"store": r["store"], "canonical_url":
                  r["dedupe_key"][4:] if r["dedupe_key"].startswith("url|") else ""} for r in db.conn.execute(
            "SELECT dedupe_key,store FROM product_offers")}
        visible = {}
        for key, page in pages.items():
            offers = {offer: facts for offer, facts in page["offers"].items()
                      if stores.get(offer) and not amazon_history.hidden(cfg, stores[offer])}
            if not offers:
                continue
            visible[key] = dict(page, offers=offers)
            if amazon_history.hidden(cfg, page) or not any(
                    stores[offer]["store"] == page.get("store") for offer in offers):
                latest = max(offers, key=lambda offer: (offers[offer]["date"], offer))
                visible[key].update(store=stores[latest]["store"], image_url="")
        pages = visible
    observations, qualifying = {}, set()
    for key, page in pages.items():
        observations[key] = [row for row in db.product_price_history(int(key), None, now=now)
                             if row["dedupe_key"] in page["offers"]]
        checks = [r for r in observations[key] if parse_iso(r["observed_at"]) >= now - timedelta(days=90)]
        recent = any(now - timedelta(days=90) <= parse_iso(o["published_at"]) <= now
                     for o in page["offers"].values())
        if (len(checks) >= cfg.seo_product_pages_min_checks and recent
                and parse_iso(checks[-1]["observed_at"]) - parse_iso(checks[0]["observed_at"])
                >= timedelta(days=cfg.seo_product_pages_min_span_days)):
            qualifying.add(key)
        page["indexable"] = bool(page.get("indexable") and parse_iso(page.get("last_qualified"))
                                  and now <= parse_iso(page["last_qualified"]) + timedelta(days=30))

    # Existing admissions keep their slots through a sparse day. A lowered
    # total cap still takes effect deterministically.
    retained = sorted((key for key, page in pages.items() if page["indexable"]),
                      key=lambda key: (pages[key]["indexed_since"], key))
    admitted = set(retained[:cfg.seo_product_pages_max_indexable])
    for key in sorted(qualifying - admitted):
        if (len(admitted) >= cfg.seo_product_pages_max_indexable
                or len(state["admitted"]) >= cfg.seo_product_pages_max_new_indexable_per_day):
            break
        admitted.add(key)
        if key not in state["admitted"]:
            state["admitted"].append(key)
        pages[key]["indexed_since"] = iso(now)
    for key, page in pages.items():
        page["indexable"] = key in admitted
        if key in admitted and key in qualifying:
            page["last_qualified"] = iso(now)
        if cfg.amazon_history == "hide":
            for field in ("indexable", "last_qualified", "indexed_since"):
                if field in page:
                    state["pages"][key][field] = page[field]
    db.meta_set(META, json.dumps(state, sort_keys=True, separators=(",", ":")))
    report = {"eligible": len(qualifying), "indexable": len(admitted),
              "new_today": sum(key in pages for key in state["admitted"]), "noindex": len(pages) - len(admitted)}
    models = {}
    for key, page in pages.items():
        live_keys = {d["dedupe_key"] for d in live.get(key, [])}
        statuses = {offer: "Live" if offer in live_keys else "Ended" if raw.get(offer, {}).get("status") == "dead"
                    else "No live deal currently published" for offer in page["offers"]}
        models[key] = dict(page, checks=observations[key], live=current.get(key), statuses=statuses)
    index = related_index(models)
    report.update(with_live_deal=len(current), related_links_avg=(
        sum(len(related(p, models, index)) for p in models.values()) / len(models) if models else 0))
    # A later variant conflict can quarantine an offer without deleting its
    # former product page. Only still-linked offers get navigation to it.
    paths = {offer: page["path"] for key, page in pages.items() for offer in page["offers"]
             if str(raw.get(offer, {}).get("product_id")) == key}
    return models, paths, report


def history_link(path):
    return f'<a class="product-history-link" href="{escape(path)}">Price history</a>' if path else ""


def hub_links(pages, group):
    from . import seo

    rows = [p for p in pages.values() if p["indexable"] and seo.in_group(p, group)]
    rows.sort(key=lambda p: (-len(p["checks"]), p["path"]))
    popular = '<h2>Price history for popular items</h2><ul>' + ''.join(
        f'<li><a href="{escape(p["path"])}">{escape(p["item"])}</a></li>' for p in rows[:12]) + '</ul>'
    return (popular if rows else "") + '<p><a href="/p/">Browse all price history</a></p>'


def category(page):
    from . import seo

    return next((g for g in seo.HUB_GROUPS if g[1] != "USED" and seo.in_group(page, g[1])),
                seo.CATGROUPS[-1])


def related_index(pages):
    """Indexable pages by category and by brand, each in path order (built once per publish)."""
    by_category, by_brand = {}, {}
    for p in sorted((p for p in pages.values() if p["indexable"]), key=lambda p: p["path"]):
        by_category.setdefault(p.get("category"), []).append(p)
        brand = str(p.get("brand") or "").strip().casefold()
        if brand:
            by_brand.setdefault(brand, []).append(p)
    return by_category, by_brand


def related(page, pages, index=None):
    """Known same-category pages first, then a stored matching brand; stable URLs."""
    by_category, by_brand = index or related_index(pages)
    brand = str(page.get("brand") or "").strip().casefold()
    # Seven same-category rows leave six after dropping the page itself.
    rows = (by_category.get(page["category"], [])[:7] if page.get("category") else []) + by_brand.get(brand, [])
    rows = {p["path"]: p for p in rows if p["path"] != page["path"]}.values()
    return sorted(rows, key=lambda p: (p.get("category") != page.get("category"), p["path"]))[:6]


def answer(price, facts, cfg):
    from . import seo

    if price is None:
        return ("A current offer is not listed for this product." + (f' The lowest we’ve seen is ${facts["low"]:.2f}'
                f' ({seo._date(facts["low_at"])}).' if facts["checks"] else ""))
    verdict = price_verdict(price, facts["median"], cfg.seo_product_pages_lower_ratio,
                            cfg.seo_product_pages_higher_ratio)
    if not verdict:
        return ""
    difference = price_difference_pct(price, facts["median"])
    comparison = (f'{display_pct(abs(difference))}% {"below" if difference > 0 else "above"}'
                  if difference is not None and display_pct(abs(difference)) else
                  "above" if verdict == "Higher than usual" else "about")
    prefix = {"Lower than usual": "Yes", "Higher than usual": "Higher than usual",
              "About usual": "About usual"}[verdict]
    window = "90-day median" if facts["median_90d"] else f'our median since {seo._date(facts["median_since"])}'
    return f'{prefix} — ${price:.2f} is {comparison} its usual ${facts["median"]:.2f} ({window}).'


def facts_html(facts, price):
    from . import seo

    rows = []
    if price is not None:
        rows.append(("Current price (live deal)", f"${price:.2f} USD"))
    if facts["checks"]:
        rows.extend([("Lowest we’ve seen", f'${facts["low"]:.2f} ({seo._date(facts["low_at"])})'),
                     ("Highest we’ve seen", f'${facts["high"]:.2f} ({seo._date(facts["high_at"])})'),
                     ("Number of our checks", str(facts["checks"])),
                     ("First check", seo._date(facts["first"])),
                     ("Last check", seo._date(facts["last"]))])
    if facts["median"] is not None:
        window = ("90-day median" if facts["median_90d"] else
                  f'median of our {facts["median_checks"]} checks since {seo._date(facts["median_since"])}')
        rows.append((f"Typical price ({window})", f'${facts["median"]:.2f} USD'))
    rows.append(("Deals found in the last 90 days", str(facts["deals_90d"])))
    if facts["average_discount"] is not None:
        samples = facts["discount_samples"]
        rows.append(("Average deal discount (90 days)",
                     f'{display_pct(facts["average_discount"])}% across {samples} deal{"" if samples == 1 else "s"}'))
    return ('<table class="product-facts"><caption>Key price facts</caption><tbody>'
            + ''.join(f'<tr><th scope="row">{escape(k)}</th><td>{escape(v)}</td></tr>' for k, v in rows)
            + '</tbody></table>')


def social_head(title, description, url, image):
    tags = {"og:type": "product", "og:title": title, "og:description": description, "og:url": url,
            "twitter:card": "summary_large_image" if image else "summary",
            "twitter:title": title, "twitter:description": description}
    if image:
        tags.update({"og:image": image, "twitter:image": image})
    return ''.join(f'<meta {"property" if k.startswith("og:") else "name"}="{k}" content="{escape(v)}">'
                   for k, v in tags.items())


def publish(out, cfg, now, pages, template, site_url, disclosure, deal_paths):
    from . import seo
    from .site import linked_disclosure

    template = reader_template(template)
    index = related_index(pages)
    wanted = {p["path"].strip("/").split("/")[1] for p in pages.values()}
    for folder in (out / "p").glob("*"):
        if folder.name == "index.html":
            continue
        if folder.name not in wanted:
            shutil.rmtree(folder) if folder.is_dir() else folder.unlink()
    emitted = []
    for page in sorted(pages.values(), key=lambda p: p["path"]):
        path, current = page["path"], page["live"]
        price = current["final_price"] if current else None
        facts = product_price_facts(page["checks"], list(page["offers"].values()), now)
        label, _group, slug = category(page)
        breadcrumbs = [(label, seo.hub_href(slug)), (page["item"], path)]
        content = ('<nav class="breadcrumbs" aria-label="Breadcrumb">'
                   '<a href="/">Home</a> › '
                   f'<a href="{seo.hub_href(slug)}">{escape(label)}</a> › '
                   f'<span aria-current="page">{escape(page["item"])}</span></nav>')
        content += facts_html(facts, price)
        if facts["checks"]:
            content += (f'<p>Last checked: <time class="last-check" datetime="{iso(facts["last"])}">'
                        f'{seo._date(facts["last"])}, {facts["last"].astimezone(timezone.utc):%H:%M} UTC</time></p>')
        explanation = answer(price, facts, cfg)
        if explanation:
            content += '<h2>Is now a good time to buy?</h2><p class="price-verdict">' + escape(explanation) + '</p>'
        if facts["latest_deal"]:
            content += ('<h2>Sale pattern</h2><p>'
                        f'On sale {facts["deals_90d"]} time{"" if facts["deals_90d"] == 1 else "s"} in the last 90 days; most recent '
                        f'{seo._date(facts["latest_deal"])} (UTC). Counts reflect distinct deals we found.</p>')
        content += f'<p class="detail-store">Store: {escape(page["store"])}</p>'
        image = str(page.get("image_url") or "")
        offers = sorted(page["offers"].items(), key=lambda pair: (pair[1]["date"], pair[0]), reverse=True)
        credits = [{"source_url": c.get("url")} for _, offer in offers[:cfg.seo_product_pages_deal_history_limit]
                   for c in offer["credits"]]
        if not source_image_allowed(image, credits, cfg):
            image = ""
        if image.startswith("https://"):
            content += (f'<img src="{escape(image)}" alt="{escape(page["item"])} product photo" '
                        'width="320" height="320" loading="lazy" referrerpolicy="no-referrer">')
        else:
            image = ""
        condition = ', '.join(str(c).replace('_', ' ') for c in page.get("conditions") or []) or 'new'
        content += f'<p>Condition: {escape(condition)}</p>'
        if current:
            content += (f'<p class="detail-price">Current deal price: <strong>${price:.2f}</strong> USD</p>'
                        '<p class="detail-availability">Availability: Live offer</p>')
        chart = price_history.chart_html(page["checks"], now, price)
        content += chart or '<p>Price charts require at least three recorded checks spanning two days.</p>'
        if current:
            checkout = current.get("checkout") or {}
            for field in ("summary", "now", "shipping"):
                if checkout.get(field):
                    content += f'<p>{escape(checkout[field])}</p>'
            if current.get("how"):
                content += '<ol>' + ''.join(f'<li>{escape(str(s))}</li>' for s in current["how"]) + '</ol>'
            for code in current.get("codes") or []:
                content += f'<p>Code: <code>{escape(str(code))}</code></p>'
            if current.get("amazon_as_of"):
                content += (f'<p class="detail-checked">Amazon price as of {escape(current["amazon_as_of"])}</p>'
                            f'<details><summary>Details</summary><p>{amazon_as_of.DISCLAIMER}</p></details>')
            url = seo._http(current.get("url"))
            if url:
                content += (f'<a class="getdeal" href="{escape(url)}" target="_blank" '
                            f'rel="sponsored noopener"{seo._outbound_attrs(current)}>Get deal</a>'
                            f'<p class="disclosure">{linked_disclosure(disclosure)}</p>')
            detail = deal_paths.get(current["key"][:8])
            if detail:
                content += f'<p><a href="{escape(detail)}">See deal details</a></p>'
        else:
            content += '<p>Explore <a href="/">current verified deals</a>.</p>'
        content += '<h2>Deals we found</h2><ul>'
        # Keep long-lived products compact; totals above still use every deal.
        for key, offer in offers[:cfg.seo_product_pages_deal_history_limit]:
            credits = ', '.join(
                f'<a href="{escape(seo._http(c.get("url")))}" rel="noopener">{escape(c["name"])}</a>'
                if seo._http(c.get("url")) else escape(c["name"]) for c in offer["credits"])
            status = page["statuses"][key]
            date = parse_iso(offer["date"])
            content += (f'<li><time datetime="{escape(offer["date"])}">{seo._date(date)}</time> (UTC)'
                        f' · ${offer["price"]:.2f} USD · {credits} · {status}</li>')
        content += f'</ul><p>Browse <a href="{seo.hub_href(slug)}">{escape(label)} deals</a>.</p>'
        if len(offers) > cfg.seo_product_pages_deal_history_limit:
            content += f'<p>Showing the {cfg.seo_product_pages_deal_history_limit} most recent deals we found.</p>'
        neighbours = related(page, pages, index)
        if neighbours:
            content += '<h2>Related products</h2><ul class="related-products">' + ''.join(
                f'<li><a href="{escape(p["path"])}">{escape(p["item"])}</a></li>' for p in neighbours) + '</ul>'
        schema = {"@context": "https://schema.org", "@type": "WebPage", "url": site_url + path,
                  "name": page["item"]}
        if facts["checks"]:
            schema["dateModified"] = iso(facts["last"])
        if current:
            product = {"@type": "Product",
                       "name": seo.seo_audit.word_limit(page["item"], seo.seo_audit.NAME_LIMIT)}
            if image:
                product["image"] = image
            product["offers"] = {"@type": "Offer", "price": f"{price:.2f}", "priceCurrency": "USD",
                                 "availability": "https://schema.org/InStock", "url": site_url + path}
            schema["mainEntity"] = product
        title = page["item"] + " price history"
        description = "Our recorded price checks and deals for " + page["item"] + "."
        if facts["checks"]:
            title += f': lowest ${facts["low"]:.2f} ({facts["low_at"]:%b %Y})'
            description = (f'We checked {page["item"]} {facts["checks"]} times since {seo._date(facts["first"])}.'
                           f' Lowest ${facts["low"]:.2f}'
                           + (f', usually ${facts["median"]:.2f}' if facts["median"] is not None else "") + ".")
        title += " | wowpriced"
        if explanation:
            description += " " + explanation
        html = seo._landing_page(template, title, page["item"], description, path, site_url,
            '<div class="product-detail">' + content + '</div>', indexable=page["indexable"],
            breadcrumbs=breadcrumbs, extra_head=seo._json_ld(schema)
            + social_head(title, description, site_url + path, image) + STYLE + (price_history.STYLE if chart else ""))
        if page["indexable"]:
            html = html.replace('</head>', '<meta name="robots" content="index,follow">\n</head>', 1)
        seo._write_page(out, path, html)
        emitted.append(path)
    if cfg.seo_product_pages_enabled:
        rows = sorted((p for p in pages.values() if p["indexable"]), key=lambda p: p["path"])
        content = '<div class="price-index">'
        for label, group, slug in seo.HUB_GROUPS:
            if group == "USED":
                continue
            members = [p for p in rows if category(p)[2] == slug]
            if members:
                content += f'<h2>{escape(label)}</h2><ul>' + ''.join(
                    f'<li><a href="{escape(p["path"])}">{escape(p["item"])}</a></li>' for p in members) + '</ul>'
        content += '</div>' if rows else '<p>Price histories are published only when they meet our verification standards. <a href="/">Browse verified deals</a>.</p></div>'
        html = seo._landing_page(template, "Product price history | wowpriced", "Price history",
            "Our recorded product price checks, grouped by category.", "/p/", site_url, content,
            indexable=bool(rows), extra_head=STYLE)
        seo._write_page(out, "/p/", html)
        emitted.append("/p/")
    else:
        (out / "p" / "index.html").unlink(missing_ok=True)
    return emitted
