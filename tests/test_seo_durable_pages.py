"""Durable-page indexing, unchanged shopper bytes, and one-time noindex receipts."""

import json
import re
from datetime import timedelta
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import indexnow, site
from tests.test_indexnow_receipts import HOST, client_for, env, send, stage
from tests.test_product_offer_pages import tree
from tests.test_publish_site import NOW, make_deal
from tests.test_seo import cfg, db, _render

FOLLOW = b'<meta name="robots" content="noindex,follow">\n'
ENDED = b'<meta name="robots" content="noindex">\n'


@pytest.mark.parametrize("setting,expected", [("", True), ("true", True), ("false", False)])
def test_config_default_and_shipped_switch(tmp_path, setting, expected):
    assert AppConfig().seo_deal_pages_indexable
    assert not load_config().seo_deal_pages_indexable
    path = tmp_path / "custom.yaml"
    path.write_text("seo:\n  deal_pages_indexable: " + setting if setting else "{}")
    assert load_config(path).seo_deal_pages_indexable is expected


@pytest.mark.parametrize("paginate", [False, True])
def test_only_deal_robots_and_sitemap_change_and_switch_on_restores_bytes(db, cfg, tmp_path,
                                                                        monkeypatch, paginate):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg.paywall_open_access = cfg.support_enabled = True
    cfg.traffic_hub_pagination_enabled = paginate
    rows = [make_deal(dedupe_key=f"durable-{i}", item=f"Offer {i}", category="electronics",
                      codes=[], conditions=[], image_url=f"https://img.example/{i}.jpg")
            for i in range(65)]
    ended = make_deal(dedupe_key="ended", status="dead", death="confirmed", died_at=NOW.isoformat())
    out = tmp_path / "site"
    on = _render(db, cfg, out, rows + [ended])["seo_audit"]
    before = tree(out)
    paths = db.meta_get("seo_deal_paths")
    cfg.seo_deal_pages_indexable = False
    off = _render(db, cfg, out, rows + [ended])["seo_audit"]
    after = tree(out)
    assert before.keys() == after.keys() and db.meta_get("seo_deal_paths") == paths
    for path, data in before.items():
        if path.startswith("deal/"):
            assert after[path].count(FOLLOW) == 1
            assert after[path].replace(FOLLOW, b"") == data.replace(ENDED, b"")
        elif path == "sitemap.xml":
            assert after[path] == re.sub(rb"  <url><loc>https://wowpriced.com/deal/.*?</url>\n", b"", data)
        else:
            assert after[path] == data, path
    assert b"https://wowpriced.com/deal/" not in after["sitemap.xml"]
    for path in ("/", "/deals/electronics/", "/stores/walmart/", "/deals/under-25/", "/best-deals-today/"):
        assert (HOST + path).encode() in after["sitemap.xml"]
    assert off["indexable_by_family"] == {**on["indexable_by_family"], "deal": 0}
    assert off["sitemap_urls"] == off["emitted_indexable"] == on["sitemap_urls"] - len(rows)
    assert all(off[k] == 0 for k in ("empty_indexable", "schema_mismatches", "broken_internal_links",
                                    "sitemap_missing", "sitemap_extra", "sitemap_duplicates"))
    cfg.seo_deal_pages_indexable = True
    _render(db, cfg, out, rows + [ended])
    assert tree(out) == before


@pytest.mark.parametrize("status", [200, 202])
def test_noindex_receipt_once_despite_churn_periodic_reminders_and_removal(env, status):
    db, cfg, _ = env
    cfg.seo_indexnow_resubmit_days = 7
    deal, hub = HOST + "/deal/offer-12345678/", HOST + "/deals/electronics/"
    pages = {deal: "original deal", hub: "original hub"}
    stage(db, cfg, "indexable", pages=pages)
    client, seen = client_for(status)
    with client:
        assert send(db, cfg, client, "indexable") == 0
        cfg.seo_deal_pages_indexable = False
        pages[deal] = FOLLOW.decode() + "original deal"
        changed = NOW + timedelta(hours=7)
        stage(db, cfg, "noindex", changed, pages)
        assert {url for url, row in db.indexnow_pages().items() if indexnow._ready(row, cfg, changed)} == {deal}
        seen.clear()
        assert send(db, cfg, client, "noindex", changed) == 0
        assert [json.loads(r.content)["urlList"] for r in seen if r.method == "POST"] == [[deal]]
        assert send(db, cfg, client, "noindex", changed) == 0
        received = db.indexnow_pages()[deal]["received_hash"]
        pages[deal] += " new price, renamed title, different related offers; ended"
        later = NOW + timedelta(days=8)
        stage(db, cfg, "churn", later, pages)
        seen.clear()
        assert send(db, cfg, client, "churn", later) == 0
        assert [json.loads(r.content)["urlList"] for r in seen if r.method == "POST"] == [[hub]]
        assert db.indexnow_pages()[deal]["received_hash"] == received
        indexnow._page_path(Path(cfg.site_dir), deal, cfg.custom_domain).unlink()
        stage(db, cfg, "removed", later + timedelta(hours=1), {hub: pages[hub]})
        seen.clear()
        assert send(db, cfg, client, "removed", later + timedelta(hours=1)) == 0
        assert not [r for r in seen if r.method == "POST"]
        # Re-enabling restores normal deal discovery; a later noindex transition
        # gets its own receipt after engines were told the page was indexable.
        cfg.seo_deal_pages_indexable = True
        stage(db, cfg, "restored", later + timedelta(hours=2), {deal: "back", hub: pages[hub]})
        assert send(db, cfg, client, "restored", later + timedelta(hours=2)) == 0
        cfg.seo_deal_pages_indexable = False
        stage(db, cfg, "off-again", later + timedelta(hours=3), pages)
        seen.clear()
        assert send(db, cfg, client, "off-again", later + timedelta(hours=3)) == 0
        assert [json.loads(r.content)["urlList"] for r in seen if r.method == "POST"] == [[deal]]


