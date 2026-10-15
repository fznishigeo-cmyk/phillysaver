"""Task 108: paid-data measurements, unchanged shadow bytes, bounded probe."""

from copy import deepcopy
from datetime import timedelta
import json
import logging
from pathlib import Path

import httpx
import pytest
import yaml

from dealradar import buybox, pipeline, rank, util, verify
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.http_client import PoliteFetcher
from dealradar.publish import site
from scripts import keepa_buybox_probe as probe
from tests.http_helpers import HostClock
from tests.test_price_accuracy_engine import NOW, api, bucket, cfg, db, seed
from tests.test_product_offer_pages import tree
from tests.test_e2e_offline import env


def record(asin, amazon=2500, new=2500, **stats):
    return {"asin": asin, "lastUpdate": int(NOW.timestamp() / 60) - verify.KEEPA_EPOCH_MINUTES,
            "stats": {"current": [amazon, new], **stats}}


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_config_modes_and_shipped_shadow(tmp_path, mode):
    assert AppConfig().keepa_buybox_rule == "off"
    assert load_config().keepa_buybox_rule == "shadow"
    path = tmp_path / "config.yaml"
    path.write_text("keepa:\n  buybox_rule: " + mode + "\n")
    assert load_config(path).keepa_buybox_rule == mode
    path.write_text("keepa:\n  buybox_rule: invalid\n")
    with pytest.raises(ValueError, match="keepa.buybox_rule"):
        load_config(path)


def test_summary_counts_all_rechecked_new_amazon_and_five_largest_gaps(db, cfg, monkeypatch):
    pairs = [(2500, 2600), (5000, 2500), (2500, 2500), (2751, 2500), (2752, 2500),
             (-1, 2500), (10000, 5000), (-1, -1), (None, None), (8000, 2500),
             (7000, 2500), (6000, 2500)]
    products = {}
    for i, (amazon, new) in enumerate(pairs):
        asin = f"B{i:09d}"
        seed(db, cfg, asin=asin, codes=["SAVE"] if i == 6 else [])
        products[asin] = record(asin, amazon, new)
    seed(db, cfg, asin="B999999999", conditions=["used"])
    # Used off-wall rows do not enter the new-condition measurement.
    calls = api(monkeypatch, products)
    bucket(db, 1000)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert result["price_source"] == {
        "checked": 12, "confirmed_amazon": 2, "confirmed_new": 8, "not_confirmed": 2,
        "amazon_above_10pct": 6, "largest_gaps": [
            {"asin": f"B{i:09d}", "final": 25.0, "amazon": pairs[i][0] / 100,
             "new": pairs[i][1] / 100} for i in (6, 9, 10, 11, 1)]}
    assert all("buybox" not in c and c["stats"] == 1 for c in calls)
    before = db.all_deals()
    count = len(calls)
    rerun = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert rerun["price_source"] == buybox.empty_summary()
    assert db.all_deals() == before and len(calls) == count


def test_photo_batch_price_verdict_also_counts_without_extra_requests(db, cfg, monkeypatch):
    row = seed(db, cfg, price_rechecked_at=util.iso(NOW - timedelta(minutes=5)))
    row["image_url"] = ""
    db.update_deal(row["dedupe_key"], payload=row)
    calls = api(monkeypatch, {row["asin"]: record(row["asin"], 5000)})
    bucket(db, 1000)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=True)
    assert result["image_backfill"] == result["price_source"]["confirmed_new"] == 1
    assert result["price_source"]["amazon_above_10pct"] == 1
    assert len(calls) == 1 and "buybox" not in calls[0]
    assert buybox.would_unconfirm(db.get_deal(row["dedupe_key"]), NOW)


@pytest.mark.parametrize("bad", [-1, -2, None, True, "2500", float("nan"), float("inf")])
def test_invalid_price_or_shipping_is_unknown(bad):
    values = buybox.prices(record("B000000001", bad, bad, buyBoxPrice=2500, buyBoxShipping=bad))
    assert values == {"amazon": None, "new": None, "buybox": None, "buybox_is_amazon": None}


def test_buybox_total_never_double_adds_shipping_and_seller_is_not_fba():
    current = [2500, 2400] + [-1] * 16 + [3000]
    values = buybox.prices({"stats": {"current": current, "buyBoxShipping": 500,
                                     "buyBoxIsFBA": True}})
    assert values["buybox"] == 30 and values["buybox_is_amazon"] is None
    assert buybox.prices({"stats": {"buyBoxPrice": 2500, "buyBoxShipping": 500,
                                    "buyBoxIsAmazon": False}})["buybox"] == 30
    assert buybox.prices({"stats": {"current": current, "buyBoxIsUnqualified": True}})["buybox"] is None
    # A separate used Buy Box must not replace the explicitly NEW fields.
    assert buybox.prices({"stats": {"current": current, "buyBoxUsedPrice": 100}})["buybox"] == 30


