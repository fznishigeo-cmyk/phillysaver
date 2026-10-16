"""Run orchestration: fetch -> parse -> savings -> dedupe/verify -> publish.

Hard budgets: 20 s/request and 90 s/source (enforced in the fetch layer),
8 min/run (enforced here). Idempotency: content-hashed posts_seen, UNIQUE
alert tokens, UNIQUE price-history rows — re-running the same hour adds
nothing and re-sends nothing.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from itertools import chain, zip_longest
from typing import Any
from urllib.parse import urljoin, urlsplit

from . import efficiency, rank, util
from .config import AppConfig, get_secret, load_config, load_sources
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
                  "| Source | Calls | USD | Deals | Publishable |", "|---|---:|---:|---:|---:|"])
    for name, stats in sorted(extract_by_source.items()):
        name = name.replace("|", "\\|").replace("\n", " ").replace("\r", " ")
        lines.append(f"| {name} | {stats['calls']} | {stats['usd']:.6f} | "
                     f"{stats.get('deals', 0)} | {stats.get('publishable', 0)} |")
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
                if (any(p.name == s.name for p in primary)
                        or (s.method == "email" and s.adapter == "newsletter")):
                    continue  # disabled/removed newsletter entry: drop from the run set
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
                    allowed_html=cur.allowed_html, fast_lane=cur.fast_lane,
                    site=cur.site, newsletter_from=cur.newsletter_from))
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

    The partner-email inbox always goes first, in both lanes: one quick read
    of our own inbox, and the freshest deals we get (operator, 2026-10-15:
    "we need deals that don't go stale").
    """
    inbox = [s for s in sources if s.method == "email"]
    sources = [s for s in sources if s.method != "email"]
    if fast_lane:
        return inbox + _rotate_for_run(sources, run_key)
    slow = [s for s in sources if not s.fast_lane]
    fast = [s for s in sources if s.fast_lane]
    return inbox + _rotate_for_run(slow, run_key) + fast


def _interleave_by_source(posts: list[RawPost]) -> list[RawPost]:
    """Round-robin posts across sources, keeping each source's own order.

    The parse loop stops at the spend cap, the max-posts cap, or the run
    deadline; with posts grouped by source, whoever is listed first takes the
    whole budget. Interleaved, every source lands a fair share per run.
    Partner emails lead (round-robin among themselves): a newsletter is a
    partner's freshest deals and must not wait behind a backlog (live
    2026-10-15: 304 posts left at the hourly deadline; operator: "we need
    deals that don't go stale").
    """
    def fair(group: list[RawPost]) -> list[RawPost]:
        queues: dict[str, list[RawPost]] = {}
        for p in group:
            queues.setdefault(p.source_name, []).append(p)
        return [p for p in chain.from_iterable(zip_longest(*queues.values()))
                if p is not None]

    return fair([p for p in posts if p.via_email]) + fair([p for p in posts if not p.via_email])


def _fetch_all(sources: list[SourceConfig], fetcher, db: DB, cfg: AppConfig,
               now: datetime, deadline: float, fast_lane: bool,
               feed_confirmed: dict[str, set[str]] | None = None,
               price_recheck: dict | None = None,
               store_checks: dict | None = None) -> tuple[list[RawPost], dict[str, str]]:
    from .fetch.base import run_source
    from .fetch.registry import get_adapter

    # Fetch (including each source's article enrichment) must stop with the
    # parse reserve intact. Passing this as the per-source run_deadline bounds
    # enrichment too — otherwise an enrichment-heavy blog runs to the full run
    # deadline and parse gets no time (live incident: 505 new posts, 1 parsed).
    fetch_deadline = deadline - _RESERVE_FOR_PARSE_S
    posts: list[RawPost] = []
    errors: dict[str, str] = {}
    newsletter_inbox = None
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
            if source.method == "email" and newsletter_inbox is not None:
                adapter = newsletter_inbox
            else:
                adapter = get_adapter(source, cfg, fast_lane=fast_lane)
                if source.method == "email":
                    newsletter_inbox = adapter
                    # Match against the whole registry, including partners whose
                    # paused/tripped web feeds are absent from this run's set.
                    primary, backup = load_sources()
                    configured = {s.name: s for s in primary + backup}
                    configured.update({s.name: s for s in sources})
                    adapter.sources = list(configured.values())
                    # Our private API shares the injected transport, but never
                    # the fetcher's body cache or partner crawl/enrichment.
                    adapter._http = fetcher._client
                    adapter._sleep, adapter._clock = fetcher._sleep, fetcher._clock
                    fetcher.newsletter_inbox = adapter
        except ValueError as e:
            errors[source.name] = f"no adapter: {e}"
            continue
        if source.adapter == "keepa" and price_recheck is not None and not price_recheck:
            # Keep the pull open through due checks -> extra pages -> idle
            # checks. Its new posts then enter the ordinary capped parser.
            def recheck(discover, source_deadline):
                price_recheck.update(_recheck_prices(
                    db, cfg, now, image_backfill=fast_lane, fetcher=fetcher,
                    deadline=source_deadline, overflow_discovery=discover,
                    rate_limited=adapter.rate_limited))
            adapter.recheck = recheck
        got, ok, err = run_source(source, adapter, fetcher, db, cfg, now,
                                  run_deadline=fetch_deadline)
        if store_checks is not None and source.adapter == "woot" and hasattr(adapter, "recheck_stats"):
            store_checks["woot"] = adapter.recheck_stats
        if hasattr(fetcher, "keepa_feed_usage") and hasattr(adapter, "token_usage"):
            fetcher.keepa_feed_usage.add(adapter.token_usage)
        if feed_confirmed is not None:
            for condition, keys in getattr(adapter, "feed_confirmed", {}).items():
                feed_confirmed[condition].update(keys)
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
                     deadline: float | None, db: DB | None = None,
                     candidate_counts: dict | None = None
                     ) -> tuple[list[tuple[RawPost, Any]], dict]:
    from .parse.extractor import parse_posts, prefilter_counts, prefilter_post_counts

    post_counts = (candidate_counts if candidate_counts is not None else
                   prefilter_post_counts(new_posts, cfg, deadline))

    stats = {"posts_sent": 0, "deals_extracted": 0, "deals_dropped_evidence": 0,
             "spend_usd": 0.0, "aborted_spend_cap": False, "parser": "disabled",
             "processed_hashes": [], "reused_posts": 0, "reused_deals": 0,
             "second_opinion": {"calls": 0, "deals_extracted": 0, "deals_passed_evidence": 0},
             "post_counts": post_counts,
             "prefilter": prefilter_counts(new_posts, post_counts)}
    if not new_posts or parser is None:
        for counts in post_counts.values():
            counts["evaluation_gap"] = "parser_unavailable"
        return [], stats
    stats["parser"] = parser.model
    try:
        pairs, pstats = parse_posts(new_posts, parser, cfg, now, tz_by_source,
                                    deadline=deadline, db=db,
                                    **({"post_counts": post_counts} if cfg.bounded_prefilter["enabled"] else {}))
    except SpendCapReached:
        log.warning("spend cap reached before parsing could start")
        return [], stats
    stats.update(pstats)
    return pairs, stats


#: Hours between live price re-checks of the same Amazon deal live in
#: config.yaml (keepa.recheck_*): fresh deals most often, older ones less,
#: used/open-box least (its recheck carries an offers scan, up to
#: verify.USED_RECHECK_TOKENS per ASIN instead of 1). Operator, 2026-10-12:
#: stay on the 20 token/min plan; prices do not move by the minute.
#: Retry window for a used deal whose offers scan answered without a
#: verdict.
_RETRY_USED_H = 6.0
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

    from .source_policy import image_granted
    granted = {s.name: s for s in sources if image_granted(s, cfg) and s.method != "email"}
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
        if "newsletter" in (deal.get("flags") or []):
            continue
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
            host = _permission_host(src, cfg)
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


def _restore_source_credit_links(db: DB, cfg: AppConfig) -> int:
    """Put back a source's own tracker link that a tracker repair replaced.

    Deals of America, DealMoon and Southern Savers keep their affiliate credit
    as a condition of their crawl permission; their tracker link is that credit.
    The 2026-10-13 tracker repair, ingest redirect resolution and the Amazon
    link-from-text fallback could swap it for the store link. While repair is
    off, any deal posted with such a tracker whose product link now points
    elsewhere gets the tracker back. Idempotent: a restored deal matches no more.
    """
    from .dedupe import _DEAL_FIELDS
    from .publish.site import is_competitor_tracker_host

    if cfg.affiliate_repair_competitor_trackers:
        return 0
    hosts = cfg.affiliate_competitor_tracker_hosts
    restored = 0
    for deal in db.all_deals():
        posted = str(deal.get("link_as_posted") or "")
        current = str(deal.get("canonical_url") or "")
        try:
            posted_host = (urlsplit(posted).hostname or "").lower()
            current_host = (urlsplit(current).hostname or "").lower()
        except ValueError:
            continue
        if (not posted or posted == current
                or not is_competitor_tracker_host(posted_host, hosts)
                or (current_host and is_competitor_tracker_host(current_host, hosts))):
            continue
        payload = {k: deal.get(k) for k in _DEAL_FIELDS if k in deal}
        payload["canonical_url"] = posted
        db.update_deal(deal["dedupe_key"], payload=payload, canonical_url=posted)
        restored += 1
    if restored:
        log.info("source credit: restored %d source tracker link(s)", restored)
    return restored


