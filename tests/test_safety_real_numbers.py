"""Task 121: publication and browser behavior, with no external requests."""

import json
import re
import shutil
import subprocess
from datetime import timedelta

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import amazon_history, product_pages, seo_audit, site
from dealradar.savings import product_price_facts
from dealradar.util import iso
from tests.test_product_entities import insert
from tests.test_product_price_pages import db, cfg, clock, seed, prepare
from tests.test_product_offer_pages import tree
from tests.test_publish_site import NOW
from tests.test_seo_deal_pages import _page
from tests.test_tracking import SAMPLE, _run_node


@pytest.mark.parametrize("value", ["show", "hide"])
def test_amazon_history_config_default_and_validation(tmp_path, value):
    assert AppConfig().amazon_history == "show"
    assert load_config().amazon_history == "hide"  # Amazon's email, operator 2026-10-17
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    assert load_config(path).amazon_history == "show"
    path.write_text(f"amazon_history: {value}\n")
    assert load_config(path).amazon_history == value
    path.write_text("amazon_history: typo\n")
    with pytest.raises(ValueError, match="amazon_history"):
        load_config(path)


@pytest.mark.parametrize("text, value", [("", ""), ("amazon_watches: off", "off"),
                                         ('amazon_watches: "on"', "on"), ("amazon_watches: on", "on")])
def test_amazon_watches_config_reads_bare_yaml_booleans(tmp_path, text, value):
    assert load_config().amazon_watches == "off"
    path = tmp_path / "config.yaml"
    path.write_text(text + "\n")
    assert load_config(path).amazon_watches == value
    path.write_text("amazon_watches: maybe\n")
    with pytest.raises(ValueError, match="amazon_watches"):
        load_config(path)


def test_watches_off_keeps_amazon_charts_but_not_amazon_watches(db, cfg, tmp_path):
    rows = seed(db, 1) + [non_amazon(db)]
    cfg.amazon_watches = "off"
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    amazon = next(d for d in result["public_deals"] if d["store"] == "Amazon")
    other = next(d for d in result["public_deals"] if d["store"] == "Best Buy")
    assert 'class="price-history"' in _page(tmp_path, amazon["deal_path"]).read_text()
    assert _page(tmp_path, amazon["product_path"]).exists()
    assert amazon["amazon"] is True and other["amazon"] is False
    script = (site.TEMPLATES_DIR / "engagement.js").read_text()
    kept = amazon_history.watches_template(script, cfg)
    assert "if (d.amazon) return" in kept and "clean.amazon = entry.amazon === true" in kept
    cfg.amazon_watches = "on"
    assert "if (d.amazon) return" not in amazon_history.watches_template(script, cfg)


def test_internal_flag_set_clear_and_blocked_storage(tmp_path):
    scenarios = {
        "set": {"search": "?utm_source=mail&internal=1&code=a%2Bb"},
        "remembered": {"storage": {"wp.internal": "1"}},
        "clear": {"search": "?internal=0&keep=yes", "storage": {"wp.internal": "1"}},
        "plain": {}, "blocked_set": {"blocked": True, "search": "?internal=1"},
        "readonly_clear": {"readonly": True, "search": "?internal=0", "storage": {"wp.internal": "1"}},
        "invalid": {"search": "?internal=oops&keep=yes"},
    }
    observed = _run_node(tmp_path, SAMPLE, scenarios)
    for name, result in observed.items():
        internal = name in {"set", "remembered", "blocked_set"}
        config = next(c[2] for c in result["dataLayer"] if c[:2] == ["config", SAMPLE["ga4_measurement_id"]])
        assert config.get("traffic_type") == ("internal" if internal else None)
        if internal:
            assert config["allow_google_signals"] is config["allow_ad_personalization_signals"] is False
            assert result["srcs"] == ["https://www.googletagmanager.com/gtag/js?id=" + SAMPLE["ga4_measurement_id"]]
            assert not result["clarity"]
            assert all(result[key] is None for key in ("fbq", "ttq", "pintrk", "uetq"))
        else:
            assert result["clarity"] and len(result["srcs"]) == 6
    assert observed["set"]["storage"]["wp.internal"] == "1"
    assert "wp.internal" not in observed["clear"]["storage"]
    assert observed["set"]["url"] == "/nested/page/?utm_source=mail&code=a%2Bb#keep"
    assert observed["clear"]["url"] == observed["invalid"]["url"] == "/nested/page/?keep=yes#keep"
    assert observed["blocked_set"]["url"] == observed["readonly_clear"]["url"] == "/nested/page/#keep"
    assert "url" not in observed["remembered"]


