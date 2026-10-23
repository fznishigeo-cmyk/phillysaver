"""Shipped tiering through real evidence, ingestion, judging, publish and reruns."""
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from dealradar import pipeline
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.models import RawPost
from dealradar.parse.schema import CriticVerdicts
from tests.http_helpers import HostClock
from tests import test_e2e_offline as offline
from tests.history_pins import pre_haiku_only_raw, pre_hide_deal_site_links_raw

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"


@pytest.fixture
def shipped_env(tmp_path, monkeypatch):
    from dealradar.http_client import PoliteFetcher

    class OfflineFetcher(PoliteFetcher):
        def __init__(self, *args, **kwargs):
            clock = HostClock()
            super().__init__(*args, **kwargs, clock=clock, sleep_fn=clock.sleep)

    # Exercise the real pacing path over recorded HTTP without wall-clock sleeps.
    monkeypatch.setattr("dealradar.http_client.PoliteFetcher", OfflineFetcher)
    env = offline._offline_env(tmp_path, monkeypatch)
    # Recorded public facts include a source-only offer; retain the OFF case.
    path = Path(env["cfg"])
    path.write_text(yaml.safe_dump(pre_hide_deal_site_links_raw(yaml.safe_load(path.read_text()))))
    cfg = load_config(env["cfg"])
    assert cfg.critic_tiering == load_config().critic_tiering
    assert cfg.critic_tiering["enabled"] is True
    # Four recorded roundups exceed the shipped 300-row protected wall plus
    # two eight-deal samples, leaving ordinary approvals to publish as Haiku.
    posts, facts = [], {}
    for batch in range(4):
        title = f"Tiering roundup {batch}"
        deals = [dict(item=f"Ordinary Widget {batch}-{i:02d}", store="Walmart",
                      product_url=f"https://www.walmart.com/ip/widget-{batch}-{i}",
                      online_only="true", regular_price=100., final_price=25.,
                      confidence=.95, category="home", evidence=dict(
                          regular_price_quote="was $100.00", final_price_quote="now $25.00"))
                 for i in range(90)]
        text = title + "\n" + "\n".join(
            d["item"] + ": was $100.00, now $25.00 online only" for d in deals)
        assert len(text) < cfg.max_chars_per_post
        posts.append(RawPost("blogone", title, f"https://blogone.example/tiering-{batch}",
                             title, text, offline.NOW, offline.NOW))
        facts[title] = deals
    original_fetch, original_deals = pipeline._fetch_all, offline._canned_deals

    def fetch(*args, **kwargs):
        found, errors = original_fetch(*args, **kwargs)
        return found + posts, errors

    def extract(prompt):
        return next((deals for title, deals in facts.items() if title in prompt),
                    original_deals(prompt))

    monkeypatch.setattr(pipeline, "_fetch_all", fetch)
    monkeypatch.setattr(offline, "_canned_deals", extract)
    return env


class RecordingMessages(offline._FakeMessages):
    def __init__(self, env, *, rescue_kill=False):
        self.env, self.rescue_kill = env, rescue_kill
        self.tiered = load_config(env["cfg"]).critic_tiering["enabled"]
        self.calls = []
        self.kill_pending_at_sonnet = False

    def create(self, **kwargs):
        self.calls.append(kwargs)
        entries = json.loads(kwargs["messages"][0]["content"])
        if not any(e["title"].startswith("Ordinary Widget") for e in entries):
            return super().create(**kwargs)
        assert "Return ONLY a JSON array" in kwargs["system"]
        categories = {"Chef Pro Knife Set": "kitchen", "Copper Pan Duo": "kitchen",
                      "Ninja Foodi 8-qt Air Fryer": "kitchen", "Mega Headphones X200": "electronics"}
        response = offline._FakeResponse(None)
        response.content = [SimpleNamespace(type="text", text=json.dumps([
            {"i": e["i"], "category": categories.get(e["title"], "home")} for e in entries]))]
        return response

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        response = super().parse(**kwargs)
        if self.tiered and kwargs["output_format"] is CriticVerdicts and kwargs["model"] == SONNET:
            entries = json.loads(kwargs["messages"][0]["content"])
            if any(e["item"] == "Chef Pro Knife Set" for e in entries):
                db = DB(self.env["db"])
                knife = next(d for d in db.all_deals() if d["item"] == "Chef Pro Knife Set")
                assert knife["status"] == "alive" and knife.get("critic") is None
                assert "critic_tier_pending" in knife["flags"]
                assert "suspect_msrp" not in knife["flags"]
                db.close()
                self.kill_pending_at_sonnet = True
                if self.rescue_kill:
                    for entry, verdict in zip(entries, response.parsed_output.verdicts):
                        if entry["item"] == "Chef Pro Knife Set":
                            verdict.plausible = True
        return response


def run(env, *, rescue_kill=False):
    client = offline._FakeClaudeClient()
    client.messages = RecordingMessages(env, rescue_kill=rescue_kill)
    result = pipeline.run(db_path=env["db"], config_path=env["cfg"], dry_run_telegram=True,
        transport=httpx.MockTransport(offline._handler), sources_override=offline.SOURCES,
        skip_validation=True, claude_client=client)
    return result, client.messages


def public(env):
    return json.loads((env["site"] / "deals.json").read_text())["deals"]


def pre_haiku_only_env(env):
    """Sonnet re-judges wall deals and Haiku kills only with haiku_only off."""
    path = Path(env["cfg"])
    path.write_text(yaml.safe_dump(pre_haiku_only_raw(yaml.safe_load(path.read_text()))))
    return env


