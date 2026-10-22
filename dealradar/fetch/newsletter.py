"""Read the operator's private inbox, never a partner's web server.

Bodies and pending acknowledgements live in memory only. The pipeline calls
``acknowledge`` after a successful run; deferred parses remain in the inbox.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from .. import util
from ..config import AppConfig, get_secret, load_sources
from ..errors import AdapterNotConfigured, DeadlineExceeded
from ..models import RawPost, SourceConfig
from .base import SourceAdapter
from .pages import _Node, _Tree

_ID = re.compile(r"msg:[0-9a-f]{64}\Z")
_URL = re.compile(r"https?://[^\s<>\"']+")
_PRICE = re.compile(r"\$\s?\d|\d\s?%")
_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                   r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
UNKNOWN_SENDERS_META_KEY = "newsletter:unknown_senders"
# Which partners' newsletters actually arrive (operator, 2026-10-14: list the
# ones that never do, so they can be resubscribed). Partner IDs, counts, dates.
PARTNER_MAIL_META_KEY = "newsletter:partner_mail"
log = logging.getLogger(__name__)
_NAV = {"about", "contact", "privacy", "terms", "subscribe", "unsubscribe",
        "newsletter", "newsletters", "confirm", "preferences", "category",
        "tag", "author", "feed", "rss", "search", "page", "go", "out",
        "click", "track", "redirect", "link", "links", "recommends"}
# A plain word (optionally .php etc.); anything else may be an ID or token.
_WORD = re.compile(r"[a-z][a-z-]{0,15}(?:\.[a-z]{2,4})?\Z")


@lru_cache(maxsize=1)
def _suffix_rules() -> set[str]:
    # Bundled ICANN + private rules; no network or disk cache. License/source
    # are in public_suffix_list.LICENSE and the compressed snapshot's header.
    with gzip.open(Path(__file__).with_name("public_suffix_list.dat.gz"), "rt", encoding="utf-8") as f:
        return {line.strip().encode("idna").decode("ascii").lower()
                for line in f if line.strip() and not line.startswith("//")}


def registrable_domain(host: str) -> str:
    """PSL longest-match rule, including wildcard suffixes and exceptions."""
    try:
        host = host.removesuffix(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return ""
    if len(host) > 253 or not _HOST.fullmatch(host):
        return ""
    labels = host.split(".")
    rules, suffix_labels = _suffix_rules(), 1
    for i in range(len(labels)):
        suffix = ".".join(labels[i:])
        if "!" + suffix in rules:
            suffix_labels = len(labels) - i - 1
            break
        if suffix in rules:
            suffix_labels = max(suffix_labels, len(labels) - i)
        if i and "*." + suffix in rules:
            suffix_labels = max(suffix_labels, len(labels) - i + 1)
    return ".".join(labels[-suffix_labels - 1:]) if len(labels) > suffix_labels else ""


def match_sender(address: str, sources: list[SourceConfig]) -> SourceConfig | None:
    """One permission partner, or unknown. Paused web feeds still receive mail."""
    address = address.strip().lower()
    domain = registrable_domain(address.rsplit("@", 1)[-1]) if "@" in address else ""
    listed, by_domain = {}, {}
    for partner in sources:
        if not partner.crawl_permission or partner.method == "email":
            continue
        try:
            own = registrable_domain(urlsplit(partner.site or partner.url).hostname or "")
        except ValueError:
            own = ""
        if address and address in {s.strip().lower() for s in partner.newsletter_from}:
            listed[partner.name] = partner
        elif domain and domain == own:
            by_domain[partner.name] = partner
    # An address a partner lists is that partner's (two listing it: unknown).
    if listed:
        return next(iter(listed.values())) if len(listed) == 1 else None
    # Every domain match is one site, so one partner with several feeds
    # (Hip2Save, Southern Savers): its first entry, enabled ones first.
    ranked = sorted(by_domain.values(), key=lambda p: not p.enabled)
    return ranked[0] if ranked else None


def _tally(db, meta_key: str, field: str, messages: list[tuple[str, str]],
           now: datetime, *, first_seen: bool = False) -> None:
    """Counts and dates only; opaque delivery IDs make failed ack retries harmless."""
    if not messages:
        return
    state = json.loads(db.meta_get(meta_key) or json.dumps({field: {}, "messages": {}}))
    day = now.astimezone(timezone.utc).date().isoformat()
    cutoff = (now.astimezone(timezone.utc) - timedelta(days=7)).date().isoformat()
    seen = {key: date for key, date in state["messages"].items() if date >= cutoff}
    for key, name in messages:
        if key in seen:
            continue
        row = state[field].setdefault(name, {"count": 0, "last_seen": day})
        if first_seen:
            row.setdefault("first_seen", day)
        row["count"] += 1
        row["last_seen"] = max(day, row["last_seen"])
        seen[key] = day
    state["messages"] = seen
    db.meta_set(meta_key, json.dumps(state, sort_keys=True))


def _record_unknown_senders(db, messages: list[tuple[str, str]], now: datetime) -> None:
    _tally(db, UNKNOWN_SENDERS_META_KEY, "senders", messages, now)


def partner_mail(db) -> dict[str, dict]:
    """Partner ID -> {count, first_seen, last_seen} of newsletters received."""
    return json.loads(db.meta_get(PARTNER_MAIL_META_KEY) or '{"partners":{}}')["partners"]


def unknown_sender_counts(db) -> dict[str, int]:
    """Unmapped addresses and counts for private operator reports, never bodies."""
    state = json.loads(db.meta_get(UNKNOWN_SENDERS_META_KEY) or '{"senders":{}}')
    primary, backup = load_sources()
    return {address: row["count"] for address, row in sorted(state["senders"].items())
            if match_sender(address, primary + backup) is None}


def unknown_sender_text(counts: dict[str, int]) -> str:
    return "New newsletter senders to map: " + ", ".join(
        f"{address or '(missing address)'} ({count})" for address, count in counts.items())


def inbox_configured(cfg: AppConfig) -> bool:
    """Only HTTPS base URLs without credentials, query strings or fragments."""
    try:
        parts = urlsplit(cfg.newsletter_inbox_url)
        return bool(get_secret("NEWSLETTER_INBOX_TOKEN") and parts.scheme == "https"
                    and parts.hostname and not parts.username and not parts.password
                    and not parts.query and not parts.fragment and parts.path in ("", "/"))
    except ValueError:
        return False


def _post_url(link: str, source: SourceConfig) -> str:
    """Accept direct partner permalinks only; never follow or unwrap trackers."""
    try:
        url = urljoin(source.site or source.url, link.strip())
        parts = urlsplit(url)
        own = urlsplit(source.site or source.url)
        path = parts.path.lower().strip("/")
        segments = path.split("/")
        if (parts.scheme in ("http", "https") and not parts.username and not parts.password
                and (parts.hostname or "").removeprefix("www.") ==
                    (own.hostname or "").removeprefix("www.")
                and path and not any(s in _NAV or s.startswith("wp-") for s in segments)
                and path not in {"deals", "blog", "coupons", "bargains"}
                and not re.search(r"\.(?:png|jpe?g|gif|svg|css|js|xml)$", path)
                and not re.search(r"[\s<>\"'\\]", url)):
            return url  # query/fragment and the partner's credit stay as posted
    except ValueError:
        pass
    return ""


def _html_deals(markup: str, source: SourceConfig) -> list[tuple[str, str]]:
    root = _Tree(markup).root
    parents, urls, texts = {}, {}, {}

    def visit(node):
        found = set()
        if node.tag in {"script", "style", "noscript", "template", "iframe"}:
            urls[id(node)] = found
            texts[id(node)] = ""
            return found
        if node.tag == "a" and (url := _post_url(node.attrs.get("href") or "", source)):
            found.add(url)
        for child in node.children:
            if isinstance(child, _Node):
                parents[id(child)] = node
                found.update(visit(child))
        urls[id(node)] = found
        texts[id(node)] = " ".join(texts[id(c)] if isinstance(c, _Node) else c
                                   for c in node.children).strip()
        return found

    visit(root)
    blocks = {}
    for anchor in root.find(tag="a"):
        url = _post_url(anchor.attrs.get("href") or "", source)
        if not url or url in blocks or url not in urls.get(id(anchor), set()):
            continue
        node = anchor
        block = None
        while node is not None and urls[id(node)] == {url}:
            if _PRICE.search(texts[id(node)]):
                block = node
            node = parents.get(id(node))
        if block is not None:
            # Copy links verbatim so the extractor can read product URLs;
            # resolving them would contact the partners or their trackers.
            links = [a.attrs.get("href") or "" for a in block.find(tag="a") if id(a) in urls]
            links = [link for link in links if link.startswith(("https://", "http://"))]
            blocks.setdefault(url, texts[id(block)] + "\n" + "\n".join(dict.fromkeys(links)))
    return list(blocks.items())


def _text_deals(text: str, source: SourceConfig) -> list[tuple[str, str]]:
    links = {_post_url(m.group().rstrip(".,);]"), source) for m in _URL.finditer(text)} - {""}
    if len(links) == 1 and _PRICE.search(text):
        return [(next(iter(links)), text.strip())]
    blocks = []
    for block in re.split(r"\n\s*\n", text):
        found = {_post_url(m.group().rstrip(".,);]"), source) for m in _URL.finditer(block)} - {""}
        if len(found) == 1 and _PRICE.search(block):
            blocks.append((next(iter(found)), block.strip()))
    return blocks


def _link_shapes(message: dict) -> tuple[list[str], bool]:
    """Public-log diagnostics: "host/first-word" per link, IDs and tokens as "*";
    never a query, fragment, full URL, address or body text. Plus: any price?"""
    html, text = str(message.get("html") or ""), str(message.get("text") or "")
    if html:
        root = _Tree(html).root
        links, visible = [a.attrs.get("href") or "" for a in root.find(tag="a")], root.text()
    else:
        links, visible = [m.group().rstrip(".,);]") for m in _URL.finditer(text)], text
    shapes = []
    for link in links:
        try:
            parts = urlsplit(link.strip())
            host = (parts.hostname or "").removesuffix(".")
        except ValueError:
            continue
        if parts.scheme not in ("http", "https"):
            continue
        domain = registrable_domain(host)
        labels = host.removeprefix("www.").split(".")[:-domain.count(".") - 1]
        first = parts.path.strip("/").split("/")[0]
        shape = ".".join([s if _WORD.match(s) else "*" for s in labels] + [domain]) if domain else "?"
        shapes.append((shape + ("/" + (first if _WORD.match(first) else "*") if first else ""))[:80])
    return shapes, bool(_PRICE.search(visible) or _PRICE.search(text))


def email_posts(message: dict, source: SourceConfig, now: datetime) -> list[RawPost]:
    blocks = _html_deals(str(message.get("html") or ""), source)
    if not blocks:
        blocks = _text_deals(str(message.get("text") or ""), source)
    posts, seen = [], set()
    for url, text in blocks:
        norm = " ".join(text.split()).lower()
        uid = hashlib.sha256((url + "\n" + norm).encode()).hexdigest()
        if uid in seen:
            continue
        seen.add(uid)
        posts.append(RawPost(
            source_name=source.name, uid=uid, url=url,
            title=text.splitlines()[0][:300], text=text,
            published_at=util.parse_feed_date(message.get("date"), source.tz, now),
            fetched_at=now, via_email=True))
    return posts


class NewsletterAdapter(SourceAdapter):
    def __init__(self, *, http_client=None, sources=None, sleep_fn=time.sleep,
                 clock=time.monotonic):
        self._http = http_client
        self.sources = sources
        self._sleep, self._clock = sleep_fn, clock
        self._last_request = None
        self._fetcher = None
        self._loaded = False
        self._posts: list[RawPost] = []
        self._pending: dict[str, list[RawPost]] = {}

    def _check(self, deadline):
        if deadline is not None and self._clock() >= deadline:
            raise DeadlineExceeded("newsletter inbox deadline")

    def _request(self, method, path, cfg, deadline, *, params=None, body=None):
        if not inbox_configured(cfg):
            raise AdapterNotConfigured("newsletter inbox URL or token missing")
        client = self._http or httpx.Client(headers={"User-Agent": cfg.resolved_user_agent()},
                                           follow_redirects=False)
        try:
            for attempt in range(cfg.max_retries + 1):
                self._check(deadline)
                host = urlsplit(cfg.newsletter_inbox_url).hostname
                rate_times = getattr(self._fetcher, "_last_request_at", {})
                last = rate_times.get(host, self._last_request)
                if last is not None:
                    wait = max(1.0, cfg.min_host_interval_s) - (self._clock() - last)
                    if wait > 0:
                        if deadline is not None and self._clock() + wait >= deadline:
                            raise DeadlineExceeded("newsletter inbox deadline")
                        self._sleep(wait)
                self._check(deadline)
                self._last_request = self._clock()
                rate_times[host] = self._last_request
                timeout = cfg.request_timeout_s
                if deadline is not None:
                    timeout = min(timeout, deadline - self._clock())
                try:
                    # This is our authenticated administrative API, not a crawl.
                    # Never cache bodies in http_cache or follow a bearer redirect.
                    with client.stream(method, cfg.newsletter_inbox_url + path,
                                       headers={"Authorization": "Bearer " + get_secret("NEWSLETTER_INBOX_TOKEN"),
                                                "Cache-Control": "no-store"},
                                       params=params, json=body, timeout=timeout,
                                       follow_redirects=False) as response:
                        status = response.status_code
                        if status in (401, 403):
                            raise AdapterNotConfigured("newsletter inbox token rejected")
                        if status != 429 and status < 500:
                            if status != 200:
                                raise RuntimeError(f"newsletter inbox HTTP {status}")
                            content = bytearray()
                            for chunk in response.iter_bytes():
                                self._check(deadline)
                                if len(content) + len(chunk) > cfg.newsletter_inbox_max_response_bytes:
                                    raise RuntimeError("newsletter inbox response too large")
                                content.extend(chunk)
                            try:
                                return json.loads(content)
                            except (ValueError, UnicodeError):
                                raise RuntimeError("newsletter inbox invalid JSON") from None
                except httpx.TransportError:
                    status = 0
                if attempt == cfg.max_retries:
                    raise RuntimeError(f"newsletter inbox unavailable (HTTP {status})") from None
                delay = 2 ** attempt
                if deadline is not None and self._clock() + delay >= deadline:
                    raise DeadlineExceeded("newsletter inbox deadline")
                self._sleep(delay)
        finally:
            if self._http is None:
                client.close()

    def fetch_posts(self, source, fetcher, db, cfg, now, deadline):
        if not inbox_configured(cfg):
            raise AdapterNotConfigured("newsletter inbox URL or token missing")
        self._fetcher = fetcher
        if not self._loaded:
            self._posts.clear()
            self._pending.clear()
            configured = self.sources
            if configured is None:
                primary, backup = load_sources()
                configured = primary + backup
            cursor, messages = "", []
            for _ in range(cfg.newsletter_inbox_max_messages):
                payload = self._request("GET", "/inbox", cfg, deadline, params={
                    "since": util.iso(now - timedelta(days=7)), "cursor": cursor,
                    "limit": cfg.newsletter_inbox_max_messages - len(messages)})
                page = payload.get("messages") if isinstance(payload, dict) else None
                if not isinstance(page, list) or len(page) > cfg.newsletter_inbox_max_messages - len(messages):
                    raise RuntimeError("newsletter inbox invalid messages")
                messages.extend(page)
                next_cursor = payload.get("next_cursor") or ""
                if (not isinstance(next_cursor, str) or len(next_cursor) > 1024
                        or (next_cursor == cursor and cursor)):
                    raise RuntimeError("newsletter inbox invalid cursor")
                if not next_cursor or len(messages) >= cfg.newsletter_inbox_max_messages:
                    break
                cursor = next_cursor
            unknown, known, parsed = [], [], {}
            for message in messages:
                self._check(deadline)
                if not isinstance(message, dict) or not _ID.fullmatch(str(message.get("id") or "")):
                    raise RuntimeError("newsletter inbox invalid message id")
                address = parseaddr(str(message.get("from") or ""))[1].lower()
                partner = match_sender(address, configured)
                posts = email_posts(message, partner, now) if partner is not None else []
                if partner is None:
                    unknown.append((message["id"], address))
                else:
                    known.append((message["id"], partner.name))
                    # messages, with no posts, with a price, link shapes
                    tally = parsed.setdefault(partner.name, [0, 0, 0, Counter()])
                    tally[0] += 1
                    if not posts:
                        shapes, priced = _link_shapes(message)
                        tally[1] += 1
                        tally[2] += priced
                        tally[3].update(shapes)
                self._pending[message["id"]] = posts
                self._posts.extend(posts)
            _record_unknown_senders(db, unknown, now)
            _tally(db, PARTNER_MAIL_META_KEY, "partners", known, now, first_seen=True)
            received = partner_mail(db)
            log.info("partner newsletters: %d message(s) this pull (%d unknown sender); "
                     "%d partner(s) have sent mail: %s", len(messages), len(unknown),
                     len(received), ", ".join(f"{name} ({row['count']}, last {row['last_seen']})"
                                              for name, row in sorted(received.items())) or "none")
            for name, (total, empty, priced, shapes) in sorted(parsed.items()):
                if empty:
                    log.info("partner newsletter %s: %d/%d message(s) gave no posts, %d with a price; "
                             "link shapes: %s", name, empty, total, priced,
                             ", ".join(f"{shape} ({n})" for shape, n in shapes.most_common(8)) or "none")
            self._loaded = True
        return self._posts

    def acknowledge(self, db, cfg, deadline=None):
        """All parts must be durably handled; unknown senders are already counted."""
        ready = [key for key, posts in self._pending.items()
                 if all(db.post_is_known(post.content_hash) for post in posts)]
        acknowledged = 0
        for start in range(0, len(ready), 100):
            ids = ready[start:start + 100]
            response = self._request("POST", "/inbox/ack", cfg, deadline, body={"ids": ids})
            if not isinstance(response, dict) or response.get("acknowledged") != len(ids):
                raise RuntimeError("newsletter inbox invalid acknowledgement")
            for key in ids:
                del self._pending[key]
            acknowledged += len(ids)
        return acknowledged
