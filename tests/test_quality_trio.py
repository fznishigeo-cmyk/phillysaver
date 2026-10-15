"""Tests for the quality trio: deal critic, category tags, second opinions.

Offline only — fake clients/parsers, no network, no real model calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from tests.http_helpers import mock_keepa_get, api_transport

import pytest

from dealradar import pipeline, rank
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.dedupe import _new_payload, upsert_extracted
from dealradar.errors import SpendCapReached
from dealradar.models import ExtractedDeal, RawPost, SavingsResult
from dealradar.parse.claude_client import ClaudeParser, critique_deal
from dealradar.parse.extractor import _smells_like_deal, parse_posts
from dealradar.parse.schema import CriticVerdicts, LLMDeal, LLMEvidence, to_extracted

NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------- fakes

class _FakeModel:
    def __init__(self, id: str, created_at: str) -> None:
        self.id = id
        self.created_at = created_at


class _FakeModels:
    def list(self):
        return [_FakeModel("claude-haiku-5-5", "2026-02-01")]


class _Usage:
    input_tokens = 1000
    output_tokens = 100


class _FakeResponse:
    def __init__(self, parsed_output) -> None:
        self.parsed_output = parsed_output
        self.usage = _Usage()
        self.stop_reason = "end_turn"
        self.content = []


class _CriticMessages:
    """messages.parse stub answering CriticVerdicts calls; records models used."""

    def __init__(self, plausible: bool = True) -> None:
        self.plausible = plausible
        self.calls: list[dict] = []

    def parse(self, *, model, max_tokens, system, messages, output_format, **kw):
        self.calls.append({"model": model, "output_format": output_format,
                           "text": messages[0]["content"]})
        if output_format is CriticVerdicts:
            entries = json.loads(messages[0]["content"])
            return _FakeResponse(CriticVerdicts.model_validate({"verdicts": [
                {"index": entry["index"], "plausible": self.plausible,
                 "confidence": 0.8, "reason": "stub"} for entry in entries]}))
        return _FakeResponse(output_format.model_validate({"deals": []}))


class _FakeClient:
    def __init__(self, messages) -> None:
        self.models = _FakeModels()
        self.messages = messages


def _cfg(**over) -> AppConfig:
    cfg = load_config()
    cfg.critic_enabled = True
    cfg.critic_model = "claude-sonnet-5-5"
    cfg.critic_shadow_batches_per_day = 0  # isolate the real critic
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def _deal_dict(**over) -> dict:
    base = {
        "dedupe_key": "si|acme|widget", "store": "Acme", "item": "Widget Pro",
        "regular_price": 100.0, "final_price": 25.0, "pct_at_checkout": 75.0,
        "abs_savings": 75.0, "online_only": "true", "currency": "USD",
        "region": "US", "status": "alive", "flags": [], "conditions": [],
        "category": "home", "verify_tier": "unverified",
        "first_seen_at": "2026-10-10T11:00:00+00:00",
    }
    base.update(over)
    return base


# --------------------------------------------------------------- critique_deal

class TestCritiqueDeal:
    def test_returns_verdict_dict_and_uses_critic_model(self):
        msgs = _CriticMessages(plausible=False)
        cfg = _cfg()
        parser = ClaudeParser(cfg, client=_FakeClient(msgs))
        v = critique_deal(parser, _deal_dict())
        assert v == {"plausible": False, "confidence": 0.8, "reason": "stub"}
        assert msgs.calls[0]["model"] == "claude-sonnet-5-5"
        assert "Widget Pro" in msgs.calls[0]["text"]

    def test_disabled_or_missing_prices_mean_no_opinion(self):
        msgs = _CriticMessages()
        parser = ClaudeParser(_cfg(critic_enabled=False), client=_FakeClient(msgs))
        assert critique_deal(parser, _deal_dict()) is None
        parser2 = ClaudeParser(_cfg(), client=_FakeClient(msgs))
        assert critique_deal(parser2, _deal_dict(regular_price=None)) is None
        assert critique_deal(parser2, _deal_dict(final_price=None)) is None
        assert msgs.calls == []  # never called the API

    def test_spend_accounted_at_critic_model_pricing(self):
        msgs = _CriticMessages()
        cfg = _cfg()
        cfg.model_pricing_per_mtok = {
            "claude-haiku-5-5": {"input": 0.10, "output": 0.50},
            "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
        }
        parser = ClaudeParser(cfg, client=_FakeClient(msgs))
        critique_deal(parser, _deal_dict())
        # 1000 in / 100 out at SONNET rates, not the default haiku model's.
        assert parser.spend_usd == pytest.approx((1000 * 2 + 100 * 10) / 1e6)
        assert parser.spend_by_kind["critic"] == {
            "calls": 1, "usd": pytest.approx(parser.spend_usd)}
        assert parser.extract_by_source == {}

    def test_malformed_parsed_output_means_no_opinion(self):
        class _OddMessages:
            def parse(self, **kw):
                return _FakeResponse(object())  # no .plausible

        parser = ClaudeParser(_cfg(), client=_FakeClient(_OddMessages()))
        assert critique_deal(parser, _deal_dict()) is None


# --------------------------------------------------------------- _run_critic

class TestRunCritic:
    def _db_with_deal(self, tmp_path, **over) -> DB:
        db = DB(tmp_path / "state.db")
        d = _deal_dict(**over)
        db.insert_deal(
            dedupe_key=d["dedupe_key"], store=d["store"], item=d["item"],
            normalized_item="widget pro", canonical_url="", payload=d,
            final_price=d["final_price"], pct_at_checkout=d["pct_at_checkout"],
            abs_savings=d["abs_savings"], online_only=d["online_only"],
            expires_at="", now=NOW)
        return db

    def test_implausible_verdict_flags_suspect_msrp(self, tmp_path):
        db = self._db_with_deal(tmp_path)
        cfg = _cfg()
        parser = ClaudeParser(cfg, client=_FakeClient(_CriticMessages(False)))
        stats = pipeline._run_critic(db, cfg, parser, NOW)
        assert stats == {"judged": 1, "suspect": 1,
                       "reused": 0, "calls": 1}
        row = db.get_deal("si|acme|widget")
        assert "suspect_msrp" in row["flags"]
        assert row["critic"]["plausible"] is False
        db.close()

    def test_plausible_verdict_persists_and_is_not_rejudged(self, tmp_path):
        db = self._db_with_deal(tmp_path)
        cfg = _cfg()
        msgs = _CriticMessages(True)
        parser = ClaudeParser(cfg, client=_FakeClient(msgs))
        assert pipeline._run_critic(db, cfg, parser, NOW) == {"judged": 1, "suspect": 0,
                       "reused": 0, "calls": 1}
        row = db.get_deal("si|acme|widget")
        assert row["critic"]["plausible"] is True
        assert "suspect_msrp" not in row["flags"]
        # Second pass: verdict stored -> zero further API calls.
        assert pipeline._run_critic(db, cfg, parser, NOW) == {"judged": 0, "suspect": 0,
                       "reused": 0, "calls": 0}
        assert len(msgs.calls) == 1
        db.close()

    def test_unpublishable_deals_never_cost_a_critic_call(self, tmp_path):
        db = self._db_with_deal(tmp_path, pct_at_checkout=30.0)  # below floor
        cfg = _cfg()
        msgs = _CriticMessages()
        parser = ClaudeParser(cfg, client=_FakeClient(msgs))
        assert pipeline._run_critic(db, cfg, parser, NOW) == {"judged": 0, "suspect": 0,
                       "reused": 0, "calls": 0}
        assert msgs.calls == []
        db.close()

    def test_no_parser_or_disabled_means_noop(self, tmp_path):
        db = self._db_with_deal(tmp_path)
        assert pipeline._run_critic(db, _cfg(), None, NOW) == {"judged": 0, "suspect": 0,
                       "reused": 0, "calls": 0}
        cfg_off = _cfg(critic_enabled=False)
        parser = ClaudeParser(cfg_off, client=_FakeClient(_CriticMessages()))
        assert pipeline._run_critic(db, cfg_off, parser, NOW) == {"judged": 0, "suspect": 0,
                       "reused": 0, "calls": 0}
        db.close()

    def test_low_confidence_pass_is_rejudged_exactly_once(self, tmp_path):
        """Live incident: junk 95%+ 'deals' slid past the critic on weak
        plausible verdicts (0.35-0.6). A <0.7 'plausible' gets exactly one
        second look; a flip to implausible flags it off the page."""
        db = self._db_with_deal(
            tmp_path, critic={"plausible": True, "confidence": 0.6,
                              "reason": "brand logic"})
        cfg = _cfg()
        msgs = _CriticMessages(plausible=False)  # the recheck flips it
        parser = ClaudeParser(cfg, client=_FakeClient(msgs))
        assert pipeline._run_critic(db, cfg, parser, NOW) == {"judged": 1, "suspect": 1,
                       "reused": 0, "calls": 1}
        row = db.get_deal("si|acme|widget")
        assert row["critic"]["rechecked"] is True
        assert "suspect_msrp" in row["flags"]
        # Settled now: no third opinion ever.
        assert pipeline._run_critic(db, cfg, parser, NOW) == {"judged": 0, "suspect": 0,
                       "reused": 0, "calls": 0}
        assert len(msgs.calls) == 1
        db.close()

    def test_confident_pass_is_never_rejudged(self, tmp_path):
        db = self._db_with_deal(
            tmp_path, critic={"plausible": True, "confidence": 0.9,
                              "reason": "solid"})
        cfg = _cfg()
        msgs = _CriticMessages(plausible=False)
        parser = ClaudeParser(cfg, client=_FakeClient(msgs))
        assert pipeline._run_critic(db, cfg, parser, NOW) == {"judged": 0, "suspect": 0,
                       "reused": 0, "calls": 0}
        assert msgs.calls == []
        db.close()

    def test_spend_cap_stops_the_critic_cleanly(self, tmp_path):
        db = self._db_with_deal(tmp_path)
        cfg = _cfg()
        parser = ClaudeParser(cfg, client=_FakeClient(_CriticMessages()))
        parser.spend_usd = parser.run_cap_usd  # cap already reached
        assert pipeline._run_critic(db, cfg, parser, NOW) == {"judged": 0, "suspect": 0,
                       "reused": 0, "calls": 0}
        assert db.get_deal("si|acme|widget").get("critic") is None
        db.close()


# --------------------------------------------------------------- publish gate

class TestSuspectGate:
    def test_suspect_flag_blocks_publication(self):
        cfg = _cfg(exclude_suspect_msrp=True)
        ok, reason = rank.is_publishable(_deal_dict(flags=["suspect_msrp"]), cfg)
        assert not ok
        assert "suspect_msrp" in reason

    def test_gate_can_be_disabled(self):
        cfg = _cfg(exclude_suspect_msrp=False)
        ok, _ = rank.is_publishable(_deal_dict(flags=["suspect_msrp"]), cfg)
        assert ok

    def test_used_deals_need_the_higher_floor_everywhere(self):
        cfg = _cfg(min_pct=65.0, used_min_pct=80.0, site_show_from_pct=50.0)
        used72 = _deal_dict(pct_at_checkout=72.0, conditions=["used"])
        used85 = _deal_dict(pct_at_checkout=85.0, conditions=["open_box"])
        new72 = _deal_dict(pct_at_checkout=72.0)
        ok, reason = rank.is_publishable(used72, cfg)
        assert not ok and "used_min_pct" in reason
        assert rank.is_publishable(used85, cfg)[0]
        assert rank.is_publishable(new72, cfg)[0]  # new deals keep 65
        # The site's lower "strong deals" tier never softens the used floor.
        ok, reason = rank.is_publishable(used72, cfg, min_pct=50.0)
        assert not ok and "used_min_pct" in reason


# --------------------------------------------------------------- categories

def _llm_deal(**over) -> LLMDeal:
    kw = dict(item="Widget", store="Acme", online_only="true",
              final_price=10.0, category="kitchen",
              evidence=LLMEvidence(final_price_quote="now $10.00"))
    kw.update(over)
    return LLMDeal(**kw)


def _post(**over) -> RawPost:
    kw = dict(source_name="blog", uid="u1", url="https://blog.example/p1",
              title="t", text="Widget deal now $10.00", published_at=NOW,
              fetched_at=NOW)
    kw.update(over)
    return RawPost(**kw)


class TestCategory:
    def test_to_extracted_carries_category(self):
        d = to_extracted(_llm_deal(), _post(), "America/New_York", NOW)
        assert d.category == "kitchen"

    def test_new_payload_carries_category(self):
        extracted = to_extracted(_llm_deal(), _post(), "America/New_York", NOW)
        sav = SavingsResult(computable=True, final_price=10.0,
                            regular_price=40.0, pct_at_checkout=75.0,
                            abs_savings=30.0, basis="quoted_regular")
        payload = _new_payload("k", extracted, sav, "", NOW)
        assert payload["category"] == "kitchen"
        assert payload["critic"] is None

    def test_merge_keeps_first_concrete_category(self, tmp_path):
        db = DB(tmp_path / "state.db")
        cfg = _cfg()
        sav = SavingsResult(computable=True, final_price=10.0,
                            regular_price=40.0, pct_at_checkout=75.0,
                            abs_savings=30.0, basis="quoted_regular")
        first = to_extracted(_llm_deal(), _post(), "America/New_York", NOW)
        upsert_extracted(db, first, sav, _post(), cfg, NOW)
        # Same deal sighted again, categorized 'other': kitchen must stick.
        again = to_extracted(_llm_deal(category="other"),
                             _post(uid="u2", url="https://blog.example/p2"),
                             "America/New_York", NOW)
        res = upsert_extracted(db, again, sav,
                               _post(uid="u2", url="https://blog.example/p2",
                                     text="Widget deal now $10.00 again"),
                               cfg, NOW)
        assert res["is_new_deal"] is False
        assert res["payload"]["category"] == "kitchen"
        db.close()

    def test_image_url_flows_from_post_to_payload_and_sticks(self, tmp_path):
        db = DB(tmp_path / "state.db")
        cfg = _cfg()
        sav = SavingsResult(computable=True, final_price=10.0,
                            regular_price=40.0, pct_at_checkout=75.0,
                            abs_savings=30.0, basis="quoted_regular")
        img = "https://m.media-amazon.com/images/I/61thing.jpg"
        first = to_extracted(_llm_deal(), _post(), "America/New_York", NOW)
        res = upsert_extracted(db, first, sav, _post(image_url=img), cfg, NOW)
        assert res["payload"]["image_url"] == img
        # A later sighting without an image never blanks the stored one.
        again = to_extracted(_llm_deal(), _post(uid="u9"), "America/New_York",
                             NOW)
        res2 = upsert_extracted(
            db, again, sav,
            _post(uid="u9", url="https://blog.example/p9",
                  text="Widget deal now $10.00 once more"),
            cfg, NOW)
        assert res2["payload"]["image_url"] == img
        db.close()

    def test_shared_code_merges_title_variants(self, tmp_path):
        """Live wart: one Amazon deal, two Slickdeals feeds, two title
        spellings — '(S-XL, Black)' vs '(Black, various sizes)' — showing as
        two rows. A shared distinctive code lowers the merge bar."""
        db = DB(tmp_path / "state.db")
        cfg = _cfg()
        sav = SavingsResult(computable=True, final_price=12.99,
                            regular_price=25.99, pct_at_checkout=50.0,
                            abs_savings=13.0, basis="quoted_regular")
        a = to_extracted(
            _llm_deal(item="Duoorra Lower Back Brace w/ Ergonomic Curve "
                           "Support Stays (S-XL, Black)",
                      codes=["8XZM0N80"], final_price=12.99,
                      evidence=LLMEvidence(final_price_quote="is $12.99")),
            _post(text="brace deal is $12.99"), "America/New_York", NOW)
        upsert_extracted(db, a, sav, _post(text="brace deal is $12.99"),
                         cfg, NOW)
        b = to_extracted(
            _llm_deal(item="Duoorra Lower Back Brace w/ Ergonomic Curve "
                           "Support (Black, various sizes)",
                      codes=["8XZM0N80"], final_price=12.99,
                      evidence=LLMEvidence(final_price_quote="is $12.99")),
            _post(uid="u2", url="https://blog.example/p2"),
            "America/New_York", NOW)
        res = upsert_extracted(
            db, b, sav, _post(uid="u2", url="https://blog.example/p2",
                              text="brace deal again is $12.99"), cfg, NOW)
        assert res["is_new_deal"] is False  # merged, not a second row
        assert len(db.all_deals()) == 1
        db.close()

    def test_storewide_code_never_merges_different_items(self, tmp_path):
        """EXTRA20-style store-wide codes must not glue distinct products."""
        db = DB(tmp_path / "state.db")
        cfg = _cfg()
        sav = SavingsResult(computable=True, final_price=71.99,
                            regular_price=199.99, pct_at_checkout=64.0,
                            abs_savings=128.0, basis="quoted_regular")
        mixer = to_extracted(
            _llm_deal(item="Martha Stewart 5.3-Quart Stand Mixer",
                      store="Kohl's", codes=["EXTRA20", "HOME10"],
                      final_price=71.99,
                      evidence=LLMEvidence(final_price_quote="is $71.99")),
            _post(text="mixer is $71.99"), "America/New_York", NOW)
        upsert_extracted(db, mixer, sav, _post(text="mixer is $71.99"),
                         cfg, NOW)
        pans = to_extracted(
            _llm_deal(item="Martha Stewart Lockton 10-Piece Ceramic "
                           "Nonstick Cookware Set",
                      store="Kohl's", codes=["EXTRA20", "HOME10"],
                      final_price=71.99,
                      evidence=LLMEvidence(final_price_quote="is $71.99")),
            _post(uid="u2", url="https://blog.example/p2"),
            "America/New_York", NOW)
        res = upsert_extracted(
            db, pans, sav, _post(uid="u2", url="https://blog.example/p2",
                                 text="cookware is $71.99"), cfg, NOW)
        assert res["is_new_deal"] is True   # different products stay apart
        assert len(db.all_deals()) == 2
        db.close()

    def test_price_improvement_clears_stale_critic_verdict(self, tmp_path):
        db = DB(tmp_path / "state.db")
        cfg = _cfg()
        sav = SavingsResult(computable=True, final_price=10.0,
                            regular_price=40.0, pct_at_checkout=75.0,
                            abs_savings=30.0, basis="quoted_regular")
        first = to_extracted(_llm_deal(), _post(), "America/New_York", NOW)
        res = upsert_extracted(db, first, sav, _post(), cfg, NOW)
        payload = res["payload"]
        payload["critic"] = {"plausible": True, "confidence": 0.9, "reason": "r"}
        db.update_deal(res["dedupe_key"], payload=payload)
        # A better price arrives: the stored verdict judged stale prices.
        sav2 = SavingsResult(computable=True, final_price=8.0,
                             regular_price=40.0, pct_at_checkout=80.0,
                             abs_savings=32.0, basis="quoted_regular")
        better = to_extracted(
            _llm_deal(final_price=8.0,
                      evidence=LLMEvidence(final_price_quote="now $8.00")),
            _post(uid="u3", url="https://blog.example/p3"),
            "America/New_York", NOW)
        res2 = upsert_extracted(db, better, sav2,
                                _post(uid="u3", url="https://blog.example/p3",
                                      text="Widget deal now $8.00"),
                                cfg, NOW)
        assert res2["price_improved"] is True
        assert res2["payload"]["critic"] is None
        db.close()


# --------------------------------------------------------------- second opinion

class SecondOpinionParser:
    """FakeParser that supports the model= override of the real one."""

    def __init__(self, cfg, first_pass=None, second_pass=None):
        self.cfg = cfg
        self.spend_usd = 0.0
        self.calls: list[str | None] = []  # the model each call used
        self.kinds: list[str] = []
        self._first = list(first_pass or [])
        self._second = list(second_pass or [])

    def extract(self, text, meta, model=None, *, kind="extract"):
        self.calls.append(model)
        self.kinds.append(kind)
        pool = self._second if model else self._first
        return pool.pop(0) if pool else []


def _socfg(**over) -> AppConfig:
    cfg = AppConfig()
    cfg.second_opinion_model = "claude-sonnet-5-5"
    cfg.max_second_opinions_per_run = 2
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


class TestSecondOpinion:
    def test_smell_detector(self):
        assert _smells_like_deal("Huge sale: widget now $10")
        assert _smells_like_deal("save 80% today")
        assert not _smells_like_deal("new recipe for banana bread")
        assert not _smells_like_deal("the stock rose 3% today")  # no deal words
        assert not _smells_like_deal("great deal on widgets")    # no figures

    def test_second_opinion_rescues_a_missed_deal(self):
        cfg = _socfg()
        rescued = _llm_deal(item="Missed Widget",
                            evidence=LLMEvidence(final_price_quote="now $10.00"))
        parser = SecondOpinionParser(cfg, first_pass=[[]],
                                     second_pass=[[rescued]])
        posts = [_post(text="Huge deal! Widget sale now $10.00 (was $40)")]
        pairs, stats = parse_posts(posts, parser, cfg, NOW, {})
        assert stats["second_opinions"] == 1
        assert parser.calls == [None, "claude-sonnet-5-5"]
        assert parser.kinds == ["extract", "second_opinion"]
        assert len(pairs) == 1
        assert pairs[0][1].item == "Missed Widget"

    def test_no_second_call_when_first_pass_found_deals(self):
        cfg = _socfg()
        parser = SecondOpinionParser(cfg, first_pass=[[_llm_deal()]])
        posts = [_post(text="Huge deal! Widget sale now $10.00")]
        _, stats = parse_posts(posts, parser, cfg, NOW, {})
        assert stats["second_opinions"] == 0
        assert parser.calls == [None]

    def test_no_second_call_for_non_deal_posts(self):
        cfg = _socfg()
        parser = SecondOpinionParser(cfg, first_pass=[[]])
        posts = [_post(text="our weekly newsletter and musings")]
        _, stats = parse_posts(posts, parser, cfg, NOW, {})
        assert stats["second_opinions"] == 0
        assert parser.calls == [None]

    def test_per_run_cap_respected(self):
        cfg = _socfg(max_second_opinions_per_run=2)
        posts = [_post(uid=f"u{i}", url=f"https://blog.example/p{i}",
                       text=f"Flash sale #{i}: widget now $1{i}.00 (was $80)")
                 for i in range(4)]
        parser = SecondOpinionParser(cfg, first_pass=[[], [], [], []])
        _, stats = parse_posts(posts, parser, cfg, NOW, {})
        assert stats["second_opinions"] == 2
        assert parser.calls.count("claude-sonnet-5-5") == 2

    def test_disabled_when_model_unset(self):
        cfg = _socfg(second_opinion_model="")
        parser = SecondOpinionParser(cfg, first_pass=[[]])
        posts = [_post(text="Huge deal! Widget sale now $10.00")]
        _, stats = parse_posts(posts, parser, cfg, NOW, {})
        assert stats["second_opinions"] == 0
        assert parser.calls == [None]


# ------------------------------------------------------- schema-rejection flag

class TestParseUnsupportedFlag:
    def test_slow_schema_rejection_paid_once_per_run(self):
        """Live run 14: messages.parse returned 'Schema is too complex' after
        ~3 minutes, for EVERY post. After one rejection the parser must go
        straight to the JSON fallback for the rest of the run."""
        import anthropic
        import httpx

        class _Msgs:
            def __init__(self):
                self.parse_calls = 0
                self.create_calls = 0

            def parse(self, **kw):
                self.parse_calls += 1
                raise anthropic.BadRequestError(
                    "Schema is too complex.",
                    response=httpx.Response(
                        400, request=httpx.Request("POST", "https://x")),
                    body=None)

            def create(self, **kw):
                self.create_calls += 1

                class R:
                    stop_reason = "end_turn"
                    usage = _Usage()
                    content = [type("B", (), {"type": "text",
                                              "text": '{"deals": []}'})()]
                return R()

        msgs = _Msgs()
        # Opt into the structured probe to exercise the one-time rejection.
        parser = ClaudeParser(_cfg(parse_structured_output=True),
                              client=_FakeClient(msgs))
        assert parser.extract("post one", {}) == []
        assert parser.extract("post two", {}) == []
        assert msgs.parse_calls == 1          # the rejection is paid once
        assert msgs.create_calls == 2         # both posts used the fallback

    def test_default_skips_structured_probe_entirely(self):
        """Production default: the minutes-long schema probe is never made —
        extraction goes straight to the JSON create() path on every post."""
        import anthropic
        import httpx

        class _Msgs:
            def __init__(self):
                self.parse_calls = 0
                self.create_calls = 0

            def parse(self, **kw):  # pragma: no cover - must never run
                self.parse_calls += 1
                raise anthropic.BadRequestError(
                    "Schema is too complex.",
                    response=httpx.Response(
                        400, request=httpx.Request("POST", "https://x")),
                    body=None)

            def create(self, **kw):
                self.create_calls += 1

                class R:
                    stop_reason = "end_turn"
                    usage = _Usage()
                    content = [type("B", (), {"type": "text",
                                              "text": '{"deals": []}'})()]
                return R()

        msgs = _Msgs()
        parser = ClaudeParser(_cfg(), client=_FakeClient(msgs))  # default: off
        assert parser.extract("post one", {}) == []
        assert parser.extract("post two", {}) == []
        assert msgs.parse_calls == 0          # never pays the slow probe
        assert msgs.create_calls == 2


# ------------------------------------------------------- live price recheck

class TestPriceRecheck:
    def _stats_response(self, current):
        import httpx
        return httpx.Response(
            200, json={"products": [{"stats": {"current": current}}]},
            request=httpx.Request("GET", "https://api.keepa.com/product"))

    def test_live_price_match_and_move(self, monkeypatch):
        import httpx

        from dealradar import verify
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        deal = {"asin": "B0TESTASIN1", "final_price": 29.99, "conditions": []}
        mock_keepa_get(monkeypatch, lambda *a, **k:
                            self._stats_response([2999, 3105, -1]))
        assert verify.recheck_amazon_price(deal, cfg) is True
        mock_keepa_get(monkeypatch, lambda *a, **k:
                            self._stats_response([9999, 10500, -1]))
        assert verify.recheck_amazon_price(deal, cfg) is False

    def test_coupon_deal_judged_against_listing_not_cart_price(self, monkeypatch):
        """Live wart: every clip-coupon / promo-code deal died at recheck —
        Amazon lists the pre-coupon price, so the cart price never matches.
        Such a deal is over only once the listing passes the quoted regular
        price."""
        import httpx

        from dealradar import verify
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        coded = {"asin": "B0TESTASIN1", "final_price": 9.99,
                 "regular_price": 19.99, "codes": ["SAVE50"], "conditions": []}
        clipped = {"asin": "B0TESTASIN1", "final_price": 9.99,
                   "regular_price": 19.99, "codes": [], "conditions": [],
                   "stack_steps": ["clip the 50% coupon"]}
        plain = {"asin": "B0TESTASIN1", "final_price": 9.99,
                 "regular_price": 19.99, "codes": [], "conditions": [],
                 "stack_steps": ["50% off sale"]}
        mock_keepa_get(monkeypatch, lambda *a, **k:
                            self._stats_response([1999, -1, -1]))
        assert verify.recheck_amazon_price(coded, cfg) is True
        assert verify.recheck_amazon_price(clipped, cfg) is True
        # A plain sale IS the listed price: a listing at the regular price
        # means the sale ended.
        assert verify.recheck_amazon_price(plain, cfg) is False
        # Listing climbed past the regular price: the coupon deal is gone too.
        mock_keepa_get(monkeypatch, lambda *a, **k:
                            self._stats_response([2499, -1, -1]))
        assert verify.recheck_amazon_price(coded, cfg) is False

    def test_recheck_backfills_missing_image_for_free(self, monkeypatch):
        """Live gap: cards without thumbnails. Every product response we
        already pay for carries imagesCSV — the recheck must harvest it."""
        import httpx

        from dealradar import verify
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True
        monkeypatch.setenv("KEEPA_KEY", "kkey")

        def resp(*a, **k):
            return httpx.Response(
                200, json={"products": [{
                    "imagesCSV": "71prodIMG.jpg,41other.jpg",
                    "stats": {"current": [2999, 3105, -1]}}]},
                request=httpx.Request("GET", "https://api.keepa.com/product"))

        mock_keepa_get(monkeypatch, resp)
        deal = {"asin": "B0TESTASIN1", "final_price": 29.99, "conditions": []}
        assert verify.recheck_amazon_price(deal, cfg) is True
        assert deal["image_url"] == \
            "https://m.media-amazon.com/images/I/71prodIMG.jpg"
        # An existing image is never overwritten.
        deal["image_url"] = "https://m.media-amazon.com/images/I/KEEP.jpg"
        verify.recheck_amazon_price(deal, cfg)
        assert deal["image_url"].endswith("KEEP.jpg")

    def test_harvests_keepa_images_list(self):
        """Keepa deprecated imagesCSV for images=[{l, m, ...}]; live wart:
        41 of 41 rechecked deals came back photo-less reading only the old
        field. The large image wins, then medium; junk is never used."""
        from dealradar import verify
        cdn = "https://m.media-amazon.com/images/I/"
        cases = [
            ({"images": [{"l": "81big.jpg", "lH": 1500, "m": "51mid.jpg"}]}, cdn + "81big.jpg"),
            ({"images": [{"m": "51mid.jpg"}]}, cdn + "51mid.jpg"),
            ({"images": [None, {"l": "../evil/x.jpg"}, {"l": "71ok.jpg"}]}, cdn + "71ok.jpg"),
            ({"images": ["61str.jpg"]}, cdn + "61str.jpg"),
            ({"images": None, "imagesCSV": "71old.jpg,41b.jpg"}, cdn + "71old.jpg"),
            ({"images": [], "imagesCSV": ""}, None),
            ({"images": [{"l": "<script>"}]}, None),
            ({}, None),
        ]
        for product, want in cases:
            deal = {"asin": "B0TESTASIN1"}
            verify._harvest_image(deal, product)
            assert deal.get("image_url") == want, product
        kept = {"image_url": cdn + "KEEP.jpg"}
        verify._harvest_image(kept, {"images": [{"l": "81big.jpg"}]})
        assert kept["image_url"] == cdn + "KEEP.jpg"

    def test_used_deal_checks_warehouse_price_and_gone_offer(self, monkeypatch):
        import httpx

        from dealradar import verify
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        used_deal = {"asin": "B0TESTASIN1", "final_price": 12.07,
                     "conditions": ["used", "limited_qty"]}
        current = [8155, 8155, -1, -1, -1, -1, -1, -1, -1, 1207]
        mock_keepa_get(monkeypatch,
                            lambda *a, **k: self._stats_response(current))
        assert verify.recheck_amazon_price(used_deal, cfg) is True
        # Warehouse slot empty in a stats-only record: Keepa refreshes it
        # only on an offers scan, so that is no opinion, never "sold".
        gone = [8155, 8155, -1, -1, -1, -1, -1, -1, -1, -1]
        mock_keepa_get(monkeypatch,
                            lambda *a, **k: self._stats_response(gone))
        assert verify.recheck_amazon_price(used_deal, cfg) is None

    def test_pipeline_recheck_marks_moved_deals_dead_once_per_window(
            self, tmp_path, monkeypatch):
        from dealradar import verify
        db = DB(tmp_path / "state.db")
        d = _deal_dict(dedupe_key="url|https://www.amazon.com/dp/B0X",
                       item="Gone Deal")
        d["asin"] = "B0XXXXXXXXX"
        db.insert_deal(dedupe_key=d["dedupe_key"], store=d["store"],
                       item=d["item"], normalized_item="gone deal",
                       canonical_url="https://www.amazon.com/dp/B0X",
                       payload=d, final_price=d["final_price"],
                       pct_at_checkout=d["pct_at_checkout"],
                       abs_savings=d["abs_savings"], online_only="true",
                       expires_at="", now=NOW)
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True
        calls = []

        def fake_recheck(deals, _cfg, budget=None, used_offers=True, fetcher=None):
            calls.extend(d["asin"] for d in deals)
            return {d["dedupe_key"]: False for d in deals}

        monkeypatch.setattr(verify, "recheck_amazon_prices", fake_recheck)
        # One "gone" reading only makes the deal a suspect: still alive,
        # asked again after _CONFIRM_GONE_H (operator: never show a deal
        # as expired that is not).
        stats = pipeline._recheck_prices(db, cfg, NOW)
        expected = {"checked": 1, "dead": 0, "image_backfill": 0,
                    "tokens_short": 0, "used_checked": 0, "suspect": 1}
        assert {k: stats[k] for k in expected} == expected
        row = db.get_deal(d["dedupe_key"])
        assert row["status"] == "alive" and row["gone_suspect_at"]
        assert "price_moved" not in row["flags"]
        # An early second reading (the photo-only batch may ask again)
        # never counts: the deal stays alive until the delay has passed...
        pipeline._recheck_prices(db, cfg, NOW + timedelta(minutes=20))
        assert db.get_deal(d["dedupe_key"])["status"] == "alive"
        # ...then a second agreeing reading kills it, as a confirmed death.
        later = NOW + timedelta(hours=pipeline._CONFIRM_GONE_H, minutes=1)
        stats = pipeline._recheck_prices(db, cfg, later)
        assert stats["checked"] == 1 and stats["dead"] == 1
        row = db.get_deal(d["dedupe_key"])
        assert row["status"] == "dead" and row["death"] == "confirmed"
        assert "price_moved" in row["flags"] and row["price_rechecked_at"]
        # Dead deals are out of the alive set: nothing more is checked.
        n = len(calls)
        assert pipeline._recheck_prices(db, cfg, later)["checked"] == 0
        assert len(calls) == n
        db.close()

    def test_in_window_recheck_only_for_photoless_deals(self, tmp_path,
                                                         monkeypatch):
        """Inside the 6-hour window a deal is left alone — unless it has no
        photo: then one early Keepa call fetches it (the product response
        carries the image), capped per run to protect the token bucket."""
        from datetime import timedelta

        from dealradar import verify
        db = DB(tmp_path / "state.db")
        recent = (NOW - timedelta(hours=1)).isoformat()
        for key, img in (("url|a", ""),
                         ("url|b", "https://m.media-amazon.com/images/I/x.jpg")):
            d = _deal_dict(dedupe_key=key, item=key, store="Amazon")
            d["asin"] = "B0" + key[-1].upper() * 8
            d["image_url"] = img
            d["price_rechecked_at"] = recent
            db.insert_deal(dedupe_key=key, store=d["store"], item=d["item"],
                           normalized_item=key, canonical_url="", payload=d,
                           final_price=d["final_price"],
                           pct_at_checkout=d["pct_at_checkout"],
                           abs_savings=d["abs_savings"], online_only="true",
                           expires_at="", now=NOW)
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True
        calls = []

        def fake_recheck(deals, _cfg, budget=None, used_offers=True, fetcher=None):
            for deal in deals:
                calls.append(deal["dedupe_key"])
                deal["image_url"] = "https://m.media-amazon.com/images/I/new.jpg"
            return {d["dedupe_key"]: True for d in deals}

        monkeypatch.setattr(verify, "recheck_amazon_prices", fake_recheck)
        stats = pipeline._recheck_prices(db, cfg, NOW)
        assert calls == ["url|a"]                      # photo'd deal untouched
        expected = {"checked": 1, "dead": 0, "image_backfill": 1,
                    "tokens_short": 0, "used_checked": 0}
        assert {k: stats[k] for k in expected} == expected
        assert db.get_deal("url|a")["image_url"].endswith("new.jpg")
        db.close()

    def test_photo_kept_when_recheck_has_no_price_opinion(self, tmp_path,
                                                          monkeypatch):
        """A Keepa record with a photo but no usable current price used to
        be thrown away whole: the verdict was None, so nothing was saved.
        The photo is kept; the deal stays due for its price check."""
        from dealradar import verify
        db = DB(tmp_path / "state.db")
        d = _deal_dict(dedupe_key="url|p", item="url|p", store="Amazon")
        d["asin"] = "B0PPPPPPPP"
        d["image_url"] = ""
        db.insert_deal(dedupe_key="url|p", store=d["store"], item=d["item"],
                       normalized_item="url|p", canonical_url="", payload=d,
                       final_price=d["final_price"],
                       pct_at_checkout=d["pct_at_checkout"],
                       abs_savings=d["abs_savings"], online_only="true",
                       expires_at="", now=NOW)
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True

        def fake_recheck(deals, _cfg, budget=None, used_offers=False, fetcher=None):
            for deal in deals:
                deal["image_url"] = "https://m.media-amazon.com/images/I/p.jpg"
            return {deal["dedupe_key"]: None for deal in deals}

        monkeypatch.setattr(verify, "recheck_amazon_prices", fake_recheck)
        pipeline._recheck_prices(db, cfg, NOW)
        got = db.get_deal("url|p")
        assert got["image_url"].endswith("/p.jpg")
        assert not got.get("price_rechecked_at")  # still due for its check
        assert got["status"] == "alive"
        db.close()

    def test_token_budget_holds_back_photo_calls_before_rechecks(
            self, tmp_path, monkeypatch):
        """Live incident: in-window photo calls plus rechecks drained the
        Keepa bucket and the deal lane 429'd into the breaker. With the
        shared budget low, photo-only calls (need 60) wait while a due
        recheck (need 15) still runs; lower still nothing spends, so the
        deal lane keeps the last tokens; refill brings everything back."""
        import json
        from datetime import timedelta

        from dealradar import verify
        from dealradar.keepa_budget import META_KEY
        db = DB(tmp_path / "state.db")
        recent = (NOW - timedelta(hours=1)).isoformat()
        for key, last in (("url|photo", recent), ("url|due", "")):
            d = _deal_dict(dedupe_key=key, item=key, store="Amazon")
            d["asin"] = "B0" + key[-1].upper() * 8
            d["image_url"] = ""
            d["price_rechecked_at"] = last
            db.insert_deal(dedupe_key=key, store=d["store"], item=d["item"],
                           normalized_item=key, canonical_url="", payload=d,
                           final_price=d["final_price"],
                           pct_at_checkout=d["pct_at_checkout"],
                           abs_savings=d["abs_savings"], online_only="true",
                           expires_at="", now=NOW)
        cfg = _cfg()
        cfg.keepa_volatility["enabled"] = False  # legacy cadence fixture
        cfg.feature_flags["keepa"] = True
        calls = []

        def fake_recheck(deals, _cfg, budget=None, used_offers=True, fetcher=None):
            calls.extend(d["dedupe_key"] for d in deals)
            budget.note({"tokensLeft": 20, "refillRate": 5}, NOW)
            return {d["dedupe_key"]: True for d in deals}

        monkeypatch.setattr(verify, "recheck_amazon_prices", fake_recheck)

        def reading(tokens):
            db.meta_set(META_KEY, json.dumps({
                "tokens_left": tokens, "refill_rate": 5,
                "seen_at": NOW.isoformat()}))

        # 30 tokens: the photo-only call waits, the due recheck runs.
        reading(30)
        stats = pipeline._recheck_prices(db, cfg, NOW)
        assert calls == ["url|due"]
        expected = {"checked": 1, "dead": 0, "image_backfill": 0,
                    "tokens_short": 1, "used_checked": 0}
        assert {k: stats[k] for k in expected} == expected
        # The response's reading is persisted for the adapter / next run.
        assert json.loads(db.meta_get(META_KEY))["tokens_left"] == 20
        # 5 tokens: nothing spends. The just-checked deal also skips an
        # identical-time retry; only the other photo is held by the budget.
        reading(5)
        calls.clear()
        stats = pipeline._recheck_prices(db, cfg, NOW)
        assert calls == [] and stats["tokens_short"] == 1
        # Half an hour of refill at 5/min: the bucket is back, both run.
        stats = pipeline._recheck_prices(db, cfg, NOW + timedelta(minutes=30))
        assert sorted(calls) == ["url|due", "url|photo"]
        assert stats["image_backfill"] == 2 and stats["tokens_short"] == 0
        db.close()


# --------------------------------------------------------------- site tiers

class TestSiteStrongTier:
    def _db(self, tmp_path) -> DB:
        db = DB(tmp_path / "state.db")
        for key, item, pct in (("si|a|hot", "Hot Item", 72.0),
                               ("si|a|strong", "Strong Item", 58.0),
                               ("si|a|weak", "Weak Item", 30.0)):
            d = _deal_dict(dedupe_key=key, item=item, pct_at_checkout=pct)
            db.insert_deal(dedupe_key=key, store=d["store"], item=item,
                           normalized_item=item.lower(), canonical_url="",
                           payload=d, final_price=d["final_price"],
                           pct_at_checkout=pct, abs_savings=d["abs_savings"],
                           online_only="true", expires_at="", now=NOW)
        return db

    def test_site_shows_strong_tier_alerts_do_not(self, tmp_path):
        db = self._db(tmp_path)
        cfg = _cfg(min_pct=65.0, site_show_from_pct=50.0)
        site = [d["item"] for d in rank.site_deals(db, cfg, NOW)]
        alerts = [d["item"] for d in rank.published_deals(db, cfg, NOW)]
        assert set(site) == {"Hot Item", "Strong Item"}  # weak 30% stays out
        assert alerts == ["Hot Item"]                    # strict floor for alerts
        db.close()

    def test_site_floor_never_rises_above_alert_floor(self, tmp_path):
        db = self._db(tmp_path)
        cfg = _cfg(min_pct=65.0, site_show_from_pct=90.0)  # misconfigured high
        site = [d["item"] for d in rank.site_deals(db, cfg, NOW)]
        assert site == ["Hot Item"]  # clamped to min_pct, nothing hidden
        db.close()


# --------------------------------------------------------------- config

class TestConfigFields:
    def test_parse_model_override_skips_heuristic(self):
        class _NoListModels:
            def list(self):  # pragma: no cover - must never be called
                raise AssertionError("models.list() called despite override")

        class _Client:
            models = _NoListModels()
            messages = _CriticMessages()

        cfg = _cfg(parse_model="claude-sonnet-5-5")
        parser = ClaudeParser(cfg, client=_Client())
        assert parser.model == "claude-sonnet-5-5"

    def test_quality_trio_config_loads(self):
        cfg = load_config()
        # Floors: used/open-box at 50, everything else at 40 (site tier too).
        assert cfg.min_pct == pytest.approx(40.0)
        assert cfg.used_min_pct == pytest.approx(50.0)
        assert cfg.site_show_from_pct == pytest.approx(40.0)
        # Tiered models: Haiku reads everything, Sonnet double-checks and judges.
        assert cfg.parse_model == "claude-haiku-5-5"
        assert cfg.second_opinion_model == "claude-sonnet-5-5"
        assert cfg.max_second_opinions_per_run == 8
        assert cfg.critic_model == "claude-sonnet-5-5"
        assert cfg.critic_enabled is True
        assert cfg.exclude_suspect_msrp is True
        assert cfg.spend_cap_usd_per_day == pytest.approx(6.00)


class TestBatchedRecheck:
    def _stats_response(self, products):
        import httpx
        return httpx.Response(200, json={"products": products, "tokensLeft": 900,
                                         "refillRate": 20},
                              request=httpx.Request("GET", "https://api.keepa.com/product"))

    def test_hundred_deals_per_keepa_call(self, monkeypatch):
        """20 tokens a minute are only usable in bulk: one /product call
        carries up to 100 ASINs; verdicts and photos come per deal."""
        import httpx

        from dealradar import verify
        from dealradar.keepa_budget import KeepaBudget
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        deals = [{"dedupe_key": f"k{i}", "asin": f"B0{i:08d}", "final_price": 10.0,
                  "conditions": []} for i in range(150)]
        seen = []

        def fake_get(url, params=None, timeout=None):
            asins = params["asin"].split(",")
            seen.append(len(asins))
            prods = []
            for a in asins:
                n = int(a[2:])
                prods.append({"asin": a, "imagesCSV": f"{n}.jpg",
                              "stats": {"current": [1000 if n % 2 else 5000, -1, -1]}})
            return self._stats_response(prods)

        mock_keepa_get(monkeypatch, fake_get)
        budget = KeepaBudget()
        verdicts = verify.recheck_amazon_prices(deals, cfg, budget=budget)
        assert seen == [100, 50]
        assert verdicts["k1"] is True and verdicts["k2"] is False
        assert len(verdicts) == 150
        assert deals[7]["image_url"] == "https://m.media-amazon.com/images/I/7.jpg"
        assert budget.tokens_left == 900 and budget.refill_rate == 20

    def test_pipeline_stops_at_the_deadline(self, tmp_path, monkeypatch):
        import time as _time

        from dealradar import verify
        db = DB(tmp_path / "state.db")
        for i in range(3):
            d = _deal_dict(dedupe_key=f"url|{i}", item=f"deal {i}", store="Amazon")
            d["asin"] = f"B0DEADLINE{i}"
            db.insert_deal(dedupe_key=d["dedupe_key"], store=d["store"],
                           item=d["item"], normalized_item=d["item"],
                           canonical_url="", payload=d, final_price=d["final_price"],
                           pct_at_checkout=d["pct_at_checkout"],
                           abs_savings=d["abs_savings"], online_only="true",
                           expires_at="", now=NOW)
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True
        calls = []
        monkeypatch.setattr(verify, "recheck_amazon_prices",
                            lambda deals, _c, budget=None, used_offers=True, fetcher=None: (
                                calls.extend(deals),
                                {d["dedupe_key"]: True for d in deals})[1])
        stats = pipeline._recheck_prices(db, cfg, NOW, deadline=_time.monotonic() - 1)
        assert calls == [] and stats["checked"] == 0 and stats["tokens_short"] == 3
        stats = pipeline._recheck_prices(db, cfg, NOW, deadline=_time.monotonic() + 60)
        assert len(calls) == 3 and stats["checked"] == 3
        db.close()


# ----------------------------------------- careful death (used / open-box)

_IMG = "https://m.media-amazon.com/images/I/x.jpg"


def _km(dt: datetime) -> int:
    """Keepa time (minutes since 2011-01-01 UTC) for an aware datetime."""
    return int(dt.timestamp()) // 60 - 21564000


def _offer(condition: int, cents: int, ship: int = -1) -> dict:
    """A Keepa offer whose offerCSV holds an older, dearer triple first:
    only the LAST [time, price, shipping] triple is the current price."""
    return {"condition": condition,
            "offerCSV": [_km(NOW) - 600, cents + 5000, ship,
                         _km(NOW), cents, ship]}


def _seed_amazon(db, key: str, asin: str, *, used: bool = False,
                 last: str = "", image: str = _IMG, **over) -> dict:
    from dealradar.keepa_budget import KeepaBudget, META_KEY
    if db.meta_get(META_KEY) is None:
        KeepaBudget(1200, 0, NOW).save(db)  # known allowance for used scans
    d = _deal_dict(dedupe_key=key, item=key, store="Amazon", asin=asin, image_url=image,
                   conditions=["used", "open_box"] if used else [],
                   price_rechecked_at=last, **over)
    db.insert_deal(dedupe_key=key, store=d["store"], item=key,
                   normalized_item=key, canonical_url="", payload=d,
                   final_price=d["final_price"],
                   pct_at_checkout=d["pct_at_checkout"],
                   abs_savings=d["abs_savings"], online_only="true",
                   expires_at="", now=NOW)
    if used:
        # Offer-scan fixtures are on the remembered home wall (task 085).
        wall = json.loads(db.meta_get("published_wall") or "[]")
        db.meta_set("published_wall", json.dumps(wall + [key]))
    return d


class TestCarefulDeath:
    """Live incident (2026-10-11): five Amazon Warehouse open-box deals were
    marked dead by the price recheck within 0.5-7 h and were still on sale.
    A plain /product record is no evidence a Warehouse unit sold."""

    FIRST_SEEN = datetime(2026, 10, 10, 11, 0, tzinfo=timezone.utc)

    def _used(self) -> dict:
        return {"asin": "B0BKQSDF5C", "final_price": 449.51,
                "conditions": ["used", "open_box"],
                "first_seen_at": self.FIRST_SEEN.isoformat()}

    def test_keepa_time(self):
        from dealradar import verify
        assert verify.KEEPA_EPOCH_MINUTES == 21564000
        assert verify.keepa_time(0) == datetime(2011, 1, 1, tzinfo=timezone.utc)
        assert verify.keepa_time(8296560) == NOW
        assert verify.keepa_time(1).timestamp() == (1 + 21564000) * 60
        for missing in (None, -1, "8296560", True):
            assert verify.keepa_time(missing) is None

    def test_used_stats_only_record_confirms_but_never_kills(self):
        from dealradar import verify

        def judge(current):
            return verify.judge_live_price(self._used(),
                                           {"stats": {"current": current}})

        empty = [89900, 89900] + [-1] * 8
        assert judge(empty) is None       # the incident: this was False
        used = list(empty)
        used[2] = 47999                   # lowest used offer, within 10%
        assert judge(used) is True
        warehouse = list(empty)
        warehouse[9] = 44951
        assert judge(warehouse) is True
        warehouse[9] = 60000              # dearer: still no proof of a sale
        assert judge(warehouse) is None
        assert verify.judge_live_price(self._used(), {}) is None

    def test_used_offers_record(self):
        from dealradar import verify
        fresh = _km(NOW)                                  # after first seen
        stale = _km(self.FIRST_SEEN - timedelta(hours=2))

        def judge(order, offers=None, scanned=fresh, **extra):
            product = {"offers": offers if offers is not None
                       else [_offer(1, 89900), _offer(3, 44951)],
                       "liveOffersOrder": order,
                       "stats": {"current": [89900, 89900] + [-1] * 8,
                                 "lastOffersUpdate": scanned}}
            product.update(extra)
            return verify.judge_live_price(self._used(), product)

        assert judge([0, 1]) is True          # live used offer at the price
        assert judge([]) is False             # fresh scan, no offer: sold
        assert judge([0]) is False            # only the new offer is live
        assert judge([], scanned=stale) is None   # scan predates the deal
        assert judge([], scanned=-1) is None
        assert judge([], offersSuccessful=False) is None
        # Shipping counts (440 + 60 > 10% over); collectible is not used.
        assert judge([0, 1], offers=[_offer(1, 89900),
                                     _offer(3, 44000, 6000)]) is False
        assert judge([0, 1], offers=[_offer(1, 89900),
                                     _offer(7, 44951)]) is False
        # Used - Good / Acceptable and Refurbished are live used units too.
        for cond in (2, 3, 4, 5, 6):
            assert judge([0, 1], offers=[_offer(1, 89900),
                                         _offer(cond, 44951)]) is True, cond
        # New (1) and unknown (0) offers at the price never count.
        for cond in (0, 1):
            assert judge([0], offers=[_offer(cond, 44951)]) is False, cond
        # A live used offer whose price Keepa could not read (-2) is a
        # listed unit: no opinion, never "sold".
        unreadable = {"condition": 2, "offerCSV": [_km(NOW), -2, -2]}
        assert judge([0, 1], offers=[_offer(1, 89900), unreadable]) is None
        assert judge([0, 1, 2], offers=[_offer(1, 89900), unreadable,
                                        _offer(3, 44951)]) is True

    def test_used_offers_fallback_when_keepa_omits_the_live_order(self):
        """liveOffersOrder null (or no offers list) is not an offers scan:
        the stats-only rule applies — confirm, never kill."""
        from dealradar import verify
        fresh = _km(NOW)

        def judge(current, **fields):
            product = {"stats": {"current": current,
                                 "lastOffersUpdate": fresh}}
            product.update(fields)
            return verify.judge_live_price(self._used(), product)

        empty = [89900, 89900] + [-1] * 8
        warehouse = empty[:9] + [44951]
        for fields in ({"offers": [_offer(1, 89900)], "liveOffersOrder": None},
                       {"liveOffersOrder": []},
                       {"offers": None, "liveOffersOrder": [0]}):
            assert judge(empty, **fields) is None, fields
            assert judge(warehouse, **fields) is True, fields

    def test_used_deal_with_a_checkout_promo(self):
        """"Like New $60, extra 20% off at checkout -> $48": the listing
        never shows $48. A live used unit is matched against the quoted
        regular price (as for new deals); only a fresh scan with no live
        used unit at all ends it."""
        from dealradar import verify
        fresh = _km(NOW)

        def judge(deal, offers, order):
            return verify.judge_live_price(deal, {
                "offers": offers, "liveOffersOrder": order,
                "stats": {"lastOffersUpdate": fresh}})

        deal = dict(self._used(), final_price=48.0, regular_price=120.0,
                    stack_steps=["extra 20% off at checkout"])
        assert judge(deal, [_offer(1, 12000), _offer(2, 6000)], [0, 1]) is True
        assert judge(deal, [_offer(1, 12000)], [0]) is False
        # No regular price to match: a live used unit is no opinion.
        bare = dict(deal, regular_price=None)
        assert judge(bare, [_offer(1, 12000), _offer(2, 6000)], [0, 1]) is None
        assert judge(bare, [_offer(1, 12000)], [0]) is False
        # Without a checkout promo the $60 listing is the price: moved.
        plain = dict(deal, stack_steps=["20% off"])
        assert judge(plain, [_offer(1, 12000), _offer(2, 6000)], [0, 1]) is False

    def test_false_needs_keepa_data_newer_than_a_drop_or_revival(self):
        """A deal re-sighted (or cheaper) at T2 is judged only on Keepa data
        from T2 on: an older answer cannot know the new price."""
        from dealradar import verify
        t2 = NOW + timedelta(minutes=98)
        before, after = _km(t2 - timedelta(minutes=15)), _km(t2)
        used = dict(self._used(), price_since=t2.isoformat())
        gone = {"offers": [_offer(1, 99999)], "liveOffersOrder": [0]}
        assert verify.judge_live_price(
            used, dict(gone, stats={"lastOffersUpdate": before})) is None
        assert verify.judge_live_price(
            used, dict(gone, stats={"lastOffersUpdate": after})) is False
        new = {"asin": "B0NEW00001", "final_price": 29.99, "conditions": [],
               "first_seen_at": self.FIRST_SEEN.isoformat(),
               "price_since": t2.isoformat()}
        moved = {"stats": {"current": [9999, 10500]}}
        assert verify.judge_live_price(new, dict(moved, lastUpdate=before)) is None
        assert verify.judge_live_price(new, dict(moved, lastUpdate=after)) is False

    def test_new_deal_dies_only_on_data_newer_than_the_deal(self, caplog):
        from dealradar import verify

        def deal():
            return {"asin": "B0NEW00001", "final_price": 29.99,
                    "conditions": [],
                    "first_seen_at": self.FIRST_SEEN.isoformat()}

        before = _km(self.FIRST_SEEN - timedelta(hours=1))
        after = _km(NOW)
        moved = {"stats": {"current": [9999, 10500]}}
        assert verify.judge_live_price(deal(), dict(moved, lastUpdate=before)) is None
        assert verify.judge_live_price(deal(), dict(moved, lastUpdate=after)) is False
        assert verify.judge_live_price(deal(), moved) is False  # no gate
        live = {"stats": {"current": [2999, -1]}, "lastUpdate": before}
        assert verify.judge_live_price(deal(), live) is True
        gone = {"stats": {"current": [-1, -1]}}
        assert verify.judge_live_price(deal(), dict(gone, lastUpdate=before)) is None
        with caplog.at_level("INFO", logger="dealradar.verify"):
            assert verify.judge_live_price(
                deal(), dict(gone, lastUpdate=after)) is False
        assert ("price recheck B0NEW00001: final=$29.99, no live new offer "
                "-> False") in caplog.text

    def test_only_used_batches_ask_keepa_for_offers(self, monkeypatch):
        import httpx

        from dealradar import verify
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        sent = []

        def fake_get(url, params=None, timeout=None):
            sent.append(dict(params))
            prods = [{"asin": a, "stats": {"current": [1000] + [-1] * 8 + [1000]}}
                     for a in params["asin"].split(",")]
            return httpx.Response(200, json={"products": prods},
                                  request=httpx.Request("GET", url))

        mock_keepa_get(monkeypatch, fake_get)
        deals = [{"dedupe_key": "n1", "asin": "B0NEW00001", "final_price": 10.0,
                  "conditions": []},
                 {"dedupe_key": "u1", "asin": "B0USED0001", "final_price": 10.0,
                  "conditions": ["open_box"]},
                 {"dedupe_key": "n2", "asin": "B0NEW00002", "final_price": 10.0,
                  "conditions": []}]
        verdicts = verify.recheck_amazon_prices(deals, cfg)
        assert [(p["asin"], p.get("offers")) for p in sent] == [
            ("B0NEW00001,B0NEW00002", None), ("B0USED0001", 20)]
        assert verdicts == {"n1": True, "n2": True, "u1": True}
        sent.clear()
        verify.recheck_amazon_prices(deals, cfg, used_offers=False)  # photos
        assert [(p["asin"], p.get("offers")) for p in sent] == [
            ("B0NEW00001,B0USED0001,B0NEW00002", None)]

    def test_used_deal_stays_alive_through_a_stats_only_answer(
            self, tmp_path, monkeypatch):
        import httpx
        db = DB(tmp_path / "state.db")
        _seed_amazon(db, "url|wh", "B0BKQSDF5C", used=True, final_price=449.51,
                     pct_at_checkout=55.0, abs_savings=550.48)
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        sent = []

        def fake_get(url, params=None, timeout=None):
            sent.append(dict(params))
            return httpx.Response(200, json={"products": [{
                "asin": "B0BKQSDF5C",
                "stats": {"current": [89900, 89900] + [-1] * 8}}]},
                request=httpx.Request("GET", url))

        mock_keepa_get(monkeypatch, fake_get)
        stats = pipeline._recheck_prices(db, cfg, NOW)
        assert sent[0]["offers"] == 20
        assert stats["checked"] == 0 and stats["dead"] == 0
        row = db.get_deal("url|wh")
        assert row["status"] == "alive" and "price_moved" not in row["flags"]
        db.close()

    def test_used_deals_come_due_every_six_hours(self, tmp_path, monkeypatch):
        from dealradar import verify
        db = DB(tmp_path / "state.db")

        def ago(hours):
            return (NOW - timedelta(hours=hours)).isoformat()

        _seed_amazon(db, "url|new4h", "B0NEW00004", last=ago(4))
        _seed_amazon(db, "url|used4h", "B0USED0004", used=True, last=ago(4))
        _seed_amazon(db, "url|used5.9h", "B0USED0059", used=True,
                     last=ago(5.9))
        _seed_amazon(db, "url|used6h", "B0USED0006", used=True, last=ago(6))
        _seed_amazon(db, "url|used7h", "B0USED0007", used=True, last=ago(7))
        _seed_amazon(db, "url|usedpic", "B0USEDPIC1", used=True, last=ago(4),
                     image="")
        cfg = _cfg()
        cfg.keepa_volatility["enabled"] = False  # legacy cadence fixture
        cfg.feature_flags["keepa"] = True
        cfg.keepa_recheck_adaptive["enabled"] = False  # exercise the fixed 085 cadence
        # The 3 h / 6 h cadence this test was written for (config.yaml
        # keepa.recheck_* now says 4/8/6 h; the mechanism is the same).
        cfg.keepa_recheck_fresh_h = cfg.keepa_recheck_new_h = 3.0
        cfg.keepa_recheck_used_h = 6.0
        calls = []

        def fake_recheck(deals, _cfg, budget=None, used_offers=True, fetcher=None):
            calls.append((sorted(d["dedupe_key"] for d in deals), used_offers))
            return {d["dedupe_key"]: True for d in deals}

        monkeypatch.setattr(verify, "recheck_amazon_prices", fake_recheck)
        stats = pipeline._recheck_prices(db, cfg, NOW)
        # New deals stay on 3 h; used wait 6 h; a photo-less used deal in
        # its window waits for its offers scan (no stats-only photo call).
        assert calls == [(["url|new4h"], True),
                         (["url|used6h", "url|used7h"], True)]
        assert stats["checked"] == 3 and stats["used_checked"] == 2
        db.close()

    def test_photoless_used_deal_still_gets_its_offers_scan(self, tmp_path,
                                                           monkeypatch):
        """A stats-only photo call that confirmed a used deal re-stamped it,
        so a photo-less used deal never came due for its offers scan."""
        from dealradar import verify
        db = DB(tmp_path / "state.db")
        _seed_amazon(db, "url|usedpic", "B0USEDPIC1", used=True, image="",
                     last=(NOW - timedelta(hours=1)).isoformat())
        cfg = _cfg()
        cfg.keepa_volatility["enabled"] = False  # legacy cadence fixture
        cfg.feature_flags["keepa"] = True
        cfg.keepa_recheck_adaptive["enabled"] = False  # exercise the fixed 085 cadence
        # The 3 h / 6 h cadence this test was written for (config.yaml
        # keepa.recheck_* now says 4/8/6 h; the mechanism is the same).
        cfg.keepa_recheck_fresh_h = cfg.keepa_recheck_new_h = 3.0
        cfg.keepa_recheck_used_h = 6.0
        calls = []

        def fake_recheck(deals, _cfg, budget=None, used_offers=True, fetcher=None):
            calls.append(([d["dedupe_key"] for d in deals], used_offers))
            return {d["dedupe_key"]: True for d in deals}

        monkeypatch.setattr(verify, "recheck_amazon_prices", fake_recheck)
        for hour in range(6):  # hourly fast-lane passes inside the window
            pipeline._recheck_prices(db, cfg, NOW + timedelta(hours=hour))
        assert calls == [(["url|usedpic"], True)]  # the 6 h offers scan
        db.close()

    def test_used_batches_are_sized_for_the_offers_cost(self, tmp_path,
                                                        monkeypatch):
        """offers=20 is billed 6 tokens per page of up to 10 offers: 13 a
        deal at worst. 75 tokens buy (75 - 10) / 13 = 5 used rechecks and
        the deal lane keeps its last 10; a full bucket still sends at most
        20 ASINs per offers call. At 22 tokens a plain recheck could run,
        but one offers scan could dig into the deal lane's 10."""
        import json

        from dealradar import verify
        from dealradar.keepa_budget import META_KEY
        assert verify.USED_RECHECK_TOKENS == 13
        assert verify.KEEPA_OFFERS_BATCH == 20
        db = DB(tmp_path / "state.db")
        for i in range(25):
            _seed_amazon(db, f"url|u{i}", f"B0USED{i:04d}", used=True)
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True
        cfg.keepa_used_scan_budget_share = 1  # isolate per-batch cost/reserve
        sizes, floor = [], []

        def fake_recheck(deals, _cfg, budget=None, used_offers=True, fetcher=None):
            sizes.append(len(deals))
            left = budget.available(NOW) - 13 * len(deals)
            floor.append(left)
            budget.note({"tokensLeft": left, "refillRate": 0}, NOW)
            return {}  # no answer (a miss): every deal stays due

        monkeypatch.setattr(verify, "recheck_amazon_prices", fake_recheck)

        def reading(tokens):
            db.meta_set(META_KEY, json.dumps({
                "tokens_left": tokens, "refill_rate": 0,
                "seen_at": NOW.isoformat()}))

        reading(75)
        stats = pipeline._recheck_prices(db, cfg, NOW)
        assert sizes == [5] and stats["tokens_short"] == 20
        assert json.loads(db.meta_get(META_KEY))["tokens_left"] == 10
        sizes.clear()
        reading(500)
        pipeline._recheck_prices(db, cfg, NOW)
        assert sizes == [20, 5]
        sizes.clear()
        reading(22)
        stats = pipeline._recheck_prices(db, cfg, NOW)
        assert sizes == [] and stats["tokens_short"] == 25
        assert min(floor) >= 10  # never below the deal lane's reserve
        db.close()

    def test_new_and_photo_batches_cost_one_token_a_deal(self, tmp_path,
                                                         monkeypatch):
        """Stats-only calls cost 1 token per ASIN: 45 tokens buy 35 new-deal
        rechecks and 65 tokens 55 photo calls, the deal lane keeping 10."""
        import json

        from dealradar import verify
        from dealradar.keepa_budget import META_KEY
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True
        cfg.keepa_volatility["enabled"] = False  # legacy token arithmetic; the planner has its own tests
        sizes = []

        def fake_recheck(deals, _cfg, budget=None, used_offers=True, fetcher=None):
            sizes.append(len(deals))
            budget.note({"tokensLeft": budget.available(NOW) - len(deals),
                         "refillRate": 0}, NOW)
            return {d["dedupe_key"]: True for d in deals}

        monkeypatch.setattr(verify, "recheck_amazon_prices", fake_recheck)

        def reading(db, tokens):
            db.meta_set(META_KEY, json.dumps({
                "tokens_left": tokens, "refill_rate": 0,
                "seen_at": NOW.isoformat()}))

        db = DB(tmp_path / "due.db")
        for i in range(50):
            _seed_amazon(db, f"url|n{i}", f"B0NEW{i:05d}")
        reading(db, 45)
        stats = pipeline._recheck_prices(db, cfg, NOW)
        assert sizes == [35] and stats["tokens_short"] == 15
        db.close()
        db = DB(tmp_path / "photo.db")
        for i in range(70):
            _seed_amazon(db, f"url|p{i}", f"B0PIC{i:05d}", image="",
                         last=(NOW - timedelta(hours=1)).isoformat())
        sizes.clear()
        reading(db, 65)
        stats = pipeline._recheck_prices(db, cfg, NOW)
        assert sizes == [55] and stats["image_backfill"] == 55
        db.close()

    def test_offers_calls_never_carry_more_than_twenty_asins(self, tmp_path,
                                                             monkeypatch):
        """Keepa rejects more than 20 ASINs with offers: 30 due used deals
        and a full bucket still go out 20 + 10 within their 35% share."""
        import httpx

        from dealradar import verify
        db = DB(tmp_path / "state.db")
        for i in range(30):
            _seed_amazon(db, f"url|u{i}", f"B0USED{i:04d}", used=True)
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        sent = []

        def fake_get(url, params=None, timeout=None):
            asins = params["asin"].split(",")
            sent.append((len(asins), params.get("offers")))
            return httpx.Response(200, json={"products": [
                {"asin": a, "stats": {"current": [-1] * 9 + [2500]}}
                for a in asins]}, request=httpx.Request("GET", url))

        mock_keepa_get(monkeypatch, fake_get)
        stats = pipeline._recheck_prices(db, cfg, NOW)
        assert sent == [(20, 20), (10, 20)]
        assert stats["used_checked"] == 30
        sent.clear()
        deals = [{"dedupe_key": f"k{i}", "asin": f"B0USED{i:04d}",
                  "final_price": 25.0, "conditions": ["used"]}
                 for i in range(45)]
        assert len(verify.recheck_amazon_prices(deals, cfg)) == 45
        assert sent == [(20, 20), (20, 20), (5, 20)]
        sent.clear()
        verify.keepa_products("kkey", [d["asin"] for d in deals], 1, None,
                              offers=20)
        assert sent == [(20, 20)]
        db.close()

    def _seed_dead(self, db, key, asin, *, died, used=True,
                   flags=("price_moved",), expires=""):
        """A deal the recheck killed at ``died`` (stamp = kill time)."""
        import dataclasses

        from dealradar.models import Deal
        first = died - timedelta(hours=2)
        payload = dataclasses.asdict(Deal(
            dedupe_key=key, store="Amazon", item=key, asin=asin,
            image_url=_IMG, conditions=["used", "open_box"] if used else [],
            flags=list(flags), final_price=449.51, regular_price=999.99,
            pct_at_checkout=55.0, abs_savings=550.48, online_only="true",
            category="electronics", expires_at=expires,
            died_at=died.isoformat(), price_rechecked_at=died.isoformat(),
            first_seen_at=first.isoformat()))
        db.insert_deal(dedupe_key=key, store="Amazon", item=key,
                       normalized_item=key, canonical_url="", payload=payload,
                       final_price=449.51, pct_at_checkout=55.0,
                       abs_savings=550.48, online_only="true",
                       expires_at=expires, now=first)
        db.update_deal(key, status="dead")

    def _published(self, db, cfg, now):
        """(keys in the alert/publish set, keys shown ALIVE on the site,
        Telegram alerts sent)."""
        from dealradar.publish.telegram_pub import send_alerts
        from dealradar.publish.site import render_site
        from pathlib import Path

        cfg.site_dir = str(Path(db.path).parent / "site")
        # These are Warehouse (used) units: social posts skip used items
        # (operator, 2026-10-13); this helper counts alertability, so lift that.
        cfg.social_exclude_conditions = []
        cfg.social_require_stock_evidence = False
        cfg.front_page = dict(cfg.front_page, top_picks=dict(
            cfg.front_page["top_picks"], exclude_conditions=[]))
        render_site(db, cfg, now)  # channel candidates come from the published picks
        alerts = {d["dedupe_key"] for d in rank.published_deals(db, cfg, now)}
        alive_on_site = {d["dedupe_key"] for d in rank.site_deals(db, cfg, now)
                         if d["status"] == "alive"}
        sent = send_alerts(db, cfg, now, dry_run=True)["sent"]
        return alerts, alive_on_site, sent

    def test_one_time_repair_reopens_recent_used_kills(self, tmp_path, caplog):
        """The repair re-opens only used deals the recheck killed within
        48 h that have not expired, and leaves them DEAD with the recheck
        stamp cleared: nothing is published or alerted before a fresh
        offers scan says so."""
        import json

        from dealradar.dedupe import _DEAL_FIELDS
        db = DB(tmp_path / "state.db")
        recent = NOW - timedelta(hours=3)
        self._seed_dead(db, "url|wh", "B0BKQSDF5C", died=recent)
        self._seed_dead(db, "url|old", "B0OLDKILL1",
                        died=NOW - timedelta(hours=49))
        self._seed_dead(db, "url|expired", "B0EXPIRED1", died=recent,
                        expires=(NOW - timedelta(hours=1)).isoformat())
        self._seed_dead(db, "url|soldout", "B0SOLDOUT1", died=recent,
                        flags=())  # a post said so
        self._seed_dead(db, "url|new", "B0NEWMOVED", died=recent, used=False)
        cfg = _cfg()
        cfg.feature_flags["keepa"] = False  # the repair runs regardless
        with caplog.at_level("INFO", logger="dealradar.pipeline"):
            pipeline._recheck_prices(db, cfg, NOW)
        assert "revive repair: 1 used/open-box deal(s)" in caplog.text
        row = db.get_deal("url|wh")
        assert row["status"] == "dead" and row["price_rechecked_at"] == ""
        assert row["flags"] == ["price_moved"]
        assert row["died_at"] == recent.isoformat()
        stored = json.loads(db.conn.execute(
            "SELECT payload FROM deals WHERE dedupe_key='url|wh'").fetchone()[0])
        assert set(_DEAL_FIELDS) <= set(stored)
        for key in ("url|old", "url|expired", "url|soldout", "url|new"):
            other = db.get_deal(key)
            assert other["status"] == "dead" and other["price_rechecked_at"]
        assert self._published(db, cfg, NOW) == (set(), set(), 0)
        db.close()

    def test_repair_runs_once_even_when_nothing_qualified(self, tmp_path,
                                                          monkeypatch):
        """The meta key is written on the first run whatever it found: a
        later legitimate kill is never re-opened."""
        from dealradar import verify
        db = DB(tmp_path / "state.db")
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True
        calls = []

        def fake_recheck(deals, _cfg, budget=None, used_offers=True, fetcher=None):
            calls.extend(d["dedupe_key"] for d in deals)
            return {d["dedupe_key"]: True for d in deals}

        monkeypatch.setattr(verify, "recheck_amazon_prices", fake_recheck)
        pipeline._recheck_prices(db, cfg, NOW)  # empty DB: nothing qualifies
        assert db.meta_get(pipeline._REVIVE_USED_KEY)
        self._seed_dead(db, "url|wh", "B0BKQSDF5C",
                        died=NOW - timedelta(hours=1))
        pipeline._recheck_prices(db, cfg, NOW)
        row = db.get_deal("url|wh")
        assert calls == [] and row["status"] == "dead"
        assert row["price_rechecked_at"]
        db.close()

    def test_reopened_deal_lives_only_on_a_fresh_offers_verdict(
            self, tmp_path, monkeypatch):
        """End to end through the real Keepa batch: tokens short -> still
        dead, unpublished, unalerted; an offers answer with no opinion ->
        the same; a live used unit at the price -> alive, published and
        alerted once. A sold unit's verdict ends the re-check, still dead."""
        import json

        import httpx

        from dealradar.keepa_budget import META_KEY
        db = DB(tmp_path / "state.db")
        died = NOW - timedelta(hours=3)
        self._seed_dead(db, "url|wh", "B0BKQSDF5C", died=died)
        self._seed_dead(db, "url|sold", "B0SOLDUNIT", died=died)
        # The channel now requires an earning Get deal link as well as a live unit.
        db.update_deal("url|wh", canonical_url="https://www.amazon.com/dp/B0BKQSDF5C")
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        answers: dict = {}
        sent = []

        def fake_get(url, params=None, timeout=None):
            asins = params["asin"].split(",")
            sent.append((asins, params.get("offers")))
            return httpx.Response(200, json={"products": [
                dict(answers[a], asin=a) for a in asins],
                "tokensLeft": 500, "refillRate": 20},
                request=httpx.Request("GET", url))

        mock_keepa_get(monkeypatch, fake_get)

        def reading(tokens):
            db.meta_set(META_KEY, json.dumps({
                "tokens_left": tokens, "refill_rate": 0,
                "seen_at": NOW.isoformat()}))

        # This repair fixture explicitly selects both former wall cards.
        db.meta_set("published_wall", json.dumps(["url|wh", "url|sold"]))
        # Tokens short (below one offers scan plus the deal lane's 10).
        reading(20)
        stats = pipeline._recheck_prices(db, cfg, NOW)
        assert sent == [] and stats["tokens_short"] == 2
        assert db.get_deal("url|wh")["status"] == "dead"
        assert self._published(db, cfg, NOW) == (set(), set(), 0)

        # No opinion: Keepa omits the live order; the stats show nothing.
        db.meta_set("published_wall", json.dumps(["url|wh", "url|sold"]))
        reading(500)
        none = {"offers": [_offer(1, 99999)], "liveOffersOrder": None,
                "stats": {"current": [-1] * 10,
                          "lastOffersUpdate": _km(NOW)}}
        answers.update({"B0BKQSDF5C": none, "B0SOLDUNIT": none})
        stats = pipeline._recheck_prices(db, cfg, NOW)
        assert sorted(sent[0][0]) == ["B0BKQSDF5C", "B0SOLDUNIT"]
        assert sent[0][1] == 20 and stats["checked"] == 0
        row = db.get_deal("url|wh")
        assert row["status"] == "dead" and row["price_rechecked_at"] == ""
        assert self._published(db, cfg, NOW) == (set(), set(), 0)

        # Fresh scan once the inconclusive answer's 6 h retry window has
        # passed: one unit is live at the price, the other is gone.
        sent.clear()
        later = NOW + timedelta(hours=6, minutes=10)
        answers["B0BKQSDF5C"] = {
            "offers": [_offer(1, 99999), _offer(2, 44951)],
            "liveOffersOrder": [0, 1], "stats": {"lastOffersUpdate": _km(later)}}
        answers["B0SOLDUNIT"] = {
            "offers": [_offer(1, 99999)], "liveOffersOrder": [0],
            "stats": {"lastOffersUpdate": _km(later)}}
        db.meta_set("published_wall", json.dumps(["url|wh", "url|sold"]))
        stats = pipeline._recheck_prices(db, cfg, later)
        assert stats["checked"] == 2 and stats["dead"] == 0
        row = db.get_deal("url|wh")
        assert row["status"] == "alive" and row["flags"] == []
        assert row["died_at"] == "" and row["price_since"] == later.isoformat()
        assert row["price_rechecked_at"] == later.isoformat()
        sold = db.get_deal("url|sold")
        assert sold["status"] == "dead" and "price_moved" in sold["flags"]
        assert sold["died_at"] == died.isoformat()
        assert sold["price_rechecked_at"] == later.isoformat()
        assert self._published(db, cfg, later) == (
            {"url|wh"}, {"url|wh"}, 1)
        # Settled: the sold unit is not scanned again, nothing re-alerts.
        sent.clear()
        pipeline._recheck_prices(db, cfg, later + timedelta(minutes=10))
        assert sent == []
        assert self._published(db, cfg, later)[2] == 0
        db.close()

    def test_resighting_revives_a_deal_the_recheck_killed(self, tmp_path,
                                                          monkeypatch):
        """B0BKQSDF5C: judged gone at 01:23; fifteen minutes later Keepa's
        deals feed listed it LOWER and the merge left it dead. The new
        sighting brings it back, and the next pass re-judges it."""
        import httpx
        db = DB(tmp_path / "state.db")
        from dealradar.keepa_budget import KeepaBudget
        KeepaBudget(1200, 0, NOW).save(db)
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        url = "https://www.amazon.com/dp/B0BKQSDF5C"
        key = "url|" + url
        db.meta_set("published_wall", json.dumps([key]))

        def sight(final, pct, at):
            ext = ExtractedDeal(item="Open-box binoculars", store="Amazon",
                                source_url=url, source_name="keepa_amazon_drops",
                                product_url=url, online_only="true",
                                conditions=["used", "open_box"])
            sav = SavingsResult(computable=True, final_price=final,
                                regular_price=999.99, pct_at_checkout=pct,
                                abs_savings=round(999.99 - final, 2),
                                basis="quoted_regular")
            post = _post(source_name="keepa_amazon_drops",
                         uid=f"B0BKQSDF5C:wh:{final:.2f}",
                         url="https://keepa.com/#!product/1-B0BKQSDF5C",
                         text=f"Open-box binoculars yours for ${final:.2f}", published_at=at)
            return upsert_extracted(db, ext, sav, post, cfg, at)

        answer = {}

        def fake_get(url, params=None, timeout=None):
            assert params["offers"] == 20
            return httpx.Response(200, json={"products": [dict(
                answer, asin="B0BKQSDF5C")]},
                request=httpx.Request("GET", url))

        mock_keepa_get(monkeypatch, fake_get)
        assert sight(449.51, 55.05, NOW)["revived"] is False
        # Two readings an hour apart (first: suspect, second: dead).
        suspect_at = NOW + timedelta(minutes=23)
        killed_at = NOW + timedelta(minutes=83)
        answer.update(offers=[_offer(1, 99999)], liveOffersOrder=[0],
                      stats={"lastOffersUpdate": _km(suspect_at)})
        assert pipeline._recheck_prices(db, cfg, suspect_at)["suspect"] == 1
        answer.update(stats={"lastOffersUpdate": _km(killed_at)})
        assert pipeline._recheck_prices(db, cfg, killed_at)["dead"] == 1
        assert "price_moved" in db.get_deal(key)["flags"]

        res = sight(431.20, 56.88, NOW + timedelta(minutes=98))
        assert res["revived"] is True and res["price_improved"] is True
        row = db.get_deal(key)
        assert row["status"] == "alive" and row["final_price"] == 431.20
        assert "price_moved" not in row["flags"]
        assert row["died_at"] == "" and row["price_rechecked_at"] == ""
        # Due at once: a fresh offers scan finds the cheaper unit.
        later = NOW + timedelta(minutes=105)
        answer.update(offers=[_offer(1, 99999), _offer(2, 43120)],
                      liveOffersOrder=[0, 1],
                      stats={"lastOffersUpdate": _km(later)})
        stats = pipeline._recheck_prices(db, cfg, later)
        assert stats["used_checked"] == 1 and stats["dead"] == 0
        assert db.get_deal(key)["status"] == "alive"
        db.close()

    def test_revived_deal_survives_keepa_data_older_than_its_sighting(
            self, tmp_path, monkeypatch):
        """Killed at T1 on a fresh scan, re-sighted at T2 at the same price:
        a cached offers answer from T1 (older than the sighting) is no
        opinion, so the revived deal stays alive in that run."""
        import httpx
        db = DB(tmp_path / "state.db")
        from dealradar.keepa_budget import KeepaBudget
        KeepaBudget(1200, 0, NOW).save(db)
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        url = "https://www.amazon.com/dp/B0BKQSDF5C"
        key = "url|" + url
        db.meta_set("published_wall", json.dumps([key]))

        def sight(source, at):
            ext = ExtractedDeal(item="Open-box binoculars", store="Amazon",
                                source_url=url, source_name=source,
                                product_url=url, online_only="true",
                                conditions=["used", "open_box"])
            sav = SavingsResult(computable=True, final_price=449.51,
                                regular_price=999.99, pct_at_checkout=55.05,
                                abs_savings=550.48, basis="quoted_regular")
            post = _post(source_name=source, uid=f"{source}:B0BKQSDF5C",
                         url=f"https://example.com/{source}",
                         text="Open-box binoculars $449.51", published_at=at)
            return upsert_extracted(db, ext, sav, post, cfg, at)

        t1 = NOW + timedelta(minutes=83)
        gone = {"asin": "B0BKQSDF5C", "offers": [_offer(1, 99999)],
                "liveOffersOrder": [0], "stats": {"lastOffersUpdate": _km(t1)}}
        mock_keepa_get(monkeypatch, lambda url, params=None, timeout=None:
                            httpx.Response(200, json={"products": [gone]},
                                           request=httpx.Request("GET", url)))
        sight("keepa_amazon_drops", NOW)
        # Two readings an hour apart (first: suspect, second: dead).
        assert pipeline._recheck_prices(db, cfg, t1 - timedelta(hours=1))["suspect"] == 1
        assert pipeline._recheck_prices(db, cfg, t1)["dead"] == 1
        t2 = t1 + timedelta(minutes=15)
        res = sight("slickdeals_frontpage", t2)
        assert res["revived"] is True and res["price_improved"] is False
        assert db.get_deal(key)["price_since"] == t2.isoformat()
        stats = pipeline._recheck_prices(db, cfg, t2)
        assert stats["dead"] == 0 and stats["checked"] == 0
        row = db.get_deal(key)
        assert row["status"] == "alive" and row["price_rechecked_at"] == ""
        db.close()
