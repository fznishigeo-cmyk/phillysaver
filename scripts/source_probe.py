"""Read-only probe of sources that keep failing (tripped in source health).

Runs in GitHub Actions (.github/workflows/source-probe.yml) because the dev
sandbox cannot reach most source sites. For each named source it makes the
same polite request the pipeline makes (PoliteFetcher: our User-Agent, robots
honoured except on the source's own host when sources.yaml documents written
crawl_permission, per-host rate limit, retries) and reports what came back:
status, final URL, whether the body is a feed, and a guess at a firewall
page. When the URL answers with an HTML page instead of a feed, or 404s, it
looks for the site's advertised feed (<link rel="alternate" ...>) and probes
up to two candidates. Nothing is written anywhere but the job log and the
run summary; it never retries around a 403 or a challenge page.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import feedparser

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dealradar.config import error_summary, load_config, load_sources  # noqa: E402
from dealradar.fetch.rss import _permission_host  # noqa: E402
from dealradar.db import DB  # noqa: E402
from dealradar.http_client import PoliteFetcher  # noqa: E402

FIREWALLS = [
    ("cloudflare", re.compile(rb"cf-chl|Just a moment\.\.\.|Attention Required! \| Cloudflare|cf-error-details", re.I)),
    ("sucuri", re.compile(rb"Sucuri WebSite Firewall|sucuri\.net", re.I)),
    ("wordfence", re.compile(rb"Wordfence|wfvt_", re.I)),
    ("akamai", re.compile(rb"Access Denied.{0,200}Reference #|akamai", re.I | re.S)),
    ("perimeterx", re.compile(rb"px-captcha|perimeterx", re.I)),
    ("imperva", re.compile(rb"Incapsula|_Incapsula_Resource|imperva", re.I)),
]
ALT_RE = re.compile(rb"<link[^>]+>", re.I)


def firewall(body: bytes | None) -> str:
    head = (body or b"")[:20000]
    return next((name for name, rx in FIREWALLS if rx.search(head)), "")


def feed_entries(body: bytes | None) -> int:
    if not body:
        return 0
    parsed = feedparser.parse(body)
    return len(parsed.entries) if parsed.get("version") else 0


def feed_links(body: bytes | None, base: str) -> list[str]:
    out = []
    for tag in ALT_RE.findall((body or b"")[:200000]):
        if re.search(rb"rel=[\"']?alternate", tag, re.I) and re.search(rb"(rss|atom)\+xml", tag, re.I):
            m = re.search(rb"href=[\"']([^\"']+)", tag, re.I)
            if m:
                out.append(urljoin(base, m.group(1).decode("utf-8", "replace")))
    return list(dict.fromkeys(out))


def probe(fetcher: PoliteFetcher, url: str, override: str | None) -> dict:
    kw = {"robots_override_host": override} if override else {}
    try:
        res = fetcher.get(url, conditional=False, **kw)
    except Exception as e:  # a probe never fails the job
        return {"url": url, "status": 0, "error": error_summary(e)}
    return {"url": url, "final": res.final_url, "status": res.status,
            "error": (res.error or "")[:160], "robots": res.blocked_by_robots,
            "bytes": len(res.body or b""), "entries": feed_entries(res.body),
            "firewall": firewall(res.body), "body": res.body}


def verdict(r: dict) -> str:
    if r.get("robots"):
        return "robots.txt disallows (no permission on file for this host)"
    if r["status"] == 0:
        return "no answer: " + (r.get("error") or "transport error")
    if r.get("entries"):
        return f"OK feed, {r['entries']} items"
    if r.get("firewall"):
        return f"blocked by {r['firewall']} bot protection"
    if r["status"] in (401, 403):
        return "refused (403): firewall/server blocks our requests"
    if r["status"] == 404:
        return "not found (404): wrong feed URL"
    if r["status"] == 429:
        return "rate limited (429)"
    if r["status"] >= 500:
        return f"server error ({r['status']})"
    if r["status"] == 200:
        return "a page, not a feed"
    return f"HTTP {r['status']}"


def main() -> int:
    names = [n.strip() for n in os.environ.get("SOURCE_NAMES", "").split(",") if n.strip()]
    cfg = load_config()
    primary, backup = load_sources()
    by_name = {s.name: s for s in primary + backup}
    unknown = [n for n in names if n not in by_name]
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        fetcher = PoliteFetcher(DB(Path(tmp) / "probe.db"), cfg)
        for name in names:
            if name in unknown:
                continue
            src = by_name[name]
            override = _permission_host(src, cfg)
            first = probe(fetcher, src.url, override)
            tried = [first]
            if not first.get("entries") and first["status"] in (200, 404):
                base = first.get("final") or src.url
                cands = feed_links(first.get("body"), base) if first["status"] == 200 else []
                if not cands:  # ask the home page what feed it advertises
                    home = f"{urlsplit(src.url).scheme}://{urlsplit(src.url).netloc}/"
                    if home.rstrip("/") != src.url.rstrip("/"):
                        h = probe(fetcher, home, override)
                        tried.append(h)
                        cands = feed_links(h.get("body"), h.get("final") or home)
                for cand in cands[:2]:
                    host = urlsplit(cand).netloc.lower()
                    tried.append(probe(fetcher, cand, override if host == override else None))
            best = next((t for t in tried if t.get("entries")), None)
            rows.append((name, bool(src.crawl_permission), first, best, tried))

    lines = ["## Source probe", "",
             "| Source | Permission | Configured URL answers | Working feed found |",
             "|---|---|---|---|"]
    for name, perm, first, best, tried in rows:
        found = f"{best['url']} ({best['entries']} items)" if best and best is not first else (
            "configured URL works" if best else "none")
        lines.append(f"| {name} | {'yes' if perm else 'no'} | {verdict(first)} | {found} |")
    for n in unknown:
        lines.append(f"| {n} | ? | not in sources.yaml | — |")
    lines += ["", "<details><summary>every request</summary>", "",
              "| Source | URL | Status | Final URL | Bytes | Verdict |", "|---|---|---|---|---|---|"]
    for name, _perm, _first, _best, tried in rows:
        for t in tried:
            lines.append(f"| {name} | {t['url']} | {t['status']} | {t.get('final', '')} | "
                         f"{t.get('bytes', 0)} | {verdict(t)} |")
    lines += ["", "</details>", ""]
    report = "\n".join(lines)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
