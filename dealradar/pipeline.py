"""Run orchestration: fetch -> parse -> savings -> dedupe/verify -> publish.

Hard budgets: 20 s/request and 90 s/source (enforced in the fetch layer),
8 min/run (enforced here). Idempotency: content-hashed posts_seen, UNIQUE
alert tokens, UNIQUE price-history rows — re-running the same hour adds
nothing and re-sends nothing.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from itertools import chain, zip_longest
from typing import Any
from urllib.parse import urljoin, urlsplit

from . import rank, util
from .config import AppConfig, load_config, load_sources
from .db import DB
from .errors import DeadlineExceeded, ParserNotConfigured, SpendCapReached
from .models import RawPost, SourceConfig
from .verify import CONFIRM_GONE_H

log = logging.getLogger("dealradar.pipeline")

# Seconds reserved at the end of the run budget for publish (critic + price
# rechecks + site render) after parse stops.
_RESERVE_FOR_TAIL_S = 90.0
# Fetching (and its enrichment) stops this early so parse gets real time. The
# parse window is _RESERVE_FOR_PARSE_S - _RESERVE_FOR_TAIL_S; keep the gap wide.
# Live incident: a 60-source sweep ran to the wire, parse got seconds, and the
# back half of the source list never published a single deal.
_RESERVE_FOR_PARSE_S = 300.0


def _spend_markdown(spend_by_kind: dict, extract_by_source: dict) -> str:
    """Operator accounting only: source names and aggregate calls/costs."""
    lines = ["## LLM spend by kind", "", "| Kind | Calls | USD |",
             "|---|---:|---:|"]
    for kind, stats in sorted(spend_by_kind.items()):
        lines.append(f"| {kind} | {stats['calls']} | {stats['usd']:.6f} |")
    lines.extend(["", "## Extraction spend by source", "",
                  "| Source | Calls | USD |", "|---|---:|---:|"])
    for name, stats in sorted(extract_by_source.items()):
        name = name.replace("|", "\\|").replace("\n", " ").replace("\r", " ")
        lines.append(f"| {name} | {stats['calls']} | {stats['usd']:.6f} |")
    return "\n".join(lines) + "\n"


def _run_key(now: datetime, fast_lane: bool) -> str:
    if fast_lane:
        # One key per quarter-hour so the four fast runs in an hour each keep
        # their own run_stats row (idempotent re-runs still collide on it).
        q = (now.minute // 15) * 15
        return now.strftime("%Y-%m-%dT%H") + f":{q:02d}:fast"
    return now.strftime("%Y-%m-%dT%H")


def _load_effective_sources(db: DB, cfg: AppConfig, fetcher, now: datetime,
                            validate_if_missing: bool = True,
                            deadline: float | None = None) -> list[SourceConfig]:
    """Validated source set from state, else first-run live validation."""
    from .fetch.validate import (effective_sources, save_effective_sources,
                                 validate_sources, write_report)

    primary, backup = load_sources()
    stored = effective_sources(db, now=now)  # None when missing or >24 h old
    if stored:
        # Config edits take effect without waiting for the daily
        # revalidation, carefully: a stored source that is HEALTHY keeps its
        # stored entry (it may carry an autodiscovered feed URL better than
        # the config's guess); one that is FAILING swaps to the current
        # config entry, so a corrected URL or a new crawl_permission heals
        # it on the very next run. Names disabled in config drop out.
        by_name = {s.name: s for s in primary if s.enabled}
        failing = {
            row["source_name"] for row in db.all_source_health()
            if int(row.get("consecutive_failures") or 0) > 0
            or row.get("tripped_until")
        }
        refreshed: list[SourceConfig] = []
        for s in stored:
            cur = by_name.get(s.name)
            if cur is None:
                if any(p.name == s.name for p in primary):
                    continue  # disabled in config now: drop from the run set
                refreshed.append(s)  # backup-sourced entry: keep as stored
            elif s.name in failing:
                refreshed.append(cur)
            else:
                # Operator flags are facts about the site, not discovery
                # state: a healthy source keeps its stored (autodiscovered)
                # URL but takes the config's CURRENT grants and lane, so a
                # new crawl/image permission applies on the next run.
                refreshed.append(dataclasses.replace(
                    s, crawl_permission=cur.crawl_permission,
                    image_permission=cur.image_permission,
                    allowed_html=cur.allowed_html, fast_lane=cur.fast_lane))
        # Sources added to sources.yaml since the last validation join the run
        # set immediately (a dead guess just fails politely and is culled at
        # the next daily revalidation) — "add a source" works on the next run.
        known = {s.name for s in refreshed}
        fresh = [s for s in by_name.values() if s.name not in known]
        if fresh:
            log.info("merging %d newly configured sources into the run set: %s",
                     len(fresh), ", ".join(s.name for s in fresh))
        return refreshed + fresh
    if not validate_if_missing:
        return [s for s in primary if s.enabled]
    log.info("first run: validating %d sources live (+%d backups)", len(primary), len(backup))
    result = validate_sources(primary, backup, fetcher, db, cfg, now,
                              run_deadline=deadline)
    write_report(result, cfg, now)
    active = result["active"]
    effective = result.get("effective", active)
    if len(active) < 5:
        # Validation can come up near-empty when the network is unreachable;
        # fall back to the configured list so the pipeline still functions,
        # and do NOT persist the empty set (next run revalidates).
        log.warning("validation found only %d live sources; using configured list "
                    "without persisting (will revalidate next run)", len(active))
        return [s for s in primary if s.enabled]
    save_effective_sources(db, effective, now)
    log.info("validated: %d active + %d key-awaiting sources "
             "(report: sources_report.md)", len(active), len(effective) - len(active))
    return effective


def _rotate_for_run(sources: list[SourceConfig], run_key: str) -> list[SourceConfig]:
    """Deterministically rotate fetch order by run key.

    Any budget cut truncates the END of the sweep; a fixed order would starve
    the same tail sources forever. Rotation moves the cut around the list
    hour by hour while idempotent re-runs of one key keep one order.
    """
    if not sources:
        return sources
    rot = int(hashlib.sha256(run_key.encode("utf-8")).hexdigest(), 16) % len(sources)
    return sources[rot:] + sources[:rot]


def _order_for_run(sources: list[SourceConfig], run_key: str,
                   fast_lane: bool) -> list[SourceConfig]:
    """Fetch order for a run.

    On the hourly sweep the fast-lane feeds (Slickdeals, Hip2Save, Keepa,
    Reddit) were just covered by the 10-minute lane, yet they are big and
    slow to fetch at 1 req/s/host — left first in line they ate the whole
    fetch budget and the hourly-only blog sources never got fetched or
    parsed (live incident: 748 fetched, new=1). So the slow blogs go FIRST
    (rotated, so a budget cut lands on a different one each hour) and the
    fast-lane feeds trail. Fast runs only fetch fast-lane sources anyway,
    so a plain rotation serves them.
    """
    if fast_lane:
        return _rotate_for_run(sources, run_key)
    slow = [s for s in sources if not s.fast_lane]
    fast = [s for s in sources if s.fast_lane]
    return _rotate_for_run(slow, run_key) + fast


def _interleave_by_source(posts: list[RawPost]) -> list[RawPost]:
    """Round-robin posts across sources, keeping each source's own order.

    The parse loop stops at the spend cap, the max-posts cap, or the run
    deadline; with posts grouped by source, whoever is listed first takes the
    whole budget. Interleaved, every source lands a fair share per run.
    """
    queues: dict[str, list[RawPost]] = {}
    for p in posts:
        queues.setdefault(p.source_name, []).append(p)
    return [p for p in chain.from_iterable(zip_longest(*queues.values()))
            if p is not None]


def _fetch_all(sources: list[SourceConfig], fetcher, db: DB, cfg: AppConfig,
               now: datetime, deadline: float, fast_lane: bool) -> tuple[list[RawPost], dict[str, str]]:
    from .fetch.base import run_source
    from .fetch.registry import get_adapter

    # Fetch (including each source's article enrichment) must stop with the
    # parse reserve intact. Passing this as the per-source run_deadline bounds
    # enrichment too — otherwise an enrichment-heavy blog runs to the full run
    # deadline and parse gets no time (live incident: 505 new posts, 1 parsed).
    fetch_deadline = deadline - _RESERVE_FOR_PARSE_S
    posts: list[RawPost] = []
    errors: dict[str, str] = {}
    for source in sources:
        if not source.enabled:
            continue
        if fast_lane and not source.fast_lane:
            continue
        if time.monotonic() > fetch_deadline:
            log.warning("fetch window exhausted; skipping remaining sources from %s "
                        "(rotation puts them first on a later run)", source.name)
            break
        try:
            adapter = get_adapter(source, cfg, fast_lane=fast_lane)
        except ValueError as e:
            errors[source.name] = f"no adapter: {e}"
            continue
        got, ok, err = run_source(source, adapter, fetcher, db, cfg, now,
                                  run_deadline=fetch_deadline)
        if ok:
            posts.extend(got)
            log.info("source %s: %d items", source.name, len(got))
        else:
            errors[source.name] = err
            log.info("source %s: skipped (%s)", source.name, err)
    return posts, errors


def _build_parser(cfg: AppConfig, claude_client=None,
                  run_cap_usd: float | None = None):
    """One shared ClaudeParser per run (extraction, second opinions, critic all
    draw on the same spend accounting). None when unconfigured/unavailable."""
    from .parse.claude_client import ClaudeParser

    try:
        return ClaudeParser(cfg, client=claude_client, run_cap_usd=run_cap_usd)
    except ParserNotConfigured:
        log.warning("DEALS_CLAUDE_API_KEY not set: parse layer skipped")
        return None
    except Exception as e:  # model lookup / client construction failed
        log.error("parse layer unavailable: %s", e)
        return None


def _parse_new_posts(new_posts: list[RawPost], parser, cfg: AppConfig,
                     now: datetime, tz_by_source: dict[str, str],
                     deadline: float | None, db: DB | None = None
                     ) -> tuple[list[tuple[RawPost, Any]], dict]:
    from .parse.extractor import parse_posts

    stats = {"posts_sent": 0, "deals_extracted": 0, "deals_dropped_evidence": 0,
             "spend_usd": 0.0, "aborted_spend_cap": False, "parser": "disabled",
             "processed_hashes": []}
    if not new_posts or parser is None:
        return [], stats
    stats["parser"] = parser.model
    try:
        pairs, pstats = parse_posts(new_posts, parser, cfg, now, tz_by_source,
                                    deadline=deadline, db=db)
    except SpendCapReached:
        log.warning("spend cap reached before parsing could start")
        return [], stats
    stats.update(pstats)
    return pairs, stats


#: Hours between live price re-checks of the same Amazon deal (a dead
#: deal is off the wall within this long, and a batch is one Keepa call).
_RECHECK_EVERY_H = 3.0
#: The same for a used/open-box deal: its recheck carries an offers scan
#: (up to verify.USED_RECHECK_TOKENS per ASIN instead of 1), so it comes
#: round half as often.
_RECHECK_EVERY_USED_H = 6.0
#: Retry window for a new-condition deal whose recheck answered without a
#: verdict (Keepa data older than the deal's price): 1 token, so hourly.
_RETRY_NEW_H = 1.0
#: A live check that says "gone" is asked again this long after; only the
#: second agreeing reading kills the deal (models.Deal.gone_suspect_at).
_CONFIRM_GONE_H = CONFIRM_GONE_H
#: Early (in-window) Keepa calls per run made only to fetch a missing photo
#: (1 token each). The shared token budget (keepa_budget) is the real
#: brake: these run only while the bucket holds MIN_FOR_IMAGE_BACKFILL,
#: so they can never starve the deal lane again (the live incident).
_IMAGE_BACKFILL_PER_RUN = 300
#: Article fetches per run that backfill photos for existing deals whose
#: source granted image display: one polite fetch per article per pass
#: (the fetcher still holds 1 req/s per host), never repeated for a deal,
#: time-boxed by the run.
_BLOG_IMAGE_BACKFILL_PER_RUN = 60


def _product_of(deal: dict[str, Any]) -> str:
    """Which product a deal is: one product at two stores is one product."""
    return str(deal.get("normalized_item") or deal.get("item") or deal["dedupe_key"])


def _in_photo_window(deal: dict[str, Any], now: datetime,
                     show_dead: timedelta) -> bool:
    """Alive, or dead recently enough for the "Just missed" strip; older
    dead rows are off the page and not worth a fetch."""
    if deal.get("status") != "dead":
        return True
    died = util.parse_iso(deal.get("died_at") or "")
    return died is not None and now - died <= show_dead


def _credited_article(db: DB, deal: dict[str, Any],
                      granted: dict) -> dict[str, Any] | None:
    """The deal's first sighting on an image-granted source's article."""
    return next((s for s in db.sightings_for(deal["id"])
                 if s["source_name"] in granted
                 and str(s.get("source_url", "")).startswith("http")), None)


def _backfill_blog_images(db: DB, fetcher, sources: list[SourceConfig],
                          deadline: float | None, cfg: AppConfig | None = None) -> int:
    """Give existing photo-less deals from image-granted sources their picture.

    Deals created before a source's image grant (or from an excerpt-only
    feed entry) keep the credited article URL in their sightings; fetching
    that page once — politely, robots/permission-checked by the fetcher —
    fills the card by the ingest contract (product_images.pick_photo): the
    photo inside the deal's own product link, else the one the page names
    after its item (against the names of every deal stored for the
    article and every product the page itself names: an unstored product's
    photo is not lent out); both, and they differ: none. Else, for a
    single-product article only, its og:image. An article is fetched once
    per pass for all of its deals. A deal whose article yields no photo is
    flagged so it is never fetched again. A link several products share
    (marked at ingest, or held by two or more products of one article), or
    one that is a store, search or category page, names none of them: no
    photo inside it, no og:image — only one named after the item.
    """
    from .dedupe import _DEAL_FIELDS
    from .fetch.rss import _first_image, _og_image, _permission_host, post_photos
    from .product_images import _names_no_product, link_identity, pick_photo, single_product

    granted = {s.name: s for s in sources if s.image_permission}
    if not granted:
        return 0
    done = 0
    show_dead = timedelta(hours=float(getattr(cfg, "show_dead_hours", 0) or 0))
    now = util.now_utc()
    deals = db.all_deals()
    # A roundup article ("10 deals at Walmart today") has ONE share image,
    # a collage that belongs to none of its deals. Live wart: the same
    # collage sat on four cards, and three jigsaw puzzles from one post
    # shared one puzzle's photo. A roundup's share image is never used;
    # each of its deals gets only its own photo (in its link, or named
    # after it), and an image already on another product's card is never
    # copied.
    # "Roundup" = an article credited by two or more DISTINCT products. One
    # product at two stores ("Bluey book at Amazon or Walmart") is one
    # product: its article photo is the product's. (A first cut counted
    # deals, not products, and dropped 173 good photos in one run.)
    article_items: dict[str, set[str]] = {}
    # article -> (item, link) of its deals: the names a photo is matched against
    article_names: dict[str, list[tuple[str, str]]] = {}
    # (article, link) -> products: a link two or more products of one
    # article hold is a store or sale page, naming none of them.
    article_links: dict[tuple[str, str], set[str]] = {}
    images_in_use: dict[str, set[str]] = {}  # image -> products showing it
    for d in deals:
        if d.get("image_url"):
            images_in_use.setdefault(d["image_url"], set()).add(_product_of(d))
        link = link_identity(d.get("canonical_url") or "")
        for sgt in db.sightings_for(d["id"]):
            if sgt["source_name"] in granted:
                url = str(sgt.get("source_url", ""))
                article_items.setdefault(url, set()).add(_product_of(d))
                article_names.setdefault(url, []).append(
                    (str(d.get("item") or ""), str(d.get("canonical_url") or "")))
                if link:
                    article_links.setdefault((url, link), set()).add(_product_of(d))
    article_deals = {url: len(items) for url, items in article_items.items()}
    deals = _repair_emptied_photos(db, deals, granted, article_deals)
    deals = _lift_roundup_flags(db, deals, granted, article_deals, now, show_dead)
    done += _clear_shared_article_images(db, deals, article_deals)
    pages: dict[str, str] = {}                 # article -> html ('' = no page)
    photos: dict[str, Any] = {}                # article -> its PostPhotos
    for deal in deals:
        if deal.get("image_url") or \
                "image_backfill_tried" in (deal.get("flags") or []):
            continue
        if not _in_photo_window(deal, now, show_dead):
            continue
        sighting = _credited_article(db, deal, granted)
        if sighting is None:
            continue
        article = str(sighting["source_url"])
        # A link several products share names none: no photo inside it, no
        # og:image; a photo named after the item is still the deal's.
        shared = bool(deal.get("link_shared")) or len(article_links.get(
            (article, link_identity(deal.get("canonical_url") or "")), ())) > 1
        if article not in pages:
            if done >= _BLOG_IMAGE_BACKFILL_PER_RUN or \
                    (deadline is not None and time.monotonic() > deadline):
                continue  # unflagged: the next pass fetches it
            src = granted[sighting["source_name"]]
            kw = {}
            host = _permission_host(src)
            if host:
                kw["robots_override_host"] = host
            try:
                res = fetcher.get(article, conditional=False,
                                  deadline=deadline, **kw)
            except DeadlineExceeded:
                break  # out of time mid-fetch: stays unflagged for the next pass
            pages[article] = ""
            if res.status == 200 and res.body and not res.blocked_by_robots:
                pages[article] = res.body.decode("utf-8", errors="replace")
            done += 1
        html = pages[article]
        image = ""
        if html:
            if article not in photos:
                photos[article] = post_photos(html, article)
            canonical = str(deal.get("canonical_url") or "")
            # A store, search or category page names no product either.
            no_product = canonical.lower().startswith(("http://", "https://")) \
                and _names_no_product(canonical)
            # Both of its links, as ingest: the page's photos are keyed by
            # the link as posted (an affiliate redirect, often).
            links = [str(deal.get("link_as_posted") or ""), canonical]
            image = pick_photo(links, str(deal.get("item") or ""),
                               article_names.get(article, []), photos[article],
                               in_link=not (shared or no_product))
            if image == "" and not (shared or no_product) \
                    and article_deals.get(article, 0) <= 1 \
                    and single_product(photos[article]):
                # Single-product article: its share image is the product's.
                # One stored deal is not enough: a page with two or more
                # product links or photos is a roundup, and its share image
                # is the collage.
                image = _og_image(html) or _first_image(html)
            image = image or ""  # None: its link and its name disagree
        product = _product_of(deal)
        if image and images_in_use.get(image, set()) - {product}:
            image = ""  # already another product's photo: not this deal's
        payload = {k: deal.get(k) for k in _DEAL_FIELDS if k in deal}
        if shared:
            payload["link_shared"] = True  # nor the retailer page's photo
        if image:
            payload["image_url"] = image
            images_in_use.setdefault(image, set()).add(product)
        else:
            flags = list(payload.get("flags") or [])
            if "image_backfill_tried" not in flags:
                flags.append("image_backfill_tried")
            payload["flags"] = flags
        db.update_deal(deal["dedupe_key"], payload=payload)
    if done:
        log.info("blog image backfill: %d article(s) fetched", done)
    return done


_PHOTO_REPAIR_KEY = "image_repair_2026_10_11"


def _repair_emptied_photos(db: DB, deals: list[dict[str, Any]], granted: dict,
                           article_deals: dict[str, int]) -> list[dict[str, Any]]:
    """One-time repair (db meta keyed): the first roundup rule counted deals
    instead of products and emptied 173 good photos in one run. Photo-less
    deals credited to a single-product article get their 'tried' flag
    lifted once so the normal pass fetches the article photo again."""
    from .dedupe import _DEAL_FIELDS

    if db.meta_get(_PHOTO_REPAIR_KEY):
        return deals
    lifted = 0
    for d in deals:
        flags = list(d.get("flags") or [])
        if d.get("image_url") or "image_backfill_tried" not in flags:
            continue
        urls = [str(s.get("source_url", "")) for s in db.sightings_for(d["id"])
                if s["source_name"] in granted]
        if not urls or any(article_deals.get(u, 0) > 1 for u in urls):
            continue
        flags.remove("image_backfill_tried")
        d["flags"] = flags
        payload = {k: d.get(k) for k in _DEAL_FIELDS if k in d}
        db.update_deal(d["dedupe_key"], payload=payload)
        lifted += 1
    db.meta_set(_PHOTO_REPAIR_KEY, util.iso(util.now_utc()))
    if lifted:
        log.info("photo repair: %d deal(s) may fetch their article photo again", lifted)
    return deals


#: Re-keyed when the per-product rule changed (layout pairing -> link and
#: name evidence): roundup deals flagged under the old rule retry once.
_ROUNDUP_LIFT_KEY = "roundup_photos_by_name_2026_10_11"


def _lift_roundup_flags(db: DB, deals: list[dict[str, Any]], granted: dict,
                        article_deals: dict[str, int], now: datetime,
                        show_dead: timedelta) -> list[dict[str, Any]]:
    """One-time lift (db meta keyed): roundup articles used to be refused
    outright — their deals were flagged 'tried' without a fetch (400 of the
    720 photo-less live cards) — then tried under a layout rule since
    replaced. Photo-less deals on the page (alive, or in the "Just missed"
    window) credited to a roundup lose that flag once, so the per-product
    pass tries each of them under the current rule."""
    from .dedupe import _DEAL_FIELDS

    if db.meta_get(_ROUNDUP_LIFT_KEY):
        return deals
    lifted = 0
    for d in deals:
        flags = list(d.get("flags") or [])
        if d.get("image_url") or "image_backfill_tried" not in flags \
                or not _in_photo_window(d, now, show_dead):
            continue
        sighting = _credited_article(db, d, granted)
        if sighting is None or article_deals.get(str(sighting["source_url"]), 0) <= 1:
            continue
        flags.remove("image_backfill_tried")
        d["flags"] = flags
        payload = {k: d.get(k) for k in _DEAL_FIELDS if k in d}
        db.update_deal(d["dedupe_key"], payload=payload)
        lifted += 1
    db.meta_set(_ROUNDUP_LIFT_KEY, util.iso(util.now_utc()))
    if lifted:
        log.info("roundup lift: %d deal(s) may get their own photo", lifted)
    return deals


def _clear_shared_article_images(db: DB, deals: list[dict[str, Any]],
                                 article_deals: dict[str, int]) -> int:
    """Drop a photo that two or more DIFFERENT products share when any of
    their deals was credited to a roundup article (two or more DISTINCT
    products from one page): that image is the article's collage, not a
    product. Each loses the image and is flagged so the backfill never
    re-attaches it. One product at two stores shares its photo by right,
    roundup or not."""
    from .dedupe import _DEAL_FIELDS

    by_image: dict[str, list[dict[str, Any]]] = {}
    for d in deals:
        if d.get("image_url"):
            by_image.setdefault(d["image_url"], []).append(d)
    cleared = 0
    for image, group in by_image.items():
        if len({_product_of(d) for d in group}) < 2:
            continue  # one deal, or one product listed twice
        from_roundup = any(
            article_deals.get(str(sgt.get("source_url", "")), 0) > 1
            for d in group for sgt in db.sightings_for(d["id"]))
        if not from_roundup:
            continue  # e.g. one retailer photo on two variant listings
        for d in group:
            payload = {k: d.get(k) for k in _DEAL_FIELDS if k in d}
            payload["image_url"] = ""
            flags = list(payload.get("flags") or [])
            if "image_backfill_tried" not in flags:
                flags.append("image_backfill_tried")
            payload["flags"] = flags
            db.update_deal(d["dedupe_key"], payload=payload)
            cleared += 1
        log.info("cleared a roundup collage shared by %d deals", len(group))
    return cleared


_BOTWALL_REPAIR_KEY = "botwall_urls_2026_10_11"


def _repair_botwall_urls(db: DB) -> int:
    """One-time repair (db meta keyed): redirect resolution followed
    Walmart, eBay and Sam's Club into their bot walls and stored the wall
    as the product link (live: 72 Walmart /blocked, 8 eBay
    /splashui/challenge, 1 Sam's Club /are-you-human cards). Each such
    deal gets the product URL the wall names
    (http_client.challenge_target) as its canonical_url, and its photo
    'tried' flags lifted so both photo passes try again under the normal
    evidence rules. The dedupe_key is identity and is never changed; a
    malformed wall parameter leaves the row untouched."""
    from .dedupe import _DEAL_FIELDS
    from .http_client import canonicalize_url, challenge_target, is_challenge_url

    if db.meta_get(_BOTWALL_REPAIR_KEY):
        return 0
    fixed = 0
    for d in db.all_deals():
        wall = str(d.get("canonical_url") or "")
        if not is_challenge_url(wall):
            continue
        target = challenge_target(wall)
        if not target:
            continue
        url = canonicalize_url(target)
        payload = {k: d.get(k) for k in _DEAL_FIELDS if k in d}
        payload["canonical_url"] = url
        payload["flags"] = [f for f in d.get("flags") or []
                            if f not in ("product_image_tried", "image_backfill_tried")]
        db.update_deal(d["dedupe_key"], payload=payload, canonical_url=url)
        fixed += 1
    db.meta_set(_BOTWALL_REPAIR_KEY, util.iso(util.now_utc()))
    if fixed:
        log.info("bot-wall repair: %d deal(s) link their product again", fixed)
    return fixed


_TRACKER_ASIN_REPAIR_KEY = "tracker_asins_2026_10_11"


def _repair_tracker_asins(db: DB) -> int:
    """Restore tracker ASINs and retry photos once, preserving affiliate links."""
    from .dedupe import _DEAL_FIELDS
    from .http_client import extract_asin, tracker_target

    if db.meta_get(_TRACKER_ASIN_REPAIR_KEY):
        return 0
    fixed = 0
    for d in db.all_deals():
        target = tracker_target(d.get("canonical_url") or "")
        if not target:
            continue
        payload = {k: d.get(k) for k in _DEAL_FIELDS if k in d}
        if not d.get("asin"):
            payload["asin"] = extract_asin(target)
        payload["flags"] = [f for f in d.get("flags") or []
                            if f not in ("product_image_tried", "image_backfill_tried")]
        db.update_deal(d["dedupe_key"], payload=payload)
        fixed += 1
    db.meta_set(_TRACKER_ASIN_REPAIR_KEY, util.iso(util.now_utc()))
    return fixed


#: Retailer product pages fetched per run for a missing photo: one polite
#: GET each (the fetcher still holds 1 req/s per host), never repeated,
#: time-boxed by the run.
_PRODUCT_IMAGE_PER_RUN = 40
#: Link hosts never fetched for a photo (substring match): Amazon, whose
#: photos come from Keepa, and deal trackers, blogs and affiliate
#: redirectors, whose share image is their own branding, not the product's.
_PRODUCT_IMAGE_SKIP_HOSTS = (
    "amazon", "slickdeals", "retailmenot", "dealnews", "redirectingat",
    "shareasale", "awin", "anrdoezrs", "linksynergy", "dpbolvw", "jdoqocy",
    "tkqlhce", "kqzyfj", "clickbank", "bit.ly", "go.skimresources", "rstyle",
    "bradsdeals", "dealmoon", "hip2save", "kcl", "thekrazycouponlady",
    "southernsavers", "moneysavingmom", "cc.dealsofamerica",
)
#: Well-known retailers (all <name>.com) get priority over other sellers.
_PRODUCT_IMAGE_RETAILERS = frozenset((
    "walmart", "target", "bestbuy", "dickssportinggoods", "kohls", "macys",
    "homedepot", "lowes", "costco", "samsclub", "wayfair", "newegg",
    "staples", "officedepot", "cvs", "walgreens", "chewy", "petco",
    "petsmart", "ulta", "sephora", "oldnavy", "gap", "nike", "adidas",
    "lego", "gamestop", "bhphotovideo", "michaels", "joann", "rei",
    "zappos", "dell", "lenovo", "samsung", "apple", "hsn", "qvc", "ebay",
    "woot", "academy", "bathandbodyworks", "bedbathandbeyond", "overstock",
    "crateandbarrel", "williams-sonoma", "potterybarn", "nordstrom",
    "jcpenney", "belk", "dsw", "famousfootwear", "underarmour", "columbia",
    "patagonia",
))
#: The share-preview tags a product page declares for link previews (what
#: Slack or iMessage show): og:image / twitter:image, either attribute
#: order, absolute or protocol-relative URL.
_PRODUCT_IMAGE_META = (
    re.compile(r"<meta[^>]+(?:property|name)=[\"'](?:og|twitter):image"
               r"(?::secure_url|:url|:src)?[\"'][^>]+content=[\"']"
               r"((?:https?:)?//[^\"']+)[\"']", re.I),
    re.compile(r"<meta[^>]+content=[\"']((?:https?:)?//[^\"']+)[\"'][^>]+"
               r"(?:property|name)=[\"'](?:og|twitter):image"
               r"(?::secure_url|:url|:src)?[\"']", re.I),
)
#: A share image that is not the product: the store's logo, placeholder or
#: default art, a sprite or icon, or a tracking pixel. "icon" and "default"
#: count only at a word start, so a silicone spatula and Nike's t_default
#: image transform keep their photo. SVGs are rejected separately.
_PRODUCT_IMAGE_JUNK = re.compile(
    r"logo|placeholder|sprite|favicon|(?<![a-z])icon|(?<![a-z_])default"
    r"|/(?:pixel|spacer|blank|1x1|beacon|transparent)[^/]*\.(?:gif|png)(?:[?#]|$)",
    re.I)


_SHORT_LINK_HOSTS = (
    "on.ltk.com", "goto.target.com", "amzn.to", "a.co", "zdcs.link",
    "creator.anchor.store", "go.magik.ly", "shoplowes.me", "link.amazon",
    "go.dealmoon.com", "clicks.trx-hub.com",
)
_SHORT_LINK_PER_RUN = 60


def _site_of(url: str) -> str:
    """The site a URL belongs to, subdomains folded (go.dealnews.com ->
    dealnews.com); "" when it has none. Two labels is enough here: it only
    tells a deal site's own redirect from a retailer's page."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    site = ".".join(host.split(".")[-2:])
    return site if "." in site else ""


def _resolve_short_links(db: DB, fetcher, deadline: float | None,
                         cfg: AppConfig, *, stats: dict | None = None,
                         deals: list[dict[str, Any]] | None = None) -> int:
    """Resolve photo-less redirect links once, newest first, keeping identity.
    deals: the deals to consider (run() passes the site's, so the per-run
    cap is spent on deals visitors see); None = every deal."""
    from .dedupe import _DEAL_FIELDS
    from .http_client import extract_asin, is_challenge_url, resolve_redirects

    resolved = tried = 0
    now = util.now_utc()
    show_dead = timedelta(hours=float(getattr(cfg, "show_dead_hours", 0) or 0))
    for deal in sorted(db.all_deals() if deals is None else deals,
                       key=lambda d: str(d.get("first_seen_at") or ""), reverse=True):
        if deal.get("image_url") or deal.get("link_shared") or \
                "link_resolve_tried" in (deal.get("flags") or []):
            continue
        if deal.get("status") == "dead":
            died = util.parse_iso(deal.get("died_at") or "")
            if died is None or now - died > show_dead:
                continue
        link = deal.get("canonical_url") or ""
        try:
            parts = urlsplit(link)
            host = (parts.hostname or "").lower()
        except ValueError:
            continue
        if parts.scheme not in ("http", "https") or not host or is_challenge_url(link):
            continue
        if host.removeprefix("www.") not in _SHORT_LINK_HOSTS:  # www.creator.anchor.store, live
            domain = _site_of(link)
            # Known sellers remain product pages even if credited as a source.
            if not domain or domain.startswith("amazon.") or \
                    domain in {r + ".com" for r in _PRODUCT_IMAGE_RETAILERS}:
                continue
            if not any(domain == _site_of(s.get("source_url") or "")
                       for s in db.sightings_for(deal["id"])):
                continue
        if deadline is not None and time.monotonic() > deadline:
            break
        try:
            target = resolve_redirects(fetcher, link, cfg.redirect_max_hops,
                                       deadline=deadline)
        except DeadlineExceeded:
            break
        tried += 1
        payload = {k: deal.get(k) for k in _DEAL_FIELDS if k in deal}
        flags = list(payload.get("flags") or [])
        flags.append("link_resolve_tried")
        changed = target != link and urlsplit(target).scheme in ("http", "https") \
            and not is_challenge_url(target)
        if changed:
            payload["canonical_url"] = target
            if not deal.get("asin"):
                payload["asin"] = extract_asin(target)
            flags = [f for f in flags if f not in
                     ("product_image_tried", "image_backfill_tried")]
            resolved += 1
        payload["flags"] = flags
        db.update_deal(deal["dedupe_key"], payload=payload,
                       canonical_url=target if changed else None)
        if tried >= _SHORT_LINK_PER_RUN:
            break
    if stats is not None:
        stats.update(resolved=resolved, tried=tried)
    return resolved


_POST_LINK_PER_RUN = 40
#: Short links (amzn.to, a.co) followed per post: a roundup can hold dozens,
#: each a polite 1 s hop, and the run's time is shared.
_POST_SHORT_LINKS = 10


def _links_from_posts(db: DB, fetcher, deadline: float | None,
                      cfg: AppConfig, *, deals: list[dict[str, Any]],
                      stats: dict | None = None) -> int:
    """Recover Amazon product links once per live, photo-less site deal.

    Cache only links and anchor words in memory; post content is never stored.
    The cap counts distinct post fetches, while stats count deals attempted.
    """
    from .dedupe import _DEAL_FIELDS
    from .fetch.pages import _Tree
    from .http_client import canonicalize_url, extract_asin, resolve_redirects

    posts: dict[str, dict[str, list[set[str]]]] = {}
    tried = found = fetched = 0
    for deal in sorted(deals, key=lambda d: str(d.get("first_seen_at") or ""),
                       reverse=True):
        if deal.get("status") != "alive" or deal.get("image_url") or \
                "post_link_tried" in (deal.get("flags") or []):
            continue
        if deal.get("canonical_url") and (deal.get("asin") or
                "amazon" not in (deal.get("store") or "").lower()):
            continue
        if deadline is not None and time.monotonic() > deadline:
            break
        sightings = db.sightings_for(deal["id"])
        post = (sightings[0].get("source_url") or "") if sightings else ""
        if post not in posts:
            if fetched >= _POST_LINK_PER_RUN:
                continue
            posts[post] = {}
            try:
                parts = urlsplit(post)
                if parts.scheme in ("http", "https") and parts.hostname \
                        and not util.official_feed_only(post):
                    fetched += 1
                    res = fetcher.get(post, conditional=False, deadline=deadline)
                    if res.status == 200 and not res.blocked_by_robots and res.body:
                        root = _Tree(res.body.decode("utf-8", "replace")).root
                        short_links = {}
                        for anchor in root.find(tag="a"):
                            try:
                                link = urljoin(res.final_url or post,
                                               anchor.attrs.get("href") or "")
                                parts = urlsplit(link)
                            except ValueError:
                                continue
                            host = (parts.hostname or "").lower()
                            if parts.scheme not in ("http", "https"):
                                continue
                            if host in ("amzn.to", "a.co"):
                                if link not in short_links and \
                                        len(short_links) >= _POST_SHORT_LINKS:
                                    continue
                                if link not in short_links:
                                    short_links[link] = resolve_redirects(
                                        fetcher, link, cfg.redirect_max_hops,
                                        deadline=deadline)
                                link = short_links[link]
                            if asin := extract_asin(link):
                                posts[post].setdefault(asin, []).append(
                                    set(util.normalize_item(anchor.text()).split()))
            except (DeadlineExceeded, ValueError):
                # A timeout or malformed link is still this deal's one attempt.
                posts[post] = {}
        candidates = posts[post]
        asin = ""
        if len(candidates) == 1:
            asin = next(iter(candidates))
        elif candidates:
            words = set(util.normalize_item(deal.get("item") or "").split())
            if words:
                scores = {a: max(len(words & text) / len(words) for text in texts)
                          for a, texts in candidates.items()}
                best = max(scores.values())
                winners = [a for a, score in scores.items() if score == best]
                if best >= 0.6 and len(winners) == 1:
                    asin = winners[0]
        payload = {k: deal.get(k) for k in _DEAL_FIELDS if k in deal}
        flags = list(payload.get("flags") or []) + ["post_link_tried"]
        canonical = None
        if asin:
            canonical = canonicalize_url(f"https://www.amazon.com/dp/{asin}")
            payload.update(asin=asin, canonical_url=canonical)
            flags = [f for f in flags if f not in
                     ("image_backfill_tried", "product_image_tried")]
            found += 1
        payload["flags"] = flags
        db.update_deal(deal["dedupe_key"], payload=payload, canonical_url=canonical)
        tried += 1
    if stats is not None:
        stats.update(tried=tried, found=found)
    return found


def _photo_gap_summary(deals: list[dict[str, Any]], redirects: dict) -> tuple[dict, str]:
    """Only host/count diagnostics: no item names, paths or query strings.
    deals: the live deals on the site."""
    from collections import Counter

    hosts = Counter()
    for deal in deals:
        if not deal.get("image_url"):
            try:
                host = (urlsplit(deal.get("canonical_url") or "").hostname or "").lower()
            except ValueError:
                host = ""
            hosts[host or "unknown"] += 1
    gaps = {"alive_without_photo": sum(hosts.values()),
            "by_canonical_host": dict(hosts.most_common(10))}
    counts = ", ".join(f"{h}: {n}" for h, n in gaps["by_canonical_host"].items()) or "none"
    lines = (f"photo gaps: {gaps['alive_without_photo']} alive deals without a photo; "
             f"by canonical host: {counts}\n"
             f"redirects: resolved {redirects['resolved']} of {redirects['tried']}\n")
    return gaps, lines


def _backfill_product_images(db: DB, fetcher, deadline: float | None,
                             cfg: AppConfig | None = None) -> int:
    """Give photo-less deals linking straight to an HTTPS retailer the
    picture that retailer itself publishes for link previews.

    Deals from sources without image rights (DealNews, Slickdeals, ...)
    show a store-name tile. The product page the deal links to declares an
    og:image / twitter:image for exactly this use — what Slack or iMessage
    would show for the link — and only that URL is stored, never the bytes
    (store facts only). This function reads NOTHING else from the page: no
    price, no text; the deal's facts stay as the evidence-backed extraction
    left them.

    Politeness is the fetcher's, with no override: robots.txt honored, one
    request per second per host, our descriptive User-Agent, conditional
    GET. A robots block, 403, 404, 5xx or timeout simply flags the deal as
    tried and moves on, quietly. One attempt per deal ever
    ("product_image_tried"), at most _PRODUCT_IMAGE_PER_RUN per run, inside
    the run deadline; run() keeps it on the fast lane. A listing (category,
    search, sale page) or a bot wall is never fetched (live: 71 fetches of
    Walmart's wall, no photo).
    """
    from html import unescape

    from .dedupe import _DEAL_FIELDS
    from .http_client import is_challenge_url, tracker_target
    from .product_images import _names_no_product

    found = tried = 0
    show_dead = timedelta(hours=float(getattr(cfg, "show_dead_hours", 0) or 0))
    now = util.now_utc()
    candidates = []
    for deal in db.all_deals():
        if deal.get("image_url") or \
                "product_image_tried" in (deal.get("flags") or []):
            continue
        if deal.get("link_shared"):
            continue  # a store or sale page several products share: no one's photo
        if deal.get("status") == "dead":
            # Same window as the blog backfill: only the "Just missed" strip.
            died = util.parse_iso(deal.get("died_at") or "")
            if died is None or now - died > show_dead:
                continue
        link = deal.get("canonical_url") or deal.get("product_url") or ""
        link = tracker_target(link) or link
        try:
            parts = urlsplit(link)
            host = (parts.hostname or "").lower()
        except ValueError:
            continue
        if parts.scheme != "https" or not host \
                or host in _SHORT_LINK_HOSTS \
                or any(t in host for t in _PRODUCT_IMAGE_SKIP_HOSTS):
            continue
        if _names_no_product(link) or is_challenge_url(link):
            # A category, search or sale page shows no one product (its
            # share image is a banner); a bot wall is never fetched.
            continue
        priority = any(host == r + ".com" or host.endswith("." + r + ".com")
                       for r in _PRODUCT_IMAGE_RETAILERS)
        candidates.append((not priority, deal, link))
    for _other, deal, link in sorted(candidates, key=lambda c: c[0]):
        if deadline is not None and time.monotonic() > deadline:
            break
        try:
            res = fetcher.get(link, conditional=True, deadline=deadline)
        except DeadlineExceeded:
            break  # out of time mid-fetch: stays unflagged for the next run
        tried += 1
        image = ""
        if res.status == 200 and res.body and not res.blocked_by_robots:
            page = res.body.decode("utf-8", errors="replace")
            # First declared share image that is not junk; a "//cdn/..."
            # URL is resolved to https, attribute entities are decoded.
            cands = (unescape(c) if c.startswith("http") else "https:" + unescape(c)
                     for pat in _PRODUCT_IMAGE_META for c in pat.findall(page))
            image = next((c for c in cands
                          if not urlsplit(c).path.lower().endswith(".svg")
                          and not _PRODUCT_IMAGE_JUNK.search(c)), "")
        payload = {k: deal.get(k) for k in _DEAL_FIELDS if k in deal}
        flags = list(payload.get("flags") or [])
        flags.append("product_image_tried")
        payload["flags"] = flags
        if image:
            payload["image_url"] = image
            found += 1
        db.update_deal(deal["dedupe_key"], payload=payload)
        if tried >= _PRODUCT_IMAGE_PER_RUN:
            break
    if tried:
        log.info("product image backfill: %d of %d retailer pages gave a photo",
                 found, tried)
    return found


_IMAGE_CHECK_PER_RUN = 30
_IMAGE_CHECK_META_PREFIX = "image_checked:"


def _check_stored_images(db: DB, fetcher, deadline: float | None,
                         now: datetime) -> int:
    """Check oldest URLs first, once per day; broken photos may retry."""
    from .dedupe import _DEAL_FIELDS

    by_url: dict[str, list[dict[str, Any]]] = {}
    for deal in db.all_deals():
        image = deal.get("image_url") or ""
        if image:
            by_url.setdefault(image, []).append(deal)
    pending = []
    for image in by_url:
        key = _IMAGE_CHECK_META_PREFIX + hashlib.sha256(image.encode()).hexdigest()
        checked = util.parse_iso(db.meta_get(key) or "")
        if checked is None or now - checked >= timedelta(hours=24):
            pending.append((checked, image, key))
    pending.sort(key=lambda c: c[0] or datetime.min.replace(tzinfo=now.tzinfo))
    cleared = 0
    for _checked, image, key in pending[:_IMAGE_CHECK_PER_RUN]:
        if deadline is not None and time.monotonic() > deadline:
            break
        try:
            res, content_type = fetcher.check_image(image, deadline=deadline)
        except DeadlineExceeded:
            break  # no completed check: retry next run
        db.meta_set(key, util.iso(now))
        # Only proof clears a photo: gone (404/410) or a page that is not an
        # image. A timeout, robots rule, 401/403 (a CDN refusing our crawler,
        # not a browser), 429 or 5xx proves nothing: checked again in 24 h.
        gone = res.status in (404, 410)
        not_image = 200 <= res.status < 300 \
            and not content_type.lower().startswith("image/")
        if not (gone or not_image):
            continue
        for deal in by_url[image]:
            payload = {k: deal.get(k) for k in _DEAL_FIELDS if k in deal}
            payload["image_url"] = ""
            payload["flags"] = [f for f in deal.get("flags") or [] if f not in
                                ("product_image_tried", "image_backfill_tried")]
            db.update_deal(deal["dedupe_key"], payload=payload)
            cleared += 1
    return cleared


_REVIVE_USED_KEY = "revive_used_2026_10_11"
#: The repair (and the fresh offers scans it asks for) reaches back this
#: far: older kills are left alone, and retries stop with the window.
_REVIVE_USED_WINDOW_H = 48.0


def _revivable(deal: dict[str, Any], now: datetime) -> bool:
    """A dead used/open-box deal the recheck killed ('price_moved') within
    _REVIVE_USED_WINDOW_H that has not expired: what the repair re-opens."""
    from .verify import is_used_deal

    if deal.get("status") != "dead" or not is_used_deal(deal) \
            or "price_moved" not in (deal.get("flags") or []):
        return False
    died = util.parse_iso(deal.get("died_at") or "")
    if died is None or now - died > timedelta(hours=_REVIVE_USED_WINDOW_H):
        return False
    expires = util.parse_iso(deal.get("expires_at") or "")
    return expires is None or expires >= now


def _revive_used_price_moved(db: DB, now: datetime) -> int:
    """One-time repair (db meta keyed): the recheck read a plain /product
    record's empty Warehouse slot as "sold" and killed live open-box deals
    (live: five Amazon Warehouse deals dead within 0.5-7 h, still on sale).
    Each such kill (_revivable) gets its recheck stamp cleared but stays
    dead: _recheck_prices gives it a fresh offers scan, and only a True
    verdict brings it back, so nothing is published or alerted unchecked."""
    from .dedupe import _DEAL_FIELDS

    if db.meta_get(_REVIVE_USED_KEY):
        return 0
    reopened = 0
    for d in db.all_deals(status="dead"):
        if not _revivable(d, now) or not d.get("price_rechecked_at"):
            continue
        payload = {k: d.get(k) for k in _DEAL_FIELDS if k in d}
        payload["price_rechecked_at"] = ""
        db.update_deal(d["dedupe_key"], payload=payload)
        reopened += 1
    db.meta_set(_REVIVE_USED_KEY, util.iso(now))
    if reopened:
        log.info("revive repair: %d used/open-box deal(s) the recheck may "
                 "have wrongly killed get a fresh offers scan", reopened)
    return reopened


def _recheck_prices(db: DB, cfg: AppConfig, now: datetime,
                    image_backfill: bool = True,
                    deadline: float | None = None) -> dict[str, Any]:
    """The 'click the page before publishing' gate, via Keepa's licensed API
    (we never scrape retailer pages): every about-to-publish Amazon deal
    gets its LIVE price checked at most once per _RECHECK_EVERY_H hours,
    a hundred deals per Keepa call (_RECHECK_EVERY_USED_H for used/open-box,
    whose batches carry an offers scan: KEEPA_OFFERS_BATCH deals per call
    at up to USED_RECHECK_TOKENS each). A moved price means the deal is
    over: status dead + 'price_moved' flag — the site shows it marked dead,
    alerts never fire for it. A dead deal the repair re-opened rides the
    used batches and comes back only on a True verdict. Photo-less new
    deals inside the window ride stats-only batches early (the record
    carries the image), after the due rechecks; used deals never do (a
    stats-only answer would re-stamp them and starve their offers scan).
    ``deadline`` (time.monotonic) ends the pass."""
    from datetime import timedelta

    from . import rank
    from .dedupe import _DEAL_FIELDS
    from .keepa_budget import (MIN_FOR_DEAL_QUERY, MIN_FOR_IMAGE_BACKFILL,
                               MIN_FOR_RECHECK, KeepaBudget)
    from .verify import (KEEPA_BATCH, KEEPA_OFFERS_BATCH, USED_RECHECK_TOKENS,
                         is_used_deal, live_price_reading, recheck_amazon_prices,
                         woot_offer_url)

    _revive_used_price_moved(db, now)
    stats = {"checked": 0, "dead": 0, "image_backfill": 0, "tokens_short": 0,
             "used_checked": 0}

    def tried_recently(deal: dict[str, Any], used: bool) -> bool:
        # An answered-but-inconclusive recheck waits its retry window: live
        # review finding, a used deal whose offers scan kept saying "no
        # opinion" was re-scanned (13 tokens) on every 10-minute run.
        tried = util.parse_iso(deal.get("recheck_tried_at") or "")
        wait = _RECHECK_EVERY_USED_H if used else _RETRY_NEW_H
        return tried is not None and now - tried < timedelta(hours=wait)

    if not cfg.feature_flags.get("keepa"):
        return stats
    # Token budget carried over from the last Keepa response of any run;
    # each batch below updates it, and it is persisted at the end.
    budget = KeepaBudget.load(db)
    due: list[dict[str, Any]] = []
    due_used: list[dict[str, Any]] = []
    photo: list[dict[str, Any]] = []
    for deal in db.all_deals(status="alive"):
        if not deal.get("asin") or woot_offer_url(deal):
            continue
        if deal.get("death") in {"post_says_over", "stated_expiry"} \
                and deal.get("gone_suspect_at") == util.iso(now):
            continue  # a post/expiry queues the first API reading for the next run
        used = is_used_deal(deal)
        every = _RECHECK_EVERY_USED_H if used else _RECHECK_EVERY_H
        if deal.get("gone_suspect_at"):
            every = _CONFIRM_GONE_H  # a suspect is asked again soon
        last = util.parse_iso(deal.get("price_rechecked_at") or "")
        in_window = last is not None and now - last < timedelta(hours=every)
        if in_window and (used or not image_backfill or deal.get("image_url")
                          or len(photo) >= _IMAGE_BACKFILL_PER_RUN):
            continue
        if tried_recently(deal, used):
            continue
        ok, _reason = rank.is_publishable(deal, cfg)
        if not ok:
            continue  # only spend tokens on deals that would publish
        (photo if in_window else due_used if used else due).append(deal)
    # Re-opened by the repair: dead until a fresh offers scan says alive.
    for deal in db.all_deals(status="dead"):
        if deal.get("asin") and not deal.get("price_rechecked_at") \
                and _revivable(deal, now) and not tried_recently(deal, True) \
                and rank.is_publishable(dict(deal, status="alive"), cfg)[0]:
            due_used.append(deal)
    # Longest-unchecked first (never-checked leading): when demand outgrows
    # the token plan the shortfall spreads across every deal instead of the
    # newest rows never being rechecked at all (live review finding).
    due.sort(key=lambda d: d.get("price_rechecked_at") or "")
    due_used.sort(key=lambda d: d.get("price_rechecked_at") or "")

    def apply(deal: dict[str, Any], verdict: bool | None,
              had_photo: bool = True, answered: bool = False) -> None:
        if verdict is None:
            # No price opinion, but a photo the record carried is still
            # kept (live wart: photo-only answers were thrown away). Keepa
            # answered yet could not decide: wait the retry window. A 429 or
            # transport miss (no answer) retries on the next pass.
            if answered or (not had_photo and deal.get("image_url")):
                payload = {k: deal.get(k) for k in _DEAL_FIELDS if k in deal}
                if answered:
                    payload["recheck_tried_at"] = util.iso(now)
                db.update_deal(deal["dedupe_key"], payload=payload)
            return
        stats["checked"] += 1
        if is_used_deal(deal):
            stats["used_checked"] += 1
        payload = {k: deal.get(k) for k in _DEAL_FIELDS if k in deal}
        payload["price_rechecked_at"] = util.iso(now)
        if deal.get("status") == "dead":
            # A re-opened kill: alive again only on a live offer at the
            # price; otherwise the stamp ends its re-check, still dead.
            if verdict:
                payload["status"] = "alive"
                payload["died_at"] = ""
                payload["death"] = ""
                payload["price_since"] = util.iso(now)
                payload["flags"] = [f for f in payload.get("flags") or []
                                    if f != "price_moved"]
                log.info("price recheck: %s is alive again (live offer at "
                         "the price)", deal.get("dedupe_key"))
            db.update_deal(deal["dedupe_key"], payload=payload,
                           status="alive" if verdict else None)
            return
        outcome = live_price_reading(payload, verdict, now)
        if outcome == "suspect":
            stats["suspect"] = stats.get("suspect", 0) + 1
        elif outcome == "dead":
            stats["dead"] += 1
        db.update_deal(deal["dedupe_key"], payload=payload,
                       status="dead" if outcome == "dead" else None)

    # Due rechecks first (a nice-to-have photo never delays them); each
    # class waits while the bucket is below its bar, and a batch never
    # takes the deal lane's last MIN_FOR_DEAL_QUERY tokens — a used batch
    # (offers scan) is sized at its worst-case cost per deal and needs room
    # for at least one.
    used_need = max(MIN_FOR_RECHECK, MIN_FOR_DEAL_QUERY + USED_RECHECK_TOKENS)
    for group, need, per_deal, per_call, is_photo in (
            (due, MIN_FOR_RECHECK, 1, KEEPA_BATCH, False),
            (due_used, used_need, USED_RECHECK_TOKENS, KEEPA_OFFERS_BATCH,
             False),
            (photo, MIN_FOR_IMAGE_BACKFILL, 1, KEEPA_BATCH, True)):
        i = 0
        while i < len(group):
            if deadline is not None and time.monotonic() > deadline:
                stats["tokens_short"] += len(group) - i
                log.info("price recheck: out of time with %d deals left; "
                         "they ride the next run", len(group) - i)
                break
            if not budget.allows(need, now):
                stats["tokens_short"] += len(group) - i
                break
            avail = budget.available(now)
            size = per_call if avail is None else max(1, min(
                per_call, int((avail - MIN_FOR_DEAL_QUERY) / per_deal)))
            batch = group[i:i + size]
            photo_less = {d["dedupe_key"] for d in batch if not d.get("image_url")}
            verdicts = recheck_amazon_prices(batch, cfg, budget=budget,
                                             used_offers=not is_photo)
            if is_photo:
                stats["image_backfill"] += len(batch)
            for deal in batch:
                apply(deal, verdicts.get(deal["dedupe_key"]),
                      had_photo=deal["dedupe_key"] not in photo_less,
                      answered=deal["dedupe_key"] in verdicts)
            found = sum(1 for d in batch
                        if d["dedupe_key"] in photo_less and d.get("image_url"))
            if photo_less:
                log.info("price recheck: %d of %d photo-less deal(s) got a "
                         "Keepa photo", found, len(photo_less))
            i += len(batch)
    budget.save(db)
    if stats["tokens_short"]:
        log.info("price recheck: %d deal(s) held back, Keepa tokens ~%.0f",
                 stats["tokens_short"], budget.available(now) or 0.0)
    return stats


def _run_critic(db: DB, cfg: AppConfig, parser, now: datetime) -> dict[str, Any]:
    """Deal critic: a stronger model judges whether each about-to-publish
    deal's claimed regular price is honest. An implausible verdict adds the
    'suspect_msrp' flag, which the publish gate excludes (configurable).
    One verdict per deal; a price change clears it for re-judging (dedupe).
    """
    from . import rank
    from .dedupe import _DEAL_FIELDS
    from .http_client import extract_asin
    from .parse.claude_client import critique_deals

    stats = {"judged": 0, "suspect": 0, "reused": 0, "calls": 0}
    if parser is None or not cfg.critic_enabled or not cfg.critic_model:
        return stats
    stored = db.all_deals()

    def asin(deal):
        store = str(deal.get("store") or "").lower()
        if store and not store.startswith("amazon"):
            return ""  # an ASIN on another retailer's deal is not reusable
        return str(deal.get("asin") or extract_asin(
            deal.get("canonical_url") or "")).upper()

    def condition_class(deal):
        conditions = set(deal.get("conditions") or [])
        for condition in ("open_box", "refurbished", "used"):
            if condition in conditions:
                return condition
        return "new"

    by_asin = {}
    for deal in stored:
        identity = asin(deal)
        if identity:
            by_asin.setdefault(identity, []).append(deal)

    def reusable(deal):
        identity = asin(deal)
        reg = deal.get("regular_price")
        if not identity or not reg or deal.get("final_price") is None:
            return None
        for other in by_asin.get(identity, []):
            if other["dedupe_key"] == deal["dedupe_key"]:
                continue
            verdict = other.get("critic")
            basis = other.get("regular_price")
            if not verdict or not basis or basis <= 0:
                continue
            if condition_class(other) != condition_class(deal):
                continue
            if abs(reg - basis) > basis * 0.02 + 1e-9:
                continue
            if (verdict.get("plausible")
                    and float(verdict.get("confidence") or 0.0) < 0.7
                    and not verdict.get("rechecked")):
                continue
            return {**verdict, "reason": str(verdict.get("reason", ""))[:120],
                    "reused_from": other["dedupe_key"]}
        return None

    def save(deal, verdict, *, reused=False):
        verdict = dict(verdict)
        if deal.get("critic"):
            verdict["rechecked"] = True
        stats["reused" if reused else "judged"] += 1
        payload = {k: deal.get(k) for k in _DEAL_FIELDS if k in deal}
        payload["critic"] = verdict
        if not verdict.get("plausible", True):
            stats["suspect"] += 1
            flags = list(payload.get("flags") or [])
            if "suspect_msrp" not in flags:
                flags.append("suspect_msrp")
            payload["flags"] = flags
            log.warning("deal critic flagged %s as suspect MSRP: %s",
                        deal["dedupe_key"], verdict.get("reason", ""))
        db.update_deal(deal["dedupe_key"], payload=payload)
        deal["critic"] = verdict  # available for reuse by later batches

    pending = []
    for deal in stored:
        if deal.get("status") != "alive":
            continue
        prior = deal.get("critic")
        if prior and not (prior.get("plausible")
                          and float(prior.get("confidence") or 0.0) < 0.7
                          and not prior.get("rechecked")):
            continue
        ok, _reason = rank.is_publishable(deal, cfg)
        if not ok:
            continue
        verdict = reusable(deal)
        if verdict is not None:
            save(deal, verdict, reused=True)
        else:
            pending.append(deal)

    size = max(1, cfg.critic_batch_size)
    calls_before = getattr(parser, "critic_calls", 0)
    shadow_batches = []
    shadow_slots = (max(0, cfg.critic_shadow_batches_per_day
                       - db.critic_shadow_counts(now)["batches"])
                    if cfg.critic_shadow_model else 0)
    for start in range(0, len(pending), size):
        batch = []
        for deal in pending[start:start + size]:
            verdict = reusable(deal)
            if verdict is not None:
                save(deal, verdict, reused=True)
            else:
                batch.append(deal)
        if not batch:
            continue
        try:
            verdicts = critique_deals(parser, batch)
        except SpendCapReached:
            log.warning("spend cap reached in deal critic; remaining deals "
                        "stay unjudged and retry next run")
            break
        for deal, verdict in zip(batch, verdicts):
            if verdict is not None:
                save(deal, verdict)
        if len(shadow_batches) < shadow_slots and any(v is not None for v in verdicts):
            shadow_batches.append((batch, verdicts))
    stats["calls"] = getattr(parser, "critic_calls", 0) - calls_before
    # Every real batch takes priority over the optional experiment.
    for batch, verdicts in shadow_batches:
        try:
            shadow = critique_deals(parser, batch, shadow=True, reserve_shadow=lambda:
                db.start_critic_shadow_batch(now, cfg.critic_shadow_batches_per_day))
        except SpendCapReached:
            break
        db.record_critic_shadow(now, verdicts, shadow)
    return stats


def _mark_shared_links(pairs) -> None:
    """A link (as posted or resolved) that two or more distinct products of
    one post hold is a store or sale page naming none of them: each deal
    holding it is marked link_shared (no photo inside that link, no post
    image). Holders are every deal the parse read in the post
    (RawPost.parsed, before evidence drops): a dropped item still shares."""
    from .product_images import link_identity

    holders: dict[tuple[int, str], set[str]] = {}   # (post, link) -> products

    def hold(post, item: str, urls) -> set[str]:
        mine = {link_identity(u) for u in urls} - {""}
        for link in mine:
            holders.setdefault((id(post), link), set()).add(util.normalize_item(item))
        return mine

    links = []
    for post, extracted in pairs:
        for item, link in post.parsed:
            hold(post, item, [link])
        links.append(hold(post, extracted.item,
                          [extracted.link_as_posted, extracted.product_url]))
    for (post, extracted), mine in zip(pairs, links):
        extracted.link_shared = any(len(holders[id(post), link]) > 1 for link in mine)


#: Wall-clock budget for resolving product links in one run.
_RESOLVE_BUDGET_S = 60.0


def _resolve_product_urls(pairs, fetcher, cfg: AppConfig, deadline: float) -> None:
    """Resolve redirect/affiliate chains to canonical retailer URLs (budget-aware).

    The source's original link stays on the sighting for credit; only
    product_url is canonicalized. The product link as the post gave it is
    kept on link_as_posted (stored on the deal too): the post's photos are
    keyed by it (RawPost.photos.in_link).
    """
    from .http_client import canonicalize_url, resolve_redirects

    # Best effort, bounded: past _RESOLVE_BUDGET_S the rest keep their link
    # canonicalized as posted (live wart: three slow stores ate 5.5 min).
    stop_at = time.monotonic() + _RESOLVE_BUDGET_S
    for _post, extracted in pairs:
        if not extracted.link_as_posted:
            extracted.link_as_posted = extracted.product_url
        if not extracted.product_url:
            continue
        if time.monotonic() > min(deadline - 30, stop_at):
            extracted.product_url = canonicalize_url(extracted.product_url)
            continue
        try:
            extracted.product_url = resolve_redirects(
                fetcher, extracted.product_url, cfg.redirect_max_hops,
                deadline=deadline - 15)  # one chain can never eat the budget
        except Exception as e:
            log.debug("redirect resolution failed for %s: %s", extracted.product_url, e)
            extracted.product_url = canonicalize_url(extracted.product_url)


def run(*, fast_lane: bool = False, db_path: str = "state.db",
        config_path: str | None = None, dry_run_telegram: bool | None = None,
        transport=None, sources_override: list[SourceConfig] | None = None,
        skip_validation: bool = False, now: datetime | None = None,
        claude_client=None) -> dict[str, Any]:
    """One pipeline run. Returns a stats summary dict."""
    from .dedupe import upsert_extracted
    from .http_client import PoliteFetcher
    from .publish.admin_alerts import check_and_alert
    from .publish.email_digest import maybe_send_digest
    from .publish.members import send_member_alerts
    from .publish.paywall import publish_full
    from .publish.newsletter import send_confirmations, send_daily_newsletter
    from .publish.site import health_markdown, render_site
    from .publish.telegram_pub import send_alerts
    from .savings import compute_savings
    from .verify import (mark_expired_dead, mark_unconfirmed_dead,
                         post_marks_dead, refresh_tiers)

    cfg = load_config(config_path)
    now = now or util.now_utc()
    started = time.monotonic()
    deadline = started + cfg.run_budget_s
    run_key = _run_key(now, fast_lane)
    kind = "fast" if fast_lane else "hourly"

    db = DB(db_path)
    db.start_run(run_key, kind, now)
    fetcher = PoliteFetcher(db, cfg, transport=transport)
    summary: dict[str, Any] = {"run_key": run_key, "kind": kind}
    summary["deaths_by_reason"] = {}
    recorded_deaths = {(d["dedupe_key"], d.get("died_at"), d.get("death"))
                       for d in db.all_deals(status="dead")}

    def record_deaths() -> None:
        # Include Woot deaths during fetch as well as expiry/Keepa deaths.
        for d in db.all_deals(status="dead"):
            stamp = (d["dedupe_key"], d.get("died_at"), d.get("death"))
            if stamp not in recorded_deaths:
                recorded_deaths.add(stamp)
                reason = d.get("death") or "unknown"
                counts = summary["deaths_by_reason"]
                counts[reason] = counts.get(reason, 0) + 1

    try:
        # ---- sources ----
        if sources_override is not None:
            sources = sources_override  # caller's exact order, never rotated
        else:
            sources = _load_effective_sources(
                db, cfg, fetcher, now,
                validate_if_missing=not skip_validation,
                deadline=deadline - _RESERVE_FOR_TAIL_S)
            # Production fairness only: on the hourly, slow blogs lead so the
            # big fast-lane feeds don't eat the budget (see _order_for_run).
            sources = _order_for_run(sources, run_key, fast_lane)
        tz_by_source = {s.name: s.tz for s in sources}

        # ---- fetch ----
        posts, fetch_errors = _fetch_all(sources, fetcher, db, cfg, now,
                                         deadline, fast_lane)
        record_deaths()
        summary["items_fetched"] = len(posts)
        summary["source_errors"] = fetch_errors

        # ---- freshness / idempotency: parse only posts not yet content-hashed.
        # Posts are RECORDED only after the parser actually handled them, so a
        # spend-cap abort, the max-posts cap, or the run deadline leaves the
        # rest unrecorded — they retry next run instead of being lost. ----
        new_posts: list[RawPost] = []
        seen_this_batch: set[str] = set()
        for post in posts:
            h = post.content_hash
            if h in seen_this_batch or db.post_is_known(h):
                continue
            seen_this_batch.add(h)
            new_posts.append(post)
        new_posts = _interleave_by_source(new_posts)
        log.info("fetched %d items, %d new (content-hash)", len(posts), len(new_posts))

        # ---- parse (Claude; evidence-verified) ----
        # Daily ceiling: this run may spend at most what remains of the cap
        # in force for the current UTC day (a temporary boost, else the base).
        from .config import daily_spend_cap, spend_allowance
        day_cap = daily_spend_cap(cfg, now)
        spent_today = db.spend_usd_for_day(now)
        remaining_today, allowed_today = spend_allowance(cfg, now, spent_today)
        pacing_line = ""
        if cfg.spend_pacing:
            pacing_line = (f"paced allowance ${remaining_today:.4f} "
                           f"(allowed ${allowed_today:.4f} of ${day_cap:.2f} "
                           f"by {now.astimezone(timezone.utc):%H:%M} UTC, spent ${spent_today:.4f})")
            log.info("%s", pacing_line)
            summary["spend_pacing"] = {"allowance": remaining_today,
                                       "allowed": allowed_today, "day_cap": day_cap,
                                       "spent": spent_today}
        parser = None
        if remaining_today <= 0:
            log.warning("daily LLM spend cap ($%.2f) exhausted; parse skipped "
                        "this run (posts stay unrecorded and retry next run)",
                        day_cap)
        else:
            # Built once per run and shared: extraction, second opinions, and
            # the deal critic all draw on the same spend caps.
            parser = _build_parser(cfg, claude_client=claude_client,
                                   run_cap_usd=remaining_today)
        # The critic keeps its share of this run's allowance: extraction may
        # use the rest, and whatever it leaves also goes to the critic.
        run_cap = parser.run_cap_usd if parser is not None else 0.0
        if parser is not None and cfg.critic_enabled and cfg.critic_model:
            parser.run_cap_usd = run_cap * (1 - cfg.critic_reserve_share)
        # Parse stops _RESERVE_FOR_TAIL_S early: the critic, price re-checks,
        # and publishing must still run inside the budget on heavy runs.
        pairs, pstats = _parse_new_posts(
            new_posts, parser, cfg, now, tz_by_source,
            deadline - _RESERVE_FOR_TAIL_S, db=db)
        summary["parse"] = pstats
        summary["structured_parse"] = pstats.get("structured_parse", {
            "posts": 0, "deals": 0, "dropped_evidence": 0})
        processed = set(pstats.get("processed_hashes") or [])
        recorded = 0
        if pstats.get("parser") == "disabled":
            # No parser this run: record nothing, so these posts parse once a
            # key is configured (fetch-only runs stay idempotent via dedupe).
            pass
        else:
            for post in new_posts:
                if post.content_hash in processed and db.record_post(post, now):
                    recorded += 1
        summary["posts_new"] = recorded

        # ---- canonical retailer URLs ----
        _resolve_product_urls(pairs, fetcher, cfg, deadline)

        # ---- savings math + dedupe/upsert ----
        _mark_shared_links(pairs)
        deals_upserted = 0
        for post, extracted in pairs:
            sav = compute_savings(extracted)
            if sav.exclude_reason in ("in_store", "non_us"):
                continue  # excluded entirely (kept out of the DB by design)
            res = upsert_extracted(db, extracted, sav, post, cfg, now)
            deals_upserted += 1
            if post_marks_dead(post, extracted):
                if mark_unconfirmed_dead(db, db.get_deal(res["dedupe_key"]),
                                         "post_says_over", now):
                    counts = summary["deaths_by_reason"]
                    counts["post_says_over"] = counts.get("post_says_over", 0) + 1
                    recorded_deaths.add((res["dedupe_key"], util.iso(now), "post_says_over"))
        summary["deals_upserted"] = deals_upserted

        # ---- verify tiers + dead marking ----
        refresh_tiers(db, cfg, now)
        summary["marked_dead"] = mark_expired_dead(db, cfg, now)
        record_deaths()

        # One-time: product links stored as a retailer's bot wall (the
        # card's link on every lane; the photo passes retry them).
        _repair_botwall_urls(db)
        _repair_tracker_asins(db)

        # ---- photos for existing deals from image-granted sources ----
        # Fast lane only: after its small fetch and parse it has minutes to
        # spare, so the backfill runs every 10 minutes with the critic's
        # tail reserve untouched. The hourly's budget stays on the blogs.
        summary["images_cleared"] = (
            _check_stored_images(db, fetcher, deadline - _RESERVE_FOR_TAIL_S, now)
            if fast_lane else 0)
        summary["redirects"] = {"resolved": 0, "tried": 0}
        summary["post_links"] = {"tried": 0, "found": 0}
        summary["short_links"] = (
            _resolve_short_links(db, fetcher, deadline - _RESERVE_FOR_TAIL_S, cfg,
                                 stats=summary["redirects"],
                                 deals=rank.site_deals(db, cfg, now))
            if fast_lane else 0)
        summary["image_backfill"] = (
            _backfill_blog_images(db, fetcher, sources,
                                  deadline - _RESERVE_FOR_TAIL_S, cfg)
            if fast_lane else 0)
        # ---- photos from the retailer's own product page (non-Amazon) ----
        # Same fast-lane slack: one polite page fetch per photo-less deal
        # that links straight to a seller, for its og:image.
        summary["product_images"] = (
            _backfill_product_images(db, fetcher,
                                     deadline - _RESERVE_FOR_TAIL_S, cfg)
            if fast_lane else 0)

        # ---- deal critic (runs BEFORE publish so flagged deals never ship) ----
        if parser is not None:
            parser.run_cap_usd = run_cap
        summary["critic"] = _run_critic(db, cfg, parser, now)

        if fast_lane:
            _links_from_posts(db, fetcher, deadline - _RESERVE_FOR_TAIL_S, cfg,
                              deals=rank.site_deals(db, cfg, now),
                              stats=summary["post_links"])

        # ---- live price re-check (Amazon deals; moved price => dead) ----
        # The in-window photo-only Keepa calls ride the fast lane, which has
        # the slack; the hourly's tail is for the critic and publishing.
        summary["price_recheck"] = _recheck_prices(
            db, cfg, now, image_backfill=fast_lane,
            deadline=deadline - _RESERVE_FOR_TAIL_S / 2)
        record_deaths()
        log.info("deaths by reason: %s", summary["deaths_by_reason"])

        from .parse.extractor import recategorize_live_deals, recategorize_other
        recategorize_live_deals(db, parser, deadline - _RESERVE_FOR_TAIL_S / 2)
        summary["recategorized_other"] = recategorize_other(
            db, parser, deadline - _RESERVE_FOR_TAIL_S / 2,
            per_run=cfg.recategorize_other_per_run)

        # Total LLM spend this run: extraction + second opinions + critic
        # (+ the newsletter pick, added after it below).
        spend_total = (float(getattr(parser, "spend_usd", 0.0))
                       if parser is not None
                       else pstats.get("spend_usd", 0.0))

        # ---- publish ----
        # last_run lands in the public deals.json: no spend or model (the
        # operator's; run_stats keeps the spend).
        site_res = render_site(db, cfg, now, last_run={
            "run_key": run_key, "kind": kind,
            "items_fetched": len(posts), "posts_new": len(new_posts),
        })
        summary["published"] = site_res.get("published", 0)
        summary["link_mix"] = site_res.get("link_mix", {})
        # Source health is the operator's only: the run's GitHub Actions
        # summary (private to the repository), never the public site.
        step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if fast_lane:
            summary["photo_gaps"], photo_lines = _photo_gap_summary(
                [d for d in rank.site_deals(db, cfg, now) if d.get("status") == "alive"],
                summary["redirects"])
            log.info("%s", photo_lines.rstrip())
            if step_summary:
                try:
                    with open(step_summary, "a", encoding="utf-8") as fh:
                        fh.write(photo_lines + "\n")
                except OSError as e:
                    log.warning("photo summary not written: %s", e)
        if step_summary and site_res.get("health"):
            try:
                with open(step_summary, "a", encoding="utf-8") as fh:
                    fh.write(health_markdown(site_res["health"]))
            except OSError as e:
                log.warning("source health summary not written: %s", e)
        # The site got the teaser; members get the full payload through the
        # Worker. A failed upload is a warning, never a failed run.
        try:
            summary["paywall_publish"] = publish_full(
                site_res.get("full_payload") or {}, cfg)
        except Exception as e:
            log.warning("paywall publish failed: %s", e)
            summary["paywall_publish"] = False
        tg = send_alerts(db, cfg, now, dry_run=dry_run_telegram,
                         deadline=deadline)
        summary["telegram"] = tg
        # Paying members (Stripe) get the same new deals by email, bundled.
        summary["member_alerts"] = send_member_alerts(db, cfg, now)
        summary["digest_sent"] = maybe_send_digest(db, cfg, now)
        # Double opt-in: new sign-ups get their confirmation link every run
        # (small and capped, so it goes before the day's big send).
        summary["confirmations"] = send_confirmations(db, cfg, now,
                                                      deadline=deadline)
        # The free list's daily top: picked once a day by the parse model on
        # the shared parser (same caps); its spend joins this run's total.
        summary["newsletter"] = send_daily_newsletter(db, cfg, now,
                                                      deadline=deadline,
                                                      parser=parser)
        if parser is not None:
            spend_total = float(getattr(parser, "spend_usd", 0.0))
        summary["spend_usd"] = spend_total
        summary["spend_by_kind"] = {
            kind: dict(stats) for kind, stats in
            getattr(parser, "spend_by_kind", {}).items()
        }
        summary["extract_by_source"] = {
            name: dict(stats) for name, stats in
            getattr(parser, "extract_by_source", {}).items()
        }
        if step_summary:
            try:
                with open(step_summary, "a", encoding="utf-8") as fh:
                    fh.write(_spend_markdown(summary["spend_by_kind"],
                                             summary["extract_by_source"]))
                    if pacing_line:
                        fh.write(pacing_line + "\n")
            except OSError as e:
                log.warning("LLM spend summary not written: %s", e)
        summary["admin_warnings"] = check_and_alert(db, cfg, now,
                                                    items_fetched=len(posts),
                                                    kind=kind)

        # ---- retention + bookkeeping ----
        db.prune(now, posts_seen_days=cfg.posts_seen_days, deals_days=cfg.deals_days)
        db.finish_run(run_key, now=util.now_utc(),
                      items_fetched=len(posts), posts_new=recorded,
                      deals_parsed=pstats.get("deals_extracted", 0),
                      published=summary["published"],
                      spend_usd=spend_total)
        summary["elapsed_s"] = round(time.monotonic() - started, 1)
        log.info("run %s done in %.1fs: %s", run_key, summary["elapsed_s"],
                 {k: v for k, v in summary.items() if k != "source_errors"})
        return summary
    finally:
        fetcher.close()
        db.close()


def validate_only(*, db_path: str = "state.db", config_path: str | None = None,
                  transport=None) -> dict[str, Any]:
    """Live-validate all sources, write sources_report.md, persist the active set."""
    from .fetch.validate import save_effective_sources, validate_sources, write_report
    from .http_client import PoliteFetcher

    cfg = load_config(config_path)
    now = util.now_utc()
    db = DB(db_path)
    fetcher = PoliteFetcher(db, cfg, transport=transport)
    try:
        primary, backup = load_sources()
        result = validate_sources(primary, backup, fetcher, db, cfg, now)
        write_report(result, cfg, now)
        if result["active"]:
            save_effective_sources(db, result.get("effective", result["active"]), now)
        log.info("validated %d sources: %d active (sources_report.md written)",
                 len(result["rows"]), result["active_count"])
        return {"active_count": result["active_count"],
                "counts": result.get("counts", {})}
    finally:
        fetcher.close()
        db.close()
