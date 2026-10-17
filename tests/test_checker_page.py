"""Offline Deal Check page, policy, identity parity and real-browser journeys."""

import gzip
import hashlib
import json
import logging
import re
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from dealradar.config import AppConfig, load_config, validated_checker
from dealradar.db import DB
from dealradar.publish import amazon_as_of, checker, checker_page, product_pages, site
from dealradar.savings import product_price_facts
from dealradar.util import iso
from tests.test_checker_index import URLS, add, build, data, end, files, public
from tests.test_product_price_pages import seed
from tests.test_publish_site import NOW
from tests.test_pwa import baseline_config
from tests.history_pins import pre_history_removal, pre_task_279

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def db():
    database = DB(":memory:")
    yield database
    database.close()


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)


def config():
    cfg = baseline_config("open")
    cfg.checker = validated_checker({"enabled": True, "stores": {"amazon": "history", "woot": "link_only"}})
    cfg.seo_product_pages_enabled = True
    return cfg


def render(db, out, cfg=None):
    cfg = cfg or config()
    rows = [add(db, store) for store in ("bestbuy", "target", "woot", "amazon", "walmart", "ebay", "other")]
    rows[1] = end(db, rows[1])
    result = site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
    return (out / "check/index.html").read_text(), result


@pytest.mark.parametrize("mode", ["membership", "open", "support", "shipped"])
@pytest.mark.usefixtures("pre_extension_privacy")
def test_disabled_matches_every_current_head_byte(db, tmp_path, mode):
    # Captured before task 241 edits, untouched 7c202391bee4 (Amazon show).
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
    cfg.open_box_scarcity = False  # recorded before task 273
    if "appeal" in cfg.front_page:
        cfg.front_page["appeal"].get("want", {})["enabled"] = False
        cfg.front_page["appeal"]["season"] = []
    if mode != "shipped":
        cfg.support_enabled = mode == "support"
        cfg.paywall_open_access = mode == "open"
    cfg.paywall_teaser_count = 1
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=seed(db))
    baseline = json.loads((FIX / "checker_page_disabled.json").read_text())[mode]
    assert {name: hashlib.sha256(body).hexdigest() for name, body in files(tmp_path).items()} == baseline["files"]
    assert hashlib.sha256(json.dumps(result["full_payload"], sort_keys=True).encode()).hexdigest() == baseline["members"]
    assert not (tmp_path / "check").exists() and "checker" not in result


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("history,policy", [("show", "history"), ("hide", "history"),
                                         ("show", "link_only"), ("hide", "link_only")])
def test_amazon_history_kill_switch_even_while_disabled(tmp_path, caplog, history, policy, enabled):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"amazon_history": history, "checker": {
        "enabled": enabled, "stores": {"amazon": policy}}}))
    caplog.set_level(logging.INFO, logger="dealradar.config")
    fallback = history == "hide" and policy == "history"
    assert load_config(path).checker["stores"]["amazon"] == ("link_only" if fallback else policy)
    assert bool(caplog.records) is fallback
    if fallback:
        assert "checker.stores.amazon uses link_only while amazon_history: hide" in caplog.text
    assert load_config().checker["enabled"] is False
    # Operator 2026-10-17 (Amazon's email): amazon_history: hide forces link_only.
    assert load_config().checker["stores"]["amazon"] == "link_only"
    assert load_config().amazon_watches == "off"
    assert load_config().checker["stores"]["walmart"] == "history"