def test_noindex_failure_stays_pending_through_churn_and_database_restart(env):
    db, cfg, _ = env
    cfg.seo_deal_pages_indexable = False
    deal = HOST + "/deal/offer-12345678/"
    stage(db, cfg, pages={deal: FOLLOW.decode() + "offer"})
    client, seen = client_for(503)
    with client:
        assert send(db, cfg, client) == 1
        assert send(db, cfg, client) == 1
    assert len([r for r in seen if r.method == "POST"]) == 1
    row = db.indexnow_pages()[deal]
    assert row["received_at"] is None and row["pending_since"] is not None
    restored = DB(db.path)
    try:
        retry = NOW + timedelta(minutes=16)
        stage(restored, cfg, "retry", retry, {deal: FOLLOW.decode() + "changed offer"})
        assert restored.indexnow_pages()[deal]["content_hash"] == row["content_hash"]
        client, seen = client_for()
        with client:
            assert send(restored, cfg, client, "retry", retry) == 0
            stage(restored, cfg, "later", NOW + timedelta(days=30), {deal: FOLLOW.decode() + "changed again"})
            assert send(restored, cfg, client, "later", NOW + timedelta(days=30)) == 0
        assert len([r for r in seen if r.method == "POST"]) == 1
        assert restored.indexnow_pages()[deal]["pending_since"] is None
    finally:
        restored.close()


def test_site_check_visits_a_deal_page_from_deals_json_when_the_sitemap_has_none():
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    # Step 8 discovers the page, step 13 visits it; a live sitemap no longer lists deal pages.
    script = r'''
const assert = require("node:assert/strict"), fs = require("node:fs");
const source = fs.readFileSync("scripts/site_check.js", "utf8");
const code = "let dealPage; await step(8," + source.split("await step(8,")[1].split("await step(9,")[0] +
  source.slice(source.indexOf('  for (const [name, url] of [["deal", dealPage]'),
    source.indexOf("  // Include errors from every interaction"));
const run = new (Object.getPrototypeOf(async function(){}).constructor)("step", "readSite", "check", "page", "SITE", "SITE_URL", "device", "checkFooter", "doc", code);
const sitemap = '<urlset>' + ["/deals/electronics/", "/deals/pets/", "/deals/home-kitchen/", "/best-deals-today/"]
  .map(p => `<url><loc>https://wowpriced.com${p}</loc></url>`).join("") + '</urlset>';
(async () => {
  for (const deals of [[{key: "a"}, {status: "dead", deal_path: "/deal/ended-00000000/"},
    {status: "alive", deal_path: "/deal/first-12345678/"},
    {status: "alive", deal_path: "/deal/second-87654321/"}], []]) {
    const requests = [], problems = [];
    const read = async p => {
      const path = new URL(p, "https://wowpriced.com/").pathname;
      requests.push(path);
      const body = path === "/sitemap.xml" ? sitemap : path === "/robots.txt" ? "User-agent: *" :
        '<title>Public page</title><a href="https://store.test/product">Get deal</a>';
      return {status: 200, text: async () => body};
    };
    await run(async (n, name, action) => { try { await action(); } catch(e) { problems.push(e.message); } }, read,
      (ok, reason) => { if (!ok) throw new Error(reason); },
      {goto: async url => {const r = await read(url); return {status: () => r.status, text: r.text};},
        screenshot: async () => {}},
      "https://wowpriced.com/", new URL("https://wowpriced.com/"), "desktop", () => {}, {meta: {}, deals});
    if (deals.length) {
      assert.deepEqual(problems, []);
      assert.equal(requests.filter(p => p === "/deal/first-12345678/").length, 1);
      assert(!requests.includes("/deal/second-87654321/"));
      assert(!requests.includes("/deal/ended-00000000/"));
    } else {
      assert.deepEqual(problems, ["no deal page in sitemap or deals.json", "no deal page in sitemap or deals.json"]);
    }
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
'''
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["node", "-e", script], cwd=root, check=True, capture_output=True, text=True)
