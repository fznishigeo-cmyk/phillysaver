"""Paywall (docs/paywall.md), pipeline side — fully offline.

The teaser split, the full-payload upload (httpx MockTransport recording
the request) and the config block.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import paywall as P


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("NEWSLETTER_SECRET", raising=False)


def _deal(i: int, status: str = "alive") -> dict:
    """A serialized deal as render_site emits it (the fields that matter)."""
    return {"dedupe_key": f"store|item-{i}", "store": f"Store {i}",
            "item": f"Secret Item {i}", "url": f"https://shop.example/{i}",
            "image_url": f"https://img.example/{i}.jpg",
            "final_price": 1.0 * i, "regular_price": 10.0 * i,
            "pct_at_checkout": 90.0 - i, "codes": ["SAVE"], "category": "toys",
            "conditions": ["used"], "headline": f"Secret Item {i} is cheap.",
            "status": status, "alive": status == "alive",
            "first_seen_at": "2026-10-10T12:00:00+00:00",
            "credits": [{"name": "src", "url": "https://src.example/p"}]}


class TestSplitTeaser:
    def test_first_n_alive_full_rest_stubbed_dead_last(self):
        cfg = AppConfig(paywall_enabled=True, paywall_teaser_count=8)
        # Site order interleaves dead deals; 11 alive + 2 dead.
        deals = [_deal(0), _deal(1, "dead"), *[_deal(i) for i in range(2, 12)],
                 _deal(12, "dead")]
        alive = [d for d in deals if d["status"] == "alive"]
        teaser, locked = P.split_teaser(deals, cfg)

        assert locked == 3
        assert len(teaser) == len(deals)
        assert teaser[:8] == alive[:8]              # full, same order
        for stub, src in zip(teaser[8:11], alive[8:]):
            assert set(stub) == {"locked", *P.STUB_KEYS}
            assert stub["locked"] is True
            assert stub["dedupe_key"] == src["dedupe_key"]
            assert stub["store"] == src["store"]
            assert stub["pct_at_checkout"] == src["pct_at_checkout"]
            assert stub["image_url"] == src["image_url"]
            assert stub["status"] == "alive" and stub["conditions"] == ["used"]
        assert teaser[11:] == [deals[1], deals[12]]  # dead: unchanged, last
        # The input is not mutated.
        assert all("item" in d and "url" in d for d in deals)

    def test_fewer_alive_than_teaser_count_locks_nothing(self):
        cfg = AppConfig(paywall_enabled=True, paywall_teaser_count=8)
        deals = [_deal(0), _deal(1), _deal(2, "dead")]
        assert P.split_teaser(deals, cfg) == (deals, 0)
        assert P.split_teaser([], cfg) == ([], 0)

    def test_disabled_is_passthrough(self):
        cfg = AppConfig(paywall_enabled=False, paywall_teaser_count=1)
        deals = [_deal(i) for i in range(5)]
        teaser, locked = P.split_teaser(deals, cfg)
        assert teaser is deals and locked == 0


def _recording_client(status: int = 204, error: Exception | None = None):
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if error is not None:
            raise error
        return httpx.Response(status)

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


PAYLOAD = {"meta": {"count": 2, "locked_count": 0}, "deals": [_deal(0), _deal(1)]}


class TestPublishFull:
    def _cfg(self, api_url="https://wowpriced-api.example.workers.dev") -> AppConfig:
        return AppConfig(paywall_enabled=True, paywall_api_url=api_url)

    def test_skips_without_api_url(self, caplog, monkeypatch):
        monkeypatch.setenv("NEWSLETTER_SECRET", "s3cret")
        client, calls = _recording_client()
        with caplog.at_level(logging.INFO, logger="dealradar.publish.paywall"):
            assert P.publish_full(PAYLOAD, self._cfg(api_url=""), client) is False
        assert calls == []
        assert "paywall publish skipped: no api_url" in caplog.text

    def test_skips_without_the_secret(self, caplog):
        client, calls = _recording_client()
        with caplog.at_level(logging.INFO, logger="dealradar.publish.paywall"):
            assert P.publish_full(PAYLOAD, self._cfg(), client) is False
        assert calls == []
        assert "paywall publish skipped: NEWSLETTER_SECRET unset" in caplog.text

    def test_posts_json_with_bearer_header(self, monkeypatch):
        monkeypatch.setenv("NEWSLETTER_SECRET", "s3cret")
        client, calls = _recording_client(204)
        # A trailing slash on api_url must not double up in the path.
        cfg = self._cfg("https://wowpriced-api.example.workers.dev/")
        assert P.publish_full(PAYLOAD, cfg, client) is True
        (req,) = calls
        assert req.method == "POST"
        assert str(req.url) == "https://wowpriced-api.example.workers.dev/api/publish"
        assert req.headers["Authorization"] == "Bearer s3cret"
        assert req.headers["Content-Type"].startswith("application/json")
        assert json.loads(req.content) == PAYLOAD

    def test_server_error_returns_false_without_raising(self, caplog, monkeypatch):
        monkeypatch.setenv("NEWSLETTER_SECRET", "s3cret")
        client, calls = _recording_client(500)
        with caplog.at_level(logging.WARNING, logger="dealradar.publish.paywall"):
            assert P.publish_full(PAYLOAD, self._cfg(), client) is False
        assert len(calls) == 1
        assert "paywall publish failed: 500" in caplog.text

    def test_transport_error_returns_false_without_raising(self, caplog, monkeypatch):
        monkeypatch.setenv("NEWSLETTER_SECRET", "s3cret")
        client, _ = _recording_client(error=httpx.ConnectError("boom"))
        with caplog.at_level(logging.WARNING, logger="dealradar.publish.paywall"):
            assert P.publish_full(PAYLOAD, self._cfg(), client) is False
        assert "paywall publish failed: boom" in caplog.text

    def test_empty_payload_never_overwrites_the_store(self, monkeypatch):
        monkeypatch.setenv("NEWSLETTER_SECRET", "s3cret")
        client, calls = _recording_client()
        assert P.publish_full({}, self._cfg(), client) is False
        assert calls == []


class TestConfig:
    def test_config_yaml_block(self):
        cfg = load_config()
        assert cfg.paywall_enabled is True
        assert cfg.paywall_teaser_count == 8
        assert cfg.paywall_trial_days == 7
        assert cfg.paywall_api_url == ""
        assert cfg.paywall_google_client_id == ""

    def test_dataclass_defaults_match_the_block(self):
        cfg = AppConfig()
        assert cfg.paywall_enabled is True
        assert cfg.paywall_teaser_count == 8
        assert cfg.paywall_trial_days == 7
        assert cfg.paywall_api_url == ""
        assert cfg.paywall_google_client_id == ""

    def test_missing_or_partial_block_is_tolerated(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text("publish:\n  min_pct: 40\n")
        cfg = load_config(path)
        assert cfg.paywall_enabled is True and cfg.paywall_teaser_count == 8
        path.write_text("paywall:\n  enabled: false\n  teaser_count: 3\n"
                        "  api_url: \"https://api.example/\"\n")
        cfg = load_config(path)
        assert cfg.paywall_enabled is False
        assert cfg.paywall_teaser_count == 3
        assert cfg.paywall_trial_days == 7
        assert cfg.paywall_api_url == "https://api.example"  # trailing slash dropped
        assert cfg.paywall_google_client_id == ""