def test_amazon_history_uses_existing_facts_verdict_and_time_helper(db):
    cfg = config()
    d = add(db, "amazon")
    models, _, _ = product_pages.prepare(db, cfg, NOW, public([d]), [d])
    page = models[str(d["product_id"])]
    facts = product_price_facts(page["checks"], list(page["offers"].values()), NOW)
    entry = build(db, cfg, [d], models)["amazon:B012345678"]
    assert entry["price"] == d["final_price"] and entry["at"] == iso(NOW)
    assert entry["amazon_as_of"] == amazon_as_of.time_text(NOW)
    assert entry["verdict"] == product_pages.answer(d["final_price"], facts, cfg)
    assert entry["checks"] == 3 and entry["since"] == iso(facts["first"])
    d = end(db, d)
    ended = build(db, cfg, [d], models)["amazon:B012345678"]
    assert ended["kind"] == "ended" and ended["price"] == 10
    assert ended["at"] == iso(NOW - timedelta(days=1))
    assert ended["amazon_as_of"] == amazon_as_of.time_text(NOW - timedelta(days=1))
    assert ended["verdict"] == product_pages.answer(None, facts, cfg)
    cfg.checker["stores"]["amazon"] = "link_only"
    assert build(db, cfg, [d], models) == {}


def test_amazon_without_price_confirmation_has_no_price_as_of_or_verdict(db):
    cfg = config()
    d = add(db, "amazon", key="unconfirmed", price_rechecked_at="", keepa_price_seen_at="")
    models, _, _ = product_pages.prepare(db, cfg, NOW, public([d]), [d])
    entry = build(db, cfg, [d], models)["amazon:B012345678"]
    assert set(entry) == {"kind", "store", "item", "at", "deal_path"}
    d = end(db, d)
    d["price_rechecked_at"] = ""
    db.update_deal(d["dedupe_key"], payload=d)
    assert build(db, cfg, [d], models) == {}


def test_amazon_history_kill_switch_also_guards_runtime_changes(db):
    cfg = config()
    d = add(db, "amazon")
    models, _, _ = product_pages.prepare(db, cfg, NOW, public([d]), [d])
    cfg.amazon_history = "hide"
    entry = build(db, cfg, [d], models)["amazon:B012345678"]
    assert set(entry) == {"kind", "store", "item", "at", "deal_path"}
    assert build(db, cfg, [end(db, d)], models) == {}


