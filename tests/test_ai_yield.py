"""Synthetic, unpaid replay and persistent useful-yield contracts (task 067)."""

import dataclasses
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from dealradar import ai_yield, pipeline, rank
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.dedupe import upsert_extracted
from dealradar.growth import report
from dealradar.parse.extractor import parse_posts, verify_evidence
from dealradar.parse.schema import CriticVerdicts, LLMDeal, LLMDeals, to_extracted
from dealradar.savings import compute_savings
from dealradar.util import iso
from tests.test_claude_client import (FakeCreateOnlyMessages, FakeCreateResponse,
    FakeMessagesWithParse, FakeParseResponse, FakeUsage, make_parser)
from tests.test_e2e_offline import NOW, SOURCES, _handler, _run, env
from tests.test_extractor import FakeParser, make_cfg, make_post
from tests.test_quality_trio import SecondOpinionParser


def _cfg():
    cfg = load_config()
    cfg.critic_tiering = dict(cfg.critic_tiering, enabled=False)  # predates tiering
    return cfg


def raw(item="Widget", price=20, regular=100, **over):
    return LLMDeal(item=item, store="Store", category="home", online_only="true",
                   product_url=f"https://retailer.example/{item.replace(' ', '-')}",
                   regular_price=regular, final_price=price,
                   evidence=dict(regular_price_quote=f"was ${regular}",
                                 final_price_quote=f"now ${price}"), **over)


def ingest(db, cfg, item, price, now, source="partner", **over):
    post = make_post(source_name=source, text=f"{item} was $100 now ${price}",
                     published_at=now, fetched_at=now, **over)
    extracted = to_extracted(raw(item, price), post, "UTC", now)
    assert verify_evidence(extracted, post.text) == (True, "")
    return upsert_extracted(db, extracted, compute_savings(extracted), post, cfg, now)["dedupe_key"]


@pytest.mark.parametrize("headline,site,price,candidate,second", [
    (40, 40, 70, False, False), (40, 40, 70.01, False, False), (40, 40, 70.1, True, False),
    (70, 40, 60, False, True), (70, 40, 60.01, False, True), (70, 40, 60.1, False, False),
    (70, 40, 70.1, True, False), (40, 70, 60, False, True),
    (70, 10, 90, False, True),
])
def test_intake_respects_effective_site_floor_and_buffer(headline, site, price, candidate, second):
    cfg = make_cfg(min_pct=headline, site_show_from_pct=site,
                   prefilter_below_floor="on", second_opinion_model="fixture")
    parser = SecondOpinionParser(cfg, first_pass=[[]], second_pass=[[]])
    _, stats = parse_posts([make_post(text=f"Widget was $100 now ${price}")], parser, cfg, NOW, {})
    assert stats["prefilter"]["would_skip"] == int(candidate)
    assert stats["second_opinion"]["calls"] == int(second)
    assert parser.kinds == ([] if candidate else ["extract"] + (["second_opinion"] if second else []))


@pytest.fixture
def replay():
    fixtures = [
        ("Coupon lamp was $100 now $90, extra $70 off coupon", raw("Coupon lamp", 90,
            stack_steps=["now $90", "extra $70 off coupon"]).model_copy(update={"final_price": None})),
        ("Rebate lamp was $100 now $90, $80 rebate", raw("Rebate lamp", 90, rebates=["$80 rebate"])),
        ("Range lamp was $100 now $90, price range", raw("Range lamp", 90, price_is_range=True)),
        ("Roundup lamp was $100 now $90, more deals: free toy", raw("Roundup lamp", 90)),
        ("Low tier lamp was $100 now $60", raw("Low tier lamp", 60)),
        ("Used lamp was $100 now $60", raw("Used lamp", 60, conditions=["used"])),
        ("Plain lamp was $100 now $90", raw("Plain lamp", 90)),
        ("Malformed lamp was $100..00 now $90", raw("Malformed lamp", 90)),
    ]
    return [(make_post(source_name=f"site{i % 3}", text=text), deal)
            for i, (text, deal) in enumerate(fixtures)]


