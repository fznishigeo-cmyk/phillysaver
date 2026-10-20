"""Numeric heuristic limits and conservative admission, entirely offline."""
import dataclasses
import json
import lzma
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest
import yaml

from dealradar import pipeline
from dealradar.config import AppConfig, load_config, validated_bounded_prefilter
from dealradar.db import DB
from dealradar.parse import extractor
from dealradar.parse.bounded_prefilter import analyze
from dealradar.parse.schema import CriticVerdicts, LLMDeal, LLMDeals
from dealradar.publish import site
from tests.bounded_prefilter_fixture import capture
from tests.test_claude_client import FakeMessagesWithParse, FakeParseResponse, FakeUsage, make_parser
from tests.test_critic_tiering import ExtractionMessages, HAIKU, SONNET, seed_yield, tier_parser
from tests.test_e2e_offline import NOW, SOURCES, _handler, env, test_rerun_is_idempotent as check_rerun
from tests.test_extractor import FakeParser, good_deal, make_cfg, make_post
from tests.test_quality_trio import SecondOpinionParser


def config(**over):
    options = dict(prefilter_below_floor="on", min_pct=40, site_show_from_pct=40,
                   second_opinion_model=SONNET)
    cfg = make_cfg(**{**options, **over})
    cfg.bounded_prefilter["enabled"] = True
    return cfg


# Explicit ordinary decisions; the legacy comparison is small and benign.
ORDINARY = [
    ("Widget was $50 now $45", True, False),
    ("Widget $45 (reg. $50)", True, False),
    ("Widget now $45 list price $50", True, False),
    ("Widget MSRP $50 sale price $45", True, False),
    ("Widget was $1,000.00 now $900.50", True, False),
    ("Widget was $1,000,000 now $900,000", True, False),
    ("Widget was $50 now $35", False, False),
    ("Widget was $50 now $10", False, True),
    ("Widget 80% off", False, True),
    ("Widget 80 percent off", False, True),
    ("Widget 80percent off", False, True),
    ("Widget 80 PERCENT off", False, True),
    ("Widget 39.9% off", False, False),
    ("Widget 40.0 % off", False, True),
    ("Widget 100% off", False, True),
    ("Widget free", False, True),
    ("Widget free shipping", False, False),
    ("Widget free delivery", False, False),
    ("Widget free returns", False, False),
    ("Widget free trials", False, False),
    ("Widget now $0.00", False, True),
    ("Widget sale $0.10", False, False),
    ("Widget sale $20. Regular price $80.", False, False),
    ("Widget sale $20\nRegular price $80", False, False),
    ("Widget was $100! Now $90", False, False),
    ("Widget was $100? Now $90", False, False),
    ("Widget was $50 now $45 coupon", False, False),
    ("Widget was $50 now $45 code SAVE", False, False),
    ("Widget was $50 now $45 clip", False, False),
    ("Widget was $50 now $45 extra", False, False),
    ("Widget was $50 now $45 stack", False, False),
    ("Widget was $50 now $45 rebate", False, False),
    ("Widget was $50 now $45 Subscribe & Save", False, False),
    ("Widget was $50 now $45 S & S", False, False),
    ("Widget was $50 now $45 gift card", False, False),
    ("Widget was $50 now $45 as low as", False, False),
    ("Widget was $50 now $45 range", False, False),
    ("Widget was $50 now $45 BOGO", False, False),
    ("Widget was $50 now $45; other was $5 now $1", False, True),
    ("Widget was $50 now $45 10% off", False, False),
    ("Café was\u00a0$50 now\u00a0$45", True, False),
    ("Widget was $50 now $45\u2003", True, False),
    ("Widget was $50\t now $45", True, False),
    ("Widget was $50 now $45.", False, False),
    ("New recipe for banana bread", False, False),
    ("The stock rose 80% today", False, False),
]


