"""Offline tests for dealradar.fetch.base (fake adapter, fake clock, real sqlite)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import httpx
import pytest

from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.errors import AdapterDisabled, AdapterNotConfigured, DeadlineExceeded
from dealradar.fetch.base import SourceAdapter, run_source
from dealradar.http_client import PoliteFetcher
from dealradar.models import RawPost, SourceConfig
from dealradar.util import iso, now_utc


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class ScriptedAdapter(SourceAdapter):
    """Returns a fixed post list, or raises a fixed exception."""

    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.deadlines: list[float | None] = []

    def fetch_posts(self, source, fetcher, db, cfg, now, deadline):
        self.deadlines.append(deadline)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def make_env(tmp_path):
    cfg = AppConfig()
    db = DB(tmp_path / "state.db")
    clock = FakeClock()
    fetcher = PoliteFetcher(
        db, cfg,
        transport=httpx.MockTransport(lambda r: httpx.Response(404)),
        sleep_fn=clock.sleep, clock=clock,
    )
    source = SourceConfig(name="src1", method="rss",
                          url="https://example.com/feed")
    return cfg, db, clock, fetcher, source


def make_post(now, source_name: str = "src1") -> RawPost:
    return RawPost(
        source_name=source_name, uid="u1", url="https://example.com/p/1",
        title="50% off widget", text="50% off widget was $20 now $10",
        published_at=now, fetched_at=now,
    )


def health_row(db: DB, name: str):
    rows = [r for r in db.all_source_health() if r["source_name"] == name]
    return rows[0] if rows else None


def test_success_returns_posts_and_records_health(tmp_path):
    cfg, db, clock, fetcher, source = make_env(tmp_path)
    now = now_utc()
    adapter = ScriptedAdapter([make_post(now)])
    posts, ok, error = run_source(source, adapter, fetcher, db, cfg, now,
                                  clock=clock)
    assert ok is True
    assert error == ""
    assert len(posts) == 1 and posts[0].uid == "u1"
    row = health_row(db, "src1")
    assert row is not None
    assert row["items_last_run"] == 1
    assert row["consecutive_failures"] == 0
    assert row["last_success_at"]
    assert not db.is_tripped("src1", now)


def test_deadline_is_min_of_run_deadline_and_source_budget(tmp_path):
    cfg, db, clock, fetcher, source = make_env(tmp_path)
    now = now_utc()
    adapter = ScriptedAdapter([])
    # No run deadline: clock() + per_source_budget_s.
    run_source(source, adapter, fetcher, db, cfg, now, clock=clock)
    assert adapter.deadlines[-1] == clock() + cfg.per_source_budget_s
    # Tighter run deadline wins.
    run_source(source, adapter, fetcher, db, cfg, now,
               run_deadline=clock() + 5.0, clock=clock)
    assert adapter.deadlines[-1] == clock() + 5.0
    # Looser run deadline loses to the per-source budget.
    run_source(source, adapter, fetcher, db, cfg, now,
               run_deadline=clock() + 10_000.0, clock=clock)
    assert adapter.deadlines[-1] == clock() + cfg.per_source_budget_s


def test_adapter_disabled_records_nothing(tmp_path):
    cfg, db, clock, fetcher, source = make_env(tmp_path)
    now = now_utc()
    adapter = ScriptedAdapter(AdapterDisabled("flag off"))
    posts, ok, error = run_source(source, adapter, fetcher, db, cfg, now,
                                  clock=clock)
    assert (posts, ok, error) == ([], True, "disabled")
    assert health_row(db, "src1") is None


def test_adapter_not_configured_records_needs_key(tmp_path):
    cfg, db, clock, fetcher, source = make_env(tmp_path)
    now = now_utc()
    adapter = ScriptedAdapter(AdapterNotConfigured("missing WOOT_API_KEY"))
    posts, ok, error = run_source(source, adapter, fetcher, db, cfg, now,
                                  clock=clock)
    assert (posts, ok, error) == ([], False, "needs_key")
    row = health_row(db, "src1")
    assert row["last_error"] == "needs_key"
    # A config state, not a site failure: never advances the circuit breaker,
    # so the source activates immediately once its secrets are set.
    assert row["consecutive_failures"] == 0
    for _ in range(4):
        run_source(source, adapter, fetcher, db, cfg, now, clock=clock)
    assert not db.is_tripped("src1", now)


def test_deadline_exceeded_records_deadline(tmp_path):
    cfg, db, clock, fetcher, source = make_env(tmp_path)
    now = now_utc()
    adapter = ScriptedAdapter(DeadlineExceeded("budget spent"))
    posts, ok, error = run_source(source, adapter, fetcher, db, cfg, now,
                                  clock=clock)
    assert (posts, ok, error) == ([], False, "deadline")
    assert health_row(db, "src1")["last_error"] == "deadline"


def test_generic_exception_recorded_truncated_to_200_chars(tmp_path):
    cfg, db, clock, fetcher, source = make_env(tmp_path)
    now = now_utc()
    message = "boom " + "x" * 300
    adapter = ScriptedAdapter(RuntimeError(message))
    posts, ok, error = run_source(source, adapter, fetcher, db, cfg, now,
                                  clock=clock)
    assert ok is False
    assert posts == []
    assert error == message[:200]
    assert len(error) == 200
    assert health_row(db, "src1")["last_error"] == message[:200]


def test_circuit_breaker_trips_after_three_consecutive_failures(tmp_path):
    cfg, db, clock, fetcher, source = make_env(tmp_path)
    assert cfg.circuit_breaker_failures == 3
    now = now_utc()
    adapter = ScriptedAdapter(RuntimeError("boom"))
    for expected_failures in (1, 2, 3):
        posts, ok, error = run_source(source, adapter, fetcher, db, cfg, now,
                                      clock=clock)
        assert (posts, ok, error) == ([], False, "boom")
        assert health_row(db, "src1")["consecutive_failures"] == expected_failures
    assert db.is_tripped("src1", now) is True

    # The next run short-circuits: adapter is NOT called, nothing is recorded.
    calls_before = len(adapter.deadlines)
    row_before = health_row(db, "src1")
    posts, ok, error = run_source(source, adapter, fetcher, db, cfg, now,
                                  clock=clock)
    assert (posts, ok, error) == ([], False, "tripped")
    assert len(adapter.deadlines) == calls_before
    assert health_row(db, "src1") == row_before

    # The trip expires after the cooldown.
    later = now + timedelta(hours=cfg.circuit_breaker_cooldown_h + 1)
    assert db.is_tripped("src1", later) is False


def test_success_resets_consecutive_failures(tmp_path):
    cfg, db, clock, fetcher, source = make_env(tmp_path)
    now = now_utc()
    failing = ScriptedAdapter(RuntimeError("boom"))
    run_source(source, failing, fetcher, db, cfg, now, clock=clock)
    run_source(source, failing, fetcher, db, cfg, now, clock=clock)
    assert health_row(db, "src1")["consecutive_failures"] == 2

    succeeding = ScriptedAdapter([make_post(now)])
    posts, ok, error = run_source(source, succeeding, fetcher, db, cfg, now,
                                  clock=clock)
    assert ok is True
    assert health_row(db, "src1")["consecutive_failures"] == 0
    assert not db.is_tripped("src1", now)


def test_source_adapter_is_abstract():
    with pytest.raises(TypeError):
        SourceAdapter()  # type: ignore[abstract]
