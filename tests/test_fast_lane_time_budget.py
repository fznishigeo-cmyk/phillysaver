"""Offline deadlines, timeout accounting and fast-lane replay (task 109)."""
import dataclasses
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import pytest
import yaml

from dealradar import critic_tiering, pipeline, rank
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.models import RawPost
from dealradar.parse import extractor
from dealradar.parse.claude_client import critique_deals
from dealradar.parse.schema import CriticVerdicts
from tests import test_e2e_offline as offline
from tests.http_helpers import HostClock
from tests.test_critic_tiering import HAIKU, SONNET, add, tier_parser
from tests.test_e2e_offline import env


@pytest.mark.parametrize("structured", [False, True])
def test_timeout_skips_without_transport_retry(structured, tmp_path):
    """A timeout costs one request: no 2 s retry. The post is still recorded as
    handled (as before), so a post that always times out cannot eat every run."""
    from tests.test_claude_client import (FakeCreateOnlyMessages, FakeMessagesWithParse,
                                         FakeSleep, _HTTPX_REQ, make_cfg, make_parser)

    error = anthropic.APITimeoutError(request=_HTTPX_REQ)
    # The extract read and the existing second opinion both time out once.
    messages = (FakeMessagesWithParse(parse_responses=[error, error]) if structured
                else FakeCreateOnlyMessages(create_responses=[error, error]))
    sleep = FakeSleep()
    cfg = make_cfg(parse_structured_output=structured, second_opinion_model=SONNET,
                   max_second_opinions_per_run=8)
    parser = make_parser(messages, cfg=cfg, sleep=sleep)
    db = DB(tmp_path / "state.db")
    parser.bind_ledger(db, "timeout")
    post = RawPost("blogone", "timeout", "https://blogone.example/timeout", "Widget",
                   "Widget was $100 now $20 online only", offline.NOW, offline.NOW)
    pairs, stats = extractor.parse_posts([post], parser, cfg, offline.NOW, {}, db=db)
    assert pairs == [] and stats["processed_hashes"] == [post.content_hash]
    assert stats["posts_sent"] == 1
    assert sleep.calls == []
    assert len(messages.parse_calls if structured else messages.create_calls) == 2
    assert db.cached_post_parse(hashlib.sha256(post.text.encode()).hexdigest(),
                                offline.NOW, cfg.post_parses_days) is None
    row = db.conn.execute("SELECT status, reserved_usd, cost_usd FROM llm_calls").fetchone()
    assert row[0] == "uncertain" and row[1] == row[2] > 0
    db.close()


@pytest.mark.parametrize("remaining,expected", [(41.9, 0), (42, 1)])
def test_extractor_reserves_transport_retry_time(monkeypatch, remaining, expected):
    from tests.test_extractor import FakeParser, make_cfg, make_post

    monkeypatch.setattr(extractor.time, "monotonic", lambda: 100.)
    parser = FakeParser([[]])
    post = make_post()
    _, stats = extractor.parse_posts([post], parser, make_cfg(request_timeout_s=20),
                                    offline.NOW, {}, deadline=100 + remaining)
    assert len(parser.calls) == len(stats["processed_hashes"]) == expected


@pytest.mark.parametrize("enabled,model,tokens", [
    (False, HAIKU, 340), (False, SONNET, 340),
    (True, HAIKU, 580), (True, SONNET, 340),
])
def test_only_enabled_tier_model_gets_more_output(enabled, model, tokens):
    from tests.test_ai_spend import deal

    parser, messages = tier_parser()
    parser.cfg.critic_tiering["enabled"] = enabled
    assert all(critique_deals(parser, [deal()] * 3, model=model))
    assert messages.calls[0]["max_tokens"] == tokens