@pytest.mark.parametrize("text,below,second", ORDINARY)
def test_known_ordinary_decisions_match_legacy(text, below, second):
    result = analyze(text, 40, config().bounded_prefilter)
    assert result.state == "known"
    assert result.below_floor is below
    assert result.second_opinion is second
    assert extractor._below_floor_post(text, 40) is below
    assert bool(extractor._smells_like_deal(text) and extractor._can_clear_floor(text, 40)) is second
    assert result.visited_chars == len(text)


@pytest.mark.parametrize("headline,site,price,below,second", [
    (70, 40, 60, False, True), (70, 40, 60.1, False, False),
    (40, 70, 70.1, True, False), (70, 10, 90, False, True)])
def test_enabled_uses_lower_floor_and_existing_savings_rounding(headline, site, price, below, second):
    cfg = config(min_pct=headline, site_show_from_pct=site)
    post = make_post(text=f"Widget was $100 now ${price}")
    parser = SecondOpinionParser(cfg, first_pass=[[]], second_pass=[[]])
    _, stats = extractor.parse_posts([post], parser, cfg, NOW, {})
    assert stats["prefilter"]["would_skip"] == int(below)
    assert stats["second_opinion"]["calls"] == int(second)


UNKNOWN = ["$1,00", "$10,0000", "$100..00", "$100.2.3", "$1,,000", "$100,",
    "$1e3", "$NaN", "$inf", "$Infinity", "$-90", "-$90", "$−90", "$ +90", "$ 90x",
    "$90-$100", "$90 - $100", "$90–$100", "$90 to $100", "$90/2", "$90+", "$90%",
    "-90%", "120%", "NaN%", "Infinity percent", "Model X200", "Model 200X", "X200%",
    "123-456", "$９０", "$٩٠", "$²", "$90percentile", "$90USD", "$0bad", "$9,99.99",
    "$90,000.0.0", "$ .90", "$90 000", "100$", "$90$100", "between $90 and $100", "＄90", "€90"]


@pytest.mark.parametrize("fragment", UNKNOWN)
def test_no_bad_number_can_leave_a_skippable_pair(fragment):
    # A valid low-discount pair before OR after the bad token stays eligible.
    for text in (f"Widget was $100 now $90 {fragment}", f"{fragment} Widget was $100 now $90"):
        result = analyze(text, 40, config().bounded_prefilter)
        assert result.state == "unknown", (fragment, result)
        assert not result.below_floor and result.second_opinion
        parser = SecondOpinionParser(config(), first_pass=[[]], second_pass=[[]])
        _, stats = extractor.parse_posts([make_post(text=text)], parser, parser.cfg, NOW, {})
        assert parser.kinds == ["extract", "second_opinion"]
        assert stats["prefilter"]["would_skip"] == 0
        assert stats["prefilter"]["bounded_prefilter"]["unknown"] == 1


@pytest.mark.parametrize("field,ceiling", [("max_chars", 100000), ("max_numeric_chars", 128),
    ("max_numeric_tokens", 4096), ("max_price_pairs", 65536)])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "32", None, float("nan"), float("inf")])
def test_invalid_limits(field, ceiling, value, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"parse": {"bounded_prefilter": {field: value}}}))
    with pytest.raises(ValueError, match="parse.bounded_prefilter"):
        load_config(path)
    with pytest.raises(ValueError, match="parse.bounded_prefilter"):
        validated_bounded_prefilter({field: ceiling + 1})


@pytest.mark.parametrize("value", [False, None, [], "on", {"enabled": "false"}, {"enabled": 1}, {"typo": 5}])
def test_invalid_switch_or_mapping(value):
    with pytest.raises(ValueError, match="parse.bounded_prefilter"):
        validated_bounded_prefilter(value)


def test_defaults_off_and_omission(tmp_path):
    assert AppConfig().bounded_prefilter == load_config().bounded_prefilter
    assert not load_config().bounded_prefilter["enabled"]
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    assert load_config(path).bounded_prefilter == AppConfig().bounded_prefilter


