"""Task 250: publication policies and bounded product output, entirely offline."""

import hashlib
import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from dealradar.config import AppConfig, CHECKER_STORES, load_config
from dealradar.publish import amazon_history, indexnow, product_pages, site
from dealradar.publish.telegram_pub import format_message
from dealradar.util import iso
from tests.test_product_entities import insert
from tests.test_product_offer_pages import tree
from tests.test_product_price_pages import db, cfg, clock, prepare, seed
from tests.test_publish_site import NOW
from tests.test_seo_deal_pages import _page
from tests.history_pins import pre_history_removal, pre_task_279

FIXTURE = Path(__file__).parent / "fixtures" / "store_policy_pages.json"
# Frozen before production edits on HEAD 90ae0ed6628151dc0a87594a9b3a59955a52b5ed.


def walmart(db, key="walmart", **facts):
    row = insert(db, key, **(dict(store="Walmart", item="Acme AB123 headphones", category="kitchen",
        canonical_url="https://www.walmart.com/ip/widget/123456789", final_price=7.31,
        image_url="https://i5.walmartimages.com/walmart-private.jpg", verify_tier="history-checked",
        price_since=iso(NOW - timedelta(days=4)), sightings=[{
            "source_name": "WalmartCredit", "source_url": "https://slickdeals.net/f/walmart-credit",
            "seen_at": iso(NOW), "final_price": 7.31}]) | facts))
    for i in range(6):
        db.record_price(key, "Walmart", 7.31 + i, NOW - timedelta(days=7 + i * 2), f"walmart-{i}")
    return row


def bestbuy(db, **facts):
    row = insert(db, "bestbuy", **(dict(store="Best Buy", item="Acme AB123 headphones", category="kitchen",
        canonical_url="https://www.bestbuy.com/site/widget/1234567.p", final_price=22,
        image_url="https://pisces.bbystatic.com/bestbuy.jpg", price_rechecked_at=iso(NOW)) | facts))
    for i in range(6):
        db.record_price(row["dedupe_key"], "Best Buy", 22 + i, NOW - timedelta(days=i * 2), f"bestbuy-{i}")
    return row


def byte_case(db, mode, include_walmart):
    cfg = pre_history_removal(load_config()) if mode == "shipped" else AppConfig(custom_domain="wowpriced.com")
    cfg.dark_polish = False  # recorded before task 254
    cfg.strip_comments = False  # recorded before task 278
    pre_task_279(cfg)  # recorded before task 279
    cfg.search_amazon = False  # recorded before task 259
    cfg.front_page.get("everyday_price", {})["enabled"] = False  # recorded before 2026-10-17 tilt
    cfg.join_prompt_delay_s = 15  # recorded before the 35 s delay
    cfg.keepa_buybox_rule = "shadow"  # recorded before the Buy Box rule went on
    cfg.non_amazon_stores = {}  # recorded before the Woot freshness override
    cfg.front_page.get("top_picks", {}).update(non_amazon_max_age_h=24) if "non_amazon_max_age_h" in cfg.front_page.get("top_picks", {}) else None  # recorded before 8 h
    cfg.keepa_click_price_rule = "off"  # recorded before task 275
    cfg.static_photos = False  # recorded before task 274
    cfg.front_page["used_tab_only"] = False  # recorded before task 274
    cfg.amazon_history = "show"  # recorded before task 274
    cfg.amazon_reference = "history"  # recorded before task 276
    cfg.front_page.pop("demote_words", None)  # recorded before task 267b
    cfg.open_box_scarcity = False  # recorded before task 273
    if "appeal" in cfg.front_page:
        cfg.front_page["appeal"].get("want", {})["enabled"] = False
        cfg.front_page["appeal"]["season"] = []
    cfg.drop_rotation_windows = 0  # recorded before task 269
    cfg.support_panel_delay_s = 0  # recorded before task 272
    cfg.support_cost_phrase = "hundreds of dollars"  # recorded before task 272
    if mode != "shipped":
        cfg.support_enabled = mode == "support"
        cfg.paywall_open_access = mode == "open"
    cfg.paywall_teaser_count = 2
    cfg.seo_product_pages_enabled = True
    rows = seed(db, 2) + [bestbuy(db)]
    if include_walmart:
        rows.append(walmart(db))
    return cfg, rows


