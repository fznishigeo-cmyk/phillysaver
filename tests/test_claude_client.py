"""Tests for dealradar.parse.claude_client — fully offline, fake clients only."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import anthropic
import httpx2
import pytest

from dealradar.config import AppConfig
from dealradar.errors import ParserNotConfigured, SpendCapReached
from dealradar.parse.claude_client import FALLBACK_MODEL, ClaudeParser
from dealradar.parse.schema import LLMDeal, LLMDeals


# ---------------------------------------------------------------- fakes

class FakeUsage:
    def __init__(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeModelInfo:
    def __init__(self, id: str, created_at: datetime) -> None:
        self.id = id
        self.created_at = created_at


class FakeModelsEndpoint:
    """models.list() auto-paginates when iterated — mimic with a plain iterator."""

    def __init__(self, models=(), error: Exception | None = None) -> None:
        self._models = list(models)
        self._error = error

    def list(self):
        if self._error is not None:
            raise self._error
        return iter(self._models)


class FakeParseResponse:
    def __init__(self, parsed_output, usage: FakeUsage,
                 stop_reason: str = "end_turn") -> None:
        self.parsed_output = parsed_output
        self.usage = usage
        self.stop_reason = stop_reason


class FakeTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class FakeCreateResponse:
    def __init__(self, text: str, usage: FakeUsage,
                 stop_reason: str = "end_turn") -> None:
        self.content = [FakeTextBlock(text)]
        self.usage = usage
        self.stop_reason = stop_reason


class FakeCreateOnlyMessages:
    """messages endpoint WITHOUT .parse — forces the JSON fallback path."""

    def __init__(self, create_responses=()) -> None:
        self._create = list(create_responses)
        self.create_calls: list[dict] = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        r = self._create.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeMessagesWithParse(FakeCreateOnlyMessages):
    """messages endpoint with .parse (structured outputs path)."""

    def __init__(self, parse_responses=(), create_responses=()) -> None:
        super().__init__(create_responses)
        self._parse = list(parse_responses)
        self.parse_calls: list[dict] = []

    def parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        r = self._parse.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeClient:
    def __init__(self, models: FakeModelsEndpoint, messages) -> None:
        self.models = models
        self.messages = messages


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


# ------------------------------------------------------------- helpers

HAIKU_NEW = "claude-haiku-5-5"

def dt(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


def default_models() -> FakeModelsEndpoint:
    return FakeModelsEndpoint([
        FakeModelInfo("claude-opus-5-5", dt(2026, 9, 15)),
        FakeModelInfo("claude-sonnet-5-5", dt(2026, 9, 1)),
        FakeModelInfo("claude-haiku-4-5", dt(2025, 10, 1)),
        FakeModelInfo(HAIKU_NEW, dt(2026, 8, 1)),
    ])


def make_cfg(**over) -> AppConfig:
    cfg = AppConfig()
    cfg.spend_cap_usd_per_run = 0.50
    cfg.retries_on_invalid_json = 2
    # These tests exercise the structured messages.parse path specifically;
    # production defaults it off (see test_structured_output_default_*).
    cfg.parse_structured_output = True
    cfg.model_pricing_per_mtok = {
        "default": {"input": 1.00, "output": 5.00},
        HAIKU_NEW: {"input": 0.10, "output": 0.50},
    }
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def make_parser(messages, *, cfg=None, models=None, sleep=None) -> ClaudeParser:
    client = FakeClient(models or default_models(), messages)
    return ClaudeParser(cfg or make_cfg(), client=client,
                        sleep=sleep or FakeSleep())


def one_deal() -> LLMDeals:
    return LLMDeals(deals=[LLMDeal(item="Widget", store="Amazon")])


META = {"source_name": "s", "source_url": "https://s.example/p", "title": "t"}

_HTTPX_REQ = httpx2.Request("POST", "https://api.example.test/v1/messages")


def status_error(status: int) -> anthropic.APIStatusError:
    resp = httpx2.Response(status, request=_HTTPX_REQ)
    if status == 429:
        return anthropic.RateLimitError("rate limited", response=resp, body=None)
    if status == 400:
        return anthropic.BadRequestError("bad request", response=resp, body=None)
    if status == 403:
        return anthropic.PermissionDeniedError("denied", response=resp, body=None)
    return anthropic.InternalServerError("server error", response=resp, body=None)


def connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(request=_HTTPX_REQ)


# -------------------------------------------------------- model selection

class TestModelSelection:
    def test_picks_newest_haiku_from_mixed_list(self):
        parser = make_parser(FakeMessagesWithParse())
        # sonnet/opus are newer overall, but 'haiku' family wins; newest haiku picked
        assert parser.model == HAIKU_NEW

    def test_falls_back_to_newest_sonnet_when_no_haiku(self):
        models = FakeModelsEndpoint([
            FakeModelInfo("claude-opus-5-5", dt(2026, 9, 15)),
            FakeModelInfo("claude-sonnet-5", dt(2026, 1, 1)),
            FakeModelInfo("claude-sonnet-5-5", dt(2026, 9, 1)),
        ])
        parser = make_parser(FakeMessagesWithParse(), models=models)
        assert parser.model == "claude-sonnet-5-5"

    def test_first_listed_when_no_haiku_or_sonnet(self):
        models = FakeModelsEndpoint([
            FakeModelInfo("claude-opus-5-5", dt(2026, 9, 15)),
            FakeModelInfo("claude-opus-5", dt(2026, 1, 1)),
        ])
        parser = make_parser(FakeMessagesWithParse(), models=models)
        assert parser.model == "claude-opus-5-5"

    def test_last_resort_constant_when_endpoint_fails(self, caplog):
        models = FakeModelsEndpoint(error=RuntimeError("models endpoint down"))
        with caplog.at_level("ERROR", logger="dealradar.parse.claude_client"):
            parser = make_parser(FakeMessagesWithParse(), models=models)
        assert parser.model == FALLBACK_MODEL
        assert any("FALLING BACK" in r.message for r in caplog.records)

    def test_missing_key_raises_parser_not_configured(self, monkeypatch):
        monkeypatch.delenv("DEALS_CLAUDE_API_KEY", raising=False)
        with pytest.raises(ParserNotConfigured):
            ClaudeParser(make_cfg())  # client=None and no secret set


# ---------------------------------------------------- parse path + spend

class TestParsePathAndSpend:
    def test_parse_path_returns_deals_and_accounts_spend(self):
        messages = FakeMessagesWithParse(parse_responses=[
            FakeParseResponse(one_deal(), FakeUsage(200_000, 100_000)),
        ])
        parser = make_parser(messages)
        deals = parser.extract("Widget now $10.00", META)
        assert [d.item for d in deals] == ["Widget"]
        # haiku pricing: (200k * 0.10 + 100k * 0.50) / 1e6 = 0.07
        assert parser.spend_usd == pytest.approx(0.07)
        call = messages.parse_calls[0]
        assert call["model"] == HAIKU_NEW
        assert call["output_format"] is LLMDeals
        assert call["max_tokens"] == 4096
        assert "BEGIN POST" in call["messages"][0]["content"]

    def test_default_pricing_used_for_unknown_model(self):
        models = FakeModelsEndpoint([FakeModelInfo("claude-haiku-9", dt(2027, 1, 1))])
        messages = FakeMessagesWithParse(parse_responses=[
            FakeParseResponse(one_deal(), FakeUsage(100_000, 10_000)),
        ])
        parser = make_parser(messages, models=models)
        parser.extract("text", META)
        # default pricing: (100k * 1.00 + 10k * 5.00) / 1e6 = 0.15
        assert parser.spend_usd == pytest.approx(0.15)

    def test_spend_cap_aborts_before_next_call(self):
        cfg = make_cfg(spend_cap_usd_per_run=0.10)
        messages = FakeMessagesWithParse(parse_responses=[
            # (700k * 0.10 + 100k * 0.50) / 1e6 = 0.12 >= 0.10 cap
            FakeParseResponse(one_deal(), FakeUsage(700_000, 100_000)),
            FakeParseResponse(one_deal(), FakeUsage(1, 1)),
        ])
        parser = make_parser(messages, cfg=cfg)
        assert len(parser.extract("post one", META)) == 1
        assert parser.spend_usd == pytest.approx(0.12)
        with pytest.raises(SpendCapReached):
            parser.extract("post two", META)
        assert len(messages.parse_calls) == 1  # the second call never went out

    def test_refusal_stop_reason_means_zero_deals(self):
        messages = FakeMessagesWithParse(parse_responses=[
            FakeParseResponse(None, FakeUsage(1_000, 500), stop_reason="refusal"),
        ])
        parser = make_parser(messages)
        assert parser.extract("sketchy post", META) == []
        assert parser.spend_usd > 0  # refused responses still cost tokens


# ------------------------------------------------------- retry behavior

class TestTransportRetries:
    def test_429_retried_once_with_2s_sleep_then_succeeds(self):
        sleep = FakeSleep()
        messages = FakeMessagesWithParse(parse_responses=[
            status_error(429),
            FakeParseResponse(one_deal(), FakeUsage(10, 10)),
        ])
        parser = make_parser(messages, sleep=sleep)
        deals = parser.extract("text", META)
        assert len(deals) == 1
        assert sleep.calls == [2.0]
        assert len(messages.parse_calls) == 2

    def test_persistent_5xx_skips_post_after_one_retry(self, caplog):
        sleep = FakeSleep()
        messages = FakeMessagesWithParse(parse_responses=[
            status_error(500), status_error(500),
        ])
        parser = make_parser(messages, sleep=sleep)
        with caplog.at_level("WARNING", logger="dealradar.parse.claude_client"):
            assert parser.extract("text", META) == []
        assert sleep.calls == [2.0]
        assert len(messages.parse_calls) == 2
        assert any("skipping post" in r.message for r in caplog.records)

    def test_connection_error_retried_then_skipped(self):
        sleep = FakeSleep()
        messages = FakeMessagesWithParse(parse_responses=[
            connection_error(), connection_error(),
        ])
        parser = make_parser(messages, sleep=sleep)
        assert parser.extract("text", META) == []
        assert sleep.calls == [2.0]

    def test_non_retryable_api_error_skips_immediately(self):
        sleep = FakeSleep()
        messages = FakeMessagesWithParse(parse_responses=[status_error(403)])
        parser = make_parser(messages, sleep=sleep)
        assert parser.extract("text", META) == []
        assert sleep.calls == []
        assert len(messages.parse_calls) == 1


# ------------------------------------------------------- JSON fallback

def valid_json_payload() -> str:
    return json.dumps({"deals": [{
        "item": "Widget", "store": "Amazon", "final_price": 10.0,
        "evidence": {"final_price_quote": "now $10.00"},
    }]})


class TestJsonFallback:
    def test_no_parse_attribute_falls_back_to_create(self):
        messages = FakeCreateOnlyMessages(create_responses=[
            FakeCreateResponse(valid_json_payload(), FakeUsage(1_000, 500)),
        ])
        parser = make_parser(messages)
        deals = parser.extract("Widget now $10.00", META)
        assert [d.item for d in deals] == ["Widget"]
        assert deals[0].final_price == 10.0
        assert len(messages.create_calls) == 1
        assert parser.spend_usd > 0
        # fallback instructs JSON-only output
        assert "JSON" in messages.create_calls[0]["system"]

    def test_bad_request_on_parse_falls_back_to_create(self):
        messages = FakeMessagesWithParse(
            parse_responses=[status_error(400)],
            create_responses=[
                FakeCreateResponse(valid_json_payload(), FakeUsage(10, 10)),
            ],
        )
        parser = make_parser(messages)
        deals = parser.extract("text", META)
        assert len(deals) == 1
        assert len(messages.parse_calls) == 1
        assert len(messages.create_calls) == 1

    def test_invalid_json_retries_twice_then_skips(self):
        # retries_on_invalid_json=2 -> initial attempt + 2 retries = 3 calls
        messages = FakeCreateOnlyMessages(create_responses=[
            FakeCreateResponse("not json at all", FakeUsage(10, 10)),
            FakeCreateResponse('{"deals": {"not": "a list"}}', FakeUsage(10, 10)),  # fails validation
            FakeCreateResponse("{broken", FakeUsage(10, 10)),
        ])
        parser = make_parser(messages, cfg=make_cfg(retries_on_invalid_json=2))
        assert parser.extract("text", META) == []
        assert len(messages.create_calls) == 3
        # every attempt was still accounted against the spend cap
        assert parser.spend_usd == pytest.approx(3 * (10 * 0.10 + 10 * 0.50) / 1e6)

    def test_type_slips_repaired_without_a_paid_reask(self):
        """Live: a third of Haiku's answers failed validation over trivia
        ("" for an empty list, true for "true", 35 for "35", a store-less
        deal with an Amazon link) and each cost up to three paid re-asks,
        then the post. One answer, every deal kept, one call."""
        slips = json.dumps({"deals": [
            {"item": "Widget", "store": "", "final_price": 10.0,
             "product_url": "https://www.amazon.com/dp/B0TESTASIN1?tag=x",
             "rebates": "", "codes": "", "online_only": True,
             "shipping_cost_or_threshold": 35, "deferred_value": 11,
             "evidence": {"final_price_quote": "now $10.00", "pct_quote": None}},
            {"item": "Gadget", "store": "Target", "final_price": 5.0,
             "stack_steps": "clip the 50% coupon",
             "evidence": {"final_price_quote": "$5.00"}},
            {"item": "", "store": "Nowhere"},
        ]})
        messages = FakeCreateOnlyMessages(create_responses=[
            FakeCreateResponse(slips, FakeUsage(10, 10)),
            FakeCreateResponse("never asked", FakeUsage(10, 10)),
        ])
        parser = make_parser(messages, cfg=make_cfg(retries_on_invalid_json=2))
        deals = parser.extract("text", META)
        assert len(messages.create_calls) == 1
        assert [d.item for d in deals] == ["Widget", "Gadget"]
        w, g = deals
        assert w.store == "Amazon" and w.rebates == [] and w.codes == []
        assert w.online_only == "true"
        assert w.shipping_cost_or_threshold == "35" and w.deferred_value == "11"
        assert g.stack_steps == ["clip the 50% coupon"]

    def test_invalid_then_valid_json_recovers(self):
        messages = FakeCreateOnlyMessages(create_responses=[
            FakeCreateResponse("garbage", FakeUsage(10, 10)),
            FakeCreateResponse(valid_json_payload(), FakeUsage(10, 10)),
        ])
        parser = make_parser(messages)
        deals = parser.extract("text", META)
        assert len(deals) == 1
        assert len(messages.create_calls) == 2

    def test_fenced_json_tolerated(self):
        fenced = "```json\n" + valid_json_payload() + "\n```"
        messages = FakeCreateOnlyMessages(create_responses=[
            FakeCreateResponse(fenced, FakeUsage(10, 10)),
        ])
        parser = make_parser(messages)
        assert len(parser.extract("text", META)) == 1

    def test_refusal_on_fallback_means_zero_deals(self):
        messages = FakeCreateOnlyMessages(create_responses=[
            FakeCreateResponse("", FakeUsage(10, 10), stop_reason="refusal"),
        ])
        parser = make_parser(messages)
        assert parser.extract("text", META) == []
        assert len(messages.create_calls) == 1

    def test_spend_cap_checked_before_fallback_calls(self):
        cfg = make_cfg(spend_cap_usd_per_run=0.0)  # cap already hit at $0
        messages = FakeCreateOnlyMessages(create_responses=[
            FakeCreateResponse(valid_json_payload(), FakeUsage(10, 10)),
        ])
        parser = make_parser(messages, cfg=cfg)
        with pytest.raises(SpendCapReached):
            parser.extract("text", META)
        assert messages.create_calls == []