@pytest.mark.parametrize("setting", ["omitted", False])
@pytest.mark.parametrize("retention", [False, True])
@pytest.mark.usefixtures("pre_extension_privacy")
def test_disabled_exact_pre_edit_calls_reservations_decisions_and_four_public_trees(tmp_path, monkeypatch, setting, retention):
    baseline = json.loads(lzma.decompress((Path(__file__).parent / "fixtures/bounded_prefilter_baseline.json.xz").read_bytes()))
    assert capture(tmp_path, monkeypatch, setting, retention=retention) == baseline


@pytest.mark.parametrize("retention", [False, True])
@pytest.mark.usefixtures("pre_extension_privacy")
def test_enabled_adversarial_eligibility_change_is_capped_and_public_bytes_stay_identical(tmp_path, monkeypatch, retention):
    baseline = json.loads(lzma.decompress((Path(__file__).parent / "fixtures/bounded_prefilter_baseline.json.xz").read_bytes()))
    result = capture(tmp_path, monkeypatch, True, retention=retention)
    assert result["public"] == baseline["public"]
    assert result["pairs"] == baseline["pairs"]
    assert result["decisions"] == baseline["decisions"]
    # Malformed $1,00 is now unknown: one additional existing Haiku-routed
    # second opinion, under the same run/day reservations; no new call kind.
    assert len(result["calls"]) == len(baseline["calls"]) + 1
    assert result["spend_by_kind"]["second_opinion"]["calls"] == 2
    assert result["stats"]["prefilter"]["bounded_prefilter"]["unknown"] == 1
    assert result["spend"] > baseline["spend"]
    assert {r["kind"] for r in result["ledger"]} <= {"extract", "second_opinion", "critic"}


@pytest.mark.parametrize("size", [16, 32, 64, 512, 6000, 12000, 48000])
@pytest.mark.parametrize("family", ["digits", "decimal", "comma", "prices", "whitespace"])
def test_adversarial_work_bounds(size, family):
    text = {"digits": "9" * size, "decimal": "9." * size,
            "comma": "9," * size, "prices": "sale " + "$10 " * size,
            "whitespace": " " * size + "Widget was $100 now $90"}[family]
    limits = config().bounded_prefilter
    result = analyze(text, 40, limits)
    assert result == analyze(text, 40, limits)
    assert result.visited_chars <= min(len(text), limits["max_chars"])
    assert result.numeric_tokens <= limits["max_numeric_tokens"]
    assert result.price_pairs <= limits["max_price_pairs"]
    if family in {"digits", "decimal", "comma"} and size > 32:
        assert result.state == "exhausted" and result.reason == "token_length"
        assert result.visited_chars == 32
    if family == "prices" and size >= 512:
        assert result.state == "exhausted" and result.reason == "tokens"
    if result.state != "known":
        assert not result.below_floor and result.second_opinion


def test_exact_limits_and_all_pair_work_use_savings(monkeypatch):
    import dealradar.savings as savings
    calls = []
    original = savings.compute_savings
    def compute(deal):
        calls.append((deal.regular_price, deal.final_price))
        return original(deal)
    monkeypatch.setattr(savings, "compute_savings", compute)
    limits = config().bounded_prefilter
    assert analyze("9" * 32, 40, limits).state == "known"
    assert analyze("9" * 33, 40, limits).reason == "token_length"
    assert analyze("1 " * 128, 40, limits).state == "known"
    assert analyze("1 " * 129, 40, limits).reason == "tokens"
    assert analyze(" " * 12000, 40, limits).state == "known"
    assert analyze(" " * 12001, 40, limits).reason == "characters"
    assert analyze("sale " + "$10 " * 23, 40, limits).price_pairs == 253
    calls.clear()
    result = analyze("sale " + "$10 " * 24, 40, limits)
    assert result.state == "exhausted" and result.reason == "pairs"
    assert result.price_pairs == len(calls) == 256
    # Distinct sentences require no cross-sentence pair work.
    assert analyze("sale $10.\n" * 100, 40, limits).price_pairs == 0


