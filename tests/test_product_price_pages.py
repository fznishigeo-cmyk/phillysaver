"""Permanent price histories: offline gates, lifecycle, bytes and receipts."""

import hashlib
import json
import re
import shutil
import subprocess
from datetime import timedelta, timezone
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import indexnow, product_pages, seo, seo_audit, site
from dealradar.savings import price_verdict
from dealradar.util import iso
from tests.test_product_entities import insert
from tests.test_indexnow_receipts import env, client_for, send, HOST
from tests.test_product_offer_pages import tree
from tests.test_publish_site import NOW
from tests.test_seo_deal_pages import _page, _path
from tests.test_e2e_offline import env as pipeline_env, test_rerun_is_idempotent as check_pipeline_rerun


@pytest.fixture
def db():
    database = DB(":memory:")
    yield database
    database.close()


@pytest.fixture
def cfg():
    return AppConfig(custom_domain="wowpriced.com", paywall_open_access=True,
                     seo_product_pages_enabled=True, seo_hub_min_items=1,
                     affiliate_amazon_tag="example-20")


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)


def seed(db, count=3, *, checks=6, span=7):
    rows = [insert(db, f"product-{i}", asin=f"B{i:09d}", store="Amazon", item=f"Kitchen Widget {i}",
                   category="kitchen", canonical_url=f"https://www.amazon.com/dp/B{i:09d}",
                   image_url=f"https://m.media-amazon.com/images/I/{i}.jpg", price_rechecked_at=iso(NOW))
            for i in range(count)]
    for d in rows:
        for i in range(checks):
            db.record_price(d["dedupe_key"], d["store"], 10 + i,
                            NOW - timedelta(days=span * i / max(1, checks - 1)), f"own-{i}")
    return rows


def prepare(db, cfg, rows, now=NOW, public=None):
    return product_pages.prepare(db, cfg, now, public if public is not None else
                                 [site._serialize_deal(d, now, cfg=cfg) for d in rows], rows)


def gates(report):
    # Task 116 gate assertions; task 118 report fields have separate coverage.
    return {k: report[k] for k in ("eligible", "indexable", "new_today", "noindex")}


def test_config_ships_off_and_all_knobs_load(tmp_path):
    assert not AppConfig().seo_product_pages_enabled
    assert load_config().seo_product_pages_enabled  # operator switched it on 2026-10-15
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    assert not load_config(path).seo_product_pages_enabled
    path.write_text("""seo:
  product_pages:
    enabled: true
    min_checks: 9
    min_span_days: 8
    max_new_indexable_per_day: 2
    max_indexable: 10
    lower_ratio: 0.9
    higher_ratio: 1.2
""")
    cfg = load_config(path)
    assert cfg.seo_product_pages_enabled
    assert [getattr(cfg, "seo_product_pages_" + name) for name in
            ("min_checks", "min_span_days", "max_new_indexable_per_day", "max_indexable",
             "lower_ratio", "higher_ratio")] == [9, 8, 2, 10, 0.9, 1.2]


@pytest.mark.parametrize("mode,expected", [
    ("membership", "c9f95cbd0ac30b4aa6a6188b9d969d2885e5266c6a6fb91a04bd76d965ffa84f"),
    ("open", "3925a48d517d29c160650745a3b673db0c14054fe4af0bb96ddbc6bdca0cf414"),
    ("support", "c22d02d6f26ba1ba99dad3d2a2d7b2795e9609c46bd12a8d0acca1cf0a2fc050"),
    ("shipped", "ce2cf599f653dbee3a74e1794a5a52793319125dcc292f092316fedbbfb810ae"),
])
def test_off_every_published_byte_matches_untouched_head(db, tmp_path, mode, expected):
    cfg = load_config() if mode == "shipped" else AppConfig(custom_domain="wowpriced.com")
    cfg.amazon_history = "show"  # predates task 121b
    cfg.front_page.get("checkout_price_deals", {})["enabled"] = False  # predates task 121b
    cfg.photoless_last = False  # predates photos-first
    if mode != "shipped":
        cfg.support_enabled = mode == "support"
        cfg.paywall_open_access = mode == "open"
    cfg.paywall_teaser_count = 1
    cfg.seo_product_pages_enabled = False  # predates task 118
    rows, out = seed(db), tmp_path / "site"
    site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
    digest = hashlib.sha256()
    for name, data in sorted(tree(out).items()):
        digest.update(name.encode() + b"\0" + data)
    assert digest.hexdigest() == expected
    assert db.meta_get(product_pages.META) is None


