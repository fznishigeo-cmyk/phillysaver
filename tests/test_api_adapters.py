"""Offline tests for the official-API source adapters.

CONCURRENT-BUILD NOTE: a sibling agent owns ``dealradar/fetch/base.py``. If
that module is not importable yet (it may still be being written), we install
a minimal stub in ``sys.modules`` exposing a plain, abstract-free
``SourceAdapter`` class BEFORE importing the adapters, so these tests never
depend on the sibling's file existing. When the real module is present, the
real abstract base is used.

All tests are fully offline: HTTP goes through ``httpx.MockTransport``,
Telegram goes through a fake client injected via ``client_factory``, and all
sleeps go through a recording ``sleep_fn``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest


def _ensure_base_stub() -> None:
    try:
        import dealradar.fetch.base  # noqa: F401
    except Exception:
        stub = types.ModuleType("dealradar.fetch.base")

        class SourceAdapter:  # abstract-free stand-in for the real ABC
            pass

        stub.SourceAdapter = SourceAdapter
        sys.modules["dealradar.fetch.base"] = stub


_ensure_base_stub()

from dealradar.config import AppConfig  # noqa: E402
from dealradar.errors import AdapterDisabled, AdapterNotConfigured  # noqa: E402
from dealradar.fetch.instagram_source import InstagramAdapter  # noqa: E402
from dealradar.fetch.meh import MehAdapter  # noqa: E402
from dealradar.fetch.reddit import RedditAdapter  # noqa: E402
from dealradar.fetch.telegram_source import TelegramAdapter  # noqa: E402
from dealradar.fetch.threads_source import ThreadsAdapter  # noqa: E402
from dealradar.fetch.woot import WootAdapter  # noqa: E402
from dealradar.models import SourceConfig  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 9, 15, 0, 0, tzinfo=timezone.utc)
REDDIT_UA = "linux:dealradar:v0.1 (by /u/tester)"

ALL_ADAPTER_SECRETS = [
    "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT",
    "WOOT_API_KEY", "MEH_API_KEY",
    "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION",
    "THREADS_TOKEN",
]


@pytest.fixture(autouse=True)
def _no_adapter_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test with a clean secret environment."""
    for name in ALL_ADAPTER_SECRETS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def cfg() -> AppConfig:
    return AppConfig()


def _fixture_json(name: str):
    return json.loads((FIXTURES / name).read_text())


def _source(name: str, adapter: str, url: str = "https://example.com") -> SourceConfig:
    return SourceConfig(name=name, method="api", url=url, adapter=adapter)


def _mock_client(handler, cfg: AppConfig) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": cfg.resolved_user_agent()},
        timeout=cfg.request_timeout_s,
    )


# ---------------------------------------------------------------- reddit


