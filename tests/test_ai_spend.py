"""Offline contracts for batched judging and evidence-backed API parsing."""
import json
from types import SimpleNamespace

import pytest

from dealradar import pipeline
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.errors import SpendCapReached
from dealradar.fetch.keepa import KeepaDealsAdapter
from dealradar.fetch.woot import WootAdapter
from dealradar.parse.claude_client import ClaudeParser, categorize_titles, critique_deals
from dealradar.parse.extractor import parse_posts, verify_evidence
from dealradar.parse.schema import CriticVerdicts, LLMDeal, to_extracted
from tests.test_keepa import NOW, SRC, _amazon_row, _wh_row


class Messages:
    def __init__(self, reply=None, *, categories=None, cost=None):
        self.calls = []
        self.reply = reply
        self.categories = categories
        self.cost = cost or (1000, 100)

    def parse(self, **kw):
        self.calls.append(kw)
        entries = json.loads(kw["messages"][0]["content"])
        if kw["output_format"] is CriticVerdicts:
            data = self.reply if self.reply is not None else {"verdicts": [
                {"index": e["index"], "plausible": True, "confidence": .9,
                 "reason": "This regular price is plausible."} for e in entries]}
        return SimpleNamespace(parsed_output=data, stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=self.cost[0],
                                                     output_tokens=self.cost[1]))

    def create(self, **kw):
        assert 'Return ONLY a JSON array' in kw["system"], "structured posts must not invoke extraction"
        self.calls.append(kw)
        entries = json.loads(kw["messages"][0]["content"])
        cats = self.categories if self.categories is not None else ["home"] * len(entries)
        data = [{"i": e["i"], "category": cat} for e, cat in zip(entries, cats)]
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(data))],
                               stop_reason="end_turn", usage=SimpleNamespace(
                                   input_tokens=self.cost[0], output_tokens=self.cost[1]))


def parser_for(messages=None, **over):
    cfg = load_config()
    cfg.critic_shadow_batches_per_day = 0  # these tests isolate the real critic
    for key, value in over.items():
        setattr(cfg, key, value)
    messages = messages or Messages()
    return ClaudeParser(cfg, client=SimpleNamespace(messages=messages)), messages


def deal(key="target", **over):
    data = dict(dedupe_key=key, store="Amazon", item="Widget Pro", asin="B000000001",
                canonical_url="https://www.amazon.com/dp/B000000001",
                regular_price=100., final_price=25., pct_at_checkout=75.,
                abs_savings=75., online_only="true", region="US", currency="USD",
                status="alive", conditions=[], flags=[], category="home")
    data.update(over)
    return data


def insert(db, data):
    db.insert_deal(dedupe_key=data["dedupe_key"], store=data["store"], item=data["item"],
                   normalized_item=data["item"].lower(), canonical_url=data["canonical_url"],
                   payload=data, final_price=data["final_price"],
                   pct_at_checkout=data["pct_at_checkout"], abs_savings=data["abs_savings"],
                   online_only="true", expires_at="", now=NOW)
    if data["status"] != "alive":
        db.update_deal(data["dedupe_key"], status=data["status"])


def verdict(index, **over):
    return dict(index=index, plausible=True, confidence=.9, reason="OK", **over)


def test_three_verdicts_one_call_short_reason_and_model_accounting():
    reply = {"verdicts": [verdict(i) for i in range(3)]}
    reply["verdicts"][1]["reason"] = "a" * 250
    parser, msgs = parser_for(Messages(reply))
    result = critique_deals(parser, [deal() for _ in range(3)])
    assert len(result) == 3 and all(result)
    assert len(result[1]["reason"]) == 120
    assert len(msgs.calls) == 1
    assert msgs.calls[0]["max_tokens"] == 340
    assert msgs.calls[0]["model"] == parser.cfg.critic_model
    assert parser.spend_by_kind["critic"] == {"calls": 1, "usd": pytest.approx(.003)}


