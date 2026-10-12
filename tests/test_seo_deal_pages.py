"""Deal landing pages and the daily list: offline public data only."""

import hashlib
import json
import re
import shutil
import subprocess
from datetime import timedelta, timezone
from html import unescape

from dealradar.config import load_config
from dealradar.publish import seo
from dealradar.publish.site import render_site
from tests.test_publish_site import NOW, make_deal
from tests.test_seo import ROOT, cfg, db, _deals, _render


def _path(d):
    id8 = hashlib.sha256(d["dedupe_key"].encode()).hexdigest()[:8]
    return seo.deal_path(d["item"], id8)


def _page(out, path):
    return out / path.strip("/") / "index.html"


def test_slug_ascii_word_cut_and_hash():
    assert seo.deal_path("Café & Ninja™ Blender", "12345678") == "/deal/cafe-ninjatm-blender-12345678/"
    name = "a" * 29 + " " + "b" * 29 + " extra"
    slug = seo.deal_path(name, "12345678").split('/')[2][:-9]
    assert slug == "a" * 29 + "-" + "b" * 29 and len(slug) == 59
    assert seo.deal_path("x" * 100, "12345678") == "/deal/deal-12345678/"
    assert seo.deal_path("💸", "12345678") == "/deal/deal-12345678/"


def test_renamed_deal_keeps_url_in_a_fresh_output_directory(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    d = make_deal(codes=[])
    _render(db, cfg, tmp_path / "a", [d])
    _render(db, cfg, tmp_path / "b", [dict(d, item="New name")])
    page = _page(tmp_path / "b", _path(d))
    assert page.exists() and '<h1>New name</h1>' in page.read_text()
    assert not _page(tmp_path / "b", _path(dict(d, item="New name"))).exists()


def test_locked_pages_removed_but_url_remembered_across_rotation(db, cfg, tmp_path):
    out = tmp_path / "site"
    _render(db, cfg, out)
    paths = list((out / "deal").glob("*/index.html"))
    assert len(paths) == cfg.paywall_teaser_count
    public = json.loads((out / "deals.json").read_text())["deals"]
    for d in _deals():
        unlocked = next(r for r in public if r["key"].startswith(_path(d).rstrip('/').rsplit('-', 1)[-1]))
        assert _page(out, _path(d)).exists() == (not unlocked.get("locked"))
    _render(db, cfg, out)
    assert all(not p.exists() for p in paths)
    assert len(json.loads(db.meta_get("seo_deal_paths"))) == 4


def test_basis_label_names_who_stated_the_regular_price():
    label = seo._basis_label
    assert label({"credits": [{"name": "Keepa"}]}) == "90-day average on Amazon, tracked by Keepa"
    assert label({"credits": [{"name": "Keepa"}], "conditions": ["used"]}) == "new price tracked by Keepa"
    assert label({"credits": [{"name": "Slickdeals"}, {"name": "Keepa"}]}) == "as stated by Slickdeals"
    assert label({"credits": []}) == "as stated by the source"


def test_cap_uses_rank_order_and_logs_exclusions(db, cfg, tmp_path, caplog):
    cfg.paywall_open_access = True
    cfg.seo_deal_pages_max = 2
    with caplog.at_level("INFO", logger="dealradar.publish.seo"):
        _render(db, cfg, tmp_path)
    assert "2 live deal pages; 4 left out by seo.deal_pages_max" in caplog.text
    assert {_path(d).strip('/') for d in _deals()[:2]} == {
        str(p.parent.relative_to(tmp_path)) for p in (tmp_path / "deal").glob("*/index.html")}
    assert load_config(ROOT / "config.yaml").seo_deal_pages_max == 3000


def test_deal_page_facts_schema_disclosure_affiliate_and_related(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    cfg.affiliate_amazon_tag = "example-20"
    cfg.affiliate_disclosure = "Disclosure: affiliate links may earn a commission."
    d = make_deal(item="Ninja & Blender", canonical_url="https://www.amazon.com/dp/B012345678",
                  codes=[], category="kitchen", conditions=["open_box"], verify_tier="history-checked")
    related = [make_deal(dedupe_key=f"r{i}", item=f"Related {i}", category="home") for i in range(8)]
    _render(db, cfg, tmp_path, [d] + related + [make_deal(dedupe_key="other", category="pets")])
    html = _page(tmp_path, _path(d)).read_text()
    title = unescape(re.search(r'<title>(.*?)</title>', html).group(1))
    assert title == "Ninja & Blender — 80% off at Walmart | wowpriced"
    assert f'<link rel="canonical" href="https://wowpriced.com{_path(d)}">' in html
    assert '<h1>Ninja &amp; Blender</h1>' in html
    # The basis names who stated it (first credit), never a bare "list price".
    assert "Regular price: $50.00 (new-condition price, as stated by Slickdeals)" in html
    assert "list price" not in html
    assert "80% off · save $40.00" in html and "Condition: open box" in html
    assert "✓ price history: The price was checked against price history." in html
    assert "Oct 9, 2026</time> (UTC)" in html and "minutes ago" not in html
    assert "<img" not in html
    assert '<a class="getdeal" href="https://www.amazon.com/dp/B012345678?tag=example-20" rel="sponsored noopener">Get deal</a><p class="disclosure">Disclosure: affiliate links may earn a commission.</p>' in html
    product = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html).group(1))
    assert product == {"@context": "https://schema.org", "@type": "Product", "name": d["item"],
                       "offers": {"@type": "Offer", "price": "10.00", "priceCurrency": "USD",
                                  "availability": "https://schema.org/InStock",
                                  "url": "https://wowpriced.com" + _path(d)}}
    assert '<strong>$10.00</strong>' in html and "Store: Walmart" in html
    assert 'href="/deals/home-kitchen/"' in html
    assert html.count('<article class="card">') == 6
    # Related: the 6 same-hub deals after this one in key order (stable).
    key = lambda x: hashlib.sha256(x["dedupe_key"].encode()).hexdigest()[:16]
    ring = sorted(related, key=key)
    after = [r for r in ring if key(r) > key(d)] + [r for r in ring if key(r) < key(d)]
    assert all(f'href="{_path(r)}"' in html for r in after[:6])
    assert all(f'href="{_path(r)}"' not in html for r in after[6:])
    long = dict(d, item="Very long item " * 15)
    _render(db, cfg, tmp_path, [long])
    assert len(unescape(re.search(r'<title>(.*?)</title>', _page(tmp_path, _path(d)).read_text()).group(1))) <= 70


