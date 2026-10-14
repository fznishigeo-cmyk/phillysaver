"""Polite HTTP client: robots.txt, per-host rate limiting, retries, conditional GETs.

Politeness contract (config.yaml -> fetch):
- ``cfg.resolved_user_agent()`` is sent on every request, robots.txt included.
- robots.txt is fetched at most once per host per fetcher lifetime and cached
  in memory. An absent robots.txt (404/410) allows requests; other errors
  disallow the host for this run (conservative).
- At most one request per ``cfg.min_host_interval_s`` per host across ALL
  requests, robots fetches included.
- Up to ``cfg.max_retries`` attempts on 429 / 5xx / transport errors with
  exponential backoff (1 s, 2 s, 4 s, ... with +-30% jitter); a numeric
  Retry-After header is honored, capped at 30 s. Any other 4xx is final.
- Response bodies are capped at 5 MB.
- Transport errors surface as ``FetchResult(status=0, error=...)``; httpx
  exceptions never escape to callers (only :class:`DeadlineExceeded` is raised,
  and only when the caller passed a deadline).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import random
import re
import time
import urllib.robotparser
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import replace
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from .config import AppConfig, error_summary
from .db import DB
from .errors import DeadlineExceeded
from .models import FetchResult
from .util import now_utc

logger = logging.getLogger("dealradar.http_client")

MAX_BODY_BYTES = 5 * 1024 * 1024  # hard cap on any response body we keep
ROBOTS_TIMEOUT_S = 10.0
RETRY_AFTER_CAP_S = 30.0

REDIRECT_HOP_CAP = 10  # safety cap on manually-followed hops for feed/page GETs

# HEAD not supported / not allowed -> retry the redirect hop with a GET.
_HEAD_FALLBACK_STATUSES = (403, 405, 501)

# Exact tracking parameter names dropped by canonicalize_url (compared
# case-insensitively; the spec lists both "linkid" and "linkId").
_TRACKING_EXACT = frozenset({
    "tag", "ref", "ref_", "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid",
    "affid", "aff_id", "afftrack", "cjevent", "clickid", "irclickid",
    "srsltid", "smid", "ascsubtag", "linkcode", "linkid", "camp", "creative",
    "creativeasin", "th", "psc", "spm", "share", "cmpid", "siteid",
    "s_kwcid", "dclid",
})

_AMAZON_HOST_RE = re.compile(r"(^|\.)amazon\.[a-z]{2,3}(\.[a-z]{2})?$")

_ASIN_RE = re.compile(
    r"/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})(?=[/?#]|$)",
    re.IGNORECASE,
)


def extract_asin(url: str) -> str:
    """Return the uppercase ASIN embedded in `url`, or '' when absent.

    Matches /dp/ASIN, /gp/product/ASIN, /gp/aw/d/ASIN and /product/ASIN where
    ASIN is exactly 10 characters of [A-Z0-9] (matched case-insensitively) —
    but only on amazon.* hosts: other retailers use 10-char alphanumeric slugs
    under /product/ that are not ASINs, and a bogus ASIN would corrupt dedupe
    keys and halt redirect resolution.
    """
    # Deal posts often write the link without a scheme ("amazon.com/dp/...");
    # urlsplit then sees no host at all, and the ASIN (hence Keepa checks and
    # the product photo) was lost.
    probe = url if "://" in url else "https://" + url.lstrip("/")
    host = (urlsplit(probe).hostname or "").lower()
    if not _AMAZON_HOST_RE.search(host):
        return ""
    m = _ASIN_RE.search(url)
    return m.group(1).upper() if m else ""


def _is_tracking_param(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith("utm_") or lowered in _TRACKING_EXACT


def canonicalize_url(url: str) -> str:
    """Normalize a product/deal URL for dedupe keys.

    Lowercases the host, drops the fragment, and drops tracking params
    (utm_* prefix plus a fixed list of exact names) while preserving the order
    of everything kept. Amazon URLs with an extractable ASIN collapse to
    ``https://www.amazon.com/dp/ASIN``.
    """
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if _AMAZON_HOST_RE.search(host):
        asin = extract_asin(url)
        if asin:
            return f"https://www.amazon.com/dp/{asin}"
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if not _is_tracking_param(k)]
    return urlunsplit(
        (parts.scheme, parts.netloc.lower(), parts.path, urlencode(kept), "")
    )


#: PerimeterX captcha pages, on any host.
_PX_CAPTCHA_RE = re.compile(r"px-captcha|perimeterx|/_?px/captcha", re.I)
#: The Walmart family's walls, by store: the refused path+query rides in
#: their ``url`` param as base64 (live: Walmart /blocked, Sam's Club
#: /are-you-human).
_BASE64_WALLS = {"walmart.com": "/blocked", "samsclub.com": "/are-you-human"}


def _base64_wall_store(host: str) -> str:
    """The Walmart-family store ``host`` belongs to, or ''."""
    return next((s for s in _BASE64_WALLS if host == s or host.endswith("." + s)), "")


def is_challenge_url(url: str) -> bool:
    """True for a retailer's bot wall: Walmart's /blocked, Sam's Club's
    /are-you-human, eBay's /splashui/challenge, a PerimeterX captcha page.
    Never followed nor fetched: a store answers a crawler's product request
    by redirecting there (live: 72 Walmart, 8 eBay and 1 Sam's Club cards
    linked shoppers to the wall)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path.lower()
    store = _base64_wall_store(host)
    if store and path.rstrip("/") == _BASE64_WALLS[store]:
        return True
    if path.startswith("/splashui/challenge"):
        return True
    return bool(_PX_CAPTCHA_RE.search(host + path))