def test_nonfinite_conversion_and_arithmetic_are_unknown(monkeypatch):
    limits = dict(config().bounded_prefilter, max_numeric_chars=128)
    # Invalid notation never lends its numeric prefix to a pair.
    assert analyze("sale $1e999", 40, limits).state == "unknown"
    monkeypatch.setattr(extractor, "_price_pair_pct", lambda *a: float("inf"))
    assert analyze("sale $10 was $100", 40, limits).state == "unknown"


@pytest.mark.parametrize("limit", ["characters", "token_length", "tokens", "pairs"])
def test_exhaustion_after_a_valid_pair_cannot_make_it_skippable(limit):
    cfg = config()
    prefix = "Widget was $100 now $90"
    if limit == "characters":
        cfg.bounded_prefilter["max_chars"] = len(prefix)
        text = prefix + " coupon"
    elif limit == "token_length":
        text = prefix + " " + "9" * 33
    elif limit == "tokens":
        cfg.bounded_prefilter["max_numeric_tokens"] = 2
        text = prefix + " 1"
    else:
        cfg.bounded_prefilter["max_price_pairs"] = 1
        text = prefix + " and $89"
    result = analyze(text, 40, cfg.bounded_prefilter)
    assert result.state == "exhausted" and result.reason == limit
    assert not result.below_floor and result.second_opinion
    parser = SecondOpinionParser(cfg, first_pass=[[]], second_pass=[[]])
    _, stats = extractor.parse_posts([make_post(text=text)], parser, cfg, NOW, {})
    assert parser.kinds == ["extract", "second_opinion"]
    assert stats["prefilter"]["would_skip"] == 0


def test_deadline_checks_inside_long_tokens_whitespace_and_pair_work(monkeypatch):
    from dealradar.parse import bounded_prefilter
    clock = [0]
    def tick():
        clock[0] += 1
        return clock[0]
    monkeypatch.setattr(bounded_prefilter.time, "monotonic", tick)
    for text in ("9" * 32, " " * 10000, "word " * 1000, "sale " + "$10 " * 24):
        clock[0] = 0
        result = analyze(text, 40, config().bounded_prefilter, deadline=20)
        assert result.state == "exhausted" and result.reason == "deadline"
        assert result.visited_chars <= 19


def test_pair_deadline_is_checked_between_evaluations(monkeypatch):
    clock = [0]
    monkeypatch.setattr(extractor.time, "monotonic", lambda: clock[0])
    original = extractor._price_pair_pct
    def compute(*args):
        clock[0] += 1
        return original(*args)
    monkeypatch.setattr(extractor, "_price_pair_pct", compute)
    result = analyze("sale " + "$10 " * 24, 40, config().bounded_prefilter, deadline=5)
    assert result.state == "exhausted" and result.reason == "deadline"
    assert result.price_pairs == clock[0] == 5


def test_enabled_heuristics_never_call_legacy_numeric_regexes(monkeypatch):
    class Forbidden:
        def search(self, *a):
            pytest.fail("legacy numeric regex in enabled heuristics")
        finditer = search
    for name in ("_DEAL_SMELL", "_POST_MONEY", "_POST_PCT", "_FREE_OFFER"):
        monkeypatch.setattr(extractor, name, Forbidden())
    cfg = config()
    parser = SecondOpinionParser(cfg, first_pass=[[]], second_pass=[[]])
    extractor.parse_posts([make_post(text="sale $1,00")], parser, cfg, NOW, {})
    assert parser.kinds == ["extract", "second_opinion"]


def test_many_ordinary_posts_at_cleaned_length_have_linear_character_counts():
    cfg = config()
    posts = [make_post(uid=str(i), text=f"Widget {i} was $100 now $90 " + "text " * 1300) for i in range(400)]
    counts = extractor.prefilter_post_counts(posts, cfg)
    diagnostics = extractor.prefilter_counts(posts + posts, counts)["bounded_prefilter"]
    assert diagnostics["posts"] == diagnostics["known"] == 400
    assert diagnostics["visited_chars"] == sum(len(extractor.clean_for_llm(p, 6000)) for p in posts)
    assert diagnostics["price_pairs"] == 400
    assert diagnostics["unknown"] == diagnostics["exhausted"] == 0


