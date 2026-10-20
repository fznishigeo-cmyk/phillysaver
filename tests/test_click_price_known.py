"""Task 275: only a fresh qualified Buy Box confirms a plain Amazon price."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import subprocess

import httpx
import pytest

from dealradar import buybox, click_price, dedupe, rank, savings, util, verify
from tests.history_pins import pre_drop_fresh
from tests.history_pins import pre_task_319
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.keepa_budget import KeepaBudget
from dealradar.keepa_rechecks import History
from dealradar.models import Evidence, ExtractedDeal, RawPost
from dealradar.parse.extractor import verify_evidence
from dealradar.publish import price_labels, seo, site, social
from dealradar.volatility import Plan
from tests.http_helpers import mock_keepa_get
from tests.test_buybox_top_picks import transport, run
from tests.test_e2e_offline import env
from tests.test_price_accuracy_engine import NOW, db, seed, product
from tests.test_product_offer_pages import tree
from tests.test_value_per_token_rechecks import minutes
from tests.test_wall_confirmation_coverage import remember


@pytest.fixture
def cfg():
    cfg = pre_task_319(load_config())
    cfg.keepa_click_price_rule = "on"
    cfg.keepa_recheck_surplus = False
    cfg.feature_flags["keepa"] = True
    cfg.keepa_hidden_new_tokens_per_run = cfg.keepa_hidden_used_scans_per_run = 0
    cfg.critic_tiering = dict(cfg.critic_tiering, enabled=False)
    cfg.seo_hub_min_items = 1
    return cfg


def adidas(db, cfg, **changes):
    asin, seen = "B0CNSDZ47Y", NOW - timedelta(hours=8)
    item = "adidas Men's D4T Woven Kansas University Shorts"
    text = item + ". On sale for $4.55. Regular price $32.93."
    post = RawPost("slickdeals_frontpage", asin, "https://slickdeals.net/f/123", item, text, seen, seen)
    extracted = ExtractedDeal(item, changes.get("store", "Amazon"), post.url, post.source_name,
        product_url=changes.get("canonical_url", f"https://www.amazon.com/dp/{asin}"),
        online_only="true", final_price=4.55, regular_price=32.93, category="fashion",
        evidence=Evidence(final_price_quote="On sale for $4.55.", regular_price_quote="Regular price $32.93."))
    assert verify_evidence(extracted, text)[0]
    inserted = dedupe.upsert_extracted(db, extracted, savings.compute_savings(extracted), post, cfg, seen)
    row = db.get_deal(inserted["dedupe_key"])
    row.update(asin=asin, image_url="https://m.media-amazon.com/images/I/shorts.jpg",
               critic={"plausible": True, "confidence": .9, "reason": "Backed basis"},
               price_rechecked_at=util.iso(NOW - timedelta(minutes=5)), **changes)
    db.update_deal(row["dedupe_key"], payload=row)
    return db.get_deal(row["dedupe_key"])


def record(row, total=None, **changes):
    p = product(455, asin=row["asin"], lastBuyBoxUpdate=minutes(NOW - timedelta(minutes=5)))
    p["stats"]["current"][1] = 455
    if total is not None:
        p["stats"].update(buyBoxPrice=round(total * 100), buyBoxShipping=0)
    p.update(changes)
    return p


def save(db, row, p):
    click_price.remember(row, p, NOW, NOW)
    db.update_deal(row["dedupe_key"], payload=row)


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_validated_config_ships_on_with_safe_missing_key(tmp_path, mode):
    assert AppConfig().keepa_click_price_rule == "off"
    assert load_config().keepa_click_price_rule == "on"
    path = tmp_path / "config.yaml"
    path.write_text(f'keepa:\n  click_price_rule: "{mode}"\n')
    assert load_config(path).keepa_click_price_rule == mode
    path.write_text("keepa:\n  click_price_rule: invalid\n")
    with pytest.raises(ValueError, match="keepa.click_price_rule"):
        load_config(path)


@pytest.mark.parametrize("total,kind", [(None, "unknown"), (4.55, "known_ok"), (32, "known_above")])
@pytest.mark.parametrize("child", [False, True])
def test_adidas_missing_matching_and_above_buybox(db, cfg, tmp_path, monkeypatch, total, kind, child):
    row = adidas(db, cfg)
    assert row["final_price"] == 4.55 and row["regular_price"] == 32.93
    save(db, row, record(row, total, **({"parentAsin": "B0PARENT00"} if child else {})))
    assert click_price.status(row, cfg, NOW) == kind
    assert verify.price_unconfirmed(row, cfg, NOW) == (kind != "known_ok")
    assert (rank.pick_score(row, cfg, NOW) is not None) == (kind == "known_ok")
    assert price_labels.push_excluded(row, cfg, NOW) == (kind != "known_ok")
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    result = site.render_site(db, cfg, NOW, out_dir=tmp_path)
    public = result["public_deals"]
    if kind == "known_above":
        assert not public  # even the display exception cannot admit a known gap
    else:
        assert len(public) == 1
        card = public[0]
        assert bool(card.get("price_unconfirmed")) == (kind == "unknown")
        assert bool(card.get("pick")) == (kind == "known_ok")
        assert bool(card.get("select_sizes")) == (child and kind == "known_ok")
        assert "select sizes" in seo.card_html(card) if child and kind == "known_ok" else "select sizes" not in seo.card_html(card)
        if kind == "unknown":
            assert "Price not confirmed" in seo.card_html(card)
            assert "D4T Woven" not in (tmp_path / "index.html").read_text()
            hubs = list((tmp_path / "deals").glob("*/index.html"))
            assert any(row["item"].replace("'", "&#x27;") in p.read_text() for p in hubs)
    assert "click_price_check" not in json.dumps(result["full_payload"])
    assert db.get_deal(row["dedupe_key"])["status"] == "alive"


@pytest.mark.parametrize("bad", ["stale", "future", "no_clock", "before_price", "before_confirmation", "unqualified", "shipping"])
def test_buybox_uses_its_own_clock_and_qualification(db, cfg, bad):
    row = adidas(db, cfg)
    p = record(row, 4.60)
    if bad == "stale":
        p["stats"]["lastBuyBoxUpdate"] = minutes(NOW - timedelta(minutes=61))
    elif bad == "future":
        p["stats"]["lastBuyBoxUpdate"] = minutes(NOW + timedelta(minutes=1))
    elif bad == "no_clock":
        del p["stats"]["lastBuyBoxUpdate"]
    elif bad == "before_price":
        row["price_since"] = util.iso(NOW - timedelta(minutes=1))
    elif bad == "before_confirmation":
        row["price_rechecked_at"] = util.iso(NOW - timedelta(minutes=1))
    elif bad == "unqualified":
        p["stats"]["buyBoxIsUnqualified"] = True
    else:
        p["stats"]["buyBoxShipping"] = -1
    save(db, row, p)
    assert click_price.status(row, cfg, NOW) == "unknown"
    assert rank.pick_score(row, cfg, NOW) is None


@pytest.mark.parametrize("promo", [{"codes": ["SAVE"]}, {"conditions": ["subscribe_save"]},
                                   {"stack_steps": ["Clip the coupon"]}])
def test_promo_path_is_unchanged(db, cfg, promo):
    row = adidas(db, cfg, **promo)
    save(db, row, record(row, 32))
    assert click_price.status(row, cfg, NOW) == "promo"
    assert not verify.price_unconfirmed(row, cfg, NOW)
    assert price_labels.push_excluded(row, cfg, NOW) == price_labels.push_excluded(
        row, replace(cfg, keepa_click_price_rule="off"), NOW)


def test_missing_older_or_unqualified_data_never_clears_gap(db, cfg):
    row = adidas(db, cfg)
    save(db, row, record(row, 32))
    before = deepcopy(row["click_price_check"])
    older, missing_clock, unqualified = (record(row, 4.6) for _ in range(3))
    older["stats"]["lastBuyBoxUpdate"] = minutes(NOW - timedelta(minutes=10))
    del missing_clock["stats"]["lastBuyBoxUpdate"]
    unqualified["stats"]["buyBoxIsUnqualified"] = True
    for p in (record(row), older, missing_clock, unqualified):
        save(db, row, p)
        assert row["click_price_check"] == before
    later = NOW + timedelta(hours=2)
    assert click_price.status(row, cfg, later) == "unknown"
    assert click_price.has_gap(row, later) and not price_labels.qualifies(row, cfg, later)
    p = record(row, 4.55)
    p["stats"]["lastBuyBoxUpdate"] = minutes(NOW)
    save(db, row, p)
    assert click_price.status(row, cfg, NOW) == "known_ok"


@pytest.mark.parametrize("age", [5, 65])
@pytest.mark.parametrize("reading", ["missing", "no_clock", "unqualified", "older", "newer"])
def test_pipeline_preserves_legacy_buybox_gap_until_qualified_later_read(
        db, cfg, monkeypatch, tmp_path, age, reading):
    row = adidas(db, cfg)
    previous = record(row, 32)
    previous["stats"]["lastBuyBoxUpdate"] = minutes(NOW - timedelta(minutes=age))
    buybox.remember(row, previous, NOW, now=NOW)
    db.update_deal(row["dedupe_key"], payload=row)
    assert not row.get("click_price_check") and click_price.has_gap(row, NOW)
    remember(db, top=[row], wall=[row])
    incoming = record(row, None if reading == "missing" else 4.60)
    if reading == "no_clock":
        del incoming["stats"]["lastBuyBoxUpdate"]
    elif reading == "unqualified":
        incoming["stats"]["buyBoxIsUnqualified"] = True
    elif reading == "older":
        incoming["stats"]["lastBuyBoxUpdate"] = minutes(NOW - timedelta(minutes=age + 1))
    elif reading == "newer":
        incoming["stats"]["lastBuyBoxUpdate"] = minutes(NOW)
    calls, _, _ = transport(monkeypatch, db, {row["asin"]: incoming})
    run(db, cfg)
    after = db.get_deal(row["dedupe_key"])
    assert len(calls) == 1 and calls[0]["buybox"] == "1"
    assert after["status"] == "alive"
    assert click_price.has_gap(after, NOW) == (reading != "newer")
    if reading != "newer":
        assert after["final_price"] == 4.55
        assert after["click_price_check"]["buybox"] == 32
        assert after["click_price_check"]["at"] == util.iso(NOW - timedelta(minutes=age))
    else:
        assert click_price.status(after, cfg, NOW) == "known_ok"
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    published = site.render_site(db, cfg, NOW, out_dir=tmp_path)
    assert bool(published["public_deals"]) == (reading == "newer")


@pytest.mark.parametrize("change", [{"asin": "B000000002"}, {"final_price": 5},
    {"price_since": util.iso(NOW)}, {"first_seen_at": util.iso(NOW)}])
def test_facts_are_bound_to_current_identity_price_and_clocks(db, cfg, change):
    row = adidas(db, cfg)
    save(db, row, record(row, 4.60))
    row.update(change)
    assert click_price.status(row, cfg, NOW) == "unknown"


@pytest.mark.parametrize("support,opened", [(False, False), (False, True), (True, False), (True, True)])
def test_shadow_counts_without_changing_any_public_or_member_bytes(db, cfg, tmp_path, monkeypatch, support, opened):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg.support_enabled, cfg.paywall_open_access = support, opened
    row = adidas(db, cfg)
    save(db, row, record(row))
    outputs = []
    for mode in ("off", "shadow"):
        cfg.keepa_click_price_rule = mode
        result = site.render_site(db, cfg, NOW, out_dir=tmp_path / mode)
        outputs.append((tree(tmp_path / mode), result["full_payload"]))
        report = click_price.summary(db, cfg, NOW, result["public_deals"], result["public_deals"])
        assert report["wall"]["unknown"] == 1
        assert report["top_picks"]["unknown"] == 1
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize("tokens", [11, 12, 13, 16, 22, 40])
@pytest.mark.parametrize("planner", [False, True])
def test_visible_reads_keep_buybox_premium_and_drop_lowest_visibility(db, cfg, monkeypatch, tokens, planner):
    cfg.keepa_volatility["enabled"] = planner
    cfg.keepa_recheck_top_live = False
    cfg.keepa_buybox_top_n = 0
    rows = [seed(db, cfg, asin=f"B{i:09d}") for i in range(5)]
    remember(db, top=[rows[1]], wall=[rows[0], rows[1], rows[2], rows[3]])
    calls, _, _ = transport(monkeypatch, db, {d["asin"]: product(2500, asin=d["asin"]) for d in rows}, tokens=tokens)
    result = run(db, cfg)
    boxes = [c for c in calls if c.get("buybox") == "1"]
    asins = [a for c in boxes for a in c["asin"].split(",")]
    expected = [d["asin"] for d in (rows[1], rows[0], rows[2], rows[3])][:max(0, (tokens - 10) // 3)] if tokens >= 15 else []
    assert asins == expected
    assert all(c.get("buybox") == "1" for c in calls if any(d["asin"] in c["asin"] for d in rows[:4]))
    assert all("buybox" not in c for c in calls if rows[4]["asin"] in c["asin"])
    assert result["tokens"]["requested"] <= tokens - 10
    assert result["price_source"]["click_price"]["extra_tokens"] == len(asins) * 2
    if planner:
        report = result["token_plan"]
        assert report["click_price_extra_per_minute"] > 0
        assert report["allocated_per_minute"] <= report["rechecks_granted_per_minute"] + 1e-9


@pytest.mark.parametrize("planner", [False, True])
@pytest.mark.parametrize("tokens,count", [(15, 1), (16, 2), (19, 3)])
def test_published_picks_and_wall_precede_hidden_recovery_candidates(
        db, cfg, monkeypatch, tmp_path, planner, tokens, count):
    cfg.keepa_volatility["enabled"] = planner
    cfg.keepa_recheck_top_live = False
    cfg.keepa_buybox_top_n = 0
    cfg.front_page["top_picks"]["count"] = 1
    hidden, pick, wall = [seed(db, cfg, asin=f"B{i:09d}",
        seen=NOW - timedelta(hours=age), price_rechecked_at=util.iso(NOW - timedelta(minutes=5)))
        for i, age in enumerate((1, 8, 12))]
    records = {d["asin"]: product(2500, asin=d["asin"], buyBoxPrice=2500, buyBoxShipping=0,
        lastBuyBoxUpdate=minutes(NOW - timedelta(minutes=5))) for d in (hidden, pick, wall)}
    for row in (pick, wall):
        save(db, row, records[row["asin"]])
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    published = site.render_site(db, cfg, NOW, out_dir=tmp_path)
    assert json.loads(db.meta_get(site.PICKS_META))[0] == hidden["dedupe_key"]
    assert json.loads(db.meta_get(click_price.PICKS_META)) == [pick["dedupe_key"]]
    assert json.loads(db.meta_get(click_price.WALL_META)) == [pick["dedupe_key"], wall["dedupe_key"]]
    assert next(d for d in published["public_deals"] if d.get("pick") == 1)["dedupe_key"] == pick["dedupe_key"]
    calls, _, _ = transport(monkeypatch, db, records, tokens=tokens)
    result = run(db, cfg)
    assert [a for c in calls for a in c["asin"].split(",")] == [d["asin"] for d in (pick, wall, hidden)][:count]
    assert all(c["buybox"] == "1" for c in calls)
    assert result["tokens"]["requested"] == count * 3
    assert result["tokens"]["bucket_end"] >= 10


@pytest.mark.parametrize("planner", [False, True])
@pytest.mark.parametrize("unique", [False, True])
@pytest.mark.parametrize("changes,cost", [({"conditions": ["used"]}, 13), ({"codes": ["SAVE"]}, 1)])
def test_published_cards_precede_hidden_buybox_reads_across_transport_modes(
        db, cfg, monkeypatch, tmp_path, planner, unique, changes, cost):
    cfg.front_page["used_tab_only"] = False  # recorded before task 274: used cards share the home wall
    cfg.keepa_volatility["enabled"] = planner
    cfg.keepa_unique_asin_rechecks_enabled = unique
    cfg.keepa_recheck_top_live = False
    cfg.keepa_buybox_top_n = 0
    cfg.keepa_used_scan_budget_share = 1
    hidden = seed(db, cfg, asin="B000000001")
    visible = seed(db, cfg, asin="B000000002", **changes,
                   price_rechecked_at=util.iso(NOW - timedelta(hours=5)))
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    site.render_site(db, cfg, NOW, out_dir=tmp_path)
    assert json.loads(db.meta_get(site.PICKS_META))[0] == hidden["dedupe_key"]
    assert json.loads(db.meta_get(click_price.WALL_META)) == [visible["dedupe_key"]]
    records = {d["asin"]: product(2500, asin=d["asin"]) for d in (hidden, visible)}
    if cost == 13:
        records[visible["asin"]].update(
            offers=[{"condition": 2, "offerCSV": [minutes(NOW), 2500, 0]}], liveOffersOrder=[0])
        records[visible["asin"]]["stats"]["lastOffersUpdate"] = minutes(NOW)
    calls, _, _ = transport(monkeypatch, db, records, tokens=24)
    result = run(db, cfg)
    expected = [visible["asin"]] + ([hidden["asin"]] if cost == 1 else [])
    assert [a for c in calls for a in c["asin"].split(",")] == expected
    assert "buybox" not in calls[0]
    assert ("offers" in calls[0]) == (cost == 13)
    assert result["tokens"]["requested"] == cost + (3 if cost == 1 else 0)
    assert result["tokens"]["bucket_end"] >= 10


@pytest.mark.parametrize("age,expected", [(61, 0), (110, 12), (120, 12)])
def test_pipeline_premium_reads_honor_stretched_paid_request_cadence(
        db, cfg, monkeypatch, age, expected):
    cfg.keepa_tokens_per_minute = 1
    cfg.keepa_recheck_top_live_adaptive = False
    cfg.keepa_amazon_pages_per_pull = cfg.keepa_warehouse_pages_per_pull = 0
    cfg.keepa_overflow_extra_pages_max = 0
    cfg.keepa_focus_categories = []
    rows = [seed(db, cfg, asin=f"B{i:09d}",
        price_rechecked_at=util.iso(NOW - timedelta(minutes=age if i < 12 else 0)),
        buybox_requested_at=util.iso(NOW - timedelta(minutes=age if i < 12 else 0)))
        for i in range(30)]
    remember(db, top=rows, wall=rows)
    selections, batches = [], Plan.batches

    def select(plan, entries, *args, **kwargs):
        selections.extend(d["dedupe_key"] for d, options in entries if options[3] == "buybox_top_new")
        return batches(plan, entries, *args, **kwargs)

    monkeypatch.setattr(Plan, "batches", select)
    records = {d["asin"]: product(2500, asin=d["asin"], buyBoxPrice=2500, buyBoxShipping=0,
                                lastBuyBoxUpdate=minutes(NOW)) for d in rows}
    calls, _, _ = transport(monkeypatch, db, records, tokens=60)
    KeepaBudget(60, 1, NOW).save(db)
    result = run(db, cfg)
    report = result["token_plan"]
    assert report["tiers"]["NORMAL"]["classes"]["top"]["effective_target_h"] == pytest.approx(2)
    assert report["allocated_per_minute"] <= report["rechecks_granted_per_minute"] == 1
    assert [a for c in calls for a in c["asin"].split(",")] == [d["asin"] for d in rows[:expected]]
    assert selections == [d["dedupe_key"] for d in rows[:expected]]
    assert all(c.get("buybox") == "1" and c["update"] == "0" for c in calls)
    assert result["tokens"]["requested"] == expected * 4
    assert result["price_source"]["click_price"]["extra_tokens"] == expected * 2
    assert result["tokens"]["bucket_end"] >= 10
    before = len(calls)
    run(db, cfg)
    assert len(calls) == before


@pytest.mark.parametrize("planner", [False, True])
@pytest.mark.parametrize("unique", [False, True])
@pytest.mark.parametrize("new_count,share", [(0, 0), (3, 0), (3, 1)])
def test_visible_due_scan_keeps_regular_funding_when_premium_reads_use_overflow(
        db, cfg, monkeypatch, planner, unique, new_count, share):
    cfg.keepa_volatility["enabled"] = planner
    cfg.keepa_unique_asin_rechecks_enabled = unique
    cfg.keepa_recheck_top_live = False
    cfg.keepa_recheck_surplus = True
    cfg.keepa_recheck_surplus_reserve = 20
    cfg.keepa_used_scan_budget_share = share
    rows = [seed(db, cfg, asin=f"B{i:09d}") for i in range(new_count)]
    used = seed(db, cfg, asin="B000000003", conditions=["used"],
                price_rechecked_at=util.iso(NOW - timedelta(hours=5)))
    remember(db, top=rows, wall=[*rows, used])
    monkeypatch.setenv("KEEPA_KEY", "offline")
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    calls, balance = [], [120]

    def get(url, params=None, **kwargs):
        assert url == "https://api.keepa.com/product"
        asins = params["asin"].split(",")
        calls.extend(asins)
        cost = len(asins) * (13 if "offers" in params else 3)
        balance[0] -= cost
        assert balance[0] >= 20
        records = [product(2500, asin=asin) for asin in asins]
        if "offers" in params:
            assert "buybox" not in params
            for record in records:
                record.update(offers=[{"condition": 2, "offerCSV": [minutes(NOW), 2500, 0]}],
                              liveOffersOrder=[0])
                record["stats"]["lastOffersUpdate"] = minutes(NOW)
        return httpx.Response(200, json={"products": records, "tokensConsumed": cost,
                              "tokensLeft": balance[0], "refillRate": 2})

    fetcher = mock_keepa_get(monkeypatch, get)
    KeepaBudget(120, 2, NOW).save(db)
    result = run(db, cfg, fetcher=fetcher)
    used_checked = not rows or bool(share)
    assert calls == [d["asin"] for d in rows] + ([used["asin"]] if used_checked else [])
    assert result["tokens"]["requested"] == 3 * new_count + 13 * used_checked
    assert History(db, cfg, NOW).attempted(used["dedupe_key"]) == used_checked


def test_visible_scope_includes_300_wall_spotlight_and_social_candidates(db, cfg, monkeypatch):
    cfg.keepa_hidden_new_tokens_per_run = 10  # non-visible Unknowns use ordinary recovery reads
    cfg.keepa_recheck_top_live = False
    cfg.keepa_buybox_top_n = 0
    rows = [seed(db, cfg, asin=f"B{i:09d}") for i in range(6)]
    db.meta_set(site.WALL_META, json.dumps(["unused"] * 299 + [rows[0]["dedupe_key"], rows[1]["dedupe_key"]]))
    db.meta_set(site.PICKS_META, json.dumps([rows[2]["dedupe_key"]]))
    db.meta_set(click_price.VISIBLE_META, json.dumps([rows[3]["dedupe_key"], rows[4]["dedupe_key"]]))
    calls, _, _ = transport(monkeypatch, db, {d["asin"]: product(2500, asin=d["asin"]) for d in rows})
    run(db, cfg)
    boxes = {a for c in calls if c.get("buybox") == "1" for a in c["asin"].split(",")}
    assert boxes == {rows[i]["asin"] for i in (0, 2, 3, 4)}
    assert {rows[1]["asin"], rows[5]["asin"]} <= {a for c in calls if "buybox" not in c for a in c["asin"].split(",")}


@pytest.mark.parametrize("published", [False, True])
def test_planner_premium_scope_matches_the_first_300_published_wall_cards(db, cfg, published):
    cfg.keepa_buybox_top_n = 0
    cfg.keepa_recheck_top_live = False
    rows = [seed(db, cfg, asin=f"B{i:09d}") for i in range(5)]
    wall = {f"other-{i}": i for i in range(299)}
    wall.update({rows[0]["dedupe_key"]: 299, rows[1]["dedupe_key"]: 300})
    picks = {rows[2]["dedupe_key"]: 0}
    if published:
        db.meta_set(click_price.PICKS_META, json.dumps(list(picks)))
        db.meta_set(click_price.WALL_META, json.dumps(list(wall)))
    db.meta_set(click_price.VISIBLE_META, json.dumps([rows[3]["dedupe_key"]]))
    plan = Plan(History(db, cfg, NOW), rows, picks, wall, 10)
    assert plan.published == {rows[i]["dedupe_key"] for i in (0, 2)} | {
        f"other-{i}" for i in range(299)}
    assert [plan.costs[d["dedupe_key"]] for d in rows] == [3, 1, 3, 3, 1]
    assert plan.report(rows)["click_price_extra_per_minute"] == pytest.approx(2 / 120 + 4 / 45)


def test_summary_contains_only_counts_and_five_compact_examples(db, cfg):
    rows = []
    for i in range(8):
        row = seed(db, cfg, asin=f"B{i:09d}", price_rechecked_at=util.iso(NOW - timedelta(minutes=10)))
        p = product(2500, asin=row["asin"], buyBoxPrice=(2500 if i % 2 else 5000), buyBoxShipping=0,
                    lastBuyBoxUpdate=minutes(NOW - timedelta(minutes=5)), buyBoxSellerId="PRIVATE-SELLER")
        save(db, row, p)
        rows.append(row)
    report = click_price.summary(db, cfg, NOW, rows[:4], rows, 16)
    assert report["wall"] == dict(known_ok=4, known_above=4, unknown=0, promo=0)
    assert report["top_picks"] == dict(known_ok=2, known_above=2, unknown=0, promo=0)
    assert len(report["examples"]) == 5 and report["extra_tokens"] == 16
    assert all(set(e) == {"asin", "final", "buybox", "amazon", "new"} for e in report["examples"])
    assert "PRIVATE-SELLER" not in json.dumps(report)


def test_home_order_and_search_in_emitted_javascript(db, cfg, tmp_path, monkeypatch):
    pre_drop_fresh(cfg)  # The original click-price home policy, before fresh Trending.
    cfg.click_price_home_fill = False  # the original rule; site.click_price_home_fill: tests/test_trending_auto_cards.py
    row = adidas(db, cfg)
    save(db, row, record(row))
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    site.render_site(db, cfg, NOW, out_dir=tmp_path)
    proc = subprocess.run(["node", "tests/click_price_logic.cjs", str(tmp_path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert click_price.home_rows([{"click_price_unknown": True}] + [{}] * 119) == [{}] * 119
    assert click_price.home_rows([{"click_price_unknown": True}] + [{}] * 120)[120]["click_price_unknown"]


@pytest.mark.parametrize("audit_mode", ["shadow", "enforce"])
def test_enabled_pipeline_rerun_is_idempotent(env, capsys, audit_mode):
    from tests.test_e2e_offline import test_rerun_is_idempotent
    path = Path(env["cfg"])
    path.write_text(path.read_text() + '\nkeepa:\n  click_price_rule: "on"\n')
    test_rerun_is_idempotent(env, capsys, audit_mode)


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("path", ["post", "redirect", "asin"])
def test_pipeline_recovers_unknown_amazon_identity_before_price_confirmation(
        cfg, tmp_path, monkeypatch, mode, path):
    from dealradar import pipeline

    cfg.keepa_click_price_rule = mode
    cfg.site_dir = str(tmp_path / "site")
    monkeypatch.setattr(pipeline, "load_config", lambda _: cfg)
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    for name in ("KEEPA_KEY", "DEALS_CLAUDE_API_KEY", "TELEGRAM_BOT_TOKEN",
                 "TELEGRAM_CHANNEL_ID", "SMTP_HOST", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(name, raising=False)
    asin = "B0CNSDZ47Y"
    target = f"https://www.amazon.com/dp/{asin}"
    link = {"post": "", "redirect": "https://on.ltk.com/+recover",
            "asin": "https://amzn.to/recover"}[path]
    db_path = tmp_path / "state.db"
    state = DB(db_path)
    try:
        row = seed(state, cfg, asin="", si=True, canonical_url=link, link_as_posted=link,
                   price_rechecked_at=util.iso(NOW))
        row["image_url"] = "https://images.example/product.jpg" if path == "asin" else ""
        key = row["dedupe_key"]
        state.update_deal(key, payload=row, canonical_url=link)
        state.conn.execute("UPDATE sightings SET source_url='https://deals.example/post'")
        state.conn.commit()
        assert rank.is_publishable(state.get_deal(key), cfg)[0]
        assert not state.get_deal(key).get("asin")
        if mode == "on":
            assert rank.site_deals(state, cfg, NOW) == []
    finally:
        state.close()
    calls, checked = [], []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        calls.append((request.method, str(request.url)))
        if request.url.host == "images.example":
            assert request.method == "HEAD"
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"})
        if str(request.url) == link:
            assert request.method == "HEAD"
            return httpx.Response(302, headers={"Location": target})
        assert str(request.url) == "https://deals.example/post"
        return httpx.Response(200, text=f'<a href="{target}">Buy</a>')

    def recheck(state, cfg, now, **kwargs):
        current = state.get_deal(key)
        checked.append(current.get("asin", ""))
        if current.get("asin") and not current.get("click_price_check"):
            assert click_price.status(current, cfg, now) == "unknown"
            if mode == "on":
                assert rank.site_deals(state, cfg, now) == []
            click_price.remember(current, product(2500, asin=asin, buyBoxPrice=2500,
                buyBoxShipping=0, lastBuyBoxUpdate=minutes(now)), now, now)
            state.update_deal(key, payload=current)
        return {}

    monkeypatch.setattr(pipeline, "_recheck_prices", recheck)

    def invoke(fast):
        return pipeline.run(fast_lane=fast, db_path=str(db_path), now=NOW,
                            transport=httpx.MockTransport(handler), sources_override=[],
                            skip_validation=True, dry_run_telegram=True)

    invoke(False)
    assert checked == [""]
    first = invoke(True)
    assert checked[-1] == asin
    assert first["post_links"]["found"] == (path == "post")
    assert first["short_links"] == (path == "redirect")
    assert first["photo_asins"]["found"] == (path == "asin")
    before = list(calls)
    repeated = invoke(True)
    assert repeated["post_links"]["tried"] == repeated["short_links"] == 0
    assert repeated["photo_asins"]["tried"] == 0
    assert calls == before
    state = DB(db_path)
    try:
        recovered = state.get_deal(key)
        assert recovered["asin"] == asin and recovered["dedupe_key"] == key
        assert click_price.status(recovered, cfg, NOW) == "known_ok"
        assert [d["dedupe_key"] for d in rank.site_deals(state, cfg, NOW)] == [key]
    finally:
        state.close()


def test_paid_rerun_preserves_prices_rows_calls_and_site(db, cfg, monkeypatch, tmp_path):
    row = adidas(db, cfg)
    remember(db, top=[row], wall=[row])
    calls, _, _ = transport(monkeypatch, db, {row["asin"]: record(row)})
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    run(db, cfg)
    site.render_site(db, cfg, NOW, out_dir=tmp_path)
    before = (db.all_deals(), tree(tmp_path), len(calls), list(db.conn.iterdump()))
    run(db, cfg)
    site.render_site(db, cfg, NOW, out_dir=tmp_path)
    assert (db.all_deals(), tree(tmp_path), len(calls), list(db.conn.iterdump())) == before


def test_shadow_paid_pass_counts_without_changing_requests_prices_or_bytes(cfg, monkeypatch, tmp_path):
    cfg.keepa_buybox_top_n = 0
    cfg.keepa_recheck_top_live = False
    cfg.keepa_volatility["enabled"] = False
    snapshots, requests = [], []
    for mode in ("off", "shadow"):
        cfg.keepa_click_price_rule = mode
        state = DB(":memory:")
        try:
            row = adidas(state, cfg)
            row["price_rechecked_at"] = util.iso(NOW - timedelta(hours=2))
            state.update_deal(row["dedupe_key"], payload=row)
            remember(state, top=[row], wall=[row])
            calls, _, _ = transport(monkeypatch, state, {row["asin"]: record(row, 32)})
            result = run(state, cfg)
            monkeypatch.setattr(site, "util_now", lambda: NOW)
            published = site.render_site(state, cfg, NOW, out_dir=tmp_path / mode)
            snapshots.append((tree(tmp_path / mode), published["full_payload"]))
            requests.append(calls)
            assert state.get_deal(row["dedupe_key"])["final_price"] == 4.55
            if mode == "shadow":
                assert result["price_source"]["click_price"]["top_picks"]["known_above"] == 1
                assert result["price_source"]["click_price"]["extra_tokens"] == 0
        finally:
            state.close()
    assert snapshots[0] == snapshots[1] and requests[0] == requests[1]


@pytest.mark.parametrize("mode", ["off", "on"])
def test_shipped_click_price_stays_within_phone_byte_budgets(db, tmp_path, monkeypatch, mode):
    from scripts.publication_budget import analyze
    from tests.phone_budget_fixture import visible_offer_count
    from tests.phone_budget_fixture import NOW as PHONE_NOW, fixture_offers

    cfg = load_config()
    cfg.keepa_click_price_rule = mode
    cfg.phone_budget = dict(cfg.phone_budget, enabled=True)
    cfg.hide_deal_site_links = False  # as publish_fixture: the larger page with partner-post offers
    rows = fixture_offers()
    # Unknown labels use normal publish gates, including critic evidence.
    for row in rows:
        row["critic"] = {"plausible": True, "confidence": .9, "reason": "Backed basis"}
    monkeypatch.setattr(site, "util_now", lambda: PHONE_NOW)
    site.render_site(db, cfg, PHONE_NOW, out_dir=tmp_path, deals=rows,
                     last_run={"run_key": "task275-phone"})
    report = analyze(tmp_path, cfg, expected_offers=visible_offer_count(cfg))
    assert report["ok"], report["metrics"]


def test_within_ten_percent_is_corrected_even_when_legacy_kill_tolerance_is_five(db, cfg, monkeypatch):
    row = adidas(db, cfg)
    remember(db, top=[row], wall=[row])
    p = record(row, 5.00)  # 9.9% higher; no averages available for repricing
    del p["stats"]["avg30"], p["stats"]["avg90"]
    transport(monkeypatch, db, {row["asin"]: p})
    run(db, cfg)
    after = db.get_deal(row["dedupe_key"])
    assert not after.get("gone_suspect_at") and after["status"] == "alive"
    assert after["final_price"] == 5.00
    assert after["regular_price"] is after["pct_at_checkout"] is after["abs_savings"] is None
    assert click_price.status(after, cfg, NOW) == "known_ok"


def test_old_listing_clock_cannot_become_buybox_confirmation(db, cfg):
    row = adidas(db, cfg)
    row["buybox_check"] = {"asin": row["asin"], "final": row["final_price"], "source": "buybox",
                           "price": 4.6, "at": util.iso(NOW)}
    assert click_price.status(row, cfg, NOW) == "unknown"


def test_unknown_never_features_or_posts_even_from_old_public_pick(db, cfg, monkeypatch):
    row = seed(db, cfg, price_rechecked_at=util.iso(NOW))
    cfg.social_require_stock_evidence = False
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    assert not social.plenty_of_stock(db, row, cfg)
    assert rank.published_deals(db, cfg, NOW) == []
    cfg.keepa_click_price_rule = "shadow"
    assert social.plenty_of_stock(db, row, cfg)
    cfg.keepa_click_price_rule = "on"
    cfg.front_page["spotlight"] = {"max_cards": 1, "sources": ["meh"]}
    row["sightings"] = [{"source_name": "meh", "seen_at": util.iso(NOW), "final_price": row["final_price"]}]
    assert rank.daily_spotlight([row], cfg, NOW) == []
    assert len(rank.daily_spotlight([row], replace(cfg, keepa_click_price_rule="off"), NOW)) == 1


@pytest.mark.parametrize("failure", ["lost", "429"])
def test_failed_visible_requests_keep_reserves_and_same_key_allowance(db, cfg, monkeypatch, failure):
    row = adidas(db, cfg)
    remember(db, top=[row], wall=[row])
    calls, _, _ = transport(monkeypatch, db, {row["asin"]: record(row)}, tokens=20, failure=failure)
    first = run(db, cfg)
    assert first["tokens"]["requested"] <= 10
    assert len(calls) == 1
    run(db, cfg)
    assert len(calls) == 1 and click_price.status(db.get_deal(row["dedupe_key"]), cfg, NOW) == "unknown"


def test_token_planner_includes_premium_when_stretching_to_a_scarce_grant(db, cfg):
    cfg.keepa_tokens_per_minute = 1  # lower the fixture grant; never increase a live budget
    cfg.keepa_amazon_pages_per_pull = cfg.keepa_warehouse_pages_per_pull = 0
    cfg.keepa_overflow_extra_pages_max = 0
    cfg.keepa_focus_categories = []
    rows = [seed(db, cfg, asin=f"B{i:09d}") for i in range(30)]
    picks = {d["dedupe_key"]: i for i, d in enumerate(rows)}
    plan = Plan(History(db, cfg, NOW), rows, picks, {}, 10)
    report = plan.report(rows)
    assert set(plan.costs.values()) == {4}  # stats + forced update + Buy Box
    assert report["needed_per_minute"] > report["granted_per_minute"] == 1
    assert report["allocated_per_minute"] <= 1 + 1e-9
    assert report["click_price_extra_per_minute"] > 0
    entries = [(d, [15, 4, 100, "top_new", True, False, False]) for d in rows]
    selected, skipped = plan.batches(entries, {}, available=16, used_left=0, seconds=100)
    assert [d["dedupe_key"] for group, *_ in selected for d in group] == [rows[0]["dedupe_key"]]
    assert skipped == 29


@pytest.mark.parametrize("changes", [{"conditions": ["used"]}, {"conditions": ["open_box"]},
    {"store": "Walmart", "canonical_url": "https://www.walmart.com/ip/12345"}])
def test_other_store_and_used_confirmation_paths_are_unchanged(db, cfg, changes):
    row = adidas(db, cfg, **changes)
    assert click_price.status(row, cfg, NOW) is None
    assert verify.price_unconfirmed(row, cfg, NOW) == verify.price_unconfirmed(
        row, replace(cfg, keepa_click_price_rule="off"), NOW)
