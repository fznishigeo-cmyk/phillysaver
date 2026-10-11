"""Woot source adapter: the official Woot developer API (developer.woot.com).

Requires the ``WOOT_API_KEY`` secret (reported upstream as ``needs_key`` until
set). One GET of the ``/feed/All`` endpoint per run — the key's quota is
1,000 requests/day, so hourly + the 15-minute lane together use ~120 (12%).
Prices are written into the post text verbatim (``Sale price: $49.99`` /
``List price: $199.99``) so the evidence-quote check can match them exactly;
Woot's ``Condition`` field is quoted too (Woot sells a lot of refurbs, which
must carry the used/open-box floor, never pass as new).

The All feed holds thousands of offers, mostly shallow or unpriced, so every
offer is scored from its structured prices before mapping: only offers whose
discount off their own list price comes within 5 points of their publish
floor are mapped, used/refurb/open-box first, deepest first (a selection
prefilter only: savings for publishing are still computed in savings.py).
At most ``woot.max_new_items_per_run`` of them are posts the pipeline has
not seen yet (each costs an LLM parse and a critic call); the rest of the
selection arrives over the next runs.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import httpx

from .. import util
from ..config import AppConfig, get_secret
from ..db import DB
from ..errors import AdapterNotConfigured, DeadlineExceeded
from ..models import RawPost, SourceConfig
from .base import SourceAdapter

log = logging.getLogger("dealradar.fetch.woot")

FEED_URL = "https://developer.woot.com/feed/All"

#: Selection ingests offers this many points under their publish floor
#: (near-misses still feed price history), like the Keepa lanes do.
FLOOR_SLACK_PCT = 5.0

#: A [min, max] price range wider than this (max/min) has no single price
#: to score: "from $9.99 to $49.99" is a menu, not a deal.
MAX_RANGE_RATIO = 1.10

#: Condition words that make an offer used for the floor (Woot's own labels:
#: "Refurbished", "Manufacturer Refurbished", "Used - Like New", "Open Box",
#: "Grade A", "New - Floor Model", "New - Damaged Box", ...). Anything not
#: plainly "New" counts as used as well, an unlabeled offer included.
_USED_RE = re.compile(
    r"refurb|recondition|renewed|\bused\b|open[\s-]*box|pre[\s-]*owned|"
    r"\bgrade\b|like[\s-]*new|floor[\s-]*model|display|damaged", re.I)
_NEW_RE = re.compile(r"(brand[\s-]+)?new\b", re.I)

#: Said in every used post, in the words the extractor tags as used: Woot's
#: own labels ("Grade B", "New - Floor Model", none at all) may not read so,
#: and an untagged post would publish against the lower new floor.
USED_MARKER = ("Counted as used/refurbished: Woot does not list it as "
               "plainly new.")


def _is_used(condition: str) -> bool:
    c = condition.strip()
    return not c or bool(_USED_RE.search(c)) or not _NEW_RE.match(c)


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip().lstrip("$").replace(",", ""))
        except ValueError:
            return None
    return None


def _single_price(value: Any, *, high: bool) -> float | None:
    """One price from Woot's shapes (number, string, [min, max] list or
    ``{"Minimum", "Maximum"}``): the high end for a sale price, the low end
    for a list price (the conservative discount). None when missing, zero,
    or a range whose ends differ by more than 10%."""
    if isinstance(value, dict):
        vals = [value.get("Minimum"), value.get("Maximum")]
    elif isinstance(value, (list, tuple)):
        vals = list(value)
    else:
        vals = [value]
    nums = [n for n in (_num(v) for v in vals) if n is not None]
    if not nums or min(nums) <= 0:
        return None
    lo, hi = min(nums), max(nums)
    if hi > lo * MAX_RANGE_RATIO:
        return None
    return hi if high else lo


def _score(item: dict, cfg: AppConfig) -> tuple[float, bool] | None:
    """(percent off list, is_used) for an offer worth mapping, else None."""
    sale = _single_price(item.get("SalePrice"), high=True)
    list_price = _single_price(item.get("ListPrice"), high=False)
    if sale is None or list_price is None or sale >= list_price:
        return None  # no list price, or priced at/above it: no deal
    pct = 100.0 * (1.0 - sale / list_price)
    used = _is_used(str(item.get("Condition") or ""))
    floor = cfg.used_min_pct if used else cfg.min_pct
    if pct < floor - FLOOR_SLACK_PCT:
        return None
    return pct, used


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise DeadlineExceeded("woot: per-source deadline crossed")


def _fmt_money(value: Any) -> str:
    """Format an API price (number, string, [min, max] list, or Woot's
    ``{"Minimum": x, "Maximum": y}`` object) as ``$X.XX``."""
    if value is None:
        return ""
    if isinstance(value, dict):
        lo = _fmt_money(value.get("Minimum"))
        hi = _fmt_money(value.get("Maximum"))
        if lo and hi and lo != hi:
            return f"{lo} - {hi}"
        return lo or hi
    if isinstance(value, (list, tuple)):
        parts = [_fmt_money(v) for v in value]
        return " - ".join(p for p in parts if p)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"${value:,.2f}"
    s = str(value).strip()
    if not s:
        return ""
    return s if s.startswith("$") else f"${s}"


class WootAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http_client
        self._sleep = sleep_fn  # kept for interface symmetry (single call per run)

    @contextmanager
    def _client(self, cfg: AppConfig) -> Iterator[httpx.Client]:
        if self._http is not None:
            yield self._http
            return
        client = httpx.Client(
            headers={"User-Agent": cfg.resolved_user_agent()},
            timeout=cfg.request_timeout_s,
            follow_redirects=True,
        )
        try:
            yield client
        finally:
            client.close()

    def fetch_posts(
        self,
        source: SourceConfig,
        fetcher: Any,
        db: DB,
        cfg: AppConfig,
        now: datetime,
        deadline: float | None,
    ) -> list[RawPost]:
        # fetcher unused: this adapter manages its own authenticated client.
        # db (when given) tells which posts the pipeline has already seen.
        key = get_secret("WOOT_API_KEY")
        if not key:
            raise AdapterNotConfigured("woot api key missing")

        with self._client(cfg) as client:
            _check_deadline(deadline)
            # The key travels ONLY in the x-api-key header: query-string keys
            # end up inside httpx error messages and from there could leak
            # into recorded (published) source errors.
            resp = client.get(FEED_URL, headers={"x-api-key": key})
            resp.raise_for_status()
            payload = resp.json()

        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = (payload.get("Items") or payload.get("items")
                     or payload.get("Offers") or [])
        else:
            items = []

        # Score every live offer, then select used before new, deepest
        # first (feed order breaks ties), up to the per-run cap. Posts the
        # pipeline has seen ride free; at most max_new_items_per_run unseen
        # ones go out per run, the rest of the selection on later runs.
        scored: list[tuple[bool, float, int, dict]] = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict) or item.get("IsSoldOut"):
                continue
            ends = util.parse_iso(str(item.get("EndDate") or ""))
            if ends is not None and ends <= now:
                continue  # already over
            score = _score(item, cfg)
            if score is not None:
                pct, used = score
                scored.append((not used, -pct, idx, item))
        scored.sort(key=lambda s: s[:3])
        n_used = sum(1 for s in scored if not s[0])

        posts: list[RawPost] = []
        selected = unseen = held = 0
        for _new, _pct, _idx, item in scored:
            if selected >= cfg.woot_max_items_per_run:
                break
            post = self._map_item(item, source, now)
            if post is None:
                continue
            selected += 1
            if db is None or not db.post_is_known(post.content_hash):
                if unseen >= cfg.woot_max_new_items_per_run:
                    held += 1
                    continue
                unseen += 1
            posts.append(post)
        log.info("woot: %d raw offers -> %d scored (%d used, %d new) -> "
                 "%d mapped (%d unseen, %d more held for later runs)",
                 len(items), len(scored), n_used, len(scored) - n_used,
                 len(posts), unseen, held)
        return posts

    @staticmethod
    def _map_item(item: dict, source: SourceConfig,
                  now: datetime) -> RawPost | None:
        uid = str(item.get("OfferId") or item.get("Id") or "")
        title = str(item.get("Title") or "")
        if not uid or not title:
            return None
        lines = [title]
        site = str(item.get("Site") or "")
        if site:
            lines.append(f"Woot category: {site}.")
        sale = _fmt_money(item.get("SalePrice"))
        if sale:
            lines.append(f"Sale price: {sale}")
        list_price = _fmt_money(item.get("ListPrice"))
        if list_price:
            lines.append(f"List price: {list_price}")
        condition = str(item.get("Condition") or "")
        if condition:
            # Quoted so the extractor tags refurb/used honestly.
            lines.append(f"Condition: {condition}.")
        if _is_used(condition):
            lines.append(USED_MARKER)
        end_date = str(item.get("EndDate") or "")
        if end_date:
            lines.append(f"Ends: {end_date}")
        lines.append("Sold online at Woot.com (Amazon's official deals site).")
        photo = item.get("Photo")
        image = photo if isinstance(photo, str) \
            and photo.startswith(("http://", "https://")) else ""
        return RawPost(
            source_name=source.name,
            uid=uid,
            url=str(item.get("Url") or ""),
            title=title,
            text="\n".join(lines),
            published_at=util.parse_feed_date(item.get("StartDate"),
                                              source.tz, now),
            fetched_at=now,
            image_url=image,
        )