def test_shipped_config_publishes_ordinary_haiku_approvals(shipped_env):
    result, messages = run(pre_haiku_only_env(shipped_env))
    assert result["parse"]["deals_dropped_evidence"] == 0
    assert result["published"] == len(public(shipped_env)) == 362
    assert result["critic"]["tiering"]["haiku_approved"] > 0
    assert result["critic"]["tiering"]["sample_judged"] == 16
    assert not result["critic"]["tiering"]["fallback"]
    db = DB(shipped_env["db"])
    cheap = {d["item"] for d in db.all_deals()
             if (d.get("critic") or {}).get("model") == HAIKU}
    assert cheap and cheap <= {d["item"] for d in public(shipped_env)}
    ordinary = {d["item"] for d in public(shipped_env) if d["item"].startswith("Ordinary Widget")}
    assert len(ordinary) == 360 and len(cheap & ordinary) == 45
    assert cheap - ordinary == {"Mega Headphones X200"}
    protected = json.loads(db.meta_get("published_wall"))
    assert len(protected) == load_config().keepa_recheck_wall_n
    assert all(db.get_deal(key)["critic"]["model"] == SONNET for key in protected)
    critic_models = [c["model"] for c in messages.calls if c.get("output_format") is CriticVerdicts]
    assert critic_models[0] == HAIKU and SONNET in critic_models
    db.close()


def test_shipped_config_sonnet_can_reverse_a_haiku_kill(shipped_env):
    result, messages = run(pre_haiku_only_env(shipped_env), rescue_kill=True)
    assert messages.kill_pending_at_sonnet
    db = DB(shipped_env["db"])
    knife = next(d for d in db.all_deals() if d["item"] == "Chef Pro Knife Set")
    assert knife["status"] == "alive"
    assert knife["critic"]["model"] == SONNET and knife["critic"]["plausible"] is True
    assert "suspect_msrp" not in knife["flags"] and "critic_tier_pending" not in knife["flags"]
    assert "Chef Pro Knife Set" in {d["item"] for d in public(shipped_env)}
    assert result["critic"]["suspect"] == 0 and result["deaths_by_reason"] == {}
    db.close()


def test_shipped_tiering_spends_less_than_off_and_rerun_is_idempotent(shipped_env, tmp_path):
    first, messages = run(shipped_env)
    # Shipped haiku_only (operator 2026-10-22, "do both on haiku"): every recorded verdict
    # is confident (0.9 >= min_confidence 0.8), so Haiku's kill is final; Sonnet never sees it.
    assert not messages.kill_pending_at_sonnet
    published = public(shipped_env)
    assert "Chef Pro Knife Set" not in {d["item"] for d in published}
    tiering = first["critic"]["tiering"]
    # 363 candidates (360 widgets, knife, air fryer, headphones) less the knife kill.
    assert tiering["escalated"] == 0 and tiering["haiku_approved"] == len(published) == 362
    # Haiku judges them in ceil(363 / 8) = 46 batches; then Sonnet only samples
    # approvals, shadow_batches_per_day (2) batches of critic_batch_size (8).
    critic_models = [c["model"] for c in messages.calls if c.get("output_format") is CriticVerdicts]
    assert critic_models == [HAIKU] * 46 + [SONNET] * 2
    assert tiering["sample_judged"] == 16 and tiering["sample_disagreed"] == 0
    assert not tiering["fallback"]
    db = DB(shipped_env["db"])
    knife = next(d for d in db.all_deals() if d["item"] == "Chef Pro Knife Set")
    assert knife["critic"]["model"] == HAIKU and knife["critic"]["plausible"] is False
    assert "suspect_msrp" in knife["flags"] and "critic_tier_pending" not in knife["flags"]
    sampled = {d["dedupe_key"] for d in db.all_deals()
               if (d.get("critic") or {}).get("model") == SONNET}
    assert len(sampled) == 16 and all(db.get_deal(k)["critic"]["plausible"] for k in sampled)
    # The protected wall keeps Haiku's verdicts; only sampled deals carry Sonnet's.
    protected = set(json.loads(db.meta_get("published_wall")))
    assert {db.get_deal(k)["critic"]["model"] for k in protected - sampled} == {HAIKU}
    tables = ("deals", "sightings", "price_history", "alerts_sent", "posts_seen", "llm_calls",
              "critic_tier_samples", "critic_tier_batches", "source_audit_posts", "ai_yield_events",
              "products", "product_keys", "product_offers")
    counts = {t: db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    verdicts = {d["dedupe_key"]: d.get("critic") for d in db.all_deals()}
    spent = db.spend_usd_for_day(offline.NOW)
    again, repeated = run(shipped_env)
    assert again["run_key"] == first["run_key"]
    assert again["posts_new"] == again["spend_usd"] == again["telegram"]["sent"] == 0
    assert repeated.calls == [] and public(shipped_env) == published
    assert counts == {t: db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    assert verdicts == {d["dedupe_key"]: d.get("critic") for d in db.all_deals()}
    assert db.spend_usd_for_day(offline.NOW) == spent
    db.close()

    path = Path(shipped_env["cfg"])
    config = yaml.safe_load(path.read_text())
    config["parse"]["critic_tiering"]["enabled"] = False
    path.write_text(yaml.safe_dump(config))
    baseline_env = dict(shipped_env, db=str(tmp_path / "untiered.db"))
    baseline, _ = run(baseline_env)
    assert public(baseline_env) == published
    assert first["spend_usd"] < baseline["spend_usd"]
    assert first["spend_by_kind"]["critic"]["usd"] < baseline["spend_by_kind"]["critic"]["usd"]
    cfg = load_config()
    assert first["spend_usd"] <= first["spend_pacing"]["allowance"] <= cfg.spend_cap_usd_per_run
