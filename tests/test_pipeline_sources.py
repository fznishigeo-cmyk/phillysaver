"""New sources.yaml entries must join the run set without waiting for the
daily revalidation of the stored effective set."""

from __future__ import annotations

from datetime import timedelta

from dealradar import pipeline
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.fetch.validate import save_effective_sources
from dealradar.models import SourceConfig
from dealradar.util import now_utc


def make_source(name: str, **kw) -> SourceConfig:
    return SourceConfig(name=name, method="rss",
                        url=f"https://{name}.example/feed/", **kw)


def test_new_configured_sources_merge_into_stored_set(tmp_path, monkeypatch):
    db = DB(tmp_path / "state.db")
    cfg = load_config()
    now = now_utc()
    stored = [make_source("a"), make_source("b")]
    save_effective_sources(db, stored, now)

    primary = [make_source("a"), make_source("b"),
               make_source("brand_new"),
               make_source("disabled_new", enabled=False)]
    monkeypatch.setattr(pipeline, "load_sources", lambda: (primary, []))

    result = pipeline._load_effective_sources(db, cfg, fetcher=None, now=now)
    names = [s.name for s in result]
    assert names == ["a", "b", "brand_new"]  # stored set + the new enabled one

    # A stale stored set still revalidates (returns None -> validation path),
    # so the merge only applies while the stored set is fresh.
    later = now + timedelta(hours=25)
    result2 = pipeline._load_effective_sources(
        db, cfg, fetcher=None, now=later, validate_if_missing=False)
    assert [s.name for s in result2] == ["a", "b", "brand_new"]
    db.close()