def _resolve_short_links(db: DB, fetcher, deadline: float | None,
                         cfg: AppConfig, *, stats: dict | None = None,
                         deals: list[dict[str, Any]] | None = None) -> int:
    """Resolve photo-less redirects and competitor trackers once, keeping identity.
    deals: the deals to consider (run() passes the site's, so the per-run
    cap is spent on deals visitors see); None = every deal."""
    from .dedupe import _DEAL_FIELDS
    from .http_client import extract_asin, is_challenge_url, resolve_redirects
    from .publish.site import (_deal_source_hosts, is_competitor_tracker_host,
                               keeps_source_tracker)

    resolved = tried = trackers_resolved = 0
    tracker_hosts = cfg.affiliate_competitor_tracker_hosts
    now = util.now_utc()
    show_dead = timedelta(hours=float(getattr(cfg, "show_dead_hours", 0) or 0))
    for deal in sorted(db.all_deals() if deals is None else deals,
                       key=lambda d: str(d.get("first_seen_at") or ""), reverse=True):
        if "newsletter" in (deal.get("flags") or []):
            continue
        if "link_resolve_tried" in (deal.get("flags") or []):
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
        if parts.scheme not in ("http", "https") or not host or is_challenge_url(link) \
                or keeps_source_tracker(link, cfg):
            continue
        # Off by default: a permitted source keeps its affiliate credit, and its
        # tracker is that credit (config affiliate.repair_competitor_trackers).
        tracker = cfg.affiliate_repair_competitor_trackers and \
            is_competitor_tracker_host(host, tracker_hosts)
        tentative_photo = deal.get("photo_asin") and not deal.get("asin")
        if ((deal.get("image_url") and not tentative_photo) or deal.get("link_shared")) and not tracker:
            continue
        if not tracker and host.removeprefix("www.") not in _SHORT_LINK_HOSTS:
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
        try:
            target_parts = urlsplit(target)
            target_host = (target_parts.hostname or "").lower().removeprefix("www.")
        except ValueError:
            target_host = ""
        changed = bool(target != link and target_host and target_parts.scheme in ("http", "https")
                       and not is_challenge_url(target))
        if changed and tracker:
            excluded = _deal_source_hosts() | set(_SHORT_LINK_HOSTS) | {"wowpriced.com"}
            changed = bool(target_host and not is_competitor_tracker_host(target_host, tracker_hosts)
                           and not any(target_host == h or target_host.endswith("." + h)
                                       for h in excluded)
                           and not any(_site_of(target) == _site_of(s.get("source_url") or "")
                                       for s in db.sightings_for(deal["id"])))
        if changed:
            payload["canonical_url"] = target
            if not deal.get("asin"):
                payload["asin"] = extract_asin(target)
                if payload["asin"]:
                    payload.update(asin_source="link", photo_asin="")
                    if tentative_photo:
                        payload["image_url"] = ""
            flags = [f for f in flags if f not in
                     ("product_image_tried", "image_backfill_tried")]
            resolved += 1
            trackers_resolved += int(tracker)
        payload["flags"] = flags
        db.update_deal(deal["dedupe_key"], payload=payload,
                       canonical_url=target if changed else None)
        if tried >= _SHORT_LINK_PER_RUN:
            break
    if stats is not None:
        stats.update(resolved=resolved, tried=tried)
        stats["trackers_resolved"] = trackers_resolved
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
        if "newsletter" in (deal.get("flags") or []):
            continue
        tentative_photo = deal.get("photo_asin") and not deal.get("asin")
        if deal.get("status") != "alive" or (deal.get("image_url") and not tentative_photo) or \
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
            payload.update(asin=asin, asin_source="link", photo_asin="", canonical_url=canonical)
            if tentative_photo:
                payload["image_url"] = ""
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
    from .source_policy import sources_for_url, source_image_allowed

    found = tried = 0
    show_dead = timedelta(hours=float(getattr(cfg, "show_dead_hours", 0) or 0))
    now = util.now_utc()
    candidates = []
    for deal in db.all_deals():
        if "newsletter" in (deal.get("flags") or []):
            continue
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
                or any(t in host for t in _PRODUCT_IMAGE_SKIP_HOSTS) \
                or sources_for_url(link):
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
        if (res.status == 200 and res.body and not res.blocked_by_robots
                and not sources_for_url(res.final_url or link)):
            page = res.body.decode("utf-8", errors="replace")
            # First declared share image that is not junk; a "//cdn/..."
            # URL is resolved to https, attribute entities are decoded.
            cands = (unescape(c) if c.startswith("http") else "https:" + unescape(c)
                     for pat in _PRODUCT_IMAGE_META for c in pat.findall(page))
            image = next((c for c in cands
                          if not urlsplit(c).path.lower().endswith(".svg")
                          and not _PRODUCT_IMAGE_JUNK.search(c)
                          and source_image_allowed(c, deal.get("sightings") or [], cfg)), "")
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
        if "newsletter" in (deal.get("flags") or []):
            continue
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
                    deadline: float | None = None, *, fetcher=None,
                    overflow_discovery=None, rate_limited=False) -> dict[str, Any]:
    """Budgeted volatility checks, correcting plain new prices in-place.

    Published Buy Box picks and budgeted surplus precede ordinary cadence
    lanes. Legacy visibility scheduling remains available with volatility off.
    Discovery precedes bounded hidden recovery and idle checks; photos use
    the remainder. Every batch preserves the deal lane's token reserve.
    """
    from datetime import timedelta

    from . import rank
    from .keepa_rechecks import History, Schedule, record_price_check
    from . import buybox
    from .dedupe import _DEAL_FIELDS, reprice_existing
    from .fetch.keepa import reprice_post
    from .keepa_budget import (MIN_FOR_DEAL_QUERY, MIN_FOR_IMAGE_BACKFILL,
                               MIN_FOR_RECHECK, KeepaBudget, run_gap_forecast)
    from .verify import (KEEPA_BATCH, KEEPA_OFFERS_BATCH, USED_RECHECK_TOKENS,
                         confirmation_coverage, is_amazon_deal, is_used_deal,
                         _live_new_price, _price_hidden_by_promo,
                         _keepa_since_price, keepa_time, keepa_observed_at,
                         live_price_reading, price_confirmed_at, price_unconfirmed,
                         recheck_amazon_prices, woot_offer_url)
    from .publish.site import PICKS_META, WALL_META

    _revive_used_price_moved(db, now)
    stats = {"checked": 0, "dead": 0, "image_backfill": 0, "tokens_short": 0,
             "used_checked": 0, "used_skipped_budget": 0, "used_off_wall": 0,
             "drift": {"within_1pct": 0, "higher_1_5pct": 0, "higher_over_5pct": 0,
                       "lower": 0, "promo_hidden": 0, "repriced": 0}, "planned": {}, "done": {}}
    stats["price_source"] = buybox.empty_summary()
    stats["buybox_top"] = dict.fromkeys(
        ("requested", "with_buybox", "repriced_up", "repriced_down", "unchanged", "tokens"), 0)
    stats["live_reads"] = {"forced": 0, "conditional": 0}
    if cfg.keepa_unique_asin_rechecks_enabled:
        stats["unique_asin_rechecks"] = dict.fromkeys(
            ("selected_consumers", "unique_asins", "duplicate_slots_avoided", "product_requests"), 0)

    def remembered(name):
        try:
            value = json.loads(db.meta_get(name) or "[]")
            return {str(k): i for i, k in enumerate(value)} if isinstance(value, list) else {}
        except (TypeError, ValueError):
            return {}

    picks, wall = remembered(PICKS_META), remembered(WALL_META)
    buybox_top = {k for k, position in wall.items() if position < cfg.keepa_buybox_top_n}
    visible = picks.keys() | wall.keys()
    inventory = db.all_deals(status="alive")
    history = History(db, cfg, now)
    schedule = Schedule(history, picks, wall)
    stats["run_gap"] = run_gap_forecast(db, cfg, now)
    gap = stats["run_gap"]["minutes"]
    budget = KeepaBudget.load(db)
    available = budget.available(now)
    surplus_reserve = max(MIN_FOR_DEAL_QUERY, cfg.keepa_recheck_surplus_reserve)
    surplus = (min(budget.overflow(gap, now) or 0, max(0, available - surplus_reserve))
               if cfg.keepa_recheck_surplus and available is not None else 0)
    reserve = surplus_reserve if surplus else MIN_FOR_DEAL_QUERY
    conditional_live = cfg.keepa_recheck_top_live_adaptive and not surplus
    plan = None
    if cfg.keepa_volatility.get("enabled", False):
        from .volatility import Plan
        plan = Plan(history, inventory, picks, wall, gap,
                    cadence_minutes=stats["run_gap"]["ema_minutes"], surplus=surplus)
        schedule.plan = plan
        schedule.enabled = True
    stats["yield"] = history.yield_stats
    base_hours = {}
    attempted = set()
    deadlines = {}

    def priority(d):
        return (deadlines[d["dedupe_key"]] if d["dedupe_key"] in deadlines else
                "suspect" if d.get("gone_suspect_at") else
                "top" if d["dedupe_key"] in picks else
                "wall" if d["dedupe_key"] in wall else "rest")
    stats["wall_schedule"] = {name: 0 for name in (
        "new", "used", "top_overlap", "cadence_new", "cadence_used", "retry_new", "retry_used")}

    def hidden_counts(rows):
        from .publish.price_labels import recovery_reason
        hidden = [d for d in rows if price_unconfirmed(d, cfg, now)
                  and rank.is_publishable(d, cfg, min_pct=min(cfg.min_pct, cfg.site_show_from_pct))[0]]
        reasons = {name: {"new": 0, "used": 0, "total": 0} for name in (
            "never_checked", "retry_window", "suspect", "cadence_not_due",
            "tried_this_run", "adaptive_requested_recently", "missing_asin",
            "current_price_never_confirmed", "used_hidden_recovery_due", "new_hidden_recovery_due")}
        for deal in hidden:
            key, used = deal["dedupe_key"], is_used_deal(deal)
            urgent = overdue(deal)
            reason = ("tried_this_run" if key in attempted or history.attempted(key) else
                      "retry_window" if not urgent and tried_recently(deal, used) else
                      "suspect" if deal.get("gone_suspect_at") else
                      "adaptive_requested_recently" if not urgent and schedule.enabled and history.requested_recently(key) else
                      "never_checked" if last_check(deal) is None else
                      "cadence_not_due" if not urgent and in_cadence(deal) else recovery_reason(deal))
            reasons[reason]["used" if used else "new"] += 1
            reasons[reason]["total"] += 1
        return {"new": sum(not is_used_deal(d) for d in hidden),
                "used": sum(is_used_deal(d) for d in hidden),
                "total": len(hidden), "reasons": reasons}

    def measurements():
        rows = db.all_deals(status="alive")
        stats["confirmed_age"] = {
            name: confirmation_coverage([d for d in rows if d["dedupe_key"] in keys], now)
            for name, keys in (("top_candidates", picks), ("wall", wall))}
        stats["confirmed_age"]["top_candidates"]["request_age"] = history.request_age(
            [d for d in rows if d["dedupe_key"] in picks])
        stats["confirmed_age"]["wall"]["request_age"] = history.request_coverage(
            [d for d in rows if d["dedupe_key"] in wall])
        if plan is not None:
            stats["token_plan"] = plan.report(rows)
            for name, keys in (("top_candidates", picks), ("wall", wall)):
                stats["confirmed_age"][name]["tiers"] = plan.coverage(
                    [d for d in rows if d["dedupe_key"] in keys])
        stats["hidden_unconfirmed"] = hidden_counts(rows)

    def tried_recently(deal: dict[str, Any], used: bool) -> bool:
        # An answered-but-inconclusive recheck waits its retry window: live
        # review finding, a used deal whose offers scan kept saying "no
        # opinion" was re-scanned (13 tokens) on every 10-minute run.
        tried = util.parse_iso(deal.get("recheck_tried_at") or "")
        wait = _RETRY_USED_H if used else _RETRY_NEW_H
        return tried is not None and now - tried < timedelta(hours=wait)

    def observed_check(deal):
        paid = util.parse_iso(deal.get("price_rechecked_at") or "")
        if deal.get("gone_suspect_at") or deal.get("death"):
            return paid  # keep the two-reading death rule and its interval
        if not is_used_deal(deal):
            return price_confirmed_at(deal)
        warehouse = util.parse_iso(deal.get("keepa_warehouse_seen_at") or "")
        since = util.parse_iso(deal.get("price_since") or "")
        valid = [at for at in (paid, warehouse) if at is not None and at <= now
                 and (since is None or at >= since)]
        return max(valid) if valid else None

    def last_check(deal):
        if plan is not None and not (deal.get("gone_suspect_at") or deal.get("death")):
            return plan.last_check(deal)
        return observed_check(deal)

    def cadence(deal):
        seen = util.parse_iso(deal.get("first_seen_at") or "")
        fresh = seen is not None and now - seen < timedelta(hours=cfg.keepa_recheck_fresh_window_h)
        every = (cfg.keepa_recheck_used_h if is_used_deal(deal) else
                 cfg.keepa_recheck_fresh_h if fresh else cfg.keepa_recheck_new_h)
        if not is_used_deal(deal):
            if cfg.keepa_recheck_top_h and deal["dedupe_key"] in picks:
                every = min(every, cfg.keepa_recheck_top_h)
            if cfg.keepa_recheck_wall_h and deal["dedupe_key"] in wall:
                every = min(every, cfg.keepa_recheck_wall_h)
        if deal.get("gone_suspect_at"):
            every = _CONFIRM_GONE_H
        base_hours[deal["dedupe_key"]] = every
        return schedule.cadence(deal, priority(deal), every)

    def in_cadence(deal):
        last = last_check(deal)
        return last is not None and 0 <= (now - last).total_seconds() / 3600 < cadence(deal)

    def overdue(deal):
        key = deal["dedupe_key"]
        if not schedule.enabled or key not in visible:
            return False
        name = "top" if key in picks else "wall"
        last = last_check(deal)
        # A suspect's second reading must still wait its full hour.
        if deal.get("gone_suspect_at") and last is not None and now - last < timedelta(hours=_CONFIRM_GONE_H):
            return False
        if plan is not None and deal.get("recheck_tried_at"):
            last = observed_check(deal)  # 092: inconclusive top answers cannot suppress deadlines
        at = last or util.parse_iso(deal.get("first_seen_at") or "")
        return at is None or (now - at).total_seconds() / 3600 >= schedule.max_age(deal, name)

    def idle_eligible(deal):
        last = last_check(deal)
        if plan is not None:
            # Free discoveries can still avoid an early duplicate, but they
            # never enter the paid-request coverage metric.
            observed = observed_check(deal)
            last = max((at for at in (last, observed) if at is not None), default=None)
            return last is None or (now - last).total_seconds() / 3600 >= max(
                cfg.keepa_recheck_interval_minutes / 60, plan.target(deal) - gap / 60 *
                max(1, float(cfg.keepa_volatility.get("idle_lookahead_runs", 2))))
        return last is None or now - last >= timedelta(hours=cfg.keepa_recheck_idle_min_age_h)

    stats["hidden_unconfirmed_before"] = hidden_counts(inventory)
    stats["hidden_recovery"] = {"tokens": 0, "estimated_tokens": 0, "requested": 0,
                                "recovered": {"new": 0, "used": 0, "total": 0}}
    if not cfg.feature_flags.get("keepa"):
        measurements()
        return stats
    if plan is not None:
        plan.prepare_clicks()
    # Token budget carried over from the last Keepa response of any run;
    # each batch below updates it, and it is persisted at the end.
    stats["tokens"] = {"bucket_start": available, "bucket_end": None,
                       "capacity": budget.capacity(), "requested": 0,
                       "unused_per_hour_estimate": None, "overflow_estimate": None,
                       "surplus_granted": surplus,
                       "surplus_spent": {"buybox_top": 0, "wall_used": 0, "rest": 0}}
    used_limit = (max(0, available - MIN_FOR_DEAL_QUERY) * cfg.keepa_used_scan_budget_share
                  if available is not None else None)
    used_spent = 0
    due: list[dict[str, Any]] = []
    due_used: list[dict[str, Any]] = []
    photo: list[dict[str, Any]] = []
    idle: list[dict[str, Any]] = []
    recovery: list[dict[str, Any]] = []
    top_reads, surplus_reads = [], []
    for deal in inventory:
        if not deal.get("asin") or not is_amazon_deal(deal):
            continue
        if cfg.keepa_unique_asin_rechecks_enabled and not re.fullmatch(
                r"[A-Z0-9]{10}", str(deal["asin"]).strip().upper()):
            continue
        if deal.get("death") in {"post_says_over", "stated_expiry"} \
                and deal.get("gone_suspect_at") == util.iso(now):
            continue  # a post/expiry queues the first API reading for the next run
        used = is_used_deal(deal)
        key = deal["dedupe_key"]
        hidden = price_unconfirmed(deal, cfg, now)
        urgent = overdue(deal)
        requested = util.parse_iso(deal.get("buybox_requested_at") or "")
        box_due = (key in buybox_top and buybox.eligible(deal) and not _price_hidden_by_promo(deal)
                   and (requested is None or now - requested >= timedelta(minutes=cfg.keepa_buybox_interval_min)))
        if history.attempted(key) or (not box_due and not urgent and schedule.enabled and history.requested_recently(key)):
            continue
        condition = "used" if used else "new"
        if key in wall:
            stats["wall_schedule"][condition] += 1
            stats["wall_schedule"]["top_overlap"] += int(key in picks)
        if used and key not in visible:
            stats["used_off_wall"] += 1
            if not hidden and plan is None and not surplus:
                continue  # legacy off-wall used rows use free Warehouse confirmations
        every = cadence(deal)
        last = last_check(deal)
        in_window = last is not None and 0 <= (now - last).total_seconds() / 3600 < every
        if key in wall and in_window:
            stats["wall_schedule"]["cadence_" + condition] += 1
        if not box_due and not urgent and tried_recently(deal, used):
            if key in wall:
                stats["wall_schedule"]["retry_" + condition] += 1
            continue
        ok, _reason = rank.is_publishable(deal, cfg, require_critic=False,
                                         min_pct=min(cfg.min_pct, cfg.site_show_from_pct))
        if not ok:
            continue  # only spend tokens on deals that would publish
        suspect_wait = (deal.get("gone_suspect_at") and last is not None
                        and now - last < timedelta(hours=_CONFIRM_GONE_H))
        if box_due and not suspect_wait:
            top_reads.append(deal)
            continue
        # Overflow buys useful earlier checks, including off-wall search
        # inventory. Keep retry/death intervals and a minimum observation age.
        observed = observed_check(deal)
        if (surplus and not hidden and not suspect_wait
                and (observed is None or now - observed >= timedelta(hours=cfg.keepa_recheck_idle_min_age_h))):
            surplus_reads.append(deal)
        if urgent and (not hidden or rank.is_publishable(
                deal, cfg, min_pct=min(cfg.min_pct, cfg.site_show_from_pct))[0]):
            deadlines[key] = "top" if key in picks else "wall"
            (due_used if used else due).append(deal)
            continue
        if hidden:
            if not in_window and rank.is_publishable(
                    deal, cfg, min_pct=min(cfg.min_pct, cfg.site_show_from_pct))[0]:
                recovery.append(deal)
            continue  # hidden prices use only the bounded recovery lanes
        if plan is not None and key in plan.boosted and not hidden and not deal.get("gone_suspect_at"):
            in_window = False
        if in_window and key in visible and not deal.get("gone_suspect_at") \
                and idle_eligible(deal):
            idle.append(deal)
        if in_window and (used or not image_backfill or deal.get("image_url")
                          or len(photo) >= _IMAGE_BACKFILL_PER_RUN):
            continue
        (photo if in_window else due_used if used else due).append(deal)
    # Re-opened by the repair: dead until a fresh offers scan says alive.
    for deal in db.all_deals(status="dead"):
        if deal["dedupe_key"] in visible and deal.get("asin") and not deal.get("price_rechecked_at") \
                and _revivable(deal, now) and not tried_recently(deal, True) \
                and not history.attempted(deal["dedupe_key"]) \
                and not (schedule.enabled and history.requested_recently(deal["dedupe_key"])) \
                and rank.is_publishable(dict(deal, status="alive"), cfg, require_critic=False)[0]:
            due_used.append(deal)
            base_hours[deal["dedupe_key"]] = cfg.keepa_recheck_used_h
    # Build the legacy transport classes; the tier planner reorders them
    # across conditions below, retaining deadline and budget boundaries.
    def order(d):
        at = last_check(d)
        return util.iso(at) if at else ""

    due.sort(key=order)
    due_used.sort(key=order)
    idle.sort(key=order)

    def apply(deal: dict[str, Any], verdict: bool | None,
              observed: datetime, had_photo: bool = True, answered: bool = False) -> None:
        if verdict is None:
            # No price opinion, but a photo the record carried is still
            # kept (live wart: photo-only answers were thrown away). Keepa
            # answered yet could not decide: wait the retry window. A 429 or
            # transport miss (no answer) retries on the next pass.
            if answered or (not had_photo and (deal.get("image_url")
                    or deal.get("photo_lookup_asin") == deal.get("asin"))):
                payload = {k: deal.get(k) for k in _DEAL_FIELDS if k in deal}
                if answered:
                    payload["recheck_tried_at"] = util.iso(now)
                db.update_deal(deal["dedupe_key"], payload=payload)
            return
        stats["checked"] += 1
        if is_used_deal(deal):
            stats["used_checked"] += 1
        payload = {k: deal.get(k) for k in _DEAL_FIELDS if k in deal}
        payload["price_rechecked_at"] = util.iso(observed)
        if deal.get("status") == "dead":
            # A re-opened kill: alive again only on a live offer at the
            # price; otherwise the stamp ends its re-check, still dead.
            if verdict:
                payload["status"] = "alive"
                payload["died_at"] = ""
                payload["death"] = ""
                payload["price_since"] = util.iso(observed)
                payload["flags"] = [f for f in payload.get("flags") or []
                                    if f != "price_moved"]
                log.info("price recheck: %s is alive again (live offer at "
                         "the price)", deal.get("dedupe_key"))
            db.update_deal(deal["dedupe_key"], payload=payload,
                           status="alive" if verdict else None)
            return
        outcome = live_price_reading(payload, verdict, observed)
        if outcome == "suspect":
            stats["suspect"] = stats.get("suspect", 0) + 1
        elif outcome == "dead":
            stats["dead"] += 1
        db.update_deal(deal["dedupe_key"], payload=payload,
                       status="dead" if outcome == "dead" else None)

    def recovery_order(deal):
        value = rank.pick_score(deal, cfg, now, require_confirmation=False, allow_used=True)
        # Non-pick inventory (e.g. missing photos) follows scored candidates.
        return (value is None, -value if value is not None else -rank.score(deal, cfg, now),
                deal["dedupe_key"])

    # Due rechecks first (a nice-to-have photo never delays them); each
    # class waits while the bucket is below its bar, and a batch never
    # takes the deal lane's last MIN_FOR_DEAL_QUERY tokens — a used batch
    # (offers scan) is sized at its worst-case cost per deal and needs room
    # for at least one.
    groups = []
    classes = [("suspect", False), ("top", False), ("wall", False), ("rest", False),
               ("suspect", True),
               ("top", True), ("wall", True), ("rest", True)]
    for name, used, is_idle in ([(name, used, False) for name, used in classes] +
                               [("hidden", used, False) for used in (False, True)] +
                               [(name, used, True) for used in (False, True)
                                for name in ("top", "wall")]):
        rows = recovery if name == "hidden" else idle if is_idle else due_used if used else due
        group = [d for d in rows if is_used_deal(d) == used and (name == "hidden" or priority(d) == name)]
        label = ("idle_" if is_idle else "") + name + ("_used" if used else "_new")
        stats["planned"][label], stats["done"][label] = len(group), 0
        # A suspect may also be a top candidate; partition its batch so
        # update=0 is paid only for configured top candidates.
        for live in (True, False):
            batch_group = [d for d in group if bool(name != "hidden" and cfg.keepa_recheck_top_live
                           and d["dedupe_key"] in picks) == live]
            cost = (USED_RECHECK_TOKENS if used else 1) + int(live)
            need = max(MIN_FOR_RECHECK, MIN_FOR_DEAL_QUERY + cost)
            groups.append((batch_group, need, cost,
                           KEEPA_OFFERS_BATCH if used else KEEPA_BATCH, label, live, used, is_idle))
    # A deadline is a pass-wide priority, not merely a tie-breaker within
    # a lower class. Keep the original labels and the shared budget gates.
    urgent_groups = []
    remaining_groups = []
    for group, *options in groups:
        urgent = [d for d in group if d["dedupe_key"] in deadlines]
        if urgent:
            urgent_groups.append((urgent, *options))
        remaining_groups.append(([d for d in group if d["dedupe_key"] not in deadlines], *options))
    urgent_groups.sort(key=lambda g: (deadlines[g[0][0]["dedupe_key"]] != "top", g[6]))
    groups = urgent_groups + remaining_groups
    if plan is not None:
        # Freeze selection order independently of transport labels/modes.
        by_key = {d["dedupe_key"]: (d, options) for rows, *options in groups for d in rows}
        ranked = plan.order([d for d, _ in by_key.values()], deadlines)
        positions = {d["dedupe_key"]: i for i, d in enumerate(ranked)}
        def phase(d, options):
            return (0 if deadlines.get(d["dedupe_key"]) == "top" else
                    4 if options[-1] else 3 if options[3].startswith("hidden_") else
                    1 if d["dedupe_key"] in picks else 2)
        entries = sorted(by_key.values(), key=lambda row: (
            phase(*row), recovery_order(row[0]) if phase(*row) == 3 else (positions[row[0]["dedupe_key"]],)))
    stats["planned"]["photo"], stats["done"]["photo"] = len(photo), 0
    groups.append((photo, MIN_FOR_IMAGE_BACKFILL, 1, KEEPA_BATCH, "photo", False, False, False))
    # Reserve before each call, including lost answers; rerunning a run key
    # cannot buy another allowance from a fresh copy of the hidden queue.
    run_row = db.conn.execute(
        "SELECT run_key FROM run_stats WHERE started_at=? AND kind IN ('fast','hourly') LIMIT 1",
        (util.iso(now),)).fetchone()
    recovery_run = run_row["run_key"] if run_row else util.iso(now)
    try:
        recovery_spent = json.loads(db.meta_get("keepa_hidden_recovery") or "{}")
    except ValueError:
        recovery_spent = {}
    if not isinstance(recovery_spent, dict) or recovery_spent.get("run_key") != recovery_run:
        recovery_spent = {"run_key": recovery_run, "new": 0, "used": 0}
    rate_limited = rate_limited or recovery_spent.get("rate_limited", False)

    discovery_done = False
    labels = {}

    def discover():
        nonlocal discovery_done, rate_limited
        discovery_done = True
        if overflow_discovery is not None and not rate_limited and (
                deadline is None or time.monotonic() <= deadline):
            stats["overflow_discovery"] = overflow_discovery(budget)
            rate_limited = stats["overflow_discovery"].get("rate_limited", False)

    def planned_groups():
        # Plan each side of discovery against its actual remaining budget:
        # free feed confirmations and receipts can change the later selection.
        for lane in ("due", "hidden", "idle", "photo"):
            if lane == "hidden":
                discover()
            if rate_limited:
                return
            candidates = []
            source = [(d, list(groups[-1][1:])) for d in photo] if lane == "photo" else entries
            for deal, options in source:
                label, used, is_idle = options[3], options[5], options[6]
                own_lane = "photo" if label == "photo" else "idle" if is_idle else "hidden" if label.startswith("hidden_") else "due"
                if own_lane != lane or deal["dedupe_key"] in attempted:
                    continue
                if lane != "due":
                    deal = db.get_deal(deal["dedupe_key"])
                if lane == "hidden" and (deal["status"] != "alive" or not price_unconfirmed(deal, cfg, now)
                        or in_cadence(deal) or tried_recently(deal, used)):
                    continue
                if is_idle and not idle_eligible(deal):
                    continue
                candidates.append((deal, options))
                labels[deal["dedupe_key"]] = label
            request_candidates = candidates
            request_consumers = {}
            if cfg.keepa_unique_asin_rechecks_enabled:
                by_policy = {}
                for deal, options in candidates:
                    asin = str(deal.get("asin") or "").strip().upper()
                    if not re.fullmatch(r"[A-Z0-9]{10}", asin) or deal.get("final_price") is None:
                        continue
                    policy = asin, options[4], options[5], deadlines.get(deal["dedupe_key"]) == "top"
                    by_policy.setdefault(policy, []).append((deal, options))
                request_candidates = [rows[0] for rows in by_policy.values()]
                request_consumers = {rows[0][0]["dedupe_key"]: [d for d, _ in rows]
                                     for rows in by_policy.values()}
            selected, skipped = plan.batches(request_candidates, deadlines,
                available=budget.available(now), used_left=max(0, (used_limit or 0) - used_spent),
                seconds=max(0, deadline - time.monotonic()) if deadline is not None else float("inf"),
                overflow=budget.overflow(gap, now), recovery_left={
                    False: max(0, cfg.keepa_hidden_new_tokens_per_run - recovery_spent["new"]),
                    True: max(0, cfg.keepa_hidden_used_scans_per_run - recovery_spent["used"])})
            if request_consumers:
                selected = [([d for representative in rows for d in request_consumers[representative["dedupe_key"]]],
                             *options) for rows, *options in selected]
                skipped = len(candidates) - sum(len(rows) for rows, *_ in selected)
            stats["tokens_short"] += skipped
            selected_keys = {d["dedupe_key"] for group, *_ in selected for d in group}
            stats["used_skipped_budget"] += sum(options[5] and d["dedupe_key"] not in selected_keys
                                                for d, options in candidates)
            yield from selected

    def top_and_surplus_groups():
        # These barriers preserve value order even when transport modes differ.
        top_reads.sort(key=lambda d: wall[d["dedupe_key"]])
        surplus_reads.sort(key=lambda d: (util.iso(observed_check(d)), d["dedupe_key"]))
        lanes = [("buybox_top_new", top_reads),
                 ("surplus_wall_used", [d for d in surplus_reads if is_used_deal(d) and d["dedupe_key"] in wall]),
                 ("surplus_rest", [d for d in surplus_reads if not (is_used_deal(d) and d["dedupe_key"] in wall)])]
        for label, rows in lanes:
            modes = {}
            requested = set()
            left = min(budget.overflow(gap, now) or 0, max(0, (budget.available(now) or 0) - reserve))
            for deal in rows:
                used = is_used_deal(deal)
                live = bool(cfg.keepa_recheck_top_live and (deal["dedupe_key"] in picks or label == "buybox_top_new"))
                asin = str(deal.get("asin") or "").strip().upper()
                shared = cfg.keepa_unique_asin_rechecks_enabled and (asin, used, live) in requested
                if label.startswith("surplus_"):
                    # Tokens that would overflow anyway: no used-scan share here
                    # (it rations the regular lanes, which keep their full share).
                    cost = (USED_RECHECK_TOKENS if used else 1) + live
                    if not shared and cost > left:
                        continue
                    if not shared:
                        left -= cost
                requested.add((asin, used, live))
                modes.setdefault((used, live), []).append(deal)
            stats["planned"][label], stats["done"][label] = len(rows), 0
            for (used, live), rows in modes.items():
                cost = (USED_RECHECK_TOKENS if used else 1) + live + 2 * (label == "buybox_top_new")
                yield (rows, max(MIN_FOR_RECHECK, reserve + cost), cost,
                       KEEPA_OFFERS_BATCH if used else KEEPA_BATCH, label, live, used, False)
        yield from planned_groups() if plan is not None else groups

    for group, need, per_deal, per_call, label, live, used, is_idle in top_and_surplus_groups():
        is_photo = label == "photo"
        is_recovery = label.startswith("hidden_")
        is_surplus = label.startswith("surplus_")
        box_request = label == "buybox_top_new"
        if (is_recovery or is_idle) and not discovery_done:
            discover()
        if is_idle or is_photo or is_recovery:
            # Extra feed pages may have confirmed a price for free. Don't
            # overwrite that clock with a pre-discovery copy in either lane.
            group = [db.get_deal(d["dedupe_key"]) for d in group]
        if is_recovery:
            group = sorted((d for d in group if d["status"] == "alive"
                            and price_unconfirmed(d, cfg, now) and not in_cadence(d)
                            and not tried_recently(d, used)), key=recovery_order)
        if is_idle:
            group = [d for d in group if idle_eligible(d)]
        group = [d for d in group if d["dedupe_key"] not in attempted]
        consumers = {}
        if cfg.keepa_unique_asin_rechecks_enabled:
            by_asin = {}
            for deal in group:
                asin = str(deal.get("asin") or "").strip().upper()
                if re.fullmatch(r"[A-Z0-9]{10}", asin) and deal.get("final_price") is not None:
                    by_asin.setdefault(asin, []).append(deal)
            consumers = {rows[0]["dedupe_key"]: rows for rows in by_asin.values()}
            group = [rows[0] for rows in by_asin.values()]
        i = 0
        while i < len(group):
            remaining_count = sum(len(consumers.get(d["dedupe_key"], [d])) for d in group[i:])
            avail = budget.available(now)
            # Keep the ordinary top read when the premium would crowd out
            # its batch. An unknown bucket cannot authorize an extra charge.
            if box_request and (avail is None or avail - reserve < min(per_call, len(group) - i) * per_deal):
                box_request = False
                per_deal -= 2
                need = max(MIN_FOR_RECHECK, reserve + per_deal)
            overflow = budget.overflow(gap, now) if is_idle or is_surplus else None
            if (is_idle or is_surplus) and (overflow is None or overflow < per_deal):
                break
            call_seconds = plan.seconds_per_call(used, live) if plan is not None else 0
            margin = max(0, float(plan.options.get("batching", {}).get("time_margin_seconds", 5))) if plan is not None else 0
            if plan is None and (cfg.keepa_recheck_surplus or cfg.keepa_buybox_top_n):
                timing = cfg.keepa_volatility.get("batching", {})
                call_seconds = max(1, float(timing.get("offers_seconds" if used else "stats_seconds", 6 if used else 1)))
                call_seconds *= max(1, float(timing.get("latency_margin", 1.2)))
                margin = max(0, float(timing.get("time_margin_seconds", 5)))
            if deadline is not None and time.monotonic() + call_seconds + margin > deadline:
                stats["tokens_short"] += remaining_count
                log.info("price recheck: out of time with %d deals left; "
                         "they ride the next run", remaining_count)
                break
            if rate_limited or not budget.allows(max(need, reserve + per_deal), now):
                stats["tokens_short"] += remaining_count
                if used:
                    stats["used_skipped_budget"] += remaining_count
                break
            avail = budget.available(now)
            # An unknown bucket must first be learned from a new-price call
            # (normally the feed already supplied it). Never invent a used cap.
            if used_limit is None and avail is not None:
                used_limit = max(0, avail - MIN_FOR_DEAL_QUERY) * cfg.keepa_used_scan_budget_share
            size = per_call if avail is None else max(1, min(
                per_call, int((avail - reserve) / per_deal)))
            if is_recovery:
                limit = cfg.keepa_hidden_used_scans_per_run if used else cfg.keepa_hidden_new_tokens_per_run
                left = max(0, limit - recovery_spent["used" if used else "new"])
                recovery_reserve = max(reserve, cfg.keepa_reserve_tokens)
                size = min(size, left, max(0, int((avail - recovery_reserve) / per_deal)) if avail is not None else 0)
                if not size:
                    break
            if is_idle or is_surplus:
                size = min(size, int(overflow / per_deal))
            if used and not is_surplus:
                size = min(size, int(max(0, (used_limit or 0) - used_spent) / per_deal))
                if not size:
                    stats["used_skipped_budget"] += remaining_count
                    break
            remaining = group[i:]
            batch = schedule.batch(remaining, min(size, len(remaining)),
                                   label.removeprefix("idle_").rsplit("_", 1)[0],
                                   base_hours, last_check, per_deal) if not (plan is not None or is_photo or is_recovery or is_surplus or label == "buybox_top_new") else remaining[:size]
            group[i:] = batch + [d for d in remaining if d not in batch]
            requested_asins = len(batch)
            if consumers:
                batch = [deal for representative in batch for deal in consumers[representative["dedupe_key"]]]
            attempted.update(d["dedupe_key"] for d in batch)
            stats["tokens"]["requested"] += requested_asins * per_deal
            if is_recovery:
                recovery_spent["used" if used else "new"] += requested_asins
                db.meta_set("keepa_hidden_recovery", json.dumps(recovery_spent))
                stats["hidden_recovery"]["requested"] += requested_asins * per_deal
            if used and not is_surplus:
                used_spent += requested_asins * per_deal  # worst case, even on a lost response
            photo_less = {d["dedupe_key"] for d in batch if not d.get("image_url")}
            options = {"live": True} if live else {}
            if live:
                stats["live_reads"]["conditional" if conditional_live else "forced"] += requested_asins
                if conditional_live:
                    options["conditional_live"] = True
            if box_request:
                options["buybox"] = True
                stats["buybox_top"]["requested"] += requested_asins
                if get_secret("KEEPA_KEY"):
                    for deal in batch:
                        deal["buybox_requested_at"] = util.iso(now)
                        db.update_deal(deal["dedupe_key"], payload={k: deal[k] for k in _DEAL_FIELDS if k in deal})
            verdicts = recheck_amazon_prices(batch, cfg, budget=budget,
                                             used_offers=not is_photo, fetcher=fetcher, **options)
            for name, value in getattr(verdicts, "diagnostics", {}).items():
                stats["unique_asin_rechecks"][name] += value
            if plan is not None:
                from .keepa_rechecks import CallHistory
                plan.calls = CallHistory(db, util.now_utc())
            rate_limited = getattr(verdicts, "rate_limited", False)
            if is_photo:
                stats["image_backfill"] += len(batch)
            for deal in batch:
                deal_label = labels.get(deal["dedupe_key"], label)
                original = dict(deal)
                verdict = verdicts.get(deal["dedupe_key"])
                if deal["dedupe_key"] in verdicts:
                    stats["done"][deal_label] += 1
                product = getattr(verdicts, "products", {}).get(deal["dedupe_key"])
                observed = keepa_observed_at(product, now, used=is_used_deal(deal))
                shopper = (buybox.fresh_reading(deal, product, cfg, now)
                           if product is not None and deal["dedupe_key"] in buybox_top else None)
                if shopper is not None:
                    observed = shopper[1]
                    stats["buybox_top"]["with_buybox"] += 1
                previous = observed_check(deal)
                regressed = previous is not None and observed < previous
                if (is_recovery or price_unconfirmed(original, cfg, now)) and product is not None:
                    stamp = product["stats"]["lastBuyBoxUpdate"] if shopper else product.get("lastUpdate")
                    offers = used and isinstance(product.get("offers"), list)
                    if offers:
                        product_stats = product.get("stats")
                        stamp = product_stats.get("lastOffersUpdate") if isinstance(product_stats, dict) else None
                    at = keepa_time(stamp)
                    hide = cfg.keepa_show_max_unconfirmed_used_h if used else cfg.keepa_show_max_unconfirmed_h
                    regressed = (regressed or at is None or at > now
                                 or _keepa_since_price(stamp, deal) is not True
                                 or (offers and product.get("offersSuccessful") is False)
                                 or now - observed > timedelta(hours=hide))
                if regressed:
                    verdict = None  # never replace newer paid/feed evidence with older data
                if product is not None:
                    buybox.measure(stats["price_source"], original, product, verdict)
                if shopper is not None and not regressed:
                    from .savings import exceeds_price_tolerance
                    verdict = not exceeds_price_tolerance(shopper[0], deal["final_price"], cfg.keepa_recheck_tolerance_pct)
                if product is not None and not is_used_deal(deal) and not regressed:
                    live_price = shopper[0] if shopper else _live_new_price(product)
                    shown = deal.get("final_price")
                    if _price_hidden_by_promo(deal):
                        stats["drift"]["promo_hidden"] += 1
                    elif live_price is not None and shown is not None and verdict is not None:
                        drift = (live_price - shown) * 100 / shown if shown else (0 if live_price == 0 else float("inf"))
                        bucket = ("within_1pct" if abs(drift) <= 1 + 1e-9 else
                                  "lower" if drift < 0 else
                                  "higher_1_5pct" if drift <= 5 + 1e-9 else "higher_over_5pct")
                        stats["drift"][bucket] += 1
                    post = reprice_post(product, deal, cfg, now, buybox=shopper is not None)
                    if post is not None and reprice_existing(db, deal, post, cfg, now, observed_at=observed):
                        stats["drift"]["repriced"] += 1
                        verdict = True  # a moved but corrected price is live, never gone
                    if shopper is not None:
                        direction = ("repriced_up" if deal["final_price"] > original["final_price"] else
                                     "repriced_down" if deal["final_price"] < original["final_price"] else "unchanged")
                        stats["buybox_top"][direction] += 1
                if product is not None and not regressed and buybox.eligible(deal):
                    buybox.remember(deal, product, observed, now=now)
                    from . import checkout_prices
                    checkout_prices.remember(deal, product, cfg, now)
                apply(deal, verdict, observed,
                      had_photo=deal["dedupe_key"] not in photo_less,
                      answered=deal["dedupe_key"] in verdicts)
                cost, estimated = getattr(verdicts, "costs", {}).get(deal["dedupe_key"],
                    (per_deal, per_deal) if deal["dedupe_key"] in verdicts else (0, 0))
                if box_request:
                    stats["buybox_top"]["tokens"] += cost
                if surplus and (box_request or is_surplus):
                    category = "buybox_top" if box_request else "wall_used" if label == "surplus_wall_used" else "rest"
                    spent = stats["tokens"]["surplus_spent"]
                    spent[category] += min(cost, max(0, surplus - sum(spent.values())))
                current = db.get_deal(deal["dedupe_key"])
                if verdict is True and product is not None and original["status"] == "alive":
                    record_price_check(db, cfg, current, product, now, shopper=shopper)
                if is_recovery:
                    stats["hidden_recovery"]["tokens"] += cost
                    stats["hidden_recovery"]["estimated_tokens"] += estimated
                    if current["status"] == "alive" and not price_unconfirmed(current, cfg, now) \
                            and rank.is_publishable(current, cfg, min_pct=min(cfg.min_pct, cfg.site_show_from_pct))[0]:
                        stats["hidden_recovery"]["recovered"]["used" if used else "new"] += 1
                        stats["hidden_recovery"]["recovered"]["total"] += 1
                if not is_photo:
                    history.record(original, product, verdict, observed, deal_label, cost, estimated,
                                   dead=current["status"] == "dead" and original["status"] != "dead", live_request=live,
                                   buybox_request=box_request, shopper_price=shopper[0] if shopper else None,
                                   observation_group=getattr(verdicts, "observation_groups", {}).get(deal["dedupe_key"]),
                                   request_tokens=getattr(verdicts, "request_costs", {}).get(deal["dedupe_key"]))
            found = sum(1 for d in batch
                        if d["dedupe_key"] in photo_less and d.get("image_url"))
            if photo_less:
                log.info("price recheck: %d of %d photo-less deal(s) got a "
                         "Keepa photo", found, len(photo_less))
            i += requested_asins
    budget.save(db)
    if rate_limited:
        recovery_spent["rate_limited"] = True
        db.meta_set("keepa_hidden_recovery", json.dumps(recovery_spent))
    stats["rate_limited"] = rate_limited
    stats["tokens"]["bucket_end"] = budget.available(now)
    stats["tokens"]["capacity"] = budget.capacity()
    overflow = budget.overflow(gap, now)
    stats["tokens"]["overflow_estimate"] = overflow
    stats["tokens"]["unused_per_hour_estimate"] = (
        round(overflow * 60 / gap, 2) if overflow is not None else None)
    measurements()
    log.info("price recheck plan/done: %s", ", ".join(
        f"{name}={planned}/{stats['done'][name]}" for name, planned in stats["planned"].items()))
    if stats["tokens_short"]:
        log.info("price recheck: %d deal(s) held back, Keepa tokens ~%.0f",
                 stats["tokens_short"], budget.available(now) or 0.0)
    return stats


