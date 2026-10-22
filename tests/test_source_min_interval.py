"""Slickdeals 2026-10-22: a bot may check their deals at most once an hour (24 a day)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from dealradar import pipeline
from dealradar.config import load_sources
from dealradar.db import DB
from dealradar.fetch.newsletter import match_sender
from dealradar.models import SourceConfig

NOW = datetime(2026, 10, 22, 20, 0, tzinfo=timezone.utc)


def test_slickdeals_is_hourly_rss_only_with_permission_on_file():
    primary, backup = load_sources()
    feeds = [s for s in primary + backup if "slickdeals.net" in s.url]
    assert feeds
    for s in feeds:
        assert s.method == "rss" and not s.allowed_html and not s.fast_lane, s.name
        if s.enabled:
            assert s.min_interval_minutes >= 60 and "at most once an hour" in s.crawl_permission, s.name
            assert "written permission by email" in s.crawl_permission, s.name
    # Operator 2026-10-22: one hourly check may read all three feeds.
    assert [s.name for s in feeds if s.enabled] == ["slickdeals_frontpage", "slickdeals_popular",
                                                    "slickdeals_amazon"]
    # Their deal-alert emails credit Slickdeals instead of being dropped as unknown.
    assert match_sender("alerts@mail.slickdeals.net", primary).name == "slickdeals_frontpage"


def test_loader_rejects_a_bad_interval(tmp_path):
    from dealradar.config import load_sources as load
    path = tmp_path / "sources.yaml"
    for value in (-1, 1.5, "60", True, None):
        path.write_text(json.dumps({"sources": [{"name": "x", "method": "rss", "url": "https://x.test/feed",
                                                 "min_interval_minutes": value}]}))
        with pytest.raises(ValueError, match="min_interval_minutes"):
            load(path)
    path.write_text(json.dumps({"sources": [{"name": "x", "method": "rss", "url": "https://x.test/feed"}]}))
    assert load(path)[0][0].min_interval_minutes == 0


def test_a_limited_source_is_fetched_at_most_once_per_interval(tmp_path):
    limited = SourceConfig("sd", "rss", "https://slickdeals.net/rss", min_interval_minutes=60)
    free = SourceConfig("blog", "rss", "https://blog.test/feed")
    db = DB(tmp_path / "state.db")
    try:
        for minutes, expected in ((0, True), (59, False), (60, True), (61, False), (119, False), (120, True)):
            now = NOW + timedelta(minutes=minutes)
            assert pipeline._owner_limit_ok(free, db, now)
            ok = pipeline._owner_limit_ok(limited, db, now)
            assert ok is expected, minutes
            if ok:
                pipeline._mark_owner_limit([limited, free], db, now)
        assert db.meta_get("source_last_fetch:blog") is None
        # A day of runs every 10 minutes reaches the feed at most 24 times.
        db.meta_set("source_last_fetch:sd", "")
        visits = 0
        for step in range(6 * 24):
            now = NOW + timedelta(minutes=10 * step)
            if pipeline._owner_limit_ok(limited, db, now):
                visits += 1
                pipeline._mark_owner_limit([limited], db, now)
        assert visits == 24
    finally:
        db.close()