def test_offline_replay_preserves_useful_source_and_category_supply(replay):
    cfg = make_cfg(min_pct=70, site_show_from_pct=40, used_min_pct=50)
    results = {}
    for mode in ("off", "shadow", "on"):
        cfg.prefilter_below_floor = mode
        accepted = []
        output = []
        for post, deal in replay:
            pairs, _ = parse_posts([post], FakeParser([[deal]], spend_usd=0), cfg, NOW, {})
            output.extend(dataclasses.asdict(d) for _, d in pairs)
            for p, extracted in pairs:
                sav = compute_savings(extracted)
                candidate = dict(dataclasses.asdict(extracted), final_price=sav.final_price,
                                 pct_at_checkout=sav.pct_at_checkout, abs_savings=sav.abs_savings)
                if rank.is_publishable(candidate, cfg, min_pct=40)[0]:
                    accepted.append((p.source_name, extracted.category, extracted.item))
        results[mode] = (output, accepted)
    assert results["shadow"] == results["off"]
    assert results["on"][1] == results["off"][1]
    assert {item for _, _, item in results["on"][1]} == {"Coupon lamp", "Low tier lamp"}
    assert "Used lamp" not in {item for _, _, item in results["on"][1]}


@pytest.mark.parametrize("text", ["was $100 now $90 extra", "was $100 now $90 rebate",
    "was $100 now $90 as low as", "was $100 now $90 range", "was $100 now $90 80% off",
    "was $100 now $90 gift card", "was $100 now -$90", "was $100..00 now $90",
    "was $100 now $90, other $20", "was $100 now $90, BOGO"])
def test_ambiguous_extra_and_malformed_offers_stay_eligible(text):
    parser = FakeParser([[]])
    _, stats = parse_posts([make_post(text=text)], parser,
                           make_cfg(prefilter_below_floor="on"), NOW, {})
    assert len(parser.calls) == 1 and stats["prefilter"]["would_skip"] == 0


@pytest.mark.parametrize("stop,parsed,unevaluated", [("end_turn", LLMDeals(deals=[]), 0),
    ("refusal", LLMDeals(deals=[]), 1), ("max_tokens", LLMDeals(deals=[]), 1),
    ("end_turn", None, 1)])
def test_empty_success_is_distinct_from_failed_extraction(tmp_path, stop, parsed, unevaluated):
    cfg = make_cfg(prefilter_below_floor="shadow", parse_structured_output=True,
                   parse_model="claude-haiku-5-5",
                   model_pricing_per_mtok=load_config().model_pricing_per_mtok)
    parser = make_parser(FakeMessagesWithParse([
        FakeParseResponse(parsed, FakeUsage(100, 20), stop_reason=stop)]), cfg=cfg)
    db = DB(tmp_path / "state.db")
    _, stats = parse_posts([make_post(text="Widget was $100 now $90")], parser, cfg, NOW, {}, db=db)
    assert stats["prefilter"]["unevaluated"] == unevaluated
    assert db.conn.execute("SELECT COUNT(*) FROM post_parses").fetchone()[0] == 1 - unevaluated
    db.close()


def test_json_failures_and_partial_salvage_never_become_clean_observations(tmp_path):
    cfg = make_cfg(prefilter_below_floor="shadow", parse_model="claude-haiku-5-5",
                   model_pricing_per_mtok=load_config().model_pricing_per_mtok)
    db = DB(tmp_path / "state.db")
    msgs = FakeCreateOnlyMessages([FakeCreateResponse("invalid", FakeUsage(100, 20)) for _ in range(3)])
    parser = make_parser(msgs, cfg=cfg)
    _, stats = parse_posts([make_post(text="Widget was $100 now $90")], parser, cfg, NOW, {}, db=db)
    assert len(msgs.create_calls) == 3
    assert stats["prefilter"]["unevaluated"] == 1
    assert parser.spend_usd > 0 and db.conn.execute("SELECT COUNT(*) FROM post_parses").fetchone()[0] == 0
    # Retaining a complete first object still does not evaluate the missing tail.
    truncated = '{"deals": [' + raw(price=90).model_dump_json() + ', {"item":'
    parser = make_parser(FakeCreateOnlyMessages([
        FakeCreateResponse(truncated, FakeUsage(100, 20), "max_tokens")]), cfg=cfg)
    pairs, stats = parse_posts([make_post(text="Widget was $100 now $90")], parser, cfg, NOW, {}, db=db)
    assert len(pairs) == 1 and stats["prefilter"]["unevaluated"] == 1
    db.close()


