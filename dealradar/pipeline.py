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
import time
from datetime import datetime
from itertools import chain, zip_longest
from typing import Any

from . import util
from .config import AppConfig, load_config, load_sources
from .db import DB
from .errors import ParserNotConfigured, SpendCapReached
from .models import RawPost, SourceConfig

log = logging.getLogger("dealradar.pipeline")

# Seconds reserved at the end of the run budget for publish (critic + price
# rechecks + site render) after parse stops.
_RESERVE_FOR_TAIL_S = 90.0
# Fetching (and its enrichment) stops this early so parse gets real time. The
# parse window is _RESERVE_FOR_PARSE_S - _RESERVE_FOR_TAIL_S; keep the gap wide.
# Live incident: a 60-source sweep ran to the wire, parse got seconds, and the
# back half of the source list never published a single deal.
_RESERVE_FOR_PARSE_S = 300.0


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
                     deadline: float | None
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
                                    deadline=deadline)
    except SpendCapReached:
        log.warning("spend cap reached before parsing could start")
        return [], stats
    stats.update(pstats)
    return pairs, stats


#: Hours between live price re-checks of the same Amazon deal.
_RECHECK_EVERY_H = 6.0
#: Early (in-window) Keepa calls per run made only to fetch a missing photo
#: (1 token each; a 429 just defers the deal to the next run).
_IMAGE_BACKFILL_PER_RUN = 40
#: Article fetches per run that backfill photos for existing deals whose
#: source granted image display: one polite fetch per deal (the fetcher
#: still holds 1 req/s per host), never repeated, time-boxed by the run.
_BLOG_IMAGE_BACKFILL_PER_RUN = 60


def _backfill_blog_images(db: DB, fetcher, sources: list[SourceConfig],
                          deadline: float | None) -> int:
    """Give existing photo-less deals from image-granted sources their picture.

    Deals created before a source's image grant (or from an excerpt-only
    feed entry) keep the credited article URL in their sightings; fetching
    that page once — politely, robots/permission-checked by the fetcher —
    and taking its og:image fills the card. A deal whose article yields no
    image is flagged so it is never fetched again.
    """
    from .dedupe import _DEAL_FIELDS
    from .fetch.rss import _first_image, _og_image, _permission_host

    granted = {s.name: s for s in sources if s.image_permission}
    if not granted:
        return 0
    done = 0
    for deal in db.all_deals(status="alive"):
        if deal.get("image_url") or \
                "image_backfill_tried" in (deal.get("flags") or []):
            continue
        sighting = next((s for s in db.sightings_for(deal["id"])
                         if s["source_name"] in granted
                         and str(s.get("source_url", "")).startswith("http")),
                        None)
        if sighting is None:
            continue
        if deadline is not None and time.monotonic() > deadline:
            break
        src = granted[sighting["source_name"]]
        kw = {}
        host = _permission_host(src)
        if host:
            kw["robots_override_host"] = host
        res = fetcher.get(sighting["source_url"], conditional=False,
                          deadline=deadline, **kw)
        image = ""
        if res.status == 200 and res.body and not res.blocked_by_robots:
            html = res.body.decode("utf-8", errors="replace")
            image = _og_image(html) or _first_image(html)
        payload = {k: deal.get(k) for k in _DEAL_FIELDS if k in deal}
        if image:
            payload["image_url"] = image
        else:
            flags = list(payload.get("flags") or [])
            flags.append("image_backfill_tried")
            payload["flags"] = flags
        db.update_deal(deal["dedupe_key"], payload=payload)
        done += 1
        if done >= _BLOG_IMAGE_BACKFILL_PER_RUN:
            break
    if done:
        log.info("blog image backfill: %d deals fetched", done)
    return done


