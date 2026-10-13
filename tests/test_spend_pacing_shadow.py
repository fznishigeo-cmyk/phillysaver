"""Paced intake and a strictly observational, budgeted critic experiment."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dealradar import pipeline
from dealradar.config import AppConfig, daily_spend_cap, load_config, spend_allowance
from dealradar.db import DB
from dealradar.growth import report
from tests.test_ai_spend import Messages, deal, insert, parser_for
from tests.test_e2e_offline import env, _run, _FakeMessages

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


@pytest.mark.parametrize("hour,minute,fraction", [
    (0, 0, 2 / 24), (6, 0, 8 / 24), (12, 0, 14 / 24), (23, 59, 1),
])
@pytest.mark.parametrize("day,cap", [(12, 15), (14, 6)])
def test_pacing_math_and_hard_caps(hour, minute, fraction, day, cap):
    cfg = load_config()
    now = NOW.replace(day=day, hour=hour, minute=minute)
    for spent in (0, .25, cap * fraction, cap, cap + 1):
        allowance, allowed = spend_allowance(cfg, now, spent)
        assert daily_spend_cap(cfg, now) == cap
        assert allowed == pytest.approx(cap * fraction)
        assert allowance == pytest.approx(max(0, min(.5, cap * fraction - spent)))
        assert 0 <= allowance <= cfg.spend_cap_usd_per_run
        assert allowance <= max(0, cap - spent)


def test_pacing_uses_utc_and_switch_off_restores_old_allowance():
    cfg = load_config()
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


@pytest.mark.parametrize("cap", [.003, .00301, .01])
def test_shadow_skips_when_full_call_will_not_fit(tmp_path, cap):
    db = DB(tmp_path / "state.db")
    insert(db, deal())
    parser, msgs = parser_for(critic_shadow_batches_per_day=12)
    parser.run_cap_usd = cap
    # Small allowances cannot reserve even the real critic's full response.
    assert pipeline._run_critic(db, parser.cfg, parser, NOW)["judged"] == int(cap == .01)
    expected = int(cap == .01)
    assert parser.spend_by_kind["critic_shadow"]["calls"] == expected
    assert db.critic_shadow_counts(NOW)["batches"] == expected
    assert parser.spend_usd <= cap
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
def test_critic_keeps_its_share_of_the_run_allowance(env, monkeypatch, share, parse_cap):
    # Paced runs are small: extraction may not spend the critic's share, and
    # the critic then gets the whole allowance (its share plus what is left).
    monkeypatch.setattr(pipeline.util, "now_utc", lambda: NOW + timedelta(hours=22))
    caps = {}
    real_parse, real_critic = pipeline._parse_new_posts, pipeline._run_critic

    def parse(posts, parser, *a, **k):
        caps["parse"] = parser.run_cap_usd
        return real_parse(posts, parser, *a, **k)

    def critic(db, cfg, parser, now):
        caps["critic"] = parser.run_cap_usd
        return real_critic(db, cfg, parser, now)

    monkeypatch.setattr(pipeline, "_parse_new_posts", parse)
    monkeypatch.setattr(pipeline, "_run_critic", critic)
    real_load = pipeline.load_config

    def load(*a, **k):
        cfg = real_load(*a, **k)
        cfg.critic_reserve_share = share
        return cfg

    monkeypatch.setattr(pipeline, "load_config", load)
    _run(env)
    assert caps["parse"] == pytest.approx(parse_cap)
    assert caps["critic"] == pytest.approx(0.5)  # the per-run cap: allowance is not the limit at 22:00
    assert load_config().critic_reserve_share == 0.5