@pytest.mark.parametrize("stop", ["no_model", "opinion_cap", "deadline_before", "deadline_after", "max_posts"])
def test_unknown_keeps_existing_opportunity_gates(monkeypatch, stop):
    cfg = config()
    clock = [0.]
    monkeypatch.setattr(extractor.time, "monotonic", lambda: clock[0])
    if stop == "no_model":
        cfg.second_opinion_model = ""
    if stop == "opinion_cap":
        cfg.max_second_opinions_per_run = 0
    if stop == "max_posts":
        cfg.max_posts_per_run = 0
    parser = SecondOpinionParser(cfg, first_pass=[[]], second_pass=[[]])
    original = parser.extract
    def extract(*args, **kwargs):
        result = original(*args, **kwargs)
        if stop == "deadline_after":
            clock[0] = 60
        return result
    parser.extract = extract
    deadline = 10 if stop == "deadline_before" else 100
    _, stats = extractor.parse_posts([make_post(text="Widget $" + "9" * 100)], parser, cfg, NOW, {}, deadline=deadline)
    assert parser.kinds == ([] if stop in {"deadline_before", "max_posts"} else ["extract"])
    assert stats["prefilter"]["would_skip"] == 0


def test_unknowns_share_existing_per_run_second_opinion_limit():
    cfg = config(max_second_opinions_per_run=1, parse_concurrency=2)
    parser = SecondOpinionParser(cfg, first_pass=[[], []], second_pass=[[]])
    posts = [make_post(uid=str(i), text=f"Widget {i} $1,00") for i in range(2)]
    _, stats = extractor.parse_posts(posts, parser, cfg, NOW, {})
    assert parser.kinds.count("extract") == 2
    assert parser.kinds.count("second_opinion") == stats["second_opinion"]["calls"] == 1
    assert stats["prefilter"]["bounded_prefilter"]["unknown"] == 2


def test_unknown_with_unconfigured_parser_is_only_a_private_diagnostic():
    post = make_post(text="sale $1,00")
    pairs, stats = pipeline._parse_new_posts([post], None, config(), NOW, {}, None)
    assert pairs == [] and stats["posts_sent"] == stats["spend_usd"] == 0
    assert stats["prefilter"]["bounded_prefilter"]["unknown"] == 1


@pytest.mark.parametrize("stop", ["daily", "run", "pacing", "critic_reserve"])
def test_unknown_never_bypasses_real_spend_reservations(tmp_path, monkeypatch, stop):
    monkeypatch.setattr("dealradar.util.now_utc", lambda: NOW)
    cfg = config(parse_model=HAIKU, parse_structured_output=True,
                 model_pricing_per_mtok=load_config().model_pricing_per_mtok)
    cfg.spend_pacing = stop == "pacing"
    cfg.spend_cap_usd_per_day = .0000001 if stop in {"daily", "pacing"} else 6
    if stop == "pacing":
        cfg.spend_cap_usd_per_day = .1
        cfg.spend_pacing_headroom_hours = 0
    messages = FakeMessagesWithParse([FakeParseResponse(LLMDeals(deals=[]), FakeUsage(10, 10))] * 2)
    parser = make_parser(messages, cfg=cfg)
    if stop == "run":
        parser.run_cap_usd = .006  # Fits Haiku extraction, cannot reserve Sonnet.
    if stop == "critic_reserve":
        cfg.critic_reserve_share = .9
        parser.run_cap_usd = cfg.spend_cap_usd_per_run * (1 - cfg.critic_reserve_share)
    db = DB(tmp_path / "state.db")
    try:
        if stop == "pacing":
            db.reserve_llm_call("spent", "earlier", HAIKU, .06, NOW, {NOW.date().isoformat(): 1}, 1)
            db.settle_llm_call("spent", .06, NOW)
        parser.bind_ledger(db, "task224-capped")
        before = db.conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
        _, stats = extractor.parse_posts([make_post(text="sale $" + "9" * 100)], parser, cfg, NOW, {}, db=db)
        expected = int(stop in {"run", "critic_reserve"})
        assert stats["aborted_spend_cap"] and len(messages.parse_calls) == expected
        assert stats["second_opinion"]["calls"] == 0
        assert db.conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == before + expected
    finally:
        db.close()