def test_internal_flag_works_without_configured_tags(tmp_path):
    observed = _run_node(tmp_path, {}, {"set": {"search": "?internal=1&keep=yes"},
                                      "clear": {"search": "?internal=0", "storage": {"wp.internal": "1"}}})
    assert observed["set"]["storage"]["wp.internal"] == "1"
    assert "wp.internal" not in observed["clear"]["storage"]
    assert observed["set"]["url"] == "/nested/page/?keep=yes#keep"
    assert all(not row["srcs"] for row in observed.values())


def test_welcome_scrubs_receipt_then_preserves_internal_choice_before_ga4(db, cfg, tmp_path):
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    cfg.tracking = SAMPLE
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[])
    head = (tmp_path / "welcome.html").read_text().split("</head>")[0]
    scripts = re.findall(r'<script>(.*?)</script>', head, flags=re.S)
    harness = r'''
const vm = require('node:vm'), assert = require('node:assert/strict');
const ctx = {URLSearchParams, navigator: {},
  location: {search: '?session_id=cs_privateReceipt123&internal=1&keep=yes', pathname: '/welcome.html', hash: '#keep'},
  localStorage: {getItem: () => null, setItem() {}},
  document: {createElement: () => ({}), head: {appendChild() {assert(!ctx.location.search.includes('session_id'));}}}};
ctx.window = ctx;
ctx.history = {replaceState(state, title, url) {const u = new URL(url, 'https://wowpriced.com'); ctx.location.search = u.search;}};
vm.createContext(ctx);
for (const code of SCRIPTS) vm.runInContext(code, ctx);
assert.equal(ctx.location.search, '?keep=yes');
assert.equal(ctx.wpCheckoutId, 'cs_privateReceipt123');
assert(ctx.dataLayer.some(c => c[0] === 'config' && c[2]?.traffic_type === 'internal'));
'''.replace("SCRIPTS", json.dumps(scripts))
    subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True)


@pytest.mark.parametrize("mode", ["show", "hide"])
def test_amazon_saved_hearts_and_price_watches_in_published_browser_code(db, cfg, tmp_path, mode):
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    cfg.support_enabled, cfg.engagement, cfg.amazon_history = True, {"saved_deals": True}, mode
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=seed(db, 1))
    home = (tmp_path / "index.html").read_text()
    code = home[home.index('  var SAVED_KEY ='):home.index('  function savedWatchEditor()')]
    harness = r'''
const vm = require('node:vm'), assert = require('node:assert/strict');
const events = [], notices = [];
const ctx = {state: {meta: {support: {enabled: true}}}, engagementOn: () => true,
  isLocked: d => !!d.locked, dealKey: d => d.key, esc: String, document: {},
  localStorage: {setItem() {}}, track: name => events.push(name),
  toast: s => notices.push(s), setTimeout: f => f()};
vm.createContext(ctx);
vm.runInContext(CODE, ctx);
const hide = HIDE;
for (const store of ['Amazon', 'Amazon Resale', ' amazon warehouse ', 'X via Amazon', 'Best Buy', 'Woot']) {
  events.length = notices.length = 0;
  const amazon = store.trim().toLowerCase().startsWith('amazon') || store === 'X via Amazon';
  const d = {key: 'key', item: 'Widget', store, final_price: 20, conditions: [], status: 'alive', asin: 'B000000001',
    ...(hide ? {amazon} : {})};
  ctx.savedDeals.entries = []; ctx.savedDeals.publicDeals = [d]; ctx.savedDeals.view = true;
  assert.equal(ctx.saveDeal(d), true);
  assert(ctx.savedButtonHtml(d).includes('aria-pressed="true"')); // hearts still save
  assert.equal(events.includes('watch_create'), !hide || !amazon);
  const entry = ctx.savedDeals.entries[0]; entry.target_price = 19;
  if (hide) {
    assert.equal(entry.amazon, amazon);
    assert.equal(ctx.parseSaved(JSON.stringify({version: 2, deals: [entry]}))[0].amazon, amazon);
  }
  d.final_price = 18;
  ctx.checkSavedWatches(true);
  assert.equal(ctx.savedDeals.matches.size, hide && amazon ? 0 : 1);
  assert.equal(notices.length, hide && amazon ? 0 : 1);
  assert.equal(ctx.savedWatchHtml(d).includes('Watch price'), !hide || !amazon);
  assert.equal(ctx.setWatchTarget(d.key, 17), !hide || !amazon);
  if (hide && amazon) {
    assert.equal(ctx.savedWatchHtml(d), '');
    assert.equal(events.filter(n => n.startsWith('watch_')).length, 0);
  }
}
'''.replace("CODE", json.dumps(code)).replace("HIDE", json.dumps(mode == "hide"))
    subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True)