def byte_snapshot(result, out):
    return {"files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(tree(out).items())},
            "members": hashlib.sha256(json.dumps(result["full_payload"], sort_keys=True).encode()).hexdigest()}


@pytest.mark.parametrize("mode", ["membership", "open", "support", "shipped"])
@pytest.mark.parametrize("include_walmart", [False, True])
@pytest.mark.usefixtures("pre_extension_privacy")
def test_all_history_every_byte_matches_pre_task_head(db, tmp_path, mode, include_walmart):
    cfg, rows = byte_case(db, mode, include_walmart)
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    expected = json.loads(FIXTURE.read_text())[f"{mode}-{include_walmart}"]
    assert byte_snapshot(result, tmp_path) == expected


def test_policy_defaults_shipped_values_and_valid_overrides(tmp_path):
    expected = dict.fromkeys(set(CHECKER_STORES) - {"amazon"}, "history")
    assert AppConfig().store_policy == expected
    # Operator 2026-10-17: no price history for any store for now.
    assert load_config().store_policy == dict.fromkeys(expected, "live_only")
    path = tmp_path / "config.yaml"
    path.write_text("store_policy: {walmart: live_only}\n")
    cfg = load_config(path)
    assert cfg.store_policy == expected | {"walmart": "live_only"}
    assert cfg.store_policy["other"] == "history"
    cfg.store_policy["target"] = "live_only"
    assert AppConfig().store_policy == expected
    path.write_text(yaml.safe_dump({"store_policy": dict.fromkeys(expected, "live_only"), "seo": {
        "product_pages": {"max_indexable": 20000, "max_pages": 20000, "keep_days": 730, "prune_days": 730}}}))
    cfg = load_config(path)
    assert set(cfg.store_policy.values()) == {"live_only"}
    assert cfg.seo_product_pages_max_pages == 20000
    assert (AppConfig().seo_product_pages_keep_days, AppConfig().seo_product_pages_prune_days,
            AppConfig().seo_product_pages_max_pages) == (90, 180, 6000)
    assert (load_config().seo_product_pages_keep_days, load_config().seo_product_pages_prune_days,
            load_config().seo_product_pages_max_pages) == (90, 180, 6000)


@pytest.mark.parametrize("value", [None, [], True, {"amazon": "history"}, {"amazon": "live_only"},
    {"unknown": "history"}, {"walmart": "off"}, {"walmart": "HISTORY"}, {"walmart": None},
    {"walmart": []}, {"walmart": False}, {"walmart": " history"}])
def test_invalid_store_policy_rejected(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"store_policy": value}))
    message = "Amazon uses amazon_history / amazon_watches" if isinstance(value, dict) and "amazon" in value else "store_policy"
    with pytest.raises(ValueError, match=message):
        load_config(path)


@pytest.mark.parametrize("settings", [
    {"max_indexable": 0}, {"max_indexable": -1}, {"max_indexable": True}, {"max_indexable": 1.5},
    {"max_indexable": "2"}, {"max_pages": 0}, {"max_pages": 20001}, {"max_pages": 1999},
    {"max_pages": False}, {"max_pages": 6000.0}, {"keep_days": 0}, {"keep_days": 181},
    {"keep_days": True}, {"keep_days": 1.5}, {"prune_days": 89}, {"prune_days": 731},
    {"prune_days": None}, {"prune_days": "180"}])
def test_invalid_bounds_rejected(tmp_path, settings):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"seo": {"product_pages": settings}}))
    with pytest.raises(ValueError, match="seo.product_pages"):
        load_config(path)