@pytest.mark.parametrize("checks,span,eligible", [(5, 7, 0), (6, 6, 0), (6, 7, 1),
                                                (6, 7 - 1 / 86400, 0), (7, 8, 1)])
def test_gate_boundaries(db, cfg, checks, span, eligible):
    pages, _, report = prepare(db, cfg, seed(db, 1, checks=checks, span=span))
    assert gates(report) == {"eligible": eligible, "indexable": eligible, "new_today": eligible,
                      "noindex": 1 - eligible}
    assert next(iter(pages.values()))["indexable"] is bool(eligible)


def test_daily_cap_total_cap_utc_boundary_and_idempotent_retry(db, cfg):
    cfg.seo_product_pages_max_new_indexable_per_day = 2
    cfg.seo_product_pages_max_indexable = 3
    rows = seed(db, 5)
    pages, paths, report = prepare(db, cfg, rows)
    assert gates(report) == {"eligible": 5, "indexable": 2, "new_today": 2, "noindex": 3}
    before = db.meta_get(product_pages.META)
    assert prepare(db, cfg, rows) == (pages, paths, report)
    assert db.meta_get(product_pages.META) == before
    # Offset input still uses UTC, with no reset at its local midnight.
    local = NOW.astimezone(timezone(timedelta(hours=12)))
    assert prepare(db, cfg, rows, local)[2] == report
    later = NOW.replace(hour=23, minute=59, second=59)
    assert prepare(db, cfg, rows, later)[2]["new_today"] == 2
    tomorrow = later + timedelta(seconds=1)
    after = prepare(db, cfg, rows, tomorrow)[2]
    assert gates(after) == {"eligible": 5, "indexable": 3, "new_today": 1, "noindex": 2}
    assert prepare(db, cfg, rows, tomorrow + timedelta(days=1))[2]["new_today"] == 0


@pytest.mark.parametrize("daily,total", [(0, 2000), (50, 0)])
def test_zero_caps_keep_all_pages_noindex(db, cfg, daily, total):
    cfg.seo_product_pages_max_new_indexable_per_day = daily
    cfg.seo_product_pages_max_indexable = total
    assert gates(prepare(db, cfg, seed(db, 1))[2]) == {
        "eligible": 1, "indexable": 0, "new_today": 0, "noindex": 1}


def test_sticky_thirty_days_since_last_qualifying_not_first_admission(db, cfg):
    rows = seed(db, 1)
    prepare(db, cfg, rows)
    prepare(db, cfg, rows, NOW + timedelta(days=5))
    # A sparse/bad day is not an indexing reversal.
    db.conn.execute("DELETE FROM price_history")
    db.conn.commit()
    assert prepare(db, cfg, [], NOW + timedelta(days=35))[2]["indexable"] == 1
    assert gates(prepare(db, cfg, [], NOW + timedelta(days=35, seconds=1))[2]) == {
        "eligible": 0, "indexable": 0, "new_today": 0, "noindex": 1}


def test_requires_a_published_deal_in_last_ninety_days(db, cfg):
    rows = seed(db, 1)
    prepare(db, cfg, rows)
    later = NOW + timedelta(days=91)
    for i in range(6):
        db.record_price(rows[0]["dedupe_key"], "Amazon", 20, later - timedelta(days=i * 2), f"later-{i}")
    pages, _, report = prepare(db, cfg, [], later)
    assert len(next(iter(pages.values()))["checks"]) == 12  # includes lifetime checks
    assert gates(report) == {"eligible": 0, "indexable": 0, "new_today": 0, "noindex": 1}


