"""Offline tests for dealradar.fetch.validate (first-run source validation)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dealradar.config import SECRET_NAMES, AppConfig
from dealradar.db import DB
from dealradar.fetch import validate as v
from dealradar.models import FetchResult, SourceConfig

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 9, 15, 0, 0, tzinfo=timezone.utc)


def fr(url: str, *, status: int = 200, body: bytes | None = b"",
       not_modified: bool = False, blocked: bool = False, error: str = "",
       final_url: str = "") -> FetchResult:
    return FetchResult(url=url, final_url=final_url or url, status=status,
                       body=body, error=error, not_modified=not_modified,
                       blocked_by_robots=blocked)


class FakeFetcher:
    """Same get/head signature as PoliteFetcher; serves canned FetchResults."""

    def __init__(self, routes: dict[str, FetchResult] | None = None) -> None:
        self.routes = dict(routes or {})
        self.calls: list[str] = []

    def get(self, url, *, conditional=True, headers=None, deadline=None) -> FetchResult:
        self.calls.append(url)
        res = self.routes.get(url)
        if res is None:
            return fr(url, status=404, body=None, error="not routed")
        return res

    def head(self, url, *, conditional=True, headers=None, deadline=None) -> FetchResult:
        return self.get(url, conditional=conditional, headers=headers, deadline=deadline)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def make_source(name: str, **kw) -> SourceConfig:
    defaults: dict = dict(name=name, method="rss", url=f"https://{name}.example/feed/")
    defaults.update(kw)
    return SourceConfig(**defaults)


@pytest.fixture(autouse=True)
def clean_secrets(monkeypatch):
    """Secrets come only from the environment; start every test with none set."""
    for name in SECRET_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def db(tmp_path):
    d = DB(tmp_path / "state.db")
    yield d
    d.close()


@pytest.fixture()
def cfg():
    return AppConfig()


FEED = fixture_bytes("rss_wp_feed.xml")


def _mixed_scenario(monkeypatch):
    """A small mixed primary/backup set exercising every status."""
    monkeypatch.setenv("WOOT_API_KEY", "k")
    primary = [
        make_source("live_one", url="https://a.example/feed/"),
        make_source("live_two", url="https://b.example/feed/"),
        make_source("dead_one", url="https://dead.example/feed/"),
        make_source("moved_one", url="https://moved.example/oldfeed"),
        make_source("reddit_x", method="api", adapter="reddit",
                    url="https://oauth.reddit.com/r/x/new"),
        make_source("woot", method="api", adapter="woot",
                    url="https://developer.woot.example/"),
        make_source("insta", method="api", adapter="instagram",
                    url="https://graph.example/ig"),
        make_source("switched_off", url="https://off.example/feed/", enabled=False),
    ]
    backups = [
        make_source("backup_live", url="https://bk1.example/feed/"),
        make_source("backup_dead", url="https://bk2.example/feed/"),
    ]
    fetcher = FakeFetcher({
        "https://a.example/feed/": fr("https://a.example/feed/", body=FEED),
        "https://b.example/feed/": fr("https://b.example/feed/", body=FEED),
        # dead_one: feed 404s and nothing is discoverable (root/fallbacks 404)
        # moved_one: old url 404s; the site root advertises /custom/feed.xml
        "https://moved.example": fr("https://moved.example",
                                    body=fixture_bytes("rss_discovery_page.html")),
        "https://moved.example/custom/feed.xml": fr(
            "https://moved.example/custom/feed.xml", body=FEED),
        "https://bk1.example/feed/": fr("https://bk1.example/feed/", body=FEED),
    })
    return primary, backups, fetcher


def test_validate_sources_mixed(db, cfg, monkeypatch):
    primary, backups, fetcher = _mixed_scenario(monkeypatch)
    result = v.validate_sources(primary, backups, fetcher, db, cfg, NOW)

    rows = {r["name"]: r for r in result["rows"]}
    assert rows["live_one"]["status"] == "live"
    assert rows["live_two"]["status"] == "live"
    assert rows["dead_one"]["status"] == "dead"
    assert rows["moved_one"]["status"] == "live_updated_url"
    assert "https://moved.example/custom/feed.xml" in rows["moved_one"]["detail"]
    assert rows["reddit_x"]["status"] == "needs_key"
    assert "REDDIT_CLIENT_ID" in rows["reddit_x"]["detail"]
    assert rows["woot"]["status"] == "live"
    assert rows["insta"]["status"] == "disabled"
    assert rows["switched_off"]["status"] == "disabled"
    assert rows["backup_live"]["status"] == "promoted_backup"
    assert rows["backup_dead"]["status"] == "dead"

    # the first promoted backup stands in for the first dead primary
    assert rows["dead_one"]["replaced_by"] == "backup_live"

    active_names = [s.name for s in result["active"]]
    assert active_names == ["live_one", "live_two", "moved_one", "woot", "backup_live"]
    assert result["active_count"] == 5
    moved = next(s for s in result["active"] if s.name == "moved_one")
    assert moved.url == "https://moved.example/custom/feed.xml"
    assert primary[3].url == "https://moved.example/oldfeed"  # original untouched

    counts = result["counts"]
    assert counts["live"] == 3           # live_one, live_two, woot
    assert counts["live_updated_url"] == 1
    assert counts["dead"] == 2           # dead_one, backup_dead
    assert counts["needs_key"] == 1
    assert counts["disabled"] == 2
    assert counts["promoted_backup"] == 1


def test_api_validation_makes_no_network_calls(db, cfg):
    primary = [
        make_source("reddit_a", method="api", adapter="reddit",
                    url="https://oauth.reddit.com/r/a/new"),
        make_source("meh", method="api", adapter="meh", url="https://meh.example/api"),
        make_source("insta", method="api", adapter="instagram",
                    url="https://graph.example/ig"),
    ]
    fetcher = FakeFetcher({})
    result = v.validate_sources(primary, [], fetcher, db, cfg, NOW)
    assert fetcher.calls == []
    statuses = {r["name"]: r["status"] for r in result["rows"]}
    assert statuses == {"reddit_a": "needs_key", "meh": "live", "insta": "disabled"}  # meh is keyless via public feed.json


def test_telegram_requirements(db, monkeypatch):
    src = make_source("tg", method="api", adapter="telegram", url="mtproto://channels")
    fetcher = FakeFetcher({})

    cfg = AppConfig()  # no channels configured
    monkeypatch.setenv("TELEGRAM_API_ID", "1")
    monkeypatch.setenv("TELEGRAM_API_HASH", "h")
    monkeypatch.setenv("TELEGRAM_SESSION", "s")
    result = v.validate_sources([src], [], fetcher, db, cfg, NOW)
    assert result["rows"][0]["status"] == "needs_key"
    assert "channels" in result["rows"][0]["detail"]

    cfg.telegram_channels = ["somepublicchannel"]
    result = v.validate_sources([src], [], fetcher, db, cfg, NOW)
    assert result["rows"][0]["status"] == "live"
    assert fetcher.calls == []


def test_threads_flag_and_token(db, monkeypatch):
    src = make_source("threads", method="api", adapter="threads",
                      url="https://graph.threads.example/")
    fetcher = FakeFetcher({})

    cfg = AppConfig()  # feature flag off
    result = v.validate_sources([src], [], fetcher, db, cfg, NOW)
    assert result["rows"][0]["status"] == "disabled"

    cfg.feature_flags = {"threads": True}
    result = v.validate_sources([src], [], fetcher, db, cfg, NOW)
    assert result["rows"][0]["status"] == "needs_key"

    monkeypatch.setenv("THREADS_TOKEN", "t")
    result = v.validate_sources([src], [], fetcher, db, cfg, NOW)
    assert result["rows"][0]["status"] == "live"


def test_backups_untouched_when_target_met(db, cfg):
    primary = [make_source(f"p{i:02d}", url=f"https://p{i:02d}.example/feed/")
               for i in range(30)]
    backup = make_source("backup_live", url="https://bk1.example/feed/")
    routes = {s.url: fr(s.url, body=FEED) for s in primary}
    routes[backup.url] = fr(backup.url, body=FEED)
    fetcher = FakeFetcher(routes)

    result = v.validate_sources(primary, [backup], fetcher, db, cfg, NOW)
    assert result["active_count"] == 30
    assert backup.url not in fetcher.calls
    assert all(r["status"] != "promoted_backup" for r in result["rows"])
    assert len(result["rows"]) == 30


def test_promotion_stops_at_target(db, cfg):
    primary = [make_source(f"p{i:02d}", url=f"https://p{i:02d}.example/feed/")
               for i in range(29)]
    backups = [
        make_source("bk1", url="https://bk1.example/feed/"),
        make_source("bk2", url="https://bk2.example/feed/"),
    ]
    routes = {s.url: fr(s.url, body=FEED) for s in primary + backups}
    fetcher = FakeFetcher(routes)

    result = v.validate_sources(primary, backups, fetcher, db, cfg, NOW)
    assert result["active_count"] == 30
    rows = {r["name"]: r for r in result["rows"]}
    assert rows["bk1"]["status"] == "promoted_backup"
    assert "bk2" not in rows                     # never validated
    assert backups[1].url not in fetcher.calls   # and never fetched


def test_write_report(db, cfg, monkeypatch, tmp_path):
    primary, backups, fetcher = _mixed_scenario(monkeypatch)
    result = v.validate_sources(primary, backups, fetcher, db, cfg, NOW)
    path = tmp_path / "sources_report.md"
    v.write_report(result, cfg, NOW, path=str(path))

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    assert lines[0] == "# dealradar sources report"
    assert "Generated: 2026-10-09T15:00:00+00:00 (UTC)" in text
    assert "- Active sources: 5" in text
    # parseable markdown table with every row, needs_key/disabled included
    assert "| name | method | status | detail/url |" in lines
    assert "| --- | --- | --- | --- |" in lines
    assert "| live_one | rss | live | https://a.example/feed/ |" in lines
    assert any(ln.startswith("| reddit_x | api | needs_key |") for ln in lines)
    assert any(ln.startswith("| switched_off | rss | disabled |") for ln in lines)
    assert any(ln.startswith("| dead_one | rss | dead |")
               and "replaced by backup_live" in ln for ln in lines)
    table_rows = [ln for ln in lines if ln.startswith("| ") and "---" not in ln]
    assert len(table_rows) == 1 + len(result["rows"])  # header + all rows

    assert "## Promoted backups" in lines
    assert any(ln.startswith("- backup_live") for ln in lines)
    assert "## Dropped sources" in lines
    assert any(ln.startswith("- dead_one:") for ln in lines)
    # below-target warning
    assert "only 5 active sources" in text and "target is 30" in text


def test_effective_sources_roundtrip(db):
    assert v.effective_sources(db) is None

    sources = [
        make_source("a"),
        make_source("r", method="api", adapter="reddit",
                    url="https://oauth.reddit.com/r/deals/new", fast_lane=True),
    ]
    now = datetime.now(timezone.utc)
    v.save_effective_sources(db, sources, now)
    assert v.effective_sources(db) == sources
    # fresh within 24 h; stale after -> None so the caller revalidates
    assert v.effective_sources(db, now=now + timedelta(hours=23)) == sources
    assert v.effective_sources(db, now=now + timedelta(hours=25)) is None

    # stored as plain JSON under the documented meta key, with a timestamp
    raw = db.meta_get("effective_sources")
    data = json.loads(raw)
    assert data["saved_at"]
    assert [d["name"] for d in data["sources"]] == ["a", "r"]
    assert data["sources"][1]["adapter"] == "reddit"

    # idempotent re-save: same key, replaced value
    v.save_effective_sources(db, sources[:1])
    assert v.effective_sources(db) == sources[:1]