@pytest.mark.parametrize("deal, expected", [
    ({"store": "Walmart", "canonical_url": "https://www.amazon.com/dp/B012345678"}, "amazon"),
    ({"store": "Amazon Warehouse", "canonical_url": "https://example.com/"}, "amazon"),
    ({"store": "Walmart", "canonical_url": "https://www.bestbuy.com/site/123.p"}, "bestbuy"),
    ({"store": "Unrelated", "canonical_url": "https://shop.walmart.com/ip/123"}, "walmart"),
    ({"store": " WAL MART ", "canonical_url": "https://example.com/"}, "walmart"),
    ({"store": "e-Bay"}, "ebay"), ({"store": "Target"}, "other"),
    ({"store": "Walmart", "canonical_url": "https://notwalmart.com/"}, "walmart"),
    ({"store": "Other", "canonical_url": "https://walmart.com.evil.test/"}, "other"),
])
def test_store_resolution_is_shared_and_amazon_has_its_own_policy(cfg, deal, expected):
    cfg.store_policy = dict.fromkeys(set(CHECKER_STORES) - {"amazon"}, "live_only")
    assert amazon_history.store_of(deal) == expected
    assert amazon_history.live_only(cfg, deal) is (expected != "amazon")


def assert_absent(out, paths):
    for name, body in tree(out).items():
        for path in paths:
            assert path.encode() not in body, name
    for path in paths:
        assert not _page(out, path).exists()
    for file in (indexnow.due_list_path(out), indexnow.manifest_path(out)):
        if file.exists():
            assert all(path not in file.read_text() for path in paths)


def test_walmart_live_only_removes_history_everywhere_but_keeps_live_price(db, cfg, tmp_path):
    cfg.engagement = {"saved_deals": True}
    cfg.product_entities_lowest_seen_enabled = True
    cfg.checker["enabled"] = True
    cfg.seo_indexnow_key = "a" * 32
    row = walmart(db)
    row["verify_tier"] = "history-checked"
    shown = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[row])
    original = shown["public_deals"][0]
    detail = _page(tmp_path, original["deal_path"]).read_text()
    assert 'class="price-history"' in detail and 'class="lowest-seen"' in detail
    baseline_checker = {k: v for p in (tmp_path / "check/data").glob("*.json") if p.name != "meta.json"
                        for k, v in json.loads(p.read_text()).items()}
    rows_before = list(db.conn.execute("SELECT * FROM price_history"))
    cfg.store_policy = {"walmart": "live_only"}
    hidden = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[row])
    deal = hidden["public_deals"][0]
    assert "product_path" not in deal and deal["amazon"] is True
    assert deal["badge"] == "verified" and "own price history" not in deal["headline"]
    assert deal["final_price"] == original["final_price"]
    detail = _page(tmp_path, deal["deal_path"]).read_text()
    for text in ('class="price-history"', 'class="lowest-seen"', "Price history", "own price history"):
        assert text not in detail
    assert "price history" not in format_message(row, cfg=cfg).lower()
    assert_absent(tmp_path, [original["product_path"]])
    assert not list(tmp_path.glob("p/*/index.html"))
    checker_rows = {k: v for p in (tmp_path / "check/data").glob("*.json") if p.name != "meta.json"
                    for k, v in json.loads(p.read_text()).items()}
    before_entry, after_entry = baseline_checker["walmart:123456789"], checker_rows["walmart:123456789"]
    assert before_entry["p_path"] == original["product_path"] and before_entry["checks"] > 0
    assert after_entry == dict(before_entry, p_path="", verdict="", checks=0, since="")
    assert list(db.conn.execute("SELECT * FROM price_history")) == rows_before
    report = hidden["product_pages"]
    assert report["written"] == 0 and report["walmart_hidden_offers"] == report["state_pages"] == 1
    assert report["state_bytes"] == len(db.meta_get(product_pages.META))
    assert hidden["site_files"] == len(tree(tmp_path))
    assert "site_files" not in json.dumps(hidden["full_payload"])
    before, state = tree(tmp_path), db.meta_get(product_pages.META)
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[row])
    assert tree(tmp_path) == before and db.meta_get(product_pages.META) == state


