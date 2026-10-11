"""The read-only source probe's offline helpers (scripts/source_probe.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "source_probe", Path(__file__).resolve().parent.parent / "scripts" / "source_probe.py")
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

RSS = (b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
       b"<item><title>a</title><link>https://x.example/a</link></item></channel></rss>")


def test_feed_links_come_from_alternate_link_tags_only():
    html = (b'<html><head><link rel="stylesheet" href="/s.css">'
            b'<link rel="alternate" type="application/rss+xml" href="/feed/">'
            b"<link rel='alternate' type='application/atom+xml' href='https://x.example/atom'>"
            b'<link rel="alternate" hreflang="es" href="/es/"></head></html>')
    assert probe.feed_links(html, "https://x.example/blog/") == [
        "https://x.example/feed/", "https://x.example/atom"]


def test_verdicts_name_the_real_cause():
    base = {"status": 200, "entries": 0, "firewall": "", "robots": False, "error": ""}
    assert probe.verdict({**base, "entries": 3}) == "OK feed, 3 items"
    assert probe.verdict({**base, "status": 403}).startswith("refused (403)")
    assert probe.verdict({**base, "status": 403, "firewall": "cloudflare"}) == \
        "blocked by cloudflare bot protection"
    assert probe.verdict({**base, "status": 404}).startswith("not found (404)")
    assert probe.verdict({**base}) == "a page, not a feed"
    assert probe.verdict({**base, "status": 0, "error": "ConnectError: no host"}) == \
        "no answer: ConnectError: no host"
    assert probe.verdict({**base, "robots": True}).startswith("robots.txt disallows")


def test_feed_and_firewall_detection():
    assert probe.feed_entries(RSS) == 1
    assert probe.feed_entries(b"<html><body>hi</body></html>") == 0
    assert probe.firewall(b"<title>Just a moment...</title>") == "cloudflare"
    assert probe.firewall(b"<html>plain</html>") == ""
