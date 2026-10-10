"""New sources.yaml entries must join the run set without waiting for the
daily revalidation of the stored effective set — and no source may starve
because of its position in the list (live incident: the back half of
sources.yaml fetched every hour yet never had one post parsed)."""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

from dealradar import pipeline
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.fetch.validate import save_effective_sources
from dealradar.models import FetchResult, RawPost, SourceConfig
from dealradar.util import now_utc

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def make_source(name: str, **kw) -> SourceConfig:
    return SourceConfig(name=name, method="rss",
                        url=f"https://{name}.example/feed/", **kw)


class TestBlogImageBackfill:
    """Existing photo-less deals from an image-granted source get one polite
    article fetch for their og:image; a page with no image is flagged so it
    is never fetched again."""

    class _Fetcher:
        def __init__(self, pages):
            self.pages = pages
            self.calls = []

        def get(self, url, *, conditional=True, headers=None, deadline=None,
                **kw):
            self.calls.append(url)
            body = self.pages.get(url)
            return FetchResult(url=url, final_url=url,
                               status=200 if body is not None else 404,
                               body=body)

    @staticmethod
    def _seed(db, key, article_url, image=""):
        payload = {"dedupe_key": key, "store": "HSN", "item": key,
                   "flags": [], "image_url": image, "conditions": []}
        deal_id = db.insert_deal(
            dedupe_key=key, store="HSN", item=key, normalized_item=key,
            canonical_url="", payload=payload, final_price=9.99,
            pct_at_checkout=60.0, abs_savings=15.0, online_only="true",
            expires_at="", now=NOW)
        db.add_sighting(deal_id, source_name="hip2save",
                        source_url=article_url, content_hash="h" + key,
                        seen_at=NOW, final_price=9.99)

    def test_fills_flags_and_never_refetches(self, tmp_path):
        db = DB(tmp_path / "state.db")
        self._seed(db, "with_pic", "https://hip2save.com/deals/a/")
        self._seed(db, "no_pic", "https://hip2save.com/deals/b/")
        self._seed(db, "already", "https://hip2save.com/deals/c/",
                   image="https://x.example/img.jpg")
        src = make_source("hip2save", allowed_html=True, image_permission=True,
                          crawl_permission="written grant on file")
        fetcher = self._Fetcher({
            "https://hip2save.com/deals/a/":
                b"<meta property='og:image' content='https://hip2save.com/i/a.jpg'>",
            "https://hip2save.com/deals/b/":
                b"<html><body>no pictures here</body></html>",
        })
        n = pipeline._backfill_blog_images(db, fetcher, [src], deadline=None)
        assert n == 2
        assert db.get_deal("with_pic")["image_url"] == "https://hip2save.com/i/a.jpg"
        assert "image_backfill_tried" in db.get_deal("no_pic")["flags"]
        assert sorted(fetcher.calls) == ["https://hip2save.com/deals/a/",
                                         "https://hip2save.com/deals/b/"]
        # Nothing left to try: a second pass makes no fetch at all.
        fetcher.calls.clear()
        assert pipeline._backfill_blog_images(db, fetcher, [src], None) == 0
        assert fetcher.calls == []
        # No granted source in the run set -> no fetches, ever.
        plain = make_source("hip2save", allowed_html=True)
        assert pipeline._backfill_blog_images(db, fetcher, [plain], None) == 0
        db.close()


def make_post(source: str, i: int) -> RawPost:
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    return RawPost(source_name=source, uid=f"{source}-{i}",
                   url=f"https://{source}.example/{i}", title=f"{source} {i}",
                   text=f"deal {source} {i} $9.99", published_at=None,
                   fetched_at=now)


