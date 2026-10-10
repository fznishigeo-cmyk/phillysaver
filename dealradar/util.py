"""Shared helpers: defensive date parsing, HTML->text, item normalization, time."""

from __future__ import annotations

import calendar
import html as html_lib
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

#: Hosts whose ToS pin us to their OFFICIAL feeds: code-level policy, not
#: configuration. No HTML page of these hosts is ever fetched — no article
#: enrichment, no feed autodiscovery, no probing — regardless of what
#: sources.yaml says.
OFFICIAL_FEED_ONLY_HOSTS: tuple[str, ...] = ("slickdeals.net",)


def official_feed_only(url: str) -> bool:
    """True when `url` is on a host where only official feeds may be fetched."""
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h)
               for h in OFFICIAL_FEED_ONLY_HOSTS)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else ""


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_feed_date(value, source_tz: str, now: datetime) -> datetime | None:
    """Defensive feed-date parsing.

    Accepts a feedparser struct_time, an ISO/RFC string, or None.
    Missing tz -> interpreted in the source's configured tz.
    Future dates -> clamped to `now`. Unparseable -> None.
    """
    dt: datetime | None = None
    tzinfo = ZoneInfo(source_tz)

    if value is None:
        return None
    if hasattr(value, "tm_year"):  # time.struct_time from feedparser (already UTC)
        try:
            dt = datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
        except (OverflowError, ValueError, OSError):
            return None
    elif isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        s = value.strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(s)
            except (TypeError, ValueError):
                return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tzinfo)
    dt = dt.astimezone(timezone.utc)
    if dt > now:
        dt = now
    return dt


def end_of_day(tz_name: str, ref: datetime) -> datetime:
    """'ends today' -> end of day in the source's tz, returned as UTC."""
    local = ref.astimezone(ZoneInfo(tz_name))
    eod = local.replace(hour=23, minute=59, second=59, microsecond=0)
    return eod.astimezone(timezone.utc)


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "iframe"}
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
              "section", "article", "ul", "ol", "table", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")
        elif tag == "a" and not self._skip_depth:
            # Keep link targets: deal posts carry the PRODUCT URL in their
            # anchors, and dropping it leaves deals with nothing to link to.
            href = next((v for k, v in attrs if k == "href"), "") or ""
            href = href.strip()
            self._hrefs.append(
                href if href.startswith(("http://", "https://")) else "")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")
        elif tag == "a" and self._hrefs:
            href = self._hrefs.pop()
            if href:
                self.parts.append(f" ({href})")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    """Strip tags/scripts, unescape entities, collapse whitespace (newlines kept)."""
    if not markup:
        return ""
    if "<" not in markup:
        text = html_lib.unescape(markup)
    else:
        p = _TextExtractor()
        try:
            p.feed(markup)
            p.close()
        except Exception:
            text = re.sub(r"<[^>]+>", " ", markup)
        else:
            text = "".join(p.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


_SIZE_COLOR_TOKENS = re.compile(
    r"\b(\d+(\.\d+)?\s?(oz|ounce|lb|lbs|ct|count|pack|pk|pc|pcs|ml|l|g|kg|inch|in|ft|\"|')"
    r"|xxs|xs|s|m|l|xl|xxl|xxxl|small|medium|large"
    r"|black|white|red|blue|green|yellow|pink|purple|gray|grey|silver|gold|navy|beige|brown|teal)\b",
    re.IGNORECASE,
)


def normalize_item(name: str) -> str:
    """Lowercase, strip sizes/colors/punctuation, token-sort. Dedupe key material."""
    s = name.lower()
    s = _SIZE_COLOR_TOKENS.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    tokens = sorted(t for t in s.split() if t)
    return " ".join(tokens)


def hours_between(a: datetime, b: datetime) -> float:
    return abs((a - b).total_seconds()) / 3600.0


def clamp_window_24h(first_seen: datetime, now: datetime) -> bool:
    """True if first_seen is within the last 24 h."""
    return now - first_seen <= timedelta(hours=24)