def challenge_target(url: str) -> str:
    """The product URL a bot wall names (the page the store refused us),
    or '' when ``url`` is no wall or its parameter is malformed: Walmart's
    and Sam's Club's ``url`` param is the base64 path+query on the store's
    own www host, eBay's ``ru`` the URL itself (an eBay URL only)."""
    if not is_challenge_url(url):
        return ""
    parts = urlsplit(url)
    # unquote, not parse_qsl: a '+' of base64 is no space.
    params = {k: unquote(v) for k, _eq, v in
              (p.partition("=") for p in parts.query.split("&"))}
    target = ""
    store = _base64_wall_store((parts.hostname or "").lower())
    if store:
        raw = params.get("url", "")
        try:
            path = base64.b64decode(raw + "=" * (-len(raw) % 4),
                                    validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return ""
        if path.startswith("/") and not path.startswith("//"):
            target = f"https://www.{store}" + path
    else:
        ru = params.get("ru", "")
        try:
            host = (urlsplit(ru).hostname or "").lower()
        except ValueError:  # "https://[bad": never a crashed run
            return ""
        if ru.startswith("https://") and (host == "ebay.com"
                                          or host.endswith(".ebay.com")):
            target = ru
    if not target or "\\" in target \
            or any(ord(c) <= 0x20 or ord(c) == 0x7f for c in target) \
            or is_challenge_url(target):
        return ""
    return target


_TRACKER_PARAMS = {"cc.dealsofamerica.com": "url", "click.linksynergy.com": "murl"}


def tracker_target(url: str) -> str:
    """Decode a known tracker's HTTPS destination without making a request."""
    try:
        parts = urlsplit(url)
        param = _TRACKER_PARAMS.get((parts.hostname or "").lower())
        if not param:
            return ""
        params = {k: unquote(v) for k, _eq, v in
                  (p.partition("=") for p in parts.query.split("&"))}
        target = params.get(param, "")
        if not target.startswith("https://") or "\\" in target \
                or any(ord(c) <= 0x20 or ord(c) == 0x7f for c in target):
            return ""
        if not urlsplit(target).hostname or is_challenge_url(target):
            return ""
        return target
    except (ValueError, TypeError, AttributeError):
        return ""


#: Per-request timeout for a redirect-resolution hop (best effort).
REDIRECT_HOP_TIMEOUT_S = 6.0


class PoliteFetcher:
    """Robots-respecting, rate-limited, retrying HTTP fetcher.

    One instance is meant to live for one pipeline run: its robots.txt cache
    and per-host rate-limit state span the fetcher lifetime. ``transport`` is
    an optional httpx transport injected by tests and the offline fixture
    pipeline; ``sleep_fn``/``clock`` default to ``time.sleep``/
    ``time.monotonic`` and are injectable for fast offline tests.
    """

    def __init__(
        self,
        db: DB,
        cfg: AppConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.db = db
        self.cfg = cfg
        self._sleep = sleep_fn if sleep_fn is not None else time.sleep
        self._clock = clock if clock is not None else time.monotonic
        # Redirects are ALWAYS followed manually (see _request_with_retries)
        # so every hop target gets its own robots.txt check and per-host rate
        # limit — httpx's internal following would bypass both.
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            transport=transport,
            follow_redirects=False,
            timeout=httpx.Timeout(cfg.request_timeout_s),
            headers={"User-Agent": cfg.resolved_user_agent()},
        )
        # host -> RobotFileParser, or True (allow all) / False (disallow all)
        self._robots: dict[str, urllib.robotparser.RobotFileParser | bool] = {}
        self._last_request_at: dict[str, float] = {}
        self._api_bodies: dict[str, bytes] = {}

    # ------------------------------------------------------------- public API

    def get(
        self,
        url: str,
        *,
        conditional: bool = True,
        headers: Mapping[str, str] | None = None,
        deadline: float | None = None,
        robots_override_host: str | None = None,
    ) -> FetchResult:
        """Polite GET.

        Honors robots.txt (a disallowed URL returns a FetchResult with
        ``blocked_by_robots=True`` and no request is made). The ONE exception:
        ``robots_override_host`` names a host whose owner gave the operator
        WRITTEN permission to crawl (sources.yaml ``crawl_permission``, with
        the correspondence kept on file) — robots.txt is skipped for requests
        to exactly that host, and never for any other host a redirect may
        reach. Rate limits, backoff, UA and every other politeness rule still
        apply. When ``conditional`` is true, sends If-None-Match /
        If-Modified-Since from ``db.get_validators(url)``; a 304 comes back as
        ``FetchResult(status=304, not_modified=True, body=None)`` and a 200
        stores fresh validators. ``deadline`` is a ``time.monotonic()`` value;
        crossing it raises :class:`DeadlineExceeded`.
        """
        self._check_deadline(deadline)
        override = (robots_override_host or "").strip().lower() or None
        if not self._host_overridden(url, override) \
                and not self._robots_allowed(url):
            logger.info("robots.txt disallows GET")
            return FetchResult(
                url=url, final_url=url, status=0, body=None,
                error="robots_disallowed", blocked_by_robots=True,
            )
        hdrs = dict(headers or {})
        if conditional:
            etag, last_modified = self.db.get_validators(url)
            if etag:
                hdrs.setdefault("If-None-Match", etag)
            if last_modified:
                hdrs.setdefault("If-Modified-Since", last_modified)
        result, _ = self._request_with_retries(
            "GET", url, hdrs, deadline=deadline, conditional=conditional,
            follow_redirects=True, read_body=True,
            robots_override_host=override,
        )
        return result

    @staticmethod
    def _host_overridden(url: str, override_host: str | None) -> bool:
        if not override_host:
            return False
        return urlsplit(url).netloc.lower() == override_host

    def head(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        deadline: float | None = None,
        robots_override_host: str | None = None,
    ) -> FetchResult:
        """Polite HEAD, with the same host-only grant policy as GET."""
        result, _ = self._request_with_retries(
            "HEAD", url, dict(headers or {}), deadline=deadline,
            conditional=False, follow_redirects=True, read_body=False,
            robots_override_host=robots_override_host,
        )
        return result

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def api_get(self, url: str, *, params=None, headers=None, deadline=None,
                max_attempts=None, respect_robots: bool = True,
                conditional: bool = True, follow_redirects: bool = True) -> httpx.Response:
        """An API response through the same robots, pacing and retry policy.

        ``respect_robots=False`` is only for an official API we hold a key or
        token for (Keepa, Woot, Meh, Threads, Reddit): robots.txt governs
        crawling web pages, and those hosts' robots.txt disallow the very
        endpoints their API terms license to keyed clients. Pacing, retries,
        the User-Agent and credential-safe logging still apply.

        Validators persist under an opaque key; response bodies stay in memory.
        A fresh run re-fetches a 304 unconditionally when its body is unavailable.
        ``conditional=False`` disables both caches for fresh live-price readings;
        ``follow_redirects=False`` confines keyed requests to the requested API.
        """
        req = httpx.Request("GET", url, params=params, headers=headers)
        target = str(req.url)
        if not conditional:
            result, resp_headers = self._request_with_retries(
                "GET", target, dict(headers or {}), deadline=deadline,
                conditional=False, follow_redirects=follow_redirects, read_body=True,
                max_attempts=max_attempts, keep_error_body=True,
                respect_robots=respect_robots)
            return self._api_response(req, result, resp_headers)
        cache_key = "api:" + hashlib.sha256(target.encode()).hexdigest()
        hdrs = dict(headers or {})
        etag, modified = self.db.get_validators(cache_key)
        if etag:
            hdrs["If-None-Match"] = etag
        if modified:
            hdrs["If-Modified-Since"] = modified
        result, resp_headers = self._request_with_retries(
            "GET", target, hdrs, deadline=deadline, conditional=True,
            follow_redirects=follow_redirects, read_body=True, validator_key=cache_key,
            max_attempts=max_attempts, keep_error_body=True,
            respect_robots=respect_robots)
        if result.status == 304 and cache_key not in self._api_bodies:
            result, resp_headers = self._request_with_retries(
                "GET", target, dict(headers or {}), deadline=deadline,
                conditional=True, follow_redirects=follow_redirects, read_body=True,
                validator_key=cache_key, max_attempts=max_attempts,
                keep_error_body=True, respect_robots=respect_robots)
        if result.status == 304 and cache_key in self._api_bodies:
            result = replace(result, status=200, body=self._api_bodies[cache_key])
        elif 200 <= result.status < 300 and result.body is not None:
            self._api_bodies[cache_key] = result.body
        return self._api_response(req, result, resp_headers)

    def api_post(self, url: str, *, data=None, auth=None, headers=None,
                 deadline=None, respect_robots: bool = True,
                 max_attempts=None) -> httpx.Response:
        """OAuth token requests share the host gap (robots as in ``api_get``)."""
        req = httpx.Request("POST", url, data=data, headers=headers)
        hdrs = dict(req.headers)
        if auth is not None:
            encoded = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
            hdrs["Authorization"] = "Basic " + encoded
        result, resp_headers = self._request_with_retries(
            "POST", url, hdrs, deadline=deadline, conditional=False,
            follow_redirects=False, read_body=True, content=req.content,
            keep_error_body=True, respect_robots=respect_robots,
            max_attempts=max_attempts)
        return self._api_response(req, result, resp_headers)

    @staticmethod
    def _api_response(req, result, headers) -> httpx.Response:
        if result.blocked_by_robots:
            raise RuntimeError("robots_disallowed")
        if result.status == 0:
            raise httpx.RequestError(result.error, request=req)
        # The body was already decoded while streaming: a Content-Encoding
        # header left on the rebuilt response would decode it a second time
        # (live 2026-10-13: Keepa always answers gzip -> DecodingError).
        kept = [(k, v) for k, v in (headers or httpx.Headers()).multi_items()
                if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
        return httpx.Response(result.status, headers=kept,
                              content=result.body or b"", request=req)

    def check_image(self, url: str, *, deadline: float | None = None
                    ) -> tuple[FetchResult, str]:
        """Check image metadata without reading bytes, honoring robots on
        HEAD and GET and every redirect. Unsupported or inconclusive HEAD
        answers get a GET fallback; a refusal is never worked around."""
        if is_challenge_url(url):
            return (FetchResult(url=url, final_url=url, status=0, body=None,
                                error="challenge_page"), "")
        for method in ("HEAD", "GET"):
            result, headers = self._request_with_retries(
                method, url, {}, deadline=deadline, conditional=False,
                follow_redirects=True, read_body=False, respect_robots=True,
            )
            content_type = headers.get("Content-Type", "").strip() if headers else ""
            if method == "HEAD" and (result.status in (405, 501) or
                    (200 <= result.status < 300 and
                     not content_type.lower().startswith("image/"))):
                continue
            return result, content_type
        return result, content_type

    # ---------------------------------------------------------------- robots

    def _robots_allowed(self, url: str, user_agent: str | None = None) -> bool:
        parts = urlsplit(url)
        host = parts.netloc.lower()
        if not host:
            return True
        if host not in self._robots:
            self._robots[host] = self._load_robots(parts.scheme or "https",
                                                   parts.netloc)
        entry = self._robots[host]
        if isinstance(entry, bool):
            return entry
        return entry.can_fetch(user_agent or self.cfg.resolved_user_agent(), url)

    def _load_robots(
        self, scheme: str, netloc: str
    ) -> urllib.robotparser.RobotFileParser | bool:
        """Fetch and parse robots.txt for one host (no robots check on itself)."""
        robots_url = f"{scheme}://{netloc}/robots.txt"
        self._rate_wait(netloc.lower())
        try:
            resp = self._client.get(
                robots_url,
                timeout=ROBOTS_TIMEOUT_S,
                headers={"User-Agent": self.cfg.resolved_user_agent()},
                follow_redirects=False,
            )
        except Exception as exc:  # transport error -> conservative disallow
            logger.warning(
                "robots.txt fetch failed (%s); host disallowed this run",
                error_summary(exc),
            )
            return False
        if 200 <= resp.status_code < 300:
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(resp.text.splitlines())
            return parser
        if resp.status_code in (404, 410):
            logger.debug("robots.txt HTTP %d for %s; everything allowed",
                         resp.status_code, netloc)
            return True
        logger.warning("robots.txt HTTP %d for %s; host disallowed this run",
                       resp.status_code, netloc)
        return False

    # ------------------------------------------------------- politeness/time

    def _rate_wait(self, host: str, deadline: float | None = None) -> None:
        """Enforce at most one request per cfg.min_host_interval_s per host."""
        interval = float(self.cfg.min_host_interval_s)
        store = {"api.bestbuy.com": "bestbuy", "api.ebay.com": "ebay"}.get(host)
        if store:
            interval = max(interval, 1.0, 1.0 / max(
                0.01, getattr(self.cfg, store + "_requests_per_second")))
        last = self._last_request_at.get(host)
        if last is not None and interval > 0:
            wait = interval - (self._clock() - last)
            if wait > 0:
                self._check_deadline(deadline, upcoming_sleep=wait)
                self._sleep(wait)
        self._last_request_at[host] = self._clock()

    def _check_deadline(self, deadline: float | None,
                        upcoming_sleep: float = 0.0) -> None:
        if deadline is None:
            return
        now = self._clock()
        if upcoming_sleep > 0.0:
            if now + upcoming_sleep > deadline:
                raise DeadlineExceeded(
                    f"backoff sleep of {upcoming_sleep:.2f}s would cross the deadline"
                )
        elif now >= deadline:
            raise DeadlineExceeded("deadline exceeded before attempt")

    def _backoff(self, attempt_index: int, retry_after_header: str | None,
                 deadline: float | None) -> None:
        delay: float | None = None
        if retry_after_header:
            try:
                delay = min(max(float(retry_after_header), 0.0),
                            RETRY_AFTER_CAP_S)
            except ValueError:
                delay = None
        if delay is None:
            delay = (2.0 ** attempt_index) * random.uniform(0.7, 1.3)
        self._check_deadline(deadline, upcoming_sleep=delay)
        self._sleep(delay)

    # --------------------------------------------------------------- request

    def _request_with_retries(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        *,
        deadline: float | None,
        conditional: bool,
        follow_redirects: bool,
        read_body: bool,
        robots_override_host: str | None = None,
        max_attempts: int | None = None,
        timeout_s: float | None = None,
        respect_robots: bool = True,
        validator_key: str | None = None,
        content: bytes | None = None,
        keep_error_body: bool = False,
    ) -> tuple[FetchResult, httpx.Headers | None]:
        """Polite manual hop loop around the per-URL attempt loop.

        With ``follow_redirects`` true, 3xx responses are followed MANUALLY up
        to REDIRECT_HOP_CAP hops: each GET hop target is checked against its
        own host's robots.txt and every hop passes through the per-host rate
        limit (httpx's internal following would bypass both). A redirect
        to a bot wall (:func:`is_challenge_url`) is never followed: status
        0, error 'challenge_page'. Conditional validators are keyed on —
        and stored for — the ORIGINAL url; If-* headers are not resent past
        the first hop.
        """
        current = url
        hdrs = dict(headers)
        user_agent = self._user_agent(hdrs)
        hop_cap = REDIRECT_HOP_CAP if follow_redirects else 0
        for hop in range(hop_cap + 1):
            self._check_deadline(deadline)
            if is_challenge_url(current):
                return (FetchResult(url=url, final_url=current, status=0,
                                    body=None, error="challenge_page"), None)
            if (respect_robots
                    and not self._host_overridden(current, robots_override_host)
                    and not self._robots_allowed(current, user_agent)):
                return (FetchResult(url=url, final_url=current, status=0,
                                    body=None, error="robots_disallowed",
                                    blocked_by_robots=True), None)
            result, resp_headers = self._attempt_url(
                method, current, hdrs, deadline=deadline, read_body=read_body,
                max_attempts=max_attempts, timeout_s=timeout_s,
                content=content, keep_error_body=keep_error_body)
            status = result.status
            location = (resp_headers.get("Location", "")
                        if resp_headers is not None else "")
            if (follow_redirects and 300 <= status < 400 and location
                    and hop < hop_cap):
                nxt = urljoin(current, location)
                if is_challenge_url(nxt):
                    logger.info("redirect points to a bot wall; not followed")
                    return (FetchResult(url=url, final_url=nxt, status=0,
                                        body=None, error="challenge_page"),
                            resp_headers)
                if (respect_robots
                        and not self._host_overridden(nxt, robots_override_host)
                        and not self._robots_allowed(nxt, user_agent)):
                    logger.info("robots.txt disallows redirect target")
                    return (FetchResult(url=url, final_url=nxt, status=0,
                                        body=None, error="robots_disallowed",
                                        blocked_by_robots=True), resp_headers)
                if urlsplit(current).netloc.lower() != urlsplit(nxt).netloc.lower():
                    hdrs = {k: v for k, v in hdrs.items() if k.lower() not in
                            ("authorization", "x-api-key", "cookie")}
                current = nxt
                hdrs = {k: v for k, v in hdrs.items() if k.lower() not in
                        ("if-none-match", "if-modified-since")}
                continue
            if (method == "GET" and conditional and 200 <= status < 300
                    and resp_headers is not None):
                etag = resp_headers.get("ETag", "")
                last_modified = resp_headers.get("Last-Modified", "")
                if etag or last_modified:
                    self.db.store_validators(validator_key or url, etag, last_modified,
                                             now_utc())
            if current != url:
                result = replace(result, url=url)
            return (result, resp_headers)
        return (FetchResult(url=url, final_url=current, status=0, body=None,
                            error="too_many_redirects"), None)

    def _attempt_url(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        *,
        deadline: float | None,
        read_body: bool,
        max_attempts: int | None = None,
        timeout_s: float | None = None,
        content: bytes | None = None,
        keep_error_body: bool = False,
    ) -> tuple[FetchResult, httpx.Headers | None]:
        """Run the retry loop for ONE url (no redirect following)."""
        host = urlsplit(url).netloc.lower()
        attempts = max(1, int(max_attempts or self.cfg.max_retries))
        for attempt in range(attempts):
            self._check_deadline(deadline)
            self._rate_wait(host, deadline)
            self._check_deadline(deadline)
            try:
                resp, body = self._send(method, url, headers,
                                        read_body=read_body,
                                        timeout_s=timeout_s, content=content)
            except httpx.HTTPError as exc:
                error = error_summary(exc)
                logger.warning("%s failed (attempt %d/%d): %s",
                               method, attempt + 1, attempts, error)
                if attempt + 1 < attempts:
                    self._backoff(attempt, None, deadline)
                    continue
                return (FetchResult(url=url, final_url=url, status=0,
                                    body=None, error=error), None)
            status = resp.status_code
            if status == 304:
                return (FetchResult(url=url, final_url=str(resp.url),
                                    status=304, body=None, not_modified=True),
                        resp.headers)
            if status == 429 or 500 <= status <= 599:
                logger.warning("%s -> HTTP %d (attempt %d/%d)",
                               method, status, attempt + 1, attempts)
                if attempt + 1 < attempts:
                    self._backoff(attempt, resp.headers.get("Retry-After"),
                                  deadline)
                    continue
                return (FetchResult(url=url, final_url=str(resp.url),
                                    status=status, body=body if keep_error_body else None,
                                    error=f"http_{status}"), resp.headers)
            # Final response: 2xx, 3xx (caller decides), or a non-429 4xx.
            error = f"http_{status}" if status >= 400 else ""
            return (FetchResult(url=url, final_url=str(resp.url),
                                status=status, body=body, error=error),
                    resp.headers)
        raise AssertionError("unreachable: attempt loop always returns")

    def _send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        *,
        read_body: bool,
        timeout_s: float | None = None,
        content: bytes | None = None,
    ) -> tuple[httpx.Response, bytes | None]:
        req_headers = dict(headers)
        user_agent = self._user_agent(req_headers)
        req_headers = {k: v for k, v in req_headers.items() if k.lower() != "user-agent"}
        req_headers["User-Agent"] = user_agent
        extra = {"timeout": timeout_s} if timeout_s else {}
        with self._client.stream(method, url, headers=req_headers,
                                 content=content, follow_redirects=False, **extra) as resp:
            body: bytes | None = None
            if (read_body and method != "HEAD"
                    and resp.status_code not in (204, 304)):
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= MAX_BODY_BYTES:
                        logger.info("response body capped at %d bytes", MAX_BODY_BYTES)
                        break
                body = b"".join(chunks)[:MAX_BODY_BYTES]
            return resp, body

    def _user_agent(self, headers: Mapping[str, str]) -> str:
        ua = next((v for k, v in headers.items() if k.lower() == "user-agent"), "")
        if ua and self.cfg.contact_email not in ua:
            return ua + f" (contact: {self.cfg.contact_email})"
        return ua or self.cfg.resolved_user_agent()

    # ---------------------------------------------------------- redirect hops

    def _redirect_hop(
        self, url: str, deadline: float | None, *, respect_robots: bool = True
    ) -> tuple[FetchResult, str]:
        """One redirect hop: HEAD first, on 403/405/501 fall back to GET.

        The GET honors robots.txt. Returns (FetchResult, Location header).
        Resolution is best effort (the link as posted is kept on failure), so
        each hop gets ONE quick try: live wart, a store answering 429 with
        Retry-After 30 and another hanging 20 s per request ate 5.5 minutes
        of a 12-minute run in backoff. Not retrying a 429 is also politer.
        """
        quick = {"max_attempts": 1, "timeout_s": REDIRECT_HOP_TIMEOUT_S}
        result, resp_headers = self._request_with_retries(
            "HEAD", url, {}, deadline=deadline, conditional=False,
            follow_redirects=False, read_body=False, respect_robots=respect_robots, **quick,
        )
        if result.status in _HEAD_FALLBACK_STATUSES:
            self._check_deadline(deadline)
            if not self._robots_allowed(url):
                logger.info("robots.txt disallows GET fallback")
                return (FetchResult(url=url, final_url=url, status=0,
                                    body=None, error="robots_disallowed",
                                    blocked_by_robots=True), "")
            result, resp_headers = self._request_with_retries(
                "GET", url, {}, deadline=deadline, conditional=False,
                follow_redirects=False, read_body=False, **quick,
            )
        location = ""
        if resp_headers is not None:
            location = resp_headers.get("Location", "")
        return result, location


@contextmanager
def api_fetcher(fetcher, db, cfg, *, http_client=None, sleep_fn=None):
    """Use the run's polite client; standalone adapters get the same policy."""
    if fetcher is not None:
        yield fetcher
        return
    memory = DB(":memory:") if db is None else None
    client = PoliteFetcher(db if db is not None else memory, cfg,
                           http_client=http_client, sleep_fn=sleep_fn)
    try:
        yield client
    finally:
        client.close()
        if memory is not None:
            memory.close()


def resolve_redirects(
    fetcher: PoliteFetcher,
    url: str,
    max_hops: int = 5,
    *,
    deadline: float | None = None,
    respect_robots: bool = True,
) -> str:
    """Resolve an affiliate/shortener redirect chain to a canonical URL.

    Manual hop loop (``follow_redirects=False``): per hop a HEAD is tried
    first; on 403/405/501 it falls back to a GET, which honors robots.txt
    (a robots-blocked GET ends the loop). Relative Location headers are
    resolved against the current URL. The loop stops at the first non-3xx
    response, when an ASIN is already extractable from the current URL (no
    need to fetch retailer pages we can already canonicalize), before a
    bot wall (:func:`is_challenge_url`: the URL redirected from, the
    product the store itself named, is kept and the wall never fetched),
    or after ``max_hops`` hops (call sites pass ``cfg.redirect_max_hops``).

    Rate limits and the optional ``deadline`` apply to every hop; crossing the
    deadline raises :class:`DeadlineExceeded`. Any other error returns
    ``canonicalize_url`` of the last known URL. ``respect_robots`` also checks
    HEAD metadata requests, so tracker repairs never contact a disallowed URL.
    """
    current = url
    try:
        for _hop in range(max_hops):
            if extract_asin(current):
                break
            result, location = fetcher._redirect_hop(current, deadline,
                                                    respect_robots=respect_robots)
            if result.blocked_by_robots:
                break
            if not (300 <= result.status < 400) or not location:
                break
            nxt = urljoin(current, location)
            if is_challenge_url(nxt):
                logger.info("redirect points to a bot wall; kept as the product URL")
                break
            current = nxt
    except DeadlineExceeded:
        raise
    except Exception as exc:
        logger.warning("redirect resolution failed (%s); returning last known URL",
                       error_summary(exc))
    return canonicalize_url(current)
