"""Tests for the quality trio: deal critic, category tags, second opinions.

Offline only — fake clients/parsers, no network, no real model calls.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dealradar import pipeline, rank
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.dedupe import _new_payload, upsert_extracted
from dealradar.errors import SpendCapReached
from dealradar.models import ExtractedDeal, RawPost, SavingsResult
from dealradar.parse.claude_client import ClaudeParser, critique_deal
from dealradar.parse.extractor import _smells_like_deal, parse_posts
from dealradar.parse.schema import CriticVerdict, LLMDeal, LLMEvidence, to_extracted

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
    """messages.parse stub answering CriticVerdict calls; records models used."""

    def __init__(self, plausible: bool = True) -> None:
        self.plausible = plausible
        self.calls: list[dict] = []

    def parse(self, *, model, max_tokens, system, messages, output_format, **kw):
        self.calls.append({"model": model, "output_format": output_format,
                           "text": messages[0]["content"]})
        if output_format is CriticVerdict:
            return _FakeResponse(CriticVerdict(
                plausible=self.plausible, confidence=0.8, reason="stub"))
        return _FakeResponse(output_format.model_validate({"deals": []}))


class _FakeClient:
    def __init__(self, messages) -> None:
        self.models = _FakeModels()
        self.messages = messages


def _cfg(**over) -> AppConfig:
    cfg = load_config()
    cfg.critic_enabled = True
    cfg.critic_model = "claude-sonnet-5-5"
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
        assert stats == {"judged": 1, "suspect": 1}
        row = db.get_deal("si|acme|widget")
        assert "suspect_msrp" in row["flags"]
        assert row["critic"]["plausible"] is False
        db.close()

    def test_plausible_verdict_persists_and_is_not_rejudged(self, tmp_path):
        db = self._db_with_deal(tmp_path)
        cfg = _cfg()
        msgs = _CriticMessages(True)
        parser = ClaudeParser(cfg, client=_FakeClient(msgs))
        assert pipeline._run_critic(db, cfg, parser, NOW) == {"judged": 1,
                                                              "suspect": 0}
        row = db.get_deal("si|acme|widget")
        assert row["critic"]["plausible"] is True
        assert "suspect_msrp" not in row["flags"]
        # Second pass: verdict stored -> zero further API calls.
        assert pipeline._run_critic(db, cfg, parser, NOW) == {"judged": 0,
                                                              "suspect": 0}
        assert len(msgs.calls) == 1
        db.close()

    def test_unpublishable_deals_never_cost_a_critic_call(self, tmp_path):
        db = self._db_with_deal(tmp_path, pct_at_checkout=40.0)  # below floor
        cfg = _cfg()
        msgs = _CriticMessages()
        parser = ClaudeParser(cfg, client=_FakeClient(msgs))
        assert pipeline._run_critic(db, cfg, parser, NOW) == {"judged": 0,
                                                              "suspect": 0}
        assert msgs.calls == []
        db.close()

    def test_no_parser_or_disabled_means_noop(self, tmp_path):
        db = self._db_with_deal(tmp_path)
        assert pipeline._run_critic(db, _cfg(), None, NOW) == {"judged": 0,
                                                               "suspect": 0}
        cfg_off = _cfg(critic_enabled=False)
        parser = ClaudeParser(cfg_off, client=_FakeClient(_CriticMessages()))
        assert pipeline._run_critic(db, cfg_off, parser, NOW) == {"judged": 0,
                                                                  "suspect": 0}
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
        assert pipeline._run_critic(db, cfg, parser, NOW) == {"judged": 1,
                                                              "suspect": 1}
        row = db.get_deal("si|acme|widget")
        assert row["critic"]["rechecked"] is True
        assert "suspect_msrp" in row["flags"]
        # Settled now: no third opinion ever.
        assert pipeline._run_critic(db, cfg, parser, NOW) == {"judged": 0,
                                                              "suspect": 0}
        assert len(msgs.calls) == 1
        db.close()

    def test_confident_pass_is_never_rejudged(self, tmp_path):
        db = self._db_with_deal(
            tmp_path, critic={"plausible": True, "confidence": 0.9,
                              "reason": "solid"})
        cfg = _cfg()
        msgs = _CriticMessages(plausible=False)
        parser = ClaudeParser(cfg, client=_FakeClient(msgs))
        assert pipeline._run_critic(db, cfg, parser, NOW) == {"judged": 0,
                                                              "suspect": 0}
        assert msgs.calls == []
        db.close()

    def test_spend_cap_stops_the_critic_cleanly(self, tmp_path):
        db = self._db_with_deal(tmp_path)
        cfg = _cfg()
        parser = ClaudeParser(cfg, client=_FakeClient(_CriticMessages()))
        parser.spend_usd = parser.run_cap_usd  # cap already reached
        assert pipeline._run_critic(db, cfg, parser, NOW) == {"judged": 0,
                                                              "suspect": 0}
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
        self._first = list(first_pass or [])
        self._second = list(second_pass or [])

    def extract(self, text, meta, model=None):
        self.calls.append(model)
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
        posts = [_post(text="Huge deal! Widget sale now $10.00")]
        pairs, stats = parse_posts(posts, parser, cfg, NOW, {})
        assert stats["second_opinions"] == 1
        assert parser.calls == [None, "claude-sonnet-5-5"]
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
                       text=f"Flash sale #{i}: widget now $1{i}.00")
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
        parser = ClaudeParser(_cfg(), client=_FakeClient(msgs))
        assert parser.extract("post one", {}) == []
        assert parser.extract("post two", {}) == []
        assert msgs.parse_calls == 1          # the rejection is paid once
        assert msgs.create_calls == 2         # both posts used the fallback


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
        monkeypatch.setattr(httpx, "get", lambda *a, **k:
                            self._stats_response([2999, 3105, -1]))
        assert verify.recheck_amazon_price(deal, cfg) is True
        monkeypatch.setattr(httpx, "get", lambda *a, **k:
                            self._stats_response([9999, 10500, -1]))
        assert verify.recheck_amazon_price(deal, cfg) is False

    def test_used_deal_checks_warehouse_price_and_gone_offer(self, monkeypatch):
        import httpx

        from dealradar import verify
        cfg = _cfg()
        cfg.feature_flags["keepa"] = True
        monkeypatch.setenv("KEEPA_KEY", "kkey")
        used_deal = {"asin": "B0TESTASIN1", "final_price": 12.07,
                     "conditions": ["used", "limited_qty"]}
        current = [8155, 8155, -1, -1, -1, -1, -1, -1, -1, 1207]
        monkeypatch.setattr(httpx, "get",
                            lambda *a, **k: self._stats_response(current))
        assert verify.recheck_amazon_price(used_deal, cfg) is True
        # Warehouse offer vanished entirely -> the deal is over.
        gone = [8155, 8155, -1, -1, -1, -1, -1, -1, -1, -1]
        monkeypatch.setattr(httpx, "get",
                            lambda *a, **k: self._stats_response(gone))
        assert verify.recheck_amazon_price(used_deal, cfg) is False

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

        def fake_recheck(deal, _cfg):
            calls.append(deal["asin"])
            return False

        monkeypatch.setattr(verify, "recheck_amazon_price", fake_recheck)
        stats = pipeline._recheck_prices(db, cfg, NOW)
        assert stats == {"checked": 1, "dead": 1}
        row = db.get_deal(d["dedupe_key"])
        assert row["status"] == "dead"
        assert "price_moved" in row["flags"]
        assert row["price_rechecked_at"]
        # Within the 6-hour window nothing is re-checked (and dead deals
        # are out of the alive set anyway).
        assert pipeline._recheck_prices(db, cfg, NOW) == {"checked": 0,
                                                          "dead": 0}
        assert len(calls) == 1
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
        assert cfg.used_min_pct == pytest.approx(60.0)
        assert cfg.parse_model == "claude-sonnet-5-5"
        assert cfg.second_opinion_model == "claude-sonnet-5-5"
        assert cfg.max_second_opinions_per_run == 8
        assert cfg.critic_model == "claude-sonnet-5-5"
        assert cfg.critic_enabled is True
        assert cfg.exclude_suspect_msrp is True
        assert cfg.spend_cap_usd_per_day == pytest.approx(5.00)