@pytest.mark.parametrize("source,cap,expected", [("good", 1, SONNET), ("good", 0, HAIKU), ("poor", 1, HAIKU)])
def test_unknown_uses_existing_yield_selection_and_daily_opinion_cap(tmp_path, monkeypatch, source, cap, expected):
    monkeypatch.setattr("dealradar.util.now_utc", lambda: NOW)
    messages = ExtractionMessages()
    parser, _ = tier_parser(messages, second_opinion_calls_per_day=cap)
    parser.cfg.bounded_prefilter["enabled"] = True
    db = DB(tmp_path / "state.db")
    try:
        seed_yield(db, source, success=3 if source == "good" else 0)
        parser.bind_ledger(db, "unknown")
        _, stats = extractor.parse_posts([make_post(source_name=source, text="sale $1,00")], parser, parser.cfg, NOW, {}, db=db)
        assert [c["model"] for c in messages.calls] == [HAIKU, expected]
        assert stats["second_opinion"]["calls"] == 1
        before = list(db.conn.iterdump())
        extractor.parse_posts([make_post(source_name=source, text="sale $1,00")], parser, parser.cfg, NOW, {}, db=db)
        assert list(db.conn.iterdump()) == before
        assert len(messages.calls) == 2
    finally:
        db.close()


def test_structured_posts_and_evidence_stay_outside_heuristics(monkeypatch):
    from dealradar.parse import bounded_prefilter
    monkeypatch.setattr(bounded_prefilter, "analyze", lambda *a: pytest.fail("structured heuristic"))
    post = make_post(structured=good_deal().model_copy(update={"category": "home"}).model_dump(mode="json"))
    pairs, stats = extractor.parse_posts([post], FakeParser(), config(), NOW, {})
    assert len(pairs) == 1 and stats["structured_parse"]["posts"] == 1
    monkeypatch.undo()
    post = make_post(text="sale $1,00")
    parser = SecondOpinionParser(config(), first_pass=[[]], second_pass=[[good_deal()]])
    pairs, stats = extractor.parse_posts([post], parser, parser.cfg, NOW, {})
    assert not pairs and stats["deals_dropped_evidence"] == 1


@pytest.mark.parametrize("audit", ["shadow", "enforce"])
def test_enabled_offline_pipeline_rerun(env, monkeypatch, capsys, audit):
    path = Path(env["cfg"])
    path.write_text(path.read_text().replace("bounded_prefilter:\n    enabled: false", "bounded_prefilter:\n    enabled: true"))
    check_rerun(env, capsys, audit)


