"""Price corrections, visibility and diagnostics using paid Keepa fixtures only."""

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from dealradar import dedupe, pipeline, rank, savings, util, verify
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.fetch.keepa import KeepaDealsAdapter, reprice_post
from dealradar.keepa_budget import KeepaBudget
from dealradar.models import Evidence, ExtractedDeal, RawPost, SourceConfig
from dealradar.parse.extractor import verify_evidence
from dealradar.parse.schema import LLMDeal, to_extracted
from dealradar.publish import site
from dealradar.publish.seo_audit import Page
from tests.http_helpers import mock_keepa_get

NOW = datetime(2026, 10, 13, 22, tzinfo=timezone.utc)
ASIN = "B000000001"


@pytest.fixture
def cfg():
    result = load_config()
    result.feature_flags["keepa"] = True
    result.critic_shadow_model = ""
    result.keepa_sweep_enabled = False
    return result


@pytest.fixture
def db():
    result = DB(":memory:")
    yield result
    result.close()


def seed(db, cfg, *, asin=ASIN, si=False, seen=NOW - timedelta(hours=8), **changes):
    text = "Widget Pro. On sale for $25.00. Regular price $100.00."
    post = RawPost("slickdeals_frontpage", asin, "https://slickdeals.net/f/123",
                   "Widget Pro", text, seen, seen)
    extracted = ExtractedDeal("Widget Pro " + asin, "Amazon", post.url, post.source_name,
                              product_url="" if si else f"https://www.amazon.com/dp/{asin}",
                              online_only="true", final_price=25, regular_price=100,
                              category="home", evidence=Evidence(
                                  final_price_quote="On sale for $25.00.",
                                  regular_price_quote="Regular price $100.00."))
    assert verify_evidence(extracted, text)[0]
    result = dedupe.upsert_extracted(db, extracted, savings.compute_savings(extracted), post, cfg, seen)
    key = result["dedupe_key"]
    deal = db.get_deal(key)
    deal.update(asin=asin, image_url="https://m.media-amazon.com/images/I/widget.jpg",
                critic={"plausible": True, "confidence": .9, "reason": "Realistic basis"}, **changes)
    db.update_deal(key, payload=deal)
    return db.get_deal(key)


def product(cents=2600, *, asin=ASIN, **stats):
    return {"asin": asin, "lastUpdate": int(NOW.timestamp() / 60) - verify.KEEPA_EPOCH_MINUTES,
            "stats": {"current": [cents, -1], "avg30": [10000], "avg90": [12000], **stats}}


def api(monkeypatch, products, *, tokens=500, cost=1):
    monkeypatch.setenv("KEEPA_KEY", "offline-key")
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    calls = []
    remaining = tokens

    def get(url, params=None, timeout=None):
        nonlocal remaining
        assert url == "https://api.keepa.com/product"  # no retailer or other API
        calls.append(params)
        wanted = params["asin"].split(",")
        remaining -= len(wanted) * cost
        assert remaining >= 10  # the existing deal-lane reserve remains
        return httpx.Response(200, json={"products": [products[a] for a in wanted if a in products],
                                         "tokensLeft": remaining, "refillRate": 20},
                              request=httpx.Request("GET", url))

    mock_keepa_get(monkeypatch, get)
    return calls


def bucket(db, tokens):
    KeepaBudget(tokens, 20, NOW).save(db)


