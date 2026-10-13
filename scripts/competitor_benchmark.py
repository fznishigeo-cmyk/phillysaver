"""Weekly benchmark: wowpriced.com vs the deal sites we compete with.

Google's PageSpeed Insights API runs Lighthouse (mobile) on each home page,
so Google loads the page once, not our crawler. Records performance,
accessibility, best-practices and SEO scores plus real-user Core Web Vitals
when Google has them, and prints a Markdown table ranked by performance with
wowpriced.com's position. Read-only; one request every few seconds.

Usage: python scripts/competitor_benchmark.py   (env PSI_API_KEY optional)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dealradar.config import load_sources  # noqa: E402

PSI = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
OURS = "https://wowpriced.com/"
EXTRA = ["https://slickdeals.net/", "https://www.dealnews.com/", "https://www.bensbargains.com/",
         "https://www.retailmenot.com/", "https://dealsea.com/", "https://www.techbargains.com/"]
CATEGORIES = ("performance", "accessibility", "best-practices", "seo")


def targets() -> list[str]:
    primary, backup = load_sources()
    urls = {OURS}
    for s in primary + backup:
        if s.crawl_permission and (s.site or "").startswith("https://"):
            parts = urlsplit(s.site)
            urls.add(f"https://{parts.netloc}/")
    urls.update(EXTRA)
    return sorted(urls, key=lambda u: (u != OURS, u))


def score(client: httpx.Client, url: str) -> dict:
    params = [("url", url), ("strategy", "mobile")] + [("category", c) for c in CATEGORIES]
    key = os.environ.get("PSI_API_KEY", "").strip()
    if key:
        params.append(("key", key))
    try:
        r = client.get(PSI, params=params, timeout=120)
    except httpx.HTTPError as e:
        return {"url": url, "error": type(e).__name__}
    if r.status_code != 200:
        return {"url": url, "error": f"HTTP {r.status_code}"}
    data = r.json()
    cats = data.get("lighthouseResult", {}).get("categories", {})
    field = data.get("loadingExperience", {}).get("metrics", {})
    row = {"url": url, **{c: round(100 * (cats.get(c, {}).get("score") or 0)) for c in CATEGORIES}}
    lcp = field.get("LARGEST_CONTENTFUL_PAINT_MS", {}).get("percentile")
    row["field_lcp_s"] = round(lcp / 1000, 1) if lcp else None
    return row


def main() -> int:
    rows = []
    with httpx.Client(headers={"User-Agent": "wowpriced-benchmark (hello@wowpriced.com)"}) as client:
        for url in targets():
            rows.append(score(client, url))
            time.sleep(3)
    good = [r for r in rows if "error" not in r]
    good.sort(key=lambda r: (-r["performance"], -r["accessibility"]))
    rank = next((i + 1 for i, r in enumerate(good) if r["url"] == OURS), None)
    lines = ["## Competitor benchmark (Google PageSpeed Insights, mobile)", "",
             f"wowpriced.com ranks **{rank} of {len(good)}** on mobile performance." if rank else
             "wowpriced.com could not be scored this run.", "",
             "| # | Site | Perf | A11y | Best pr. | SEO | Real-user LCP (s) |", "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(good, 1):
        name = urlsplit(r["url"]).netloc + (" **(us)**" if r["url"] == OURS else "")
        lines.append(f"| {i} | {name} | {r['performance']} | {r['accessibility']} | "
                     f"{r['best-practices']} | {r['seo']} | {r['field_lcp_s'] or 'n/a'} |")
    failed = [r for r in rows if "error" in r]
    if failed:
        lines += ["", "Not scored: " + ", ".join(f"{urlsplit(r['url']).netloc} ({r['error']})" for r in failed)]
    report = "\n".join(lines) + "\n"
    print(report)
    Path("benchmark.json").write_text(json.dumps(rows, indent=1))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
