"""Shadow precision, publication privacy and verified product history rules."""

import hashlib
import json
import logging
from datetime import timedelta

import httpx
import pytest

from dealradar import product_shadow
from dealradar.config import AppConfig, load_config
from dealradar.growth import report
from dealradar.publish import product_offers, site
from dealradar.savings import lowest_seen
from dealradar.util import iso
from tests.test_product_entities import insert
from tests.test_product_offer_pages import offers, tree
from tests.test_publish_site import NOW
from tests.test_seo import cfg, db
from tests.test_seo_deal_pages import _page, _path


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)


def serialized(rows):
    live = [site._serialize_deal(d, NOW) for d in rows]
    return live, {d["key"][:8]: _path(r) for d, r in zip(live, rows)}


def history(db, rows):
    for age, price in ((3, 12), (2, 7), (1, 9)):
        db.record_price(rows[0]["dedupe_key"], rows[0]["store"], price,
                        NOW - timedelta(days=age), f"verified-{age}")


def latest(db, cfg):
    return product_shadow.report_data(db, cfg, NOW)["latest"]


@pytest.mark.parametrize("a,b,reason", [
    ({"item": "Acme AB123 2-pack"}, {"item": "Acme AB123 3-pack"}, "pack_size"),
    ({"item": "Acme AB123 pack of 2"}, {"item": "Acme AB123 pack of 4"}, "pack_size"),
    ({"quantity": 1}, {"quantity": 2}, "pack_size"),
    ({"item": "Acme AB123 64GB"}, {"item": "Acme AB123 128GB"}, "capacity"),
    ({"capacity": "1 L"}, {"capacity": "2 L"}, "capacity"),
    ({"item": "Acme AB123 size S"}, {"item": "Acme AB123 size XL"}, "variant"),
    ({"item": "Acme AB123 32-inch"}, {"item": "Acme AB123 42-inch"}, "variant"),
    ({"item": "Acme AB123 red"}, {"item": "Acme AB123 blue"}, "variant"),
    ({"color": "red"}, {"color": "blue"}, "variant"),
    ({"variant": "deluxe"}, {"variant": "standard"}, "variant"),
    ({"item": "Acme AB123 cerulean"}, {"item": "Acme AB123 azure"}, "variant"),
    ({"conditions": []}, {"conditions": ["used"]}, "condition"),
    ({"item": "Acme AB123 new"}, {"item": "Acme AB123 refurbished"}, "condition"),
    ({"condition_grade": "like new"}, {"condition_grade": "acceptable"}, "condition"),
    ({"condition": "used - like new"}, {"condition": "used - acceptable"}, "condition"),
    ({"item": "Acme AB123 headphones"}, {"item": "Acme AB124 headphones"}, "model"),
    ({"model_number": "AB123"}, {"model_number": "AB124"}, "model"),
    ({"final_price": 10}, {"final_price": 3.99}, "price_ratio"),
    ({"final_price": 10}, {"final_price": 25.01}, "price_ratio"),
    ({"final_price": 0}, {"final_price": 10}, "price_ratio"),
])
def test_each_guard_blocks_both_directions_and_records_private_reason(db, cfg, a, b, reason):
    rows = offers(db)[:2]
    # Deliberately corrupt a linked pair to exercise the publication backstop,
    # independent of the product resolver's earlier exact-signature guard.
    rows = [dict(rows[0], **a), dict(rows[1], **b)]
    live, paths = serialized(rows)
    cfg.product_entities_also_at_enabled = True
    blocks, _, shadow = product_offers.evaluate(db, cfg, NOW, live, rows, paths, "guard")
    assert blocks == {} and product_offers.blocks(live, rows, paths) == {}
    assert shadow["also_at_pages"] == 0
    assert shadow["blocked_pairs"] == 2 and shadow["blocked_by_reason"][reason] == 2
    assert all(reason in p["flags"] and not p["shown"] for p in shadow["samples"])


@pytest.mark.parametrize("price", [4, 25, 10])
def test_price_ratio_boundaries_are_inclusive(db, price):
    rows = offers(db)[:2]
    rows[1]["final_price"] = price
    live, paths = serialized(rows)
    assert len(product_offers.blocks(live, rows, paths)) == 2