@pytest.mark.parametrize("reply,expected", [
    ({"verdicts": [verdict(2), verdict(0)]}, [True, False, True]),
    ({"verdicts": [verdict(0), verdict(0), verdict(1), verdict(99)]}, [False, True, False]),
    ({"verdicts": "broken"}, [False, False, False]),
    ({"verdicts": [{"index": "0", "plausible": True}]}, [False, False, False]),
])
def test_bad_indices_or_malformed_reply_leave_deals_unjudged(reply, expected):
    parser, _ = parser_for(Messages(reply))
    assert [v is not None for v in critique_deals(parser, [deal()] * 3)] == expected


@pytest.mark.parametrize("over,reused", [
    ({}, True),
    ({"regular_price": 102.}, True),
    ({"regular_price": 98.}, True),
    ({"asin": "", "canonical_url": "https://www.amazon.com/dp/B000000001"}, True),
    ({"asin": "B000000002", "canonical_url": "https://www.amazon.com/dp/B000000002"}, False),
    ({"conditions": ["used"]}, False),
    ({"conditions": ["open_box"]}, False),
    ({"conditions": ["refurbished"]}, False),
    ({"regular_price": 105.}, False),
    ({"store": "Woot", "canonical_url": "https://woot.com/offers/widget"}, False),
])
def test_verdict_reuse_is_same_asin_condition_and_basis_only(tmp_path, over, reused):
    db = DB(tmp_path / "state.db")
    insert(db, deal("origin", status="dead", critic=dict(plausible=True, confidence=.9, reason="OK")))
    insert(db, deal(**over))
    parser, msgs = parser_for()
    stats = pipeline._run_critic(db, parser.cfg, parser, NOW)
    assert stats == dict(judged=int(not reused), suspect=0, reused=int(reused), calls=int(not reused))
    assert len(msgs.calls) == int(not reused)
    assert (db.get_deal("target")["critic"].get("reused_from") == "origin") == reused
    db.close()


@pytest.mark.parametrize("confidence,rechecked,plausible,reused", [
    (.6, False, True, False), (.6, True, True, True), (.9, False, True, True),
    (.6, False, False, True),
])
def test_weak_pass_requires_recheck_and_reused_suspect_is_flagged(
        tmp_path, confidence, rechecked, plausible, reused):
    db = DB(tmp_path / "state.db")
    insert(db, deal("origin", status="dead", critic=dict(plausible=plausible,
        confidence=confidence, rechecked=rechecked, reason="basis judged")))
    insert(db, deal())
    parser, msgs = parser_for()
    stats = pipeline._run_critic(db, parser.cfg, parser, NOW)
    assert stats["reused"] == int(reused)
    assert len(msgs.calls) == int(not reused)
    assert ("suspect_msrp" in db.get_deal("target")["flags"]) == (reused and not plausible)
    db.close()


def test_batches_caps_and_missing_verdict_retry(tmp_path):
    db = DB(tmp_path / "state.db")
    for i in range(5):
        insert(db, deal(str(i), asin=f"B00000000{i}"))
    parser, msgs = parser_for(Messages(cost=(200000, 10000)), critic_batch_size=2)
    stats = pipeline._run_critic(db, parser.cfg, parser, NOW)
    assert stats == dict(judged=2, suspect=0, reused=0, calls=1)
    assert sum(d.get("critic") is None for d in db.all_deals()) == 3
    parser, msgs = parser_for(Messages({"verdicts": [verdict(0)]}))
    stats = pipeline._run_critic(db, parser.cfg, parser, NOW)
    assert stats["judged"] == 1
    assert sum(d.get("critic") is None for d in db.all_deals()) == 2
    parser, _ = parser_for()
    assert pipeline._run_critic(db, parser.cfg, parser, NOW)["judged"] == 2
    db.close()


def keepa_post(warehouse=False):
    if warehouse:
        return KeepaDealsAdapter._map_warehouse_row(_wh_row(1), SRC, NOW)
    return KeepaDealsAdapter._map_row(_amazon_row(1), SRC, NOW)