@pytest.mark.parametrize("interruption,unevaluated", [("max_posts", 2), ("deadline", 3), ("spend_cap", 2),
                                                      ("disabled", 3)])
def test_deferred_candidates_are_counted_before_caps_and_deadline(interruption, unevaluated):
    posts = [make_post(source_name=f"site{i}", text=f"Widget {i} was $100 now $90") for i in range(3)]
    cfg = make_cfg(prefilter_below_floor="shadow", max_posts_per_run=1 if interruption == "max_posts" else 10)
    parser = FakeParser([[]], raise_cap_at=1 if interruption == "spend_cap" else None)
    if interruption == "disabled":
        _, stats = pipeline._parse_new_posts(posts, None, cfg, NOW, {}, None)
    else:
        _, stats = parse_posts(posts, parser, cfg, NOW, {}, deadline=0 if interruption == "deadline" else None)
    assert stats["prefilter"]["would_skip"] == 3
    assert stats["prefilter"]["unevaluated"] == unevaluated
    assert set(stats["prefilter"]["by_source"]) == {"site0", "site1", "site2"}


def test_preinstrumentation_cache_is_reused_but_cannot_prove_shadow_safety(tmp_path):
    db = DB(tmp_path / "state.db")
    cfg = make_cfg(prefilter_below_floor="shadow")
    post = make_post(text="Widget was $100 now $90")
    parse_posts([post], FakeParser([[]]), cfg, NOW, {}, db=db)
    ai_yield.start(db, cfg, NOW + timedelta(days=1), "first")
    _, stats = parse_posts([dataclasses.replace(post, source_name="other")], FakeParser(raise_cap_at=0),
                           cfg, NOW + timedelta(days=1), {}, db=db)
    assert stats["reused_posts"] == 1 and stats["prefilter"]["unevaluated"] == 1
    assert stats["post_counts"][dataclasses.replace(post, source_name="other").content_hash]["evaluation_gap"] == "legacy_cache"
    db.close()


def test_repeated_non_candidate_post_is_never_treated_as_a_candidate():
    post = make_post(text="Widget was $100 now $20")
    parser = FakeParser([[raw()]])
    pairs, stats = parse_posts([post, post], parser, make_cfg(prefilter_below_floor="shadow"), NOW, {})
    assert len(pairs) == 2 and len(parser.calls) == 1
    assert stats["prefilter"]["would_skip"] == stats["prefilter"]["unevaluated"] == 0


@pytest.fixture
def yield_env(env):
    path = Path(env["cfg"])
    path.write_text(path.read_text().replace("critic_shadow_batches_per_day: 12", "critic_shadow_batches_per_day: 0")
                    .replace("recategorize_other_per_run: 240", "recategorize_other_per_run: 0"))
    db = DB(env["db"])
    db.meta_set("recategorized_v2", "1")
    db.close()
    return env


def run_posts(env, monkeypatch, posts, extracts, verdicts=()):
    messages = FakeMessagesWithParse([
        *(FakeParseResponse(LLMDeals(deals=deals), FakeUsage(100, 20)) for deals in extracts),
        FakeParseResponse(CriticVerdicts.model_validate({"verdicts": list(verdicts)}), FakeUsage(100, 20))])
    monkeypatch.setattr(pipeline, "_fetch_all", lambda *a, **kw: (posts, {}))
    return pipeline.run(db_path=env["db"], config_path=env["cfg"],
                        sources_override=SOURCES, skip_validation=True, now=NOW,
                        transport=httpx.MockTransport(_handler), dry_run_telegram=True,
                        claude_client=SimpleNamespace(messages=messages))


