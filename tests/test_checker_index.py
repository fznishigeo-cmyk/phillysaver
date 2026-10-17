"""Offline policy, publication, URL identity and repeat-run Deal Check guards."""

import hashlib
import json
from datetime import timedelta, timezone
from pathlib import Path

import pytest
import yaml

from dealradar.config import AppConfig, CHECKER_STORES, load_config, validated_checker
from dealradar.db import DB
from dealradar.publish import checker, product_pages, site
from dealradar.savings import product_price_facts
from dealradar.util import iso
from tests.test_e2e_offline import env as pipeline_env, test_rerun_is_idempotent as check_rerun
from tests.test_product_entities import insert
from tests.test_product_price_pages import seed
from tests.test_publish_site import NOW
from tests.history_pins import pre_history_removal

FIX = Path(__file__).parent / "fixtures"
URLS = {
    "amazon": "https://www.amazon.com/dp/B012345678",
    "walmart": "https://www.walmart.com/ip/widget/123456789",
    "ebay": "https://www.ebay.com/itm/123456789012",
    "bestbuy": "https://www.bestbuy.com/site/widget/1234567.p?skuId=1234567",
    "target": "https://www.target.com/p/widget/-/A-12345678",
    "woot": "https://home.woot.com/offers/a-good-widget",
    "other": "https://shop.example/products/a-good-widget?utm_source=feed",
}


@pytest.fixture
def db():
    database = DB(":memory:")
    yield database
    database.close()


@pytest.fixture
def cfg():
    cfg = AppConfig(custom_domain="wowpriced.com", paywall_open_access=True,
                    seo_product_pages_enabled=True)
    cfg.checker["enabled"] = True
    return cfg


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)


def add(db, retailer="bestbuy", key=None, **facts):
    key = key or retailer
    d = insert(db, key, **(dict(store=retailer.title(), canonical_url=URLS[retailer],
        item="Widget " + key, category="kitchen", price_since=iso(NOW - timedelta(days=8)),
        price_rechecked_at=iso(NOW), codes=[], conditions=[]) | facts))
    for i, days in enumerate((0, 3, 8)):
        db.record_price(key, d["store"], 10 + i * 10, NOW - timedelta(days=days), f"own-{i}")
    return db.get_deal(key)


def end(db, d, days=0):
    facts = dict(d, death="confirmed", died_at=iso(NOW - timedelta(days=days)),
                 price_rechecked_at=iso(NOW - timedelta(days=days + 1)))
    db.update_deal(d["dedupe_key"], status="dead", payload=facts)
    return db.get_deal(d["dedupe_key"])


def public(rows):
    return [dict(site._serialize_deal(d, NOW), deal_path=f'/deal/{d["dedupe_key"]}/') for d in rows]


def build(db, cfg, rows, models=None):
    return checker.build(db, cfg, NOW, public(rows), models or {}, rows)


def files(out):
    return {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()}


def data(out):
    entries = {}
    for path in (out / "check" / "data").glob("*.json"):
        if path.name not in {"meta.json", "all.json"}:
            entries.update(json.loads(path.read_bytes()))
    return entries


def index_data(out):
    body = json.loads((out / "check/data/all.json").read_bytes())
    assert body["version"] == 1 and body["generated_at"] == iso(NOW)
    previous, values = "", []
    for prefix, suffix in body["values"]:
        previous = previous[:prefix] + suffix
        values.append(json.loads(previous))
    return {alias: {field: values[i] for field, i in zip(body["fields"], row) if i >= 0}
            for alias, row in body["entries"].items()}


def test_default_config_and_valid_overrides(tmp_path):
    assert AppConfig().checker == validated_checker({})
    # Operator 2026-10-17 (Amazon's email): amazon_history: hide turns Amazon to link_only.
    assert load_config().checker == validated_checker({"stores": {"amazon": "history"}}, "hide")
    assert not load_config().checker["enabled"]
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    assert load_config(path).checker == validated_checker({})
    path.write_text(yaml.safe_dump({"checker": {"enabled": True, "ended_days": 365,
        "shards": 1024, "stores": {"target": "live_only", "walmart": "link_only"}}}))
    cfg = load_config(path)
    assert cfg.checker == {"enabled": True, "ended_days": 365, "shards": 1024,
                          "stores": CHECKER_STORES | {"target": "live_only", "walmart": "link_only"}}
    cfg.checker["stores"]["other"] = "link_only"
    assert AppConfig().checker["stores"]["other"] == "history"


