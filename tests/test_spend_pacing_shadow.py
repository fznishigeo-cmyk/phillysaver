"""Paced intake and a strictly observational, budgeted critic experiment."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dealradar import pipeline
from dealradar.config import AppConfig, daily_spend_cap, load_config, spend_allowance
from dealradar.db import DB
from dealradar.growth import report
from tests.test_ai_spend import Messages, deal, insert, parser_for
from tests.test_e2e_offline import env, _run, _FakeMessages, keep_source_posts as e2e_keep_source_posts

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def historic_boost(cfg):
    """These pacing tests predate the 2026-10-14 $10 boost: pin the old one."""
    cfg.spend_cap_boost_usd_per_day, cfg.spend_cap_boost_until = 15.0, "2026-10-13"
    return cfg


@pytest.fixture(autouse=True)
def _historic_boost_in_copied_config(request):
    if "env" in request.fixturenames:
        path = Path(request.getfixturevalue("env")["cfg"])
        text = path.read_text()
        old = 'spend_cap_boost_usd_per_day: 10.00\n  spend_cap_boost_until: "2026-10-22"'
        assert old in text
        path.write_text(text.replace(
            old, 'spend_cap_boost_usd_per_day: 15.00\n  spend_cap_boost_until: "2026-10-13"'))


@pytest.mark.parametrize("hour,minute,fraction", [
    (0, 0, 2 / 24), (6, 0, 8 / 24), (12, 0, 14 / 24), (23, 59, 1),
])
@pytest.mark.parametrize("day,cap", [(12, 15), (14, 6)])
def test_pacing_math_and_hard_caps(hour, minute, fraction, day, cap):
    cfg = historic_boost(load_config())
    now = NOW.replace(day=day, hour=hour, minute=minute)
    for spent in (0, .25, cap * fraction, cap, cap + 1):
        allowance, allowed = spend_allowance(cfg, now, spent)
        assert daily_spend_cap(cfg, now) == cap
        assert allowed == pytest.approx(cap * fraction)
        assert allowance == pytest.approx(max(0, min(.5, cap * fraction - spent)))
        assert 0 <= allowance <= cfg.spend_cap_usd_per_run
        assert allowance <= max(0, cap - spent)


def test_pacing_uses_utc_and_switch_off_restores_old_allowance():
    cfg = historic_boost(load_config())
    now = NOW.astimezone(timezone(timedelta(hours=-5)))
    assert spend_allowance(cfg, now, .4) == pytest.approx((.1, .5))
    cfg.spend_pacing = False
    for spent in (0, .4, 5.9, 6, 7):
        assert spend_allowance(cfg, now, spent) == pytest.approx(
            (max(0, min(.5, 6 - spent)), 6))


def test_config_switches_and_headroom(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('parse:\n  spend_pacing: false\n  spend_pacing_headroom_hours: 4\n'
                    '  critic_shadow_model: ""\n  critic_shadow_batches_per_day: 0\n')
    cfg = load_config(path)
    assert not cfg.spend_pacing and cfg.spend_pacing_headroom_hours == 4
    assert cfg.critic_shadow_model == "" and cfg.critic_shadow_batches_per_day == 0
    cfg.spend_pacing = True
    cfg.spend_cap_usd_per_day = 6
    assert spend_allowance(cfg, NOW, 0)[1] == 1


def test_zero_paced_allowance_leaves_posts_for_next_run(env, monkeypatch, tmp_path, caplog):
    e2e_keep_source_posts(env)
    monkeypatch.setattr(pipeline.util, "now_utc", lambda: NOW)
    step = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step))
    caplog.set_level("INFO", logger="dealradar.pipeline")
    db = DB(env["db"])
    db.start_run("earlier", "fast", NOW)
    db.finish_run("earlier", now=NOW, items_fetched=0, posts_new=0,
                  deals_parsed=0, published=0, spend_usd=.5)
    first = _run(env)
    assert first["items_fetched"] == 5 and first["posts_new"] == 0
    assert first["parse"]["parser"] == "disabled" and first["spend_usd"] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM posts_seen").fetchone()[0] == 0
    line = "paced allowance $0.0000 (allowed $0.5000 of $6.00 by 00:00 UTC, spent $0.5000)"
    assert line in step.read_text() and line in caplog.messages
    monkeypatch.setattr(pipeline.util, "now_utc", lambda: NOW + timedelta(hours=6))
    second = _run(env)
    assert second["posts_new"] == 5 and second["published"] == 2
    assert second["spend_by_kind"]["critic_shadow"]["calls"] == 1
    assert db.spend_usd_for_day(NOW) == pytest.approx(.5 + second["spend_usd"])
    assert db.critic_shadow_counts(NOW)["batches"] == 1
    third = _run(env)
    assert third["posts_new"] == third["spend_usd"] == 0
    assert db.critic_shadow_counts(NOW)["batches"] == 1
    db.close()


class DisagreeingMessages(Messages):
    def parse(self, **kw):
        response = super().parse(**kw)
        shadow = "haiku" in kw["model"]
        for v in response.parsed_output["verdicts"]:
            v["plausible"] = (v["index"] % 2 == 0) != shadow
        return response


@pytest.mark.parametrize("limit", [0, 1, 2])
def test_shadow_limit_priority_same_prompt_and_model_pricing(tmp_path, limit):
    path = tmp_path / "state.db"
    db = DB(path)
    for i in range(6):
        insert(db, deal(str(i), asin=f"B00000000{i}"))
    parser, msgs = parser_for(DisagreeingMessages(), critic_batch_size=2,
                              critic_shadow_batches_per_day=limit)
    stats = pipeline._run_critic(db, parser.cfg, parser, NOW)
    assert stats["judged"] == 6 and stats["calls"] == 3
    assert [m["model"] for m in msgs.calls] == [parser.cfg.critic_model] * 3 + [
        parser.cfg.critic_shadow_model] * limit
    for real, shadow in zip(msgs.calls[:limit], msgs.calls[3:]):
        assert {k: v for k, v in real.items() if k != "model"} == {
            k: v for k, v in shadow.items() if k != "model"}
    assert parser.spend_by_kind["critic_shadow"] == {
        "calls": limit, "usd": pytest.approx(.00015 * limit)}
    assert parser.spend_usd == pytest.approx(.009 + .00015 * limit)
    db.close()
    db = DB(path)  # limits survive process restarts
    assert db.critic_shadow_counts(NOW) == dict(
        batches=limit, deals_judged=2 * limit, same_verdict=0,
        shadow_kept_critic_killed=limit, shadow_killed_critic_kept=limit)
    insert(db, deal("next", asin="B000000009"))
    parser, msgs = parser_for(critic_shadow_batches_per_day=limit)
    pipeline._run_critic(db, parser.cfg, parser, NOW)
    assert len(msgs.calls) == 1
    insert(db, deal("tomorrow", asin="B000000008"))
    pipeline._run_critic(db, parser.cfg, parser, NOW + timedelta(days=1))
    assert db.critic_shadow_counts(NOW + timedelta(days=1))["batches"] == min(1, limit)
    db.close()


@pytest.mark.parametrize("cap", [.003, .00301, .01, .02])
def test_shadow_skips_when_full_call_will_not_fit(tmp_path, cap):
    db = DB(tmp_path / "state.db")
    insert(db, deal())
    parser, msgs = parser_for(critic_shadow_batches_per_day=12)
    parser.run_cap_usd = cap
    # Small allowances cannot reserve even the real critic's full response.
    assert pipeline._run_critic(db, parser.cfg, parser, NOW)["judged"] == int(cap == .02)
    expected = int(cap == .02)
    assert parser.spend_by_kind["critic_shadow"]["calls"] == expected
    assert db.critic_shadow_counts(NOW)["batches"] == expected
    assert parser.spend_usd <= cap
    db.close()


@pytest.mark.parametrize("limit", [0, 1, 2])
def test_tiered_shadow_samples_sonnet_after_haiku_with_its_own_daily_cap(tmp_path, limit):
    import json

    db = DB(tmp_path / "state.db")
    for i in range(6):
        insert(db, deal(str(i), asin=f"B00000000{i}", item=f"Widget {i}"))
    cfg = load_config()
    parser, msgs = parser_for(critic_batch_size=2,
        critic_tiering=dict(cfg.critic_tiering, enabled=True, shadow_batches_per_day=limit),
        keepa_recheck_top_n=0, keepa_recheck_wall_n=0)
    stats = pipeline._run_critic(db, parser.cfg, parser, NOW)
    assert stats["judged"] == 6 + 2 * limit and stats["calls"] == 3
    assert stats["tiering"]["sample_judged"] == 2 * limit
    assert stats["tiering"]["sample_disagreed"] == 0
    assert [c["model"] for c in msgs.calls] == [cfg.critic_tiering["model"]] * 3 + [cfg.critic_model] * limit
    real_items = {e["item"] for c in msgs.calls[:3] for e in json.loads(c["messages"][0]["content"])}
    for sample in msgs.calls[3:]:
        assert sample["system"] == msgs.calls[0]["system"]
        assert {e["item"] for e in json.loads(sample["messages"][0]["content"])} <= real_items
    assert parser.spend_by_kind["critic_shadow"] == {"calls": limit, "usd": pytest.approx(.003 * limit)}
    assert parser.spend_usd == pytest.approx(.00045 + .003 * limit)
    assert db.conn.execute("SELECT COUNT(*) FROM critic_tier_batches").fetchone()[0] == limit
    assert db.critic_shadow_counts(NOW)["batches"] == 0  # legacy samples are independent
    assert pipeline._run_critic(db, parser.cfg, parser, NOW)["judged"] == 0
    assert len(msgs.calls) == 3 + limit
    db.close()


@pytest.mark.parametrize("model", ["", "claude-haiku-5-5"])
def test_shadow_disabled_or_invalid_reply_never_changes_real_verdict(tmp_path, model):
    class InvalidShadow(Messages):
        def parse(self, **kw):
            response = super().parse(**kw)
            if "haiku" in kw["model"]:
                response.parsed_output = {"verdicts": "invalid"}
            return response

    db = DB(tmp_path / "state.db")
    insert(db, deal())
    parser, msgs = parser_for(InvalidShadow(), critic_shadow_batches_per_day=1,
                              critic_shadow_model=model)
    pipeline._run_critic(db, parser.cfg, parser, NOW)
    assert db.get_deal("target")["critic"]["plausible"] is True
    assert db.critic_shadow_counts(NOW)["batches"] == int(bool(model))
    assert db.critic_shadow_counts(NOW)["deals_judged"] == 0
    assert parser.spend_by_kind["critic_shadow"]["usd"] == pytest.approx(.00015 if model else 0)
    db.close()


def test_opposite_shadow_never_changes_published_deals(env, monkeypatch, tmp_path):
    e2e_keep_source_posts(env)
    monkeypatch.setattr(pipeline.util, "now_utc", lambda: NOW)
    config = Path(env["cfg"])
    original = config.read_text()
    config.write_text(original.replace("critic_shadow_batches_per_day: 12",
                                       "critic_shadow_batches_per_day: 0"))
    baseline = _run(env)
    public = (env["site"] / "deals.json").read_bytes()
    db = DB(env["db"])
    deals = db.all_deals()
    db.close()
    real_parse = _FakeMessages.parse

    def opposite(self, **kw):
        result = real_parse(self, **kw)
        if kw["output_format"].__name__ == "CriticVerdicts" and "haiku" in kw["model"]:
            for verdict in result.parsed_output.verdicts:
                verdict.plausible = not verdict.plausible
        return result

    monkeypatch.setattr(_FakeMessages, "parse", opposite)
    config.write_text(original)
    env["db"] = str(tmp_path / "shadow.db")
    observed = _run(env)
    assert observed["published"] == baseline["published"] == 2
    assert observed["critic"] == baseline["critic"]
    assert (env["site"] / "deals.json").read_bytes() == public
    db = DB(env["db"])
    assert db.all_deals() == deals
    assert db.critic_shadow_counts(NOW) == dict(batches=1, deals_judged=3,
        same_verdict=0, shadow_kept_critic_killed=1, shadow_killed_critic_kept=2)
    db.close()


def test_growth_shadow_utc_window_and_missing_verdicts(tmp_path):
    path = tmp_path / "state.db"
    db = DB(path)
    yes, no = {"plausible": True}, {"plausible": False}
    for days in (0, 6, 7, -1):
        day = NOW - timedelta(days=days)
        assert db.start_critic_shadow_batch(day, 12)
        db.record_critic_shadow(day, [yes, no, yes, no, None], [yes, no, no, None, yes])
    db.close()
    before = path.read_bytes()
    data = report._critic_shadow(path, NOW.astimezone(timezone(timedelta(hours=-5))))
    assert path.read_bytes() == before  # report opens the snapshot read-only
    assert data["today"]["same_verdict"] == 2 and data["today"]["deals_judged"] == 3
    assert data["week"]["same_verdict"] == 4 and data["week"]["deals_judged"] == 6
    _, text = report.build_report(AppConfig(), {"critic_shadow": data}, {}, NOW.date())
    assert "critic shadow (Haiku vs Sonnet): agree 2/3 today, 7-day 4/6" in text
    assert text.index("== Money ==") < text.index("critic shadow")


def test_growth_collect_reads_snapshot_and_handles_pre_migration_db(tmp_path, monkeypatch):
    import httpx

    path = tmp_path / "state.db"
    db = DB(path)
    db.conn.execute("DROP TABLE critic_shadow_daily")
    db.conn.commit()
    db.close()
    monkeypatch.setattr(report, "get_secret", lambda name: "")
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as client:
        data, status = report.collect(AppConfig(), NOW, client, db_path=path)
    assert status["critic_shadow"] == "ok"
    assert data["critic_shadow"]["today"]["deals_judged"] == 0
    db = DB(path, read_only=True)
    assert not db.conn.execute("SELECT 1 FROM sqlite_master WHERE name='critic_shadow_daily'").fetchone()
    db.close()


@pytest.mark.parametrize("share,parse_cap", [(0.5, 0.25), (0.0, 0.5)])
@pytest.mark.parametrize("mode", ["shadow", "on"])
def test_critic_keeps_its_share_of_the_run_allowance(env, monkeypatch, share, parse_cap, mode):
    # Paced runs are small: extraction may not spend the critic's share, and
    # the critic then gets the whole allowance (its share plus what is left).
    monkeypatch.setattr(pipeline.util, "now_utc", lambda: NOW + timedelta(hours=22))
    caps = {}
    real_parse, real_critic = pipeline._parse_new_posts, pipeline._run_critic

    def parse(posts, parser, *a, **k):
        caps["parse"] = parser.run_cap_usd
        return real_parse(posts, parser, *a, **k)

    def critic(db, cfg, parser, now, **kwargs):
        caps["critic"] = parser.run_cap_usd
        return real_critic(db, cfg, parser, now, **kwargs)

    monkeypatch.setattr(pipeline, "_parse_new_posts", parse)
    monkeypatch.setattr(pipeline, "_run_critic", critic)
    real_load = pipeline.load_config

    def load(*a, **k):
        cfg = real_load(*a, **k)
        cfg.critic_reserve_share = share
        cfg.intake_mode = mode
        return cfg

    monkeypatch.setattr(pipeline, "load_config", load)
    _run(env)
    assert caps["parse"] == pytest.approx(parse_cap)
    assert caps["critic"] == pytest.approx(0.5)  # the per-run cap: allowance is not the limit at 22:00
    assert load_config().critic_reserve_share == 0.5


@pytest.mark.parametrize("written,mode", [('"off"', "off"), ("shadow", "shadow"), ('"on"', "on"), ("on", "on")])
def test_intake_config_modes_and_defaults(tmp_path, written, mode):
    path = tmp_path / "config.yaml"
    path.write_text("intake:\n  mode: " + written + "\n")
    assert load_config(path).intake_mode == mode
    assert load_config().intake_mode == "shadow"


@pytest.mark.parametrize("key,value", [("mode", "live"), ("window_days", 0), ("window_days", 31),
    ("min_posts", 0), ("min_history_hours", 0), ("max_wait_hours", float("inf")),
    ("floor_share", 0), ("exploration_share", 0), ("max_source_share", 1.1),
    ("max_store_share", .1), ("floor_share", .9)])
def test_intake_config_rejects_invalid_bounds(tmp_path, key, value):
    import yaml

    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"intake": {key: value}}))
    with pytest.raises(ValueError, match="intake"):
        load_config(path)


def test_intake_shadow_preserves_reads_output_and_spend_with_a_changed_proposal(env, monkeypatch, tmp_path):
    e2e_keep_source_posts(env)
    import json
    import yaml
    from dealradar import source_audit

    monkeypatch.setattr(pipeline.util, "now_utc", lambda: NOW + timedelta(hours=22))
    history = {"sources": {s: {"low_sample": False, "useful_per_usd": rate, "store_mix": {}}
                           for s, rate in (("blogone", 0), ("feedtwo", 100))}}
    monkeypatch.setattr(source_audit, "intake_history", lambda *a: history)
    path = Path(env["cfg"])
    config = yaml.safe_load(path.read_text())
    config["intake"].update(mode="off", max_source_share=.8)
    path.write_text(yaml.safe_dump(config))
    reads, fetch_orders = [], []
    original = pipeline._parse_new_posts
    original_fetch = pipeline._fetch_all

    def observe_fetch(sources, *a, **k):
        fetch_orders.append([s.name for s in sources])
        return original_fetch(sources, *a, **k)

    def observe(posts, *a, **k):
        reads.append([p.content_hash for p in posts])
        return original(posts, *a, **k)

    monkeypatch.setattr(pipeline, "_parse_new_posts", observe)
    monkeypatch.setattr(pipeline, "_fetch_all", observe_fetch)
    off = _run(env)
    public = (env["site"] / "deals.json").read_bytes()
    home = (env["site"] / "index.html").read_bytes()
    config["intake"]["mode"] = "shadow"
    path.write_text(yaml.safe_dump(config))
    env["db"] = str(tmp_path / "shadow.db")
    shadow = _run(env)
    assert reads[0] == reads[1]
    assert fetch_orders[0] == fetch_orders[1]
    assert shadow["intake"]["would_have_changed"] > 0
    assert shadow["intake"]["fetch_order_changes"] == 2
    assert public == (env["site"] / "deals.json").read_bytes()
    assert home == (env["site"] / "index.html").read_bytes()
    assert shadow["spend_by_kind"] == off["spend_by_kind"]
    assert shadow["extract_by_source"] == off["extract_by_source"]
    assert sum(shadow["intake"]["unique_public_by_source"].values()) == 2
    assert sum(shadow["intake"]["public_category_mix"].values()) == shadow["published"]
    assert "oldest_deferred_hours" in shadow["intake"]
    assert "intake" not in json.loads(public)["meta"]["last_run"]
    db = DB(env["db"])
    costs = db.meta_get("intake_cost:" + shadow["run_key"])
    facts = db.conn.execute("SELECT COUNT(*) FROM ai_yield_events").fetchone()[0]
    db.close()
    again = _run(env)
    assert again["posts_new"] == again["spend_usd"] == 0
    assert again["intake"]["unique_public_by_source"] == {}
    db = DB(env["db"])
    assert db.meta_get("intake_cost:" + shadow["run_key"]) == costs
    assert db.conn.execute("SELECT COUNT(*) FROM ai_yield_events").fetchone()[0] == facts
    db.close()


def test_on_keeps_pacing_critic_keepa_caps_and_retries_deferred_hashes(env, monkeypatch):
    import json
    import yaml
    from dealradar.keepa_budget import KeepaBudget
    from dealradar.util import iso

    monkeypatch.setattr(pipeline.util, "now_utc", lambda: NOW)
    path = Path(env["cfg"])
    config = yaml.safe_load(path.read_text())
    config["intake"]["mode"] = "on"
    config["parse"]["max_posts_per_run"] = 2
    path.write_text(yaml.safe_dump(config))
    db = DB(env["db"])
    db.meta_set("intake_since", iso(NOW - timedelta(hours=8)))
    db.record_source_result("feedtwo", ok=True, error="", items=2, now=NOW - timedelta(hours=7))
    budget = KeepaBudget(tokens_left=10, refill_rate=0, seen_at=NOW)
    budget.save(db)
    keepa_before = db.meta_get("keepa_budget")
    # Existing paced spend blocks all parsing, including an overdue source.
    db.start_run("earlier", "hourly", NOW)
    db.finish_run("earlier", now=NOW, items_fetched=0, posts_new=0,
                  deals_parsed=0, published=0, spend_usd=.5)
    blocked = _run(env)
    assert blocked["posts_new"] == blocked["spend_usd"] == 0
    assert blocked["intake"]["deferred_posts"] == 5
    assert db.conn.execute("SELECT COUNT(*) FROM posts_seen").fetchone()[0] == 0
    monkeypatch.setattr(pipeline.util, "now_utc", lambda: NOW + timedelta(hours=6))
    for expected in (2, 2, 1):
        summary = _run(env)
        assert summary["posts_new"] == expected
        assert len(summary["intake"]["proposed_post_order"]) <= 2
        assert summary["spend_usd"] <= summary["spend_pacing"]["allowance"] <= .5
        assert summary["price_recheck"]["tokens_short"] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM posts_seen").fetchone()[0] == 5
    assert json.loads(db.meta_get("keepa_budget")) == json.loads(keepa_before)
    assert db.spend_usd_for_day(NOW) <= 6
    before = (db.conn.execute("SELECT COUNT(*) FROM ai_yield_events").fetchone()[0],
              db.conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0])
    again = _run(env)
    assert again["posts_new"] == again["spend_usd"] == 0
    assert again["intake"]["unique_public_by_source"] == {}
    assert before == (db.conn.execute("SELECT COUNT(*) FROM ai_yield_events").fetchone()[0],
                      db.conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0])
    db.close()