class TestFairScheduling:
    def test_interleave_round_robins_across_sources(self):
        posts = ([make_post("a", i) for i in range(3)]
                 + [make_post("b", i) for i in range(2)]
                 + [make_post("c", i) for i in range(1)])
        out = pipeline._interleave_by_source(posts)
        assert [p.uid for p in out] == [
            "a-0", "b-0", "c-0", "a-1", "b-1", "a-2"]

    def test_interleave_keeps_within_source_order_and_loses_nothing(self):
        posts = [make_post("x", i) for i in range(5)] \
            + [make_post("y", i) for i in range(4)]
        out = pipeline._interleave_by_source(posts)
        assert len(out) == len(posts)
        assert [p.uid for p in out if p.source_name == "x"] == \
            [f"x-{i}" for i in range(5)]
        assert pipeline._interleave_by_source([]) == []

    def test_rotation_is_deterministic_and_complete(self):
        sources = [make_source(f"s{i}") for i in range(7)]
        a = pipeline._rotate_for_run(sources, "2026-10-10T19")
        b = pipeline._rotate_for_run(sources, "2026-10-10T19")
        assert [s.name for s in a] == [s.name for s in b]  # idempotent re-run
        assert sorted(s.name for s in a) == sorted(s.name for s in sources)
        # Across the run keys of a day, the head position actually moves.
        heads = {pipeline._rotate_for_run(sources, f"2026-10-10T{h:02d}")[0].name
                 for h in range(24)}
        assert len(heads) > 1
        assert pipeline._rotate_for_run([], "k") == []

    def test_hourly_puts_slow_sources_before_fast_lane_feeds(self):
        """The hourly must fetch the slow blogs before the big fast-lane
        feeds, or the feeds eat the budget and the blogs never parse."""
        srcs = [make_source("slick", fast_lane=True),
                make_source("blog_a"), make_source("keepa", fast_lane=True),
                make_source("blog_b"), make_source("blog_c")]
        order = pipeline._order_for_run(srcs, "2026-10-10T20", fast_lane=False)
        names = [s.name for s in order]
        last_slow = max(i for i, s in enumerate(order) if not s.fast_lane)
        first_fast = min(i for i, s in enumerate(order) if s.fast_lane)
        assert last_slow < first_fast          # every slow source precedes fast
        assert sorted(names) == sorted(s.name for s in srcs)  # nothing dropped

    def test_fast_run_order_is_a_plain_rotation(self):
        srcs = [make_source(f"s{i}", fast_lane=(i % 2 == 0)) for i in range(5)]
        order = pipeline._order_for_run(srcs, "k", fast_lane=True)
        assert sorted(s.name for s in order) == sorted(s.name for s in srcs)

    def test_fetch_stops_inside_the_parse_reserve(self):
        """_fetch_all must leave _RESERVE_FOR_PARSE_S on the clock: with the
        deadline already inside the reserve, no source is attempted."""
        deadline = time.monotonic() + pipeline._RESERVE_FOR_PARSE_S - 5.0
        posts, errors = pipeline._fetch_all(
            [make_source("never_reached")], fetcher=None, db=None,
            cfg=load_config(), now=now_utc(), deadline=deadline,
            fast_lane=False)
        assert posts == [] and errors == {}

    def test_parse_reserve_exceeds_tail_reserve(self):
        assert pipeline._RESERVE_FOR_PARSE_S > pipeline._RESERVE_FOR_TAIL_S


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


def test_failing_sources_take_config_edits_immediately(tmp_path, monkeypatch):
    """A healthy stored source keeps its (possibly autodiscovered) stored
    entry; a FAILING one swaps to the current config entry, so a corrected
    URL or new crawl_permission heals it next run, not next revalidation.
    A name disabled in config drops out of the run set at once."""
    db = DB(tmp_path / "state.db")
    cfg = load_config()
    now = now_utc()
    stored = [make_source("healthy"), make_source("broken"),
              make_source("nowdisabled")]
    save_effective_sources(db, stored, now)

    db.record_source_result("healthy", ok=True, error="", items=5, now=now)
    db.record_source_result("broken", ok=False, error="http_404", items=0,
                            now=now)

    fixed = SourceConfig(name="broken", method="rss",
                         url="https://broken.example/real-feed/",
                         crawl_permission="written permission on file")
    healthy_cfg = SourceConfig(name="healthy", method="rss",
                               url="https://healthy.example/other-url/")
    primary = [healthy_cfg, fixed,
               make_source("nowdisabled", enabled=False)]
    monkeypatch.setattr(pipeline, "load_sources", lambda: (primary, []))

    result = pipeline._load_effective_sources(db, cfg, fetcher=None, now=now)
    by_name = {s.name: s for s in result}
    assert sorted(by_name) == ["broken", "healthy"]  # disabled one dropped
    # Healthy keeps the stored entry (autodiscovered URLs survive).
    assert by_name["healthy"].url == "https://healthy.example/feed/"
    # ...but takes the config's current operator grants and lane at once
    # (live incident: Hip2Save's image grant was stuck behind the daily
    # revalidation because the healthy stored entry won wholesale).
    assert by_name["healthy"].image_permission is False
    granted_cfg = SourceConfig(name="healthy", method="rss",
                               url="https://healthy.example/other-url/",
                               image_permission=True, fast_lane=True,
                               crawl_permission="written grant on file")
    monkeypatch.setattr(pipeline, "load_sources",
                        lambda: ([granted_cfg, fixed], []))
    again = {s.name: s for s in
             pipeline._load_effective_sources(db, cfg, fetcher=None, now=now)}
    assert again["healthy"].url == "https://healthy.example/feed/"  # stored URL
    assert again["healthy"].image_permission is True                # new grant
    assert again["healthy"].fast_lane is True
    assert again["healthy"].crawl_permission == "written grant on file"
    # Failing takes the config fix, permission note included.
    assert by_name["broken"].url == "https://broken.example/real-feed/"
    assert by_name["broken"].crawl_permission
    db.close()