def test_mixed_page_uses_no_walmart_facts_and_keeps_bestbuy_photo(db, cfg, tmp_path):
    a = bestbuy(db, asin="B012345678")
    b = walmart(db, asin="B012345678")
    assert a["product_id"] == b["product_id"]
    cfg.product_entities_lowest_seen_enabled = True
    shown = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[a, b])
    path = shown["public_deals"][0]["product_path"]
    cfg.store_policy = {"walmart": "live_only"}
    hidden = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[a, b])
    html = _page(tmp_path, path).read_text()
    assert "Store: Best Buy" in html and a["image_url"] in html
    for text in ("7.31", "WalmartCredit", "walmart-credit", "walmart-private", b["price_since"],
                 iso(NOW - timedelta(days=17))):
        assert text not in html
    pages, paths, _ = prepare(db, cfg, [a, b])
    page = pages[str(a["product_id"])]
    assert page["store"] == "Best Buy" and page["image_url"] == a["image_url"]
    assert set(page["offers"]) == {a["dedupe_key"]}
    assert {r["dedupe_key"] for r in page["checks"]} == {a["dedupe_key"]}
    assert b["dedupe_key"] not in paths
    bestbuy_public = next(d for d in hidden["public_deals"] if d["store"] == "Best Buy")
    detail = _page(tmp_path, bestbuy_public["deal_path"]).read_text()
    assert "Lowest we've seen: $22.00" in detail and "Lowest we've seen: $7.31" not in detail
    # The offer key, rather than a changed history-row label, owns the policy.
    db.conn.execute("UPDATE price_history SET store='Best Buy' WHERE canonical_key=?", (b["dedupe_key"],))
    db.conn.execute("DELETE FROM deals")
    db.conn.commit()
    pages, _, _ = prepare(db, cfg, [])
    page = next(iter(pages.values()))
    assert page["store"] == "Best Buy" and not page["live"]
    assert {r["dedupe_key"] for r in page["checks"]} == {a["dedupe_key"]}
    before = db.meta_get(product_pages.META)
    assert prepare(db, cfg, [])[0] == pages and db.meta_get(product_pages.META) == before


def test_amazon_hide_keeps_mixed_page_photo_blank_when_visible_offer_is_off_wall(db, cfg, tmp_path):
    amazon = seed(db, 1)[0]
    target = insert(db, "target", store="Target", asin=amazon["asin"], item=amazon["item"], final_price=20,
        canonical_url="https://www.target.com/p/widget/-/A-12345678", category="kitchen",
        image_url="https://images.example/target.jpg")
    assert target["product_id"] == amazon["product_id"]
    prepare(db, cfg, [amazon, target])
    db.update_deal(amazon["dedupe_key"], status="dead", payload=dict(
        amazon, death="confirmed", died_at=iso(NOW)))
    amazon = db.get_deal(amazon["dedupe_key"])
    cfg.amazon_history = "hide"
    pages, _, _ = prepare(db, cfg, [amazon])
    page = pages[str(amazon["product_id"])]
    assert page["store"] == "Target" and page["image_url"] == ""
    assert set(page["offers"]) == {target["dedupe_key"]}
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[amazon])
    html = _page(tmp_path, page["path"]).read_text()
    assert target["image_url"] not in html and amazon["image_url"] not in html


@pytest.mark.parametrize("store,name,url", [
    ("bestbuy", "Best Buy", "https://www.bestbuy.com/site/widget/1234567.p"),
    ("target", "Target", "https://www.target.com/p/widget/-/A-12345678"),
    ("woot", "Woot", "https://home.woot.com/offers/widget"),
    ("ebay", "eBay", "https://www.ebay.com/itm/123456789012"),
    ("other", "Other Shop", "https://shop.example/widget"),
])
def test_each_store_policy_uses_original_facts_after_public_url_rewriting(db, cfg, tmp_path, store, name, url):
    allowed = walmart(db, asin="B012345678", final_price=22)
    db.conn.execute("DELETE FROM price_history")
    restricted = insert(db, "restricted", store=name, canonical_url=url, asin="B012345678",
                        category="kitchen", final_price=3.27, image_url="https://images.example/restricted.jpg")
    restricted["verify_tier"] = "history-checked"
    assert allowed["product_id"] == restricted["product_id"]
    for row in (allowed, restricted):
        for i in range(6):
            db.record_price(row["dedupe_key"], row["store"], row["final_price"],
                            NOW - timedelta(days=i * 2), f"price-{i}")
    cfg.affiliate_sovrn_enabled, cfg.affiliate_sovrn_key = True, "a" * 32
    cfg.product_entities_lowest_seen_enabled = True
    cfg.store_policy = {store: "live_only"}
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[allowed, restricted])
    deals = {d["dedupe_key"]: d for d in result["public_deals"]}
    assert "product_path" not in deals["restricted"]
    assert deals["restricted"]["badge"] == "verified" and deals["restricted"]["amazon"] is True
    detail = _page(tmp_path, deals["restricted"]["deal_path"]).read_text()
    assert 'class="price-history"' not in detail and 'class="lowest-seen"' not in detail
    allowed_detail = _page(tmp_path, deals[allowed["dedupe_key"]]["deal_path"]).read_text()
    assert "Lowest we've seen: $22.00" in allowed_detail
    assert "Lowest we've seen: $3.27" not in allowed_detail
    html = _page(tmp_path, deals[allowed["dedupe_key"]]["product_path"]).read_text()
    assert "Store: Walmart" in html and "$3.27" not in html and "restricted.jpg" not in html
    # Older offers absent from this wall still retain their original policy.
    later = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[allowed])
    detail = _page(tmp_path, later["public_deals"][0]["deal_path"]).read_text()
    assert "Lowest we've seen: $22.00" in detail and "Lowest we've seen: $3.27" not in detail