@pytest.mark.parametrize("si", [False, True])
@pytest.mark.parametrize("live", [20, 26, 25.25])
def test_up_down_and_exact_threshold_reprice_the_original_source_row(db, cfg, monkeypatch, si, live):
    before = seed(db, cfg, si=si)
    record = product(round(live * 100))
    calls = api(monkeypatch, {ASIN: record})
    checked = []
    normal_check = verify_evidence

    def evidence(extracted, text):
        checked.append((extracted, text))
        return normal_check(extracted, text)

    monkeypatch.setattr("dealradar.parse.extractor.verify_evidence", evidence)
    summary = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    after = db.get_deal(before["dedupe_key"])
    assert after["id"] == before["id"] and len(db.all_deals()) == 1
    assert after["dedupe_key"].startswith("si|" if si else "url|https://www.amazon.com/dp/")
    assert after["final_price"] == live and after["regular_price"] == 100
    assert after["sightings"] == before["sightings"] and after["critic"] == before["critic"]
    assert after["status"] == "alive" and not after["gone_suspect_at"]
    assert "price_moved" not in after["flags"]
    extracted, text = checked[0]
    assert extracted.evidence.final_price_quote == f"On sale for ${live:.2f} at Amazon."
    assert extracted.evidence.regular_price_quote in text
    computed = savings.compute_savings(extracted)
    assert after["pct_at_checkout"] == computed.pct_at_checkout
    assert after["abs_savings"] == computed.abs_savings
    assert summary["drift"]["repriced"] == 1 and len(calls) == 1
    monkeypatch.setattr("dealradar.parse.claude_client.critique_deals",
                        lambda *a, **kw: pytest.fail("correction must not add an LLM call"))
    assert pipeline._run_critic(db, cfg, object(), NOW)["calls"] == 0
    # The identical paid record has no second mutation or history observation.
    post = reprice_post(record, before, cfg, NOW)
    counts = db.conn.execute("SELECT count(*) FROM price_history").fetchone()[0]
    assert not dedupe.reprice_existing(db, before, post, cfg, NOW)
    assert db.get_deal(before["dedupe_key"]) == after
    assert db.conn.execute("SELECT count(*) FROM price_history").fetchone()[0] == counts
    assert pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)["checked"] == 0
    assert len(calls) == 1


def test_correction_below_the_floor_is_hidden_alive_and_not_gone(db, cfg, monkeypatch, tmp_path):
    deal = seed(db, cfg)
    api(monkeypatch, {ASIN: product(9000)})
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    row = db.get_deal(deal["dedupe_key"])
    assert row["final_price"] == 90 and row["pct_at_checkout"] == 10
    assert row["status"] == "alive" and "price_moved" not in row["flags"]
    assert rank.site_deals(db, cfg, NOW) == []
    site.render_site(db, cfg, NOW, out_dir=tmp_path)
    assert json.loads((tmp_path / "deals.json").read_text())["deals"] == []


@pytest.mark.parametrize("changes", [{"codes": ["SAVE20"]}, {"conditions": ["subscribe_save"]},
                                    {"stack_steps": ["Clip coupon"]}, {"conditions": ["used"]},
                                    {"conditions": ["open_box"]}])
def test_promos_and_used_are_never_repriced(db, cfg, changes):
    deal = seed(db, cfg, **changes)
    assert reprice_post(product(5000), deal, cfg, NOW) is None


@pytest.mark.parametrize("stats", [{"avg30": []}, {"avg90": [-1]}, {"avg30": None},
                                   {"avg90": None}, {"current": [-1, 5000]}])
def test_ineligible_records_retain_the_existing_kill_rule(db, cfg, monkeypatch, stats):
    deal = seed(db, cfg)
    record = product(5000, **stats)
    assert reprice_post(record, deal, cfg, NOW) is None
    api(monkeypatch, {ASIN: record})
    summary = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert summary["drift"]["repriced"] == 0
    assert db.get_deal(deal["dedupe_key"])["gone_suspect_at"] == util.iso(NOW)


def test_no_offer_needs_two_agreeing_readings(db, cfg, monkeypatch):
    deal = seed(db, cfg)
    api(monkeypatch, {ASIN: product(-1)})
    assert pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)["dead"] == 0
    row = db.get_deal(deal["dedupe_key"])
    assert row["status"] == "alive" and row["gone_suspect_at"]
    assert verify.price_confirmed_at(row) is None
    assert pipeline._recheck_prices(db, cfg, NOW + timedelta(hours=1), image_backfill=False)["dead"] == 1
    assert db.get_deal(deal["dedupe_key"])["death"] == "confirmed"


def test_stale_or_unverified_correction_cannot_change_a_price(db, cfg, monkeypatch):
    deal = seed(db, cfg)
    record = product(2600)
    record["lastUpdate"] -= 24 * 60
    assert reprice_post(record, deal, cfg, NOW) is None
    post = reprice_post(product(), deal, cfg, NOW)
    post.text = post.text.replace("On sale for $26.00", "On sale for $99.00")
    assert not dedupe.reprice_existing(db, deal, post, cfg, NOW)
    assert db.get_deal(deal["dedupe_key"])["final_price"] == 25