@pytest.mark.parametrize("facts,kind", [
    ({"asin": "B012345678"}, "asin"), ({"upc": "036000291452"}, "gtin"),
    ({"bestbuy_sku": "123456"}, "sku"), ({"model": "AB123"}, "brand_model"),
])
def test_shadow_counts_match_type_and_uses_published_price(db, cfg, facts, kind):
    rows = [insert(db, "a", store="Walmart", **facts), insert(db, "b", store="Target", **facts)]
    live, paths = serialized(rows)
    rows[1]["final_price"] = 9999
    _, _, shadow = product_offers.evaluate(db, cfg, NOW, live, rows, paths, kind)
    assert shadow["also_at_pages"] == 2
    assert shadow["also_at_pages_by_match"] == {k: (2 if k == kind else 0) for k in product_offers.MATCH_TYPES}
    assert shadow["blocked_pairs"] == 0
    assert all(p["match"] == kind and p["page"]["price"] == 10 and p["other"]["price"] == 10
               for p in shadow["samples"])


def test_shadow_every_publish_private_bounded_repeatable_and_retained(db, cfg, tmp_path, caplog):
    cfg.paywall_open_access = True
    rows = [insert(db, str(i), store=f"Store {i}", asin="B012345678") for i in range(8)]
    history(db, rows)
    out = tmp_path / "site"
    caplog.set_level(logging.INFO)
    result = site.render_site(db, cfg, NOW, out_dir=out, deals=rows, last_run={"run_key": "one"})
    private = latest(db, cfg)
    assert private["published_pages"] == private["also_at_pages"] == private["lowest_seen_pages"] == 8
    assert private["candidate_pairs"] == 56 and len(private["samples"]) == 30
    assert set(private["samples"][0]["page"]) == {"title", "store", "condition", "price"}
    for marker in ("also_at_pages", "lowest_seen_pages", "blocked_pairs", "candidate_pairs"):
        assert marker not in json.dumps(result) and marker not in caplog.text
        assert all(marker.encode() not in data for data in tree(out).values())
    original = db.meta_get(product_shadow.META)
    before = tree(out)
    site.render_site(db, cfg, NOW, out_dir=out, deals=list(reversed(rows)), last_run={"run_key": "one"})
    assert db.meta_get(product_shadow.META) == original
    # Ordering can alter the site; the normal same-input retry stays byte-identical.
    site.render_site(db, cfg, NOW, out_dir=out, deals=rows, last_run={"run_key": "one"})
    assert tree(out) == before
    product_shadow.record(db, cfg, NOW, "two", private)
    assert len(json.loads(db.meta_get(product_shadow.META))) == 2
    product_shadow.record(db, cfg, NOW + timedelta(days=8), "three", private)
    assert list(json.loads(db.meta_get(product_shadow.META))) == ["three"]


def test_flagged_pairs_get_sample_priority_and_identity_is_not_inferred(db, cfg):
    rows = [insert(db, str(i), store=f"Store {i}", asin="B012345678") for i in range(10)]
    rows[-1]["item"] = "Acme AB999 headphones"
    live, paths = serialized(rows)
    _, _, shadow = product_offers.evaluate(db, cfg, NOW, live, rows, paths, "priority")
    assert shadow["blocked_pairs"] == 18
    assert all(p["flags"] for p in shadow["samples"][:18])
    rows = [dict(d, asin="") for d in rows[:2]]
    live, paths = serialized(rows)
    _, _, shadow = product_offers.evaluate(db, cfg, NOW, live, rows, paths, "identity")
    assert shadow["blocked_by_reason"] == {"identity": 2}


def point(price, age, condition="new"):
    return {"price": price, "observed_at": iso(NOW - timedelta(days=age)), "condition": condition}


def test_lowest_thin_data_and_condition_separation():
    assert lowest_seen([point(8, 1), point(7, 1), point(6, 1), point(9, 0)], "new", NOW) is None
    assert lowest_seen([point(8, 2), point(7, 1), point(1, 0, "used")], "new", NOW) is None
    rows = [point(8, 2), point(7, 1), point(9, 0), point(1, 0, "used")]
    assert lowest_seen(rows, "new", NOW) == {"price": 7, "date": "2026-10-08"}
    assert lowest_seen(rows, "used", NOW) is None


def test_lowest_window_invalid_prices_ties_and_utc_days():
    rows = [point(7, 30), point(7, 1), point(9, 0), point(1, 30.001), point(1, -0.01),
            point(-1, 4), point(float("nan"), 4), point(float("inf"), 4),
            point(None, 4), dict(point(1, 0), observed_at="bad")]
    assert lowest_seen(rows, "new", NOW) == {"price": 7, "date": "2026-09-09"}
    assert lowest_seen(list(reversed(rows)), "new", NOW) == lowest_seen(rows, "new", NOW)
    same_utc_day = [dict(point(5, 0), observed_at="2026-10-08T23:30:00-02:00"),
                    dict(point(4, 0), observed_at="2026-10-09T01:30:00+00:00"), point(6, 1)]
    assert lowest_seen(same_utc_day, "new", NOW) is None