@pytest.mark.parametrize("verdict,misses,gaps,new", [(True, 1, 0, 2), (False, 0, 0, 0), (None, 0, 1, 0)])
def test_post_critic_misses_count_posts_and_missing_judgment_is_a_gap(
        yield_env, monkeypatch, verdict, misses, gaps, new):
    # A synthetic false-positive rule exercises a real miss without relaxing
    # evidence or production gates. Two useful products in ONE candidate post.
    monkeypatch.setattr("dealradar.parse.extractor._below_floor_post", lambda *a: True)
    post = make_post(source_name="keepa", text="Widget was $100 now $20; Lamp was $100 now $20")
    verdicts = [] if verdict is None else [dict(index=i, plausible=verdict, confidence=.9, reason="Fixture")
                                           for i in range(2)]
    summary = run_posts(yield_env, monkeypatch, [post], [[raw(), raw("Lamp")]], verdicts)
    assert summary["prefilter"]["would_skip"] == 1
    assert summary["prefilter"]["would_skip_publishable"] == misses
    assert summary["prefilter"]["unevaluated"] == gaps
    assert summary["ai_yield"]["unique_new_public_deals"] == new
    db = DB(yield_env["db"])
    assert tuple(db.conn.execute("SELECT evaluated,miss FROM ai_prefilter_posts").fetchone()) == (1 - gaps, misses)
    db.close()


def test_known_miss_and_incomplete_evaluation_are_both_reported(yield_env, monkeypatch):
    monkeypatch.setattr("dealradar.parse.extractor._below_floor_post", lambda *a: True)
    post = make_post(source_name="keepa", text="Widget was $100 now $20; Lamp was $100 now $20")
    bad = raw("Lamp").model_copy(update={"evidence": raw("Lamp").evidence.model_copy(
        update={"final_price_quote": "invented now $20"})})
    summary = run_posts(yield_env, monkeypatch, [post], [[raw(), bad]],
                        [dict(index=0, plausible=True, confidence=.9, reason="Fixture")])
    assert summary["prefilter"]["would_skip_publishable"] == 1
    assert summary["prefilter"]["unevaluated"] == 1
    assert summary["ai_yield"]["unique_new_public_deals"] == 1
    db = DB(yield_env["db"])
    assert tuple(db.conn.execute("SELECT evaluated,miss,gap FROM ai_prefilter_posts").fetchone()) == (0, 1, "evidence")
    assert db.conn.execute("SELECT COUNT(*) FROM post_parses").fetchone()[0] == 0
    db.close()


def test_failed_execution_keeps_pending_audit_and_actual_paid_spend(yield_env, monkeypatch):
    post = make_post(source_name="blogone", text="PRIVATE BODY Widget was $100 now $90")
    monkeypatch.setattr(pipeline, "_fetch_all", lambda *a, **kw: ([post], {}))

    def failed_read(posts, parser, *a, **kw):
        parser._account(SimpleNamespace(usage=FakeUsage(100, 20)), kind="extract", source_name="blogone")
        raise RuntimeError("offline interrupted read")

    monkeypatch.setattr(pipeline, "_parse_new_posts", failed_read)
    with pytest.raises(RuntimeError, match="interrupted read"):
        _run(yield_env)
    db = DB(yield_env["db"])
    assert tuple(db.conn.execute("SELECT evaluated,miss,gap FROM ai_prefilter_posts").fetchone()) == (0, 0, "run_failed")
    assert db.conn.execute("SELECT failed FROM ai_yield_runs").fetchone()[0] == 1
    assert db.spend_usd_for_day(NOW) > 0
    assert "PRIVATE BODY" not in "\n".join(db.conn.iterdump())
    db.close()


def test_disabled_parser_reports_unevaluated_candidates_in_run_summary(yield_env, monkeypatch):
    post = make_post(source_name="blogone", text="Widget was $100 now $90")
    monkeypatch.setattr(pipeline, "_fetch_all", lambda *a, **kw: ([post], {}))
    monkeypatch.setattr(pipeline, "_build_parser", lambda *a, **kw: None)
    summary = _run(yield_env)
    assert summary["prefilter"]["would_skip"] == summary["ai_yield"]["prefilter_unevaluated"] == 1
    assert summary["prefilter"]["would_skip_publishable"] == 0
    assert summary["ai_yield"]["new_deals_per_usd"] is None
    db = DB(yield_env["db"])
    assert db.conn.execute("SELECT gap FROM ai_prefilter_posts").fetchone()[0] == "parser_unavailable"
    db.close()


