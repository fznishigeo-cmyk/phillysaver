"""Every source API shares robots checks, host pacing and safe diagnostics."""

import json
import logging
from urllib.parse import quote

import httpx
import pytest

from dealradar import verify
from dealradar.fetch.base import run_source
from dealradar.fetch.keepa import KeepaDealsAdapter
from dealradar.fetch.meh import MehAdapter
from dealradar.fetch.reddit import RedditAdapter
from dealradar.fetch.threads_source import ThreadsAdapter
from dealradar.fetch.woot import WootAdapter
from dealradar.http_client import PoliteFetcher, resolve_redirects
from dealradar.models import SourceConfig
from tests.http_helpers import HostClock
from tests.test_partner_policies import NOW, cfg, db, source
from tests.test_keepa import _amazon_row


@pytest.mark.parametrize("check", ["ua", "gap"])
def test_product_calls_use_contact_and_host_gap(db, cfg, monkeypatch, check):
    clock, seen = HostClock(), []
    def handle(req):
        seen.append((clock(), req))
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, json={"products": []})
    transport = httpx.MockTransport(handle)
    f = PoliteFetcher(db, cfg, transport=transport, clock=clock, sleep_fn=clock.sleep)
    try:
        with httpx.Client(transport=transport) as client:
            monkeypatch.setattr(httpx, "get", client.get)
            monkeypatch.setattr(verify, "_standalone_keepa_fetcher", lambda: f, raising=False)
            verify.keepa_products("synthetic-credential", ["B012345678"], 1, None)
            verify.keepa_products("synthetic-credential", ["B012345679"], 1, None)
        products = [(at, r) for at, r in seen if r.url.path == "/product"]
        assert len(products) == 2
        if check == "ua":
            assert all("hello@wowpriced.com" in r.headers["User-Agent"] for _, r in seen)
        else:
            assert products[1][0] - products[0][0] >= 1.0
    finally:
        f.close()


@pytest.mark.parametrize("name,adapter", [
    ("meh", MehAdapter), ("woot", WootAdapter),
    ("keepa_amazon_drops", KeepaDealsAdapter), ("reddit_deals", RedditAdapter),
    ("threads", ThreadsAdapter),
])
def test_keyed_apis_are_not_crawling_but_keep_our_name(db, cfg, monkeypatch, name, adapter):
    """robots.txt governs crawling web pages. api.keepa.com, developer.woot.com
    and meh.com answer robots.txt with Disallow/403/redirect for the very
    endpoints their API terms license to keyed clients (robots probe,
    2026-10-13), so a keyed call never asks robots.txt; it still carries our
    User-Agent and goes through the shared pacing."""
    for key in ("MEH_API_KEY", "WOOT_API_KEY", "KEEPA_KEY", "REDDIT_CLIENT_ID",
                "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT", "THREADS_TOKEN"):
        monkeypatch.setenv(key, "synthetic-credential")
    cfg.feature_flags["threads"] = True
    requests = []
    def handle(req):
        requests.append(req)
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return httpx.Response(200, json={"products": [], "items": []})
    transport = httpx.MockTransport(handle)
    f = PoliteFetcher(db, cfg, transport=transport, sleep_fn=lambda _: None)
    src = source(name) if name not in {"reddit_deals", "threads"} else SourceConfig(
        name=name, method="api", adapter=name, url="https://oauth.reddit.com/r/deals/new")
    try:
        with httpx.Client(transport=transport) as client:
            run_source(src, adapter(http_client=client), f, db, cfg, NOW)
        assert requests and all(r.url.path != "/robots.txt" for r in requests)
        assert all("hello@wowpriced.com" in r.headers["User-Agent"] for r in requests)
    finally:
        f.close()


def test_cached_reddit_token_still_paces_listings(db, cfg, monkeypatch):
    for name in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"):
        monkeypatch.setenv(name, "synthetic-credential")
    clock, seen = HostClock(), []
    def handle(req):
        seen.append((clock(), req))
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.path == "/api/v1/access_token":
            assert req.headers["Authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "synthetic-token"})
        assert req.headers["Authorization"] == "bearer synthetic-token"
        return httpx.Response(200, json={"data": {"children": []}})
    f = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handle),
                      clock=clock, sleep_fn=clock.sleep)
    adapter = RedditAdapter()
    src = SourceConfig("reddit_deals", "api", "https://oauth.reddit.com/r/deals/new", adapter="reddit")
    try:
        adapter.fetch_posts(src, f, db, cfg, NOW, None)
        adapter.fetch_posts(src, f, db, cfg, NOW, None)
        listings = [(at, r) for at, r in seen if r.url.host == "oauth.reddit.com" and r.url.path != "/robots.txt"]
        assert listings[1][0] - listings[0][0] >= 1.0
        assert sum(r.url.path == "/api/v1/access_token" for _, r in seen) == 1
        assert all("hello@wowpriced.com" in r.headers["User-Agent"] for _, r in seen)
    finally:
        f.close()


def test_api_redirect_checks_target_and_drops_auth(db, cfg):
    requests = []
    def handle(req):
        requests.append(req)
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if req.url.host == "api.example":
            return httpx.Response(302, headers={"Location": "https://target.example/feed"})
        assert "Authorization" not in req.headers
        assert "x-api-key" not in req.headers
        return httpx.Response(200, json={"items": []})
    f = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handle), sleep_fn=lambda _: None)
    try:
        f.api_get("https://api.example/feed", headers={"Authorization": "bearer synthetic-token", "x-api-key": "synthetic-key"})
        assert [r.url.path for r in requests] == ["/robots.txt", "/feed", "/robots.txt", "/feed"]
    finally:
        f.close()


