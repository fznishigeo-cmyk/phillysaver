"""Live integration test — hits real sources over the network. Marked `slow`
(deselected by default; run with: pytest -m slow). Requires outbound access to
the feed domains (see README network allowlist)."""

from __future__ import annotations

import pytest

from dealradar import util
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.models import SourceConfig


@pytest.mark.slow
def test_live_fetch_two_real_feeds(tmp_path):
    from dealradar.fetch.base import run_source
    from dealradar.fetch.registry import get_adapter
    from dealradar.http_client import PoliteFetcher

    cfg = load_config()
    db = DB(tmp_path / "state.db")
    fetcher = PoliteFetcher(db, cfg)
    sources = [
        SourceConfig(name="slickdeals_frontpage", method="rss",
                     url="https://slickdeals.net/newsearch.php?mode=frontpage"
                         "&searcharea=deals&searchin=first&rss=1"),
        SourceConfig(name="hip2save", method="rss", url="https://hip2save.com/feed/"),
    ]
    try:
        total = 0
        for s in sources:
            posts, ok, err = run_source(s, get_adapter(s, cfg), fetcher, db, cfg,
                                        util.now_utc())
            assert ok, f"{s.name} failed: {err}"
            total += len(posts)
            for p in posts[:3]:
                assert p.title and p.url
        assert total > 0
    finally:
        fetcher.close()
        db.close()
