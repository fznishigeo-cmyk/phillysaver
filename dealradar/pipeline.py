"""Run orchestration: fetch -> parse -> savings -> dedupe/verify -> publish.

Hard budgets: 20 s/request and 90 s/source (enforced in the fetch layer),
8 min/run (enforced here). Idempotency: content-hashed posts_seen, UNIQUE
alert tokens, UNIQUE price-history rows — re-running the same hour adds
nothing and re-sends nothing.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from . import util
from .config import AppConfig, load_config, load_sources
from .db import DB
from .errors import ParserNotConfigured, SpendCapReached
from .models import RawPost, SourceConfig

log = logging.getLogger("dealradar.pipeline")

# Seconds reserved at the end of the run budget for parse/publish when fetching.
_RESERVE_FOR_TAIL_S = 90.0


def _run_key(now: datetime, fast_lane: bool) -> str:
    key = now.strftime("%Y-%m-%dT%H")
    return f"{key}:fast" if fast_lane else key


def _load_effective_sources(db: DB, cfg: AppConfig, fetcher, now: datetime,
                            validate_if_missing: bool = True) -> list[SourceConfig]:
    """Validated source set from state, else first-run live validation."""
    from .fetch.validate import (effective_sources, save_effective_sources,
                                 validate_sources, write_report)

    primary, backup = load_sources()
    stored = effective_sources(db)
    if stored:
        return stored
    if not validate_if_missing:
        return [s for s in primary if s.enabled]
    log.info("first run: validating %d sources live (+%d backups)", len(primary), len(backup))
    result = validate_sources(primary, backup, fetcher, db, cfg, now)
    write_report(result, cfg, now)
    active = result["active"]
    if len(active) < 5:
        # Validation can come up near-empty when the network is unreachable;
        # fall back to the configured list so the pipeline still functions,
        # and do NOT persist the empty set (next run revalidates).
        log.warning("validation found only %d live sources; using configured list "
                    "without persisting (will revalidate next run)", len(active))
        return [s for s in primary if s.enabled]
    save_effective_sources(db, active)
    log.info("validated: %d active sources (report: sources_report.md)", len(active))
    return active


def _fetch_all(sources: list[SourceConfig], fetcher, db: DB, cfg: AppConfig,
               now: datetime, deadline: float, fast_lane: bool) -> tuple[list[RawPost], dict[str, str]]:
    from .fetch.base import run_source
    from .fetch.registry import get_adapter

    posts: list[RawPost] = []
    errors: dict[str, str] = {}
    for source in sources:
        if not source.enabled:
            continue
        if fast_lane and not source.fast_lane:
            continue
        if time.monotonic() > deadline - _RESERVE_FOR_TAIL_S:
            log.warning("run budget nearly exhausted; skipping remaining sources from %s",
                        source.name)
            break
        try:
            adapter = get_adapter(source, cfg, fast_lane=fast_lane)
        except ValueError as e:
            errors[source.name] = f"no adapter: {e}"
            continue
        got, ok, err = run_source(source, adapter, fetcher, db, cfg, now,
                                  run_deadline=deadline)
        if ok:
            posts.extend(got)
            log.info("source %s: %d items", source.name, len(got))
        else:
            errors[source.name] = err
            log.info("source %s: skipped (%s)", source.name, err)
    return posts, errors


def _parse_new_posts(new_posts: list[RawPost], cfg: AppConfig, now: datetime,
                     tz_by_source: dict[str, str],
                     claude_client=None) -> tuple[list[tuple[RawPost, Any]], dict]:
    from .parse.claude_client import ClaudeParser
    from .parse.extractor import parse_posts

    stats = {"posts_sent": 0, "deals_extracted": 0, "deals_dropped_evidence": 0,
             "spend_usd": 0.0, "aborted_spend_cap": False, "parser": "disabled"}
    if not new_posts:
        return [], stats
    try:
        parser = ClaudeParser(cfg, client=claude_client)
    except ParserNotConfigured:
        log.warning("DEALS_CLAUDE_API_KEY not set: parse layer skipped")
        return [], stats
    except Exception as e:  # model lookup / client construction failed
        log.error("parse layer unavailable: %s", e)
        return [], stats
    stats["parser"] = parser.model
    try:
        pairs, pstats = parse_posts(new_posts, parser, cfg, now, tz_by_source)
    except SpendCapReached:
        log.warning("spend cap reached before parsing could start")
        return [], stats
    stats.update(pstats)
    return pairs, stats


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
                fetcher, extracted.product_url, cfg.redirect_max_hops)
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
            sources = sources_override
        else:
            sources = _load_effective_sources(db, cfg, fetcher, now,
                                              validate_if_missing=not skip_validation)
        tz_by_source = {s.name: s.tz for s in sources}

        # ---- fetch ----
        posts, fetch_errors = _fetch_all(sources, fetcher, db, cfg, now, deadline, fast_lane)
        summary["items_fetched"] = len(posts)
        summary["source_errors"] = fetch_errors

        # ---- freshness / idempotency: parse only posts first seen now, within 24 h ----
        new_posts: list[RawPost] = []
        for post in posts:
            if db.record_post(post, now):
                new_posts.append(post)
        summary["posts_new"] = len(new_posts)
        log.info("fetched %d items, %d new (content-hash)", len(posts), len(new_posts))

        # ---- parse (Claude; evidence-verified) ----
        pairs, pstats = _parse_new_posts(new_posts, cfg, now, tz_by_source,
                                         claude_client=claude_client)
        summary["parse"] = pstats

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
                db.update_deal(res["dedupe_key"], status="dead")
        summary["deals_upserted"] = deals_upserted

        # ---- verify tiers + dead marking ----
        refresh_tiers(db, cfg, now)
        summary["marked_dead"] = mark_expired_dead(db, cfg, now)

        # ---- publish ----
        site_res = render_site(db, cfg, now, last_run={
            "run_key": run_key, "kind": kind,
            "items_fetched": len(posts), "posts_new": len(new_posts),
            "spend_usd": round(pstats.get("spend_usd", 0.0), 4),
            "parser_model": pstats.get("parser", ""),
        })
        summary["published"] = site_res.get("published", 0)
        tg = send_alerts(db, cfg, now, dry_run=dry_run_telegram)
        summary["telegram"] = tg
        summary["digest_sent"] = maybe_send_digest(db, cfg, now)
        summary["admin_warnings"] = check_and_alert(db, cfg, now,
                                                    items_fetched=len(posts))

        # ---- retention + bookkeeping ----
        db.prune(now, posts_seen_days=cfg.posts_seen_days, deals_days=cfg.deals_days)
        db.finish_run(run_key, now=util.now_utc(),
                      items_fetched=len(posts), posts_new=len(new_posts),
                      deals_parsed=pstats.get("deals_extracted", 0),
                      published=summary["published"],
                      spend_usd=pstats.get("spend_usd", 0.0))
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
            save_effective_sources(db, result["active"])
        log.info("validated %d sources: %d active (sources_report.md written)",
                 len(result["rows"]), result["active_count"])
        return {"active_count": result["active_count"],
                "counts": result.get("counts", {})}
    finally:
        fetcher.close()
        db.close()