@pytest.mark.parametrize("warehouse", [False, True])
def test_keepa_matches_llm_path_and_verifies_with_no_extract(warehouse):
    post = keepa_post(warehouse)
    assert post is not None
    # A canned LLM extraction of the same synthesized source text.
    llm = LLMDeal(item=post.title, store="Amazon", online_only="true",
                  product_url="https://www.amazon.com/dp/" + post.structured["asin"],
                  regular_price=post.structured["regular_price"],
                  final_price=post.structured["final_price"],
                  conditions=["used", "open_box"] if warehouse else [],
                  evidence={"final_price_quote": post.text.splitlines()[1],
                            "regular_price_quote": post.text.splitlines()[2]})
    expected = to_extracted(llm, post, "UTC", NOW)
    parser, msgs = parser_for()
    pairs, stats = parse_posts([post], parser, parser.cfg, NOW, {})
    actual = pairs[0][1]
    for field in ("item", "store", "final_price", "regular_price", "conditions", "product_url"):
        assert getattr(actual, field) == getattr(expected, field)
    assert verify_evidence(actual, post.text) == (True, "")
    assert stats["structured_parse"] == dict(posts=1, deals=1, dropped_evidence=0)
    assert stats["posts_sent"] == 0
    assert parser.spend_by_kind["extract"]["calls"] == 0
    assert parser.extract_by_source[SRC.name] == dict(calls=0, usd=0.)
    assert 'Return ONLY a JSON array' in msgs.calls[0]["system"]


def test_altered_structured_quote_is_dropped_before_category_spend():
    post = keepa_post()
    post.structured["evidence"]["final_price_quote"] += " invented"
    parser, msgs = parser_for()
    pairs, stats = parse_posts([post], parser, parser.cfg, NOW, {})
    assert pairs == []
    assert stats["structured_parse"] == dict(posts=1, deals=0, dropped_evidence=1)
    assert msgs.calls == []


def woot_post(**over):
    item = dict(OfferId="1", Title="Widget", SalePrice=25., ListPrice=100., Condition="New",
                Url="https://www.woot.com/offers/widget", EndDate="2026-12-01T00:00:00Z")
    item.update(over)
    return WootAdapter._map_item(item, SRC, NOW)


@pytest.mark.parametrize("price", [[25., 26.], {"Minimum": 25., "Maximum": 26.}, "25 - 26", None])
def test_woot_range_or_missing_price_keeps_llm_read(price):
    post = woot_post(SalePrice=price)
    assert post.structured is None
    class Fake:
        spend_usd = 0.
        def extract(self, text, meta, **kw):
            self.called = text
            return []
    fake = Fake()
    parse_posts([post], fake, load_config(), NOW, {})
    assert fake.called == post.text


@pytest.mark.parametrize("condition,conditions", [("New", []), ("Open Box", ["used", "open_box"]),
                                                  ("Refurbished", ["used"]), ("", ["used"])])
def test_woot_plain_prices_parse_in_code(condition, conditions):
    post = woot_post(Condition=condition)
    parser, _ = parser_for()
    pairs, _ = parse_posts([post], parser, parser.cfg, NOW, {})
    extracted = pairs[0][1]
    assert (extracted.item, extracted.store, extracted.final_price, extracted.regular_price,
            extracted.conditions, extracted.product_url) == (
        "Widget", "Woot", 25., 100., conditions, post.url)
    assert extracted.expires_at == "2026-12-01T00:00:00+00:00"
    assert verify_evidence(extracted, post.text)[0]
    assert parser.spend_by_kind["extract"]["calls"] == 0


