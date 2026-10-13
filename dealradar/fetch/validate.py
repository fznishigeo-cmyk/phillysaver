"""First-run source validation.

Checks every configured source for liveness (feeds are fetched + parsed; API
sources are checked for required secrets only — no network), repairs moved
feeds via autodiscovery, promotes backup sources until the active set reaches
TARGET_ACTIVE, writes a human-readable sources_report.md, and persists the
resulting effective source list in the DB meta table.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser

import time

from ..config import AppConfig, get_secret
from ..errors import DeadlineExceeded
from ..models import SourceConfig
from ..util import official_feed_only
from .autodiscovery import discover_feed, site_root

log = logging.getLogger("dealradar.validate")

#: Validation promotes backups until this many sources are active.
TARGET_ACTIVE = 30

META_KEY = "effective_sources"

#: Per-source budget during first-run/daily validation (seconds).
PER_SOURCE_VALIDATE_BUDGET_S = 20.0

#: Required secrets per API adapter (telegram additionally needs configured channels).
ADAPTER_SECRETS: dict[str, list[str]] = {
    "reddit": ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"],
    "woot": ["WOOT_API_KEY"],
    "meh": [],  # keyless via Meh's public feed.json; MEH_API_KEY upgrades it
    "keepa": ["KEEPA_KEY"],
    "kcl_wall": [],  # keyless; permission-based wall crawl (crawl_permission)
    "telegram": ["TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION"],
    "threads": ["THREADS_TOKEN"],
}

ACTIVE_STATUSES = ("live", "live_updated_url", "promoted_backup")


def _feed_ok(body: bytes) -> bool:
    """True when `body` parses as a feed we can use."""
    feed = feedparser.parse(body)
    if len(feed.entries) >= 1:
        return True
    return bool(not feed.bozo and feed.get("version"))


def _validate_feed_source(
    source: SourceConfig, fetcher: Any, deadline: float | None = None, cfg=None
) -> tuple[str, str, SourceConfig | None]:
    """(status, detail, active_config|None) for an rss/html source."""
    from .rss import _permission_host
    kw = {}
    override = _permission_host(source, cfg)
    if override:  # only sources with documented written permission
        kw["robots_override_host"] = override
    res = fetcher.get(source.url, conditional=False, deadline=deadline, **kw)
    if res.blocked_by_robots:
        reason = "blocked_by_robots"
    elif res.status != 200 or not res.body:
        reason = f"http_{res.status}" + (f" {res.error}" if res.error else "")
    elif not _feed_ok(res.body):
        reason = "not_a_feed"
    else:
        return "live", source.url, source

    if official_feed_only(source.url):
        # Code-level policy (e.g. Slickdeals): the OFFICIAL feed or nothing.
        # Never fetch these hosts' HTML pages, never rewrite the feed URL.
        return ("dead",
                f"{reason}; official-feed-only host, autodiscovery forbidden",
                None)
    new_url = discover_feed(fetcher, site_root(source.url), deadline=deadline)
    if new_url:
        updated = dataclasses.replace(source, url=new_url)
        return (
            "live_updated_url",
            f"{reason}; feed moved: {source.url} -> {new_url}",
            updated,
        )
    return "dead", f"{reason}; autodiscovery found no feed", None


def _validate_api_source(source: SourceConfig, cfg: AppConfig) -> tuple[str, str]:
    """(status, detail) for an API source. Never touches the network."""
    adapter = source.adapter
    if adapter == "instagram":
        return "disabled", "instagram adapter is stubbed"
    if adapter == "threads" and not cfg.feature_flags.get("threads", False):
        return "disabled", "feature_flags.threads is off"
    required = ADAPTER_SECRETS.get(adapter)
    if required is None:
        return "needs_key", f"unknown adapter '{adapter}'"
    missing = [name for name in required if not get_secret(name)]
    if missing:
        return "needs_key", "missing " + ", ".join(missing)
    if adapter == "telegram" and not cfg.telegram_channels:
        return "needs_key", "no telegram.channels configured"
    return "live", "configured"


def _validate_one(
    source: SourceConfig, fetcher: Any, cfg: AppConfig,
    deadline: float | None = None,
) -> tuple[str, str, SourceConfig | None]:
    """(status, detail, active_config|None) for any source."""
    if not source.enabled:
        return "disabled", "disabled in sources.yaml", None
    from .pages import PAGE_ADAPTERS
    if ((source.method == "html" and source.adapter in PAGE_ADAPTERS)
            or (source.method == "api" and source.adapter == "jungle_deals")):
        from .pages import PageAdapter
        try:
            if source.adapter == "jungle_deals":
                from .jungle import JungleAdapter
                JungleAdapter.fetch_deals(source, fetcher,
                                          datetime.now(timezone.utc), deadline)
            else:
                PageAdapter.fetch_page(source, fetcher,
                                       datetime.now(timezone.utc), deadline)
        except DeadlineExceeded:
            raise
        except RuntimeError as exc:
            return "dead", str(exc), None
        return "live", source.url, source
    if source.method in ("rss", "html"):
        return _validate_feed_source(source, fetcher, deadline, cfg)
    if source.method == "api":
        status, detail = _validate_api_source(source, cfg)
        return status, detail, source if status == "live" else None
    return "dead", f"unknown method '{source.method}'", None


def validate_sources(
    primary: list[SourceConfig],
    backups: list[SourceConfig],
    fetcher: Any,
    db: Any,
    cfg: AppConfig,
    now: datetime,
    run_deadline: float | None = None,
) -> dict[str, Any]:
    """Validate all primary sources, then promote backups until TARGET_ACTIVE.

    ``run_deadline`` (time.monotonic) bounds the whole pass; each source also
    gets a short per-source budget. Sources the deadline skips are marked
    'skipped_deadline' and stay in the effective run set — our own time budget
    must never cull a source.
    """
    rows: list[dict[str, str]] = []
    active: list[SourceConfig] = []

    def _source_deadline() -> float | None:
        per = time.monotonic() + PER_SOURCE_VALIDATE_BUDGET_S
        return min(run_deadline, per) if run_deadline is not None else per

    def _out_of_time() -> bool:
        return run_deadline is not None and time.monotonic() >= run_deadline

    standby: list[SourceConfig] = []  # configured API sources awaiting secrets

    for source in primary:
        if _out_of_time():
            status, detail, active_cfg = (
                "skipped_deadline", "run budget exhausted before validation", None)
            standby.append(source)  # keep it runnable; never cull on our budget
        else:
            try:
                status, detail, active_cfg = _validate_one(
                    source, fetcher, cfg, _source_deadline())
            except DeadlineExceeded:
                status, detail, active_cfg = (
                    "skipped_deadline", "per-source validation budget hit", None)
                standby.append(source)
        if status == "needs_key":
            standby.append(source)
        rows.append(
            {
                "name": source.name,
                "method": source.method,
                "url": source.url,
                "status": status,
                "detail": detail,
                "replaced_by": "",
            }
        )
        if active_cfg is not None:
            active.append(active_cfg)
        log.info("validate %s: %s (%s)", source.name, status, detail)

    # Primary rows that a promoted backup can stand in for, worst first.
    replaceable = [r for r in rows if r["status"] == "dead"] + [
        r for r in rows if r["status"] == "needs_key"
    ]

    for backup in backups:
        if len(active) >= TARGET_ACTIVE:
            break
        if _out_of_time():
            break
        try:
            status, detail, active_cfg = _validate_one(
                backup, fetcher, cfg, _source_deadline())
        except DeadlineExceeded:
            continue
        if active_cfg is not None:
            status = "promoted_backup"
            active.append(active_cfg)
            if replaceable:
                replaceable.pop(0)["replaced_by"] = backup.name
        rows.append(
            {
                "name": backup.name,
                "method": backup.method,
                "url": backup.url,
                "status": status,
                "detail": detail,
                "replaced_by": "",
            }
        )
        log.info("validate backup %s: %s (%s)", backup.name, status, detail)

    counts = dict(Counter(row["status"] for row in rows))
    return {
        "active": active,
        # The run set: live sources plus key-awaiting API sources, so that a
        # secret added later activates on the next run (needs_key attempts are
        # local-only and never trip the circuit breaker).
        "effective": active + standby,
        "rows": rows,
        "counts": counts,
        "active_count": len(active),
    }


def _cell(value: str) -> str:
    """Escape a value for a one-line markdown table cell."""
    return value.replace("|", "\\|").replace("\n", " ").strip()


def write_report(
    result: dict[str, Any],
    cfg: AppConfig,
    now: datetime,
    path: str = "sources_report.md",
) -> None:
    """Render the validation result as sources_report.md (facts only, no post text)."""
    rows = result["rows"]
    counts = result["counts"]
    active_count = result["active_count"]
    ts = now.astimezone(timezone.utc).isoformat(timespec="seconds")

    lines: list[str] = []
    lines.append("# dealradar sources report")
    lines.append("")
    lines.append(f"Generated: {ts} (UTC)")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Active sources: {active_count}")
    for status in sorted(counts):
        lines.append(f"- {status}: {counts[status]}")
    if active_count < TARGET_ACTIVE:
        lines.append("")
        lines.append(
            f"**Note:** only {active_count} active sources; target is "
            f"{TARGET_ACTIVE} and the backup pool is exhausted."
        )
    lines.append("")
    lines.append("## Sources")
    lines.append("")
    lines.append("| name | method | status | detail/url |")
    lines.append("| --- | --- | --- | --- |")
    for row in rows:
        detail = row["detail"] or row["url"]
        if row.get("replaced_by"):
            detail = f"{detail} (replaced by {row['replaced_by']})"
        lines.append(
            f"| {_cell(row['name'])} | {_cell(row['method'])} "
            f"| {_cell(row['status'])} | {_cell(detail)} |"
        )

    promoted = [r for r in rows if r["status"] == "promoted_backup"]
    dropped = [r for r in rows if r["status"] == "dead"]
    lines.append("")
    lines.append("## Promoted backups")
    lines.append("")
    if promoted:
        lines.extend(f"- {r['name']} ({_cell(r['url'])})" for r in promoted)
    else:
        lines.append("None.")
    lines.append("")
    lines.append("## Dropped sources")
    lines.append("")
    if dropped:
        lines.extend(f"- {r['name']}: {_cell(r['detail'])}" for r in dropped)
    else:
        lines.append("None.")
    lines.append("")

    Path(path).write_text("\n".join(lines), encoding="utf-8")
    log.info("wrote %s (%d rows, %d active)", path, len(rows), active_count)


def save_effective_sources(db: Any, sources: list[SourceConfig],
                           now: datetime | None = None) -> None:
    """Persist the post-validation source list for later runs."""
    saved_at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    db.meta_set(META_KEY, json.dumps({
        "saved_at": saved_at,
        "sources": [dataclasses.asdict(s) for s in sources],
    }))


def effective_sources(db: Any, now: datetime | None = None,
                      max_age_h: float = 24.0) -> list[SourceConfig] | None:
    """The persisted post-validation source list.

    Returns None when never validated OR when the stored set is older than
    ``max_age_h`` (the caller then revalidates), so feed moves are repaired
    and dead sources culled at most a day late.
    """
    raw = db.meta_get(META_KEY)
    if raw is None:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict):  # legacy shape: force one revalidation
        return None
    if now is not None:
        try:
            saved_at = datetime.fromisoformat(data.get("saved_at", ""))
        except ValueError:
            return None
        if (now - saved_at).total_seconds() > max_age_h * 3600:
            log.info("effective source set is older than %.0f h; revalidating",
                     max_age_h)
            return None
    return [SourceConfig(**d) for d in data.get("sources", [])]
