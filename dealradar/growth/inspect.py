"""Ask Google whether our key pages are indexed (Search Console URL Inspection).

Operator 2026-10-16: "wowpriced.com doesn't come up when you search google ...
THESE ARE THINGS YOU SHOULD BE CHECKING." Read-only; prints one line per URL
(public facts about our own public pages) and flags the home page when Google
has not indexed it or chose another canonical.
"""

from __future__ import annotations

import os
import sys

import httpx

from ..config import load_config
from . import google

KEY_PAGES = ("/", "/about", "/how-it-works", "/best-deals-today/", "/join")


def lines(results: list[dict], home: str) -> list[str]:
    out = []
    for r in results:
        out.append(" | ".join([r["url"], r["verdict"] or "?", r["coverageState"] or "?",
                               "google canonical " + (r["googleCanonical"] or "none"),
                               "last crawl " + (r["lastCrawlTime"] or "never"),
                               r["pageFetchState"] or "?", r["robotsTxtState"] or "?",
                               r["indexingState"] or "?"]))
    first = next((r for r in results if r["url"] == home), None)
    if first and (first["verdict"] != "PASS" or first["googleCanonical"] not in ("", home)):
        out.append(f"HOME PAGE NOT INDEXED AS ITSELF: {first['coverageState'] or first['verdict']}")
    return out


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    base = "https://" + (cfg.custom_domain or "wowpriced.com").strip("/")
    urls = [base + p for p in KEY_PAGES] + list(argv if argv is not None else sys.argv[1:])
    sa = os.environ.get("GOOGLE_SA_JSON", "")
    if not sa or not cfg.growth_search_console_site:
        print("url inspection: skipped (GOOGLE_SA_JSON or growth.search_console_site missing)")
        return 0
    with httpx.Client(timeout=30) as client:
        token = google.access_token(client, sa)
        results = []
        for url in urls:
            try:
                results.append(google.inspect_url(client, token, cfg.growth_search_console_site, url))
            except httpx.HTTPError as exc:
                print(f"{url} | error {type(exc).__name__}")
    for line in lines(results, base + "/"):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