def test_unknown_failed_pipeline_repeat_and_later_resume_add_no_duplicate_paid_work(env, monkeypatch, caplog):
    path = Path(env["cfg"])
    cfg = load_config(path).raw
    cfg["parse"]["bounded_prefilter"]["enabled"] = True
    cfg["parse"]["max_posts_per_run"] = 1
    cfg["parse"]["concurrency"] = 1
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    db = DB(env["db"])
    db.meta_set("recategorized_v2", "1")  # This pre-existing migration has already run in production.
    db.close()
    posts = [make_post(uid=str(i), source_name="blogone", title=item,
        url=f"https://blogone.example/{i}", text=f"{item} was $100 now $20 X200")
        for i, item in enumerate(("Coffee grinder", "Garden table"))]
    monkeypatch.setattr(pipeline, "_fetch_all", lambda *a, **kw: (posts, {}))
    calls, attempts = [], {}
    class Messages:
        def parse(self, **kw):
            calls.append(kw)
            if kw["output_format"] is CriticVerdicts:
                entries = json.loads(kw["messages"][0]["content"])
                reply = CriticVerdicts(verdicts=[dict(index=e["index"], plausible=True,
                    confidence=.95, reason="offline") for e in entries])
            else:
                item = next(p.title for p in posts if p.title in kw["messages"][0]["content"])
                attempts[item] = attempts.get(item, 0) + 1
                deals = [] if attempts[item] == 1 else [LLMDeal(item=item, store="Walmart", category="home",
                    online_only="true", regular_price=100, final_price=20,
                    product_url="https://www.walmart.com/ip/" + item.replace(" ", "-"),
                    evidence=dict(regular_price_quote="was $100", final_price_quote="now $20"))]
                reply = LLMDeals(deals=deals)
            return FakeParseResponse(reply, FakeUsage(100, 30))
    client = SimpleNamespace(messages=Messages())
    def run():
        return pipeline.run(db_path=env["db"], config_path=env["cfg"], fast_lane=True,
            now=NOW, sources_override=[dataclasses.replace(SOURCES[0], fast_lane=True)],
            transport=httpx.MockTransport(_handler), skip_validation=True,
            claude_client=client, dry_run_telegram=True)
    render = site.render_site
    def fail_publish(*args, **kwargs):
        raise RuntimeError("injected publication failure")
    monkeypatch.setattr(site, "render_site", fail_publish)
    monkeypatch.setenv("DEALRADAR_RUN_KEY", "task224-failure")
    with pytest.raises(RuntimeError, match="injected publication failure"):
        run()
    tables = ("llm_calls", "llm_reservations", "deals", "sightings", "price_history", "posts_seen",
              "source_audit_posts", "ai_prefilter_posts", "second_opinion_yield", "alerts_sent")
    def snapshot():
        db = DB(env["db"])
        try:
            result = {}
            for table in tables:
                rows = [dict(row) for row in db.conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                if table == "deals":
                    # Recovery completes the first successful publication's
                    # anchors. They are not another parse/observation/charge.
                    for row in rows:
                        row.pop("ai_public_at")
                        row.pop("ai_public_price")
                result[table] = rows
            return result
        finally:
            db.close()
    before, paid = snapshot(), len(calls)
    db = DB(env["db"])
    assert db.conn.execute("SELECT parsed_at FROM source_audit_posts WHERE content_hash=?",
                           (posts[1].content_hash,)).fetchone()[0] is None
    db.close()
    monkeypatch.setattr(site, "render_site", render)
    repeated = run()
    assert repeated["spend_usd"] == 0 and len(calls) == paid
    assert snapshot() == before
    monkeypatch.setenv("DEALRADAR_RUN_KEY", "task224-resume")
    following = run()
    assert following["parse"]["posts_sent"] == 1
    assert following["second_opinion"]["calls"] == 1
    assert following["prefilter"]["bounded_prefilter"]["unknown"] == 1
    resumed, paid = snapshot(), len(calls)
    assert len(resumed["posts_seen"]) == len(before["posts_seen"]) + 1
    assert run()["spend_usd"] == 0 and len(calls) == paid
    assert snapshot() == resumed
    assert all("X200" not in str(rows) for rows in resumed.values())
    assert "source audit counters failed" not in caplog.text


def test_legacy_adversarial_observation_has_outer_process_timeout():
    # Legacy probes never run inline; timeout is an observation, not a claim
    # of exponential behavior. Output contains counts/times, never input text.
    script = """
import json, time
from dealradar.parse.extractor import _smells_like_deal, _can_clear_floor
start = time.perf_counter()
text = 'sale ' + '9' * int(__import__('sys').argv[1])
print(json.dumps({'smell': _smells_like_deal(text), 'clear': _can_clear_floor(text, 40), 'elapsed_s': time.perf_counter() - start}))
"""
    observations = []
    for size in (256, 1024, 4096):
        try:
            result = subprocess.run([sys.executable, "-c", script, str(size)], capture_output=True, text=True, timeout=2, check=True)
            row = json.loads(result.stdout)
            assert not row["smell"] and not row["clear"]
            observations.append(dict(size=size, **row))
        except subprocess.TimeoutExpired:
            observations.append(dict(size=size, timeout_s=2))
    print(json.dumps(observations))