@pytest.mark.parametrize("value", [None, [], True, {"enabled": "false"}, {"extra": 1},
    {"ended_days": 0}, {"ended_days": 366}, {"ended_days": True}, {"ended_days": 1.5},
    {"shards": 15}, {"shards": 1025}, {"shards": 48}, {"shards": 256.0},
    {"stores": []}, {"stores": {"target": "off"}}, {"stores": {"unknown": "history"}},
    {"stores": {"target": {}}}, {"stores": {"amazon": "live_only"}},
    {"stores": {"ebay": "history"}}])
def test_invalid_config_fails_even_when_disabled(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"checker": value}))
    with pytest.raises(ValueError, match="checker"):
        load_config(path)


@pytest.mark.parametrize("mode", ["membership", "open", "support", "shipped"])
@pytest.mark.usefixtures("pre_extension_privacy")
def test_disabled_every_published_byte_matches_untouched_head(db, tmp_path, mode):
    # Frozen from untouched d28b551eebd4, at NOW, using this same seed/config.
    cfg = pre_history_removal(load_config()) if mode == "shipped" else AppConfig(custom_domain="wowpriced.com")
    cfg.dark_polish = False  # recorded before task 254
    cfg.search_amazon = False  # recorded before task 259
    cfg.front_page.get("everyday_price", {})["enabled"] = False  # recorded before 2026-10-17 tilt
    cfg.join_prompt_delay_s = 15  # recorded before the 35 s delay
    cfg.keepa_buybox_rule = "shadow"  # recorded before the Buy Box rule went on
    cfg.non_amazon_stores = {}  # recorded before the Woot freshness override
    cfg.front_page.get("top_picks", {}).update(non_amazon_max_age_h=24) if "non_amazon_max_age_h" in cfg.front_page.get("top_picks", {}) else None  # recorded before 8 h
    if mode != "shipped":
        cfg.support_enabled = mode == "support"
        cfg.paywall_open_access = mode == "open"
    cfg.paywall_teaser_count = 1
    cfg.amazon_history = "hide" if mode == "shipped" else cfg.amazon_history  # recorded before amz378_998
    rows, out = seed(db), tmp_path / "site"
    result = site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
    baseline = json.loads((FIX / "checker_disabled.json").read_text())[mode]
    assert {name: hashlib.sha256(body).hexdigest() for name, body in files(out).items()} == baseline["files"]
    assert hashlib.sha256(json.dumps(result["full_payload"], sort_keys=True).encode()).hexdigest() == baseline["members"]
    assert not (out / "check").exists()
    assert "checker" not in result


@pytest.mark.parametrize("amazon_history", ["show", "hide"])
def test_enabled_store_policies_public_metadata_and_no_other_public_fields(db, cfg, tmp_path, amazon_history):
    cfg.amazon_history = amazon_history
    rows = [add(db, s) for s in URLS]
    out = tmp_path / "site"
    result = site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
    entries = data(out)
    assert index_data(out) == entries
    assert len(entries) == 7
    for store, url in URLS.items():
        entry = entries[checker.aliases(url)[0]]
        assert entry["kind"] == "live" and entry["store"] == store
        assert entry["at"] == iso(NOW) and entry["deal_path"].startswith("/deal/")
        if store == "amazon":
            assert set(entry) == {"kind", "store", "item", "at", "deal_path"}
        elif store == "ebay":
            assert set(entry) == {"kind", "store", "item", "at", "deal_path", "price", "credit"}
        else:
            assert set(entry) == {"kind", "store", "item", "at", "deal_path", "price", "credit",
                                 "p_path", "verdict", "checks", "since"}
    meta = json.loads((out / "check/data/meta.json").read_bytes())
    assert meta == {"generated_at": iso(NOW), "shards": 256, "stores": CHECKER_STORES,
                    "counts": {"kind": {"live": 7}, "store": dict.fromkeys(URLS, 1)}}
    report = result["checker"]
    assert report == {"aliases": 7, "live": 7, "ended": 0, "link_only": 1,
        "dropped_ended": 0, "shards": 256, "bytes": sum(p.stat().st_size for p in (out / "check/data").iterdir())}
    assert "Disallow: /check/data/\n" in (out / "robots.txt").read_text()
    assert checker.HEADERS in (out / "_headers").read_text()
    for name, body in files(out).items():
        if name == "check/index.html":
            continue
        if name.endswith((".html", ".xml")) or name == "deals.json":
            assert b'"checker"' not in body and b"/check/data/" not in body