@pytest.mark.parametrize("price,median,expected", [(94, 100, "Lower than usual"),
    (95, 100, "About usual"), (105, 100, "About usual"), (106, 100, "Higher than usual"),
    (3.99, 3.8, "About usual"), (15.77, 16.6, "About usual"),
    (3.23, (3.1 + 3.7) / 2, "About usual"),
    (0, 0, "About usual"), (1, 0, "Higher than usual"), (None, 100, ""),
    (10, None, ""), (float("nan"), 100, ""), (10, float("inf"), ""), (-1, 10, "")])
def test_verdict_thresholds_and_invalid_prices(price, median, expected):
    assert price_verdict(price, median) == expected
    assert price_verdict(94, 100, 0.9, 1.2) == "About usual"


def test_render_canonical_sitemap_schema_links_credits_photo_and_rerun(db, cfg, tmp_path):
    rows = seed(db, 2)
    # One rich and one thin history; both get pages but only one is indexed.
    db.conn.execute("DELETE FROM price_history WHERE canonical_key='product-1'")
    db.conn.commit()
    out = tmp_path / "site"
    result = site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
    assert gates(result["product_pages"]) == {"eligible": 1, "indexable": 1, "new_today": 1, "noindex": 1}
    public = result["public_deals"]
    for d in public:
        html = _page(out, d["product_path"]).read_text()
        assert f'<link rel="canonical" href="https://wowpriced.com{d["product_path"]}">' in html
        assert '"@type": "Product"' in html and '"@type": "Offer"' in html
        product = seo_audit.Page(html, d["product_path"]).products()[0]
        assert not {"aggregateRating", "review", "reviews"} & set(product)
        assert set(product["offers"]) == {"@type", "price", "priceCurrency", "availability", "url"}
        assert 'target="_blank" rel="sponsored noopener" data-deal-outbound data-store="Amazon"' in html
        assert '?tag=example-20' in html and '<h2>Deals we found</h2>' in html
        assert 'src="https://m.media-amazon.com/images/I/' in html
        assert 'href="https://slickdeals.net/f/1" rel="noopener">Slickdeals</a>' in html
        assert "keepa" not in html.lower()
        assert f'href="{d["product_path"]}">Price history</a>' in _page(out, d["deal_path"]).read_text()
        assert f'href="{d["product_path"]}">Price history</a>' in (out / "index.html").read_text()
    rich = next(d for d in public if d["dedupe_key"] == "product-0")
    thin = next(d for d in public if d["dedupe_key"] == "product-1")
    sitemap = (out / "sitemap-products.xml").read_text()
    assert rich["product_path"] in sitemap and thin["product_path"] not in sitemap
    assert 'content="index,follow"' in _page(out, rich["product_path"]).read_text()
    assert 'content="noindex,follow"' in _page(out, thin["product_path"]).read_text()
    hub = _page(out, "/deals/home-kitchen/").read_text()
    section = hub.split('<h2>Price history for popular items</h2>')[1].split('</ul>')[0]
    assert rich["product_path"] in section and thin["product_path"] not in section
    assert all(result["seo_audit"][k] == 0 for k in ("empty_indexable", "schema_mismatches",
        "broken_internal_links", "sitemap_missing", "sitemap_extra", "sitemap_duplicates"))
    before, state = tree(out), db.meta_get(product_pages.META)
    site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
    assert tree(out) == before and db.meta_get(product_pages.META) == state


def test_page_and_history_survive_deletion_and_rename_without_a_live_button(db, cfg, tmp_path):
    rows = seed(db, 1)
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path / "first", deals=rows)
    path = result["public_deals"][0]["product_path"]
    # A product's facts/path survive deletion of its ended deal row.
    db.conn.execute("DELETE FROM deals")
    db.conn.commit()
    out = tmp_path / "later"
    result = site.render_site(db, cfg, NOW + timedelta(days=1), out_dir=out, deals=[])
    html = _page(out, path).read_text()
    assert 'Our price checks, last 90 days' in html and ' · 6 checks' in html
    assert '<h1>Kitchen Widget 0</h1>' in html
    assert 'class="getdeal"' not in html and '"offers"' not in html
    assert 'Browse <a href="/deals/home-kitchen/">' in html
    assert path in (out / "sitemap-products.xml").read_text()
    assert result["seo_audit"]["schema_mismatches"] == result["seo_audit"]["empty_indexable"] == 0
    assert seo_audit.Page(html, path).schema_mismatch() is False


