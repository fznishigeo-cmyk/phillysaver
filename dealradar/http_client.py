"""Polite HTTP client: robots.txt, per-host rate limiting, retries, conditional GETs.

Politeness contract (config.yaml -> fetch):
- ``cfg.resolved_user_agent()`` is sent on every request, robots.txt included.
- robots.txt is fetched at most once per host per fetcher lifetime and cached
  in memory. HTTP 4xx for robots.txt => everything allowed; 5xx or a transport
  error => the host is treated as DISALLOWED for this run (conservative).
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

import logging
import random
import re
import time
import urllib.robotparser
from collections.abc import Callable, Mapping
from dataclasses import replace
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from .config import AppConfig
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
        self._client = httpx.Client(
            transport=transport,
            follow_redirects=False,
            timeout=httpx.Timeout(cfg.request_timeout_s),
            headers={"User-Agent": cfg.resolved_user_agent()},
        )
        # host -> RobotFileParser, or True (allow all) / False (disallow all)
        self._robots: dict[str, urllib.robotparser.RobotFileParser | bool] = {}
        self._last_request_at: dict[str, float] = {}

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
            logger.info("robots.txt disallows GET %s", url)
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
    ) -> FetchResult:
        """Polite HEAD.

        HEAD requests deliberately SKIP the robots.txt check: a HEAD transfers
        response metadata only (status + headers, never page content), so it
        does not crawl anything robots.txt protects. Robots rules are enforced
        on any GET that follows (see :func:`resolve_redirects`). Rate limits,
        retries, the User-Agent and the deadline all still apply.
        """
        result, _ = self._request_with_retries(
            "HEAD", url, dict(headers or {}), deadline=deadline,
            conditional=False, follow_redirects=True, read_body=False,
        )
        return result

    def close(self) -> None:
        self._client.close()

    # ---------------------------------------------------------------- robots

    def _robots_allowed(self, url: str) -> bool:
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
        return entry.can_fetch(self.cfg.resolved_user_agent(), url)

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
            )
        except Exception as exc:  # transport error -> conservative disallow
            logger.warning(
                "robots.txt fetch failed for %s (%s: %s); host disallowed this run",
                netloc, type(exc).__name__, exc,
            )
            return False
        if 200 <= resp.status_code < 300:
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(resp.text.splitlines())
            return parser
        if 400 <= resp.status_code < 500:
            logger.debug("robots.txt HTTP %d for %s; everything allowed",
                         resp.status_code, netloc)
            return True
        logger.warning("robots.txt HTTP %d for %s; host disallowed this run",
                       resp.status_code, netloc)
        return False

    # ------------------------------------------------------- politeness/time

    def _rate_wait(self, host: str) -> None:
        """Enforce at most one request per cfg.min_host_interval_s per host."""
        interval = float(self.cfg.min_host_interval_s)
        last = self._last_request_at.get(host)
        if last is not None and interval > 0:
            wait = interval - (self._clock() - last)
            if wait > 0:
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
    ) -> tuple[FetchResult, httpx.Headers | None]:
        """Polite manual hop loop around the per-URL attempt loop.

        With ``follow_redirects`` true, 3xx responses are followed MANUALLY up
        to REDIRECT_HOP_CAP hops: each GET hop target is checked against its
        own host's robots.txt and every hop passes through the per-host rate
        limit (httpx's internal following would bypass both). Conditional
        validators are keyed on — and stored for — the ORIGINAL url; If-*
        headers are not resent past the first hop.
        """
        current = url
        hdrs = dict(headers)
        hop_cap = REDIRECT_HOP_CAP if follow_redirects else 0
        for hop in range(hop_cap + 1):
            result, resp_headers = self._attempt_url(
                method, current, hdrs, deadline=deadline, read_body=read_body)
            status = result.status
            location = (resp_headers.get("Location", "")
                        if resp_headers is not None else "")
            if (follow_redirects and 300 <= status < 400 and location
                    and hop < hop_cap):
                nxt = urljoin(current, location)
                if (method == "GET"
                        and not self._host_overridden(nxt, robots_override_host)
                        and not self._robots_allowed(nxt)):
                    logger.info("robots.txt disallows redirect target %s", nxt)
                    return (FetchResult(url=url, final_url=nxt, status=0,
                                        body=None, error="robots_disallowed",
                                        blocked_by_robots=True), resp_headers)
                current = nxt
                hdrs = {k: v for k, v in hdrs.items() if k.lower() not in
                        ("if-none-match", "if-modified-since")}
                continue
            if (method == "GET" and conditional and 200 <= status < 300
                    and resp_headers is not None):
                etag = resp_headers.get("ETag", "")
                last_modified = resp_headers.get("Last-Modified", "")
                if etag or last_modified:
                    self.db.store_validators(url, etag, last_modified,
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
    ) -> tuple[FetchResult, httpx.Headers | None]:
        """Run the retry loop for ONE url (no redirect following)."""
        host = urlsplit(url).netloc.lower()
        attempts = max(1, int(self.cfg.max_retries))
        for attempt in range(attempts):
            self._check_deadline(deadline)
            self._rate_wait(host)
            try:
                resp, body = self._send(method, url, headers,
                                        read_body=read_body)
            except httpx.HTTPError as exc:
                error = f"{type(exc).__name__}: {exc}".strip().rstrip(":")
                logger.warning("%s %s failed (attempt %d/%d): %s",
                               method, url, attempt + 1, attempts, error)
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
                logger.warning("%s %s -> HTTP %d (attempt %d/%d)",
                               method, url, status, attempt + 1, attempts)
                if attempt + 1 < attempts:
                    self._backoff(attempt, resp.headers.get("Retry-After"),
                                  deadline)
                    continue
                return (FetchResult(url=url, final_url=str(resp.url),
                                    status=status, body=None,
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
    ) -> tuple[httpx.Response, bytes | None]:
        req_headers = dict(headers)
        req_headers["User-Agent"] = self.cfg.resolved_user_agent()
        with self._client.stream(method, url, headers=req_headers,
                                 follow_redirects=False) as resp:
            body: bytes | None = None
            if (read_body and method != "HEAD"
                    and resp.status_code not in (204, 304)):
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= MAX_BODY_BYTES:
                        logger.info("body for %s capped at %d bytes",
                                    url, MAX_BODY_BYTES)
                        break
                body = b"".join(chunks)[:MAX_BODY_BYTES]
            return resp, body

    # ---------------------------------------------------------- redirect hops

    def _redirect_hop(
        self, url: str, deadline: float | None
    ) -> tuple[FetchResult, str]:
        """One redirect hop: HEAD first, on 403/405/501 fall back to GET.

        The GET honors robots.txt. Returns (FetchResult, Location header).
        """
        result, resp_headers = self._request_with_retries(
            "HEAD", url, {}, deadline=deadline, conditional=False,
            follow_redirects=False, read_body=False,
        )
        if result.status in _HEAD_FALLBACK_STATUSES:
            self._check_deadline(deadline)
            if not self._robots_allowed(url):
                logger.info("robots.txt disallows GET fallback for %s", url)
                return (FetchResult(url=url, final_url=url, status=0,
                                    body=None, error="robots_disallowed",
                                    blocked_by_robots=True), "")
            result, resp_headers = self._request_with_retries(
                "GET", url, {}, deadline=deadline, conditional=False,
                follow_redirects=False, read_body=False,
            )
        location = ""
        if resp_headers is not None:
            location = resp_headers.get("Location", "")
        return result, location


def resolve_redirects(
    fetcher: PoliteFetcher,
    url: str,
    max_hops: int = 5,
    *,
    deadline: float | None = None,
) -> str:
    """Resolve an affiliate/shortener redirect chain to a canonical URL.

    Manual hop loop (``follow_redirects=False``): per hop a HEAD is tried
    first; on 403/405/501 it falls back to a GET, which honors robots.txt
    (a robots-blocked GET ends the loop). Relative Location headers are
    resolved against the current URL. The loop stops at the first non-3xx
    response, when an ASIN is already extractable from the current URL (no
    need to fetch retailer pages we can already canonicalize), or after
    ``max_hops`` hops (call sites pass ``cfg.redirect_max_hops``).

    Rate limits and the optional ``deadline`` apply to every hop; crossing the
    deadline raises :class:`DeadlineExceeded`. Any other error returns
    ``canonicalize_url`` of the last known URL.
    """
    current = url
    try:
        for _hop in range(max_hops):
            if extract_asin(current):
                break
            result, location = fetcher._redirect_hop(current, deadline)
            if result.blocked_by_robots:
                break
            if not (300 <= result.status < 400) or not location:
                break
            current = urljoin(current, location)
    except DeadlineExceeded:
        raise
    except Exception:
        logger.exception("resolve_redirects(%s) failed; returning last known URL",
                         url)
    return canonicalize_url(current)