def test_enabled_page_seo_navigation_rules_budget_and_private_summary(db, tmp_path):
    cfg = config()
    html, result = render(db, tmp_path, cfg)
    assert f'<title>{checker_page.TITLE.replace("&", "&amp;")}</title>' in html
    assert '<link rel="canonical" href="https://wowpriced.com/check/">' in html
    assert 'name="robots" content="noindex' not in html
    assert '<h1>Is this a good deal?</h1>' in html
    assert checker_page.INTRO.replace("'", "&#x27;") in html
    rules = re.search(r'<script id="check-rules" type="application/json">(.*?)</script>', html)[1]
    assert rules == json.dumps(checker.js_rules(), separators=(",", ":"))
    schemas = [json.loads(s) for s in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
    schema = next(s for s in schemas if s.get("@type") == "WebApplication")
    assert schema["url"] == "https://wowpriced.com/check/"
    assert not {"aggregateRating", "offers", "price", "review"} & set(schema)
    assert '<label for="product-url">Product link</label>' in html and 'aria-live="polite"' in html
    assert "Your link is never sent to stores or other sites." in html
    assert "eBay results cover live deals only, because listings end." in html
    assert "Walmart and eBay results cover live deals only" not in html
    assert html.count('<h3>') == 3 and 'prefers-color-scheme:dark' in html
    assert ':focus-visible' in html and 'min-height:44px' in html
    assert len(gzip.compress(html.encode(), mtime=0)) < 40_000
    assert not re.search(r'<script[^>]+src=|rel="stylesheet"|fonts\.google', html)
    assert "{{" not in html
    assert 'Disallow: /check/data/\n' in (tmp_path / "robots.txt").read_text()
    assert 'Disallow: /check/\n' not in (tmp_path / "robots.txt").read_text()
    assert checker.HEADERS in (tmp_path / "_headers").read_text()
    assert 'https://wowpriced.com/check/' in (tmp_path / 'sitemap-pages.xml').read_text()
    assert 'Try Deal Check</a>' in (tmp_path / "p/index.html").read_text()
    for path in tmp_path.rglob("*.html"):
        footer = re.search(r'<footer>(.*?)</footer>', path.read_text(), re.S)[1]
        assert checker_page.LINK in footer, path
    assert set(result["checker"]) == {"aliases", "live", "ended", "link_only", "dropped_ended", "shards", "bytes"}
    original = files(tmp_path)
    # A second render does not duplicate links or change shard/page bytes.
    rows = db.all_deals()
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    assert files(tmp_path) == original
    cfg.checker["enabled"] = False
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    assert not (tmp_path / "check").exists()
    assert all(checker_page.LINK not in p.read_text() for p in tmp_path.rglob("*.html"))


def test_tracking_and_other_global_scripts_never_receive_pasted_url(db, tmp_path):
    cfg = load_config()
    cfg.amazon_reference = "history"  # recorded before task 276: includes the /p/ reader
    cfg.checker["enabled"] = True
    cfg.paywall_open_access = True
    cfg.seo_product_pages_enabled = True
    cfg.tracking = {"ga4_measurement_id": "G-ABC123", "clarity_project_id": "ywsuwmrauy"}
    html, _ = render(db, tmp_path, cfg)
    assert "googletagmanager" in (tmp_path / "index.html").read_text()
    assert "googletagmanager" not in html and "clarity.ms" not in html and "<!-- wp-tracking -->" not in html
    assert 'name="referrer" content="no-referrer"' in html
    assert "connect-src 'self'" in html
    assert "manifest-src 'self'" in html
    assert html.count("<!-- wp-pwa-footer -->") == (tmp_path / "p/index.html").read_text().count("<!-- wp-pwa-footer -->") == 2


def node(script, *args):
    if not shutil.which("node"):
        pytest.skip("Node unavailable")
    result = subprocess.run(["node", str(Path(__file__).with_name(script)), *map(str, args)],
                            capture_output=True, text=True, timeout=90)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, (result.stdout + result.stderr)[-5000:]


def test_page_javascript_matches_python_aliases_and_every_shard_size(db, tmp_path):
    render(db, tmp_path)
    rows = json.loads((FIX / "checker_aliases.json").read_text())
    # Preserve raw path/query spelling rather than WHATWG URL normalization.
    extra = ["https://SHOP.example:443/a/../b?x=~*+!%2B&x=&UTM_source=x#h",
             "https://shop.example/café?a=é&blank&x=%FF", "https://shop.example?a=b&&c=d",
             "https://amazon.co.uk/dp/B012345678?tag=x", "https://shop.example:81/widget",
             "https://@shop.example/products/widget", "http://:@shop.example/products/widget",
             "https://bestbuy.com/site/widget/１２３.p?skuId=１２３", "https://bestbuy.com/a?skuId=&skuId=2"]
    rows += [{"url": u, "aliases": checker.aliases(u)} for u in extra]
    for row in rows:
        assert row["aliases"] == checker.aliases(row["url"])
        row["shards"] = {str(count): [checker.shard(a, count) for a in row["aliases"]]
                         for count in (16, 32, 64, 128, 256, 512, 1024)}
    fixture = tmp_path / "parity.json"
    fixture.write_text(json.dumps(rows))
    node("checker_page_logic.cjs", tmp_path / "check/index.html", fixture)


def test_chromium_offline_all_result_and_keyboard_flows(db, tmp_path):
    cfg = config()
    cfg.tracking = {"ga4_measurement_id": "G-ABC123", "clarity_project_id": "ywsuwmrauy", "meta_pixel_id": "12345678"}
    render(db, tmp_path, cfg)
    (tmp_path / "browser-fixtures.json").write_text(json.dumps({"urls": URLS, "now": iso(NOW),
        "amazon_as_of": amazon_as_of.time_text(NOW)}))
    node("checker_page_browser.cjs", tmp_path)