def test_steps_match_public_payload_and_credit_links(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    d = make_deal(stack_steps=["Clip the coupon", "Apply SAVE10"], category="electronics")
    _render(db, cfg, tmp_path, [d])
    html = _page(tmp_path, _path(d)).read_text()
    payload = json.loads((tmp_path / "deals.json").read_text())["deals"][0]
    assert f'href="{payload["url"]}" rel="sponsored noopener">Get deal' in html
    assert "How to get this price" in html and "<li>Clip the coupon</li>" in html
    assert "<code>SAVE10</code>" in html and "Slickdeals</a>" in html
    assert "✓ 2+ sources: Two or more independent sources confirmed this price." in html


def test_ended_page_retained_from_database_until_row_deleted(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    d = make_deal(codes=[], category="electronics")
    db.insert_deal(dedupe_key=d["dedupe_key"], store=d["store"], item=d["item"],
                   normalized_item="lego", canonical_url=d["canonical_url"], payload=d,
                   final_price=10, pct_at_checkout=80, abs_savings=40, online_only="true",
                   expires_at="", now=NOW)
    _render(db, cfg, tmp_path, [d])
    path = _page(tmp_path, _path(d))
    db.update_deal(d["dedupe_key"], status="dead", payload=dict(d, died_at=NOW.isoformat()))
    # No longer on the wall, but the row (and previously public URL) still exists.
    _render(db, cfg, tmp_path, [], now=NOW + timedelta(days=2))
    html = path.read_text()
    assert "This deal has ended" in html and '<meta name="robots" content="noindex">' in html
    assert '>Get deal</a>' not in html and 'href="/deals/electronics/"' in html
    assert _path(d) not in (tmp_path / "sitemap.xml").read_text()
    assert "offers" not in json.loads(re.search(r'application/ld\+json">(.*?)</script>', html).group(1))
    db.conn.execute("DELETE FROM deals WHERE dedupe_key=?", (d["dedupe_key"],))
    db.conn.commit()
    _render(db, cfg, tmp_path, [])
    assert not path.parent.exists() and db.meta_get("seo_deal_paths") == "{}"


def test_best_today_order_date_links_count_and_repeat_bytes(db, cfg, tmp_path):
    cfg.support_enabled = True
    deals = [make_deal(dedupe_key=f"k{i}", item=f"Item {i}", codes=[], category="electronics")
             for i in range(137)]
    date = NOW.astimezone(timezone(timedelta(hours=-10)))
    _render(db, cfg, tmp_path, deals, now=date)
    best = _page(tmp_path, "/best-deals-today/")
    html = best.read_text()
    assert '<title>Best deals today (October 9, 2026) | wowpriced</title>' in html
    assert "100+ live deals" in html and html.count('<article class="card">') == 30
    assert re.findall(r'<h3 class="title"><a href="([^"]+)"', html) == [_path(d) for d in deals[:30]]
    hub = (tmp_path / "deals/electronics/index.html").read_text()
    assert all(f'<h3 class="title"><a href="{_path(d)}">' in hub for d in deals[:60])
    for page in [html, _page(tmp_path, _path(deals[0])).read_text()]:
        assert "Join free" in page and "free trial" not in page and "members only" not in page
    # Deal bytes depend on data, not on the run time; best-today changes at UTC midnight.
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in (tmp_path / 'deal').rglob('*.html')}
    sitemap = (tmp_path / 'sitemap.xml').read_bytes()
    _render(db, cfg, tmp_path, deals, now=NOW + timedelta(hours=4))
    assert best.read_text() == html and (tmp_path / 'sitemap.xml').read_bytes() == sitemap
    assert before == {str(p.relative_to(tmp_path)): p.read_bytes() for p in (tmp_path / 'deal').rglob('*.html')}
    _render(db, cfg, tmp_path, deals, now=NOW + timedelta(days=1))
    assert "October 10, 2026" in best.read_text()


def test_price_since_is_lastmod_and_new_page_indexnow_once(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    d = make_deal(price_since="2026-10-09T13:00:00+00:00", codes=[])
    _render(db, cfg, tmp_path, [d])
    sitemap = (tmp_path / "sitemap.xml").read_text()
    assert f'<loc>https://wowpriced.com{_path(d)}</loc><lastmod>{d["price_since"]}</lastmod>' in sitemap
    due = lambda: json.loads(seo.due_list_path(tmp_path).read_text())
    assert "https://wowpriced.com" + _path(d) in due()
    assert "https://wowpriced.com/best-deals-today/" in due()
    _render(db, cfg, tmp_path, [d])
    assert due() == []


def test_1600_deal_publish_file_count(db, tmp_path):
    cfg = load_config(ROOT / "config.yaml")
    deals = [make_deal(dedupe_key=f"large{i}", item=f"Deal {i}", codes=[])
             for i in range(1600)]
    render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=deals)
    assert sum(p.is_file() for p in (tmp_path / "site").rglob('*')) == 1632


def test_site_check_reads_deal_and_daily_page_and_rejects_missing_get_deal():
    if not shutil.which("node"):
        import pytest
        pytest.skip("node unavailable")
    # Exercise step 8 with offline HTTP responses; no retailer URL is opened.
    script = r'''
const assert = require("node:assert/strict"), fs = require("node:fs");
const source = fs.readFileSync("scripts/site_check.js", "utf8");
const code = "await step(8," + source.split("await step(8,")[1].split("await step(9,")[0];
const run = new (Object.getPrototypeOf(async function(){}).constructor)("step", "readSite", "check", code);
const sitemap = '<urlset>' + ["/deal/first-12345678/", "/deal/second-87654321/",
  "/deals/electronics/", "/deals/pets/", "/deals/home-kitchen/", "/best-deals-today/"]
  .map(p => `<url><loc>https://wowpriced.com${p}</loc></url>`).join("") + '</urlset>';
(async () => {
  for (const missing of ["", "/deal/first-12345678/", "/best-deals-today/"]) {
    const requests = [];
    const read = async p => {
      const path = new URL(p, "https://wowpriced.com/").pathname;
      requests.push(path);
      const body = path === "/sitemap.xml" ? sitemap : path === "/robots.txt" ? "User-agent: *" :
        '<title>Public page</title>' + (path === missing ? "" :
          '<a class="getdeal" href="https://store.test/product" rel="sponsored noopener">Get deal</a>');
      return {status: 200, text: async () => body};
    };
    const execute = () => run(async (n, name, action) => action(), read,
      (ok, reason) => { if (!ok) throw new Error(reason); });
    if (missing) await assert.rejects(execute, /missing Get deal link/);
    else await execute();
    assert(requests.includes("/deal/first-12345678/"));
    assert(requests.includes("/best-deals-today/"));
    assert(!requests.includes("/deal/second-87654321/"));
    assert(!requests.some(p => p.includes("store.test")));
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
'''
    subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)


def test_small_print_names_wowpriced_once_the_amazon_tag_is_set(db, cfg, tmp_path):
    pages = ("about.html", "careers.html", "contact.html", "hacks.html", "how-it-works.html",
             "privacy.html", "deals/electronics/index.html", "best-deals-today/index.html")
    cfg.paywall_open_access = True
    _render(db, cfg, tmp_path / "off")
    for name in pages:  # no Store ID: the source-only line, unchanged
        html = (tmp_path / "off" / name).read_text()
        assert "source's affiliate links" in html and "As an Amazon Associate" not in html, name
    cfg.affiliate_amazon_tag = "example-20"
    _render(db, cfg, tmp_path / "on")
    for name in pages:
        html = (tmp_path / "on" / name).read_text()
        assert "source's affiliate links" not in html, name
        assert "As an Amazon Associate, wowpriced earns from qualifying purchases." in html, name
