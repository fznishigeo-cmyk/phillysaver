"""Tests for the adapter registry (dealradar.fetch.registry).

CONCURRENT-BUILD NOTE: a sibling agent owns ``dealradar/fetch/base.py``. If it
is not importable yet, a minimal abstract-free ``SourceAdapter`` stub is
installed in ``sys.modules`` BEFORE importing the adapters (see
``_ensure_base_stub``). Likewise, the registry's rss/html branch does a lazy
``from .rss import RssAdapter`` — another sibling's file — so that branch is
tested against a fake ``dealradar.fetch.rss`` module injected into
``sys.modules`` rather than the real one.
"""

from __future__ import annotations

import sys
import types

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
from dealradar.fetch.instagram_source import InstagramAdapter  # noqa: E402
from dealradar.fetch.kcl_wall import KclWallAdapter  # noqa: E402
from dealradar.fetch.meh import MehAdapter  # noqa: E402
from dealradar.fetch.reddit import RedditAdapter  # noqa: E402
from dealradar.fetch.registry import get_adapter  # noqa: E402
from dealradar.fetch.telegram_source import TelegramAdapter  # noqa: E402
from dealradar.fetch.threads_source import ThreadsAdapter  # noqa: E402
from dealradar.fetch.woot import WootAdapter  # noqa: E402
from dealradar.models import SourceConfig  # noqa: E402


def _src(method: str, adapter: str = "", name: str = "some_source") -> SourceConfig:
    return SourceConfig(name=name, method=method, url="https://example.com",
                        adapter=adapter)


@pytest.fixture
def cfg() -> AppConfig:
    return AppConfig()


@pytest.mark.parametrize(
    ("adapter_name", "expected_cls"),
    [
        ("reddit", RedditAdapter),
        ("woot", WootAdapter),
        ("meh", MehAdapter),
        ("kcl_wall", KclWallAdapter),
        ("telegram", TelegramAdapter),
        ("threads", ThreadsAdapter),
        ("instagram", InstagramAdapter),
    ],
)
def test_api_adapters_resolve(cfg: AppConfig, adapter_name: str,
                              expected_cls: type) -> None:
    adapter = get_adapter(_src("api", adapter_name), cfg)
    assert type(adapter) is expected_cls


def test_telegram_receives_fast_lane_kwarg(cfg: AppConfig) -> None:
    fast = get_adapter(_src("api", "telegram"), cfg, fast_lane=True)
    slow = get_adapter(_src("api", "telegram"), cfg)
    assert isinstance(fast, TelegramAdapter) and fast._fast_lane is True
    assert isinstance(slow, TelegramAdapter) and slow._fast_lane is False


def test_non_telegram_adapters_ignore_fast_lane(cfg: AppConfig) -> None:
    # fast_lane is accepted for every source; only telegram consumes it.
    adapter = get_adapter(_src("api", "woot"), cfg, fast_lane=True)
    assert type(adapter) is WootAdapter


def test_rss_and_html_use_rss_adapter(cfg: AppConfig,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    # The registry imports .rss lazily inside the branch; inject a fake module
    # so this test stays independent of the sibling-owned rss.py file.
    fake_mod = types.ModuleType("dealradar.fetch.rss")

    class FakeRssAdapter:
        pass

    fake_mod.RssAdapter = FakeRssAdapter
    monkeypatch.setitem(sys.modules, "dealradar.fetch.rss", fake_mod)

    assert isinstance(get_adapter(_src("rss"), cfg), FakeRssAdapter)
    assert isinstance(get_adapter(_src("html"), cfg), FakeRssAdapter)


def test_unknown_api_adapter_raises(cfg: AppConfig) -> None:
    with pytest.raises(ValueError, match="unknown api adapter"):
        get_adapter(_src("api", "myspace"), cfg)


def test_blank_api_adapter_raises(cfg: AppConfig) -> None:
    with pytest.raises(ValueError, match="unknown api adapter"):
        get_adapter(_src("api", ""), cfg)


def test_unknown_method_raises(cfg: AppConfig) -> None:
    with pytest.raises(ValueError, match="unknown method"):
        get_adapter(_src("carrier_pigeon"), cfg)