def test_locked_members_unconfirmed_and_flagged_never_contribute(db, cfg, tmp_path):
    rows = [add(db, key=str(i), canonical_url=f"https://www.bestbuy.com/site/widget/{100+i}.p") for i in range(10)]
    mutations = [{"locked": True}, {"members_only": True}, {"member_only": True},
                 {"price_unconfirmed": True}, {"flags": ["suspect_msrp"]},
                 {"gone_suspect_at": iso(NOW)}, {"flags": ["price_moved"]}, {"flagged": True}]
    for row, mutation in zip(rows, mutations):
        row.update(mutation)
    result = build(db, cfg, rows)
    assert set(result) == {"bestbuy:108", "bestbuy:109"}
    # Publication's real closed-paywall projection supplies only one free row.
    cfg.paywall_open_access = False
    cfg.paywall_teaser_count = 1
    out = tmp_path / "site"
    site.render_site(db, cfg, NOW, out_dir=out, deals=rows[-2:])
    published = json.loads((out / "deals.json").read_bytes())["deals"]
    assert len([d for d in published if d.get("locked")]) == 1
    assert len(data(out)) == 1
    assert {e["item"] for e in data(out).values()} == {d["item"] for d in published if not d.get("locked")}


@pytest.mark.parametrize("requirement", [
    {"codes": ["SAVE10"]}, {"stack_steps": ["Clip coupon"]}, {"conditions": ["subscribe_save"]},
    {"conditions": ["membership_only"]}, {"conditions": ["prime_only"]},
    {"conditions": ["in_cart"]}, {"conditions": ["in_store_only"]}, {"conditions": ["multi_buy"]},
    {"rebates": ["$5 rebate"]}, {"deferred_value": "$10 gift card"},
    {"stack_steps": ["Members pay $10"]}, {"stack_steps": ["Buy 2 for $10"]},
    {"stack_steps": ["Pay with a gift card"]}, {"conditions": ["new_customer"]},
    {"basis": "bogo"}, {"deal_type": "b2g1"},
])
def test_conditional_price_is_live_deal_only_never_history_or_verdict(db, cfg, requirement):
    d = add(db, **requirement)
    models, _, _ = product_pages.prepare(db, cfg, NOW, public([d]), [d])
    entry = next(iter(build(db, cfg, [d], models).values()))
    assert entry["price"] == 10 and entry["kind"] == "live"
    assert entry["verdict"] == entry["since"] == "" and entry["checks"] == 0
    ended = end(db, d)
    assert build(db, cfg, [ended], models) == {}


def test_verdict_exactly_reuses_product_answer_and_filters_restricted_checks(db, cfg):
    cfg.checker["stores"]["walmart"] = "live_only"
    d = add(db)
    models, _, _ = product_pages.prepare(db, cfg, NOW, public([d]), [d])
    page = models[str(d["product_id"])]
    facts = product_price_facts(page["checks"], list(page["offers"].values()), NOW)
    entry = build(db, cfg, [d], models)["bestbuy:1234567"]
    assert entry["verdict"] == product_pages.answer(d["final_price"], facts, cfg)
    assert entry["verdict"].startswith("Yes — $10.00")
    assert entry["checks"] == facts["checks"] == 3
    assert entry["since"] == iso(NOW - timedelta(days=8)) and entry["p_path"] == page["path"]
    # Mixed product histories can contain other policies and conditional offers.
    for s, changes in (("amazon", {}), ("walmart", {}), ("ebay", {}), ("target", {"codes": ["ONLY"]})):
        add(db, s, **changes)
        page["offers"][s] = {"price": 999, "date": iso(NOW), "published_at": iso(NOW), "credits": []}
        page["checks"].append({"dedupe_key": s, "price": 999, "observed_at": iso(NOW)})
    rows = db.all_deals()
    for row in rows:
        row["product_id"] = d["product_id"]
    assert build(db, cfg, rows, models)["bestbuy:1234567"] == entry


