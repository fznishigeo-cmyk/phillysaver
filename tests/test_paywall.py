"""Paywall (docs/paywall.md), pipeline side — fully offline.

The teaser split, the full-payload upload (httpx MockTransport recording
the request) and the config block.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest

from dealradar.config import AppConfig, load_config
from dealradar.publish import paywall as P


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("NEWSLETTER_SECRET", raising=False)


def _deal(i: int, status: str = "alive") -> dict:
    """A serialized deal as render_site emits it (the fields that matter)."""
    return {"dedupe_key": f"store|item-{i}", "key": f"k{i:015d}", "store": f"Store {i}",
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
            assert stub["key"] == src["key"] and "dedupe_key" not in stub
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
        # The switch is a deliberate operator decision (off until the page
        # and the Worker are live); only its type is asserted here.
        assert isinstance(cfg.paywall_enabled, bool)
        assert cfg.paywall_teaser_count == 8
        assert cfg.paywall_trial_days == 7
        assert isinstance(cfg.paywall_api_url, str)
        # Operator values (set once the Worker and the Google client exist).
        assert isinstance(cfg.paywall_google_client_id, str)

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


class TestTeaserVariety:
    def test_at_most_two_per_store_while_others_remain(self):
        from dealradar.config import AppConfig
        from dealradar.publish.paywall import split_teaser
        cfg = AppConfig(paywall_enabled=True, paywall_teaser_count=4)
        deals = [{"status": "alive", "item": f"case {i}", "store": "Woot",
                  "dedupe_key": f"w{i}", "pct_at_checkout": 90 - i} for i in range(5)]
        deals += [{"status": "alive", "item": "tv", "store": "Walmart", "dedupe_key": "wm",
                   "pct_at_checkout": 60},
                  {"status": "alive", "item": "shoes", "store": "Target", "dedupe_key": "tg",
                   "pct_at_checkout": 55}]
        teaser, locked = split_teaser(deals, cfg)
        unlocked = [d for d in teaser if not d.get("locked")]
        assert [d["item"] for d in unlocked] == ["case 0", "case 1", "tv", "shoes"]
        assert locked == 3 and all(d.get("locked") for d in teaser[4:])

    def test_leftover_slots_fill_from_the_same_store(self):
        from dealradar.config import AppConfig
        from dealradar.publish.paywall import split_teaser
        cfg = AppConfig(paywall_enabled=True, paywall_teaser_count=3)
        deals = [{"status": "alive", "item": f"case {i}", "store": "Woot",
                  "dedupe_key": f"w{i}"} for i in range(4)]
        teaser, locked = split_teaser(deals, cfg)
        assert [d["item"] for d in teaser if not d.get("locked")] == ["case 0", "case 1", "case 2"]
        assert locked == 1


class TestStubOpacity:
    def test_locked_stub_carries_only_an_opaque_key(self):
        """Review finding: the dedupe key names the product link or the item
        words, so a visitor could read locked deals out of deals.json."""
        from dealradar.config import AppConfig
        from dealradar.publish.paywall import split_teaser
        cfg = AppConfig(paywall_enabled=True, paywall_teaser_count=0)
        deals = [{"status": "alive", "item": "Secret Widget", "store": "Walmart",
                  "dedupe_key": "url|https://www.walmart.com/ip/secret-widget/123",
                  "key": "0123456789abcdef", "url": "https://www.walmart.com/ip/123",
                  "pct_at_checkout": 55.0, "category": "home"}]
        teaser, locked = split_teaser(deals, cfg)
        stub = teaser[0]
        assert stub["locked"] and stub["key"] == "0123456789abcdef"
        text = str(stub)
        assert "dedupe_key" not in stub and "walmart.com" not in text
        assert "Secret Widget" not in text and "url" not in stub


class TestContractDoc:
    """docs/paywall.md states what the Worker enforces (worker.test.mjs proves it)."""

    TEXT = (Path(__file__).resolve().parent.parent / "docs" / "paywall.md").read_text()

    def test_trial_is_per_account_and_email_address_not_per_person(self):
        """Review finding: no card fingerprint is checked, so the contract
        cannot promise one trial per person."""
        assert "One trial per account and email address" in self.TEXT
        assert "One Stripe trial per account and email address" in self.TEXT
        assert "One trial per person" not in self.TEXT and "One Stripe trial per person" not in self.TEXT

    def test_checkout_409s_and_portal_paths_are_documented(self):
        for needle in ('409 {error:"payment_issue", me}', '409 {error:"contact_support", me}',
                       "released_customer", "checkout:<ref>", "expires_at", "resource_missing",
                       "its verified email (b)"):
            assert needle in self.TEXT, needle


class TestTeaserRotation:
    """Operator ask: every drop must ACTUALLY change the free 8, so a visitor
    who has not subscribed keeps seeing new deals."""

    def _cfg(self, n=8, pool=48):
        return AppConfig(paywall_enabled=True, paywall_teaser_count=n, paywall_teaser_pool=pool)

    def _drops(self, deals, cfg, k):
        recent, out = [], []
        for _ in range(k):
            teaser, _locked = P.split_teaser(deals, cfg, recent)
            out.append([d["key"] for d in teaser if not d.get("locked") and d["status"] == "alive"])
            recent = P.teaser_memory(teaser, recent, cfg)
        return out

    def test_consecutive_drops_unlock_different_deals_and_cycle_the_pool(self):
        cfg = self._cfg()
        deals = [_deal(i) for i in range(100)]   # 100 stores: no variety skips
        drops = self._drops(deals, cfg, 7)
        assert drops[0] == [d["key"] for d in deals[:8]]     # the best 8 first
        for a, b in zip(drops, drops[1:]):
            assert len(b) == 8 and not set(a) & set(b)       # never last drop's
        assert set().union(*drops[:6]) == {d["key"] for d in deals[:48]}  # top 48 only
        assert drops[6] == drops[0]                           # then round again

    def test_a_new_best_deal_is_unlocked_at_once(self):
        cfg = self._cfg()
        deals = [_deal(i) for i in range(1, 60)]
        teaser, _ = P.split_teaser(deals, cfg)
        recent = P.teaser_memory(teaser, [], cfg)
        hot = _deal(0)
        teaser, _ = P.split_teaser([hot, *deals], cfg, recent)
        assert teaser[0]["key"] == hot["key"] and not teaser[0].get("locked")

    def test_rotation_keeps_store_variety(self):
        cfg = self._cfg(n=4, pool=12)
        deals = []
        for i in range(12):
            d = _deal(i)
            d["store"] = "Amazon" if i % 2 else f"Store {i}"
            deals.append(d)
        for drop in self._drops(deals, cfg, 3):
            stores = [next(d["store"] for d in deals if d["key"] == k) for k in drop]
            assert stores.count("Amazon") <= 2

    def test_store_variety_never_brings_back_last_drops_deals(self):
        """Review finding on live data: a pool of 25 Amazon, 21 Woot and 2
        Walmart deals brought the same 2 Walmart deals back every drop,
        because the store cap reached into the recently shown ones."""
        cfg = self._cfg()
        stores = ["Amazon"] * 25 + ["Woot"] * 21 + ["Walmart"] * 2
        deals = []
        for i, store in enumerate(stores):
            d = _deal(i)
            d["store"] = store
            deals.append(d)
        drops = self._drops(deals, cfg, 6)
        for a, b in zip(drops, drops[1:]):
            assert not set(a) & set(b)
        assert set().union(*drops) == {d["key"] for d in deals}  # the whole pool, once

    def test_memory_is_bounded_and_off_with_a_pool_of_n(self):
        cfg = self._cfg(n=8, pool=48)
        teaser, _ = P.split_teaser([_deal(i) for i in range(60)], cfg)
        assert len(P.teaser_memory(teaser, [f"x{i}" for i in range(100)], cfg)) == 40
        cfg = self._cfg(n=8, pool=8)
        assert P.teaser_memory(teaser, ["x"], cfg) == []

    def test_config_knob(self):
        assert load_config().paywall_teaser_pool == 48
        assert AppConfig().paywall_teaser_pool == 48
