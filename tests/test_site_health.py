"""Offline checks of the public visitor report, including privacy and politeness."""

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dealradar.config import AppConfig
from dealradar.growth import report, site_health

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


@pytest.fixture
def clock(monkeypatch):
    ticks = [0.0]
    monkeypatch.setattr(site_health.time, "monotonic", lambda: ticks[0])
    monkeypatch.setattr(site_health.time, "sleep", lambda delay: ticks.__setitem__(0, ticks[0] + delay))
    return ticks


def deal(store="Woot", image="", **extra):
    return dict(store=store, image_url=image, alive=True, src=["source.example"], **extra)


class PublicSite:
    def __init__(self, deals, *, age=6, home_status=200, home='<div id="content"></div>', images=None):
        self.payload = {"deals": deals, "meta": {
            "published_at": (NOW - timedelta(minutes=age)).isoformat(),
            "cadence_minutes": 15, "locked_count": 7}}
        self.home_status, self.home, self.images = home_status, home, images or {}
        self.requests = []

    def __call__(self, req):
        self.requests.append((req, site_health.time.monotonic()))
        assert req.headers["user-agent"].startswith("dealradar/")
        assert req.extensions["timeout"]["read"] == 6
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if req.url.path == "/deals.json":
            return httpx.Response(200, json=self.payload)
        if req.url.host == "shop.example":
            return httpx.Response(self.home_status, text=self.home)
        answer = self.images.get((req.method, req.url.path), (200, "image/jpeg"))
        if isinstance(answer, Exception):
            raise answer
        status, content_type = answer
        return httpx.Response(status, headers={"Content-Type": content_type})


def check(fake):
    cfg = AppConfig(custom_domain="shop.example")
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        return site_health.check(client, cfg, NOW)


def test_coverage_and_top_missing_stores_and_sources(clock):
    missing = []
    for i in range(12):
        missing += [dict(store=f"Store {i}", alive=True, src=[f"src{i}.example"] * 2)] * (i + 1)
    fake = PublicSite(missing + [deal(image="https://img.example/ok")] + [
        dict(store="Dead", alive=False, image_url=""), dict(locked=True)])
    health = check(fake)
    assert health["live"] == 79 and health["photos"] == 1
    assert health["coverage"] == pytest.approx(100 / 79)
    assert health["missing_stores"][0] == ("Store 11", 12)
    assert health["missing_sources"][0] == ("src11.example", 12)
    assert len(health["missing_stores"]) == len(health["missing_sources"]) == 10
    assert health["locked_count"] == 7
    assert health["warnings"] == ["photo coverage under 85%"]
    text = "\n".join(site_health.lines(health))
    assert "⚠ photo coverage" in text and "Last publish: 6 min ago" in text
    assert "src11.example 12" in text


def test_sample_spreads_across_stores_and_rate_limits_each_host(clock):
    fake = PublicSite([deal(f"Store {s}", f"https://img.example/{s}/{i}")
                       for s in range(12) for i in range(10)])
    health = check(fake)
    sampled = [req for req, _ in fake.requests if req.method == "HEAD"]
    assert health["checked"] == len(sampled) == 60
    counts = Counter(req.url.path.split("/")[1] for req in sampled)
    assert len(counts) == 12 and max(counts.values()) <= 6
    previous = {}
    for req, at in fake.requests:
        host = req.url.host
        if host in previous:
            assert at - previous[host] >= 1
        previous[host] = at


def test_six_per_store_even_when_sample_is_small(clock):
    health = check(PublicSite([deal(image=f"https://img.example/{i}") for i in range(20)]))
    assert health["checked"] == 6


def test_broken_status_type_timeout_and_get_fallback(clock):
    fake = PublicSite([deal("Revolve", f"https://img.example/{i}") for i in range(6)], images={
        ("HEAD", "/0"): (404, "image/jpeg"),
        ("HEAD", "/1"): (200, "text/html"),
        ("HEAD", "/2"): (405, "text/html"),
        ("GET", "/2"): (206, "image/webp"),
        ("HEAD", "/3"): (403, "text/html"),
        ("GET", "/3"): (403, "text/html"),
        ("HEAD", "/4"): httpx.ReadTimeout("private image URL"),
        ("HEAD", "/5"): (200, "IMAGE/PNG; charset=binary"),
    })
    health = check(fake)
    # 404 and a non-image page are broken; a 403 and a timeout prove nothing.
    assert health["broken"] == 2 and health["broken_stores"] == [("Revolve", 2)]
    assert health["unchecked"] == 2 and health["checked"] == 4
    assert health["warnings"] == ["broken photos over 5%"]
    fallbacks = [req for req, _ in fake.requests if req.url.host == "img.example" and req.url.path != "/robots.txt" and req.method == "GET"]
    assert len(fallbacks) == 2
    assert all(req.headers["range"] == "bytes=0-1023" for req in fallbacks)


