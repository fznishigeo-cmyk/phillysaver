"""Store and price landing pages: public facts and offline site checks."""

import json
import re
import shutil
import subprocess
from datetime import timedelta, timezone

import pytest
import yaml

from dealradar.config import load_config
from dealradar.publish import seo
from tests.test_publish_site import NOW, make_deal
from tests.test_seo import ROOT, cfg, db, _render, _receive
from tests.test_seo_deal_pages import _page, _path


def _rows(store, n, **overrides):
    return [make_deal(dedupe_key=f"{store}|{i}", item=f"{store} item {i}", store=store,
                      codes=[], category="electronics", **overrides) for i in range(n)]


def _store_paths(out):
    return {f"/stores/{p.name}/" for p in (out / "stores").glob("*")}


def _sitemap_paths(out):
    return re.findall(r"<loc>https://wowpriced.com([^<]+)</loc>", (out / "sitemap.xml").read_text())


def test_store_slugs_and_case_dot_com_folding(db, cfg, tmp_path):
    assert seo.store_slug("Macy's") == "macys"
    assert seo.store_slug(" Macy’s.COM ") == "macys"
    assert seo.store_slug("Amazon.com") == seo.store_slug("AMAZON") == "amazon"
    assert seo.store_slug("Café & Home") == "cafe-home"
    cfg.support_enabled = True
    deals = _rows("Amazon.com", 5) + _rows("Amazon", 5) + _rows("AMAZON.COM", 5)
    _render(db, cfg, tmp_path, deals + _rows("Macy's", 15))
    assert _store_paths(tmp_path) == {"/stores/amazon/", "/stores/macys/"}
    html = _page(tmp_path, "/stores/amazon/").read_text()
    assert '<h1>Amazon deals today</h1>' in html
    assert "15 live deals right now, median 80% off." in html
    assert re.findall(r'<h3 class="title"><a href="([^"]+)"', html) == [_path(d) for d in deals]


def test_config_defaults_and_custom_keys(tmp_path):
    defaults = load_config(ROOT / "config.yaml")
    assert defaults.seo_store_page_min_deals == 15
    assert defaults.seo_store_pages_max == 12
    assert defaults.seo_price_pages == [10, 25, 50]
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"seo": {"store_page_min_deals": 21,
                                          "store_pages_max": 2, "price_pages": [50, 5, 5]}}))
    custom = load_config(path)
    assert custom.seo_store_page_min_deals == 21 and custom.seo_store_pages_max == 2
    assert custom.seo_price_pages == [5, 50]
    path.write_text("{}")
    fallback = load_config(path)
    assert fallback.seo_store_page_min_deals == 15 and fallback.seo_store_pages_max == 12
    assert fallback.seo_price_pages == [10, 25, 50]