def test_reprice_uses_amazons_own_price_and_the_lower_average(db, cfg):
    deal = seed(db, cfg)
    # A cheaper marketplace offer ($24, shipping not included) is not what
    # the shopper sees on Amazon's page: the correction uses Amazon's $26.
    post = reprice_post(product(2600, current=[2600, 2400], avg30=[12000], avg90=[9000]), deal, cfg, NOW)
    assert post.structured["final_price"] == 26 and post.structured["regular_price"] == 90
    assert "Regular price (90-day average on Amazon): $90.00." in post.text
    assert dedupe.reprice_existing(db, deal, post, cfg, NOW)
    assert db.get_deal(deal["dedupe_key"])["pct_at_checkout"] == 71.1


def test_reprice_uses_a_list_price_the_item_sold_near(db, cfg):
    deal = seed(db, cfg)
    sold_near = product(2600, current=[2600, -1, -1, -1, 9800], avg30=[12000], avg90=[9000])
    post = reprice_post(sold_near, deal, cfg, NOW)
    assert "Regular price (List Price on Amazon): $98.00." in post.text
    assert post.structured["regular_price_basis"] == "List Price on Amazon"
    never_near = product(2600, current=[2600, -1, -1, -1, 20000], avg30=[12000], avg90=[9000])
    assert "List Price" not in reprice_post(never_near, deal, cfg, NOW).text


def test_scheduler_priority_used_cadence_and_wall_cadence(db, cfg, monkeypatch):
    records, deals = {}, {}
    for i, name in enumerate(("rest", "wall", "top", "suspect", "used_rest", "used_top", "used_wall")):
        asin = f"B0{i:08d}"
        used = name.startswith("used")
        changes = {"conditions": ["used"] if used else [],
                   "price_rechecked_at": util.iso(NOW - timedelta(hours=25 if used else 13))}
        if name == "suspect":
            changes["gone_suspect_at"] = util.iso(NOW - timedelta(hours=2))
        deals[name] = seed(db, cfg, asin=asin, **changes)
        records[asin] = product(2500, asin=asin)
        if used:
            records[asin]["stats"]["current"] = [10000, 10000, 2500]
    db.meta_set(site.PICKS_META, json.dumps([deals[n]["dedupe_key"] for n in ("top", "used_top")]))
    db.meta_set(site.WALL_META, json.dumps([deals[n]["dedupe_key"] for n in ("wall", "used_wall")]))
    calls = api(monkeypatch, records)
    summary = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    seen = [a for call in calls for a in call["asin"].split(",")]
    assert seen == [deals[n]["asin"] for n in ("suspect", "top", "wall", "rest", "used_top", "used_wall", "used_rest")]
    assert summary["planned"] == summary["done"]
    for name, hours in (("top", 1), ("wall", 2), ("rest", 4), ("used_top", 24), ("used_wall", 24)):
        row = db.get_deal(deals[name]["dedupe_key"])
        row["price_rechecked_at"] = util.iso(NOW - timedelta(hours=hours) + timedelta(seconds=1))
        db.update_deal(row["dedupe_key"], payload=row)
    calls.clear()
    pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert calls == []
    pipeline._recheck_prices(db, cfg, NOW + timedelta(seconds=1), image_backfill=False)
    assert {a for call in calls for a in call["asin"].split(",")} == {
        deals[n]["asin"] for n in ("top", "wall", "rest", "used_top", "used_wall")}