def test_lowest_uses_product_history_not_other_products_and_only_detail_changes(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    rows = offers(db)[:2]
    history(db, rows)
    other = insert(db, "other", asin="B012345679")
    for age in (1, 2, 3):
        db.record_price("other", other["store"], 1, NOW - timedelta(days=age), str(age))
    off, on = tmp_path / "off", tmp_path / "on"
    site.render_site(db, cfg, NOW, out_dir=off, deals=rows)
    assert latest(db, cfg)["lowest_seen_pages"] == 2
    cfg.product_entities_lowest_seen_enabled = True
    site.render_site(db, cfg, NOW, out_dir=on, deals=rows)
    before, after = tree(off), tree(on)
    fact = product_offers.lowest_html({"price": 7, "date": "2026-10-07"}).encode()
    assert before.keys() == after.keys()
    for path in before:
        assert after[path].replace(fact, b"") == before[path], path
        assert (fact in after[path]) == path.startswith("deal/")
    assert b"Lowest we've seen: $7.00 on " in fact and b'datetime="2026-10-07"' in fact
    assert "Also at " not in _page(on, _path(rows[0])).read_text()


def test_lowest_filters_stored_conditions_and_never_reads_raw_cached_prices(db, cfg, monkeypatch):
    rows = offers(db)[:2]
    history(db, rows)
    used = insert(db, "used", store="Target", asin="B012345678", conditions=["used"])
    db.record_price("used", "Target", 1, NOW - timedelta(days=1), "verified-used")
    # Even if a future product layer shares the identity across conditions,
    # the historical minimum must keep each observation's physical condition.
    db.conn.execute("UPDATE product_offers SET product_id=? WHERE dedupe_key='used'",
                    (rows[0]["product_id"],))
    db.conn.commit()
    rows[0]["keepa_history"] = [point(0.01, day) for day in (1, 2, 3)]
    original, calls = db.product_price_history, []
    def recorded(product_id, days, *, now):
        calls.append((product_id, days))
        return original(product_id, days, now=now)
    monkeypatch.setattr(db, "product_price_history", recorded)
    live, paths = serialized(rows)
    _, lowest, _ = product_offers.evaluate(db, cfg, NOW, live, rows, paths, "conditions")
    assert lowest == {key: {"price": 7, "date": "2026-10-07"} for key in ("a", "b")}
    assert calls == [(rows[0]["product_id"], 30)]
    db.conn.execute("DELETE FROM price_history WHERE canonical_key='a'")
    db.conn.commit()
    assert product_offers.evaluate(db, cfg, NOW, live, rows, paths, "thin")[1] == {}


def test_switches_do_not_change_shadow_and_disabled_restore_all_files(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    rows = offers(db)
    history(db, rows)
    out = tmp_path / "site"
    site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
    before, shadow = tree(out), db.meta_get(product_shadow.META)
    cfg.product_entities_also_at_enabled = cfg.product_entities_lowest_seen_enabled = True
    site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
    assert db.meta_get(product_shadow.META) == shadow
    html = _page(out, _path(rows[0])).read_text()
    assert "Also at " in html and "Lowest we've seen" in html
    cfg.product_entities_also_at_enabled = cfg.product_entities_lowest_seen_enabled = False
    site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
    assert tree(out) == before and db.meta_get(product_shadow.META) == shadow


@pytest.mark.parametrize("excluded", ["price_unconfirmed", "locked", "dead", "missing_page"])
def test_neither_block_on_excluded_page_or_pair_to_it(db, cfg, excluded):
    rows = offers(db)[:2]
    history(db, rows)
    live, paths = serialized(rows)
    if excluded == "dead":
        live[0]["status"] = "dead"
    elif excluded == "missing_page":
        paths.pop(live[0]["key"][:8])
    else:
        live[0][excluded] = True
    cfg.product_entities_also_at_enabled = cfg.product_entities_lowest_seen_enabled = True
    blocks, lowest, shadow = product_offers.evaluate(db, cfg, NOW, live, rows, paths, "excluded")
    assert blocks == {} and "a" not in lowest
    assert shadow["also_at_pages"] == 0 and shadow["lowest_seen_pages"] == 1
    assert shadow["samples"] == []


def test_labeled_deal_render_excludes_both_blocks_and_target(db, cfg, tmp_path):
    cfg.paywall_open_access = cfg.unconfirmed_label_enabled = True
    cfg.keepa_show_max_unconfirmed_h = 12
    cfg.product_entities_also_at_enabled = cfg.product_entities_lowest_seen_enabled = True
    rows = offers(db)[:2]
    rows[0].update(store="Amazon", first_seen_at=iso(NOW - timedelta(hours=30)))
    history(db, rows)
    site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=rows)
    labeled = _page(tmp_path / "site", _path(rows[0])).read_text()
    assert "Price not confirmed" in labeled
    assert "Lowest we've seen" not in labeled and "Also at " not in labeled
    assert "Also at " not in _page(tmp_path / "site", _path(rows[1])).read_text()
    assert latest(db, cfg)["lowest_seen_pages"] == 1


def test_private_growth_report_reads_snapshot_without_requests_or_log_leaks(db, cfg, tmp_path, monkeypatch, caplog):
    cfg.paywall_open_access = True
    rows = offers(db)
    site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=rows)
    cfg.custom_domain = ""  # No external report sections configured.
    monkeypatch.setattr(report, "get_secret", lambda name: "")
    def no_network(request):
        pytest.fail("shadow must not make requests")
    caplog.set_level(logging.INFO)
    with httpx.Client(transport=httpx.MockTransport(no_network)) as client:
        data, status = report.collect(cfg, NOW, client, db_path=db.path)
    assert status["product_shadow"] == "ok" and data["product_shadow"]["latest"] == latest(db, cfg)
    _, text = report.build_report(cfg, data, status, NOW.date())
    assert "Product page shadow (private)" in text and "asin, would show:" in text
    assert "Acme AB123 headphones | Target | new | $9.00 USD" in text
    assert "Acme AB123" not in caplog.text and "would show" not in caplog.text
    assert product_shadow.report_data(db, cfg, NOW + timedelta(days=8))["latest"] is None