@pytest.mark.parametrize("mode", ["haiku", "sonnet", "fallback", "sample", "promoted", "off"])
def test_each_critic_loop_stops_before_its_deadline(tmp_path, monkeypatch, mode):
    from tests.test_critic_tiering import TierMessages

    clock = HostClock()

    class Messages(TierMessages):
        def parse(self, **kwargs):
            result = super().parse(**kwargs)
            clock.sleep(3)
            return result

    parser, messages = tier_parser(Messages())
    cfg = parser.cfg
    cfg.critic_batch_size = 1
    cfg.critic_tiering["enabled"] = mode != "off"
    db = DB(tmp_path / "state.db")
    for key in ("a", "b", "c"):
        add(db, key)
    # Three Haiku calls take 9s; only one subsequent Sonnet call fits.
    if mode == "sonnet":
        cfg.critic_tiering["high_price_usd"] = 20
    elif mode == "fallback":
        db.meta_set(critic_tiering.FALLBACK, "offline fallback")
    elif mode == "sample":
        cfg.critic_tiering["shadow_batches_per_day"] = 2
    elif mode == "promoted":
        cfg.keepa_recheck_wall_n = 2
        for row in db.all_deals():
            db.update_deal(row["dedupe_key"], payload=dict(row, critic=dict(
                plausible=True, confidence=.9, reason="OK", model=HAIKU)))
        # Isolate the promotion pass: the real pending gate/save is used below.
        monkeypatch.setattr(critic_tiering, "run", lambda *a, **kw: {"escalated": 0})
    monkeypatch.setattr(critic_tiering.time, "monotonic", clock)
    deadline = 51 if mode in {"sonnet", "sample"} else 42
    result = pipeline._run_critic(db, cfg, parser, offline.NOW, deadline=deadline)
    expected = [HAIKU] * 3 + [SONNET] if mode in {"sonnet", "sample"} else [
        SONNET if mode in {"off", "fallback", "promoted"} else HAIKU]
    assert [c["model"] for c in messages.calls] == expected
    assert clock.now + 42 > deadline
    if mode != "sample":
        pending = [d for d in db.all_deals() if "critic_tier_pending" in d["flags"]
                   or not d.get("critic")]
        assert pending
        if mode != "off":
            assert all(not rank.is_publishable(d, cfg)[0] for d in pending)
    else:
        assert result["tiering"]["sample_judged"] == 1
        assert db.conn.execute("SELECT COUNT(*) FROM critic_tier_batches").fetchone()[0] == 1
    db.close()


@pytest.mark.parametrize("fast_lane", [False, True])
@pytest.mark.parametrize("fetch_elapsed", [0, 420])
def test_pipeline_parse_stop_is_fast_lane_only(env, monkeypatch, fast_lane, fetch_elapsed):
    clock = HostClock()
    monkeypatch.setattr(pipeline.time, "monotonic", clock)
    def fetch(*args, **kwargs):
        clock.sleep(fetch_elapsed)
        return [], {}

    monkeypatch.setattr(pipeline, "_fetch_all", fetch)
    seen = {}
    real_parse, real_critic = pipeline._parse_new_posts, pipeline._run_critic

    def parse(*args, **kwargs):
        seen["parse"] = args[5]
        return real_parse(*args, **kwargs)

    def critic(*args, **kwargs):
        seen["critic"] = kwargs["deadline"]
        return real_critic(*args, **kwargs)

    monkeypatch.setattr(pipeline, "_parse_new_posts", parse)
    monkeypatch.setattr(pipeline, "_run_critic", critic)
    pipeline.run(db_path=env["db"], config_path=env["cfg"], fast_lane=fast_lane,
                 now=offline.NOW, sources_override=[], skip_validation=True,
                 transport=httpx.MockTransport(offline._handler),
                 claude_client=offline._FakeClaudeClient(), dry_run_telegram=True)
    cfg = load_config(env["cfg"])
    assert seen == {"parse": min(fetch_elapsed + cfg.fast_lane_parse_budget_s, cfg.run_budget_s - 90)
                   if fast_lane else cfg.run_budget_s - 90,
                    "critic": cfg.run_budget_s - 45 if fast_lane else None}