@pytest.mark.parametrize("live,cost", [(False, 1), (True, 2)])
def test_nearly_empty_bucket_spends_only_on_top_candidates(db, cfg, monkeypatch, live, cost):
    deals = [seed(db, cfg, asin=f"B0{i:08d}") for i in range(10)]
    top = deals[-5:]
    db.meta_set(site.PICKS_META, json.dumps([d["dedupe_key"] for d in top]))
    bucket(db, 15)
    cfg.keepa_recheck_top_live = live
    calls = api(monkeypatch, {d["asin"]: product(2500, asin=d["asin"]) for d in deals}, tokens=15, cost=cost)
    summary = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert len(calls) == 1 and calls[0]["asin"].split(",") == [d["asin"] for d in top[:5 // cost]]
    assert (calls[0].get("update") == "0") == live
    assert summary["done"]["top_new"] == 5 // cost and summary["done"]["rest_new"] == 0
    assert summary["tokens_short"] == 10 - 5 // cost
    assert KeepaBudget.load(db).available(NOW) >= 10


def test_drift_and_confirmed_age_metrics(db, cfg, monkeypatch):
    deals = [seed(db, cfg, asin=f"B0{i:08d}") for i in range(4)]
    db.meta_set(site.PICKS_META, json.dumps([d["dedupe_key"] for d in deals]))
    db.meta_set(site.WALL_META, json.dumps([d["dedupe_key"] for d in deals[:2]]))
    api(monkeypatch, {d["asin"]: product(c, asin=d["asin"]) for d, c in zip(deals, [2520, 2600, 2700, 2400])})
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert result["drift"] == {"within_1pct": 1, "higher_1_5pct": 1, "higher_over_5pct": 1, "lower": 1, "promo_hidden": 0, "repriced": 3}
    assert result["confirmed_age"]["top_candidates"] == {
        "amazon": 4, "unconfirmed": 0, "within_1h": 1, "within_3h": 1, "within_12h": 1, "oldest_h": 0}
    assert result["confirmed_age"]["wall"]["amazon"] == 2
    rows = [dict(deals[0], price_rechecked_at=util.iso(NOW - timedelta(hours=h))) for h in (.5, 2, 8, 30)]
    rows += [deals[0], dict(deals[0], store="Walmart", canonical_url="")]
    assert verify.confirmed_age(rows, NOW) == {"amazon": 5, "unconfirmed": 1, "within_1h": .2,
                                             "within_3h": .4, "within_12h": .6, "oldest_h": 30}
    assert verify.confirmed_age([], NOW)["within_1h"] is None


@pytest.mark.parametrize("used,max_h", [(False, 48), (True, 72)])
def test_hide_unconfirmed_and_return_without_a_death(db, cfg, monkeypatch, tmp_path, used, max_h):
    deal = seed(db, cfg, seen=NOW - timedelta(days=5), conditions=["used"] if used else [],
                price_rechecked_at=util.iso(NOW - timedelta(hours=max_h, seconds=1)))
    assert rank.site_deals(db, cfg, NOW) == []
    site.render_site(db, cfg, NOW, out_dir=tmp_path)
    if not used:
        assert json.loads(db.meta_get(site.PICKS_META)) == [deal["dedupe_key"]]
    record = product(2500)
    if used:
        record["stats"]["current"] = [10000, 10000, 2500]
    api(monkeypatch, {ASIN: record})
    bucket(db, 500)
    stats = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert stats["hidden_unconfirmed"]["total"] == 0
    assert len(rank.site_deals(db, cfg, NOW)) == 1
    assert db.get_deal(deal["dedupe_key"])["status"] == "alive"
    site.render_site(db, cfg, NOW, out_dir=tmp_path)
    row = json.loads((tmp_path / "deals.json").read_text())["deals"][0]
    assert ("pick" in row) == (not used)


def test_pick_candidates_precede_confirmation_and_caps_and_wall_matches_home(db, cfg, tmp_path):
    cfg.keepa_recheck_top_n = 2
    cfg.keepa_recheck_wall_n = 2
    recent = seed(db, cfg, asin="B000000010", price_rechecked_at=util.iso(NOW))
    unknown = seed(db, cfg, asin="B000000020")
    old = seed(db, cfg, asin="B000000030", price_rechecked_at=util.iso(NOW - timedelta(hours=4)))
    assert rank.pick_score(unknown, cfg, NOW) is None and rank.pick_score(old, cfg, NOW) is None
    assert rank.pick_score(recent, cfg, NOW) is not None
    assert rank.pick_score(dict(unknown, store="Walmart", canonical_url=""), cfg, NOW) is not None
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path)
    scores = sorted([(d["dedupe_key"], rank.pick_score(d, cfg, NOW, require_confirmation=False))
                     for d in (recent, unknown, old)], key=lambda p: p[1], reverse=True)
    assert json.loads(db.meta_get(site.PICKS_META)) == [k for k, score in scores[:2]]
    doc = json.loads((tmp_path / "deals.json").read_text())
    assert [d["dedupe_key"] for d in doc["deals"] if "pick" in d] == [recent["dedupe_key"]]
    ordered = site.seo.place_gone(rank.pick_order(doc["deals"], cfg.front_page))
    assert json.loads(db.meta_get(site.WALL_META)) == [d["dedupe_key"] for d in ordered[:2]]
    assert result["confirmed_age"]["top_candidates"]["amazon"] == 2
    assert result["confirmed_age"]["top_candidates"]["within_1h"] == .5


def test_keepa_lane_is_a_confirmation_but_an_old_or_different_price_is_not(db, cfg):
    post = KeepaDealsAdapter._map_row({"asin": ASIN, "title": "Widget", "current": [2500],
                                      "avg": [[10000]] * 4,
                                      "lastUpdate": int(NOW.timestamp() / 60) - verify.KEEPA_EPOCH_MINUTES},
                                     SourceConfig("keepa", "api", ""), NOW)
    ext = to_extracted(LLMDeal.model_validate(post.structured), post, "UTC", NOW)
    result = dedupe.upsert_extracted(db, ext, savings.compute_savings(ext), post, cfg, NOW)
    row = db.get_deal(result["dedupe_key"])
    assert verify.price_confirmed_at(row) == NOW
    assert verify.price_confirmation_recent(row, NOW + timedelta(hours=3), 3)
    assert not verify.price_confirmation_recent(row, NOW + timedelta(hours=3, seconds=1), 3)
    assert verify.price_confirmed_at(dict(row, price_since=util.iso(NOW + timedelta(minutes=1)))) is None
    legacy = dict(row)
    legacy.pop("keepa_price_seen_at")
    assert verify.price_confirmed_at(legacy) == NOW
    assert verify.price_confirmed_at(dict(legacy, final_price=20)) is None
    assert not verify.price_unconfirmed(dict(row, store="Walmart", canonical_url=""), cfg, NOW + timedelta(days=100))


def test_card_and_deal_page_timestamps_and_node_logic(db, cfg, tmp_path):
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    checked = NOW - timedelta(minutes=25)
    seed(db, cfg, price_rechecked_at=util.iso(checked))
    site.render_site(db, cfg, NOW, out_dir=tmp_path)
    doc = json.loads((tmp_path / "deals.json").read_text())
    row = doc["deals"][0]
    assert row["checked_at"] == util.iso(checked)
    home = (tmp_path / "index.html").read_text()
    assert '<span class="pcl">price checked </span>25m ago</span>' in home
    page = (tmp_path / row["deal_path"].lstrip("/") / "index.html").read_text()
    assert 'Price checked <time datetime="2026-10-13T21:35:00+00:00">Oct 13, 2026 05:35 PM</time> ET' in page
    assert ".metarow{flex-wrap:nowrap;overflow:hidden;" in home  # phone height unchanged
    subprocess.run(["node", str(Path(__file__).with_name("price_accuracy_logic.cjs")), str(tmp_path)],
                   check=True, capture_output=True, text=True, timeout=30)


def test_config_knobs_defaults_overrides_and_operator_comments(cfg, tmp_path):
    defaults = {"reprice_min_pct": 1, "recheck_top_n": 200, "recheck_wall_n": 300,
                "recheck_wall_h": 2, "pick_max_unconfirmed_h": 3,
                "show_max_unconfirmed_h": 48, "show_max_unconfirmed_used_h": 72,
                "recheck_top_live": False}
    assert {k: getattr(cfg, "keepa_" + k) for k in defaults} == defaults
    path = tmp_path / "config.yaml"
    overrides = {k: v + 1 if type(v) is not bool else True for k, v in defaults.items()}
    path.write_text("keepa: " + json.dumps(overrides))
    loaded = load_config(path)
    assert {k: getattr(loaded, "keepa_" + k) for k in defaults} == overrides


def test_used_top_waits_for_its_whole_offers_budget(db, cfg, monkeypatch):
    cfg.keepa_used_scan_budget_share = 1  # isolate the hard deal-lane reserve
    rest = seed(db, cfg, asin="B000000010", conditions=["used"])
    top = seed(db, cfg, asin="B000000020", conditions=["used"])
    db.meta_set(site.PICKS_META, json.dumps([top["dedupe_key"]]))
    records = {d["asin"]: product(2500, asin=d["asin"], current=[10000, 10000, 2500])
               for d in (rest, top)}
    bucket(db, 22)
    calls = api(monkeypatch, records, tokens=23, cost=13)
    assert pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)["tokens_short"] == 2
    assert calls == []
    bucket(db, 23)
    result = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert calls[0]["asin"] == top["asin"] and calls[0]["offers"] == 20
    assert result["done"]["top_used"] == 1 and result["done"]["rest_used"] == 0