def _recheck_prices(db: DB, cfg: AppConfig, now: datetime) -> dict[str, Any]:
    """The 'click the page before publishing' gate, via Keepa's licensed API
    (we never scrape retailer pages): every about-to-publish Amazon deal
    gets its LIVE price checked at most once per 6 hours. A moved price
    means the deal is over: status dead + 'price_moved' flag — the site
    shows it marked dead, alerts never fire for it."""
    from datetime import timedelta

    from . import rank
    from .dedupe import _DEAL_FIELDS
    from .verify import recheck_amazon_price

    stats = {"checked": 0, "dead": 0, "image_backfill": 0}
    if not cfg.feature_flags.get("keepa"):
        return stats
    for deal in db.all_deals(status="alive"):
        if not deal.get("asin"):
            continue
        last = util.parse_iso(deal.get("price_rechecked_at") or "")
        if last is not None and now - last < timedelta(hours=_RECHECK_EVERY_H):
            # Inside the window only a photo-less deal earns an early call:
            # the same product response carries its image (the harvest
            # landed after many deals' first recheck). Capped per run so the
            # Keepa token bucket keeps serving the deal lane.
            if deal.get("image_url") or \
                    stats["image_backfill"] >= _IMAGE_BACKFILL_PER_RUN:
                continue
            stats["image_backfill"] += 1
        ok, _reason = rank.is_publishable(deal, cfg)
        if not ok:
            continue  # only spend tokens on deals that would publish
        verdict = recheck_amazon_price(deal, cfg)
        if verdict is None:
            continue  # API had no opinion; try again next pass
        stats["checked"] += 1
        payload = {k: deal.get(k) for k in _DEAL_FIELDS if k in deal}
        payload["price_rechecked_at"] = util.iso(now)
        if verdict:
            db.update_deal(deal["dedupe_key"], payload=payload)
            continue
        stats["dead"] += 1
        flags = list(payload.get("flags") or [])
        if "price_moved" not in flags:
            flags.append("price_moved")
        payload["flags"] = flags
        payload["died_at"] = util.iso(now)
        db.update_deal(deal["dedupe_key"], payload=payload, status="dead")
        log.info("price recheck: %s is over (live price moved); marked dead",
                 deal.get("dedupe_key"))
    return stats


def _run_critic(db: DB, cfg: AppConfig, parser, now: datetime) -> dict[str, Any]:
    """Deal critic: a stronger model judges whether each about-to-publish
    deal's claimed regular price is honest. An implausible verdict adds the
    'suspect_msrp' flag, which the publish gate excludes (configurable).
    One verdict per deal; a price change clears it for re-judging (dedupe).
    """
    from . import rank
    from .dedupe import _DEAL_FIELDS
    from .parse.claude_client import critique_deal

    stats = {"judged": 0, "suspect": 0}
    if parser is None or not cfg.critic_enabled or not cfg.critic_model:
        return stats
    for deal in db.all_deals(status="alive"):
        prior = deal.get("critic")
        if prior:
            # One second look per deal: a LOW-CONFIDENCE pass (< 0.7) gets
            # re-judged exactly once — weak "plausible" verdicts are where
            # inflated prices slip through. Everything else is settled
            # until a price change clears the verdict (dedupe).
            recheck = (prior.get("plausible")
                       and float(prior.get("confidence") or 0.0) < 0.7
                       and not prior.get("rechecked"))
            if not recheck:
                continue
        ok, _reason = rank.is_publishable(deal, cfg)
        if not ok:
            continue  # the critic only spends on deals that would publish
        try:
            verdict = critique_deal(parser, deal)
        except SpendCapReached:
            log.warning("spend cap reached in deal critic; remaining deals "
                        "stay unjudged and retry next run")
            break
        if verdict is None:
            continue  # critic disabled/failed: no opinion, deal unaffected
        if prior:
            verdict["rechecked"] = True
        stats["judged"] += 1
        payload = {k: deal.get(k) for k in _DEAL_FIELDS if k in deal}
        payload["critic"] = verdict
        if not verdict.get("plausible", True):
            stats["suspect"] += 1
            flags = list(payload.get("flags") or [])
            if "suspect_msrp" not in flags:
                flags.append("suspect_msrp")
            payload["flags"] = flags
            log.warning("deal critic flagged %s as suspect MSRP: %s",
                        deal.get("dedupe_key", "?"),
                        verdict.get("reason", ""))
        db.update_deal(deal["dedupe_key"], payload=payload)
    return stats