def test_cross_source_feed_email_reruns_and_price_changes_count_once(tmp_path):
    db = DB(tmp_path / "state.db")
    cfg = _cfg()
    ai_yield.start(db, cfg, NOW, "run")
    key = ingest(db, cfg, "Widget", 20, NOW)
    assert ai_yield.record_public(db, cfg, NOW, "run", [key, key]) == dict(unique_new_public_deals=1, useful_price_updates=0)
    for source in ("partner", "other_feed", "other_site"):
        ingest(db, cfg, "Widget", 20, NOW, source, via_email=source == "partner")
    assert ai_yield.record_public(db, cfg, NOW, "run", [key]) == dict(unique_new_public_deals=0, useful_price_updates=0)
    later = NOW + timedelta(minutes=15)
    ingest(db, cfg, "Widget", 15, later, "email", via_email=True)
    assert ai_yield.record_public(db, cfg, later, "run", [key]) == dict(unique_new_public_deals=0, useful_price_updates=1)
    ingest(db, cfg, "Widget", 20, NOW, "late_copy")  # older sighting cannot restore the old price
    assert ai_yield.record_public(db, cfg, later, "run", [key])["useful_price_updates"] == 0
    ingest(db, cfg, "Widget", 25, later + timedelta(minutes=15))
    assert ai_yield.record_public(db, cfg, later, "run", [key])["useful_price_updates"] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM ai_yield_events").fetchone()[0] == 3
    db.close()


def test_tiered_yield_waits_for_a_valid_critic_then_counts_publication_once(tmp_path, monkeypatch):
    monkeypatch.setattr("dealradar.util.now_utc", lambda: NOW)
    db = DB(tmp_path / "state.db")
    cfg = load_config()
    assert cfg.critic_tiering["enabled"] is True
    cfg.critic_tiering["shadow_batches_per_day"] = 0
    cfg.keepa_recheck_top_n = cfg.keepa_recheck_wall_n = 0
    ai_yield.start(db, cfg, NOW.replace(hour=0), "run")
    key = ingest(db, cfg, "Widget", 20, NOW)
    empty = dict(unique_new_public_deals=0, useful_price_updates=0)
    assert ai_yield.record_public(db, cfg, NOW, "run", [key]) == empty
    messages = FakeMessagesWithParse(parse_responses=[FakeParseResponse(
        CriticVerdicts.model_validate({"verdicts": [dict(index=0, plausible=True,
            confidence=.9, reason="Fixture")]}), FakeUsage(100, 20))])
    parser = make_parser(messages, cfg=cfg)
    parser.bind_ledger(db, "run")
    result = pipeline._run_critic(db, cfg, parser, NOW)
    assert result["tiering"]["haiku_approved"] == 1
    assert [c["model"] for c in messages.parse_calls] == [cfg.critic_tiering["model"]]
    assert db.get_deal(key)["critic"]["model"] == cfg.critic_tiering["model"]
    assert ai_yield.record_public(db, cfg, NOW, "run", [key]) == dict(
        unique_new_public_deals=1, useful_price_updates=0)
    assert ai_yield.record_public(db, cfg, NOW, "run", [key]) == empty
    measured = ai_yield.aggregate(db, cfg, NOW.replace(hour=0), NOW.replace(hour=0) + timedelta(days=1))
    assert measured["spend_usd"] == pytest.approx(parser.spend_usd)
    assert measured["new_deals_per_usd"] == pytest.approx(1 / parser.spend_usd)
    db.close()


