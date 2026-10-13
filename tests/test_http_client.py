"""Offline tests for dealradar.http_client (httpx.MockTransport, fake clock/sleep)."""

from __future__ import annotations

import httpx
import pytest

from dealradar import http_client
from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.errors import DeadlineExceeded
from dealradar.http_client import (
    MAX_BODY_BYTES,
    PoliteFetcher,
    canonicalize_url,
    challenge_target,
    extract_asin,
    is_challenge_url,
    resolve_redirects,
)


class FakeClock:
    """Monotonic clock whose sleep() advances time and records every delay."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class Router:
    """Routes MockTransport requests by (method, host, path); records them all.

    Each route maps to a list of responders (callables taking the request).
    Responders are consumed in order; the last one repeats. A responder may
    return an httpx.Response or an Exception instance (which is raised).
    """

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str, str], list] = {}
        self.requests: list[httpx.Request] = []

    def add(self, method: str, url: str, *responders) -> None:
        u = httpx.URL(url)
        self.routes[(method.upper(), u.host, u.path)] = list(responders)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.host, request.url.path)
        responders = self.routes.get(key)
        if not responders:
            return httpx.Response(404, text="unrouted")
        responder = responders.pop(0) if len(responders) > 1 else responders[0]
        out = responder(request)
        if isinstance(out, Exception):
            raise out
        return out

    def count(self, method: str, url: str) -> int:
        u = httpx.URL(url)
        return sum(
            1 for r in self.requests
            if r.method == method.upper() and r.url.host == u.host
            and r.url.path == u.path
        )


def make_fetcher(tmp_path, router: Router, clock: FakeClock, **cfg_overrides):
    cfg = AppConfig()
    cfg.user_agent = "dealradar-test/0.1 (contact: {contact_email})"
    cfg.contact_email = "bot@example.com"
    for key, value in cfg_overrides.items():
        setattr(cfg, key, value)
    db = DB(tmp_path / "state.db")
    fetcher = PoliteFetcher(
        db, cfg,
        transport=httpx.MockTransport(router.handler),
        sleep_fn=clock.sleep,
        clock=clock,
    )
    return fetcher, db, cfg


# --------------------------------------------------------------------- robots


@pytest.mark.parametrize("head_status,head_type,get_status,get_type,valid", [
    (200, "image/jpeg", 500, "", True),  # HEAD suffices: no GET
    (405, "", 200, "image/png", True),
    (501, "", 200, "image/webp", True),
    (200, "", 200, "image/jpeg", True),
    (200, "text/html", 200, "image/jpeg", True),
    (200, "text/html", 200, "text/html", False),
    (405, "", 404, "text/html", False),
    (404, "image/jpeg", 200, "image/jpeg", False),
    (403, "text/html", 200, "image/jpeg", False),  # refusal: no fallback
])
def test_image_check_head_and_get_without_reading_bytes(
        tmp_path, head_status, head_type, get_status, get_type, valid):
    clock, router = FakeClock(), Router()
    image = "https://cdn.example/product.jpg"
    router.add("GET", "https://cdn.example/robots.txt",
               lambda r: httpx.Response(200, text="User-agent: *\nAllow: /\n"))
    router.add("HEAD", image,
               lambda r: httpx.Response(head_status, headers={"Content-Type": head_type}))

    class UnreadImage(httpx.SyncByteStream):
        def __iter__(self):
            raise AssertionError("image bytes must never be read")
            yield b""  # make this a generator

    router.add("GET", image, lambda r: httpx.Response(
        get_status, headers={"Content-Type": get_type}, stream=UnreadImage()))
    fetcher, db, cfg = make_fetcher(tmp_path, router, clock, max_retries=1)
    result, content_type = fetcher.check_image(image)
    assert (200 <= result.status < 300 and content_type.startswith("image/")) is valid
    assert result.body is None
    fallback = head_status in (405, 501) or (head_status == 200 and
                                           not head_type.startswith("image/"))
    assert router.count("GET", image) == int(fallback)
    assert clock.sleeps == [cfg.min_host_interval_s] * (1 + int(fallback))
    assert all(r.headers["User-Agent"] == cfg.resolved_user_agent()
               for r in router.requests)
    fetcher.close()
    db.close()


@pytest.mark.parametrize("method", ["HEAD", "GET"])
def test_image_check_honors_robots_on_redirects(tmp_path, method):
    clock, router = FakeClock(), Router()
    image = "https://cdn.example/product.jpg"
    target = "https://other.example/private/product.jpg"
    router.add("GET", "https://cdn.example/robots.txt",
               lambda r: httpx.Response(200, text="User-agent: *\nAllow: /\n"))
    router.add("HEAD", image, lambda r: httpx.Response(
        302, headers={"Location": target}) if method == "HEAD" else httpx.Response(405))
    router.add("GET", image,
               lambda r: httpx.Response(302, headers={"Location": target}))
    router.add("GET", "https://other.example/robots.txt",
               lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /private\n"))
    fetcher, db, _ = make_fetcher(tmp_path, router, clock)
    result, _ = fetcher.check_image(image)
    assert result.blocked_by_robots
    assert router.count("HEAD", target) == router.count("GET", target) == 0
    fetcher.close()
    db.close()


def test_image_check_never_requests_disallowed_images_or_bot_walls(tmp_path):
    clock, router = FakeClock(), Router()
    image = "https://cdn.example/private.jpg"
    wall = "https://www.ebay.com/splashui/challenge?ru=product"
    router.add("GET", "https://cdn.example/robots.txt",
               lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /\n"))
    fetcher, db, _ = make_fetcher(tmp_path, router, clock)
    assert fetcher.check_image(image)[0].blocked_by_robots
    assert router.count("HEAD", image) == router.count("GET", image) == 0
    assert fetcher.check_image(wall)[0].error == "challenge_page"
    assert len(router.requests) == 1
    fetcher.close()
    db.close()


def test_robots_disallow_blocks_without_request(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("GET", "https://example.com/robots.txt",
               lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /private\n"))
    router.add("GET", "https://example.com/ok",
               lambda r: httpx.Response(200, content=b"hello"))
    fetcher, db, cfg = make_fetcher(tmp_path, router, clock)

    res = fetcher.get("https://example.com/private/feed")
    assert res.blocked_by_robots is True
    assert res.status == 0
    assert res.body is None
    assert res.error == "robots_disallowed"
    assert res.url == res.final_url == "https://example.com/private/feed"
    assert router.count("GET", "https://example.com/private/feed") == 0

    # Allowed path still works, and robots.txt was fetched exactly once.
    res2 = fetcher.get("https://example.com/ok")
    assert res2.status == 200
    assert res2.body == b"hello"
    fetcher.get("https://example.com/private/other")  # blocked, cached robots
    assert router.count("GET", "https://example.com/robots.txt") == 1
    fetcher.close()


def test_robots_4xx_means_everything_allowed(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("GET", "https://open.example/robots.txt",
               lambda r: httpx.Response(404))
    router.add("GET", "https://open.example/feed",
               lambda r: httpx.Response(200, content=b"ok"))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock)
    res = fetcher.get("https://open.example/feed")
    assert res.status == 200
    assert res.blocked_by_robots is False
    fetcher.close()


def test_robots_override_applies_to_the_permitted_host_only(tmp_path):
    """Written-permission override (sources.yaml crawl_permission): robots is
    skipped for exactly the permitted host; a redirect to ANY OTHER host is
    still robots-checked, so a grant can never leak past its grantor."""
    clock, router = FakeClock(), Router()
    router.add("GET", "https://granted.example/robots.txt",
               lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /\n"))
    router.add("GET", "https://granted.example/feed",
               lambda r: httpx.Response(200, content=b"<rss/>"))
    router.add("GET", "https://granted.example/hop",
               lambda r: httpx.Response(
                   301, headers={"Location": "https://other.example/feed"}))
    router.add("GET", "https://other.example/robots.txt",
               lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /\n"))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock)

    # Without the override the disallow holds.
    assert fetcher.get("https://granted.example/feed").blocked_by_robots is True
    # With it, the permitted host's own page fetches.
    res = fetcher.get("https://granted.example/feed",
                      robots_override_host="granted.example")
    assert res.status == 200 and res.body == b"<rss/>"
    # A redirect leaving the permitted host is still robots-checked.
    hop = fetcher.get("https://granted.example/hop",
                      robots_override_host="granted.example")
    assert hop.blocked_by_robots is True
    # The override never covers a different host directly either.
    assert fetcher.get("https://other.example/feed",
                       robots_override_host="granted.example"
                       ).blocked_by_robots is True
    fetcher.close()


def test_robots_5xx_disallows_host_for_run(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("GET", "https://flaky.example/robots.txt",
               lambda r: httpx.Response(500))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock)
    res = fetcher.get("https://flaky.example/feed")
    assert res.blocked_by_robots is True
    assert res.status == 0
    # Cached as disallowed: robots.txt not refetched for the next URL.
    fetcher.get("https://flaky.example/other")
    assert router.count("GET", "https://flaky.example/robots.txt") == 1
    fetcher.close()


def test_robots_transport_error_disallows_host(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("GET", "https://down.example/robots.txt",
               lambda r: httpx.ConnectError("no route"))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock)
    res = fetcher.get("https://down.example/feed")
    assert res.blocked_by_robots is True
    assert res.status == 0
    assert router.count("GET", "https://down.example/feed") == 0
    fetcher.close()


def test_head_honors_robots_check(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("GET", "https://closed.example/robots.txt",
               lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /\n"))
    router.add("HEAD", "https://closed.example/page",
               lambda r: httpx.Response(200))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock)
    res = fetcher.head("https://closed.example/page")
    assert res.status == 0
    assert res.blocked_by_robots is True
    assert router.count("GET", "https://closed.example/robots.txt") == 1
    assert router.count("HEAD", "https://closed.example/page") == 0
    fetcher.close()


def test_user_agent_with_contact_email_on_every_request(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("GET", "https://ua.example/feed",
               lambda r: httpx.Response(200, content=b"ok"))
    fetcher, _, cfg = make_fetcher(tmp_path, router, clock)
    fetcher.get("https://ua.example/feed")
    expected = cfg.resolved_user_agent()
    assert "bot@example.com" in expected
    assert router.count("GET", "https://ua.example/robots.txt") == 1
    for request in router.requests:
        assert request.headers["User-Agent"] == expected
    fetcher.close()


# ----------------------------------------------------------------- rate limit


def test_rate_limit_spacing_per_host(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("GET", "https://example.com/feed",
               lambda r: httpx.Response(200, content=b"ok"))
    router.add("GET", "https://other.example/feed",
               lambda r: httpx.Response(200, content=b"ok"))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock,
                                 min_host_interval_s=1.0)
    # Request 1 to example.com is the robots fetch (no wait); the GET itself
    # then waits a full interval, and so does the second GET.
    fetcher.get("https://example.com/feed")
    fetcher.get("https://example.com/feed")
    assert clock.sleeps == [1.0, 1.0]
    # A different host is independent: its robots fetch needs no wait (only
    # the GET after it does, because robots fetches count toward the limit).
    fetcher.get("https://other.example/feed")
    assert clock.sleeps == [1.0, 1.0, 1.0]
    fetcher.close()


# -------------------------------------------------------------------- retries


def test_retry_backoff_on_500_then_success(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("GET", "https://retry.example/feed",
               lambda r: httpx.Response(500),
               lambda r: httpx.Response(500),
               lambda r: httpx.Response(200, content=b"ok"))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock,
                                 min_host_interval_s=0.0, max_retries=3)
    res = fetcher.get("https://retry.example/feed")
    assert res.status == 200
    assert res.body == b"ok"
    assert router.count("GET", "https://retry.example/feed") == 3
    # Backoff sequence 1 s then 2 s, each with +-30% jitter.
    assert len(clock.sleeps) == 2
    assert 0.7 <= clock.sleeps[0] <= 1.3
    assert 1.4 <= clock.sleeps[1] <= 2.6
    fetcher.close()


def test_retries_exhausted_returns_final_5xx(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("GET", "https://dead.example/feed",
               lambda r: httpx.Response(503))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock,
                                 min_host_interval_s=0.0, max_retries=3)
    res = fetcher.get("https://dead.example/feed")
    assert res.status == 503
    assert res.error == "http_503"
    assert res.body is None
    assert router.count("GET", "https://dead.example/feed") == 3
    fetcher.close()


def test_429_retry_after_honored(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("GET", "https://busy.example/feed",
               lambda r: httpx.Response(429, headers={"Retry-After": "7"}),
               lambda r: httpx.Response(200, content=b"ok"))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock,
                                 min_host_interval_s=0.0, max_retries=3)
    res = fetcher.get("https://busy.example/feed")
    assert res.status == 200
    assert clock.sleeps == [7.0]
    fetcher.close()


def test_429_retry_after_capped_at_30s(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("GET", "https://slow.example/feed",
               lambda r: httpx.Response(429, headers={"Retry-After": "600"}),
               lambda r: httpx.Response(200, content=b"ok"))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock,
                                 min_host_interval_s=0.0, max_retries=3)
    res = fetcher.get("https://slow.example/feed")
    assert res.status == 200
    assert clock.sleeps == [30.0]
    fetcher.close()


def test_plain_4xx_is_final_no_retry(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("GET", "https://gone.example/feed",
               lambda r: httpx.Response(404, content=b"nope"))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock,
                                 min_host_interval_s=0.0, max_retries=3)
    res = fetcher.get("https://gone.example/feed")
    assert res.status == 404
    assert res.error == "http_404"
    assert router.count("GET", "https://gone.example/feed") == 1
    assert clock.sleeps == []
    fetcher.close()


def test_transport_error_returns_status_0_never_raises(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("GET", "https://noconn.example/feed",
               lambda r: httpx.ConnectError("boom"))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock,
                                 min_host_interval_s=0.0, max_retries=2)
    res = fetcher.get("https://noconn.example/feed")
    assert res.status == 0
    assert res.error == "ConnectError"
    assert res.blocked_by_robots is False
    assert router.count("GET", "https://noconn.example/feed") == 2
    fetcher.close()


# ------------------------------------------------------------ conditional GET


def test_conditional_get_stores_then_sends_validators(tmp_path):
    clock, router = FakeClock(), Router()
    url = "https://feed.example/rss"
    etag = 'W/"abc123"'
    last_mod = "Wed, 01 Oct 2025 00:00:00 GMT"

    def feed(request: httpx.Request) -> httpx.Response:
        if request.headers.get("If-None-Match") == etag:
            assert request.headers.get("If-Modified-Since") == last_mod
            return httpx.Response(304)
        return httpx.Response(
            200, headers={"ETag": etag, "Last-Modified": last_mod},
            content=b"<rss/>",
        )

    router.add("GET", url, feed)
    fetcher, db, _ = make_fetcher(tmp_path, router, clock,
                                  min_host_interval_s=0.0)

    first = fetcher.get(url)
    assert first.status == 200
    assert first.body == b"<rss/>"
    assert db.get_validators(url) == (etag, last_mod)

    second = fetcher.get(url)
    assert second.status == 304
    assert second.not_modified is True
    assert second.body is None

    third = fetcher.get(url, conditional=False)
    assert third.status == 200
    feed_requests = [r for r in router.requests if r.url.path == "/rss"]
    assert "If-None-Match" not in feed_requests[-1].headers
    fetcher.close()


# ------------------------------------------------------------------- deadline


def test_deadline_raises_before_any_attempt(tmp_path):
    clock, router = FakeClock(), Router()
    fetcher, _, _ = make_fetcher(tmp_path, router, clock)
    with pytest.raises(DeadlineExceeded):
        fetcher.get("https://late.example/feed", deadline=clock())
    assert router.requests == []
    fetcher.close()


def test_deadline_raises_before_backoff_sleep_that_crosses_it(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("GET", "https://late.example/feed",
               lambda r: httpx.Response(500))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock,
                                 min_host_interval_s=0.0, max_retries=3)
    # First attempt fits; the ~1 s backoff (>= 0.7 s) would cross the deadline.
    with pytest.raises(DeadlineExceeded):
        fetcher.get("https://late.example/feed", deadline=clock() + 0.5)
    assert router.count("GET", "https://late.example/feed") == 1
    assert clock.sleeps == []
    fetcher.close()


# ------------------------------------------------------------------- body cap


def test_body_capped_at_5mb(tmp_path):
    clock, router = FakeClock(), Router()
    big = b"x" * (MAX_BODY_BYTES + 4096)
    router.add("GET", "https://big.example/blob",
               lambda r: httpx.Response(200, content=big))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock,
                                 min_host_interval_s=0.0)
    res = fetcher.get("https://big.example/blob")
    assert res.status == 200
    assert res.body is not None
    assert len(res.body) == MAX_BODY_BYTES
    fetcher.close()


def test_get_follows_redirects_and_reports_final_url(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("GET", "https://move.example/old",
               lambda r: httpx.Response(
                   301, headers={"Location": "https://move.example/new"}))
    router.add("GET", "https://move.example/new",
               lambda r: httpx.Response(200, content=b"moved"))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock,
                                 min_host_interval_s=0.0)
    res = fetcher.get("https://move.example/old")
    assert res.status == 200
    assert res.body == b"moved"
    assert res.url == "https://move.example/old"
    assert res.final_url == "https://move.example/new"
    fetcher.close()


# ---------------------------------------------------- canonicalize / ASIN


@pytest.mark.parametrize("url,expected", [
    ("https://www.amazon.com/dp/B0ABCDE123", "B0ABCDE123"),
    ("https://www.amazon.com/Some-Item/dp/B0ABCDE123/ref=sr_1_1", "B0ABCDE123"),
    ("https://www.amazon.com/gp/product/b0abcde123/", "B0ABCDE123"),
    ("https://www.amazon.com/gp/aw/d/B012345678?psc=1", "B012345678"),
    # Non-amazon hosts never yield ASINs: /product/<10 alnum> is a slug there.
    ("https://shop.example/product/B099999999", ""),
    ("https://www.amazon.com/dp/B0ABCDE1234", ""),   # 11 chars: not an ASIN
    ("https://www.amazon.com/dp/B0ABCDE12", ""),     # 9 chars: not an ASIN
    ("https://example.com/no-asin-here", ""),
    ("", ""),
])
def test_extract_asin(url, expected):
    assert extract_asin(url) == expected


def test_extract_asin_without_scheme():
    # Deal posts often write the link bare ("amazon.com/dp/..."); losing the
    # ASIN there cost the deal its Keepa checks and product photo.
    assert extract_asin("amazon.com/dp/B0DJWMD7R2") == "B0DJWMD7R2"
    assert extract_asin("www.amazon.com/gp/product/B0C8WTP6W8?th=1") == "B0C8WTP6W8"
    assert extract_asin("example.com/dp/B0DJWMD7R2") == ""


@pytest.mark.parametrize("raw,expected", [
    # Amazon URLs with an ASIN collapse to the canonical /dp/ form.
    ("https://www.amazon.com/Cool-Gadget/dp/B0ABCDE123/ref=sr_1_1?tag=deals-20&th=1&psc=1",
     "https://www.amazon.com/dp/B0ABCDE123"),
    ("https://smile.amazon.co.uk/gp/product/b0abcde123?ref_=foo",
     "https://www.amazon.com/dp/B0ABCDE123"),
    # utm stripping keeps real params and their order.
    ("https://Example.com/Path?utm_source=nl&a=1&utm_medium=email&b=2#section",
     "https://example.com/Path?a=1&b=2"),
    # amzn-style tracking params stripped everywhere.
    ("https://shop.example.com/item?fbclid=xyz&id=42&gclid=1&linkCode=ur2&ascsubtag=aa&tag=aff-20",
     "https://shop.example.com/item?id=42"),
    # Param order preserved.
    ("https://example.com/p?b=2&a=1", "https://example.com/p?b=2&a=1"),
    # Fragment dropped; host lowercased; path case kept.
    ("https://EXAMPLE.com/Keep/Case#frag", "https://example.com/Keep/Case"),
    # Tracking names only match the parameter name, not its value.
    ("https://example.com/p?keep=utm_source", "https://example.com/p?keep=utm_source"),
    # Amazon host without an ASIN is treated like any other host.
    ("https://www.amazon.com/deals?utm_campaign=x&node=5",
     "https://www.amazon.com/deals?node=5"),
])
def test_canonicalize_url(raw, expected):
    assert canonicalize_url(raw) == expected


# ------------------------------------------------------------- redirect chain


def test_resolve_redirects_follows_head_chain(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("HEAD", "https://short.ly/abc",
               lambda r: httpx.Response(
                   301, headers={"Location": "https://store.example.com/x"}))
    router.add("HEAD", "https://store.example.com/x",
               lambda r: httpx.Response(
                   302, headers={"Location": "/final?utm_source=t&id=9"}))
    router.add("HEAD", "https://store.example.com/final",
               lambda r: httpx.Response(200))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock,
                                 min_host_interval_s=0.0)
    out = resolve_redirects(fetcher, "https://short.ly/abc")
    assert out == "https://store.example.com/final?id=9"
    assert {r.method for r in router.requests} == {"HEAD", "GET"}
    assert sum(r.method == "HEAD" for r in router.requests) == 3
    assert sum(r.url.path == "/robots.txt" for r in router.requests) == 2
    fetcher.close()


def test_resolve_redirects_head_405_falls_back_to_get(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("HEAD", "https://short.ly/g", lambda r: httpx.Response(405))
    router.add("GET", "https://short.ly/g",
               lambda r: httpx.Response(
                   301, headers={"Location": "https://dest.example.com/page"}))
    router.add("HEAD", "https://dest.example.com/page",
               lambda r: httpx.Response(200))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock,
                                 min_host_interval_s=0.0)
    out = resolve_redirects(fetcher, "https://short.ly/g")
    assert out == "https://dest.example.com/page"
    # The GET fallback consulted robots.txt first (default: unrouted -> 404 -> allowed).
    assert router.count("GET", "https://short.ly/robots.txt") == 1
    assert router.count("GET", "https://short.ly/g") == 1
    fetcher.close()


def test_resolve_redirects_robots_blocked_get_ends_loop(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("GET", "https://blocked.example.com/robots.txt",
               lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /\n"))
    router.add("HEAD", "https://blocked.example.com/r",
               lambda r: httpx.Response(403))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock,
                                 min_host_interval_s=0.0)
    out = resolve_redirects(fetcher, "https://blocked.example.com/r?utm_source=x")
    assert out == "https://blocked.example.com/r"
    assert router.count("GET", "https://blocked.example.com/r") == 0
    fetcher.close()


def test_resolve_redirects_hop_cap(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("HEAD", "https://loop.example/a",
               lambda r: httpx.Response(
                   301, headers={"Location": "https://loop.example/b"}))
    router.add("HEAD", "https://loop.example/b",
               lambda r: httpx.Response(
                   301, headers={"Location": "https://loop.example/a"}))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock,
                                 min_host_interval_s=0.0)
    out = resolve_redirects(fetcher, "https://loop.example/a", max_hops=3)
    # a -> b -> a -> b: exactly 3 hops performed, then we stop where we are.
    assert out == "https://loop.example/b"
    assert sum(r.method == "HEAD" for r in router.requests) == 3
    assert router.count("GET", "https://loop.example/robots.txt") == 1
    fetcher.close()


def test_resolve_redirects_asin_short_circuit_skips_retailer_fetch(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("HEAD", "https://short.ly/amz",
               lambda r: httpx.Response(301, headers={
                   "Location": "https://www.amazon.com/Nice-Thing/dp/B0ABCDE123?tag=aff-20"}))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock,
                                 min_host_interval_s=0.0)
    out = resolve_redirects(fetcher, "https://short.ly/amz")
    assert out == "https://www.amazon.com/dp/B0ABCDE123"
    assert not any("amazon" in str(r.url) for r in router.requests)
    fetcher.close()


def test_resolve_redirects_transport_error_returns_last_known(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("HEAD", "https://short.ly/x",
               lambda r: httpx.Response(301, headers={
                   "Location": "https://dead.example/page?utm_source=t"}))
    router.add("HEAD", "https://dead.example/page",
               lambda r: httpx.ConnectError("boom"))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock,
                                 min_host_interval_s=0.0, max_retries=1)
    out = resolve_redirects(fetcher, "https://short.ly/x")
    assert out == "https://dead.example/page"
    fetcher.close()


def test_resolve_redirects_deadline_applies_to_hops(tmp_path):
    clock, router = FakeClock(), Router()
    fetcher, _, _ = make_fetcher(tmp_path, router, clock,
                                 min_host_interval_s=0.0)
    with pytest.raises(DeadlineExceeded):
        resolve_redirects(fetcher, "https://short.ly/x", deadline=clock())
    assert router.requests == []
    fetcher.close()


def test_resolve_redirects_gives_a_rate_limited_store_one_quick_try(tmp_path):
    """Live wart: a store answering 429 with Retry-After 30 was retried 3x,
    90 s per link, and slow stores ate 5.5 minutes of a 12-minute run.
    Resolution is best effort: one try per hop, a short timeout, no backoff;
    the link as posted is kept."""
    clock, router = FakeClock(), Router()
    router.add("HEAD", "https://slowstore.example/p/1",
               lambda r: httpx.Response(429, headers={"Retry-After": "30"}))
    seen_timeouts = []

    def timed(r):
        seen_timeouts.append(r.extensions.get("timeout"))
        return httpx.Response(301, headers={"Location": "https://slowstore.example/p/1"})

    router.add("HEAD", "https://short.ly/q", timed)
    fetcher, _, _ = make_fetcher(tmp_path, router, clock,
                                 min_host_interval_s=0.0, max_retries=3)
    out = resolve_redirects(fetcher, "https://short.ly/q")
    assert out == "https://slowstore.example/p/1"
    assert router.count("HEAD", "https://slowstore.example/p/1") == 1
    assert clock.sleeps == []  # no backoff, no Retry-After wait
    assert seen_timeouts and all(
        t and t.get("read") == http_client.REDIRECT_HOP_TIMEOUT_S for t in seen_timeouts)
    # Ordinary fetches keep their retries.
    router.add("GET", "https://slowstore.example/robots.txt",
               lambda r: httpx.Response(200, text="User-agent: *\nAllow: /\n"))
    router.add("GET", "https://slowstore.example/feed",
               lambda r: httpx.Response(429))
    fetcher.get("https://slowstore.example/feed", conditional=False)
    assert router.count("GET", "https://slowstore.example/feed") == 3
    fetcher.close()


# ------------------------------------------------------------------ bot walls

WM_ITEM = "https://www.walmart.com/ip/Glue-Gun/1949719114?irgwc=1&clickid=c1"
EBAY_ITEM = "https://www.ebay.com/itm/158273640669?mkcid=1"
# The live shapes: Walmart's PerimeterX wall carries the refused path+query
# as base64, eBay's challenge the refused URL itself; a per-request uuid
# made one product several dedupe keys.
WM_WALL = ("https://www.walmart.com/blocked?url=L2lwL0dsdWUtR3VuLzE5NDk3MTkxMTQ"
           "%2FaXJnd2M9MSZjbGlja2lkPWMx&uuid=4f1e&vid=&g=b")
EBAY_WALL = ("https://www.ebay.com/splashui/challenge?ap=1&appName=orch&ru="
             "https%3A%2F%2Fwww.ebay.com%2Fitm%2F158273640669%3Fmkcid%3D1&iid=e2")
PX_WALL = "https://www.zillow.com/captchaPerimeterX/?url=%2Fhomedetails%2F1&uuid=9"
# Sam's Club's wall is Walmart's under another path (live: row 503).
SAMS_ITEM = ("https://www.samsclub.com/ip/HP-Smart-Tank-5104/17067010453"
             "?classType=REGULAR&irgwc=1&clickid=c1")
SAMS_WALL = ("https://www.samsclub.com/are-you-human?url=L2lwL0hQLVNtYXJ0LVRhbmst"
             "NTEwNC8xNzA2NzAxMDQ1Mz9jbGFzc1R5cGU9UkVHVUxBUiZpcmd3Yz0xJmNsaWNraWQ9YzE"
             "&uuid=6fd63ab9-c4f8-11f1-b1f2-26a5e253ad17&vid=&g=b")


@pytest.mark.parametrize("product,wall", [
    (WM_ITEM, WM_WALL), (EBAY_ITEM, EBAY_WALL), (SAMS_ITEM, SAMS_WALL),
    ("https://www.zillow.com/homedetails/1", PX_WALL),
    ("https://shop.example.com/p/kettle", "/px-captcha?uuid=1"),  # relative
])
def test_resolve_redirects_stops_before_a_bot_wall(tmp_path, product, wall):
    """Live wart: Walmart and eBay answer a crawler's product request by
    redirecting to their bot wall; the wall became the deal's link (and,
    with its per-request uuid, several cards for one product). The loop
    keeps the URL the store redirected FROM and never requests the wall."""
    clock, router = FakeClock(), Router()
    router.add("HEAD", "https://short.ly/w",
               lambda r: httpx.Response(302, headers={"Location": product}))
    router.add("HEAD", product,
               lambda r: httpx.Response(307, headers={"Location": wall}))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock, min_host_interval_s=0.0)
    out = resolve_redirects(fetcher, "https://short.ly/w")
    assert out == canonicalize_url(product)
    assert sum(r.method == "HEAD" for r in router.requests) == 2
    assert not any(str(r.url) == wall for r in router.requests)
    fetcher.close()


def test_get_never_follows_into_a_bot_wall(tmp_path):
    clock, router = FakeClock(), Router()
    router.add("GET", WM_ITEM,
               lambda r: httpx.Response(307, headers={"Location": WM_WALL}))
    fetcher, _, _ = make_fetcher(tmp_path, router, clock, min_host_interval_s=0.0)
    res = fetcher.get(WM_ITEM, conditional=False)
    assert (res.status, res.body, res.error) == (0, None, "challenge_page")
    assert router.count("GET", WM_ITEM) == 1
    assert router.count("GET", "https://www.walmart.com/blocked") == 0
    fetcher.close()


def test_challenge_target_decodes_the_refused_product():
    assert is_challenge_url(WM_WALL) and is_challenge_url(EBAY_WALL)
    assert is_challenge_url(PX_WALL) and is_challenge_url(SAMS_WALL)
    assert challenge_target(WM_WALL) == WM_ITEM
    assert challenge_target(EBAY_WALL) == EBAY_ITEM
    assert challenge_target(SAMS_WALL) == SAMS_ITEM  # on Sam's own host
    # A '+' of base64 is no space (Walmart may not percent-encode it).
    plus = "https://www.walmart.com/blocked?url=L2lwLz8+&uuid=1"
    assert challenge_target(plus) == "https://www.walmart.com/ip/?>"


@pytest.mark.parametrize("url", [
    WM_ITEM, EBAY_ITEM, "https://example.com/blocked?url=L2lwLzE",  # no wall
    "https://www.walmart.com/blocked?uuid=1",                  # no url param
    "https://www.walmart.com/blocked?url=%%%&uuid=1",          # not base64
    "https://www.walmart.com/blocked?url=Ly9ldmlsLmNvbS94",    # "//evil.com/x"
    "https://www.walmart.com/blocked?url=aXAvMQ",              # "ip/1": no "/"
    "https://www.walmart.com/blocked?url=L2lwLyAx",            # "/ip/ 1": a space
    "https://www.samsclub.com/are-you-human?uuid=1",           # no url param
    "https://www.ebay.com/splashui/challenge?ap=1",            # no ru
    "https://www.ebay.com/splashui/challenge?ru=https%3A%2F%2Fevil.com%2Fitm%2F1",
    "https://www.ebay.com/splashui/challenge?ru=%2Fitm%2F1",   # not absolute
    "https://www.ebay.com/splashui/challenge?ru=https%3A%2F%2F%5Bbad",  # unparsable
])
def test_challenge_target_leaves_anything_else_alone(url):
    assert challenge_target(url) == ""
