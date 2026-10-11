"""Read-only check of product photo URLs (GitHub Actions; the dev sandbox
cannot reach image hosts). For each URL: one GET with our User-Agent and NO
Referer (exactly how the deal page asks: referrerpolicy="no-referrer"),
redirects followed, at most one request per second per host. Prints status,
content type and size, so a host that refuses hotlinked photos shows up.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dealradar.config import load_config  # noqa: E402


def main() -> int:
    urls = [u.strip() for u in os.environ.get("IMAGE_URLS", "").split() if u.strip()]
    ua = load_config().resolved_user_agent()
    last: dict[str, float] = {}
    lines = ["## Image probe", "", "| URL | Status | Type | Bytes |", "|---|---|---|---|"]
    with httpx.Client(follow_redirects=True, timeout=20,
                      headers={"User-Agent": ua, "Accept": "image/*"}) as client:
        for url in urls:
            host = urlsplit(url).netloc
            wait = 1.0 - (time.monotonic() - last.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            last[host] = time.monotonic()
            try:
                r = client.get(url)
                lines.append(f"| {url[:110]} | {r.status_code} | "
                             f"{r.headers.get('content-type', '')[:30]} | {len(r.content)} |")
            except Exception as e:  # a probe never fails the job
                lines.append(f"| {url[:110]} | error | {type(e).__name__} | 0 |")
    report = "\n".join(lines) + "\n"
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