def test_old_inventory_and_below_floor_rows_never_inflate_new_supply(tmp_path):
    db = DB(tmp_path / "state.db")
    cfg = _cfg()
    key = ingest(db, cfg, "Widget", 20, NOW - timedelta(days=2))
    hidden = ingest(db, cfg, "Hidden lamp", 90, NOW)
    ai_yield.start(db, cfg, NOW, "first")
    assert ai_yield.record_public(db, cfg, NOW, "first", [key, hidden])["unique_new_public_deals"] == 0
    ingest(db, cfg, "Widget", 15, NOW)
    ingest(db, cfg, "Hidden lamp", 25, NOW + timedelta(minutes=1))
    assert ai_yield.record_public(db, cfg, NOW, "first", [key, hidden]) == dict(unique_new_public_deals=0, useful_price_updates=1)
    db.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_retired_inventory_reappearance_is_not_another_new_identity(tmp_path, legacy):
    db = DB(tmp_path / "state.db")
    cfg = _cfg()
    if legacy:
        key = ingest(db, cfg, "Widget", 20, NOW - timedelta(days=1))
    ai_yield.start(db, cfg, NOW, "first")
    if not legacy:
        key = ingest(db, cfg, "Widget", 20, NOW)
        ai_yield.record_public(db, cfg, NOW, "first", [key])
    db.update_deal(key, status="dead", last_seen_at=NOW)
    later = NOW + timedelta(days=15)
    db.prune(later)
    assert db.get_deal(key) is None
    key = ingest(db, cfg, "Widget", 20, later)
    assert ai_yield.record_public(db, cfg, later, "again", [key])["unique_new_public_deals"] == 0
    db.close()


def test_pipeline_yield_spend_and_full_offline_rerun_contract(env):
    first = _run(env)
    assert first["ai_yield"]["unique_new_public_deals"] == 2
    assert first["ai_yield"]["useful_price_updates"] == 0
    assert first["ai_yield"]["spend_usd"] == pytest.approx(first["spend_usd"])
    assert first["ai_yield"]["new_deals_per_usd"] == pytest.approx(2 / first["spend_usd"])
    assert first["spend_by_kind"]["critic_shadow"]["usd"] > 0
    db = DB(env["db"])
    before = db.conn.execute("SELECT COUNT(*) FROM ai_yield_events").fetchone()[0]
    db.close()
    second = _run(env)
    assert second["ai_yield"]["unique_new_public_deals"] == second["ai_yield"]["useful_price_updates"] == 0
    assert second["ai_yield"]["new_deals_per_usd"] is None
    db = DB(env["db"])
    assert db.conn.execute("SELECT COUNT(*) FROM ai_yield_events").fetchone()[0] == before
    db.close()


def test_zero_spend_unknown_models_uncertain_cost_and_shared_run_key(tmp_path, monkeypatch):
    db = DB(tmp_path / "state.db")
    cfg = _cfg()
    cfg.spend_pacing = False
    midnight = NOW.replace(hour=0)
    ai_yield.start(db, cfg, midnight, "shared")
    key = ingest(db, cfg, "Widget", 20, NOW)
    ai_yield.record_public(db, cfg, NOW, "shared", [key])
    assert ai_yield.aggregate(db, cfg, midnight, midnight + timedelta(days=1))["new_deals_per_usd"] is None
    monkeypatch.setattr("dealradar.util.now_utc", lambda: NOW)
    parser = make_parser(FakeMessagesWithParse(), cfg=cfg)
    parser.bind_ledger(db, "shared")
    for kind, model in (("extract", "unknown-model"), ("category", cfg.parse_model), ("critic", cfg.critic_model),
                        ("critic_shadow", cfg.critic_shadow_model)):
        kwargs = dict(model=model, max_tokens=100, system="prefix", messages=[])
        reservation = parser._reserve(kwargs)
        parser._settle(reservation, kwargs, SimpleNamespace(usage=FakeUsage(100, 20)), kind=kind)
    # Additional execution with the same run key must add real spend, no new supply.
    kwargs = dict(model="unknown-model", max_tokens=100, system="prefix", messages=[])
    reservation = parser._reserve(kwargs)
    parser._settle(reservation, kwargs, kind="extract")
    db.start_run("shared", "fast", midnight)
    db.finish_run("shared", now=NOW, items_fetched=0, posts_new=0, deals_parsed=0, published=1,
                  spend_usd=parser.spend_usd, accounted_spend_usd=parser.ledger_spend_usd)
    db.start_run("shared", "fast", NOW)
    db.finish_run("shared", now=NOW, items_fetched=0, posts_new=0, deals_parsed=0, published=1, spend_usd=.02)
    sample = ai_yield.aggregate(db, cfg, midnight, midnight + timedelta(days=1))
    assert sample["unique_new_public_deals"] == 1
    assert sample["spend_usd"] == pytest.approx(parser.spend_usd + .02)
    assert db.spend_usd_for_run("shared") == pytest.approx(sample["spend_usd"])
    assert sample["new_deals_per_usd"] == pytest.approx(1 / sample["spend_usd"])
    db.close()