@pytest.mark.parametrize("watches", ["", "on", "off"])
@pytest.mark.parametrize("history", ["show", "hide"])
def test_watch_flag_changes_only_walmart_json_and_keeps_browser_guard(db, cfg, watches, history):
    cfg.amazon_watches, cfg.amazon_history = watches, history
    a, b = bestbuy(db), walmart(db)
    before = json.dumps(site._serialize_deal(a, NOW, cfg=cfg))
    cfg.store_policy = {"walmart": "live_only"}
    assert json.dumps(site._serialize_deal(a, NOW, cfg=cfg)) == before
    assert site._serialize_deal(b, NOW, cfg=cfg)["amazon"] is True
    script = amazon_history.watches_template((site.TEMPLATES_DIR / "engagement.js").read_text(), cfg)
    assert 'if (d.amazon) return ""' in script
    assert "clean.amazon = entry.amazon === true" in script


def test_walmart_hearts_work_but_saved_watches_do_not(db, cfg, tmp_path):
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    cfg.store_policy, cfg.amazon_watches = {"walmart": "live_only"}, "on"
    cfg.support_enabled, cfg.engagement = True, {"saved_deals": True}
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[walmart(db)])
    home = (tmp_path / "index.html").read_text()
    code = home[home.index('  var SAVED_KEY ='):home.index('  function savedWatchEditor()')]
    harness = r'''
const vm = require('node:vm'), assert = require('node:assert/strict');
const events = [], notices = [];
const ctx = {state: {meta: {support: {enabled: true}}}, engagementOn: () => true,
  isLocked: d => !!d.locked, dealKey: d => d.key, esc: String, document: {},
  localStorage: {setItem() {}}, track: name => events.push(name),
  toast: s => notices.push(s), setTimeout: f => f()};
vm.createContext(ctx); vm.runInContext(CODE, ctx);
const d = DEAL;
ctx.savedDeals.entries = []; ctx.savedDeals.publicDeals = [d]; ctx.savedDeals.view = true;
assert.equal(ctx.saveDeal(d), true);
assert(ctx.savedButtonHtml(d).includes('aria-pressed="true"'));
const entry = ctx.savedDeals.entries[0]; entry.target_price = 9;
assert.equal(entry.amazon, true);
assert.equal(ctx.parseSaved(JSON.stringify({version: 2, deals: [entry]}))[0].amazon, true);
d.final_price = 6; ctx.checkSavedWatches(true);
assert.equal(ctx.savedDeals.matches.size, 0); assert.equal(notices.length, 0);
assert.equal(ctx.savedWatchHtml(d), ''); assert.equal(ctx.setWatchTarget(d.key, 5), false);
assert.equal(events.filter(n => n.startsWith('watch_')).length, 0);
'''.replace("CODE", json.dumps(code)).replace("DEAL", json.dumps(result["public_deals"][0]))
    subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True)


def age_state(db, days, *, indexed=False):
    state = json.loads(db.meta_get(product_pages.META))
    for page in state["pages"].values():
        for offer in page["offers"].values():
            offer["published_at"] = iso(NOW - timedelta(days=days))
        page.update(indexable=indexed, indexed_since=iso(NOW - timedelta(days=days)), last_qualified=iso(NOW))
    db.meta_set(product_pages.META, json.dumps(state))
    return state