@pytest.mark.parametrize("tracking", [{}, SAMPLE])
def test_every_html_has_one_pinterest_optout_and_google_schema_stays(db, cfg, tmp_path, tracking):
    cfg.tracking = tracking
    rows = seed(db, 2)
    for _ in range(2):
        site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
        for path in tmp_path.rglob("*.html"):
            html = path.read_text()
            assert html.count('<meta name="pinterest-rich-pin" content="false">') == 1, path
            assert html.index('name="pinterest-rich-pin"') < html.index("</head>")
    html = _page(tmp_path, "/p/").read_text()
    assert "wp.internal" in html
    product = next(tmp_path.glob("p/*/index.html"))
    assert seo_audit.Page(product.read_text(), "/p/product/").products()


@pytest.mark.parametrize("support,open_access", [(False, False), (False, True), (True, False), (True, True)])
def test_show_matches_omitted_for_every_published_byte(db, cfg, tmp_path, support, open_access):
    cfg.support_enabled, cfg.paywall_open_access = support, open_access
    cfg.engagement = {"saved_deals": True}
    rows = seed(db, 2)
    site.render_site(db, cfg, NOW, out_dir=tmp_path / "default", deals=rows)
    cfg.amazon_history = "show"
    site.render_site(db, cfg, NOW, out_dir=tmp_path / "show", deals=rows)
    assert tree(tmp_path / "default") == tree(tmp_path / "show")


def non_amazon(db, key="other", **kwargs):
    row = insert(db, key, store="Best Buy", bestbuy_sku="1234567", category="kitchen",
                 canonical_url="https://www.bestbuy.com/site/widget/1234567.p", **kwargs)
    for i in range(6):
        db.record_price(key, "Best Buy", 22 + i, NOW - timedelta(days=i * 2), f"bestbuy-{i}")
    return row


def test_hide_removes_old_amazon_pages_links_sitemap_and_charts_idempotently(db, cfg, tmp_path):
    cfg.amazon_as_of_enabled = True
    rows = seed(db, 1) + [non_amazon(db)]
    shown = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    amazon = next(d for d in shown["public_deals"] if d["store"] == "Amazon")
    other = next(d for d in shown["public_deals"] if d["store"] == "Best Buy")
    other_deal = _page(tmp_path, other["deal_path"]).read_bytes()
    assert 'class="price-history"' in _page(tmp_path, amazon["deal_path"]).read_text()
    cfg.amazon_history = "hide"
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    assert not _page(tmp_path, amazon["product_path"]).exists()
    # The related Amazon card loses its own history link; other deal content stays.
    amazon_link = product_pages.history_link(amazon["product_path"]).encode()
    assert _page(tmp_path, other["deal_path"]).read_bytes() == other_deal.replace(amazon_link, b"")
    detail = _page(tmp_path, amazon["deal_path"]).read_text()
    assert 'price-history' not in detail and 'Our price checks' not in detail
    assert 'Amazon price as of' in detail and 'Sale price: <strong>$10.00' in detail
    for name, data in tree(tmp_path).items():
        if name.endswith((".html", ".xml", ".json")):
            assert amazon["product_path"].encode() not in data, name
    assert other["product_path"] in _page(tmp_path, "/p/").read_text()
    assert result["product_pages"]["indexable"] == 1
    assert all(result["seo_audit"][key] == 0 for key in
               ("schema_mismatches", "broken_internal_links", "sitemap_missing", "sitemap_extra"))
    before, state = tree(tmp_path), db.meta_get(product_pages.META)
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    assert tree(tmp_path) == before and db.meta_get(product_pages.META) == state
    cfg.amazon_history = "show"
    restored = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    assert next(d for d in restored["public_deals"] if d["store"] == "Amazon")["product_path"] == amazon["product_path"]