def test_utc_boundaries_and_read_only_old_snapshots(tmp_path):
    path = tmp_path / "state.db"
    db = DB(path)
    cfg = _cfg()
    midnight = NOW.replace(hour=0)
    ai_yield.start(db, cfg, midnight - timedelta(days=8), "first")
    for offset in (-1, 0):
        at = midnight + timedelta(seconds=offset)
        key = ingest(db, cfg, f"Widget {offset}", 20, at)
        ai_yield.record_public(db, cfg, at, "run", [key])
    db.close()
    before = path.read_bytes()
    data = report._ai_yield(path, cfg, midnight.astimezone(timezone(timedelta(hours=-5))))
    assert data["yesterday"]["unique_new_public_deals"] == data["week"]["unique_new_public_deals"] == 1
    assert path.read_bytes() == before
    old = tmp_path / "old.db"
    with sqlite3.connect(old) as conn:
        conn.execute("CREATE TABLE meta(k TEXT PRIMARY KEY,v TEXT)")
    before = old.read_bytes()
    assert "unavailable" in report._ai_yield(old, cfg, NOW)
    assert old.read_bytes() == before


def test_bounded_observations_keep_live_identity_dedupe_and_miss_memory(tmp_path):
    db = DB(tmp_path / "state.db")
    cfg = _cfg()
    ai_yield.start(db, cfg, NOW, "run")
    key = ingest(db, cfg, "Widget", 20, NOW)
    ai_yield.record_public(db, cfg, NOW, "run", [key])
    post = make_post(text="Widget was $100 now $90")
    ai_yield.observe_prefilter(db, cfg, [post], {post.content_hash: {
        "prefilter_would_skip": True, "evaluation_complete": True, "prefilter_miss": True}}, NOW)
    db.prune(NOW + timedelta(days=61), run_stats_days=60)
    for table in ("ai_yield_events", "ai_yield_runs", "ai_prefilter_posts"):
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert ai_yield.record_public(db, cfg, NOW + timedelta(days=61), "again", [key])["unique_new_public_deals"] == 0
    assert db.meta_get("ai_yield_missed_policy") == ai_yield.policy_key(cfg)
    old = ai_yield.aggregate(db, cfg, NOW, NOW + timedelta(days=1))
    assert "unavailable" in old
    db.close()


def complete_sample(db, cfg, *, candidates=210, sites=3):
    end = NOW.replace(hour=0)
    start = end - timedelta(days=7)
    ai_yield.start(db, cfg, start, "start")
    ai_yield.finish(db, cfg, start, "start", complete=True)
    for hour in range(7 * 24):
        stamp = start + timedelta(hours=hour)
        ai_yield.start(db, cfg, stamp, f"hour{hour}")
        ai_yield.finish(db, cfg, stamp, f"hour{hour}", complete=True)
    posts = [make_post(source_name=f"feed{i % sites}", url=f"https://site{i % sites}.example/post/{i}",
                       text=f"Widget {i} was $100 now $90") for i in range(candidates)]
    ai_yield.observe_prefilter(db, cfg, posts, {p.content_hash: {"prefilter_would_skip": True,
        "evaluation_complete": True} for p in posts}, start)
    db.meta_set("ai_yield_replay_policy", ai_yield.policy_key(cfg))
    return start, end


@pytest.mark.parametrize("change", ["small", "two_sites", "miss", "unevaluated", "coverage_gap",
    "failed_run", "pending_run", "partial_start", "policy_changed", "replay_missing", "mode_on", "none"])