@pytest.mark.parametrize("changes", [{"conditions": ["open_box"]},
    {"store": "Walmart", "canonical_url": "https://walmart.com/item"},
    {"final_price": 24}, {"asin": "B000000002"},
    {"price_since": util.iso(NOW + timedelta(minutes=1))}])
def test_rule_never_reuses_wrong_condition_store_identity_or_price(db, cfg, changes):
    deal = seed(db, cfg)
    buybox.remember(deal, record(deal["asin"], 5000), NOW, now=NOW)
    assert buybox.would_unconfirm(deal, NOW)
    deal.update(changes)
    assert not buybox.would_unconfirm(deal, NOW)


@pytest.mark.parametrize("support,opened", [(False, False), (False, True), (True, False), (True, True)])
def test_shadow_preserves_every_published_byte_and_paid_call(db, cfg, tmp_path, monkeypatch, support, opened):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg.support_enabled, cfg.paywall_open_access = support, opened
    cfg.paywall_teaser_count = 1
    snapshots, requests = [], []
    for mode in ("off", "shadow"):
        with_db = DB(":memory:")
        cfg.keepa_buybox_rule = mode
        products = {}
        for i in range(3):
            row = seed(with_db, cfg, asin=f"B{i:09d}")
            products[row["asin"]] = record(row["asin"], 5000)
        calls = api(monkeypatch, products)
        bucket(with_db, 1000)
        summary = pipeline._recheck_prices(with_db, cfg, NOW, image_backfill=False)
        out = tmp_path / mode
        published = site.render_site(with_db, cfg, NOW, out_dir=out)
        snapshots.append(tree(out))
        requests.append(deepcopy(calls))
        report = buybox.shadow_summary(with_db, cfg, NOW, published["public_deals"])
        if mode == "shadow":
            assert report["would_label"] == (3 if support or opened else 1)
            assert report["would_hide"] == 0
            assert all(b"buybox_check" not in content for content in snapshots[-1].values())
            assert summary["price_source"]["amazon_above_10pct"] == 3
        with_db.close()
    assert snapshots[0] == snapshots[1]
    assert requests[0] == requests[1]


@pytest.mark.parametrize("label_enabled", [False, True])
def test_on_labels_or_hides_exactly_shadow_count_without_killing(db, cfg, monkeypatch, tmp_path, label_enabled):
    cfg.unconfirmed_label_enabled = label_enabled
    cfg.keepa_buybox_rule = "shadow"
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    products = {}
    for i in range(6):
        row = seed(db, cfg, asin=f"B{i:09d}")
        products[row["asin"]] = record(row["asin"], 5000 if i in (0, 2) else -1)
    products["B000000001"]["stats"].update(buyBoxPrice=2500, buyBoxShipping=500)
    products["B000000002"]["stats"].update(buyBoxPrice=2500, buyBoxShipping=0)
    products["B000000003"]["stats"].update(buyBoxPrice=2751, buyBoxShipping=0)
    products["B000000004"]["stats"].update(buyBoxPrice=2000, buyBoxShipping=0)
    calls = api(monkeypatch, products)
    bucket(db, 1000)
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    out = tmp_path / "site"
    baseline = site.render_site(db, cfg, NOW, out_dir=out)
    report = buybox.shadow_summary(db, cfg, NOW, baseline["public_deals"])
    assert report == {"mode": "shadow", "evaluated": 6,
                      "would_label": 2 if label_enabled else 0,
                      "would_hide": 0 if label_enabled else 2}
    cfg.keepa_buybox_rule = "on"
    enforced = site.render_site(db, cfg, NOW, out_dir=out)
    rows = enforced["public_deals"]
    assert sum(bool(d.get("price_unconfirmed")) for d in rows) == report["would_label"]
    assert len(baseline["public_deals"]) - len(rows) == report["would_hide"]
    expected = {"https://www.amazon.com/dp/B000000000", "https://www.amazon.com/dp/B000000001"}
    changed = {d["canonical_url"] for d in db.all_deals() if verify.price_unconfirmed(d, cfg, NOW)}
    assert changed == expected
    assert not changed & {d["canonical_url"] for d in rank.published_deals(db, cfg, NOW)}
    assert all(d["status"] == "alive" and not d.get("gone_suspect_at") for d in db.all_deals())
    # Same-key rechecks buy nothing and cannot re-confirm a still-higher click price.
    before = tree(out)
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    site.render_site(db, cfg, NOW, out_dir=out)
    assert tree(out) == before and len(calls) == 1