def test_hide_also_suppresses_optional_amazon_lowest_seen_summary(db, cfg, tmp_path, monkeypatch):
    from dealradar.publish import product_offers

    rows = seed(db, 1) + [non_amazon(db)]
    cfg.product_entities_lowest_seen_enabled = True
    facts = {d["dedupe_key"]: {"price": 8, "date": iso(NOW)} for d in rows}
    monkeypatch.setattr(product_offers, "evaluate", lambda *args: ({}, facts, {}))
    shown = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    assert all('class="lowest-seen"' in _page(tmp_path, d["deal_path"]).read_text()
               for d in shown["public_deals"])
    cfg.amazon_history = "hide"
    hidden = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    for d in hidden["public_deals"]:
        assert ('class="lowest-seen"' in _page(tmp_path, d["deal_path"]).read_text()) is (d["store"] != "Amazon")


def test_all_amazon_hidden_leaves_no_product_urls_in_the_index_or_sitemap(db, cfg, tmp_path):
    rows = seed(db, 1)
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    path = result["public_deals"][0]["product_path"]
    cfg.amazon_history = "hide"
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    assert not list(tmp_path.glob("p/*/index.html"))
    assert not _page(tmp_path, path).exists()
    index = seo_audit.Page(_page(tmp_path, "/p/").read_text(), "/p/")
    assert not index.indexable and "Price histories are published only when they meet our verification standards." in index.scope.text()
    assert not any("/p/" in url for url in seo_audit.sitemap_urls(tmp_path, "https://wowpriced.com"))
    assert result["product_pages"]["indexable"] == result["product_pages"]["new_today"] == 0
    assert all(result["seo_audit"][key] == 0 for key in
               ("schema_mismatches", "broken_internal_links", "sitemap_missing", "sitemap_extra"))
    before, state = tree(tmp_path), db.meta_get(product_pages.META)
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    assert tree(tmp_path) == before and db.meta_get(product_pages.META) == state


def test_mixed_store_products_use_only_non_amazon_checks_and_offers_even_after_deletion(db, cfg, tmp_path):
    amazon = seed(db, 1)[0]
    other = non_amazon(db, asin=amazon["asin"], item=amazon["item"])
    assert amazon["product_id"] == other["product_id"]
    rows = [amazon, other]
    pages, paths, _ = prepare(db, cfg, rows)
    path = paths[amazon["dedupe_key"]]
    cfg.amazon_history = "hide"
    pages, paths, _ = prepare(db, cfg, rows)
    page = next(iter(pages.values()))
    assert set(page["offers"]) == {other["dedupe_key"]}
    assert all(r["store"] == "Best Buy" for r in page["checks"])
    assert page["live"]["store"] == page["store"] == "Best Buy"
    assert amazon["dedupe_key"] not in paths and paths[other["dedupe_key"]] == path
    db.conn.execute("DELETE FROM deals")
    db.conn.commit()
    pages, paths, _ = prepare(db, cfg, [])
    assert set(next(iter(pages.values()))["offers"]) == {other["dedupe_key"]}
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[])
    html = _page(tmp_path, path).read_text()
    assert "Store: Best Buy" in html and "Lowest $22.00" in html and "Lowest $10.00" not in html