def _resolve_product_urls(pairs, fetcher, cfg: AppConfig, deadline: float) -> None:
    """Resolve redirect/affiliate chains to canonical retailer URLs (budget-aware).

    The source's original link stays on the sighting for credit; only
    product_url is canonicalized.
    """
    from .http_client import canonicalize_url, resolve_redirects

    for _post, extracted in pairs:
        if not extracted.product_url:
            continue
        if time.monotonic() > deadline - 30:
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
    from .publish.site import render_site
    from .publish.telegram_pub import send_alerts
    from .savings import compute_savings
    from .verify import mark_expired_dead, refresh_tiers, text_marks_dead

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
        # Daily ceiling: this run may spend at most what remains of
        # cfg.spend_cap_usd_per_day for the current UTC day.
        remaining_today = max(0.0, cfg.spend_cap_usd_per_day
                              - db.spend_usd_for_day(now))
        parser = None
        if remaining_today <= 0:
            log.warning("daily LLM spend cap ($%.2f) exhausted; parse skipped "
                        "this run (posts stay unrecorded and retry tomorrow)",
                        cfg.spend_cap_usd_per_day)
        else:
            # Built once per run and shared: extraction, second opinions, and
            # the deal critic all draw on the same spend caps.
            parser = _build_parser(cfg, claude_client=claude_client,
                                   run_cap_usd=remaining_today)
        # Parse stops _RESERVE_FOR_TAIL_S early: the critic, price re-checks,
        # and publishing must still run inside the budget on heavy runs.
        pairs, pstats = _parse_new_posts(
            new_posts, parser, cfg, now, tz_by_source,
            deadline - _RESERVE_FOR_TAIL_S)
        summary["parse"] = pstats
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
        deals_upserted = 0
        for post, extracted in pairs:
            sav = compute_savings(extracted)
            if sav.exclude_reason in ("in_store", "non_us"):
                continue  # excluded entirely (kept out of the DB by design)
            res = upsert_extracted(db, extracted, sav, post, cfg, now)
            deals_upserted += 1
            if text_marks_dead(post.text):
                res["payload"]["died_at"] = util.iso(now)
                db.update_deal(res["dedupe_key"], payload=res["payload"],
                               status="dead")
        summary["deals_upserted"] = deals_upserted

        # ---- verify tiers + dead marking ----
        refresh_tiers(db, cfg, now)
        summary["marked_dead"] = mark_expired_dead(db, cfg, now)

        # ---- deal critic (runs BEFORE publish so flagged deals never ship) ----
        summary["critic"] = _run_critic(db, cfg, parser, now)

        # ---- live price re-check (Amazon deals; moved price => dead) ----
        summary["price_recheck"] = _recheck_prices(db, cfg, now)

        # ---- photos for existing deals from image-granted sources ----
        summary["image_backfill"] = _backfill_blog_images(
            db, fetcher, sources, deadline - 30.0)

        # Total LLM spend this run: extraction + second opinions + critic.
        spend_total = (float(getattr(parser, "spend_usd", 0.0))
                       if parser is not None
                       else pstats.get("spend_usd", 0.0))

        # ---- publish ----
        site_res = render_site(db, cfg, now, last_run={
            "run_key": run_key, "kind": kind,
            "items_fetched": len(posts), "posts_new": len(new_posts),
            "spend_usd": round(spend_total, 4),
            "parser_model": pstats.get("parser", ""),
        })
        summary["published"] = site_res.get("published", 0)
        tg = send_alerts(db, cfg, now, dry_run=dry_run_telegram,
                         deadline=deadline)
        summary["telegram"] = tg
        summary["digest_sent"] = maybe_send_digest(db, cfg, now)
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