def test_head_redirect_stops_at_target_robots(db, cfg):
    requests = []
    def handle(req):
        requests.append(req)
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\n" + (
                "Disallow: /\n" if req.url.host == "target.example" else "Allow: /\n"))
        return httpx.Response(302, headers={"Location": "https://target.example/private"})
    f = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handle), sleep_fn=lambda _: None)
    try:
        resolve_redirects(f, "https://short.example/item")
        assert [(r.url.host, r.url.path) for r in requests] == [
            ("short.example", "/robots.txt"), ("short.example", "/item"),
            ("target.example", "/robots.txt")]
    finally:
        f.close()


def test_api_validators_retry_and_transient_body_cache(db, cfg):
    clock, seen = HostClock(), []
    count = 0
    def handle(req):
        nonlocal count
        seen.append((clock(), req))
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        count += 1
        if count == 1:
            return httpx.Response(503)
        if "If-None-Match" in req.headers:
            return httpx.Response(304)
        return httpx.Response(200, json={"items": ["widget"]}, headers={"ETag": "v1"})
    f = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handle), clock=clock, sleep_fn=clock.sleep)
    try:
        assert f.api_get("https://api.example/feed", params={"key": "synthetic-credential"}).json() == {"items": ["widget"]}
        assert f.api_get("https://api.example/feed", params={"key": "synthetic-credential"}).json() == {"items": ["widget"]}
        assert count == 3
        assert all(b - a >= 1.0 for (a, _), (b, _) in zip(seen, seen[1:]))
        assert all("synthetic-credential" not in row["url"]
                   for row in db.conn.execute("SELECT url FROM http_cache"))
    finally:
        f.close()
    fresh = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handle), sleep_fn=lambda _: None)
    try:
        assert fresh.api_get("https://api.example/feed", params={"key": "synthetic-credential"}).json() == {"items": ["widget"]}
        assert count == 5  # 304 without a retained body, then unconditional 200
    finally:
        fresh.close()


@pytest.mark.parametrize("operation", ["keepa_check", "recheck", "batch", "meh", "keepa_pages", "transport"])
@pytest.mark.parametrize("sentinel", ["synthetic-credential", "synthetic+credential/value"])
def test_api_errors_log_no_credentials(db, cfg, monkeypatch, caplog, operation, sentinel):
    for name in ("KEEPA_KEY", "MEH_API_KEY"):
        monkeypatch.setenv(name, sentinel)
    cfg.feature_flags["keepa"] = True
    cfg.keepa_focus_categories = []
    cfg.keepa_amazon_pages_per_pull = 2
    cfg.keepa_amazon_max_new_posts_per_pull = 500
    cfg.max_retries = 1
    def handle(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if operation == "transport":
            raise httpx.ReadTimeout("failed " + str(req.url), request=req)
        if operation == "keepa_pages" and req.url.path == "/deal":
            selection = json.loads(req.url.params["selection"])
            if selection["priceTypes"] == [0] and selection["page"] == 0:
                return httpx.Response(200, json={"deals": {"dr": [_amazon_row(i) for i in range(150)]}})
            if selection["page"] == 0:
                return httpx.Response(200, json={"deals": {"dr": []}})
        return httpx.Response(403)
    transport = httpx.MockTransport(handle)
    f = PoliteFetcher(db, cfg, transport=transport, sleep_fn=lambda _: None)
    deal = {"asin": "B012345678", "dedupe_key": "widget", "final_price": 30.0}
    try:
        with httpx.Client(transport=transport) as client:
            monkeypatch.setattr(httpx, "get", client.get)
            monkeypatch.setattr(verify, "_standalone_keepa_fetcher", lambda: f, raising=False)
            with caplog.at_level(logging.WARNING):
                if operation in {"keepa_check", "transport"}:
                    verify.keepa_check(deal, cfg)
                elif operation == "recheck":
                    verify.recheck_amazon_price(deal, cfg)
                elif operation == "batch":
                    verify.recheck_amazon_prices([deal], cfg)
                else:
                    name = "meh" if operation == "meh" else "keepa_amazon_drops"
                    adapter = MehAdapter if operation == "meh" else KeepaDealsAdapter
                    run_source(source(name), adapter(http_client=client), f, db, cfg, NOW)
        assert caplog.text
        assert sentinel not in caplog.text
        assert quote(sentinel, safe="") not in caplog.text
        assert "Traceback" not in caplog.text
        assert "https://" not in caplog.text
    finally:
        f.close()


def test_a_gzip_api_answer_is_decoded_once(db, cfg):
    """Keepa always answers gzip; the rebuilt response must not decode again
    (live 2026-10-13: every Keepa call failed with DecodingError)."""
    import gzip
    body = gzip.compress(json.dumps({"products": [], "tokensLeft": 9}).encode())
    def handle(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=body, headers={
            "Content-Encoding": "gzip", "Content-Type": "application/json"})
    f = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handle), sleep_fn=lambda _: None)
    try:
        resp = f.api_get("https://api.keepa.com/product", params={"key": "synthetic"},
                         max_attempts=1, respect_robots=False)
        assert resp.status_code == 200 and resp.json() == {"products": [], "tokensLeft": 9}
    finally:
        f.close()