def test_stable_identifiers_only_and_exact_conditions_never_mix(db, cfg):
    a = insert(db, "amazon-a", asin="B012345678")
    b = insert(db, "amazon-b", asin="B012345678")
    used = insert(db, "amazon-used", asin="B012345678", conditions=["used"])
    sku = insert(db, "bestbuy", store="Best Buy", bestbuy_sku="1234567")
    vague = insert(db, "vague", item="Unknown item", brand="")
    pages, paths, _ = prepare(db, cfg, [a, b, used, sku, vague])
    assert len(pages) == 3 and paths["amazon-a"] == paths["amazon-b"]
    assert paths["amazon-a"] != paths["amazon-used"] and "bestbuy" in paths and "vague" not in paths
    assert {r["condition"] for r in db.product_price_history(used["product_id"], 90, now=NOW)} <= {"used"}


def test_existing_product_url_survives_title_change_and_repricing_updates_date(db, cfg):
    rows = seed(db, 1)
    _, paths, _ = prepare(db, cfg, rows)
    later = NOW + timedelta(days=1)
    renamed = dict(rows[0], item="Renamed widget", final_price=9, price_since=iso(later))
    pages, renamed_paths, _ = prepare(db, cfg, [renamed], later)
    assert renamed_paths == paths
    page = next(iter(pages.values()))
    assert page["item"] == "Renamed widget"
    assert page["offers"][renamed["dedupe_key"]]["date"] == iso(later)
    assert page["offers"][renamed["dedupe_key"]]["price"] == 9


def test_quarantined_variant_does_not_link_to_previous_product_history(db, cfg, tmp_path):
    rows = seed(db, 1)
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    path = result["public_deals"][0]["product_path"]
    db.update_deal(rows[0]["dedupe_key"], payload=dict(rows[0], conditions=["used"]))
    changed = db.get_deal(rows[0]["dedupe_key"])
    assert changed["product_id"] is None
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[changed])
    deal = result["public_deals"][0]
    assert "product_path" not in deal
    assert f'href="{path}">Price history</a>' not in _page(tmp_path, deal["deal_path"]).read_text()
    # Keep the former product's permanent page without attributing a live offer.
    html = _page(tmp_path, path).read_text()
    assert 'class="getdeal"' not in html and '"offers"' not in html


def test_live_store_matches_selected_offer_and_both_live_statuses_are_honest(db, cfg, tmp_path):
    a = insert(db, "a", asin="B012345678", store="Amazon", final_price=8)
    b = insert(db, "b", asin="B012345678", store="Target", final_price=10)
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[a, b])
    paths = {d["product_path"] for d in result["public_deals"]}
    assert len(paths) == 1
    html = _page(tmp_path, paths.pop()).read_text()
    assert '<p class="detail-store">Store: Amazon</p>' in html
    assert html.count(' · Live</li>') == 2


def test_initial_ended_page_has_history_and_status_but_no_offer(db, cfg, tmp_path):
    rows = seed(db, 1)
    ended = dict(rows[0], status="dead", death="confirmed", died_at=iso(NOW))
    db.update_deal(ended["dedupe_key"], status="dead")
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[ended])
    assert result["product_pages"]["indexable"] == 1
    path = json.loads(db.meta_get(product_pages.META))["pages"][str(ended["product_id"])]["path"]
    html = _page(tmp_path, path).read_text()
    assert ' · Ended</li>' in html and ' · 6 checks' in html
    assert 'class="getdeal"' not in html and '"offers"' not in html
    assert result["seo_audit"]["schema_mismatches"] == 0


