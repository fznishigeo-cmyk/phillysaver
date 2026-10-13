"""Public visitor checks; results belong only in the operator's report."""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from datetime import datetime
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from ..http_client import is_challenge_url
from ..publish.site import link_mix


class PublicClient:
    """Use the report client, with robots and rate limits on every redirect hop."""

    def __init__(self, client, cfg):
        self.client = client
        self.ua = cfg.resolved_user_agent()
        if cfg.contact_email and cfg.contact_email not in self.ua:
            self.ua += f" (contact: {cfg.contact_email})"
        self.interval = max(1.0, cfg.min_host_interval_s)
        self.last = {}
        self.robots = {}

    def _send(self, method, url, headers, read):
        host = urlsplit(url).hostname
        delay = self.interval - (time.monotonic() - self.last.get(host, -float('inf')))
        if delay > 0:
            time.sleep(delay)
        self.last[host] = time.monotonic()
        with self.client.stream(method, url, headers={**headers, "User-Agent": self.ua},
                                timeout=6, follow_redirects=False) as response:
            if read:
                response.read()
            return response

    def _allowed(self, url):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.robots:
            try:
                response = self._send("GET", origin + "/robots.txt", {}, True)
                if response.is_success:
                    parser = RobotFileParser()
                    parser.parse(response.text.splitlines())
                    self.robots[origin] = parser
                else:
                    self.robots[origin] = 400 <= response.status_code < 500
            except httpx.HTTPError:
                self.robots[origin] = False
        rule = self.robots[origin]
        return rule if isinstance(rule, bool) else rule.can_fetch(self.ua, url)

    def request(self, method, url, *, headers=None, read=False):
        for _ in range(11):
            if is_challenge_url(url) or (method == "GET" and not self._allowed(url)):
                raise PermissionError("Public request disallowed")
            for attempt in range(2):
                try:
                    response = self._send(method, url, headers or {}, read)
                except httpx.TransportError:
                    if attempt:
                        raise
                else:
                    if response.status_code != 429 and response.status_code < 500:
                        break
                    if attempt:
                        break
                time.sleep(2 ** attempt)
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            location = response.headers.get("location")
            if not location:
                return response
            url = urljoin(url, location)
        raise httpx.TooManyRedirects("Public redirect limit")


def check(client, cfg, now: datetime) -> dict:
    public = PublicClient(client, cfg)
    base = "https://" + cfg.custom_domain.strip().rstrip("/")
    response = public.request("GET", base + "/deals.json", read=True)
    response.raise_for_status()
    payload = response.json()
    live = [d for d in payload["deals"]
            if not d.get("locked") and d.get("alive", d.get("status") == "alive")]
    photos = [d for d in live if d.get("image_url")]
    missing = [d for d in live if not d.get("image_url")]
    stores = Counter(d.get("store") or "Unknown" for d in missing)
    sources = Counter(s for d in missing for s in
                      {c.get("name") for c in d.get("credits") or []} if s)
    groups = defaultdict(list)
    for deal in photos:
        group = groups[deal.get("store") or "Unknown"]
        if len(group) < 6:
            group.append(deal)
    # Round-robin gives stores a fair share even when more than ten qualify.
    sample = [group[i] for i in range(6) for group in groups.values() if i < len(group)][:60]
    # Broken means proof a visitor sees no photo: gone (404/410) or not an
    # image. A robots rule, a CDN refusing our checker (401/403), a rate
    # limit, a server error or a timeout is "unchecked", never broken.
    broken, unchecked = Counter(), 0
    for deal in sample:
        try:
            image = public.request("HEAD", deal["image_url"])
            if image.status_code in (403, 405):
                image = public.request("GET", str(image.url),
                                       headers={"Range": "bytes=0-1023"})
        except (httpx.HTTPError, PermissionError, ValueError):
            unchecked += 1
            continue
        is_image = image.headers.get("content-type", "").lower().startswith("image/")
        if image.status_code in (404, 410) or (image.is_success and not is_image):
            broken[deal.get("store") or "Unknown"] += 1
        elif not image.is_success:
            unchecked += 1
    meta = payload["meta"]
    age = (now - datetime.fromisoformat(meta["published_at"].replace("Z", "+00:00"))).total_seconds() / 60
    coverage = 100 * len(photos) / len(live) if live else 0.0
    warnings = []
    if coverage < 85:
        warnings.append("photo coverage under 85%")
    checked = len(sample) - unchecked
    if checked and sum(broken.values()) / checked > .05:
        warnings.append("broken photos over 5%")
    if age > 3 * meta["cadence_minutes"]:
        warnings.append("stale publish")
    try:
        home = public.request("GET", base + "/", read=True)
        home_status = home.status_code
        home_content = 'id="content"' in home.text
    except (httpx.HTTPError, PermissionError):
        home_status, home_content = 0, False
    if home_status != 200:
        warnings.append("home page not 200")
    if not home_content:
        warnings.append("home page missing content")
    return dict(live=len(live), photos=len(photos), coverage=coverage,
                link_mix=link_mix(live, cfg.affiliate_amazon_tag,
                                  competitor_tracker_hosts=cfg.affiliate_competitor_tracker_hosts),
                missing_stores=stores.most_common(10), missing_sources=sources.most_common(10),
                checked=len(sample) - unchecked, unchecked=unchecked,
                broken=sum(broken.values()), broken_stores=broken.most_common(),
                age_minutes=age, locked_count=meta.get("locked_count", 0),
                home_status=home_status, home_content=home_content, warnings=warnings)


def lines(health: dict) -> list[str]:
    def counts(items):
        return ", ".join(f"{name} {count:,}" for name, count in items) or "none"

    result = ["", "== Site health =="]
    if health["warnings"]:
        result.append("⚠ " + "; ".join(health["warnings"]))
    result += [f"Photos: {health['photos']:,} of {health['live']:,} live deals ({health['coverage']:.1f}%)",
               "Missing most at stores: " + counts(health["missing_stores"]),
               "Missing most at sources: " + counts(health["missing_sources"]),
               f"Broken: {health['broken']} of {health['checked']} checked ("
               + counts(health["broken_stores"]) + ")"
               + (f"; {health['unchecked']} could not be checked" if health.get("unchecked") else ""),
               f"Last publish: {health['age_minutes']:.0f} min ago; locked: {health['locked_count']:,}",
               f"Home page: HTTP {health['home_status']}; content "
               + ("present" if health["home_content"] else "missing")]
    return result