def test_quarantined_offer_cannot_supply_another_offers_verdict(db, cfg):
    d = add(db)
    models, _, _ = product_pages.prepare(db, cfg, NOW, public([d]), [d])
    baseline = build(db, cfg, [d], models)
    other = add(db, "target")
    other["product_id"] = None  # an old product link has since been quarantined
    page = models[str(d["product_id"])]
    page["offers"][other["dedupe_key"]] = {"date": iso(NOW), "price": 1000}
    page["checks"].append({"dedupe_key": other["dedupe_key"], "observed_at": iso(NOW), "price": 1000})
    assert build(db, cfg, [d, other], models)["bestbuy:1234567"] == baseline["bestbuy:1234567"]


def test_ended_is_last_dated_plain_check_and_restricted_stores_have_none(db, cfg):
    rows = [add(db, s) for s in URLS]
    models, _, _ = product_pages.prepare(db, cfg, NOW, public(rows), rows)
    rows = [end(db, d) for d in rows]
    result = build(db, cfg, rows, models)
    assert {e["store"] for e in result.values()} == {"bestbuy", "target", "woot", "walmart", "other"}
    assert all(e["kind"] == "ended" and e["price"] == 10 and e["at"] == iso(NOW) for e in result.values())
    bestbuy = rows[3]
    page = models[str(bestbuy["product_id"])]
    facts = product_price_facts(page["checks"], list(page["offers"].values()), NOW)
    assert result["bestbuy:1234567"]["verdict"] == product_pages.answer(None, facts, cfg)
    # Identified product models retain public provenance outside the grey wall.
    assert checker.build(db, cfg, NOW, [], models) == {
        k: dict(v, deal_path="") for k, v in result.items() if v["p_path"]}
    old = end(db, bestbuy, days=91)
    assert "bestbuy:1234567" not in build(db, cfg, [old], models)


def test_duplicate_alias_prefers_live_then_latest_deterministically(db, cfg):
    a = add(db, key="a")
    b = add(db, key="b", now=NOW - timedelta(hours=2),
            price_rechecked_at=iso(NOW - timedelta(hours=1)))
    expected = build(db, cfg, [a, b])
    assert build(db, cfg, [b, a]) == expected
    assert expected["bestbuy:1234567"]["item"] == a["item"]
    ended = end(db, a)
    assert build(db, cfg, [ended, b])["bestbuy:1234567"]["item"] == b["item"]


def test_ended_window_boundaries_use_verified_dates_and_never_add_state(db, cfg):
    d = add(db, now=NOW - timedelta(days=100), price_since=iso(NOW - timedelta(days=100)))
    db.conn.execute("DELETE FROM price_history")
    for days, price in ((91, 8), (90, 9), (1, 1)):
        db.record_price(d["dedupe_key"], d["store"], price, NOW - timedelta(days=days), str(days))
    d = end(db, d, days=90)
    before = list(db.conn.iterdump())
    entry = build(db, cfg, [d])["bestbuy:1234567"]
    assert entry["kind"] == "ended" and entry["price"] == 9
    assert entry["at"] == iso(NOW - timedelta(days=90))
    assert list(db.conn.iterdump()) == before
    db.conn.execute("DELETE FROM price_history WHERE price=9")
    assert build(db, cfg, [d]) == {}  # older and post-death readings cannot replace it


def test_amazon_url_has_link_only_policy_even_with_other_store_name(db, cfg):
    d = add(db, "amazon", store="Unrelated seller", price_rechecked_at="")
    entry = build(db, cfg, [d])["amazon:B012345678"]
    assert entry["store"] == "amazon"
    assert set(entry) == {"kind", "store", "item", "at", "deal_path"}


def test_alias_fallback_keeps_selected_variation_and_never_guesses_an_id():
    url = "https://www.ebay.com/itm/305123456789?var=123&utm_source=feed"
    assert checker.aliases(url) == ["url:https://www.ebay.com/itm/305123456789?var=123"]
    assert checker.aliases("https://m.target.com/p/widget/-/A-82493853?utm_source=feed") == ["target:82493853"]
    assert checker.aliases("https://amazon.com.evil.example/dp/B012345678") == [
        "url:https://amazon.com.evil.example/dp/B012345678"]
    assert checker.aliases("https://amzn.to/a-product") == []


