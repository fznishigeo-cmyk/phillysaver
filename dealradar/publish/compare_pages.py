"""Static, arithmetic value comparisons. The shipped OFF path changes no bytes."""

import json
import hashlib
import re
import shutil
from datetime import timedelta
from html import escape

from .. import compare, verify
from ..util import iso, parse_iso
from . import amazon_as_of, product_pages

LINK = '<a href="/compare/">Compare prices</a>'
STYLE = """<style>
.compare-list{list-style:none;padding:0}.compare-row{background:var(--card);color:var(--fg);
border:1px solid var(--line);border-radius:12px;padding:16px;margin:16px 0;overflow:hidden}
.compare-row img{float:left;width:100px;height:100px;object-fit:contain;margin:0 16px 12px 0}
.compare-row h2{margin-top:0;font-size:18px}.compare-row details{clear:both}
.compare-row summary{cursor:pointer;min-height:44px;align-content:center}
.compare-row a,.compare-terms a{min-height:44px;display:inline-flex;align-items:center}
.compare-row[hidden],.best-value[hidden],.sale-badge[hidden]{display:none}
#compare-search{width:100%;min-height:48px;padding:10px;font:inherit;background:var(--card);
color:var(--fg);border:1px solid var(--muted);border-radius:8px}
.compare-terms li{margin:8px 0}.compare-row blockquote{margin:8px 0;color:var(--muted)}
</style>"""
SEARCH = """<script>(function(){
var terms=JSON.parse(document.getElementById('compare-terms').textContent);
var input=document.getElementById('compare-search'),list=document.getElementById('compare-results');
var status=document.getElementById('compare-status');
function words(s){return s.toLowerCase().match(/[a-z0-9]+/g)||[]}
function update(){var q=input.value.trim().toLowerCase(),qw=words(q);
var matches=terms.filter(function(t){return !q||qw.every(function(w){return t.words.indexOf(w)>=0})});
status.textContent=matches.length?'':'Not covered yet. Closest product types:';
if(!matches.length)matches=terms.slice().sort(function(a,b){
function score(t){return qw.filter(function(w){return t.words.indexOf(w)>=0}).length}
return score(b)-score(a)||a.name.localeCompare(b.name)}).slice(0,5);
list.replaceChildren();matches.forEach(function(t){var li=document.createElement('li');
var label=document.createElement(t.path?'a':'span');label.textContent=t.name;
if(t.path)label.setAttribute('href',t.path);else label.textContent+=' — Not covered yet';
li.appendChild(label);list.appendChild(li)})}
input.addEventListener('input',update);update();
})();</script>"""
CLOCK = """<script>(function(){
var list=document.querySelector('.compare-list');if(!list)return;
var rows=Array.from(list.querySelectorAll('.compare-row'));
var badges=Array.from(list.querySelectorAll('.sale-badge'));
function update(){var now=Date.now(),visible=0;
badges.forEach(function(badge){var until=Date.parse(badge.dataset.confirmedUntil);
badge.hidden=!Number.isFinite(until)||now>until});
rows.forEach(function(row){var clock=row.querySelector('time.detail-checked');
var age=now-Date.parse(clock.getAttribute('datetime'));
row.hidden=!Number.isFinite(age)||age<0||age>Number(list.dataset.maxAgeHours)*3600000;
if(!row.hidden){visible++;clock.textContent='Checked '+Math.floor(age/60000)+' min ago'}});
var banner=document.querySelector('.best-value');if(banner)banner.hidden=visible!==rows.length;
document.getElementById('compare-empty').hidden=visible>0}
update();setInterval(update,60000);window.addEventListener('pageshow',update);
document.addEventListener('visibilitychange',update);
})();</script>"""


def unit_text(value, unit, display_unit=None):
    amount = f"{value * 100:.2f}".rstrip('0').rstrip('.') + '¢' if value < 1 else f"${value:.2f}"
    return amount + " per " + (display_unit or {"fl_oz": "fl oz", "each": "item", "count": "item"}.get(unit, unit))