@pytest.mark.parametrize("days,written,pruned", [(90, 1, 0), (90.0001, 0, 0),
    (180, 0, 0), (180.0001, 0, 1)])
def test_retention_boundaries_and_admission_pruning(db, cfg, days, written, pruned):
    rows = seed(db, 1, checks=0)
    prepare(db, cfg, rows)
    state = age_state(db, days)
    key = str(rows[0]["product_id"])
    state["admitted"] = [key]
    db.meta_set(product_pages.META, json.dumps(state))
    models, paths, report = prepare(db, cfg, [])
    assert len(models) == len(paths) == report["written"] == written
    assert report["pruned_state"] == pruned and report["state_pages"] == 1 - pruned
    assert report["not_written_old"] == (not written and not pruned)
    retained = json.loads(db.meta_get(product_pages.META))
    assert (key in retained["pages"]) is (not pruned)
    assert (key in retained["admitted"]) is (not pruned)
    assert report["state_bytes"] == len(db.meta_get(product_pages.META))


def test_old_page_is_removed_from_every_output_and_state_prunes_later(db, cfg, tmp_path):
    cfg.seo_indexnow_key = "a" * 32
    rows = seed(db, 1, checks=0)
    first = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    path = first["public_deals"][0]["product_path"]
    age_state(db, 91)
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[])
    assert_absent(tmp_path, [path])
    assert result["product_pages"]["not_written_old"] == 1
    assert str(rows[0]["product_id"]) in json.loads(db.meta_get(product_pages.META))["pages"]
    age_state(db, 181)
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[])
    assert result["product_pages"]["pruned_state"] == 1
    assert json.loads(db.meta_get(product_pages.META))["pages"] == {}


def test_old_indexable_page_is_kept_even_beyond_prune_window(db, cfg, tmp_path):
    prepare(db, cfg, seed(db, 1, checks=0))
    state = age_state(db, 200, indexed=True)
    path = next(iter(state["pages"].values()))["path"]
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[])
    assert _page(tmp_path, path).exists()
    assert path in (tmp_path / "sitemap-products.xml").read_text()
    assert result["product_pages"]["written"] == 1 and result["product_pages"]["pruned_state"] == 0


def test_hidden_old_page_is_pruned_too(db, cfg):
    row = walmart(db)
    prepare(db, cfg, [row])
    age_state(db, 181)
    cfg.store_policy = {"walmart": "live_only"}
    models, paths, report = prepare(db, cfg, [])
    assert models == paths == {}
    assert report["pruned_state"] == 1 and report["state_pages"] == 0


def test_pruning_reclaims_admission_slots_before_rerun(db, cfg):
    cfg.seo_product_pages_max_new_indexable_per_day = 1
    prepare(db, cfg, seed(db, 1))
    age_state(db, 181)
    row = insert(db, "new", asin="B111111111", store="Amazon",
                 canonical_url="https://www.amazon.com/dp/B111111111")
    for i in range(6):
        db.record_price(row["dedupe_key"], "Amazon", 10 + i, NOW - timedelta(days=i * 2), str(i))
    models, paths, report = prepare(db, cfg, [row])
    assert report["pruned_state"] == report["new_today"] == report["indexable"] == 1
    assert models[str(row["product_id"])]["indexable"]
    state = db.meta_get(product_pages.META)
    retry = prepare(db, cfg, [row])
    assert retry[:2] == (models, paths) and db.meta_get(product_pages.META) == state


def test_file_cap_is_deterministic_indexable_first_and_leaks_no_dropped_paths(db, cfg, tmp_path):
    rows = seed(db, 10)
    cfg.seo_product_pages_max_indexable = cfg.seo_product_pages_max_new_indexable_per_day = 2
    initial = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    before_paths = {d["product_path"] for d in initial["public_deals"]}
    models, _, _ = prepare(db, cfg, rows)
    expected = [p["path"] for p in sorted(models.values(), key=lambda p: (
        not p["indexable"], product_pages._admission_order(p, [p["live"]])))[:4]]
    cfg.seo_product_pages_max_pages = 4
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    written = {"/" + str(p.parent.relative_to(tmp_path)) + "/" for p in tmp_path.glob("p/*/index.html")}
    assert written == set(expected)
    assert result["product_pages"]["written"] == 4 and result["product_pages"]["capped"] == 6
    assert result["product_pages"]["indexable"] == 2
    assert_absent(tmp_path, before_paths - written)
    before, state = tree(tmp_path), db.meta_get(product_pages.META)
    reversed_models, _, _ = prepare(db, cfg, rows[::-1])
    assert {p["path"] for p in reversed_models.values()} == written
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    assert tree(tmp_path) == before and db.meta_get(product_pages.META) == state


