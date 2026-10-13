"""Offline contracts for more evidence-backed deals per AI dollar (task 059)."""
import dataclasses
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from dealradar import pipeline
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.parse.claude_client import critique_deals
from dealradar.parse.extractor import parse_posts
from dealradar.parse.schema import CriticVerdicts, LLMDeals
from tests.test_claude_client import (FakeCreateOnlyMessages, FakeCreateResponse,
                                    FakeMessagesWithParse, FakeParseResponse,
                                    FakeUsage, META, make_parser, one_deal)
from tests.test_e2e_offline import NOW, SOURCES, _handler, _run, env
from tests.test_extractor import FakeParser, good_deal, make_cfg, make_post
from tests.test_quality_trio import SecondOpinionParser


@pytest.mark.parametrize("model,in_rate,out_rate,read_mult", [
    ("claude-haiku-5-5", .10, .50, .10),
    ("claude-sonnet-5-5", 2., 10., .05),
])
def test_cache_usage_is_priced_by_the_actual_model(model, in_rate, out_rate, read_mult):
    cfg = load_config()
    parser = make_parser(FakeMessagesWithParse(), cfg=cfg)
    usage = SimpleNamespace(input_tokens=100, output_tokens=40,
                            cache_creation_input_tokens=200, cache_read_input_tokens=300)
    cost = parser._account(SimpleNamespace(usage=usage), model, kind="second_opinion")
    assert cost == pytest.approx((100 * in_rate + 200 * in_rate * 1.25
                                 + 300 * in_rate * read_mult + 40 * out_rate) / 1e6)
    assert parser.spend_by_kind["second_opinion"] == {"calls": 1, "usd": cost}
    assert parser.cache_tokens == {"read": 300, "write": 200, "uncached": 100}


@pytest.mark.parametrize("model", ["unknown-model", "claude-haiku-5-5"])
def test_missing_cache_multipliers_use_conservative_default(model):
    cfg = load_config()
    cfg.model_pricing_per_mtok["default"].update(cache_write=1., cache_read=.05)
    cfg.model_pricing_per_mtok["claude-haiku-5-5"] = {"input": .1, "output": .5}
    parser = make_parser(FakeMessagesWithParse(), cfg=cfg)
    rate = 1. if model == "unknown-model" else .1
    usage = SimpleNamespace(input_tokens=0, output_tokens=0,
                            cache_creation_input_tokens=1000, cache_read_input_tokens=1000)
    assert parser._account(SimpleNamespace(usage=usage), model) == pytest.approx(
        1000 * rate * (1.25 + .10) / 1e6)


@pytest.mark.parametrize("model", ["claude-haiku-5-5", "claude-sonnet-5-5"])
@pytest.mark.parametrize("cached", [False, True])
def test_reservation_covers_a_full_cache_write_and_all_output(model, cached):
    parser = make_parser(FakeMessagesWithParse(), cfg=load_config())
    system = [{"type": "text", "text": "static prefix"}]
    if cached:
        system[0]["cache_control"] = {"type": "ephemeral"}
    kwargs = dict(model=model, max_tokens=100, system=system,
                  messages=[{"role": "user", "content": "variable post"}])
    reservation = parser._reserve(kwargs)
    input_bound = len(json.dumps(dict(system=system, messages=kwargs["messages"],
                                     schema=None)).encode("utf-8")) + 1024
    usage = SimpleNamespace(input_tokens=0 if cached else input_bound, output_tokens=100,
                            cache_creation_input_tokens=input_bound if cached else 0,
                            cache_read_input_tokens=0)
    parser._settle(reservation, kwargs, SimpleNamespace(usage=usage), kind="extract")
    assert reservation[1] >= parser.spend_usd
    assert reservation[1] == pytest.approx(parser.spend_usd)
    assert parser._reserved_usd == 0