def test_retained_photos_still_need_current_source_permission(db, cfg, tmp_path, monkeypatch):
    rows = seed(db, 1)
    monkeypatch.setattr(product_pages, "source_image_allowed", lambda *args: False)
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    html = _page(tmp_path, result["public_deals"][0]["product_path"]).read_text()
    assert '<img' not in html and '"image"' not in html
    assert result["seo_audit"]["schema_mismatches"] == 0


def test_private_unconfirmed_range_and_third_party_history_cannot_qualify(db, cfg):
    rows = seed(db, 1, checks=0)
    rows[0]["keepa_history"] = [{"price": 99, "observed_at": iso(NOW - timedelta(days=i))} for i in range(20)]
    rows[0]["keepa_avg90"] = 999
    assert prepare(db, cfg, rows)[2]["eligible"] == 0
    private = insert(db, "private", asin="B000000000")
    for i in range(6):
        db.record_price("private", "Amazon", 99, NOW - timedelta(days=i * 2), str(i))
    assert prepare(db, cfg, rows)[2]["eligible"] == 0
    public = [dict(site._serialize_deal(private, NOW), price_unconfirmed=True)]
    assert prepare(db, cfg, [private], public=public)[2]["eligible"] == 0
    public = [dict(site._serialize_deal(private, NOW), flags=["range"])]
    assert prepare(db, cfg, [private], public=public)[2]["eligible"] == 0


def test_hub_product_links_cap_at_twelve_and_only_indexable(db, cfg):
    pages, _, _ = prepare(db, cfg, seed(db, 15))
    links = product_pages.hub_links(pages, ["kitchen"])
    assert links.count('<li>') == 12
    assert product_pages.hub_links(pages, ["electronics"]) == '<p><a href="/p/">Browse all price history</a></p>'


def test_deal_page_cap_does_not_hide_product_histories_and_locked_stubs_stay_private(db, cfg, tmp_path):
    rows = seed(db, 1)
    cfg.seo_deal_pages_max = 0
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    deal = result["public_deals"][0]
    assert "deal_path" not in deal and _page(tmp_path, deal["product_path"]).exists()
    assert result["product_pages"]["indexable"] == 1
    assert f'href="{deal["product_path"]}">Price history</a>' in (tmp_path / "index.html").read_text()
    private = insert(db, "hidden", asin="B098765432")
    prepare(db, cfg, [private], public=[{"locked": True, "key": "stub"}])
    assert str(private["product_id"]) not in json.loads(db.meta_get(product_pages.META))["pages"]


def test_switching_off_removes_product_files_and_all_links(db, cfg, tmp_path):
    rows = seed(db, 1)
    out = tmp_path / "site"
    site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
    assert list((out / "p").rglob("*.html"))
    cfg.seo_product_pages_enabled = False
    site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
    assert not list((out / "p").rglob("*.html"))
    assert all(b'href="/p/' not in data for data in tree(out).values())
    assert b'product_path' not in (out / "deals.json").read_bytes()


def test_dynamic_card_history_link_uses_only_a_valid_local_path(db, cfg, tmp_path):
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=seed(db, 1))
    html = (tmp_path / "index.html").read_text()
    snippet = re.search(r'    if \(typeof d.product_path.*?\n    }', html, re.S)[0]
    js = r'''
const assert = require('node:assert/strict');
const esc = s => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;');
const make = new Function('d', 'esc', 'let actions="";' + CODE + ';return actions;');
assert.match(make({product_path:'/p/widget-0123456789abcdef/'},esc), />Price history<\/a>/);
for (const path of ['https://example.com/', '/p/../bad/', 'javascript:alert(1)', null]) {
  assert.equal(make({product_path:path},esc), '');
}
'''.replace('CODE', json.dumps(snippet))
    result = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_audit_detects_invented_reviews_wrong_prices_and_offers_without_live_deal(db, cfg, tmp_path):
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=seed(db, 1))
    path = result["public_deals"][0]["product_path"]
    html = _page(tmp_path, path).read_text()
    for bad in (html.replace('"price": "10.00"', '"price": "99.00"'),
                html.replace('"@type": "Product"', '"@type": "Product", "review": {}'),
                re.sub(r'<p class="detail-price">.*?</p>', '', html)):
        assert seo_audit.Page(bad, path).schema_mismatch()