class TestReddit:
    SRC = SourceConfig(name="reddit_deals", method="api",
                       url="https://oauth.reddit.com/r/deals/new", adapter="reddit")

    def _set_secrets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "csecret")
        monkeypatch.setenv("REDDIT_USER_AGENT", REDDIT_UA)

    def test_missing_credentials_raise(self, cfg: AppConfig) -> None:
        with pytest.raises(AdapterNotConfigured, match="reddit credentials missing"):
            RedditAdapter().fetch_posts(self.SRC, None, None, cfg, NOW, None)

    def test_partially_missing_credentials_raise(
        self, cfg: AppConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "csecret")
        # REDDIT_USER_AGENT still missing
        with pytest.raises(AdapterNotConfigured):
            RedditAdapter().fetch_posts(self.SRC, None, None, cfg, NOW, None)

    def test_mapping_auth_and_token_cache(
        self, cfg: AppConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._set_secrets(monkeypatch)
        counts = {"token": 0, "listing": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if (request.url.host == "www.reddit.com"
                    and request.url.path == "/api/v1/access_token"):
                counts["token"] += 1
                assert request.method == "POST"
                assert request.headers["Authorization"].startswith("Basic ")
                assert b"grant_type=client_credentials" in request.read()
                # Reddit's required UA format, sent verbatim.
                assert request.headers["User-Agent"] == REDDIT_UA
                return httpx.Response(200, json=_fixture_json("api_reddit_token.json"))
            if request.url.host == "oauth.reddit.com":
                counts["listing"] += 1
                assert request.url.path == "/r/deals/new.json"
                assert request.url.params["limit"] == "50"
                assert request.url.params["raw_json"] == "1"
                assert request.headers["Authorization"] == "bearer test-token-abc"
                assert request.headers["User-Agent"] == REDDIT_UA
                return httpx.Response(200, json=_fixture_json("api_reddit_listing.json"))
            raise AssertionError(f"unexpected request: {request.url}")

        sleeps: list[float] = []
        adapter = RedditAdapter(http_client=_mock_client(handler, cfg),
                                sleep_fn=sleeps.append)
        posts = adapter.fetch_posts(self.SRC, None, None, cfg, NOW, None)

        # Stickied post skipped; order preserved.
        assert [p.uid for p in posts] == ["t3_self1", "t3_link1"]

        self_post = posts[0]
        assert self_post.source_name == "reddit_deals"
        assert self_post.url == "https://www.reddit.com/r/deals/comments/self1/anker_charger_50_off/"
        assert self_post.title == "Anker charger 50% off"
        assert self_post.text.startswith("Anker charger 50% off\n")
        assert "now $19.99 with code SAVE50" in self_post.text
        assert self_post.published_at == datetime.fromtimestamp(1759990000, tz=timezone.utc)
        assert self_post.fetched_at == NOW

        # Link post (empty selftext): destination URL becomes the body.
        link_post = posts[1]
        assert "https://example.com/sony-deal" in link_post.text
        assert link_post.published_at == datetime.fromtimestamp(1759995000, tz=timezone.utc)

        # Politeness: slept 1 s between the token call and the listing call.
        assert sleeps == [1.0]

        # Second run: the bearer token is cached on the instance, so only the
        # listing endpoint is hit again and no extra politeness sleep happens.
        adapter.fetch_posts(self.SRC, None, None, cfg, NOW, None)
        assert counts == {"token": 1, "listing": 2}
        assert sleeps == [1.0]


# ---------------------------------------------------------------- woot


class TestWoot:
    SRC = SourceConfig(name="woot", method="api",
                       url="https://developer.woot.com/#getting-started", adapter="woot")

    def test_missing_key_raises(self, cfg: AppConfig) -> None:
        with pytest.raises(AdapterNotConfigured):
            WootAdapter().fetch_posts(self.SRC, None, None, cfg, NOW, None)

    def test_mapping_with_verbatim_prices(
        self, cfg: AppConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WOOT_API_KEY", "wkey")
        payload = {"Items": [
            {"OfferId": "off-1", "Title": "RoboVac 11S", "Site": "Electronics",
             "Url": "https://electronics.woot.com/offers/robot-vacuum",
             "SalePrice": {"Minimum": 49.99, "Maximum": 49.99},
             "ListPrice": 199.99, "Condition": "New",
             "StartDate": "2026-10-09T05:00:00Z",
             "EndDate": "2026-10-11T04:59:59Z",
             "Photo": "https://d3gqasl9vmjfy8.cloudfront.net/robovac.jpg",
             "IsSoldOut": False},
            {"OfferId": "off-2", "Title": "MacBook Air M2 (Refurbished)",
             "Site": "Computers",
             "Url": "https://computers.woot.com/offers/macbook-air",
             "SalePrice": {"Minimum": 499.99, "Maximum": 649.99},
             "ListPrice": 1199.0, "Condition": "Refurbished",
             "IsSoldOut": False},
            {"OfferId": "off-3", "Title": "Gone Already",
             "SalePrice": 1.0, "IsSoldOut": True},
        ]}

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "developer.woot.com"
            assert request.url.path == "/feed/All"
            # The key must travel ONLY in the header — query-string keys leak
            # into recorded error messages (see redact_secrets).
            assert "key" not in request.url.params
            assert request.headers["x-api-key"] == "wkey"
            return httpx.Response(200, json=payload)

        adapter = WootAdapter(http_client=_mock_client(handler, cfg))
        posts = adapter.fetch_posts(self.SRC, None, None, cfg, NOW, None)

        # Sold-out offers never become posts.
        assert [p.uid for p in posts] == ["off-1", "off-2"]

        first = posts[0]
        assert first.title == "RoboVac 11S"
        assert first.url == "https://electronics.woot.com/offers/robot-vacuum"
        # Prices appear verbatim so evidence quotes can match them exactly.
        assert "Sale price: $49.99" in first.text        # min==max collapses
        assert "List price: $199.99" in first.text
        assert "Ends: 2026-10-11T04:59:59Z" in first.text
        assert first.image_url == \
            "https://d3gqasl9vmjfy8.cloudfront.net/robovac.jpg"
        assert first.published_at == datetime(2026, 10, 9, 5, 0,
                                              tzinfo=timezone.utc)
        assert first.fetched_at == NOW

        refurb = posts[1]
        assert "Sale price: $499.99 - $649.99" in refurb.text  # price range
        assert "Condition: Refurbished." in refurb.text  # used-floor signal


# ---------------------------------------------------------------- meh


class TestMeh:
    SRC = SourceConfig(name="meh", method="api",
                       url="https://meh.com/api/1/current.json", adapter="meh")

    def test_missing_key_falls_back_to_public_json_feed(
        self, cfg: AppConfig
    ) -> None:
        # No MEH_API_KEY -> Meh's official keyless feed.json keeps the source live.
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "meh.com"
            assert request.url.path == "/feed.json"
            assert "apikey" not in request.url.params
            return httpx.Response(200, json={
                "version": "https://jsonfeed.org/version/1",
                "items": [{
                    "id": "feed-123",
                    "url": "https://meh.com/deals/widget-3-pack",
                    "title": "Widget 3-Pack",
                    "content_html": "<p>Today only: <b>$12.00</b> for three widgets.</p>",
                    "date_published": "2026-10-09T12:00:00Z",
                }],
            })

        adapter = MehAdapter(http_client=_mock_client(handler, cfg))
        posts = adapter.fetch_posts(self.SRC, None, None, cfg, NOW, None)
        assert len(posts) == 1
        post = posts[0]
        assert post.uid == "feed-123"
        assert post.title == "Widget 3-Pack"
        assert "$12.00" in post.text          # price survives verbatim for evidence
        assert "<p>" not in post.text         # html stripped
        assert post.published_at is not None

    def test_single_post_with_verbatim_price(
        self, cfg: AppConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MEH_API_KEY", "mkey")

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "meh.com"
            assert request.url.path == "/api/1/current.json"
            assert request.url.params["apikey"] == "mkey"
            return httpx.Response(200, json=_fixture_json("api_meh_current.json"))

        adapter = MehAdapter(http_client=_mock_client(handler, cfg))
        posts = adapter.fetch_posts(self.SRC, None, None, cfg, NOW, None)

        assert len(posts) == 1
        post = posts[0]
        assert post.uid == "deal-789"
        assert post.url == "https://meh.com/deals/fancy-gadget-2-pack"
        assert post.title == "Fancy Gadget 2-Pack"
        assert post.text.startswith("Fancy Gadget 2-Pack\n")
        assert "Price: $19.00" in post.text
        # Identical per-item prices collapse to one line.
        assert post.text.count("Price: $19.00") == 1
        # Story body present, markdown crudely stripped.
        assert "great gadget for cheap" in post.text
        assert "**" not in post.text
        assert "](" not in post.text
        # first_seen governs freshness for meh.
        assert post.published_at is None
        assert post.fetched_at == NOW


# ---------------------------------------------------------------- telegram


class _FakeMessage:
    def __init__(self, id: int, message: str, date: datetime | None) -> None:
        self.id = id
        self.message = message
        self.date = date


class _FakeTelegramClient:
    """Context-manager + get_messages fake standing in for sync Telethon."""

    def __init__(self, messages_by_channel: dict[str, list[_FakeMessage]]) -> None:
        self._messages = messages_by_channel
        self.calls: list[tuple[str, int]] = []
        self.entered = 0
        self.exited = 0

    def __enter__(self) -> "_FakeTelegramClient":
        self.entered += 1
        return self

    def __exit__(self, *exc) -> bool:
        self.exited += 1
        return False

    def get_messages(self, channel: str, limit: int = 0) -> list[_FakeMessage]:
        self.calls.append((channel, limit))
        return self._messages.get(channel, [])


class TestTelegram:
    SRC = SourceConfig(name="telegram_channels", method="api",
                       url="mtproto://channels", adapter="telegram")

    def _set_secrets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TELEGRAM_API_ID", "12345")
        monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
        monkeypatch.setenv("TELEGRAM_SESSION", "stringsession")

    def _cfg(self, channels: list[str]) -> AppConfig:
        cfg = AppConfig()
        cfg.telegram_channels = channels
        return cfg

    def test_missing_secrets_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = self._cfg(["chana"])
        with pytest.raises(AdapterNotConfigured, match="telegram credentials missing"):
            TelegramAdapter().fetch_posts(self.SRC, None, None, cfg, NOW, None)

    def test_no_channels_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set_secrets(monkeypatch)
        cfg = self._cfg([])
        with pytest.raises(AdapterNotConfigured, match="no telegram channels"):
            TelegramAdapter().fetch_posts(self.SRC, None, None, cfg, NOW, None)

    @pytest.mark.skipif(importlib.util.find_spec("telethon") is not None,
                        reason="telethon installed; lazy-import failure path not reachable")
    def test_telethon_missing_raises_not_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._set_secrets(monkeypatch)
        cfg = self._cfg(["chana"])
        with pytest.raises(AdapterNotConfigured, match="telethon not installed"):
            TelegramAdapter().fetch_posts(self.SRC, None, None, cfg, NOW, None)

    def test_mapping_across_channels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set_secrets(monkeypatch)
        cfg = self._cfg(["chana", "chanb"])
        when = datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc)
        fake = _FakeTelegramClient({
            "chana": [
                _FakeMessage(11, "RTIC cooler 60% off\n$36 at example.com", when),
                _FakeMessage(12, "", when),           # media-only: skipped
                _FakeMessage(13, "   ", when),        # whitespace-only: skipped
            ],
            "chanb": [_FakeMessage(7, "Second channel deal", when)],
        })
        factory_args: list[tuple[str, str, str]] = []

        def factory(api_id: str, api_hash: str, session: str) -> _FakeTelegramClient:
            factory_args.append((api_id, api_hash, session))
            return fake

        sleeps: list[float] = []
        adapter = TelegramAdapter(client_factory=factory, sleep_fn=sleeps.append)
        posts = adapter.fetch_posts(self.SRC, None, None, cfg, NOW, None)

        assert factory_args == [("12345", "hash", "stringsession")]
        assert fake.entered == 1 and fake.exited == 1
        assert fake.calls == [("chana", 30), ("chanb", 30)]
        assert sleeps == [1.0]  # one sleep between the two channel reads

        assert [p.uid for p in posts] == ["chana/11", "chanb/7"]
        first = posts[0]
        assert first.url == "https://t.me/chana/11"
        assert first.title == "RTIC cooler 60% off"  # first line of the message
        assert first.text == "RTIC cooler 60% off\n$36 at example.com"
        assert first.published_at == when
        assert first.published_at.tzinfo is not None
        assert first.fetched_at == NOW

    def test_fast_lane_reads_only_first_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._set_secrets(monkeypatch)
        cfg = self._cfg(["chana", "chanb"])
        when = datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc)
        fake = _FakeTelegramClient({
            "chana": [_FakeMessage(1, "fast deal", when)],
            "chanb": [_FakeMessage(2, "slow deal", when)],
        })
        sleeps: list[float] = []
        adapter = TelegramAdapter(fast_lane=True,
                                  client_factory=lambda *a: fake,
                                  sleep_fn=sleeps.append)
        posts = adapter.fetch_posts(self.SRC, None, None, cfg, NOW, None)

        assert fake.calls == [("chana", 30)]
        assert [p.uid for p in posts] == ["chana/1"]
        assert sleeps == []  # single channel: no inter-channel sleep


# ---------------------------------------------------------------- threads


class TestThreads:
    SRC = SourceConfig(name="threads_search", method="api",
                       url="https://graph.threads.net/v1.0/keyword_search",
                       adapter="threads")

    def test_flag_off_raises_disabled(self, cfg: AppConfig) -> None:
        assert not cfg.feature_flags.get("threads")
        with pytest.raises(AdapterDisabled, match="threads flag off"):
            ThreadsAdapter().fetch_posts(self.SRC, None, None, cfg, NOW, None)

    def test_flag_on_but_no_token_raises_not_configured(self) -> None:
        cfg = AppConfig()
        cfg.feature_flags = {"threads": True}
        with pytest.raises(AdapterNotConfigured, match="threads token missing"):
            ThreadsAdapter().fetch_posts(self.SRC, None, None, cfg, NOW, None)

    def test_keyword_search_mapping_and_dedupe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THREADS_TOKEN", "ttoken")
        cfg = AppConfig()
        cfg.feature_flags = {"threads": True}
        # Three configured keywords: only the first two may be searched.
        cfg.raw = {"threads": {"keywords": ["deal", "% off", "never-searched"]}}
        queries: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "graph.threads.net"
            assert request.url.path == "/v1.0/keyword_search"
            assert request.url.params["search_type"] == "TOP"
            assert request.url.params["fields"] == "id,text,permalink,timestamp,username"
            assert request.url.params["access_token"] == "ttoken"
            queries.append(request.url.params["q"])
            return httpx.Response(200, json=_fixture_json("api_threads_search.json"))

        sleeps: list[float] = []
        adapter = ThreadsAdapter(http_client=_mock_client(handler, cfg),
                                 sleep_fn=sleeps.append)
        posts = adapter.fetch_posts(self.SRC, None, None, cfg, NOW, None)

        assert queries == ["deal", "% off"]  # max 2 keyword searches
        assert sleeps == [1.0]
        # Both searches return the same two items: deduped by uid.
        assert [p.uid for p in posts] == ["th_1", "th_2"]
        first = posts[0]
        assert first.url == "https://www.threads.net/@dealguy/post/th_1"  # credit
        assert first.title == "Huge deal: espresso maker 70% off today only"
        assert "Link in bio" in first.text
        assert first.published_at == datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
        assert first.fetched_at == NOW

    def test_default_keywords_when_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THREADS_TOKEN", "ttoken")
        cfg = AppConfig()
        cfg.feature_flags = {"threads": True}
        queries: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            queries.append(request.url.params["q"])
            return httpx.Response(200, json={"data": []})

        adapter = ThreadsAdapter(http_client=_mock_client(handler, cfg),
                                 sleep_fn=lambda s: None)
        posts = adapter.fetch_posts(self.SRC, None, None, cfg, NOW, None)
        assert posts == []
        assert queries == ["deal", "% off"]


# ---------------------------------------------------------------- instagram


class TestInstagram:
    def test_always_disabled(self, cfg: AppConfig) -> None:
        src = SourceConfig(name="instagram_hashtags", method="api",
                           url="https://graph.facebook.com/v21.0/ig_hashtag_search",
                           adapter="instagram")
        with pytest.raises(AdapterDisabled, match="stubbed"):
            InstagramAdapter().fetch_posts(src, None, None, cfg, NOW, None)