def _run_critic(db: DB, cfg: AppConfig, parser, now: datetime, *,
                audit_stats: dict | None = None, deadline: float | None = None) -> dict[str, Any]:
    """Audit publication candidates, then judge unresolved regular-price claims.

    Shadow preserves paid judging; enforce admits a current code basis or a
    critic verdict. Audit-owned rejections carry suspect_msrp and a reason.
    """
    from . import rank, legitimacy
    from .dedupe import _DEAL_FIELDS
    from .http_client import extract_asin
    from .parse.claude_client import critique_deals

    stats = {"judged": 0, "suspect": 0, "reused": 0, "calls": 0}
    audit_stats = audit_stats if audit_stats is not None else {}
    audit_stats.update(legitimacy.empty_stats(cfg))
    enabled = parser is not None and cfg.critic_enabled and cfg.critic_model
    stored = db.all_deals()
    tiered = cfg.critic_tiering["enabled"] and cfg.critic_enabled and bool(cfg.critic_model)
    high, fallback = set(), ""
    if tiered:
        from . import critic_tiering

        high = critic_tiering.high_visibility(db, cfg, now, stored)
        fallback = critic_tiering.fallback_reason(db, cfg)

    def needs_stronger(deal, verdict):
        return (tiered and isinstance(verdict, dict)
                and verdict.get("model") == cfg.critic_tiering["model"]
                and (fallback or deal["dedupe_key"] in high or not verdict.get("plausible")
                     or not critic_tiering.confident(verdict, cfg.critic_tiering["min_confidence"])))

    def asin(deal):
        store = str(deal.get("store") or "").lower()
        if store and not store.startswith("amazon"):
            return ""  # an ASIN on another retailer's deal is not reusable
        return str(deal.get("asin") or extract_asin(
            deal.get("canonical_url") or "")).upper()

    def condition_class(deal):
        from .conditions import normalize_conditions

        conditions = set(normalize_conditions(deal.get("conditions")))
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
            if needs_stronger(deal, verdict):
                continue
            basis = other.get("regular_price")
            if (not isinstance(verdict, dict) or type(verdict.get("plausible")) is not bool
                    or not basis or basis <= 0):
                continue
            if verdict.get("audit_fingerprint") and verdict["audit_fingerprint"] != legitimacy.fingerprint(other, cfg):
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
        verdict["audit_fingerprint"] = legitimacy.fingerprint(deal, cfg)
        if deal.get("critic"):
            verdict["rechecked"] = True
        stats["reused" if reused else "judged"] += 1
        payload = {k: deal.get(k) for k in _DEAL_FIELDS if k in deal}
        payload["critic"] = verdict
        if tiered:
            payload["flags"] = [f for f in payload.get("flags") or [] if f != "critic_tier_pending"]
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
        if tiered:
            deal["flags"] = payload.get("flags") or []
        legitimacy.compare(db, deal, verdict, cfg, now, audit_stats)

    pending = []
    for deal in stored:
        if deal.get("status") != "alive":
            continue
        probe = dict(deal, flags=[f for f in deal.get("flags") or [] if f != "suspect_msrp"])
        ok, _reason = rank.is_publishable(probe, cfg, require_critic=False,
                                         min_pct=min(cfg.min_pct, cfg.site_show_from_pct),
                                         allow_spotlight_price=True)
        if not ok:
            continue
        legitimacy.evaluate(db, deal, cfg, now, audit_stats)
        prior = deal.get("critic")
        valid_prior = legitimacy.settled_critic(prior)
        if valid_prior and not needs_stronger(deal, prior) and (not prior.get("audit_fingerprint")
                            or prior["audit_fingerprint"] == legitimacy.fingerprint(deal, cfg)):
            legitimacy.compare(db, deal, prior, cfg, now, audit_stats)
            continue
        if cfg.audit_mode == "enforce" and deal["audit"].get("critic_avoided"):
            continue
        if tiered and deal.get("regular_price") and deal.get("final_price") is not None:
            deal["flags"] = list(dict.fromkeys([*(deal.get("flags") or []), "critic_tier_pending"]))
            db.update_deal(deal["dedupe_key"], payload={k: deal.get(k) for k in _DEAL_FIELDS if k in deal})
        if not enabled:
            continue
        verdict = reusable(deal)
        if verdict is not None:
            save(deal, verdict, reused=True)
        else:
            pending.append(deal)

    if not enabled:
        return stats
    if tiered:
        calls_before = getattr(parser, "critic_calls", 0)
        # All unresolved deals are packed together after reuse, without holes
        # left by cached verdicts or code-supported audit candidates.
        remaining = []
        for deal in pending:
            verdict = reusable(deal)
            if verdict is not None:
                save(deal, verdict, reused=True)
            else:
                remaining.append(deal)
        stats["tiering"] = critic_tiering.run(
            db, cfg, parser, now, remaining, high, save, fallback=fallback, deadline=deadline)
        stats["tiering"]["escalated"] += critic_tiering.protect_promoted(
            db, cfg, parser, now, save, deadline=deadline)
        stats["calls"] = getattr(parser, "critic_calls", 0) - calls_before
        return stats
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
        remaining = [d for d in batch if d["audit"]["outcome"] == "needs_critic"]
        original = batch
        if cfg.audit_mode == "enforce":
            batch = remaining
            if not batch:
                legitimacy.record_batch(db, original, remaining, cfg, parser, now, audit_stats)
                continue
        try:
            if deadline is not None and time.monotonic() + 2 * cfg.request_timeout_s + 2 > deadline:
                log.warning("run deadline reached in deal critic; remaining deals "
                            "stay unjudged and retry next run")
                break
            verdicts = critique_deals(parser, batch)
        except SpendCapReached:
            log.warning("spend cap reached in deal critic; remaining deals "
                        "stay unjudged and retry next run")
            break
        legitimacy.record_batch(db, original, remaining, cfg, parser, now, audit_stats)
        for deal, verdict in zip(batch, verdicts):
            if verdict is not None:
                save(deal, verdict)
        if len(shadow_batches) < shadow_slots and any(v is not None for v in verdicts):
            shadow_batches.append((batch, verdicts))
    stats["calls"] = getattr(parser, "critic_calls", 0) - calls_before
    # Every real batch takes priority over the optional experiment.
    for batch, verdicts in shadow_batches:
        try:
            if deadline is not None and time.monotonic() + 2 * cfg.request_timeout_s + 2 > deadline:
                break
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
    from .publish.site import keeps_source_tracker
    from .photo_recovery import resolve_posts

    # Best effort, bounded: past _RESOLVE_BUDGET_S the rest keep their link
    # canonicalized as posted (live wart: three slow stores ate 5.5 min).
    stop_at = time.monotonic() + _RESOLVE_BUDGET_S
    for _post, extracted in pairs:
        if not extracted.link_as_posted:
            extracted.link_as_posted = extracted.product_url
        if getattr(_post, "via_email", False) or not extracted.product_url or keeps_source_tracker(extracted.product_url, cfg):
            continue  # a permitted source's tracker stays exactly as posted
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

    resolve_posts(pairs, fetcher, cfg, min(deadline - 30, stop_at))


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
    from . import ai_yield, source_audit
    from .parse.extractor import prefilter_counts, prefilter_post_counts
    from .verify import (mark_ended_post, mark_expired_dead, mark_unconfirmed_dead,
                         post_marks_dead, refresh_tiers)

    cfg = load_config(config_path)
    now = now or util.now_utc()
    started = time.monotonic()
    deadline = started + cfg.run_budget_s
    run_key = os.environ.get("DEALRADAR_RUN_KEY") or _run_key(now, fast_lane)
    kind = "fast" if fast_lane else "hourly"

    db = DB(db_path)
    db.accuracy_run_key = run_key
    repeated_fast_run = fast_lane and db.conn.execute(
        "SELECT 1 FROM run_stats WHERE run_key=? AND finished_at IS NOT NULL", (run_key,)).fetchone()
    db.start_run(run_key, kind, now)
    fetcher = PoliteFetcher(db, cfg, transport=transport)
    from .keepa_budget import TokenUsage
    fetcher.keepa_feed_usage = TokenUsage()
    summary: dict[str, Any] = {"run_key": run_key, "kind": kind}
    parser = None
    yield_spend_before = db.spend_usd_for_run(run_key)
    yield_counts = {"unique_new_public_deals": 0, "useful_price_updates": 0}
    audit_complete = run_complete = yield_recorded = False
    new_posts, sources = [], []
    pstats = {"post_counts": {}}
    processed = set()
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
        from . import clicks
        if clicks.enabled(cfg):
            summary["click_signal"] = clicks.ingest(db, cfg, now, fetcher, run_key, deadline=deadline)
        efficiency.start(db, now, run_key)
        db.backfill_products(cfg.product_entities_backfill_per_run, run_key=run_key)
        ai_yield.start(db, cfg, now, run_key)
        if db.meta_get("intake_since") is None:
            db.meta_set("intake_since", util.iso(now))
        yield_events_before = {r[0] for r in db.conn.execute(
            "SELECT event_key FROM ai_yield_events WHERE run_key=?", (run_key,))}
        # ---- sources ----
        if sources_override is not None:
            sources = sources_override  # baseline caller order, never rotated
        else:
            sources = _load_effective_sources(
                db, cfg, fetcher, now,
                validate_if_missing=not skip_validation,
                deadline=deadline - _RESERVE_FOR_TAIL_S)
            # Production fairness only: on the hourly, slow blogs lead so the
            # big fast-lane feeds don't eat the budget (see _order_for_run).
            sources = _order_for_run(sources, run_key, fast_lane)
        intake_history = source_audit.intake_history(db, cfg, now)
        eligible_sources = [s for s in sources if s.enabled and (not fast_lane or s.fast_lane)
                            and not db.is_tripped(s.name, now)]
        fetch_weights = source_audit.intake_weights(eligible_sources, intake_history, cfg)
        weighted_sources, overdue_sources = source_audit.intake_source_order(
            sources, fetch_weights, intake_history, db, cfg, now, fast_lane)
        summary["intake"] = {
            "mode": cfg.intake_mode, "weights": fetch_weights,
            "proposed_fetch_order": [s.name for s in weighted_sources],
            "fetch_order_changes": sum(a.name != b.name for a, b in zip(sources, weighted_sources)),
            "unique_public_by_source": {}, "public_category_mix": {},
        }
        if "unavailable" in intake_history:
            summary["intake"]["unavailable"] = intake_history["unavailable"]
        if cfg.intake_mode == "on":
            sources = weighted_sources
        tz_by_source = {s.name: s.tz for s in sources}

        # ---- fetch ----
        from . import non_amazon_freshness as freshness
        from .store_prices import recheck_store_prices
        before_freshness, before_scope = freshness.before_snapshot(db, cfg, now)
        summary["store_checks"] = recheck_store_prices(
            db, cfg, now, fetcher=fetcher, deadline=deadline - _RESERVE_FOR_PARSE_S,
            run_key=run_key)
        feed_confirmed = {"new": set(), "used": set()}
        early_recheck = {}
        posts, fetch_errors = _fetch_all(sources, fetcher, db, cfg, now,
                                         deadline, fast_lane, feed_confirmed, early_recheck,
                                         summary["store_checks"])
        rereads, source_checks = freshness.source_rechecks(
            db, cfg, sources, posts, fetcher, now, run_key,
            deadline=min(deadline - _RESERVE_FOR_PARSE_S / 2,
                         time.monotonic() + cfg.non_amazon_source_recheck_budget_s))
        # Rereads lead the same bounded/capped parse queue and retain its evidence gate.
        reread_urls = {(p.source_name, p.url) for p in rereads}
        posts = rereads + [p for p in posts if (p.source_name, p.url) not in reread_urls]
        inbox = getattr(fetcher, "newsletter_inbox", None)
        if inbox is not None:
            tz_by_source.update({s.name: s.tz for s in inbox.sources})
        record_deaths()
        summary["items_fetched"] = len(posts)
        summary["source_errors"] = fetch_errors
        db.audit_seen(posts, now)

        # ---- freshness / idempotency: parse only posts not yet content-hashed.
        # Posts are RECORDED only after the parser actually handled them, so a
        # spend-cap abort, the max-posts cap, or the run deadline leaves the
        # rest unrecorded — they retry next run instead of being lost. ----
        new_posts: list[RawPost] = []
        seen_this_batch: set[str] = set()
        for post in posts:
            h = post.content_hash
            if h in seen_this_batch or (db.post_is_known(h) and not post.price_recheck):
                continue
            seen_this_batch.add(h)
            new_posts.append(post)
        new_posts = _interleave_by_source(new_posts)
        # Evaluate the same observed posts in shadow. Only on substitutes the
        # weighted prefix; unselected hashes remain unknown for the next run.
        post_sources = {s.name: s for s in [*sources, *(inbox.sources if inbox is not None else [])]}
        post_weights = source_audit.intake_weights(
            [post_sources.get(name, SourceConfig(name=name, method="rss", url=""))
             for name in dict.fromkeys(p.source_name for p in new_posts)], intake_history, cfg)
        overdue_posts = source_audit.intake_due_posts(db, post_weights, cfg, now)
        weighted_posts = source_audit.intake_post_order(
            new_posts, post_weights, intake_history, cfg,
            [*overdue_posts, *(s for s in overdue_sources if s not in overdue_posts)])
        baseline_prefix = new_posts[:cfg.max_posts_per_run]
        summary["intake"].update(
            post_weights=post_weights,
            proposed_post_order=[p.content_hash for p in weighted_posts],
            proposed_post_sources=[p.source_name for p in weighted_posts],
            would_have_changed=sum(a != b for a, b in zip_longest(
                [p.content_hash for p in baseline_prefix], [p.content_hash for p in weighted_posts])),
        )
        parse_posts = weighted_posts if cfg.intake_mode == "on" else new_posts
        log.info("fetched %d items, %d new (content-hash)", len(posts), len(new_posts))
        for post in new_posts:
            mark_ended_post(db, post, now, cfg)
        record_deaths()

        candidate_counts = prefilter_post_counts(new_posts, cfg, deadline - _RESERVE_FOR_TAIL_S)
        pstats["post_counts"] = candidate_counts
        ai_yield.observe_prefilter(db, cfg, new_posts, candidate_counts, now,
                                  [*sources, *(inbox.sources if inbox is not None else [])])

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
        if repeated_fast_run:
            # A bounded parse leaves posts pending. Replaying this key must
            # not admit another chunk; both reads and verdicts retry next key.
            log.info("fast-lane run already finished; deferred parsing and judging wait for the next run key")
        elif remaining_today <= 0:
            log.warning("daily LLM spend cap ($%.2f) exhausted; parse skipped "
                        "this run (posts stay unrecorded and retry next run)",
                        day_cap)
        else:
            # Built once per run and shared: extraction, second opinions, and
            # the deal critic all draw on the same spend caps.
            parser = _build_parser(cfg, claude_client=claude_client,
                                   run_cap_usd=remaining_today)
            from .parse.claude_client import ClaudeParser

            if isinstance(parser, ClaudeParser):
                parser.bind_ledger(db, run_key)
        # The critic keeps its share of this run's allowance: extraction may
        # use the rest, and whatever it leaves also goes to the critic.
        run_cap = parser.run_cap_usd if parser is not None else 0.0
        if parser is not None and cfg.critic_enabled and cfg.critic_model:
            parser.run_cap_usd = run_cap * (1 - cfg.critic_reserve_share)
        # Parse stops _RESERVE_FOR_TAIL_S early: the critic, price re-checks,
        # and publishing must still run inside the budget on heavy runs.
        parse_stop = deadline - _RESERVE_FOR_TAIL_S
        if fast_lane:
            parse_stop = min(parse_stop, time.monotonic() + cfg.fast_lane_parse_budget_s)
        pairs, pstats = _parse_new_posts(
            parse_posts, parser, cfg, now, tz_by_source,
            parse_stop, db=db,
            **({"candidate_counts": candidate_counts} if cfg.bounded_prefilter["enabled"] else {}))
        if cfg.intake_mode == "on":
            pstats["post_counts"] = {**candidate_counts, **pstats.get("post_counts", {})}
            selected = {p.content_hash for p in parse_posts}
            for post in new_posts:
                if post.content_hash not in selected and post.content_hash in pstats["post_counts"]:
                    pstats["post_counts"][post.content_hash]["evaluation_gap"] = "intake_limit"
            pstats["prefilter"] = prefilter_counts(new_posts, pstats["post_counts"])
        summary["parse"] = pstats
        summary["second_opinion"] = pstats.get("second_opinion", {
            "calls": 0, "deals_extracted": 0, "deals_passed_evidence": 0})
        summary["prefilter"] = pstats.get("prefilter", prefilter_counts(new_posts, {}))
        summary["structured_parse"] = pstats.get("structured_parse", {
            "posts": 0, "deals": 0, "dropped_evidence": 0})
        if remaining_today <= 0:
            for counts in pstats.get("post_counts", {}).values():
                counts["evaluation_gap"] = "daily_cap"
        processed = set(pstats.get("processed_hashes") or [])
        recorded = 0
        if pstats.get("parser") == "disabled":
            # No parser this run: record nothing, so these posts parse once a
            # key is configured (fetch-only runs stay idempotent via dedupe).
            pass
        else:
            for post in new_posts:
                if not post.via_email and post.content_hash in processed and db.record_post(post, now):
                    recorded += 1
        summary["posts_new"] = recorded

        # ---- canonical retailer URLs ----
        _resolve_product_urls(pairs, fetcher, cfg, deadline)

        # ---- savings math + dedupe/upsert ----
        _mark_shared_links(pairs)
        deals_upserted = 0
        audit_outcomes = []
        for post, extracted in pairs:
            counts = pstats.get("post_counts", {}).get(post.content_hash)
            sav = compute_savings(extracted, allow_price_only=(
                post.source_name in rank.spotlight_sources(cfg)))
            if post.via_email:
                sav.flags.append("newsletter")
            if sav.exclude_reason in ("in_store", "non_us"):
                if counts is not None:
                    counts["other"] = counts.get("other", 0) + 1
                continue  # excluded entirely (kept out of the DB by design)
            res = upsert_extracted(db, extracted, sav, post, cfg, now)
            if post.via_email:
                from .dedupe import _DEAL_FIELDS
                stored = db.get_deal(res["dedupe_key"])
                if "newsletter" not in (stored.get("flags") or []):
                    payload = {k: stored.get(k) for k in _DEAL_FIELDS if k in stored}
                    payload["flags"] = list(stored.get("flags") or []) + ["newsletter"]
                    db.update_deal(res["dedupe_key"], payload=payload)
            audit_outcomes.append((post, res["dedupe_key"], extracted, sav))
            if counts is not None and not res["is_new_deal"]:
                counts["duplicate"] = counts.get("duplicate", 0) + 1
            deals_upserted += 1
            if post_marks_dead(post, extracted):
                if mark_unconfirmed_dead(db, db.get_deal(res["dedupe_key"]),
                                         "post_says_over", now, cfg):
                    counts = summary["deaths_by_reason"]
                    counts["post_says_over"] = counts.get("post_says_over", 0) + 1
                    recorded_deaths.add((res["dedupe_key"], util.iso(now), "post_says_over"))
        summary["deals_upserted"] = deals_upserted

        # ---- verify tiers + dead marking ----
        refresh_tiers(db, cfg, now, fetcher=fetcher)
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
        summary["redirects"] = {"resolved": 0, "tried": 0, "trackers_resolved": 0}
        summary["post_links"] = {"tried": 0, "found": 0}
        summary["source_credit_restored"] = _restore_source_credit_links(db, cfg)
        summary["short_links"] = (
            _resolve_short_links(db, fetcher, deadline - _RESERVE_FOR_TAIL_S, cfg,
                                 stats=summary["redirects"],
                                 deals=rank.site_deals(db, cfg, now))
            if fast_lane else 0)
        summary["image_backfill"] = (
            _backfill_blog_images(db, fetcher, sources,
                                  deadline - _RESERVE_FOR_TAIL_S, cfg)
            if fast_lane else 0)
        # Non-Amazon photos come from granted sources or existing official
        # API reads. Do not spend the photo pass on retailer/bot-wall pages.
        summary["product_images"] = 0

        # ---- deal critic (runs BEFORE publish so flagged deals never ship) ----
        if parser is not None:
            parser.run_cap_usd = run_cap
        summary["audit"] = {}
        summary["critic"] = _run_critic(db, cfg, parser, now, audit_stats=summary["audit"],
            deadline=deadline - _RESERVE_FOR_TAIL_S / 2 if fast_lane else None)

        from . import photo_recovery
        summary["photo_asins"] = photo_recovery.backfill_asins(
            db, rank.site_deals(db, cfg, now), fetcher, cfg,
            deadline - _RESERVE_FOR_TAIL_S) if fast_lane else {"tried": 0, "found": 0}
        if fast_lane:
            _links_from_posts(db, fetcher, deadline - _RESERVE_FOR_TAIL_S, cfg,
                              deals=rank.site_deals(db, cfg, now),
                              stats=summary["post_links"])

        # ---- live price re-check (correct plain new prices, confirm deaths) ----
        # The in-window photo-only Keepa calls ride the fast lane, which has
        # the slack; the hourly's tail is for the critic and publishing.
        summary["price_recheck"] = early_recheck or _recheck_prices(
            db, cfg, now, image_backfill=fast_lane,
            deadline=deadline - _RESERVE_FOR_TAIL_S / 2, fetcher=fetcher)
        summary["price_recheck"]["feed_confirmed"] = {k: len(v) for k, v in feed_confirmed.items()}
        summary["photo_search"] = photo_recovery.search_photos(
            db, rank.site_deals(db, cfg, now), fetcher, cfg, now,
            deadline - _RESERVE_FOR_TAIL_S / 2, run_key,
            rate_limited=summary["price_recheck"].get("rate_limited", False)) if fast_lane else {}
        record_deaths()
        log.info("deaths by reason: %s", summary["deaths_by_reason"])

        # Count the first site gate AFTER critic/rechecks, once per parsed
        # post version. Duplicate merges are counted separately, even live ones.
        # Diagnostics only: a failure here never stops the run.
        try:
            from .source_audit import gate_reason
            site_floor = min(cfg.min_pct, cfg.site_show_from_pct)
            for post, key, extracted, sav in audit_outcomes:
                counts = pstats.get("post_counts", {}).get(post.content_hash)
                deal = db.get_deal(key)
                if counts is None or deal is None:
                    continue
                candidate = dict(deal, final_price=sav.final_price,
                                 regular_price=sav.regular_price,
                                 pct_at_checkout=sav.pct_at_checkout,
                                 abs_savings=sav.abs_savings,
                                 online_only=extracted.online_only,
                                 currency=extracted.currency, region=extracted.region,
                                 conditions=extracted.conditions)
                # A missing critic verdict is a gap only when this sighting
                # otherwise could publish; clear below-floor items need no
                # extra paid judgment for the audit.
                judgment_complete = True
                if counts.get("prefilter_would_skip") and cfg.critic_enabled and cfg.critic_model:
                    unjudged = dict(candidate, flags=[f for f in candidate.get("flags", [])
                                                     if f != "suspect_msrp"])
                    if rank.is_publishable(unjudged, cfg, min_pct=site_floor, require_critic=False)[0]:
                        verdict = deal.get("critic")
                        valid = (isinstance(verdict, dict)
                                 and type(verdict.get("plausible")) is bool
                                 and (not verdict["plausible"] or verdict.get("rechecked")
                                      or float(verdict.get("confidence") or 0) >= .7))
                        if cfg.audit_mode == "enforce":
                            from .legitimacy import fingerprint, valid_code_basis

                            decision = deal.get("audit") or {}
                            valid = valid or valid_code_basis(deal, cfg) or (
                                decision.get("outcome") == "reject"
                                and decision.get("fingerprint") == fingerprint(deal, cfg))
                        if (not valid or deal.get("final_price") != sav.final_price
                                or deal.get("regular_price") != sav.regular_price):
                            judgment_complete = False
                            counts["evaluation_complete"] = False
                            counts["evaluation_gap"] = "critic"
                ok, reason = rank.is_publishable(deal, cfg, min_pct=site_floor)
                if ok:
                    # A merged row's better price cannot make a below-floor
                    # sighting publishable in the source or shadow counters.
                    if rank.is_publishable(candidate, cfg, min_pct=site_floor)[0]:
                        if parser is not None and hasattr(parser, "extract_by_source"):
                            parser.extract_by_source[post.source_name or "unknown"]["publishable"] += 1
                        if counts.get("prefilter_would_skip") and judgment_complete:
                            counts["prefilter_miss"] = True
                else:
                    category = gate_reason(reason)
                    gate = {"below floor": "below_floor", "critic suspect": "critic"}.get(
                        category, category)
                    counts[gate] = counts.get(gate, 0) + 1
            for post in new_posts:
                counts = pstats.get("post_counts", {}).get(post.content_hash)
                # Deadline-only counters (234) and bounded diagnostics (224)
                # do not make a deferred post a completed parse observation.
                if counts is not None and (post.content_hash in processed or (
                        counts.get("prefilter_would_skip") and not cfg.bounded_prefilter["enabled"])):
                    db.audit_parsed(post, counts, now)
            ai_yield.observe_prefilter(db, cfg, new_posts, pstats.get("post_counts", {}), now,
                                      [*sources, *(inbox.sources if inbox is not None else [])])
            audit_complete = True
        except Exception as e:
            log.warning("source audit counters failed: %s", type(e).__name__)
            for counts in pstats.get("post_counts", {}).values():
                if counts.get("prefilter_would_skip"):
                    counts["evaluation_complete"] = False
                    counts["evaluation_gap"] = "audit_failed"
        summary["prefilter"] = prefilter_counts(new_posts, pstats.get("post_counts", {}))
        summary["parse"]["prefilter"] = summary["prefilter"]

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
        from . import accuracy
        accuracy.extra_samples(db, cfg, now, fetcher=fetcher,
                               deadline=deadline - _RESERVE_FOR_TAIL_S / 2, run_key=run_key,
                               rate_limited=(summary["price_recheck"].get("rate_limited", False)
                                             or summary["photo_search"].get("rate_limited", False)))
        # last_run lands in the public deals.json: no spend or model (the
        # operator's; run_stats keeps the spend).
        site_res = render_site(db, cfg, now, last_run={
            "run_key": run_key, "kind": kind,
            "items_fetched": len(posts), "posts_new": len(new_posts),
        })
        summary["photo_coverage"] = photo_recovery.coverage([
            db.get_deal(d["dedupe_key"]) or d for d in site_res["public_deals"]
            if not d.get("locked")], cfg)
        log.info("photo_coverage: %s", summary["photo_coverage"])
        summary["published"] = site_res.get("published", 0)
        summary["appeal"] = site_res.get("appeal", {})
        from . import buybox
        summary["price_source"] = summary["price_recheck"].get("price_source", buybox.empty_summary())
        summary["buybox_top"] = summary["price_recheck"].get("buybox_top", {})
        summary["price_source"]["buybox_rule"] = buybox.shadow_summary(
            db, cfg, now, site_res["public_deals"])
        summary["non_amazon_freshness"] = freshness.record_snapshot(
            db, cfg, now, before_freshness, site_res["public_deals"], source_checks, before_scope)
        summary["unconfirmed_label"] = site_res["unconfirmed_label"]
        summary["unconfirmed_label"]["recheck_reasons"] = summary["price_recheck"].get(
            "hidden_unconfirmed", {}).get("reasons", {})
        from .publish.price_labels import META as label_report_meta
        db.meta_set(label_report_meta, json.dumps(summary["unconfirmed_label"], sort_keys=True))
        summary["price_recheck"]["confirmed_age"] = site_res["confirmed_age"]
        if "token_plan" in summary["price_recheck"]:
            summary["token_plan"] = summary["price_recheck"]["token_plan"]
        summary["seo_audit"] = site_res.get("seo_audit", {})
        summary["product_pages"] = site_res.get("product_pages", {})
        summary["posted_redirects"] = site_res.get("posted_redirects", {})
        summary["checkout"] = site_res.get("checkout", {})
        summary["checkout_price_deals"] = site_res["checkout_price_deals"]
        from .publish.indexnow import metrics as indexnow_metrics
        summary["indexnow"] = indexnow_metrics(db, cfg, now)
        try:
            public_keys = ai_yield.public_keys(site_res, cfg)
            yield_counts = ai_yield.record_public(db, cfg, util.now_utc(), run_key,
                                                 public_keys)
            summary["intake"]["unique_public_by_source"] = dict(Counter(
                r["source_name"] for r in db.conn.execute(
                    "SELECT event_key,source_name FROM ai_yield_events WHERE run_key=? AND kind='new'", (run_key,))
                if r["event_key"] not in yield_events_before))
            summary["intake"]["public_category_mix"] = dict(Counter(
                d.get("category") or "other" for key in public_keys if (d := db.get_deal(key))))
            yield_recorded = True
        except Exception as e:
            log.warning("AI yield measurement failed: %s", type(e).__name__)
        summary["link_mix"] = site_res.get("link_mix", {})
        summary["link_mix"]["trackers_resolved"] = summary["redirects"]["trackers_resolved"]
        log.info("link mix: earning %d; not earning %d; trackers resolved %d",
                 summary["link_mix"].get("earning", 0), summary["link_mix"].get("not_earning", 0),
                 summary["link_mix"]["trackers_resolved"])
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
                        fh.write(photo_lines + "\nphoto_coverage: " + json.dumps(summary["photo_coverage"], sort_keys=True) + "\n")
                except OSError as e:
                    log.warning("photo summary not written: %s", e)
        if step_summary and site_res.get("health"):
            try:
                with open(step_summary, "a", encoding="utf-8") as fh:
                    fh.write(health_markdown(site_res["health"]))
                    from .publish.price_labels import summary_lines as label_lines
                    fh.write("\n" + "\n".join(label_lines(summary["unconfirmed_label"])) + "\n")
                    fh.write("\n" + "\n".join(freshness.summary_lines(summary["non_amazon_freshness"])) + "\n")
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
                         deadline=deadline, run_key=run_key)
        summary["telegram"] = tg
        if tg.get("sent"):
            from .publish.seo import remember_posted_pages
            remember_posted_pages(db, cfg, site_res["public_deals"])
        # Paying members (Stripe) get the same new deals by email, bundled.
        summary["member_alerts"] = send_member_alerts(db, cfg, now, run_key=run_key)
        summary["digest_sent"] = maybe_send_digest(db, cfg, now, run_key=run_key)
        # Double opt-in: new sign-ups get their confirmation link every run
        # (small and capped, so it goes before the day's big send).
        summary["confirmations"] = send_confirmations(db, cfg, now,
                                                      deadline=deadline,
                                                      run_key=run_key)
        # The free list's daily top: picked once a day by the parse model on
        # the shared parser (same caps); its spend joins this run's total.
        summary["newsletter"] = send_daily_newsletter(db, cfg, now,
                                                      deadline=deadline,
                                                      parser=parser,
                                                      run_key=run_key)
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
        summary["llm_cache_tokens"] = dict(getattr(parser, "cache_tokens", {}))
        from . import ai_usage
        summary["ai_usage_day"] = ai_usage.daily(db, util.now_utc())
        for usage_line in ai_usage.lines(summary["ai_usage_day"]):
            log.info("%s", usage_line)
        untracked_spend = max(0.0, spend_total - float(getattr(parser, "ledger_spend_usd", 0.0)))
        yield_spend = max(spend_total, db.spend_usd_for_run(run_key) - yield_spend_before + untracked_spend)
        summary["ai_yield"] = ai_yield.metrics(
            yield_counts, yield_spend, summary["prefilter"]["unevaluated"])
        if not yield_recorded:
            summary["ai_yield"]["unavailable"] = "public yield measurement failed"
            summary["ai_yield"]["new_deals_per_usd"] = None
        summary["ai_yield"]["prefilter_readiness"] = ai_yield.readiness(db, cfg, util.now_utc())
        summary["efficiency"] = efficiency.run_metrics(
            summary, fetcher.keepa_feed_usage,
            {s.name for s in sources if s.method == "api" and s.adapter == "keepa"})
        if step_summary:
            try:
                with open(step_summary, "a", encoding="utf-8") as fh:
                    fh.write(_spend_markdown(summary["spend_by_kind"],
                                             summary["extract_by_source"]))
                    fh.write("\n".join(ai_usage.lines(summary["ai_usage_day"])) + "\n")
                    if pacing_line:
                        fh.write(pacing_line + "\n")
            except OSError as e:
                log.warning("LLM spend summary not written: %s", e)
        summary["admin_warnings"] = check_and_alert(db, cfg, now,
                                                    items_fetched=len(posts),
                                                    kind=kind)

        # ---- retention + bookkeeping ----
        # Inbox posts become known only after their facts and the rest of the
        # run were handled. Failed/capped reads stay pending, including every
        # unprocessed part of a multi-deal email.
        for post in new_posts:
            if post.via_email and post.content_hash in processed and db.record_post(post, now):
                recorded += 1
        summary["posts_new"] = recorded
        summary["intake"].update(source_audit.intake_deferred(
            db, [*sources, *(inbox.sources if inbox is not None else [])], now))
        summary["intake"]["deferred_posts"] = len(new_posts) - len(processed)
        cache_maintenance = db.prune(now, posts_seen_days=cfg.posts_seen_days, deals_days=cfg.deals_days,
                 post_parses_days=cfg.post_parses_days, llm_calls_days=cfg.llm_calls_days,
                 run_stats_days=cfg.run_stats_days, http_cache_days=cfg.http_cache_days,
                 outgoing_days=cfg.outgoing_days,
                 incremental_cache_enabled=cfg.incremental_cache_enabled, run_key=run_key,
                 cache_max_examined_per_run=cfg.incremental_cache_max_examined_per_run,
                 cache_budget_s=cfg.incremental_cache_budget_s, fast_lane=fast_lane,
                 deadline=deadline - _RESERVE_FOR_TAIL_S)
        if cache_maintenance is not None:
            summary["cache_maintenance"] = cache_maintenance
        inbox = getattr(fetcher, "newsletter_inbox", None)
        if inbox is not None:
            try:
                summary["inbox_acknowledged"] = inbox.acknowledge(db, cfg, deadline)
            except Exception:
                # Never log an HTTP exception or email body. A failed ack is
                # retried on the next pull; content hashes prevent re-parsing.
                log.warning("newsletter inbox acknowledgement deferred")
                summary["inbox_acknowledged"] = 0
        summary["elapsed_s"] = round(time.monotonic() - started, 1)
        run_complete = (audit_complete and yield_recorded and not pstats.get("aborted_spend_cap")
                        and not summary["prefilter"]["unevaluated"]
                        and len(processed) == len(new_posts))
        log.info("run %s done in %.1fs: %s", run_key, summary["elapsed_s"],
                 {k: v for k, v in summary.items() if k != "source_errors"})
        return summary
    finally:
        try:
            # Persist only counters/ages, including unavailable fields on a
            # failed run. No public payload or external request is involved.
            try:
                values = summary.get("efficiency") or efficiency.run_metrics(
                    summary, fetcher.keepa_feed_usage,
                    {s.name for s in sources if s.method == "api" and s.adapter == "keepa"})
                efficiency.record(db, now, run_key, values)
            except Exception as e:
                log.warning("efficiency measurement failed: %s", type(e).__name__)
            db.finish_run(run_key, now=util.now_utc(),
                          items_fetched=summary.get("items_fetched", 0),
                          posts_new=summary.get("posts_new", 0),
                          deals_parsed=summary.get("parse", {}).get("deals_extracted", 0),
                          published=summary.get("published", 0),
                          spend_usd=float(getattr(parser, "spend_usd", 0.0)),
                          accounted_spend_usd=float(getattr(parser, "ledger_spend_usd", 0.0)),
                          untracked_at=now,
                          posts_left=sum(c.get("evaluation_gap") == "deadline"
                                         for c in pstats.get("post_counts", {}).values()),
                          deadline_hit=pstats.get("deadline_hit", False))
        finally:
            try:
                try:
                    source_audit.record_intake_cost(
                        db, cfg, util.now_utc(), run_key,
                        getattr(parser, "extract_by_source", {}), new_posts, processed,
                        spend_before=yield_spend_before)
                except Exception as e:
                    log.warning("intake cost measurement failed: %s", type(e).__name__)
                if not audit_complete:
                    counts = pstats.get("post_counts", {})
                    for values in counts.values():
                        if values.get("prefilter_would_skip"):
                            values["evaluation_complete"] = False
                            values["evaluation_gap"] = "run_failed"
                    ai_yield.observe_prefilter(db, cfg, new_posts, counts, now, sources)
                ai_yield.finish(db, cfg, util.now_utc(), run_key, complete=run_complete)
            finally:
                fetcher.close()
                db.close()
            try:
                summary["state_db_bytes"] = os.path.getsize(db.path)
                log.info("state.db bytes: %d", summary["state_db_bytes"])
            except OSError:
                pass  # a measurement must never mask the run's own outcome


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