@pytest.mark.parametrize("count", [16, 32, 64, 128, 256, 512, 1024])
def test_shard_count_first_sha_digits_and_repeat_bytes(db, cfg, tmp_path, count):
    cfg.checker["shards"] = count
    entries = build(db, cfg, [add(db)])
    report = checker.publish(tmp_path, cfg, NOW, entries)
    before = files(tmp_path)
    assert len(before) == count + 2 and data(tmp_path) == index_data(tmp_path) == entries
    assert checker.publish(tmp_path, cfg, NOW.astimezone(timezone(timedelta(hours=12))), entries) == report
    assert files(tmp_path) == before
    alias = next(iter(entries))
    digest = hashlib.sha256(alias.encode()).hexdigest()
    assert int(checker.shard(alias, count), 16) == int(digest, 16) >> (256 - (count - 1).bit_length())
    if count == 256:
        assert checker.shard(alias, count) == digest[:2]


def test_actual_64kb_cap_drops_oldest_ended_first_and_removes_old_shards(cfg, tmp_path):
    cfg.checker["shards"] = 16
    keys = []
    for i in range(10000):
        alias = "url:https://shop.example/" + str(i)
        if checker.shard(alias, 16) == "00":
            keys.append(alias)
            if len(keys) == 25:
                break
    entries = {key: {"kind": "ended", "store": "other", "item": "é" * 1000,
                    "at": iso(NOW - timedelta(days=i)), "price": 10, "deal_path": ""}
               for i, key in enumerate(keys)}
    entries[keys[0]]["kind"] = "live"
    folder = tmp_path / "check/data"
    folder.mkdir(parents=True)
    (folder / "obsolete.json").write_text("private old data")
    result = checker.publish(tmp_path, cfg, NOW, entries)
    kept = data(tmp_path)
    assert index_data(tmp_path) == kept
    assert result["dropped_ended"] > 0 and keys[0] in kept
    assert result["dropped_ended"] == len(entries) - len(kept)
    assert keys[-1] not in kept and keys[1] in kept
    assert all(p.stat().st_size < 64 * 1024 for p in folder.glob("*.json") if p.name not in {"meta.json", "all.json"})
    assert not (folder / "obsolete.json").exists()
    before = files(tmp_path)
    assert checker.publish(tmp_path, cfg, NOW, dict(reversed(list(entries.items())))) == result
    assert files(tmp_path) == before
    too_big = {keys[0]: dict(entries[keys[0]], item="x" * 65536)}
    with pytest.raises(ValueError, match="live entries exceed"):
        checker.publish(tmp_path, cfg, NOW, too_big)
    assert files(tmp_path) == before


def test_disable_removes_previous_data_and_directives(db, cfg, tmp_path):
    rows = [add(db)]
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    assert data(tmp_path)
    assert (tmp_path / "check/data/all.json").is_file()
    cfg.checker["enabled"] = False
    site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    assert not (tmp_path / "check").exists()
    assert "/check/data/" not in (tmp_path / "robots.txt").read_text()
    assert checker.HEADERS not in (tmp_path / "_headers").read_text()


@pytest.mark.parametrize("amazon", ["show", "hide"])
@pytest.mark.parametrize("policy", ["history", "live_only", "link_only"])
def test_compact_index_preserves_all_policy_filtered_fields(db, cfg, tmp_path, amazon, policy):
    cfg.amazon_history = amazon
    for store in ("bestbuy", "target", "walmart", "woot", "other"):
        cfg.checker["stores"][store] = policy
    cfg.checker["stores"]["amazon"] = "history"
    rows = [add(db, s, item="Café 🎧 " + s) for s in URLS]
    models, _, _ = product_pages.prepare(db, cfg, NOW, public(rows), rows)
    rows[3] = end(db, rows[3])
    entries = build(db, cfg, rows, models)
    checker.publish(tmp_path, cfg, NOW, entries)
    assert index_data(tmp_path) == data(tmp_path) == entries
    original = files(tmp_path)
    checker.publish(tmp_path, cfg, NOW, dict(reversed(list(entries.items()))))
    assert files(tmp_path) == original