def test_hidden_count_and_critic_flags_survive_corrections(db, cfg):
    old = seed(db, cfg, seen=NOW - timedelta(days=5),
               price_rechecked_at=util.iso(NOW - timedelta(hours=49)))
    cfg.feature_flags["keepa"] = False  # measurement itself needs no API
    summary = pipeline._recheck_prices(db, cfg, NOW, image_backfill=False)
    assert summary["hidden_unconfirmed"] == {"new": 1, "used": 0, "total": 1}
    old["flags"] = ["suspect_msrp", "newsletter", "unknown_shipping"]
    old["critic"] = {"plausible": False, "confidence": .9}
    db.update_deal(old["dedupe_key"], payload=old)
    assert dedupe.reprice_existing(db, old, reprice_post(product(), old, cfg, NOW), cfg, NOW)
    row = db.get_deal(old["dedupe_key"])
    assert row["critic"]["plausible"] is False and "suspect_msrp" in row["flags"]
    assert "newsletter" in row["flags"] and "unknown_shipping" not in row["flags"]
    assert rank.site_deals(db, cfg, NOW) == []


def test_new_source_price_discards_the_old_confirmation_and_average_label(db, cfg):
    deal = seed(db, cfg)
    assert dedupe.reprice_existing(db, deal, reprice_post(product(), deal, cfg, NOW), cfg, NOW)
    later = NOW + timedelta(minutes=10)
    post = RawPost("slickdeals_frontpage", "changed", "https://slickdeals.net/f/123",
                   deal["item"], "Now $22.00. Regular price $80.00.", later, later)
    ext = ExtractedDeal(deal["item"], "Amazon", post.url, post.source_name,
                        product_url=deal["canonical_url"], online_only="true",
                        final_price=22, regular_price=80, evidence=Evidence(
                            final_price_quote="Now $22.00.", regular_price_quote="Regular price $80.00."))
    assert verify_evidence(ext, post.text)[0]
    dedupe.upsert_extracted(db, ext, savings.compute_savings(ext), post, cfg, later)
    row = db.get_deal(deal["dedupe_key"])
    assert row["regular_price_basis"] == "" and verify.price_confirmed_at(row) is None
    assert row["critic"] is None and len(db.all_deals()) == 1


