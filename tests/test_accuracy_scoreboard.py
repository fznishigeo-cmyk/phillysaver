"""117: private, independent price samples and one growth scoreboard; offline."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dealradar import accuracy, pipeline, savings, util, verify
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.growth import report
from dealradar.keepa_budget import KeepaBudget
from dealradar.publish import site
from tests.test_price_accuracy_engine import NOW, cfg as engine_cfg, db, product, seed


@pytest.fixture(params=[False, True], ids=["legacy", "compact"])
def cfg(engine_cfg, request):
    engine_cfg.compact_accuracy_samples = request.param
    return engine_cfg


def visible(db, rows, *, run="previous", at=NOW - timedelta(hours=2)):
    accuracy.publish(db, rows, at, run)
    db.accuracy_run_key = "current"


def samples(db):
    rows = [dict(r) for r in db.conn.execute("SELECT * FROM accuracy_samples ORDER BY deal_key")]
    if accuracy.compact_exists(db):
        for r in db.conn.execute("SELECT k.deal_key,r.run_key,s.checked_at,s.observed_at,s.bucket,k.source,s.outcome"
                                 " FROM accuracy_samples_compact s JOIN accuracy_sample_keys k ON k.id=s.key_id"
                                 " JOIN accuracy_sample_runs r ON r.id=s.run_id"):
            row = dict(r)
            for name in ("checked_at", "observed_at"):
                row[name] = util.iso(datetime.fromtimestamp(row[name], timezone.utc))
            rows.append(row)
    return sorted(rows, key=lambda r: r["deal_key"])


@pytest.mark.parametrize("matches,n,expected", [
    (0, 0, None), (0, 10, (0, .2775327999)), (10, 10, (.7224672001, 1)),
    (5, 10, (.2365930905, .7634069095)), (999, 1000, (.9943574414, .9998234536)),
])
def test_wilson_values(matches, n, expected):
    result = accuracy.wilson(matches, n)
    assert result == (pytest.approx(expected, abs=1e-9) if expected else None)


@pytest.mark.parametrize("source,price,expected", [
    ("keepa", 25.24, "match"), ("keepa", 25.25, "higher"), ("keepa", 24.75, "lower"),
    ("woot", 25.125, "match"), ("woot", 25.13, "higher"),
    ("bestbuy", 25.01, "higher"), ("ebay", 24.99, "lower"),
])
def test_existing_reprice_thresholds(source, price, expected):
    assert savings.accuracy_price_outcome(price, 25, source=source) == expected


def test_extra_knob_loads_from_config_and_is_off_when_omitted(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("accuracy:\n  extra_samples_per_day: 3\n")
    assert load_config(path).accuracy_extra_samples_per_day == 3
    path.write_text("{}")
    assert load_config(path).accuracy_extra_samples_per_day == 0


def test_unmeasured_and_checkout_only_never_become_one_hundred_percent(db, cfg):
    data = accuracy.report_data(db, cfg, NOW)
    assert len(data["daily"]) == 7
    assert accuracy.format_score(data["week"]) == "not measured (n=0)"
    value = accuracy.score([{"outcome": "checkout_price"}] * 4)
    assert value["n"] == 0 and value["checkout_price"] == 4
    assert "not measured" in accuracy.format_score(value)
    assert "100%" not in "\n".join(accuracy.report_lines(data))


@pytest.mark.parametrize("live,outcome", [(25, "match"), (25.1, "match"),
                                         (25.25, "higher"), (30, "higher"), (20, "lower")])
def test_snapshot_price_survives_same_run_correction(db, cfg, live, outcome):
    row = seed(db, cfg)
    visible(db, [row])
    db.update_deal(row["dedupe_key"], final_price=live)
    accuracy.record(db, cfg, row["dedupe_key"], NOW, source="keepa", price=live)
    assert samples(db)[0]["outcome"] == outcome
    expected = {"deal_key", "run_key", "checked_at", "observed_at", "bucket", "source", "outcome"}
    assert set(samples(db)[0]) == (expected if cfg.compact_accuracy_samples else expected | {"store"})


@pytest.mark.parametrize("changes", [{"codes": ["SAVE"]}, {"stack_steps": ["Clip the coupon"]},
                                   {"conditions": ["subscribe_save"]}])
def test_checkout_price_kind_is_separate_even_when_listing_matches(db, cfg, changes):
    row = seed(db, cfg, **changes)
    visible(db, [row])
    accuracy.record(db, cfg, row["dedupe_key"], NOW, source="keepa", price=25)
    assert samples(db)[0]["outcome"] == "checkout_price"
    assert accuracy.score(samples(db))["n"] == 0


def test_unpublished_locked_dead_and_same_creating_run_are_excluded(db, cfg):
    rows = [seed(db, cfg, asin=f"B{i:09d}") for i in range(4)]
    visible(db, [rows[0], dict(rows[1], locked=True), dict(rows[2], status="dead")], run="current")
    for row in rows:
        accuracy.record(db, cfg, row["dedupe_key"], NOW, source="keepa", price=25)
    assert samples(db) == []
    accuracy.publish(db, [rows[0]], NOW, "current")
    accuracy.record(db, cfg, rows[0]["dedupe_key"], NOW, source="keepa", price=25)
    assert samples(db) == []  # a same-key rerun after publication is still excluded


def test_actual_visible_positions_and_rest_are_not_candidate_ranks(db, cfg, monkeypatch):
    rows = [{"dedupe_key": f"key-{i}", "store": "Amazon", "final_price": 25,
             "status": "alive", "asin": f"B{i:09d}"} for i in range(125)]
    monkeypatch.setattr(db, "get_deal", lambda key: next(r for r in rows if r["dedupe_key"] == key))
    visible(db, rows)
    for i, expected in ((0, "top30"), (29, "top30"), (30, "top120"),
                        (119, "top120"), (120, "rest"), (124, "rest")):
        accuracy.record(db, cfg, rows[i]["dedupe_key"], NOW, source="keepa", price=25)
        assert next(r for r in samples(db) if r["deal_key"] == rows[i]["dedupe_key"])["bucket"] == expected


def test_retention_and_duplicate_observations_and_rerun(db, cfg):
    row = seed(db, cfg)
    visible(db, [row], at=NOW - timedelta(days=40))
    for age in (36, 35, 1):
        at = NOW - timedelta(days=age)
        accuracy.record(db, cfg, row["dedupe_key"], at, source="keepa", price=25, run_key=str(age))
    accuracy.record(db, cfg, row["dedupe_key"], NOW, source="keepa", price=30,
                    observed=NOW - timedelta(days=1), run_key="another")
    accuracy.record(db, cfg, row["dedupe_key"], NOW, source="keepa", price=30, run_key="1")
    assert len(samples(db)) == 3
    db.prune(NOW)
    assert len(samples(db)) == 2 and all(r["outcome"] == "match" for r in samples(db))


@pytest.mark.parametrize("change", ["old", "future", "missing", "malformed", "unknown_stock"])
def test_inconclusive_keepa_data_never_counts_as_match_or_unavailable(db, cfg, change):
    row = seed(db, cfg)
    visible(db, [row])
    item = product(2500)
    if change == "old":
        item["lastUpdate"] -= 120
    elif change == "future":
        item["lastUpdate"] += 1
    elif change == "missing":
        item.pop("lastUpdate")
    elif change == "malformed":
        item["stats"] = []
    else:
        item["stats"]["current"] = [-2, -2]
    accuracy.keepa(db, cfg, {row["asin"]: item}, NOW)
    assert samples(db) == []


def test_buybox_shipping_and_own_clock_and_explicit_unavailable(db, cfg):
    row = seed(db, cfg)
    visible(db, [row])
    item = product(2500, buyBoxPrice=2500, buyBoxShipping=500,
                   lastBuyBoxUpdate=product()["lastUpdate"] - 120)
    accuracy.keepa(db, cfg, {row["asin"]: item}, NOW)
    assert not samples(db)
    item["stats"]["lastBuyBoxUpdate"] += 120
    accuracy.keepa(db, cfg, {row["asin"]: item}, NOW)
    assert samples(db)[0]["outcome"] == "higher"
    db.accuracy_run_key = "next"
    item = product(-1)
    item["lastUpdate"] += 1
    accuracy.keepa(db, cfg, {row["asin"]: item}, NOW + timedelta(minutes=1))
    assert {r["outcome"] for r in samples(db)} == {"higher", "unavailable"}


@pytest.mark.parametrize("failed", [False, True])
def test_used_offer_scan_has_its_own_stock_evidence(db, cfg, failed):
    row = seed(db, cfg, conditions=["used"])
    visible(db, [row])
    item = product(-1, lastOffersUpdate=product()["lastUpdate"])
    item.update(offers=[], liveOffersOrder=[], offersSuccessful=not failed)
    accuracy.keepa(db, cfg, {row["asin"]: item}, NOW)
    assert [r["outcome"] for r in samples(db)] == ([] if failed else ["unavailable"])


def test_paid_buybox_read_audits_before_repricing_without_extra_requests(db, cfg, monkeypatch):
    from tests.test_buybox_top_picks import record, transport
    from tests.test_wall_confirmation_coverage import remember
    row = seed(db, cfg)
    visible(db, [row])
    remember(db, top=[row], wall=[row])
    cfg.keepa_buybox_top_n = 120
    calls, _, _ = transport(monkeypatch, db, {row["asin"]: record(row, 30)})
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert len(calls) == 1
    assert db.get_deal(row["dedupe_key"])["final_price"] == 30
    assert samples(db)[0]["outcome"] == "higher"


def test_paid_sweep_products_reuse_existing_request_for_visible_offers(db, cfg, monkeypatch):
    from tests.test_keepa import FakeKeepa, SRC, _adapter
    row = seed(db, cfg, conditions=["used"])
    visible(db, [row])
    monkeypatch.setenv("KEEPA_KEY", "offline")
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    cfg.keepa_sweep_enabled = True
    cfg.keepa_focus_categories = []
    current = [10000, 10000] + [-1] * 7 + [3000]
    item = product(10000, current=current)
    item["title"] = row["item"]
    fake = FakeKeepa(tokens=1200, finder=[row["asin"]], products={row["asin"]: item})
    _adapter(fake, cfg).fetch_posts(SRC, None, db, cfg, NOW, None)
    assert [r["outcome"] for r in samples(db)] == ["higher"]
    assert fake.calls.count(("product", 1)) == 1


@pytest.mark.parametrize("store", ["bestbuy", "ebay"])
def test_existing_store_requests_audit_before_correction(db, cfg, monkeypatch, store):
    from tests.test_store_prices import API, NOW as store_now, seed as store_seed
    cfg.feature_flags[store + "_live"] = True
    for name in ("BESTBUY_API_KEY", "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"):
        monkeypatch.setenv(name, "synthetic-" + name)
    row = store_seed(db, cfg, store)
    visible(db, [row], at=store_now - timedelta(hours=1))

    def change(body):
        if store == "bestbuy":
            body["products"][0]["salePrice"] = 30
        else:
            body["price"]["value"] = "30"

    api = API(db, cfg, change=change)
    api.run(db, cfg, run_key="current")
    assert samples(db)[0]["outcome"] == "higher"
    assert len(api.calls) == (1 if store == "bestbuy" else 2)
    api.run(db, cfg, run_key="current")
    assert len(samples(db)) == 1


@pytest.mark.parametrize("price,outcome", [(15.99, "match"), (9.99, "lower"), (28, "higher"),
                                          (None, "unavailable")])
def test_woot_feed_audits_every_read_before_correction(db, cfg, price, outcome):
    from tests.test_woot_reprice import NOW as woot_now, check, offer, stored
    row = stored(db, cfg)
    visible(db, [row], at=woot_now - timedelta(hours=1))
    item = offer(price if price is not None else 15.99, IsSoldOut=price is None)
    check(db, cfg, [item])
    check(db, cfg, [item])
    assert [r["outcome"] for r in samples(db)] == [outcome]


def test_no_extra_requests_by_default(db, cfg, monkeypatch):
    assert cfg.accuracy_extra_samples_per_day == AppConfig().accuracy_extra_samples_per_day == 0
    visible(db, [seed(db, cfg)])
    monkeypatch.setattr(verify, "recheck_amazon_prices", lambda *a, **k: pytest.fail("extra request"))
    accuracy.extra_samples(db, cfg, NOW)
    assert not db.conn.execute("SELECT * FROM accuracy_extra_runs").fetchall()


def test_extra_random_visible_reads_daily_cap_reserve_six_hours_and_rerun(db, cfg, monkeypatch):
    from tests.test_buybox_top_picks import transport
    rows = [seed(db, cfg, asin=f"B{i:09d}") for i in range(6)]
    visible(db, rows[:5])
    cfg.accuracy_extra_samples_per_day = 2
    calls, _, _ = transport(monkeypatch, db, {r["asin"]: product(2500, asin=r["asin"]) for r in rows})
    db.update_deal(rows[0]["dedupe_key"], payload=dict(rows[0], price_rechecked_at=util.iso(NOW)))
    accuracy.extra_samples(db, cfg, NOW)
    assert len(calls) == len(samples(db)) == 2
    checked = {r["deal_key"] for r in samples(db)}
    assert not checked & {rows[0]["dedupe_key"], rows[-1]["dedupe_key"]}
    accuracy.extra_samples(db, cfg, NOW)
    accuracy.extra_samples(db, cfg, NOW + timedelta(minutes=10), run_key="next")
    assert len(calls) == 2
    cfg.accuracy_extra_samples_per_day = 5
    KeepaBudget(cfg.keepa_recheck_surplus_reserve + 3, 0, NOW).save(db)
    accuracy.extra_samples(db, cfg, NOW, run_key="reserve")
    assert len(calls) == 2


@pytest.mark.parametrize("mode", ["lost", "429", "stopped", "deadline"])
def test_optional_sampling_reserves_failed_attempts_and_honors_stop_guards(db, cfg, monkeypatch, mode):
    visible(db, [seed(db, cfg, asin=f"B{i:09d}") for i in range(3)])
    cfg.accuracy_extra_samples_per_day = 2
    monkeypatch.setenv("KEEPA_KEY", "offline")
    KeepaBudget(2000, 0, NOW).save(db)
    calls = []

    def read(*args, **kwargs):
        calls.append(args[0])
        result = verify.PriceRechecks()
        result.rate_limited = mode == "429"
        return result

    monkeypatch.setattr(verify, "recheck_amazon_prices", read)
    accuracy.extra_samples(db, cfg, NOW, rate_limited=mode == "stopped",
                           deadline=0 if mode == "deadline" else None)
    expected = 0 if mode in ("stopped", "deadline") else 1 if mode == "429" else 2
    assert len(calls) == expected
    assert db.conn.execute("SELECT COUNT(*) FROM accuracy_extra_reads").fetchone()[0] == expected
    accuracy.extra_samples(db, cfg, NOW, rate_limited=mode == "stopped",
                           deadline=0 if mode == "deadline" else None)
    assert len(calls) == expected and not samples(db)


def test_optional_deal_becomes_eligible_again_after_six_hours(db, cfg, monkeypatch):
    visible(db, [seed(db, cfg)])
    cfg.accuracy_extra_samples_per_day = 2
    monkeypatch.setenv("KEEPA_KEY", "offline")
    KeepaBudget(2000, 0, NOW).save(db)
    calls = []
    monkeypatch.setattr(verify, "recheck_amazon_prices", lambda *a, **k: calls.append(a[0]) or {})
    accuracy.extra_samples(db, cfg, NOW)
    accuracy.extra_samples(db, cfg, NOW + timedelta(hours=1), run_key="too_soon")
    assert len(calls) == 1
    accuracy.extra_samples(db, cfg, NOW + timedelta(hours=6, minutes=1), run_key="due_again")
    assert len(calls) == 2
    assert db.conn.execute("SELECT COUNT(*) FROM accuracy_extra_reads").fetchone()[0] == 2


@pytest.mark.parametrize("failure", [None, "lost", "429"])
def test_optional_sampling_persists_shared_token_balance(db, cfg, monkeypatch, failure):
    from tests.test_buybox_top_picks import transport
    visible(db, [seed(db, cfg, asin=f"B{i:09d}") for i in range(2)])
    cfg.accuracy_extra_samples_per_day = 2
    floor = max(cfg.keepa_reserve_tokens, cfg.keepa_recheck_surplus_reserve)  # accuracy.extra_samples' reserve
    calls, _, _ = transport(monkeypatch, db, {}, tokens=floor + 4, failure=failure)
    accuracy.extra_samples(db, cfg, NOW)
    assert len(calls) == 1
    assert KeepaBudget.load(db).available(NOW) == floor
    accuracy.extra_samples(db, cfg, NOW, run_key="later")
    assert len(calls) == 1  # another run cannot spend the same four tokens again


def test_old_read_only_snapshot_without_audit_schema_is_not_measured(db, cfg):
    db.conn.execute("DROP TABLE accuracy_samples")
    assert accuracy.report_data(db, cfg, NOW)["week"]["n"] == 0


def test_private_collection_reads_local_state_without_requesting_or_logging_metrics(tmp_path, cfg, db, monkeypatch, caplog):
    row = seed(db, cfg)
    visible(db, [row], at=NOW - timedelta(days=2))
    accuracy.record(db, cfg, row["dedupe_key"], NOW - timedelta(days=1), source="keepa", price=30)
    path = tmp_path / "private.db"
    stored = DB(path)
    db.conn.backup(stored.conn)
    stored.close()
    cfg.custom_domain = ""
    cfg.growth_ga4_property_id = cfg.growth_search_console_site = ""
    monkeypatch.setattr(report.site_health, "check", lambda *a: pytest.fail("unexpected request"))
    monkeypatch.setenv("GOOGLE_SA_JSON", "")
    with httpx.Client(transport=httpx.MockTransport(
            lambda request: pytest.fail("accuracy collection must be offline"))) as client:
        data, status = report.collect(cfg, NOW, client, db_path=path)
    assert status["accuracy"] == "ok"
    assert data["accuracy"]["week"]["n"] == 1
    assert "Wilson" not in caplog.text and "accuracy_samples" not in caplog.text


def test_daily_buckets_misses_and_seven_completed_days(db, cfg):
    row = seed(db, cfg)
    visible(db, [row], at=NOW - timedelta(days=9))
    for age, price in ((0, 25), (1, 25), (2, 30), (3, 20), (7, 25), (8, 30)):
        accuracy.record(db, cfg, row["dedupe_key"], NOW - timedelta(days=age),
                        source="keepa", price=price, run_key=str(age))
    data = accuracy.report_data(db, cfg, NOW)
    assert data["week"]["n"] == 4 and data["week"]["matches"] == 2
    assert data["daily"][-1]["overall"]["rate"] == 1
    assert data["daily"][-1]["buckets"]["rest"]["n"] == 0
    text = "\n".join(accuracy.report_lines(data))
    assert "7-day line:" in text and "higher 1" not in text  # daily causes, not invented root causes
    assert accuracy.score([{"outcome": k} for k in ("higher", "lower", "unavailable")])["causes"] == [
        ("higher", 1), ("lower", 1), ("unavailable", 1)]


def test_scoreboard_uses_existing_sourced_totals(db, cfg):
    # Building the report is pure: use collected-shape totals, no new queries.
    data = {"ga4": {"yesterday": {"deal_click": 17, "sign_up": 3},
                    "window": {"deal_click": 80, "sign_up": 12}},
            "search_console": {"impressions": 900, "clicks": 20, "start": "2026-10-01", "end": "2026-10-07"},
            "saved": {"returning": {"sessions": 20, "classified_sessions": 100, "unclassified_sessions": 7}},
            "accuracy": accuracy.report_data(db, cfg, NOW)}
    text = "\n".join(report._scoreboard(cfg, data, NOW.date() - timedelta(days=1)))
    assert text.startswith("Daily checks") and "Search impressions: 900; clicks: 20 (Search Console" in text
    assert "Get-deal clicks: 17 / 80 (GA4" in text and "Sign-ups: 3 / 12 (GA4 actions" in text
    assert "Price accuracy: not measured (n=0)" in text
    assert "Returning sessions (7 days): 20 / 100 (20.0%)" in text


def test_private_snapshot_and_metrics_never_change_published_bytes(db, cfg, tmp_path, monkeypatch, caplog):
    row = seed(db, cfg, price_rechecked_at=util.iso(NOW))
    visible(db, [row])
    accuracy.record(db, cfg, row["dedupe_key"], NOW, source="keepa", price=30)
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    first, second = tmp_path / "one", tmp_path / "two"
    site.render_site(db, cfg, NOW, out_dir=first)
    monkeypatch.setattr(accuracy, "publish", lambda *a, **k: None)
    site.render_site(db, cfg, NOW, out_dir=second)
    tree = lambda folder: {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    assert tree(first) == tree(second)
    assert all(b"accuracy_samples" not in b and b"Wilson" not in b for b in tree(first).values())
    assert "Wilson" not in caplog.text and "accuracy_samples" not in caplog.text


def test_an_audit_bug_never_breaks_the_run(caplog):
    from dealradar import accuracy

    class Broken:
        def meta_get(self, key):
            raise RuntimeError("boom")

    assert accuracy.publish(Broken(), [{"dedupe_key": "a"}], None, "run") is None
    assert accuracy.record(Broken(), None, "a", None, source="keepa", price=1.0) is None
    assert "accuracy: publish skipped (RuntimeError)" in caplog.messages