def test_category_reuse_and_single_title_only_batch(tmp_path):
    db = DB(tmp_path / "state.db")
    post = keepa_post()
    insert(db, deal("stored", status="dead", asin=post.structured["asin"], category="kitchen"))
    parser, msgs = parser_for(Messages(categories=["invalid", "toys_games"]))
    posts = [post, woot_post(), woot_post(OfferId="2", Title="Toy Train")]
    pairs, stats = parse_posts(posts, parser, parser.cfg, NOW, {}, db=db)
    assert [d.category for _, d in pairs] == ["kitchen", "other", "toys_games"]
    assert len(msgs.calls) == 2  # only the invalid first answer is retried
    assert json.loads(msgs.calls[0]["messages"][0]["content"]) == [
        {"i": 0, "title": "Widget"}, {"i": 1, "title": "Toy Train"}]
    assert msgs.calls[0]["model"] == parser.model
    assert parser.spend_by_kind["category"]["calls"] == 2
    db.close()


def test_category_reuse_costs_nothing(tmp_path):
    db = DB(tmp_path / "state.db")
    post = keepa_post()
    insert(db, deal("stored", asin="", canonical_url=post.structured["product_url"], category="kitchen"))
    parser, msgs = parser_for()
    pairs, _ = parse_posts([post], parser, parser.cfg, NOW, {}, db=db)
    assert pairs[0][1].category == "kitchen" and msgs.calls == []
    db.close()


def test_category_cap_propagates_and_parse_defaults_safely():
    parser, msgs = parser_for()
    parser.spend_usd = parser.run_cap_usd
    with pytest.raises(SpendCapReached):
        categorize_titles(parser, ["Widget"])
    pairs, stats = parse_posts([keepa_post()], parser, parser.cfg, NOW, {})
    assert pairs[0][1].category == "other"
    assert stats["aborted_spend_cap"] is True
    assert msgs.calls == []


def test_category_short_output_preserves_the_answer():
    parser, msgs = parser_for(Messages(categories=["home"]))
    assert categorize_titles(parser, ["Widget", "Toy"]) == ["home", "home"]
    assert len(msgs.calls) == 2


def test_one_category_call_at_most_sixty_titles():
    parser, msgs = parser_for()
    posts = [woot_post(OfferId=str(i), Title=f"Widget {i}") for i in range(65)]
    pairs, stats = parse_posts(posts, parser, parser.cfg, NOW, {})
    assert len(msgs.calls) == 1
    assert len(json.loads(msgs.calls[0]["messages"][0]["content"])) == 60
    assert [d.category for _, d in pairs[60:]] == ["other"] * 5


def test_failed_calls_default_without_routing_around_caps(monkeypatch):
    parser, msgs = parser_for()
    monkeypatch.setattr(parser, "_request", lambda fn: None)
    assert critique_deals(parser, [deal()]) == [None]
    assert categorize_titles(parser, ["Widget"]) == ["other"]
    assert msgs.calls == []


def test_structured_ingest_rerun_is_idempotent_and_stores_no_raw_facts(tmp_path):
    from dealradar.dedupe import upsert_extracted
    from dealradar.savings import compute_savings

    db = DB(tmp_path / "state.db")
    post = keepa_post()
    parser, msgs = parser_for()
    pairs, stats = parse_posts([post], parser, parser.cfg, NOW, {}, db=db)
    for raw, extracted in pairs:
        savings = compute_savings(extracted)
        upsert_extracted(db, extracted, savings, raw, parser.cfg, NOW)
        assert db.record_post(raw, NOW)
    first = db.all_deals()
    assert first and first[0]["asin"] == post.structured["asin"]
    assert "structured" not in first[0] and "text" not in first[0]
    assert db.post_is_known(post.content_hash)
    # Same filter as run(): the second run submits no known posts.
    remaining = [p for p in [post] if not db.post_is_known(p.content_hash)]
    before = len(msgs.calls)
    pairs, stats = parse_posts(remaining, parser, parser.cfg, NOW, {}, db=db)
    assert pairs == [] and stats["processed_hashes"] == []
    assert db.all_deals() == first and len(msgs.calls) == before
    db.close()