@pytest.mark.parametrize("structured", [False, True])
def test_static_extraction_prefix_is_cached_before_variable_posts(structured):
    responses = [FakeParseResponse(one_deal(), FakeUsage(10, 10)) for _ in range(2)]
    creates = [FakeCreateResponse('{"deals": []}', FakeUsage(10, 10)) for _ in range(2)]
    messages = FakeMessagesWithParse(parse_responses=responses, create_responses=creates)
    cfg = load_config()
    cfg.parse_structured_output = structured
    parser = make_parser(messages, cfg=cfg)
    parser.extract("PRIVATE POST ONE", META)
    parser.extract("PRIVATE POST TWO", META, model=cfg.second_opinion_model, kind="second_opinion")
    calls = messages.parse_calls if structured else messages.create_calls
    assert calls[0]["system"] == calls[1]["system"]
    assert calls[0]["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "PRIVATE POST" not in json.dumps(calls[0]["system"])
    assert "PRIVATE POST ONE" in calls[0]["messages"][0]["content"]
    assert "PRIVATE POST TWO" in calls[1]["messages"][0]["content"]
    if not structured:
        assert "ONLY a single JSON object" in calls[0]["system"][0]["text"]


def test_critic_prefix_and_cache_usage_use_the_critic_model():
    usage = SimpleNamespace(input_tokens=100, output_tokens=50,
                            cache_creation_input_tokens=600, cache_read_input_tokens=1200)
    reply = CriticVerdicts.model_validate({"verdicts": [
        dict(index=0, plausible=True, confidence=.9, reason="OK")]})
    messages = FakeMessagesWithParse(parse_responses=[FakeParseResponse(reply, usage)])
    parser = make_parser(messages, cfg=load_config())
    result = critique_deals(parser, [dict(item="PRIVATE ITEM", store="Store",
                                        regular_price=100., final_price=20.)])
    assert result[0]["plausible"] is True
    call = messages.parse_calls[0]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "PRIVATE ITEM" not in json.dumps(call["system"])
    assert parser.spend_usd == pytest.approx((100 * 2 + 600 * 2 * 1.25
                                             + 1200 * 2 * .05 + 50 * 10) / 1e6)


def test_paid_json_reasks_and_uncertain_responses_keep_cache_costs(tmp_path, monkeypatch):
    monkeypatch.setattr("dealradar.util.now_utc", lambda: NOW)
    write = SimpleNamespace(input_tokens=50, output_tokens=20,
                            cache_creation_input_tokens=1000, cache_read_input_tokens=0)
    read = SimpleNamespace(input_tokens=50, output_tokens=20,
                           cache_creation_input_tokens=0, cache_read_input_tokens=1000)
    cfg = load_config()
    cfg.spend_pacing = False
    messages = FakeCreateOnlyMessages([
        FakeCreateResponse("bad json", write), FakeCreateResponse('{"deals": []}', read)])
    parser = make_parser(messages, cfg=cfg)
    db = DB(tmp_path / "state.db")
    parser.bind_ledger(db, "reask")
    assert parser.extract("post", META) == []
    expected = (2 * (50 * .1 + 20 * .5) + 1000 * .1 * (1.25 + .10)) / 1e6
    assert parser.spend_usd == pytest.approx(expected)
    assert db.spend_usd_for_day(NOW) == pytest.approx(expected)
    assert parser.spend_by_kind["extract"]["calls"] == 2
    kwargs = messages.create_calls[-1]
    reservation = parser._reserve(kwargs)
    parser._settle(reservation, kwargs, kind="extract")  # unreadable paid response
    assert parser.spend_usd == pytest.approx(expected + reservation[1])
    row = db.conn.execute("SELECT status,cost_usd FROM llm_calls ORDER BY rowid DESC").fetchone()
    assert tuple(row) == ("uncertain", reservation[1])
    db.close()


@pytest.mark.parametrize("workers", [1, 6])
def test_identical_cleaned_text_is_read_once_even_in_the_same_batch(tmp_path, monkeypatch, workers):
    db = DB(tmp_path / "state.db")
    posts = [make_post(source_name="one", text="Widget  was $20.00 now $10.00"),
             make_post(source_name="two", text="Widget was $20.00 now $10.00")]
    messages = FakeCreateOnlyMessages([FakeCreateResponse(
        LLMDeals(deals=[good_deal()]).model_dump_json(), FakeUsage(100, 50))])
    parser = make_parser(messages, cfg=load_config())
    parser.cfg.parse_concurrency = workers
    verified = []
    from dealradar.parse import extractor
    verify = extractor.verify_evidence

    def observe(deal, text):
        verified.append((deal.source_name, text))
        return verify(deal, text)

    monkeypatch.setattr(extractor, "verify_evidence", observe)
    pairs, stats = parse_posts(posts, parser, parser.cfg, NOW, {}, db=db)
    assert [d.source_name for _, d in pairs] == ["one", "two"]
    assert [d.source_url for _, d in pairs] == [p.url for p in posts]
    assert len(messages.create_calls) == 1
    assert stats["posts_sent"] == 1
    assert stats["reused_posts"] == stats["reused_deals"] == 1
    assert [name for name, _ in verified] == ["one", "two"]
    assert stats["processed_hashes"] == [p.content_hash for p in posts]
    assert parser.extract_by_source["two"] == dict(calls=0, usd=0., deals=1, publishable=0)
    payload = json.loads(db.conn.execute("SELECT deals FROM post_parses").fetchone()[0])
    assert payload == [good_deal().model_dump(mode="json")]
    db.close()


def test_reuse_survives_restarts_and_reverifies_evidence(tmp_path):
    path = tmp_path / "state.db"
    db = DB(path)
    first = make_post(source_name="one", text="Widget was $20.00 now $10.00\nPRIVATE BODY MARKER")
    parse_posts([first], FakeParser([[good_deal()]]), make_cfg(), NOW, {}, db=db)
    assert "PRIVATE BODY MARKER" not in "\n".join(db.conn.iterdump())
    db.close()
    db = DB(path)
    second = dataclasses.replace(first, source_name="two", url="https://two.example/post")
    parser = FakeParser(raise_cap_at=0)
    pairs, stats = parse_posts([second], parser, make_cfg(), NOW, {}, db=db)
    assert len(pairs) == stats["reused_deals"] == 1
    assert pairs[0][1].source_name == "two" and pairs[0][1].source_url == second.url
    cached = good_deal("invented now $10.00").model_dump(mode="json")
    db.conn.execute("UPDATE post_parses SET deals=?", (json.dumps([cached]),))
    pairs, stats = parse_posts([second], parser, make_cfg(), NOW, {}, db=db)
    assert pairs == [] and stats["deals_dropped_evidence"] == 1
    assert stats["reused_posts"] == 1 and stats["reused_deals"] == 0
    assert parser.calls == []
    db.close()


@pytest.mark.parametrize("change", ["one_character", "expired", "changed_truncation"])
def test_different_or_old_model_text_is_never_reused(tmp_path, change):
    db = DB(tmp_path / "state.db")
    first = make_post(text="Widget was $20.00 now $10.00 trailing words")
    cfg = make_cfg(max_chars_per_post=100)
    parse_posts([first], FakeParser([[]]), cfg, NOW, {}, db=db)
    second = dataclasses.replace(first, source_name="other")
    at = NOW
    if change == "one_character":
        second.text += "!"
    elif change == "expired":
        at += timedelta(days=cfg.post_parses_days, seconds=1)
    else:
        cfg.max_chars_per_post = 28
    parser = FakeParser([[]])
    _, stats = parse_posts([second], parser, cfg, at, {}, db=db)
    assert len(parser.calls) == 1 and stats["reused_posts"] == 0
    db.close()


def test_empty_parse_is_reused_and_cache_keeps_its_own_short_retention(tmp_path):
    db = DB(tmp_path / "state.db")
    post = make_post()
    cfg = make_cfg(post_parses_days=2)
    parse_posts([post], FakeParser([[]]), cfg, NOW, {}, db=db)
    other = dataclasses.replace(post, source_name="other")
    _, stats = parse_posts([other], FakeParser(raise_cap_at=0), cfg,
                           NOW + timedelta(days=2), {}, db=db)
    assert stats["reused_posts"] == 1
    db.prune(NOW + timedelta(days=2, seconds=1), post_parses_days=2)
    assert db.conn.execute("SELECT COUNT(*) FROM post_parses").fetchone()[0] == 0
    db.close()


def test_reused_completed_batchmate_survives_another_posts_spend_cap():
    posts = [make_post(source_name="one"),
             make_post(source_name="two", text="Different post"),
             make_post(source_name="three")]
    parser = FakeParser([[good_deal()]], raise_cap_at=1)
    pairs, stats = parse_posts(posts, parser, make_cfg(parse_concurrency=3), NOW, {})
    assert stats["aborted_spend_cap"] is True
    assert [p.source_name for p, _ in pairs] == ["one", "three"]
    assert stats["processed_hashes"] == [posts[0].content_hash, posts[2].content_hash]
    assert stats["posts_sent"] == stats["reused_posts"] == 1


def test_rejected_parse_can_be_shared_in_memory_but_is_never_persisted(tmp_path):
    db = DB(tmp_path / "state.db")
    posts = [make_post(source_name=name) for name in ("one", "two")]
    parser = FakeParser([[good_deal("invented $10.00")]])
    pairs, stats = parse_posts(posts, parser, make_cfg(parse_concurrency=2), NOW, {}, db=db)
    assert pairs == [] and len(parser.calls) == 1
    assert stats["reused_posts"] == 1 and stats["deals_dropped_evidence"] == 2
    assert db.conn.execute("SELECT COUNT(*) FROM post_parses").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize("text,fires", [
    ("Widget 60% off", True), ("Widget 60 percent off", True), ("Widget $20 (reg $80)", True),
    ("Widget free", True), ("Widget now $0", True), ("Widget 100% off", True),
    ("Widget sale $45 (reg $50)", False), ("Widget sale now $45", False),
    ("Widget sale $20. Regular price $80.", False),
    ("Widget sale $20\nRegular price $80", False),
    ("Widget $1,000 (reg. $4,000)", True), ("Widget 59.9% off", False),
    ("Widget sale $0.10", False), ("Widget free shipping", False),
])
def test_second_opinion_requires_a_floor_signal(text, fires):
    cfg = make_cfg(min_pct=60, second_opinion_model="claude-sonnet-5-5")
    parser = SecondOpinionParser(cfg, first_pass=[[]], second_pass=[[]])
    _, stats = parse_posts([make_post(text=text)], parser, cfg, NOW, {})
    assert parser.kinds == ["extract"] + (["second_opinion"] if fires else [])
    assert stats["second_opinion"] == dict(calls=int(fires), deals_extracted=0, deals_passed_evidence=0)


def test_second_opinion_yield_counts_only_its_evidence_passes():
    cfg = make_cfg(min_pct=40, second_opinion_model="claude-sonnet-5-5")
    parser = SecondOpinionParser(cfg, first_pass=[[]], second_pass=[[
        good_deal(), good_deal("not in this post $10.00")]])
    _, stats = parse_posts([make_post(text="Widget was $20.00 now $10.00")],
                           parser, cfg, NOW, {})
    assert stats["second_opinion"] == dict(calls=1, deals_extracted=2, deals_passed_evidence=1)


@pytest.mark.parametrize("floor,fires", [(40, True), (70, False)])
def test_second_opinion_threshold_moves_with_min_pct(floor, fires):
    cfg = make_cfg(min_pct=floor, second_opinion_model="claude-sonnet-5-5")
    parser = SecondOpinionParser(cfg, first_pass=[[]], second_pass=[[]])
    _, stats = parse_posts([make_post(text="Widget 60% off")], parser, cfg, NOW, {})
    assert stats["second_opinion"]["calls"] == int(fires)


@pytest.mark.parametrize("text,candidate", [
    ("Widget was $50 now $45", True), ("Widget $45 (reg. $50)", True),
    ("Widget now $45 list price $50", True), ("Widget MSRP $50 sale price $45", True),
    ("Widget was $50 now $35", False), ("Widget was $50 now $10", False),
    ("Widget was $50 now $45 coupon", False), ("Widget was $50 now $45 code SAVE", False),
    ("Widget was $50 now $45 clip", False), ("Widget was $50 now $45 extra", False),
    ("Widget was $50 now $45 stack", False), ("Widget was $50 now $45 rebate", False),
    ("Widget was $50 now $45 Subscribe & Save", False),
    ("Widget was $50 now $45 subscription", False),
    ("Widget was $50 now $45, other item $1", False),
    ("Widget was $50 now $45; other was $5 now $1", False),
    ("Widget now $45, another now $50", False),
    ("Widget was $50, shipping $45", False), ("Widget was $50. Now $45", False),
])
def test_shadow_prefilter_only_counts_and_on_actually_skips(text, candidate):
    post = make_post(text=text)
    results = []
    for mode in ("off", "shadow", "on"):
        parser = FakeParser([[]])
        _, stats = parse_posts([post], parser,
                               make_cfg(min_pct=40, prefilter_below_floor=mode), NOW, {})
        assert len(parser.calls) == int(mode != "on" or not candidate)
        assert stats["prefilter"]["would_skip"] == int(mode != "off" and candidate)
        assert stats["processed_hashes"] == [post.content_hash]
        results.append(stats["deals_extracted"])
    assert results == [0, 0, 0]


def test_prefilter_never_skips_a_free_structured_post():
    from dealradar.parse.schema import LLMDeal

    deal = LLMDeal(item="Widget", store="Store", category="home", regular_price=50.,
                   final_price=45.,
                   evidence=dict(regular_price_quote="was $50", final_price_quote="now $45"))
    post = make_post(text="Widget was $50 now $45", structured=deal.model_dump(mode="json"))
    parser = FakeParser([])
    pairs, stats = parse_posts([post], parser,
                               make_cfg(min_pct=40, prefilter_below_floor="on"), NOW, {})
    assert parser.calls == [] and len(pairs) == 1
    assert stats["prefilter"]["would_skip"] == 0


def test_shadow_output_matches_off_and_lower_site_floor_preserves_useful_posts(env, monkeypatch):
    from dealradar.parse.schema import LLMDeal

    text = "PRIVATE BODY MARKER: Widget was $50 now $45"
    raw = LLMDeal(item="Widget", store="Store", category="home", online_only="true",
                  regular_price=50., final_price=45.,
                  evidence=dict(regular_price_quote="was $50", final_price_quote="now $45"))
    post = make_post(source_name="blogone", text=text)
    cfg = make_cfg(min_pct=40)
    off, _ = parse_posts([post], FakeParser([[raw]]), cfg, NOW, {})
    cfg.prefilter_below_floor = "shadow"
    shadow, stats = parse_posts([post], FakeParser([[raw]]), cfg, NOW, {})
    assert [dataclasses.asdict(d) for _, d in off] == [dataclasses.asdict(d) for _, d in shadow]
    assert stats["prefilter"]["would_skip"] == 1
    cfg_path = Path(env["cfg"])
    cfg_path.write_text(cfg_path.read_text().replace("site_show_from_pct: 40", "site_show_from_pct: 10"))
    messages = FakeMessagesWithParse(parse_responses=[FakeParseResponse(LLMDeals(deals=[raw]), FakeUsage(100, 50))])
    monkeypatch.setattr(pipeline, "_fetch_all", lambda *a, **kw: ([post], {}))
    db = DB(env["db"])
    db.meta_set("recategorized_v2", "1")
    db.close()
    summary = pipeline.run(db_path=env["db"], config_path=env["cfg"],
                           sources_override=SOURCES, skip_validation=True,
                           transport=httpx.MockTransport(_handler),
                           claude_client=SimpleNamespace(messages=messages), dry_run_telegram=True)
    assert summary["prefilter"] == dict(would_skip=0, would_skip_publishable=0,
                                        unevaluated=0, by_source={})
    assert summary["extract_by_source"]["blogone"]["publishable"] == 1
    db = DB(env["db"])
    assert "PRIVATE BODY MARKER" not in "\n".join(db.conn.iterdump())
    db.close()


def test_pipeline_reuse_records_two_credits_and_rerun_adds_no_rows(env, monkeypatch):
    from dealradar.parse.schema import LLMDeal

    posts = [make_post(source_name=s.name, url=s.url + "/post",
                       text="Widget was $80 now $20") for s in SOURCES]
    raw = LLMDeal(item="Widget", store="Store", category="home", online_only="true",
                  regular_price=80., final_price=20.,
                  evidence=dict(regular_price_quote="was $80", final_price_quote="now $20"))
    messages = FakeMessagesWithParse(parse_responses=[
        FakeParseResponse(LLMDeals(deals=[raw]), FakeUsage(100, 50)),
        FakeParseResponse(CriticVerdicts.model_validate({"verdicts": [
            dict(index=0, plausible=True, confidence=.9, reason="OK")]}), FakeUsage(100, 50))])
    monkeypatch.setattr(pipeline, "_fetch_all", lambda *a, **kw: (posts, {}))
    cfg_path = Path(env["cfg"])
    cfg_path.write_text(cfg_path.read_text().replace("critic_shadow_batches_per_day: 12",
                                                   "critic_shadow_batches_per_day: 0"))
    db = DB(env["db"])
    db.meta_set("recategorized_v2", "1")
    db.close()
    def run():
        return pipeline.run(db_path=env["db"], config_path=env["cfg"],
                            sources_override=SOURCES, skip_validation=True,
                            transport=httpx.MockTransport(_handler),
                            claude_client=SimpleNamespace(messages=messages), dry_run_telegram=True)
    first = run()
    assert first["parse"]["reused_posts"] == first["parse"]["reused_deals"] == 1
    assert first["spend_by_kind"]["extract"]["calls"] == 1
    published = json.loads((env["site"] / "deals.json").read_text())["deals"]
    assert len(published) == 1 and len(published[0]["credits"]) == 2
    assert first["extract_by_source"]["feedtwo"] == dict(calls=0, usd=0., deals=1, publishable=1)
    db = DB(env["db"])
    tables = ("deals", "sightings", "price_history", "posts_seen", "post_parses", "llm_calls")
    before = {t: db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    db.close()
    second = run()
    assert second["run_key"] == first["run_key"] and second["posts_new"] == 0
    assert second["parse"]["reused_posts"] == 0
    db = DB(env["db"])
    assert {t: db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables} == before
    db.close()


def test_summary_reports_evidence_and_post_critic_publishable_yield(env):
    summary = _run(env)
    assert summary["second_opinion"] == dict(calls=0, deals_extracted=0, deals_passed_evidence=0)
    assert summary["prefilter"] == dict(would_skip=0, would_skip_publishable=0,
                                        unevaluated=0, by_source={})
    assert set(summary["llm_cache_tokens"]) == {"read", "write", "uncached"}
    assert summary["parse"]["reused_posts"] == summary["parse"]["reused_deals"] == 0
    assert {s: (c["deals"], c["publishable"]) for s, c in summary["extract_by_source"].items()} == {
        "blogone": (4, 1), "feedtwo": (2, 2)}


def test_merged_better_price_does_not_inflate_shadow_or_source_yield(env, monkeypatch):
    from dealradar.parse.schema import LLMDeal

    posts = [make_post(source_name=source.name, text=f"Widget was $50 now ${price}")
             for source, price in zip(SOURCES, (45, 20))]
    raw = [LLMDeal(item="Widget", store="Store", category="home", online_only="true",
                   regular_price=50., final_price=price,
                   evidence=dict(regular_price_quote="was $50", final_price_quote=f"now ${price}"))
           for price in (45, 20)]
    class SightingMessages(FakeMessagesWithParse):
        def parse(self, **kwargs):
            if kwargs["output_format"] is LLMDeals:
                # Concurrent requests can arrive in either order: each fake
                # answer must quote THIS request's post, just like a real call.
                self.parse_calls.append(kwargs)
                text = kwargs["messages"][0]["content"]
                deal = next(r for r in raw if r.evidence.final_price_quote in text)
                return FakeParseResponse(LLMDeals(deals=[deal]), FakeUsage(100, 50))
            return super().parse(**kwargs)

    messages = SightingMessages(parse_responses=[
        FakeParseResponse(CriticVerdicts.model_validate({"verdicts": [
            dict(index=0, plausible=True, confidence=.9, reason="OK")]}), FakeUsage(100, 50))])
    monkeypatch.setattr(pipeline, "_fetch_all", lambda *a, **kw: (posts, {}))
    cfg_path = Path(env["cfg"])
    cfg_path.write_text(cfg_path.read_text().replace("critic_shadow_batches_per_day: 12",
                                                   "critic_shadow_batches_per_day: 0"))
    db = DB(env["db"])
    db.meta_set("recategorized_v2", "1")
    db.close()
    summary = pipeline.run(db_path=env["db"], config_path=env["cfg"],
                           sources_override=SOURCES, skip_validation=True,
                           transport=httpx.MockTransport(_handler),
                           claude_client=SimpleNamespace(messages=messages), dry_run_telegram=True)
    assert summary["published"] == 1
    assert summary["prefilter"] == dict(would_skip=1, would_skip_publishable=0,
        unevaluated=0, by_source={"blogone": dict(would_skip=1, would_skip_publishable=0,
                                                  unevaluated=0)})
    assert summary["extract_by_source"]["blogone"]["publishable"] == 0
    assert summary["extract_by_source"]["feedtwo"]["publishable"] == 1


@pytest.mark.parametrize("mode", ["off", "shadow", "on", "invalid"])
@pytest.mark.parametrize("quoted", [False, True])
def test_prefilter_config_modes(tmp_path, mode, quoted):
    path = tmp_path / "config.yaml"
    value = f'"{mode}"' if quoted else mode
    path.write_text(f'parse:\n  prefilter_below_floor: {value}\n')
    if mode == "invalid":
        with pytest.raises(ValueError, match="prefilter_below_floor"):
            load_config(path)
    else:
        assert load_config(path).prefilter_below_floor == mode
    assert load_config().prefilter_below_floor == "shadow"


def test_disabled_parser_still_reports_new_summary_keys():
    _, stats = pipeline._parse_new_posts([], None, load_config(), NOW, {}, None)
    assert stats["reused_posts"] == stats["reused_deals"] == 0
    assert stats["second_opinion"] == dict(calls=0, deals_extracted=0, deals_passed_evidence=0)
    assert stats["prefilter"] == dict(would_skip=0, would_skip_publishable=0,
                                      unevaluated=0, by_source={})