def test_readiness_requires_all_rollout_evidence(tmp_path, change):
    db = DB(tmp_path / "state.db")
    cfg = _cfg()
    start, end = complete_sample(db, cfg, candidates=199 if change == "small" else 210,
                                  sites=2 if change == "two_sites" else 3)
    if change in ("miss", "unevaluated"):
        db.conn.execute("UPDATE ai_prefilter_posts SET miss=1" if change == "miss" else
                        "UPDATE ai_prefilter_posts SET evaluated=0,gap='critic'")
    elif change == "coverage_gap":
        db.conn.execute("DELETE FROM ai_yield_runs WHERE run_key IN ('hour50','hour51')")
    elif change == "failed_run":
        ai_yield.finish(db, cfg, start + timedelta(hours=50), "hour50", complete=False)
    elif change == "pending_run":
        ai_yield.start(db, cfg, start + timedelta(hours=50), "hour50")
    elif change == "partial_start":
        db.meta_set("ai_yield_since", iso(start + timedelta(minutes=1)))
    elif change == "policy_changed":
        cfg.site_show_from_pct = 30
    elif change == "replay_missing":
        db.meta_set("ai_yield_replay_policy", "")
    elif change == "mode_on":
        cfg.prefilter_below_floor = "on"
    result = ai_yield.readiness(db, cfg, end)
    assert result["ready_for_review"] == (change == "none")
    assert bool(result["reasons"]) == (change != "none")
    assert cfg.prefilter_below_floor == ("on" if change == "mode_on" else "shadow")
    db.close()


def test_feed_copies_do_not_meet_the_readiness_sample_and_same_site_is_not_independent(tmp_path):
    db = DB(tmp_path / "state.db")
    cfg = _cfg()
    start, end = complete_sample(db, cfg)
    db.conn.execute("UPDATE ai_prefilter_posts SET text_fingerprint='same-text'")
    result = ai_yield.readiness(db, cfg, end)
    assert result["fully_evaluated_candidates"] == 3
    assert any("insufficient evidence" in reason for reason in result["reasons"])
    db.conn.execute("UPDATE ai_prefilter_posts SET site='one-site.example',text_fingerprint=content_hash")
    result = ai_yield.readiness(db, cfg, end)
    assert result["independent_source_sites"] == 1 and not result["ready_for_review"]
    db.close()


def test_failed_then_successful_shared_run_does_not_erase_the_gap(tmp_path):
    db = DB(tmp_path / "state.db")
    cfg = _cfg()
    start, end = complete_sample(db, cfg)
    ai_yield.finish(db, cfg, start, "hour0", complete=False)
    ai_yield.start(db, cfg, start + timedelta(minutes=10), "hour0")
    ai_yield.finish(db, cfg, start + timedelta(minutes=10), "hour0", complete=True)
    assert not ai_yield.readiness(db, cfg, end)["ready_for_review"]
    assert db.conn.execute("SELECT failed FROM ai_yield_runs WHERE run_key='hour0'").fetchone()[0] == 1
    db.close()


def test_growth_report_private_metrics_and_unavailable_history(tmp_path, monkeypatch, caplog):
    db = DB(tmp_path / "state.db")
    cfg = _cfg()
    start, end = complete_sample(db, cfg)
    key = ingest(db, cfg, "Widget", 20, end - timedelta(seconds=1))
    ai_yield.record_public(db, cfg, end - timedelta(seconds=1), "last", [key])
    db.close()
    monkeypatch.setattr(report, "get_secret", lambda name: "")
    caplog.set_level("INFO", logger="dealradar.growth")
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    data, status = report.collect(cfg, end, client, db_path=tmp_path / "state.db")
    client.close()
    assert status["ai_yield"] == "ok"
    _, text = report.build_report(cfg, data, status, end.date())
    assert "AI yield (UTC" in text and "new public deals 1" in text
    assert "useful price updates 0" in text and "new deals/USD n/a" in text
    assert "0 unevaluated" in text and "ready for manual review" in text
    assert "new public deals" not in "\n".join(caplog.messages)
    _, old_text = report.build_report(cfg, {"ai_yield": {"unavailable": "history unavailable"}}, {}, end.date())
    missing = next(line for line in old_text.splitlines() if line.startswith("not measured yet:"))
    assert "AI yield" in missing and "AI yield: n/a" not in old_text