def probe_state(db, cfg, n=35):
    rows = [seed(db, cfg, asin=f"B{i:09d}") for i in range(n)]
    # Actual published picks lead the wall; the scheduler's candidate list
    # is deliberately different and includes a non-public key.
    db.meta_set(site.WALL_META, json.dumps(list(dict.fromkeys(
        [rows[-1]["dedupe_key"], rows[1]["dedupe_key"]] + [d["dedupe_key"] for d in rows]))))
    db.meta_set(site.PICKS_META, json.dumps(["unpublished", rows[0]["dedupe_key"]] +
                                         [rows[1]["dedupe_key"]]))
    return rows


def transport_probe(db, cfg, handler, key="secret-probe-key"):
    clock = HostClock()
    cache = DB(":memory:")
    fetcher = PoliteFetcher(cache, cfg, transport=httpx.MockTransport(handler),
                            sleep_fn=clock.sleep, clock=clock)
    try:
        return probe.probe(db, fetcher, key)
    finally:
        fetcher.close()
        cache.close()


def test_probe_one_request_ceiling_selection_aggregates_and_safe_log(db, cfg, caplog, tmp_path):
    rows = probe_state(db, cfg)
    requests = []
    secret = "secret-probe-key"

    def handler(req):
        requests.append(req)
        asins = req.url.params["asin"].split(",")
        assert len(asins) == 30 and asins[:3] == [rows[-1]["asin"], rows[1]["asin"], rows[0]["asin"]]
        assert dict(req.url.params) == {"key": secret, "domain": "1", "asin": ",".join(asins),
                                        "stats": "1", "history": "0", "buybox": "1"}
        assert "hello@wowpriced.com" in req.headers["User-Agent"]
        products = [record(a, buyBoxPrice=[2500, 2600, 3000][i % 3], buyBoxShipping=0,
                           buyBoxIsAmazon=i % 2 == 0, buyBoxSellerId=secret)
                    for i, a in enumerate(asins[:-1])]
        return httpx.Response(200, json={"products": products, "tokensConsumed": 90, "tokensLeft": 777,
                                         "error": secret})

    snapshot = tmp_path / "state.db"
    target = DB(snapshot)
    db.conn.backup(target.conn)
    target.close()
    before = snapshot.read_bytes()
    readonly = DB(snapshot, read_only=True)
    with caplog.at_level(logging.DEBUG):
        report = transport_probe(readonly, cfg, handler)
    readonly.close()
    assert snapshot.read_bytes() == before
    assert len(requests) == 1
    assert report["max_tokens_requested"] == 90 <= report["token_ceiling"] == 100
    assert report["tokensConsumed"] == 90 and report["tokensLeft"] == 777
    assert (report["within_2pct"], report["within_10pct"], report["above_10pct"], report["missing_buybox"]) == (10, 20, 9, 1)
    assert len(report["examples"]) == 10
    assert all(set(row) == {"asin", "final", "amazon", "new", "buybox", "buybox_is_amazon"}
               for row in report["examples"])
    assert secret not in caplog.text + json.dumps(report)


@pytest.mark.parametrize("status", [302, 304, 401, 429, 500, "transport", "malformed"])
def test_probe_failure_never_retries_redirects_or_leaks_key(db, cfg, status, caplog):
    probe_state(db, cfg, 2)
    calls = []
    def handler(req):
        calls.append(req)
        if status == "transport":
            raise httpx.ReadTimeout(str(req.url), request=req)
        if status == "malformed":
            return httpx.Response(200, text="secret-probe-key")
        return httpx.Response(status, json={"error": "secret-probe-key", "tokensConsumed": 0, "tokensLeft": 0},
                              headers={"Location": "https://example.com/?key=secret-probe-key"})
    with caplog.at_level(logging.DEBUG):
        report = transport_probe(db, cfg, handler)
    assert len(calls) == 1 and report["status"] != "ok"
    assert "secret-probe-key" not in json.dumps(report) + caplog.text


def test_probe_empty_key_or_population_never_spends(db, cfg):
    def fail(req):
        pytest.fail("no HTTP calls")
    assert transport_probe(db, cfg, fail)["status"] == "no_published_candidates"
    probe_state(db, cfg, 2)
    assert transport_probe(db, cfg, fail, key="")["status"] == "missing_key"


def test_probe_selection_excludes_unpublished_used_non_amazon_dead_and_duplicate_asins(db, cfg):
    rows = probe_state(db, cfg, 7)
    for row, change in zip(rows, [{"conditions": ["used"]}, {"status": "dead"},
            {"store": "Target", "canonical_url": "https://target.com/p/item"},
            {"asin": "invalid"}, {"asin": rows[-1]["asin"]}]):
        row.update(change)
        db.update_deal(row["dedupe_key"], payload=row, status=row["status"], canonical_url=row["canonical_url"])
        if "store" in change:
            db.conn.execute("UPDATE deals SET store=? WHERE dedupe_key=?", (change["store"], row["dedupe_key"]))
    # An attractive candidate which has never appeared on the public wall.
    private = seed(db, cfg, asin="B999999999")
    db.meta_set(site.PICKS_META, json.dumps([private["dedupe_key"], rows[-1]["dedupe_key"]]))
    assert [d["asin"] for d in probe.selected(db)] == [rows[-1]["asin"], rows[-2]["asin"]]