def synthetic_aliases():
    # Full history rows, distinct titles, prices, paths and source URLs; no lost fields.
    return {f"bestbuy:{6500000+i}": {
        "kind": "live", "store": "bestbuy",
        "item": f"Example Brand Wireless Headphones Model {i:05d} (Black)",
        "at": iso(NOW), "deal_path": f"/deal/wireless-headphones-{i:08x}/",
        "price": round(19.99 + i % 200, 2),
        "credit": [{"name": "Example deals", "url": "https://deals.example/posts/" + str(i)}],
        "p_path": f"/p/wireless-headphones-{i:08x}/", "verdict": "Yes — lower than usual.",
        "checks": 50, "since": iso(NOW - timedelta(days=88))} for i in range(10_000)}


def test_compact_10000_alias_build_size_and_repeat_bytes(cfg, tmp_path):
    entries = synthetic_aliases()
    report = checker.publish(tmp_path, cfg, NOW, entries)
    path = tmp_path / "check/data/all.json"
    body = path.read_bytes()
    assert len(body) < checker.MAX_INDEX_BYTES == 1_500_000
    assert report["aliases"] == 10_000 and report["dropped_ended"] == 0
    assert index_data(tmp_path) == data(tmp_path) == entries
    assert checker.publish(tmp_path, cfg, NOW, dict(reversed(list(entries.items())))) == report
    assert path.read_bytes() == body
    print(f"all.json for 10,000 full-history aliases: {len(body):,} bytes")


def test_index_cap_drops_oldest_ended_with_ties_and_preserves_live(cfg, tmp_path, monkeypatch):
    entries = {f"bestbuy:{i}": {"kind": "ended", "store": "bestbuy", "item": "Widget " + str(i),
        "at": iso(NOW - timedelta(days=i // 2)), "price": i, "deal_path": f"/deal/widget-{i}/"}
        for i in range(24)}
    entries["bestbuy:live"] = dict(entries["bestbuy:0"], kind="live")
    oldest = sorted((k for k in entries if entries[k]["kind"] == "ended"), key=lambda k: (entries[k]["at"], k))
    expected = {k: v for k, v in entries.items() if k not in oldest[:10]}
    monkeypatch.setattr(checker, "MAX_INDEX_BYTES", len(checker.compact_index(NOW, expected)) + 1)
    report = checker.publish(tmp_path, cfg, NOW, entries)
    assert report["dropped_ended"] == 10
    assert index_data(tmp_path) == data(tmp_path) == expected
    assert (tmp_path / "check/data/all.json").stat().st_size < checker.MAX_INDEX_BYTES
    before = files(tmp_path)
    assert checker.publish(tmp_path, cfg, NOW, dict(reversed(list(entries.items())))) == report
    assert files(tmp_path) == before
    monkeypatch.setattr(checker, "MAX_INDEX_BYTES", len(checker.compact_index(NOW, {"bestbuy:live": entries["bestbuy:live"]})))
    with pytest.raises(ValueError, match="live entries exceed all.json"):
        checker.publish(tmp_path, cfg, NOW, entries)
    assert files(tmp_path) == before


def test_direct_disabled_publish_never_writes_an_index(cfg, tmp_path):
    cfg.checker["enabled"] = False
    assert checker.publish(tmp_path, cfg, NOW, {}) == {}
    assert not (tmp_path / "check/data/all.json").exists()


def test_alias_fixture_and_json_safe_shared_rules():
    fixtures = json.loads((FIX / "checker_aliases.json").read_text())
    assert len(fixtures) >= 30
    for row in fixtures:
        assert checker.aliases(row["url"]) == row["aliases"], row["url"]
    assert {alias.split(":")[0] for row in fixtures for alias in row["aliases"]} == set(URLS) - {"other"} | {"url"}
    rules = checker.js_rules()
    assert json.loads(json.dumps(rules)) == rules
    assert rules["amazon"]["pattern"] == checker._ASIN_RE.pattern
    assert rules["bestbuy"]["paths"] == list(checker.BESTBUY_PATHS)
    assert rules["walmart"]["path"] == checker.WALMART_PATH
    assert rules["woot"]["path"] == checker.WOOT_PATH


@pytest.mark.parametrize("audit_mode", ["shadow", "enforce"])
def test_enabled_pipeline_rerun_keeps_shards_rows_and_sends(pipeline_env, capsys, audit_mode):
    path = Path(pipeline_env["cfg"])
    path.write_text(path.read_text().replace("checker:\n  enabled: false", "checker:\n  enabled: true"))
    check_rerun(pipeline_env, capsys, audit_mode)
