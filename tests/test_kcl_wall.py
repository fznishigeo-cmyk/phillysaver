"""Offline tests for the KCL live deal-wall adapter.

Live incident: the user's two test deals (Ekouaer lounge set, Women's
Christmas tee) sat on KCL's deal wall but never entered their RSS feed, so
dealradar missed both. The wall adapter fetches the wall page itself under
the operator's documented written permission (sources.yaml
``crawl_permission``) and chunks the text for the evidence-verified parse.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from dealradar.fetch.kcl_wall import (
    MAX_CHUNK_CHARS,
    MAX_CHUNKS,
    KclWallAdapter,
    _chunk_lines,
)
from dealradar.models import FetchResult, SourceConfig

NOW = datetime(2026, 10, 10, 21, 0, 0, tzinfo=timezone.utc)
WALL_URL = "https://thekrazycouponlady.com/coupons-for/amazon"

WALL_HTML = """<html><head><title>Amazon Deals</title>
<script>var tracking = "no prices in here";</script></head><body>
<div class="card">
  <a href="https://thekrazycouponlady.com/deals/tee">Women's Christmas Graphic Tee</a>
  <span>$3.60</span> <s>$19.99</s> <span>82% Off</span>
  <a href="https://www.amazon.com/dp/B0TEE">Shop on Amazon</a>
</div>
<div class="card">
  <a href="https://thekrazycouponlady.com/deals/lounge">Ekouaer Lounge Set</a>
  <span>$14.99</span> <s>$39.99</s> <span>Code 5HXNMK18</span>
</div>
<div class="card">
  <a href="https://thekrazycouponlady.com/deals/vac">Robot Vacuum</a>
  <span>$89.00</span> <s>$299.99</s>