@pytest.mark.parametrize("age, stale", [(45, False), (46, True)])
def test_stale_threshold(clock, age, stale):
    health = check(PublicSite([], age=age))
    assert ("stale publish" in health["warnings"]) == stale


@pytest.mark.parametrize("status, home, warning", [
    (503, '<div id="content">', "home page not 200"),
    (200, "empty shell", "home page missing content"),
])
def test_home_problems(clock, status, home, warning):
    health = check(PublicSite([], home_status=status, home=home))
    assert warning in health["warnings"]


@pytest.mark.parametrize("fail, expected", [(False, "site_health: ok"), (True, "site_health: error HTTPStatusError")])
def test_section_runs_without_secrets_and_logs_only_status(clock, monkeypatch, caplog, fail, expected):
    for secret in ("GOOGLE_SA_JSON", "META_SYSTEM_TOKEN", "STRIPE_SECRET_KEY"):
        monkeypatch.delenv(secret, raising=False)
    fake = PublicSite([deal(image="https://img.example/private-token")])

    def handler(req):
        if fail and req.url.path == "/deals.json":
            return httpx.Response(502, text="private response and URL")
        return fake(req)

    caplog.set_level(logging.INFO, logger="dealradar.growth")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        data, status = report.collect(AppConfig(custom_domain="shop.example"), NOW, client)
    assert status["site_health"] == ("error" if fail else "ok")
    assert [r.message for r in caplog.records if r.name == "dealradar.growth" and r.message.startswith("site_health:")] == [expected]
    assert "private-token" not in caplog.text and "private response" not in caplog.text
    if not fail:
        _, text = report.build_report(AppConfig(), data, status, NOW.date())
        assert text.index("== Site health ==") < text.index("Sections:")


def test_get_fallback_honors_robots(clock):
    requests = []
    fake = PublicSite([deal(image="https://img.example/blocked")])

    def handler(req):
        requests.append(req)
        if req.url.host == "img.example":
            if req.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
            assert req.method == "HEAD"
            return httpx.Response(405)
        return fake(req)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        health = site_health.check(client, AppConfig(custom_domain="shop.example"), NOW)
    assert health["broken"] == 0 and health["unchecked"] == 1  # robots: not ours to judge
    assert not any(req.method == "GET" and req.url.path == "/blocked" for req in requests)


def test_redirect_hops_are_rate_limited_and_challenges_not_followed(clock):
    fake = PublicSite([deal(image="https://img.example/redirect"),
                       deal(image="https://img.example/challenge")])

    def handler(req):
        if req.url.path == "/redirect":
            fake.requests.append((req, site_health.time.monotonic()))
            return httpx.Response(302, headers={"Location": "https://other.example/photo"})
        if req.url.path == "/challenge":
            fake.requests.append((req, site_health.time.monotonic()))
            return httpx.Response(302, headers={"Location": "https://www.walmart.com/blocked"})
        assert req.url.host != "www.walmart.com"
        return fake(req)

    cfg = AppConfig(custom_domain="shop.example", contact_email="hello@shop.example")
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        health = site_health.check(client, cfg, NOW)
    assert health["checked"] == 1 and health["unchecked"] == 1 and health["broken"] == 0
    assert all("hello@shop.example" in req.headers["user-agent"] for req, _ in fake.requests)


def test_broken_share_exactly_five_percent_does_not_warn(clock):
    fake = PublicSite([deal(f"Store {i // 5}", f"https://img.example/{i}") for i in range(20)],
                      images={("HEAD", "/0"): (404, "image/jpeg")})
    health = check(fake)
    assert health["broken"] == 1 and health["checked"] == 20
    assert "broken photos over 5%" not in health["warnings"]


def test_robots_errors_conservatively_block_public_get(clock):
    requests = []

    def handler(req):
        requests.append(req)
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PermissionError):
            site_health.check(client, AppConfig(custom_domain="shop.example"), NOW)
    assert all(req.url.path == "/robots.txt" for req in requests)
