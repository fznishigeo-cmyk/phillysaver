"""Permanent exact-product pages built only from our published facts and checks."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from datetime import datetime, timedelta, timezone
from html import escape

from ..savings import price_verdict
from ..source_policy import source_image_allowed
from ..util import iso, parse_iso
from . import amazon_as_of, price_history

META = "seo_product_pages"
BLOCK = re.compile(r"/\*product-pages\*/(.*?)/\*/product-pages\*/", re.S)
STYLE = """<style>
.product-detail img{display:block;max-width:min(100%,320px);max-height:320px;object-fit:contain}
.product-detail li{margin:12px 0}
</style>"""
LINK_STYLE = """.actions{flex-wrap:wrap}
.product-history-link{display:inline-flex;align-items:center;min-height:44px}
.actions>.product-history-link{flex-basis:100%}
"""


def template(text, enabled):
    return BLOCK.sub(lambda m: m[1] if enabled else "", text)


def empty_summary():
    return dict.fromkeys(("eligible", "indexable", "new_today", "noindex"), 0)


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
                     ("item", "store", "image_url", "category", "conditions")})
        previous = page["offers"].get(d["dedupe_key"], {})
        published = iso(now) if d["status"] == "alive" else previous.get("published_at") or d.get("first_seen_at") or iso(now)
        price_date = (facts.get("price_since") or (previous.get("date") if previous.get("price") == d["final_price"]
                      else iso(now) if previous else d.get("first_seen_at")) or iso(now))
        page["offers"][d["dedupe_key"]] = {
            "date": price_date, "price": d["final_price"],
            "credits": d.get("credits") or [], "published_at": published}
        if d["status"] == "alive":
            live.setdefault(key, []).append(d)

    current = {key: min(rows, key=lambda d: (d["final_price"], d["key"])) for key, rows in live.items()}
    for key, d in current.items():
        pages[key].update({field: d.get(field) for field in
                           ("item", "store", "image_url", "category", "conditions")})
    observations, qualifying = {}, set()
    for key, page in pages.items():
        observations[key] = [row for row in db.product_price_history(int(key), 90, now=now)
                             if row["dedupe_key"] in page["offers"]]
        checks = observations[key]
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
    db.meta_set(META, json.dumps(state, sort_keys=True, separators=(",", ":")))
    report = {"eligible": len(qualifying), "indexable": len(admitted),
              "new_today": len(state["admitted"]), "noindex": len(pages) - len(admitted)}
    models = {}
    for key, page in pages.items():
        live_keys = {d["dedupe_key"] for d in live.get(key, [])}
        statuses = {offer: "Live" if offer in live_keys else "Ended" if raw.get(offer, {}).get("status") == "dead"
                    else "No live deal currently published" for offer in page["offers"]}
        models[key] = dict(page, checks=observations[key], live=current.get(key), statuses=statuses)
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
    if not rows:
        return ""
    return '<h2>Price history for popular items</h2><ul>' + ''.join(
        f'<li><a href="{escape(p["path"])}">{escape(p["item"])}</a></li>' for p in rows[:12]) + '</ul>'


def publish(out, cfg, now, pages, template, site_url, disclosure, deal_paths):
    from . import seo
    from .site import linked_disclosure

    wanted = {p["path"].strip("/").split("/")[1] for p in pages.values()}
    for folder in (out / "p").glob("*"):
        if folder.name not in wanted:
            shutil.rmtree(folder) if folder.is_dir() else folder.unlink()
    emitted = []
    for page in pages.values():
        path, current = page["path"], page["live"]
        price = current["final_price"] if current else None
        content = f'<p class="detail-store">Store: {escape(page["store"])}</p>'
        image = str(page.get("image_url") or "")
        credits = [{"source_url": c.get("url")} for offer in page["offers"].values() for c in offer["credits"]]
        if not source_image_allowed(image, credits, cfg):
            image = ""
        if image.startswith("https://"):
            content += (f'<img src="{escape(image)}" alt="{escape(page["item"])}" '
                        'loading="lazy" referrerpolicy="no-referrer">')
        condition = ', '.join(str(c).replace('_', ' ') for c in page.get("conditions") or []) or 'new'
        content += f'<p>Condition: {escape(condition)}</p>'
        if current:
            content += (f'<p class="detail-price">Current deal price: <strong>${price:.2f}</strong> USD</p>'
                        '<p class="detail-availability">Availability: Live offer</p>')
        chart = price_history.chart_html(page["checks"], now, price)
        content += chart or '<p>Not enough recorded checks to chart a price history yet.</p>'
        history = price_history.summary(page["checks"], now)
        verdict = price_verdict(price, history["median"] if history else None,
                                cfg.seo_product_pages_lower_ratio, cfg.seo_product_pages_higher_ratio)
        if verdict:
            content += f'<p class="price-verdict">{verdict} compared with our 90-day median.</p>'
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
            content += '<p>No live deal currently published.</p>'
        content += '<h2>Deals we found</h2><ul>'
        for key, offer in sorted(page["offers"].items(), key=lambda pair: (pair[1]["date"], pair[0]), reverse=True):
            credits = ', '.join(
                f'<a href="{escape(seo._http(c.get("url")))}" rel="noopener">{escape(c["name"])}</a>'
                if seo._http(c.get("url")) else escape(c["name"]) for c in offer["credits"])
            status = page["statuses"][key]
            date = parse_iso(offer["date"])
            content += (f'<li><time datetime="{escape(offer["date"])}">{date:%Y-%m-%d}</time> (UTC)'
                        f' · ${offer["price"]:.2f} USD · {credits} · {status}</li>')
        label, _group, slug = next((g for g in seo.HUB_GROUPS if g[1] != "USED" and seo.in_group(page, g[1])),
                                   seo.CATGROUPS[-1])
        content += f'</ul><p>Browse <a href="{seo.hub_href(slug)}">{escape(label)} deals</a>.</p>'
        product = {"@context": "https://schema.org", "@type": "Product",
                   "name": seo.seo_audit.word_limit(page["item"], seo.seo_audit.NAME_LIMIT)}
        if image.startswith("https://"):
            product["image"] = image
        if current:
            product["offers"] = {"@type": "Offer", "price": f"{price:.2f}", "priceCurrency": "USD",
                                 "availability": "https://schema.org/InStock", "url": site_url + path}
        html = seo._landing_page(template, page["item"] + " price history | wowpriced", page["item"],
            "Our recorded price checks and deals for " + page["item"] + ".", path, site_url,
            '<div class="product-detail">' + content + '</div>', indexable=page["indexable"],
            extra_head=seo._json_ld(product) + STYLE + (price_history.STYLE if chart else ""))
        if page["indexable"]:
            html = html.replace('</head>', '<meta name="robots" content="index,follow">\n</head>', 1)
        seo._write_page(out, path, html)
        emitted.append(path)
    return emitted