def test_cap_prefers_distinct_offers_then_non_amazon(db, cfg):
    rows = seed(db, 3, checks=0)
    other = bestbuy(db)
    extra = insert(db, "extra", store="Target", asin=rows[0]["asin"], item=rows[0]["item"])
    cfg.affiliate_amazon_tag = ""
    cfg.seo_product_pages_max_indexable = 1
    cfg.seo_product_pages_max_new_indexable_per_day = 0
    cfg.seo_product_pages_max_pages = 2
    models, _, report = prepare(db, cfg, rows + [other, extra])
    assert list(models) == [str(rows[0]["product_id"]), str(other["product_id"])]
    assert report["capped"] == 2


def test_two_hundred_amazon_products_are_bounded_under_show(db, cfg, tmp_path):
    cfg.amazon_history = "show"
    cfg.seo_product_pages_max_pages = cfg.seo_product_pages_max_indexable = 7
    rows = seed(db, 200, checks=0)
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    assert len(list(tmp_path.glob("p/*/index.html"))) == cfg.seo_product_pages_max_pages
    assert result["product_pages"]["written"] == 7 and result["product_pages"]["capped"] == 193
    assert sum("product_path" in d for d in result["public_deals"]) == 7
    assert result["site_files"] == len(tree(tmp_path))


def test_checker_never_points_to_capped_product_path(db, cfg, tmp_path):
    cfg.checker["enabled"] = True
    cfg.seo_product_pages_max_indexable = cfg.seo_product_pages_max_pages = 1
    rows = [bestbuy(db), insert(db, "second", store="Best Buy", category="kitchen",
            canonical_url="https://www.bestbuy.com/site/widget/7654321.p")]
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    entries = {k: v for p in (tmp_path / "check/data").glob("*.json") if p.name not in {"meta.json", "all.json"}
               for k, v in json.loads(p.read_text()).items()}
    assert len(entries) == 2
    assert sum(bool(e["p_path"]) for e in entries.values()) == 1
    for entry in entries.values():
        if entry["p_path"]:
            assert _page(tmp_path, entry["p_path"]).exists()
    assert sum("product_path" in d for d in result["public_deals"]) == 1


def test_disabled_summary_still_counts_existing_private_state(db, cfg):
    prepare(db, cfg, seed(db, 1))
    stored = db.meta_get(product_pages.META)
    cfg.seo_product_pages_enabled = False
    models, paths, report = prepare(db, cfg, [])
    assert models == paths == {}
    assert report["written"] == 0 and report["state_pages"] == 1
    assert report["state_bytes"] == len(stored) and db.meta_get(product_pages.META) == stored


def test_site_file_warning_threshold_is_private(db, cfg, tmp_path, caplog):
    out = tmp_path / "site"
    initial = site.render_site(db, cfg, NOW, out_dir=out, deals=[])
    padding = out / "padding.bin"
    padding.touch()
    # Hard links keep the 15,000-file boundary cheap in disk space and inodes.
    for i in range(15000 - initial["site_files"] - 1):
        (out / f"padding-{i}.bin").hardlink_to(padding)
    result = site.render_site(db, cfg, NOW, out_dir=out, deals=[])
    assert result["site_files"] == 15000
    assert not any("site_files" in r.message for r in caplog.records)
    (out / "one-more.bin").hardlink_to(padding)
    result = site.render_site(db, cfg, NOW, out_dir=out, deals=[])
    assert result["site_files"] == 15001
    assert [r.message for r in caplog.records if "site_files" in r.message] == ["site_files exceeds 15000: 15001"]
    for path in out.rglob("*.html"):
        assert "site_files" not in path.read_text()
    assert "site_files" not in (out / "deals.json").read_text()