def test_fast_lane_tiered_replay_stops_parse_and_critic_and_is_idempotent(env, monkeypatch, caplog):
    from dealradar.http_client import PoliteFetcher

    clock = HostClock()
    monkeypatch.setenv("DEALRADAR_RUN_KEY", "timing:first")
    for module in (pipeline, extractor, critic_tiering):
        monkeypatch.setattr(module.time, "monotonic", clock)

    class OfflineFetcher(PoliteFetcher):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, clock=clock, sleep_fn=clock.sleep)

    monkeypatch.setattr("dealradar.http_client.PoliteFetcher", OfflineFetcher)
    cfg_path = Path(env["cfg"])
    config = yaml.safe_load(cfg_path.read_text())
    config["fetch"]["run_budget_s"] = 480
    config["parse"]["concurrency"] = 1  # deterministic sum of stub latencies
    config["parse"]["critic_tiering"]["enabled"] = True
    config["parse"]["critic_batch_size"] = 1
    config["parse"]["structured_output"] = False
    cfg_path.write_text(yaml.safe_dump(config))
    posts = [RawPost("blogone", f"timing-{i}", f"https://blogone.example/timing-{i}",
                     f"Timing Widget {i}", f"Timing Widget {i}: was $100 now $25 online only",
                     offline.NOW, offline.NOW) for i in range(150)]
    monkeypatch.setattr(pipeline, "_fetch_all", lambda *a, **kw: (posts, {}))
    db = DB(env["db"])
    # A regular-price backlog forces the critic to use its full window.
    for i in range(150):
        add(db, f"backlog-{i}")
    db.close()

    class TimingMessages(offline._FakeMessages):
        def __init__(self):
            self.extracts, self.timeouts, self.critics, self.read = [], [], [], set()

        def create(self, **kwargs):
            if isinstance(kwargs["system"], str):
                entries = json.loads(kwargs["messages"][0]["content"])
                response = offline._FakeResponse(None)
                response.content = [SimpleNamespace(type="text", text=json.dumps([
                    {"i": e["i"], "category": "home"} for e in entries]))]
                return response
            prompt = kwargs["messages"][0]["content"]
            self.extracts.append(clock.now)  # extract reads and second opinions alike
            self.read.add(next(p.title for p in posts if p.title + ":" in prompt))
            if len(self.extracts) % 3 == 0:
                self.timeouts.append(prompt)
                clock.sleep(20)
                raise anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.example/messages"))
            clock.sleep(6)
            item = next(p.title for p in posts if p.title + ":" in prompt)
            response = offline._FakeResponse(None)
            response.content = [SimpleNamespace(type="text", text=json.dumps({"deals": [dict(
                item=item, store="Walmart", online_only="true", category="home",
                product_url=f"https://www.walmart.com/ip/{item.rsplit(' ', 1)[1]}",
                regular_price=100, final_price=25, confidence=.95,
                evidence=dict(regular_price_quote="was $100", final_price_quote="now $25"))]}))]
            return response

        def parse(self, **kwargs):
            assert kwargs["output_format"] is CriticVerdicts
            self.critics.append(clock.now)
            clock.sleep(3)
            return super().parse(**kwargs)

    def run():
        client = offline._FakeClaudeClient()
        client.messages = TimingMessages()
        start = clock.now
        result = pipeline.run(db_path=env["db"], config_path=env["cfg"], fast_lane=True,
            now=offline.NOW, sources_override=[dataclasses.replace(offline.SOURCES[0], fast_lane=True)],
            transport=httpx.MockTransport(offline._handler), skip_validation=True,
            claude_client=client, dry_run_telegram=True)
        return result, client.messages, clock.now - start

    first, messages, elapsed = run()
    assert elapsed <= 450
    assert 0 < len(messages.extracts) < 150 and messages.timeouts
    # No read (or second opinion) can still be running past the parse budget.
    assert messages.extracts[-1] + 20 <= config["parse"]["fast_lane_parse_budget_s"]
    assert first["parse"]["spend_usd"] > 0 and not first["parse"]["aborted_spend_cap"]
    assert messages.critics[-1] + 42 <= 480 - 45
    assert clock.now + 42 > 480 - 45
    assert "run deadline reached: stopping parse" in caplog.text
    assert "critic: Sonnet deadline reached" in caplog.text
    db = DB(env["db"])
    pending = [d for d in db.all_deals() if "critic_tier_pending" in d["flags"]]
    assert pending and all(not rank.is_publishable(d, load_config(env["cfg"]))[0] for d in pending)
    public = json.loads((env["site"] / "deals.json").read_text())["deals"]
    assert not {d["item"] for d in pending} & {d["item"] for d in public}
    # Every post that was read is recorded, failed reads included (as before).
    assert db.conn.execute("SELECT COUNT(*) FROM posts_seen").fetchone()[0] == len(messages.read)
    tables = ("deals", "sightings", "price_history", "posts_seen", "alerts_sent", "products",
              "product_keys", "product_offers", "source_audit_posts", "llm_calls",
              "critic_tier_samples", "critic_tier_batches")
    before = {t: db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    again, repeated, _ = run()
    assert again["run_key"] == first["run_key"]
    assert again["spend_usd"] == again["posts_new"] == 0
    assert repeated.extracts == repeated.critics == []
    assert {t: db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables} == before
    monkeypatch.setenv("DEALRADAR_RUN_KEY", "timing:next")
    following, retried, _ = run()
    assert following["posts_new"] > 0 and retried.extracts and retried.critics
    assert db.conn.execute("SELECT COUNT(*) FROM posts_seen").fetchone()[0] > before["posts_seen"]
    db.close()