</div>
</body></html>"""


def fr(url: str = WALL_URL, *, status: int = 200, body: bytes | None = b"",
       not_modified: bool = False, blocked: bool = False) -> FetchResult:
    return FetchResult(url=url, final_url=url, status=status, body=body,
                       not_modified=not_modified, blocked_by_robots=blocked)


class FakeFetcher:
    """PoliteFetcher stand-in that records the kwargs of every get()."""

    def __init__(self, result: FetchResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, *, conditional=True, headers=None, deadline=None,
            **kw) -> FetchResult:
        self.calls.append((url, kw))
        return self.result


class StrictFetcher(FakeFetcher):
    """Rejects the override kwarg — proves it is only sent with permission."""

    def get(self, url, *, conditional=True, headers=None,
            deadline=None) -> FetchResult:
        self.calls.append((url, {}))
        return self.result


def make_source(**kw) -> SourceConfig:
    defaults: dict = dict(
        name="kcl_wall_amazon", method="api", adapter="kcl_wall",
        url=WALL_URL, allowed_html=True,
        crawl_permission="operator reports written email permission from KCL",
    )
    defaults.update(kw)
    return SourceConfig(**defaults)


class TestFetch:
    def test_wall_maps_to_price_bearing_posts_with_links(self) -> None:
        fetcher = FakeFetcher(fr(body=WALL_HTML.encode()))
        posts = KclWallAdapter().fetch_posts(
            make_source(), fetcher, None, None, NOW, None)

        assert len(posts) == 1  # small wall fits one chunk
        post = posts[0]
        assert post.source_name == "kcl_wall_amazon"
        assert post.uid.startswith("wall:0:") and len(post.uid) == 19
        assert post.url == WALL_URL             # credit: the wall itself
        assert post.title == "KCL deal wall (part 1)"
        assert post.published_at is None        # first_seen governs freshness
        assert post.fetched_at == NOW
        # Verbatim prices survive for evidence verification.
        assert "$3.60" in post.text and "$19.99" in post.text
        assert "$14.99" in post.text and "5HXNMK18" in post.text
        # Anchor hrefs survive the cleaner so deals keep product links.
        assert "(https://www.amazon.com/dp/B0TEE)" in post.text
        # Script content never leaks into the parsed text.
        assert "tracking" not in post.text

    def test_permission_host_forwarded_to_fetcher(self) -> None:
        fetcher = FakeFetcher(fr(body=WALL_HTML.encode()))
        KclWallAdapter().fetch_posts(
            make_source(), fetcher, None, None, NOW, None)
        [(url, kw)] = fetcher.calls
        assert url == WALL_URL
        assert kw == {"robots_override_host": "thekrazycouponlady.com"}

    def test_no_permission_means_no_override_kwarg(self) -> None:
        # StrictFetcher would raise TypeError if the kwarg were passed.
        fetcher = StrictFetcher(fr(body=WALL_HTML.encode()))
        posts = KclWallAdapter().fetch_posts(
            make_source(crawl_permission=""), fetcher, None, None, NOW, None)
        assert len(posts) == 1

    def test_not_modified_returns_empty(self) -> None:
        fetcher = FakeFetcher(fr(status=304, body=None, not_modified=True))
        assert KclWallAdapter().fetch_posts(
            make_source(), fetcher, None, None, NOW, None) == []

    def test_blocked_by_robots_raises(self) -> None:
        fetcher = FakeFetcher(fr(status=200, body=None, blocked=True))
        try:
            KclWallAdapter().fetch_posts(
                make_source(), fetcher, None, None, NOW, None)
        except RuntimeError as e:
            assert "blocked_by_robots" in str(e)
        else:
            raise AssertionError("expected RuntimeError")

    def test_http_error_raises(self) -> None:
        fetcher = FakeFetcher(fr(status=503, body=None))
        try:
            KclWallAdapter().fetch_posts(
                make_source(), fetcher, None, None, NOW, None)
        except RuntimeError as e:
            assert "http_503" in str(e)
        else:
            raise AssertionError("expected RuntimeError")

    def test_priceless_page_warns_and_maps_nothing(self, caplog) -> None:
        """JS-only render / layout change: the diagnostic must be loud."""
        body = b"<html><body><div id='root'>Loading deals...</div></body></html>"
        fetcher = FakeFetcher(fr(body=body))
        with caplog.at_level(logging.WARNING, logger="dealradar.fetch.kcl_wall"):
            posts = KclWallAdapter().fetch_posts(
                make_source(), fetcher, None, None, NOW, None)
        assert posts == []
        assert any("price tokens" in r.message for r in caplog.records)

    def test_unchanged_wall_hashes_identically(self) -> None:
        """Same wall text => same uids, so re-runs re-parse nothing."""
        a = KclWallAdapter().fetch_posts(
            make_source(), FakeFetcher(fr(body=WALL_HTML.encode())),
            None, None, NOW, None)
        b = KclWallAdapter().fetch_posts(
            make_source(), FakeFetcher(fr(body=WALL_HTML.encode())),
            None, None, NOW, None)
        assert [p.uid for p in a] == [p.uid for p in b]


class TestChunking:
    def test_price_less_chunks_are_dropped(self) -> None:
        text = "nav\nabout us\ncareers\n" * 50  # no prices anywhere
        assert _chunk_lines(text) == []

    def test_long_wall_splits_and_caps(self) -> None:
        card = "Deal item name here\n$9.99 was $29.99\nShop now\n"
        text = card * 2000  # far beyond MAX_CHUNKS * MAX_CHUNK_CHARS
        chunks = _chunk_lines(text)
        assert 1 <= len(chunks) <= MAX_CHUNKS
        assert all(len(c) <= MAX_CHUNK_CHARS for c in chunks)
        assert all("$9.99" in c for c in chunks)

    def test_small_priced_text_is_one_chunk(self) -> None:
        chunks = _chunk_lines("Widget\n$5.00 was $25.00")
        assert chunks == ["Widget\n$5.00 was $25.00"]