@pytest.mark.parametrize("status", [200, 202])
def test_indexnow_once_only_after_gate_and_no_churn_or_periodic_reminders(env, status):
    db, cfg, _ = env
    cfg.paywall_open_access = cfg.seo_product_pages_enabled = True
    cfg.seo_indexnow_resubmit_days = 7
    cfg.seo_product_pages_min_checks = 7
    rows = seed(db, 1)
    def render(run, now=NOW, deals=rows):
        return site.render_site(db, cfg, now, deals=deals, last_run={"run_key": run})
    result = render("thin")
    path = result["public_deals"][0]["product_path"]
    url = HOST + path
    assert url not in db.indexnow_pages()
    assert url not in json.loads(indexnow.due_list_path(cfg.site_dir).read_text())
    client, seen = client_for(status)
    with client:
        assert send(db, cfg, client, "thin") == 0
        db.record_price(rows[0]["dedupe_key"], "Amazon", 17, NOW - timedelta(days=3), "seventh")
        qualifies = NOW + timedelta(seconds=2)  # Keep the endpoint's one-second pacing.
        render("qualifies", qualifies)
        assert send(db, cfg, client, "qualifies", qualifies) == 0
        assert db.indexnow_pages()[url]["received_hash"]
        seen.clear()
        render("churn", NOW + timedelta(days=8))
        assert send(db, cfg, client, "churn", NOW + timedelta(days=8)) == 0
        assert all(url not in json.loads(r.content)["urlList"] for r in seen if r.method == "POST")
        # The initial receipt also survives noindex and later requalification.
        db.conn.execute("DELETE FROM price_history")
        db.conn.commit()
        render("noindex", NOW + timedelta(days=39), deals=[])
        assert url not in json.loads(indexnow.due_list_path(cfg.site_dir).read_text())
        assert url not in json.loads(indexnow.manifest_path(cfg.site_dir).read_text())["versions"]
        later = NOW + timedelta(days=40)
        for i in range(7):
            db.record_price(rows[0]["dedupe_key"], "Amazon", 10, later - timedelta(days=i * 2), f"again-{i}")
        seen.clear()
        render("returns", later)
        assert send(db, cfg, client, "returns", later) == 0
        assert all(url not in json.loads(r.content)["urlList"] for r in seen if r.method == "POST")


def test_indexnow_failed_initial_product_receipt_retries_same_version(env):
    db, cfg, _ = env
    cfg.paywall_open_access = cfg.seo_product_pages_enabled = True
    rows = seed(db, 1)
    result = site.render_site(db, cfg, NOW, deals=rows, last_run={"run_key": "first"})
    url = HOST + result["public_deals"][0]["product_path"]
    client, _ = client_for(503)
    with client:
        assert send(db, cfg, client, "first") == 1
    first = db.indexnow_pages()[url]["content_hash"]
    later = NOW + timedelta(minutes=16)
    site.render_site(db, cfg, later, deals=rows, last_run={"run_key": "retry"})
    assert db.indexnow_pages()[url]["content_hash"] == first
    client, seen = client_for()
    with client:
        assert send(db, cfg, client, "retry", later) == 0
    assert any(url in json.loads(r.content)["urlList"] for r in seen if r.method == "POST")


@pytest.mark.parametrize("audit_mode", ["shadow", "enforce"])
def test_pipeline_rerun_with_product_pages_adds_no_rows_spend_or_alerts(pipeline_env, capsys, audit_mode):
    path = Path(pipeline_env["cfg"])
    text = path.read_text().replace("product_pages:\n    enabled: false", "product_pages:\n    enabled: true")
    path.write_text(text)
    assert load_config(path).seo_product_pages_enabled
    check_pipeline_rerun(pipeline_env, capsys, audit_mode)