def test_config_defaults_switches_and_guard_limits(tmp_path):
    for cfg in (AppConfig(), load_config()):
        assert not cfg.product_entities_lowest_seen_enabled and not cfg.product_entities_also_at_enabled
    path = tmp_path / "config.yaml"
    path.write_text("product_entities:\n  lowest_seen_enabled: true\n  min_price_ratio: 0.1\n"
                    "  max_price_ratio: 5\n  shadow_retention_days: 0\n")
    cfg = load_config(path)
    assert cfg.product_entities_lowest_seen_enabled
    assert cfg.product_entities_min_price_ratio == 0.4 and cfg.product_entities_max_price_ratio == 2.5
    assert cfg.product_entities_shadow_retention_days == 3


@pytest.mark.parametrize("mode,expected", [
    ("membership", "c44e0fc5de6a76c52378cfa60124a886a9f534d9ba6c08531d9cae0b7dbe5dd2"),
    ("open", "cbf5ec092e4833d5bb1b6e6f61f2f8ae613697383e59e4ba80f64c7cdb123daa"),
    ("support", "5ddcfd43da3883235e72fe9f0aae03c5248b862d8f74dfa51d1ccae008c42abe"),
    ("shipped", "58e22397bcd9987e3cef1f0d58584820867dbaff6283b717f08d697861f9e7fa"),
])
def test_both_off_match_untouched_head_with_real_product_links_and_history(db, tmp_path, mode, expected):
    # Generated on untouched HEAD, with 18 offers / 6 linked products and
    # sufficient history, so the always-on shadow actually has work to do.
    # Task 102 refreshes shipped CSS/JS/version; other modes retain their pins.
    cfg = load_config() if mode == "shipped" else AppConfig(custom_domain="wowpriced.com", paywall_enabled=True)
    if mode == "shipped":
        cfg.raw["first_party_clicks"]["enabled"] = False  # pin predates the click signal switch-on
    if mode != "shipped":
        cfg.support_enabled = mode == "support"
        cfg.paywall_open_access = mode == "open"
    cfg.seo_indexnow_key = "0123456789abcdef0123456789abcdef"
    rows = [insert(db, f"row-{i}", store=("Walmart", "Target", "Woot")[i % 3],
                   item=f"Acme AB12{i // 3} headphones", asin=f"B0123456{i // 3:02d}",
                   final_price=10 + i % 3) for i in range(18)]
    for row in rows:
        for age, price in ((3, 12), (2, 7), (1, 9)):
            db.record_price(row["dedupe_key"], row["store"], price, NOW - timedelta(days=age), str(age))
    out = tmp_path / "site"
    site.render_site(db, cfg, NOW, out_dir=out, deals=rows, last_run={"run_key": "byte-check"})
    files = {path: hashlib.sha256(data).hexdigest() for path, data in tree(out).items()}
    assert hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest() == expected
    assert latest(db, cfg)["lowest_seen_pages"] > 0