def test_store_floor_cap_and_stable_tie_order(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    deals = (_rows("Small", 14) + _rows("Tie Z", 15) + _rows("Tie A", 15)
             + _rows("Largest", 16))
    cfg.seo_store_pages_max = 2
    _render(db, cfg, tmp_path, deals)
    assert _store_paths(tmp_path) == {"/stores/largest/", "/stores/tie-a/"}
    paths = [p for p in _sitemap_paths(tmp_path) if p.startswith("/stores/")]
    assert paths == ["/stores/largest/", "/stores/tie-a/"]
    cfg.seo_store_pages_max = 12
    _render(db, cfg, tmp_path, deals)
    assert _store_paths(tmp_path) == {"/stores/largest/", "/stores/tie-a/", "/stores/tie-z/"}
    cfg.seo_store_page_min_deals = 16
    _render(db, cfg, tmp_path, deals)
    assert _store_paths(tmp_path) == {"/stores/largest/"}
    cfg.seo_store_pages_max = 0
    _render(db, cfg, tmp_path, deals)
    assert _store_paths(tmp_path) == set()


@pytest.mark.parametrize("n, count", [(15, "15"), (99, "99"), (100, "100+"), (137, "100+")])
def test_store_content_count_median_wall_order_and_support(db, cfg, tmp_path, n, count):
    cfg.support_enabled = True
    cfg.affiliate_amazon_tag = "example-20"
    cfg.affiliate_disclosure = load_config().affiliate_disclosure
    deals = _rows("Amazon", n)
    for i, d in enumerate(deals):
        d["pct_at_checkout"] = 70 if i < n // 2 else 91
        d["canonical_url"] = "https://www.amazon.com/dp/B012345678"
    _render(db, cfg, tmp_path, deals + _rows("Walmart", 15))
    html = _page(tmp_path, "/stores/amazon/").read_text()
    assert '<title>Amazon deals today | wowpriced</title>' in html
    assert '<h1>Amazon deals today</h1>' in html
    median = 81 if n % 2 == 0 else 91
    assert f"{count} live deals right now, median {median}% off." in html
    assert '<link rel="canonical" href="https://wowpriced.com/stores/amazon/">' in html
    assert 'name="robots" content="noindex"' not in html and '<img' not in html
    assert re.findall(r'<h3 class="title"><a href="([^"]+)"', html) == [_path(d) for d in deals[:60]]
    assert 'href="https://www.amazon.com/dp/B012345678?tag=example-20"' in html
    assert "As an Amazon Associate, wowpriced earns from qualifying purchases." in html
    assert "Be first to get our deals email" in html and "free trial" not in html and "members only" not in html
    browse = html.split('<h2>More categories</h2>')[1].split('</main>')[0]
    assert '<strong>Stores</strong>: <a href="/stores/walmart/">Walmart</a>' in browse
    assert 'href="/stores/amazon/"' not in browse
    assert '<strong>By price</strong>' in browse
    for _label, _group, slug in seo.CATGROUPS:
        assert f'href="{seo.hub_href(slug)}"' in browse
    for path in ["/deals/electronics/", _path(deals[0]), "/best-deals-today/"]:
        page = _page(tmp_path, path).read_text()
        browse = page.split('<h2>More categories</h2>')[1].split('</main>')[0]
        assert '<strong>Stores</strong>' in browse and 'href="/stores/amazon/"' in browse
        assert '<strong>By price</strong>' in browse and 'href="/deals/under-25/"' in browse


def test_price_inclusive_filter_floor_and_card_limit(db, cfg, tmp_path):
    cfg.support_enabled = True
    low = _rows("Low", 9, final_price=9.99)
    edges = [make_deal(dedupe_key=f"edge-{i}", item=f"Edge {i}", codes=[], final_price=price)
             for i, price in enumerate([10, 10.01, 25, 25.01, 50, 50.01, None])]
    deals = low + edges + _rows("Twenty", 65, final_price=20)
    _render(db, cfg, tmp_path, deals)
    for n in [10, 25, 50]:
        html = _page(tmp_path, f"/deals/under-{n}/").read_text()
        selected = [d for d in deals if d["final_price"] is not None and d["final_price"] <= n]
        assert f'<title>Deals under ${n} today | wowpriced</title>' in html
        assert f'<h1>Deals under ${n} today</h1>' in html
        assert f'<link rel="canonical" href="https://wowpriced.com/deals/under-{n}/">' in html
        assert re.findall(r'<h3 class="title"><a href="([^"]+)"', html) == [_path(d) for d in selected[:60]]
        assert "Be first to get our deals email" in html and "members only" not in html and "free trial" not in html
    _render(db, cfg, tmp_path, low)
    assert not list((tmp_path / "deals").glob("under-*"))
    assert not any(p.startswith("/deals/under-") for p in _sitemap_paths(tmp_path))


def test_locked_and_dead_deals_do_not_count_or_leak(db, cfg, tmp_path):
    deals = _rows("Walmart", 20) + _rows("Dead", 15, status="dead")
    _render(db, cfg, tmp_path, deals)
    assert _store_paths(tmp_path) == set()
    assert not list((tmp_path / "deals").glob("under-*"))
    cfg.paywall_open_access = True
    _render(db, cfg, tmp_path, deals)
    assert _store_paths(tmp_path) == {"/stores/walmart/"}
    assert _page(tmp_path, "/deals/under-10/").read_text().count('<article class="card">') == 20
    assert "Dead item" not in _page(tmp_path, "/deals/under-10/").read_text()


def test_price_slug_collision_asserted_even_below_floor(cfg, monkeypatch):
    assert {f"under-{n}" for n in cfg.seo_price_pages}.isdisjoint(
        slug for _label, _group, slug in seo.CATGROUPS)
    monkeypatch.setattr(seo, "CATGROUPS", seo.CATGROUPS + (("Collision", ["other"], "under-25"),))
    with pytest.raises(AssertionError, match="collides with category hub: under-25"):
        seo._price_pages(cfg, [])


def test_sitemap_indexnow_stable_bytes_and_removal(db, cfg, tmp_path):
    cfg.support_enabled = True
    deals = _rows("Walmart", 20, price_since="2026-10-09T13:00:00+00:00")
    # The newest deal is outside the top 60 and has a different UTC offset.
    deals += _rows("Amazon", 61)
    deals[-1]["first_seen_at"] = "2026-10-09T09:00:00-04:00"
    out = tmp_path / "site"
    local_time = NOW.astimezone(timezone(timedelta(hours=-10)))
    _render(db, cfg, out, deals, now=local_time)
    paths = ["/stores/walmart/", "/stores/amazon/", "/deals/under-10/",
             "/deals/under-25/", "/deals/under-50/"]
    sitemap = (out / "sitemap.xml").read_text()
    due = json.loads(seo.due_list_path(out).read_text())
    for path in paths:
        lastmod = NOW.isoformat()  # baseline observed at publish, not inferred from sightings
        assert f'<loc>https://wowpriced.com{path}</loc><lastmod>{lastmod}</lastmod>' in sitemap
        assert "https://wowpriced.com" + path in due
    _receive(db, due)
    snapshots = {p: _page(out, p).read_bytes() for p in paths}
    _render(db, cfg, out, deals, now=NOW + timedelta(hours=4))
    assert snapshots == {p: _page(out, p).read_bytes() for p in paths}
    assert (out / "sitemap.xml").read_text() == sitemap
    assert json.loads(seo.due_list_path(out).read_text()) == []
    _render(db, cfg, out, deals, now=NOW + timedelta(days=1))
    assert snapshots == {p: _page(out, p).read_bytes() for p in paths}
    assert (out / "sitemap.xml").read_text() == sitemap
    # Falling below the store floor removes the folder and all navigation.
    _render(db, cfg, out, deals[:14])
    assert _store_paths(out) == set()
    assert not any(p.startswith("/stores/") for p in _sitemap_paths(out))
    assert '<strong>Stores</strong>' not in _page(out, "/best-deals-today/").read_text()
    # Removed pages remain pending for a deployed removal notification; the
    # last successful receipt still throttles this deliberately earlier clock.
    removed = [r for url, r in db.indexnow_pages().items() if "/stores/" in url]
    assert removed and all(r["removed"] and r["pending_since"] for r in removed)
    cfg.seo_price_pages = []
    _render(db, cfg, out, deals[:14])
    assert not list((out / "deals").glob("under-*"))
    assert '<strong>By price</strong>' not in _page(out, "/best-deals-today/").read_text()
    assert _page(out, "/deals/electronics/").exists()


def test_site_check_selects_real_hubs_first_store_and_under_25():
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    script = r'''
const assert = require("node:assert/strict"), fs = require("node:fs");
const source = fs.readFileSync("scripts/site_check.js", "utf8");
const code = "await step(8," + source.split("await step(8,")[1].split("await step(9,")[0];
const run = new (Object.getPrototypeOf(async function(){}).constructor)("step", "readSite", "check", "checkFooter", "doc", code);
(async () => {
  for (const listed of [false, true]) {
    for (const failure of ["", "store-http", "store-title", "price-http", "price-title"]) {
      if (!listed && failure) continue;
      const paths = ["/deals/under-10/", "/deals/under-50/", "/deals/electronics/", "/deals/pets/",
        "/deals/home-kitchen/", "/deals/other/", "/deal/first-12345678/", "/best-deals-today/"];
      if (listed) paths.unshift("/deals/under-25/", "/stores/amazon/", "/stores/walmart/");
      const requests = [];
      const read = async u => {
        const path = new URL(u, "https://wowpriced.com/").pathname;
        requests.push(path);
        const selected = failure.startsWith("store") ? path === "/stores/amazon/" : path === "/deals/under-25/";
        const body = path === "/sitemap.xml" ? '<urlset>' + paths.map(p =>
          `<url><loc>https://wowpriced.com${p}</loc></url>`).join("") + '</urlset>' :
          path === "/robots.txt" ? "User-agent: *" :
          (selected && failure.endsWith("title") ? "" : "<title>Public page</title>") +
          '<a href="https://store.test/product">Get deal</a>';
        return {status: selected && failure.endsWith("http") ? 503 : 200, text: async () => body};
      };
      const execute = () => run(async (n, name, action) => action(), read,
        (ok, reason) => { if (!ok) throw new Error(reason); }, () => {}, {meta:{}});
      if (failure) await assert.rejects(execute, /HTTP 503|missing title/);
      else await execute();
      for (const hub of ["electronics", "pets", "home-kitchen"]) assert(requests.includes(`/deals/${hub}/`));
      assert(!requests.includes("/deals/other/"));
      assert(!requests.includes("/deals/under-10/") && !requests.includes("/deals/under-50/"));
      assert.equal(requests.includes("/stores/amazon/"), listed);
      assert.equal(requests.includes("/deals/under-25/"), listed);
      assert(!requests.includes("/stores/walmart/"));
      assert(!requests.some(p => p.includes("store.test")));
      // Two walks plus deals.json and each home/data request stay under 40,
      // even accounting for Cloudflare's redirects from .html page URLs.
      const redirects = requests.filter(p => p.endsWith(".html")).length;
      assert(2 * (requests.length + redirects) + 5 <= 40);
    }
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
'''
    subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)


def test_store_spellings_share_one_page_and_never_crash():
    from dealradar.config import AppConfig
    cfg = AppConfig(seo_store_page_min_deals=2)
    live = [{"store": s, "final_price": 5.0} for s in ("Macy's", "Macys", "macys.com", "Target")]
    pages = seo._store_pages(cfg, live)
    assert list(pages) == ["/stores/macys/"]
    assert pages["/stores/macys/"][0] == "Macy's" and len(pages["/stores/macys/"][1]) == 3