def test_relative_check_age_does_not_churn_the_seo_fingerprint():
    markup = ('<html><head><title>Deals</title></head><body><main><article class="card">'
              '<span class="chip price-checked" data-checked-at="2026-10-13T21:35:00Z">'
              'price checked 25m ago</span></article></main></body></html>')
    original = Page(markup, "/").fingerprint()
    assert Page(markup.replace("25m", "35m"), "/").fingerprint() == original
    # A newer confirmation is not a substantive change: no lastmod churn.
    assert Page(markup.replace("21:35", "21:45"), "/").fingerprint() == original


@pytest.mark.parametrize("changes", [{"conditions": ["used"]}, {"codes": ["SAVE20"]}])
def test_application_rechecks_current_row_eligibility(db, cfg, changes):
    deal = seed(db, cfg)
    post = reprice_post(product(), deal, cfg, NOW)
    db.update_deal(deal["dedupe_key"], payload=dict(deal, **changes))
    assert not dedupe.reprice_existing(db, deal, post, cfg, NOW)
    assert db.get_deal(deal["dedupe_key"])["final_price"] == 25


def test_reprice_keeps_the_free_photo_from_the_same_record(db, cfg, monkeypatch):
    deal = seed(db, cfg)
    db.update_deal(deal["dedupe_key"], payload=dict(deal, image_url=""))
    record = dict(product(), imagesCSV="tracked.jpg")
    calls = api(monkeypatch, {ASIN: record})
    result = pipeline._recheck_prices(db, cfg, NOW)
    row = db.get_deal(deal["dedupe_key"])
    assert result["drift"]["repriced"] == 1 and len(calls) == 1
    assert row["image_url"] == "https://m.media-amazon.com/images/I/tracked.jpg"