def test_mixed_history_without_live_non_amazon_offer_keeps_store_on_rerun(db, cfg):
    amazon = seed(db, 1)[0]
    bestbuy = non_amazon(db, key="z", asin=amazon["asin"], item=amazon["item"])
    target = insert(db, "a", store="Target", asin=amazon["asin"], item=amazon["item"])
    cfg.amazon_history = "hide"
    rows = [dict(bestbuy, status="dead"), dict(target, status="dead"), amazon]
    first = prepare(db, cfg, rows)
    page = next(iter(first[0].values()))
    assert page["live"] is None and page["store"] == "Best Buy"
    state = db.meta_get(product_pages.META)
    assert prepare(db, cfg, rows) == first
    assert db.meta_get(product_pages.META) == state


def test_non_amazon_pages_stay_byte_identical_and_no_amazon_histories_leave_an_empty_index(db, cfg, tmp_path):
    rows = [non_amazon(db)]
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    before = {name: data for name, data in tree(tmp_path).items() if name.endswith(".html") and name != "index.html"}
    cfg.amazon_history = "hide"
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    assert all((tmp_path / name).read_bytes() == data for name, data in before.items())
    # An existing Amazon-only page is removed even after the underlying deal is deleted.
    cfg.amazon_history = "show"
    amazon = seed(db, 1)[0]
    _, paths, _ = prepare(db, cfg, [amazon])
    db.conn.execute("DELETE FROM deals")
    db.conn.commit()
    cfg.amazon_history = "hide"
    pages, paths, _ = prepare(db, cfg, [])
    assert amazon["dedupe_key"] not in paths
    assert all(p["store"] != "Amazon" for p in pages.values())


@pytest.mark.parametrize("days,full", [(7, False), (89.999, False), (90, True), (120, False)])
def test_typical_price_and_answer_name_actual_window(db, cfg, tmp_path, days, full):
    rows = seed(db, 1, span=days)
    pages, _, _ = prepare(db, cfg, rows)
    page = next(iter(pages.values()))
    facts = product_price_facts(page["checks"], list(page["offers"].values()), NOW)
    assert facts["median_90d"] is full
    html, answer = product_pages.facts_html(facts, 10), product_pages.answer(10, facts, cfg)
    if full:
        assert "Typical price (90-day median)" in html and "(90-day median)." in answer
    else:
        date = site.seo._date(facts["median_since"])
        assert f'median of our {facts["median_checks"]} checks since {date}' in html
        assert f'(our median since {date}).' in answer
        assert "90-day median" not in html + answer
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    path = result["public_deals"][0]["product_path"]
    text = _page(tmp_path, path).read_text()
    assert not seo_audit.Page(text, path).schema_mismatch()
    if not full:
        bad = text.replace(f'(our median since {date})', '(90-day median)')
        assert seo_audit.Page(bad, path).schema_mismatch()


def url_amazon(db, store="X via Amazon"):
    url = "https://www.amazon.com/dp/B000000001"
    row = insert(db, "url|" + url, store=store, asin="B000000001", item="Kitchen Widget",
                 category="kitchen", canonical_url=url, price_rechecked_at=iso(NOW))
    for i in range(6):
        db.record_price(row["dedupe_key"], store, 10 + i, NOW - timedelta(days=i * 2), f"amazon-{i}")
    return row