def test_old_or_unknown_buybox_clock_cannot_borrow_fresh_listing_time(db, cfg):
    deal = seed(db, cfg)
    p = record(deal["asin"], buyBoxPrice=5000, buyBoxShipping=0)
    for stamp in (-1, 0, p["lastUpdate"] + 1):
        p["stats"]["lastBuyBoxUpdate"] = stamp
        buybox.remember(deal, p, NOW, now=NOW)
        assert not buybox.would_unconfirm(deal, NOW)
    p["stats"]["lastBuyBoxUpdate"] = p["lastUpdate"]
    buybox.remember(deal, p, NOW, now=NOW)
    assert buybox.would_unconfirm(deal, NOW)


def test_missing_or_older_buybox_read_never_clears_an_existing_gap(db, cfg):
    row = seed(db, cfg)
    p = record(row["asin"], buyBoxPrice=5000, buyBoxShipping=0)
    buybox.remember(row, p, NOW, now=NOW)
    before = deepcopy(row["buybox_check"])
    for record_without_price in ({}, {"stats": None}, {"stats": {"current": [-1, -1]}}):
        buybox.remember(row, record_without_price, NOW, now=NOW)
        assert row["buybox_check"] == before
    p["stats"]["buyBoxPrice"] = 2500
    for stamp in (-1, p["lastUpdate"] - 60, p["lastUpdate"] + 60):
        p["stats"]["lastBuyBoxUpdate"] = stamp
        buybox.remember(row, p, NOW, now=NOW)
        assert row["buybox_check"] == before
    p["stats"]["lastBuyBoxUpdate"] = p["lastUpdate"]
    buybox.remember(row, p, NOW, now=NOW)
    assert not buybox.would_unconfirm(row, NOW)


def test_repricing_binds_rule_to_corrected_final_and_stale_read_cannot_overwrite(db, cfg, monkeypatch):
    row = seed(db, cfg)
    p = record(row["asin"], 2600, 2500, avg30=[10000], avg90=[10000],
               buyBoxPrice=5000, buyBoxShipping=0)
    calls = api(monkeypatch, {row["asin"]: p})
    bucket(db, 1000)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    after = db.get_deal(row["dedupe_key"])
    assert result["price_source"]["confirmed_new"] == 1
    assert after["buybox_check"]["final"] == after["final_price"] == 26
    assert buybox.would_unconfirm(after, NOW)
    p["lastUpdate"] -= 60
    p["stats"]["current"] = [2600, 2600]
    cfg.keepa_recheck_fresh_h = 0
    pipeline._recheck_prices(db, cfg, NOW + timedelta(hours=8), image_backfill=False)
    assert len(calls) == 2
    assert db.get_deal(row["dedupe_key"])["buybox_check"] == after["buybox_check"]


def test_full_pipeline_exposes_private_summary_and_keeps_rerun_idempotent(env):
    from tests.test_e2e_offline import _run
    first = _run(env)
    assert first["price_source"] == dict(buybox.empty_summary(), buybox_rule={
        "mode": "shadow", "evaluated": 0, "would_label": 0, "would_hide": 0})
    before = tree(env["site"])
    state = DB(env["db"])
    tables = ("deals", "sightings", "price_history", "alerts_sent")
    counts = [state.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables]
    state.close()
    second = _run(env)
    assert second["run_key"] == first["run_key"] and second["posts_new"] == 0
    assert second["telegram"]["sent"] == 0
    state = DB(env["db"])
    assert [state.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables] == counts
    state.close()
    assert json.loads(before["deals.json"])["deals"] == json.loads(tree(env["site"])["deals.json"])["deals"]
    assert all(b'"price_source"' not in content for content in before.values())


def test_probe_workflow_is_manual_read_only_and_never_saves_state():
    text = Path(".github/workflows/keepa-buybox-probe.yml").read_text()
    doc = yaml.safe_load(text)
    assert doc.get("on", doc.get(True)) == {"workflow_dispatch": None}
    assert doc["permissions"] == {"contents": "read"}
    assert "bash scripts/restore_state.sh" in text
    assert "python scripts/keepa_buybox_probe.py" in text
    assert not any(s in text for s in ("save_state", "commit", "push", "upload-artifact", "schedule:"))