def footer_links(out, cfg):
    if cfg.compare["publish"] != "on" or not (out / 'compare/index.html').is_file():
        return
    for path in out.rglob("*.html"):
        html = path.read_text(encoding="utf-8")
        def add(match):
            return match[0] if LINK in match[0] else match[0].replace('</nav>', ' · ' + LINK + '</nav>', 1)
        updated = re.sub(r'<footer\b[^>]*>.*?</footer>', add, html, flags=re.S)
        if updated != html:
            path.write_text(updated, encoding="utf-8")


def publish(out, db, cfg, now, site_url, disclosure, products, *, deals=(), deal_paths=None):
    from . import seo
    from .site import copy_variant, linked_disclosure, rewrite_affiliate, site_footer

    mode = cfg.compare["publish"]
    folder = out / "compare"
    if mode == "off":
        if folder.is_dir():
            shutil.rmtree(folder)
        return []
    terms = (compare.load_terms(cfg.compare["terms_file"], strict=False) or [])[:cfg.compare["max_terms"]]
    data = compare.state(db)
    rows = {t["slug"]: compare.ranked_rows(t, data, cfg, now) for t in terms}
    wanted = {t["slug"] for t in terms if rows[t["slug"]]}
    if not wanted:
        if folder.is_dir():
            shutil.rmtree(folder)
        return []
    if folder.is_dir():
        for child in folder.iterdir():
            if child.is_dir() and child.name not in wanted:
                shutil.rmtree(child)
    template = copy_variant((seo.TEMPLATES_DIR / "hub.html").read_text(encoding="utf-8"), True, cfg.support_enabled)
    template = site_footer(template, now, disclosure, cfg.support_enabled,
        amazon_as_of_enabled=True,
        event_links=seo.event_pages.links(cfg), checker_enabled=cfg.checker["enabled"])
    template = product_pages.reader_template(template)
    template = template.replace("{{TABS}}", "")
    template = re.sub(r'{{DEALS}}.*?</main>', '{{DEALS}}</main>', template, count=1, flags=re.S)
    template = seo._fill(template, DROP_MINUTES=str(int(cfg.drop_minutes)))
    emitted = []
    sales = {}
    for deal in deals:
        key = hashlib.sha256(str(deal.get('dedupe_key') or '').encode()).hexdigest()[:8]
        path = (deal_paths or {}).get(key)
        at = verify.price_confirmed_at(deal)
        if (deal.get('status') != 'alive' or deal.get('price_unconfirmed') or deal.get('price_aged')
                or re.search(r'average|median|history|historical|\d+[ -]day|lowest|highest', seo._basis_label(deal), re.I)
                or not compare.positive(deal.get('pct_at_checkout')) or at is None or at > now
                or verify.price_unconfirmed(deal, cfg, now) or not verify.is_amazon_deal(deal)
                or not path or not (out / path.strip('/') / 'index.html').is_file()):
            continue
        asin = deal.get('asin')
        if isinstance(asin, str) and compare.ASIN.fullmatch(asin):
            hours = (cfg.keepa_show_max_unconfirmed_used_h if verify.is_used_deal(deal)
                     else cfg.keepa_show_max_unconfirmed_h)
            sales.setdefault(asin, (path, deal['pct_at_checkout'], iso(at + timedelta(hours=hours))))

    def write(path, heading, content, schema=None):
        head = STYLE + (seo._json_ld(schema) if schema and mode == "on" else "")
        html = seo._landing_page(template, heading + " | wowpriced", heading,
            "Compare current Amazon prices and evidenced cost per unit.", path, site_url, content,
            indexable=mode == "on", extra_head=head)
        seo._write_page(out, path, html)
        emitted.append(path)

    for term in terms:
        shown = rows[term["slug"]]
        if not shown:
            continue
        path = f'/compare/{term["slug"]}/'
        content = '<p><a href="/compare/">All product types</a></p><h2>Best value right now</h2>'
        measured = [r for r in shown if r["unit_price"] is not None]
        def per_unit(value):
            return unit_text(value, term["unit"], term.get("display_unit"))
        if measured:
            best, high = measured[0], max(r["unit_price"] for r in measured)
            label = 'Best value' if len(measured) == len(shown) else 'Best value among items that state their size'
            content += (f'<p class="best-value">{label}: {escape(best["title"])} at '
                        f'{escape(per_unit(best["unit_price"]))}'
                        + (f'; others shown go up to {escape(per_unit(high))}' if len(measured) > 1 else '') + '.</p>')
        content += (f'<p id="compare-empty" hidden>No current prices. Please check again later.</p>'
                    f'<ol class="compare-list" data-max-age-hours="{cfg.compare["max_price_age_h"]}">')
        schema_rows = []
        for position, row in enumerate(shown, 1):
            at = parse_iso(row["observed_at"])
            minutes = int((now - at).total_seconds() // 60)
            url = rewrite_affiliate(f'https://www.amazon.com/dp/{row["asin"]}', cfg.affiliate_amazon_tag)
            content += f'<li class="compare-row" id="{row["asin"]}">'
            if row["image"]:
                content += (f'<img src="https://m.media-amazon.com/images/I/{escape(row["image"])}" '
                            'width="100" height="100" loading="lazy" alt="Product photo" referrerpolicy="no-referrer">')
            content += (f'<h2>{escape(row["title"])}</h2><p class="detail-price">${row["price"]:.2f} USD</p>'
                        f'<p class="detail-checked"><time class="detail-checked" datetime="{escape(row["observed_at"])}">'
                        f'Checked {minutes} min ago</time> · Amazon price as of '
                        f'<time datetime="{escape(row["observed_at"])}">{escape(amazon_as_of.time_text(at))}</time></p>')
            if row["unit_price"] is not None:
                content += (f'<details title="{escape(row["quote"])}"><summary>'
                            f'{escape(per_unit(row["unit_price"]))}</summary>'
                            f'<blockquote>{escape(row["quote"])}</blockquote></details>')
            if row["asin"] in sales:
                deal_path, pct, until = sales[row["asin"]]
                content += (f'<p><a class="sale-badge" href="{escape(deal_path)}" data-confirmed-until="{escape(until)}">'
                            f'On sale now — {pct:g}% off</a></p>')
            content += f'<p><a class="getdeal" href="{escape(url)}" rel="sponsored noopener" target="_blank">View on Amazon</a></p>'
            content += '</li>'
            schema_rows.append({"@type": "ListItem", "position": position,
                                "name": row["title"], "url": site_url + path + '#' + row["asin"]})
        content += (f'</ol><p class="disclosure">{linked_disclosure(disclosure)}</p>'
                    f'<details><summary>Price and availability details</summary><p>{amazon_as_of.DISCLAIMER}</p></details>' + CLOCK)
        write(path, term["name"], content, {"@context": "https://schema.org", "@type": "ItemList",
                                           "itemListElement": schema_rows})
    index = [dict(name=t["name"], path=f'/compare/{t["slug"]}/' if t["slug"] in wanted else "",
                  words=re.findall(r'[a-z0-9]+', ' '.join([t["name"], *t["keywords"]]).lower())) for t in terms]
    content = ('<p>Compare everyday products by current price and cost per unit.</p>'
               '<label for="compare-search">Find a product type</label>'
               '<input id="compare-search" type="search" placeholder="Laundry pods, cables…">'
               '<p id="compare-status" role="status" aria-live="polite"></p><ul id="compare-results" class="compare-terms">')
    content += ''.join(f'<li><a href="{t["path"]}">{escape(t["name"])}</a></li>' if t["path"]
                       else f'<li>{escape(t["name"])} — Not covered yet</li>' for t in index)
    content += ('</ul><script id="compare-terms" type="application/json">'
                + json.dumps(index, separators=(',', ':')).replace('<', '\\u003c') + '</script>' + SEARCH)
    write('/compare/', 'Compare prices', content)
    return emitted