def test_url_identified_amazon_has_no_chart_product_page_or_history_words(db, cfg, tmp_path):
    from dealradar.publish.email_digest import build_digest
    from dealradar.publish.members import build_member_email
    from dealradar.publish.telegram_pub import format_message

    row = url_amazon(db)
    row["verify_tier"] = "history-checked"
    shown = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[row])["public_deals"][0]
    assert 'class="price-history"' in _page(tmp_path, shown["deal_path"]).read_text()
    assert "price history" in shown["headline"]
    assert "amazon" not in shown
    cfg.amazon_history = "hide"
    hidden = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[row])["public_deals"][0]
    assert hidden["amazon"] is True and hidden["badge"] == "verified"
    assert hidden["final_price"] == shown["final_price"]
    assert hidden["regular_price"] == shown["regular_price"]
    assert not _page(tmp_path, shown["product_path"]).exists()
    assert "product_path" not in hidden
    for text in [site.seo.card_html(hidden, now=NOW),
                 _page(tmp_path, hidden["deal_path"]).read_text(),
                 (tmp_path / "deals.json").read_text(), format_message(row, cfg=cfg),
                 *build_digest([row], cfg, NOW)[1:], *build_member_email([row], cfg)[1:]]:
        assert "price history" not in text.lower()
    for path in ["/", "/deals/home-kitchen/", "/best-deals-today/"]:
        page = seo_audit.Page(_page(tmp_path, path).read_text(), path)
        assert all("price history" not in n.text().lower() for n in page.scope.nodes()
                   if n.tag == "article")
    assert 'class="price-history"' not in _page(tmp_path, hidden["deal_path"]).read_text()
    assert "price history" in format_message(row, cfg=AppConfig())
    db.conn.execute("DELETE FROM deals")
    db.conn.commit()
    assert prepare(db, cfg, [])[0] == {}


@pytest.mark.parametrize("store", ["Amazon", "X via Amazon"])
def test_mixed_store_lowest_seen_uses_only_non_amazon_prices(db, cfg, tmp_path, store):
    amazon = url_amazon(db, store)
    other = non_amazon(db, asin=amazon["asin"], item=amazon["item"])
    assert amazon["product_id"] == other["product_id"]
    cfg.amazon_history = "hide"
    cfg.product_entities_lowest_seen_enabled = True
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=[amazon, other])
    bestbuy = next(d for d in result["public_deals"] if d["store"] == "Best Buy")
    assert bestbuy["amazon"] is False
    html = _page(tmp_path, bestbuy["deal_path"]).read_text()
    assert "Lowest we've seen: $22.00" in html
    assert "Lowest we've seen: $10.00" not in html
    # History rows can carry a different old store label; their offer key decides.
    db.conn.execute("UPDATE price_history SET store='Best Buy' WHERE canonical_key=?", (amazon["dedupe_key"],))
    db.conn.commit()
    pages, paths, _ = prepare(db, cfg, [amazon, other])
    page = next(iter(pages.values()))
    assert set(page["offers"]) == {other["dedupe_key"]}
    assert {r["dedupe_key"] for r in page["checks"]} == {other["dedupe_key"]}
    assert amazon["dedupe_key"] not in paths
    db.conn.execute("DELETE FROM deals")
    db.conn.commit()
    page = next(iter(prepare(db, cfg, [])[0].values()))
    assert page["store"] == "Best Buy"
    assert {r["dedupe_key"] for r in page["checks"]} == {other["dedupe_key"]}


@pytest.mark.parametrize("offset,full", [(0, True), (1, True), (1.001, False), (80, False)])
def test_90_day_median_requires_checks_near_window_start(offset, full):
    rows = [{"observed_at": iso(NOW - timedelta(days=days)), "price": price}
            for days, price in [(120, 99), (90 - offset, 15), (3, 12), (0, 10)]]
    facts = product_price_facts(rows, [], NOW)
    assert facts["median_90d"] is full
    assert facts["median"] == 12 and facts["median_checks"] == 3


def test_operator_decisions_ship_without_changing_code_defaults():
    cfg, defaults = load_config(), AppConfig()
    assert cfg.amazon_history == "hide" and defaults.amazon_history == "show"  # Amazon's email 2026-10-17
    assert cfg.amazon_watches == "off" and defaults.amazon_watches == ""
    assert cfg.front_page["checkout_price_deals"]["enabled"] is True
    assert not defaults.front_page.get("checkout_price_deals", {}).get("enabled", False)
    assert cfg.pinterest["daily_max"] == 10
    assert cfg.meta["facebook"]["daily_max"] == 1 and cfg.meta["facebook"]["hours_utc"] == [17]
    assert cfg.meta["instagram"]["mode"] == "draft"
    assert (cfg.seo_product_pages_max_indexable, cfg.seo_product_pages_max_new_indexable_per_day) == (200, 50)
    assert (defaults.seo_product_pages_max_indexable, defaults.seo_product_pages_max_new_indexable_per_day) == (2000, 50)
