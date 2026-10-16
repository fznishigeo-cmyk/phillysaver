"""Task 106: durable routing/caps, model-priced receipts and offline replay."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
from types import SimpleNamespace

import pytest

from dealradar import ai_usage, critic_tiering, pipeline, rank
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.errors import SpendCapReached
from dealradar.growth import report
from dealradar.parse.claude_client import ClaudeParser, critique_deals
from dealradar.parse.extractor import parse_posts
from dealradar.parse.schema import CriticVerdicts
from tests.test_ai_spend import Messages, deal, insert, parser_for
from tests.test_e2e_offline import NOW, env, _run
from tests.test_extractor import good_deal, make_post

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"


class TierMessages(Messages):
    def __init__(self, *, sonnet_kills=False, **kwargs):
        super().__init__(**kwargs)
        self.sonnet_kills = sonnet_kills

    def parse(self, **kw):
        response = super().parse(**kw)
        entries = json.loads(kw["messages"][0]["content"])
        for entry, verdict in zip(entries, response.parsed_output["verdicts"]):
            if kw["model"] == HAIKU:
                verdict["plausible"] = "kill" not in entry["item"]
                if "low" in entry["item"]:
                    verdict["confidence"] = .5
                if "nan" in entry["item"]:
                    verdict["confidence"] = float("nan")
                if "invalid" in entry["item"]:
                    verdict["index"] = 999
            else:
                verdict["plausible"] = not self.sonnet_kills
        return response


def tier_parser(messages=None, **options):
    parser, messages = parser_for(messages or TierMessages())
    parser.cfg.critic_tiering.update(enabled=True, shadow_batches_per_day=0, **options)
    # Routing unit tests separately exercise projection with real rank knobs.
    parser.cfg.keepa_recheck_top_n = parser.cfg.keepa_recheck_wall_n = 0
    return parser, messages


def add(db, key, **kwargs):
    row = deal(key, asin="", store="Walmart", canonical_url=f"https://example.com/{key}",
               item=key, **kwargs)
    db.insert_deal(dedupe_key=key, store=row["store"], item=key, normalized_item=key,
                   canonical_url=row["canonical_url"], payload=row, final_price=row["final_price"],
                   pct_at_checkout=row["pct_at_checkout"], abs_savings=row["abs_savings"],
                   online_only="true", expires_at="", now=NOW)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr("dealradar.util.now_utc", lambda: NOW)


def test_routing_kill_low_invalid_nan_price_and_plain_approve(tmp_path):
    db = DB(tmp_path / "state.db")
    parser, messages = tier_parser()
    for key in ("plain", "kill", "low", "invalid", "nan", "expensive"):
        add(db, key, final_price=251 if key == "expensive" else 25)
    parser.bind_ledger(db, "route")
    stats = pipeline._run_critic(db, parser.cfg, parser, NOW)
    assert [c["model"] for c in messages.calls] == [HAIKU, SONNET]
    assert [len(json.loads(c["messages"][0]["content"])) for c in messages.calls] == [6, 5]
    assert stats["tiering"]["haiku_approved"] == 1
    assert stats["tiering"]["escalated"] == 5
    for key in ("plain", "kill", "low", "invalid", "nan", "expensive"):
        row = db.get_deal(key)
        assert row["critic"]["model"] == (HAIKU if key == "plain" else SONNET)
        assert row["critic"]["plausible"] and "suspect_msrp" not in row["flags"]
        assert rank.is_publishable(row, parser.cfg)[0]
    assert messages.calls[0]["system"] == messages.calls[1]["system"]
    assert messages.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    usage = ai_usage.daily(db, NOW)
    assert usage["by_kind"]["critic"]["models"] == {HAIKU: .00015, SONNET: .003}
    assert usage["by_kind"]["critic"]["input_tokens"] == 2000
    assert usage["usd"] == pytest.approx(parser.spend_usd)
    before = parser.spend_usd
    assert pipeline._run_critic(db, parser.cfg, parser, NOW)["judged"] == 0
    assert parser.spend_usd == before
    db.close()


def test_projected_wall_and_top_candidates_use_existing_rank(tmp_path):
    db = DB(tmp_path / "state.db")
    parser, messages = tier_parser()
    cfg = parser.cfg
    cfg.keepa_recheck_wall_n = cfg.keepa_recheck_top_n = 1
    add(db, "wall", pct_at_checkout=90)
    add(db, "pick", image_url="https://images.example/pick.jpg", pct_at_checkout=75)
    add(db, "ordinary", pct_at_checkout=50)
    keys = critic_tiering.high_visibility(db, cfg, NOW, db.all_deals())
    assert "pick" in keys
    assert "ordinary" not in keys
    # A prior published wall is protected while the prospective wall changes.
    db.meta_set("published_wall", json.dumps(["wall"]))
    pipeline._run_critic(db, cfg, parser, NOW)
    assert db.get_deal("wall")["critic"]["model"] == SONNET
    assert db.get_deal("pick")["critic"]["model"] == SONNET
    assert db.get_deal("ordinary")["critic"]["model"] == HAIKU
    # Becoming high visibility invalidates a cached Haiku verdict.
    db.meta_set("published_wall", json.dumps(["ordinary"]))
    pipeline._run_critic(db, cfg, parser, NOW)
    assert db.get_deal("ordinary")["critic"]["model"] == SONNET
    db.close()


def test_escalations_pack_across_batches_and_kills_need_sonnet(tmp_path):
    db = DB(tmp_path / "state.db")
    parser, messages = tier_parser(TierMessages(sonnet_kills=True))
    parser.cfg.critic_batch_size = 4
    for i in range(12):
        add(db, f"{'kill' if i % 4 == 0 else 'plain'}-{i}")
    pipeline._run_critic(db, parser.cfg, parser, NOW)
    assert [c["model"] for c in messages.calls] == [HAIKU] * 3 + [SONNET]
    assert len(json.loads(messages.calls[-1]["messages"][0]["content"])) == 3
    assert sum("suspect_msrp" in d["flags"] for d in db.all_deals()) == 3
    db.close()


def test_cap_during_escalation_leaves_every_source_unpublished(tmp_path):
    db = DB(tmp_path / "state.db")
    parser, messages = tier_parser()
    parser.run_cap_usd = .005
    add(db, "kill")
    add(db, "plain")
    pipeline._run_critic(db, parser.cfg, parser, NOW)
    assert [c["model"] for c in messages.calls] == [HAIKU]
    assert db.get_deal("kill").get("critic") is None
    assert not rank.is_publishable(db.get_deal("kill"), parser.cfg)[0]
    assert rank.is_publishable(db.get_deal("plain"), parser.cfg)[0]
    db.close()


def test_sonnet_shadow_daily_cap_fallback_withdraws_prior_cheap_verdicts(tmp_path, caplog):
    db = DB(tmp_path / "state.db")
    parser, messages = tier_parser(TierMessages(sonnet_kills=True),
                                  window_deals=4, min_judged_deals=4)
    parser.cfg.critic_tiering["shadow_batches_per_day"] = 1
    parser.cfg.critic_batch_size = 4
    add(db, "prior", critic=dict(plausible=True, confidence=.9, reason="OK", model=HAIKU))
    for i in range(12):
        add(db, str(i))
    result = pipeline._run_critic(db, parser.cfg, parser, NOW)
    assert len([c for c in messages.calls if c["model"] == SONNET]) == 1
    assert result["tiering"]["sample_disagreed"] == 4
    assert "4/4" in db.meta_get(critic_tiering.FALLBACK)
    assert "Sonnet-for-all" in caplog.text
    assert all(not rank.is_publishable(d, parser.cfg)[0] for d in db.all_deals())
    # A fresh process sees the fallback and never makes a new Haiku critic call.
    next_parser, next_messages = tier_parser(TierMessages(sonnet_kills=False))
    pipeline._run_critic(db, next_parser.cfg, next_parser, NOW)
    assert next_messages.calls and all(c["model"] == SONNET for c in next_messages.calls)
    assert db.get_deal("prior")["critic"]["model"] == SONNET
    assert db.conn.execute("SELECT COUNT(*) FROM critic_tier_batches").fetchone()[0] == 1
    db.close()


def test_rolling_window_threshold_and_missing_samples(tmp_path):
    db = DB(tmp_path / "state.db")
    parser, _ = tier_parser(window_deals=10, min_judged_deals=10, max_disagreement=.1)
    for i in range(20):
        db.conn.execute("INSERT INTO critic_tier_samples VALUES (?,?,?)",
                        (str(i), NOW.isoformat(), int(i not in {0, 1, 19})))
    db.conn.commit()
    assert critic_tiering.fallback_reason(db, parser.cfg) == ""  # 1/10 equals limit
    db.conn.execute("UPDATE critic_tier_samples SET agrees=0 WHERE event_key='18'")
    db.conn.commit()
    assert "2/10" in critic_tiering.fallback_reason(db, parser.cfg)
    db.close()


class ExtractionMessages:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def create(self, **kw):
        self.calls.append(kw.copy())
        raw = self.responses.pop(0) if self.responses else '{"deals": []}'
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=raw)], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=100, output_tokens=40,
                                   cache_creation_input_tokens=200, cache_read_input_tokens=300))


def seed_yield(db, source, *, success=3, calls=3):
    for i in range(calls):
        db.reserve_llm_call(f"{source}-{i}", "history", HAIKU, .001, NOW - timedelta(days=1),
                            {(NOW - timedelta(days=1)).date().isoformat(): 6}, .5,
                            kind="second_opinion", source_name=source)
        db.settle_llm_call(f"{source}-{i}", .001, NOW - timedelta(days=1))
        db.conn.execute("INSERT INTO second_opinion_yield VALUES (?,?,?,?,?)",
                        (f"{source}-{i}", (NOW - timedelta(days=1)).isoformat(), source, 1, int(i < success)))
        db.conn.commit()


def test_second_opinion_yield_routing_daily_cap_reasks_and_prompt_unchanged(tmp_path):
    db = DB(tmp_path / "state.db")
    messages = ExtractionMessages(['bad json', '{"deals": []}'])
    parser, _ = tier_parser(messages, second_opinion_calls_per_day=1)
    parser.bind_ledger(db, "second")
    seed_yield(db, "good")
    seed_yield(db, "poor", success=0)
    for source in ("good", "poor", "new", "good"):
        parser.extract("Synthetic deal", {"source_name": source}, model=SONNET, kind="second_opinion")
    assert [c["model"] for c in messages.calls] == [SONNET, HAIKU, HAIKU, HAIKU, HAIKU]
    assert all(c["system"] == messages.calls[0]["system"] for c in messages.calls)
    usage = ai_usage.daily(db, NOW)
    row = usage["by_kind"]["second_opinion"]
    assert row["calls"] == 5 and row["cache_read_tokens"] == 1500
    assert row["cache_write_tokens"] == 1000
    assert row["usd"] == pytest.approx(.00113 + 4 * .000058)
    assert row["models"] == {SONNET: pytest.approx(.00113), HAIKU: pytest.approx(4 * .000058)}
    sources = ai_usage.second_opinion_sources(db.conn, NOW, 7)
    assert sources["good"]["calls"] == 6 and sources["poor"]["yield"] == 0
    db.close()


def test_second_opinion_cap_atomic_across_parsers_and_midnight(tmp_path, monkeypatch):
    db = DB(tmp_path / "state.db")
    seed_yield(db, "good")
    parsers = []
    for i in range(4):
        parser, messages = tier_parser(ExtractionMessages(), second_opinion_calls_per_day=1)
        parser.bind_ledger(db, str(i))
        parsers.append((parser, messages))
    def read(pair):
        pair[0].extract("deal", {"source_name": "good"}, model=SONNET, kind="second_opinion")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(read, parsers))
    assert sum(c["model"] == SONNET for _, m in parsers for c in m.calls) == 1
    monkeypatch.setattr("dealradar.util.now_utc", lambda: NOW + timedelta(days=1))
    read(parsers[0])
    assert parsers[0][1].calls[-1]["model"] == SONNET
    db.close()


def test_second_opinion_evidence_yield_is_deduped_and_can_qualify_source(tmp_path):
    db = DB(tmp_path / "state.db")
    result = good_deal().model_dump(mode="json")
    messages = ExtractionMessages(['{"deals": []}', json.dumps({"deals": [result]})])
    parser, _ = tier_parser(messages, second_opinion_min_calls=1)
    parser.bind_ledger(db, "posts")
    post = make_post(text="Widget was $20.00 now $10.00 online only")
    pairs, stats = parse_posts([post], parser, parser.cfg, NOW, {}, db=db)
    assert stats["second_opinion"]["calls"] == 1
    assert pairs and stats["second_opinion"]["deals_passed_evidence"] == 1
    before = ai_usage.second_opinion_sources(db.conn, NOW)
    parse_posts([post], parser, parser.cfg, NOW, {}, db=db)
    assert ai_usage.second_opinion_sources(db.conn, NOW) == before
    assert before[post.source_name]["yield"] == 1
    parser.extract("new post", {"source_name": post.source_name}, model=SONNET, kind="second_opinion")
    assert messages.calls[-1]["model"] == SONNET
    db.close()


def test_daily_usage_midnight_legacy_uncertain_and_private_report(tmp_path):
    db = DB(tmp_path / "state.db")
    usage = SimpleNamespace(input_tokens=10, output_tokens=20,
                            cache_read_input_tokens=30, cache_creation_input_tokens=40)
    yesterday = NOW - timedelta(days=1)
    db.reserve_llm_call("midnight", "old", SONNET, .1, yesterday,
                        {yesterday.date().isoformat(): 6, NOW.date().isoformat(): 6}, .5, kind="critic")
    db.settle_llm_call("midnight", .002, NOW, usage=usage)
    db.reserve_llm_call("lost", "old", HAIKU, .02, yesterday,
                        {yesterday.date().isoformat(): 6, NOW.date().isoformat(): 6}, .5, kind="category")
    db.settle_llm_call("lost", .02, NOW, uncertain=True)
    db.start_run("legacy", "hourly", NOW)
    db.finish_run("legacy", now=NOW, items_fetched=0, posts_new=0,
                  deals_parsed=0, published=0, spend_usd=.03)
    today = ai_usage.daily(db, NOW.astimezone(timezone(timedelta(hours=-5))))
    assert today["usd"] == pytest.approx(db.spend_usd_for_day(NOW))
    assert today["legacy_usd"] == .03
    assert today["by_kind"]["category"]["unknown_usage_calls"] == 1
    assert ai_usage.daily(db, yesterday)["usd"] == .02
    cfg = load_config()
    _, text = report.build_report(cfg, {"ai_usage": report._ai_usage(db.path, NOW)}, {}, NOW.date())
    assert "AI usage" in text.split(report.TECHNICAL_HEADING)[1]
    assert "tokens unavailable" in text and "usage missing for 1" in text
    db.close()


def test_switch_off_matches_prechange_spend_verdicts_and_every_site_byte(env):
    from pathlib import Path
    import yaml

    path = Path(env["cfg"])  # this byte pin predates the RSS trim
    path.write_text(path.read_text().replace("feed_max_items: 20", "feed_max_items: 50")
                    .replace("feed_details: false", "feed_details: true"))
    # This byte pin predates the durable-pages SEO switch.
    path.write_text(path.read_text().replace("deal_pages_indexable: false", "deal_pages_indexable: true"))
    config = yaml.safe_load(path.read_text())
    config["amazon_history"] = "show"  # predates task 121b
    config["publish"]["front_page"]["appeal"]["enabled"] = False  # predates the broad-appeal switch
    config["publish"]["front_page"]["checkout_price_deals"]["enabled"] = False  # predates the checkout-price switch
    config["publish"]["amazon_as_of"]["enabled"] = False  # predates the Amazon as-of switch
    config["publish"]["photoless_last"] = False  # predates photos-first
    config["seo"]["product_pages"]["enabled"] = False  # predates product pages
    config["seo"]["event_pages"]["enabled"] = False  # predates event pages
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    summary = _run(env)
    assert summary["spend_usd"] == pytest.approx(.00729)
    assert summary["spend_by_kind"]["critic"] == {"calls": 1, "usd": .0054}
    db = DB(env["db"])
    assert all("model" not in (d.get("critic") or {}) for d in db.all_deals())
    db.close()
    files = {str(p.relative_to(env["site"])): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(env["site"].rglob("*")) if p.is_file()}
    # Captured from untouched HEAD with this same offline fixture before task 106.
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    assert digest == "794cbc804e6af093607b183143f6d5d29f5b88e38340f84beaf571d045e7d5f6"
    assert _run(env)["spend_usd"] == 0


class DayReplayMessages(TierMessages):
    """Synthetic usage, never a claim about captured provider invoices.

    Extraction 5,000 input/4,000 output reproduces the brief's $0.025/10
    Haiku posts. Critic/category receipts reuse the existing e2e 1,200/300
    fixture. No cache hits are assumed in the full-day estimate.
    """
    def __init__(self):
        super().__init__(cost=(1200, 300))

    def create(self, **kw):
        if isinstance(kw["system"], str):
            return super().create(**kw)
        self.calls.append(kw.copy())
        return SimpleNamespace(content=[SimpleNamespace(type="text", text='{"deals": []}')],
            stop_reason="end_turn", usage=SimpleNamespace(input_tokens=5000, output_tokens=4000))


def day_replay(path, monkeypatch, *, enabled, all_escalate=False):
    from dealradar.parse.claude_client import categorize_titles

    db = DB(path)
    seed_yield(db, "good", success=30, calls=30)
    cfg = load_config()
    # Baseline measures the entire demand under the existing $10 boost.
    # ON is tested under the normal $6 ceiling, without the temporary boost.
    if enabled:
        cfg.spend_cap_boost_usd_per_day = 0
    cfg.critic_tiering["enabled"] = enabled
    # Historical successful evidence keeps the source eligible all day, so
    # the daily cap (not low-yield routing) supplies this conservative bound.
    for run in range(82):  # documented 58 fast + 24 hourly runs/day
        at = NOW.replace(hour=0) + timedelta(minutes=run * 1440 / 82)
        monkeypatch.setattr("dealradar.util.now_utc", lambda: at)
        parser = ClaudeParser(cfg, client=SimpleNamespace(messages=DayReplayMessages()))
        parser.bind_ledger(db, f"replay-{run}")
        if run % 3 == 0 and run < 72:  # 24 hourly batches of ten posts
            for i in range(10):
                parser.extract(f"Synthetic post {run}-{i}", {"source_name": "partner"})
        parser.extract(f"Missed post {run}", {"source_name": "good"}, model=SONNET, kind="second_opinion")
        categorize_titles(parser, [f"Title {run}"])
        rows = [deal(f"{run}-{i}", item=f"Replay widget {i}") for i in range(32)]
        if enabled:
            high = {d["dedupe_key"] for d in (rows if all_escalate else rows[:8])}
            critic_tiering.run(db, cfg, parser, at, rows, high, lambda *_args: None)
        else:
            for start in range(0, 32, cfg.critic_batch_size):
                batch = rows[start:start + cfg.critic_batch_size]
                critique_deals(parser, batch)
                if run < cfg.critic_shadow_batches_per_day and start == 0:
                    critique_deals(parser, batch, shadow=True, reserve_shadow=lambda: True)
        assert parser.spend_usd < cfg.spend_cap_usd_per_run
    result = ai_usage.daily(db, NOW.replace(hour=23, minute=59))
    assert result["usd"] == pytest.approx(db.spend_usd_for_day(NOW))
    db.close()
    return result


@pytest.mark.parametrize("all_escalate", [False, True])
def test_full_day_replay_cost_and_model_split(tmp_path, monkeypatch, all_escalate):
    before = day_replay(tmp_path / "before.db", monkeypatch, enabled=False)
    after = day_replay(tmp_path / "after.db", monkeypatch, enabled=True, all_escalate=all_escalate)
    assert before["usd"] == pytest.approx(6.49658)
    assert before["by_kind"]["second_opinion"]["calls"] == 82
    assert after["by_kind"]["second_opinion"]["models"][SONNET] == pytest.approx(.6)
    expected = 3.2569 if all_escalate else 1.9393
    assert after["usd"] == pytest.approx(expected)
    assert after["usd"] <= 4.50
    # Save the compact measured replay split for the handoff arithmetic.
    (tmp_path / "measured.json").write_text(json.dumps({"before": before, "after": after}, indent=2))


def test_tiered_pipeline_rerun_is_idempotent(env):
    from pathlib import Path

    path = Path(env["cfg"])
    path.write_text(path.read_text().replace("critic_tiering:\n    enabled: false", "critic_tiering:\n    enabled: true"))
    first = _run(env)
    assert first["critic"]["tiering"]["escalated"] > 0
    db = DB(env["db"])
    rows = db.conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    verdicts = {d["dedupe_key"]: d.get("critic") for d in db.all_deals()}
    second = _run(env)
    assert second["spend_usd"] == second["posts_new"] == 0
    assert rows == db.conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    assert verdicts == {d["dedupe_key"]: d.get("critic") for d in db.all_deals()}
    db.close()


@pytest.mark.parametrize("which", ["run", "day"])
def test_tiering_never_bypasses_existing_money_reservations(tmp_path, which):
    db = DB(tmp_path / "state.db")
    parser, messages = tier_parser()
    parser.cfg.spend_pacing = False
    parser.cfg.spend_cap_boost_usd_per_day = 0
    parser.bind_ledger(db, "current")
    for i in range(12 if which == "day" else 1):
        db.reserve_llm_call(str(i), "current" if which == "run" else f"old-{i}", SONNET,
                            .5, NOW, {NOW.date().isoformat(): 6}, .5)
    add(db, "plain")
    pipeline._run_critic(db, parser.cfg, parser, NOW)
    assert messages.calls == []
    assert db.get_deal("plain").get("critic") is None
    assert not rank.is_publishable(db.get_deal("plain"), parser.cfg)[0]
    db.close()


def test_exhausted_second_opinion_quota_fits_remaining_haiku_allowance(tmp_path):
    db = DB(tmp_path / "state.db")
    seed_yield(db, "good")
    parser, messages = tier_parser(ExtractionMessages(), second_opinion_calls_per_day=0)
    parser.bind_ledger(db, "tiny")
    parser.run_cap_usd = .01  # fits Haiku's worst-case extraction, not Sonnet's
    parser.extract("a deal", {"source_name": "good"}, model=SONNET, kind="second_opinion")
    assert [c["model"] for c in messages.calls] == [HAIKU]
    db.close()


def test_invalid_shadow_is_not_agreement_and_daily_claim_survives_restart(tmp_path, monkeypatch):
    class InvalidSample(TierMessages):
        def parse(self, **kw):
            response = super().parse(**kw)
            if kw["model"] == SONNET:
                response.parsed_output = {"verdicts": []}
            return response

    db = DB(tmp_path / "state.db")
    for run in range(3):
        at = NOW + timedelta(days=int(run == 2))
        monkeypatch.setattr("dealradar.util.now_utc", lambda: at)
        parser, messages = tier_parser(InvalidSample())
        parser.cfg.critic_tiering["shadow_batches_per_day"] = 1
        add(db, f"plain-{run}")
        pipeline._run_critic(db, parser.cfg, parser, at)
        assert sum(c["model"] == SONNET for c in messages.calls) == int(run != 1)
    assert db.conn.execute("SELECT COUNT(*) FROM critic_tier_samples").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM critic_tier_batches").fetchone()[0] == 2
    assert db.meta_get(critic_tiering.FALLBACK) is None
    db.close()


def test_read_only_legacy_usage_marks_missing_detail_without_migrating(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE run_stats (started_at TEXT, spend_usd REAL)")
        conn.execute("INSERT INTO run_stats VALUES (?,?)", (NOW.isoformat(), .123))
    db = DB(path, read_only=True)
    usage = ai_usage.daily(db, NOW)
    assert usage["usd"] == usage["legacy_usd"] == .123
    assert usage["by_kind"] == {}
    assert "calls/tokens unavailable" in ai_usage.lines(usage)[0]
    assert db.conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='llm_calls'").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize("cap_after_leader", [False, True])
def test_rejected_leader_cannot_promote_a_haiku_only_wall_candidate(tmp_path, cap_after_leader):
    class RejectLeader(TierMessages):
        def parse(self, **kw):
            response = super().parse(**kw)
            entries = json.loads(kw["messages"][0]["content"])
            if kw["model"] == SONNET:
                for entry, verdict in zip(entries, response.parsed_output["verdicts"]):
                    verdict["plausible"] = entry["item"] != "leader"
                if cap_after_leader:
                    parser.run_cap_usd = 0
            return response

    db = DB(tmp_path / "state.db")
    parser, messages = tier_parser(RejectLeader())
    parser.cfg.keepa_recheck_wall_n = 1
    add(db, "leader", pct_at_checkout=90)
    add(db, "next", pct_at_checkout=80)
    add(db, "ordinary", pct_at_checkout=70)
    pipeline._run_critic(db, parser.cfg, parser, NOW)
    assert "suspect_msrp" in db.get_deal("leader")["flags"]
    if cap_after_leader:
        assert all(not rank.is_publishable(d, parser.cfg)[0] for d in db.all_deals())
    else:
        assert db.get_deal("next")["critic"]["model"] == SONNET
        assert db.get_deal("ordinary")["critic"]["model"] == HAIKU
        assert rank.is_publishable(db.get_deal("ordinary"), parser.cfg)[0]
    db.close()
