"""Mock transports for older API fixtures and a deterministic host clock."""

import httpx

from dealradar import verify
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.http_client import PoliteFetcher


class HostClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def api_transport(handler):
    def route(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return handler(req)
    return httpx.MockTransport(route)


def mock_keepa_get(monkeypatch, handler):
    """Keep existing product fixtures, with real polite HTTP around them."""
    def route(req):
        params = dict(req.url.params)
        for field in ("domain", "stats", "offers"):
            if field in params:
                params[field] = int(params[field])
        return handler(str(req.url.copy_with(query=None)), params=params, timeout=20)
    clock = HostClock()
    fetcher = PoliteFetcher(DB(":memory:"), load_config(), transport=api_transport(route),
                            sleep_fn=clock.sleep, clock=clock)
    monkeypatch.setattr(verify, "_standalone_keepa_fetcher", lambda: fetcher)
    return fetcher
