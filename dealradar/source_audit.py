"""Read-only source coverage report. Outputs contain no source/deal content.

Older state has no discovery/evidence/merge ledger. Report unavailable history
as n/a, rather than inventing zeroes. Retained sightings supply current gates.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import re
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx

from . import rank, util
from .config import get_secret, load_sources
from .db import DB, SCHEMA
from .errors import AdapterDisabled, AdapterNotConfigured, DeadlineExceeded
from .fetch.registry import get_adapter
from .fetch.rss import _permission_host
from .fetch.validate import effective_sources
from .http_client import PoliteFetcher
from .publish.admin_alerts import send_admin_text
from .publish.site import _serialize_deal

VERDICTS = ("OK", "LOW YIELD", "STALE", "BROKEN", "TRIPPED")
_AFFILIATE_IDS = {"tag", "affid", "aff_id", "afftrack", "ranmid", "raneaid",
                  "ransiteid", "cjevent", "irclickid", "clickid", "pubid"}


@contextmanager
def _quiet():
    # Adapters sometimes log markup or authenticated URLs. No such diagnostics
    # may reach this command's public stdout, even with --verbose.
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous)


def _reason(error: str) -> str:
    """Allow-listed status labels only; never echo arbitrary stored errors."""
    low = error.lower()
    for needle, label in (
        ("robots", "robots blocked"), ("needs_key", "needs_key"),
        ("deadline", "deadline"), ("challenge", "bot wall"),
        ("empty_or_not_feed", "feed empty or moved"),
        ("not_a_feed", "feed empty or moved"),
        ("empty_or_changed_deal_page", "adapter selector changed or empty page"),
        ("missing_deal_shopping_links", "shopping links missing"),
        ("jungle", "API schema changed or empty"),
        ("disabled", "adapter disabled"), ("html_not_allowed", "HTML disallowed"),
        ("unknown", "adapter configuration unknown"),
    ):
        if needle in low:
            return label
    match = re.search(r"(?:http[_ ]|status(?: code)?[ :']*)([1-5][0-9]{2})\b", low)
    if match:
        return "HTTP " + match[1]
    if "timeout" in low:
        return "timeout"
    if any(s in low for s in ("connect", "dns", "transport")):
        return "transport/DNS failure"
    return "adapter failure" if error else "none"


class _ProbeFetcher(PoliteFetcher):
    """Same polite client, with unconditional probes and count-only metadata."""
    def begin(self, source):
        self.granted_host = _permission_host(source)
        self.statuses = []
        self.permission_used = False
        self.robots_blocked = False
        self.error = ""

    def get(self, url, **kwargs):
        kwargs["conditional"] = False  # 304 cannot measure items/newest date
        override = kwargs.get("robots_override_host")
        if override != self.granted_host:
            kwargs.pop("robots_override_host", None)
            override = None
        self.permission_used |= self._host_overridden(url, override)
        result = super().get(url, **kwargs)
        self.statuses.append(result.status)
        self.robots_blocked |= result.blocked_by_robots
        if result.error:
            self.error = _reason(result.error)
        return result


def _table_exists(db, name):
    return bool(db.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _history(db, source, now, days, since_tracking):
    since = util.iso(now - timedelta(days=days))
    end = util.iso(now)
    parsed = db.conn.execute(
        "SELECT COUNT(*) FROM posts_seen WHERE source_name=? AND first_seen_at>=?"
        " AND first_seen_at<=?", (source.name, since, end)).fetchone()[0]
    tracked = _table_exists(db, "source_audit_posts")
    seen_sql = "SELECT content_hash FROM posts_seen WHERE source_name=? AND first_seen_at>=? AND first_seen_at<=?"
    args = (source.name, since, end)
    if tracked:
        seen_sql += (" AND NOT EXISTS (SELECT 1 FROM source_audit_posts a"
                     " WHERE a.source_name=posts_seen.source_name"
                     " AND a.content_hash=posts_seen.content_hash)")
        seen_sql += " UNION SELECT content_hash FROM source_audit_posts WHERE source_name=? AND first_seen_at>=? AND first_seen_at<=?"
        args += args
    seen = db.conn.execute("SELECT COUNT(*) FROM (" + seen_sql + ")", args).fetchone()[0]
    complete = since_tracking is not None and since_tracking <= now - timedelta(days=days)
    counts = {"seen": seen, "parsed": parsed, "complete": complete,
              "extracted": None, "evidence": None, "duplicate": None,
              "below_floor": None, "critic": None, "dead": None, "other": None}
    if tracked and since_tracking is not None:
        row = db.conn.execute(
            "SELECT COALESCE(SUM(extracted),0), COALESCE(SUM(evidence),0),"
            " COALESCE(SUM(duplicate),0), COALESCE(SUM(below_floor),0),"
            " COALESCE(SUM(critic),0), COALESCE(SUM(dead),0), COALESCE(SUM(other),0)"
            " FROM source_audit_posts WHERE source_name=? AND parsed_at>=? AND parsed_at<=?",
            (source.name, since, end)).fetchone()
        counts.update(zip(("extracted", "evidence", "duplicate", "below_floor",
                           "critic", "dead", "other"), row))
    ids = {r[0] for r in db.conn.execute(
        "SELECT DISTINCT deal_id FROM sightings WHERE source_name=? AND seen_at>=? AND seen_at<=?",
        (source.name, since, end))}
    counts.update(ids=ids, gates=Counter(), live=0)
    return counts


def gate_reason(reason):
    """Stable count label for the publisher's first failing gate."""
    if reason.startswith("status:"):
        return "dead"
    if "suspect_msrp" in reason:
        return "critic suspect"
    if reason.startswith(("pct_at_checkout:", "abs_savings:")):
        return "below floor"
    return "other"


def _carries_credit(link, deal, source, sightings):
    """Source post/tracker, or an unchanged affiliate ID from its first sighting.

    A separate 'via' credit is deliberately not counted as Get deal credit.
    """
    parts = urlsplit(link)
    host = (parts.hostname or "").lower().removeprefix("www.")
    own = (urlsplit(source.site or source.url).hostname or "").lower().removeprefix("www.")
    if own and (host == own or host.endswith("." + own)):
        return True
    if not sightings or sightings[0]["source_name"] != source.name:
        return False
    posted = deal.get("link_as_posted") or ""
    old = dict(parse_qsl(urlsplit(posted).query))
    new = dict(parse_qsl(parts.query))
    old_host = (urlsplit(posted).hostname or "").lower()
    return old_host == (parts.hostname or "").lower() and any(
        k.lower() in _AFFILIATE_IDS and v and new.get(k) == v for k, v in old.items())


def audit(db, cfg, now, *, sources=None, transport=None, sleep_fn=None):
    """Probe each enabled registry entry once, including enabled backups.

    The caller opens state with mode=ro. Adapters and HTTP caches write only
    to a disposable in-memory copy; the breaker does not prevent this probe.
    """
    with _quiet():
        return _audit(db, cfg, now, sources=sources, transport=transport, sleep_fn=sleep_fn)


def _audit(db, cfg, now, *, sources, transport, sleep_fn):
    if sources is None:
        primary, backups = load_sources()
        sources = primary + backups
    sources = sorted((s for s in sources if s.enabled),
                     key=lambda s: (not bool(s.crawl_permission), s.name))
    saved = {s.name: s for s in effective_sources(db, now=now) or []}
    health = {r["source_name"]: r for r in db.all_source_health()}
    tracking = util.parse_iso(db.meta_get("source_audit_since") or "")
    deals = {d["id"]: d for d in db.all_deals()}
    floor = min(cfg.min_pct, cfg.site_show_from_pct)
    rows = []
    memory = DB(":memory:")
    fetcher = _ProbeFetcher(memory, cfg, transport=transport, sleep_fn=sleep_fn)
    try:
        for configured in sources:
            h = health.get(configured.name, {})
            source = configured
            if configured.name in saved and not (h.get("consecutive_failures") or h.get("tripped_until")):
                prior = saved[configured.name]
                source = dataclasses.replace(configured, url=prior.url,
                                             method=prior.method, adapter=prior.adapter)
            db.conn.backup(memory.conn)
            memory.conn.executescript(SCHEMA)  # older snapshots; memory only
            # A scheduled audit still reads the listing when a recent Keepa pull
            # would self-throttle. Token reserves and adapter limits still apply.
            memory.conn.execute("DELETE FROM meta WHERE k='keepa_last_fetch_at'")
            memory.conn.execute("DELETE FROM http_cache")
            memory.conn.commit()
            fetcher.begin(configured)  # a moved feed never extends the grant
            posts, failure = [], ""
            api_statuses = []
            # The Telegram reader is one login session the fast lane uses every
            # 10 minutes; a second connection at once can get it revoked.
            skipped = configured.adapter == "telegram"
            try:
                if skipped:
                    failure = "probe skipped (shared login session)"
                    raise AdapterDisabled(failure)
                adapter = get_adapter(source, cfg)
                # Official authenticated adapters manage their own HTTP client.
                # Inject a client solely to observe HTTP codes and support mocks.
                with httpx.Client(transport=transport, timeout=cfg.request_timeout_s,
                                  follow_redirects=True,
                                  headers={"User-Agent": cfg.resolved_user_agent()},
                                  event_hooks={"response": [lambda r: api_statuses.append(r.status_code)]}) as client:
                    if hasattr(adapter, "_http"):
                        adapter._http = client
                    if sleep_fn is not None and hasattr(adapter, "_sleep"):
                        adapter._sleep = sleep_fn
                    posts = adapter.fetch_posts(source, fetcher, memory, cfg, now,
                                                time.monotonic() + cfg.per_source_budget_s)
            except AdapterNotConfigured:
                failure = "needs_key"
            except AdapterDisabled:
                failure = failure or "adapter disabled"
            except DeadlineExceeded:
                failure = "deadline"
            except httpx.HTTPStatusError as exc:
                failure = f"HTTP {exc.response.status_code}"
            except Exception as exc:
                failure = _reason(str(exc))
            newest = max((p.published_at for p in posts if p.published_at), default=None)
            periods = {}
            for label, days in (("24h", 1), ("7d", 7)):
                counts = _history(db, source, now, days, tracking)
                for deal_id in counts.pop("ids"):
                    deal = deals.get(deal_id)
                    if deal is None:
                        continue
                    ok, reason = rank.is_publishable(deal, cfg, min_pct=floor)
                    if ok:
                        counts["live"] += 1
                    else:
                        counts["gates"][gate_reason(reason)] += 1
                periods[label] = counts
            source_ids = {r[0] for r in db.conn.execute(
                "SELECT DISTINCT deal_id FROM sightings WHERE source_name=?", (source.name,))}
            live, credited = 0, 0
            for deal_id in source_ids:
                deal = deals.get(deal_id)
                if deal is None or not rank.is_publishable(deal, cfg, min_pct=floor)[0]:
                    continue
                sightings = db.sightings_for(deal_id)
                public = _serialize_deal(dict(deal, sightings=sightings), now,
                                        cfg.affiliate_amazon_tag, cfg.affiliate_link_overrides,
                                        sovrn_key=cfg.affiliate_sovrn_key,
                                        sovrn_enabled=cfg.affiliate_sovrn_enabled,
                                        competitor_tracker_hosts=cfg.affiliate_competitor_tracker_hosts,
                                        ebay_campid=cfg.affiliate_ebay_campaign_id)
                live += 1
                credited += _carries_credit(public["url"], deal, configured, sightings)
            tripped = db.is_tripped(source.name, now)
            seven = periods["7d"]
            denominator = seven["parsed"] or len(posts)
            if tripped:
                verdict, cause = "TRIPPED", _reason(h.get("last_error") or "")
            elif not skipped and (failure or not posts):
                verdict = "BROKEN"
                cause = failure or fetcher.error or "adapter returned 0 items"
            elif newest is not None and now - newest > timedelta(days=3):
                verdict, cause = "STALE", "newest item older than 3 days"
            elif denominator >= 5 and seven["live"] / denominator < 0.1:
                verdict = "LOW YIELD"
                causes = Counter(seven["gates"])
                if seven["evidence"]:
                    causes["evidence"] += seven["evidence"]
                if seven["duplicate"]:
                    causes["duplicate"] += seven["duplicate"]
                if seven["other"]:
                    causes["other"] += seven["other"]
                cause = causes.most_common(1)[0][0] if causes else "no completed parses" if not seven["parsed"] else "few accepted deals"
            else:
                verdict, cause = "OK", "fetch and yield within audit thresholds"
            breaker = None
            if _table_exists(db, "source_audit_breakers"):
                breaker = db.conn.execute("SELECT * FROM source_audit_breakers WHERE source_name=?",
                                          (source.name,)).fetchone()
            since = breaker["tripped_at"] if breaker and tripped else None
            if tripped and not since:
                until = util.parse_iso(h.get("tripped_until") or "")
                since = util.iso(until - timedelta(hours=cfg.circuit_breaker_cooldown_h)) + " (estimated)"
            rows.append({"name": source.name, "permission": bool(configured.crawl_permission),
                         "http": ",".join(map(str, sorted(set(fetcher.statuses + api_statuses)))) or "n/a",
                         "items": len(posts), "newest": util.iso(newest) or "n/a",
                         "access": ("permission" if fetcher.permission_used else "robots" if fetcher.statuses else "official API")
                                   + ("; robots blocked" if fetcher.robots_blocked else ""),
                         "tripped": tripped, "error": _reason(h.get("last_error") or ""),
                         "since": since or "n/a", "until": h.get("tripped_until") or "n/a",
                         "failures": h.get("consecutive_failures", 0),
                         "trips": breaker["trips"] if breaker else None,
                         "periods": periods, "live": live, "credited": credited,
                         "verdict": verdict, "cause": cause, "probe_error": failure})
    finally:
        fetcher.close()
        memory.close()
    return rows


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _count(value, complete):
    return "n/a" if value is None else str(value) if complete else f">={value} (partial)"


def markdown(rows, now):
    lines = ["# Source coverage audit", "", f"Generated: {util.iso(now)}", "",
             "One adapter invocation per enabled source, permission sources first; adapter caps, filters, enrichment and token reserves apply. This is a listing sample, not proof of every deal on a site.", "",
             "24h / 7d counts use first discovery and completed parse times. Legacy posts_seen records completed parses only; discoveries, extracted/evidence/duplicate counts and trip totals before audit tracking are unavailable. Partial counts are lower bounds.", "",
             "Drops are counted when ingest completes, after critic and rechecks (first failing site gate). Duplicate counts are merges, which may still contribute a live deal. Other includes in-store/non-US exclusions. Current exclusions show first failing gates among retained deals sighted in each window; these can change after ingest. Live now uses the site's floor. Get deal credit excludes the separate via link and measures source-host links or preserved affiliate IDs attributed to the first sighting.", "",
             "LOW YIELD means at least 5 parsed posts in 7d (current items if no parses) with fewer than 10% yielding live deals. Unknown item dates are n/a. A TRIPPED source is still probed; since is estimated for legacy breakers and trip counts cover tracked transitions only.", "",
             "| Source | Grant | Verdict | HTTP | Items | Newest UTC | Access | Breaker (since / until; failures; trips; last error) | Posts seen 24h / 7d | Posts parsed 24h / 7d | Extracted 24h / 7d | Drops 24h / 7d | Current exclusions 24h / 7d | Live now | Get deal credit | Likely cause / probe |",
             "|---|---|---|---|---:|---|---|---|---|---|---|---|---|---:|---|---|"]
    for row in rows:
        periods = [row["periods"][p] for p in ("24h", "7d")]
        seen = " / ".join(str(p["seen"]) if p["complete"] else f">={p['seen']} (partial)" for p in periods)
        parsed = " / ".join(str(p["parsed"]) for p in periods)
        extracted = " / ".join(_count(p["extracted"], p["complete"]) for p in periods)
        drops = []
        for p in periods:
            drops.append("; ".join(f"{label} {_count(p[key], p['complete'])}" for key, label in
                                   (("evidence", "evidence"), ("below_floor", "below floor"),
                                    ("critic", "critic suspect"), ("duplicate", "duplicate"),
                                    ("dead", "dead"), ("other", "other"))))
        current = " / ".join("; ".join(f"{gate} {p['gates'][gate]}" for gate in
                                       ("below floor", "critic suspect", "dead", "other")) for p in periods)
        breaker = f"{'TRIPPED' if row['tripped'] else 'closed'}: {row['since']} / {row['until']}; failures {row['failures']}; trips {_count(row['trips'], True)}; {row['error']}"
        share = f"{row['credited']}/{row['live']} ({row['credited'] / row['live']:.0%})" if row["live"] else "n/a (0 live)"
        cause = row["cause"] + ("; probe: " + row["probe_error"] if row["probe_error"] else "")
        values = (row["name"], "yes" if row["permission"] else "no", row["verdict"], row["http"],
                  row["items"], row["newest"], row["access"], breaker, seen, parsed, extracted,
                  " / ".join(drops), current, row["live"], share, cause)
        lines.append("| " + " | ".join(_cell(v) for v in values) + " |")
    return "\n".join(lines) + "\n"


def admin_message(rows):
    counts = Counter(row["verdict"] for row in rows)
    severity = {v: i for i, v in enumerate(("TRIPPED", "BROKEN", "STALE", "LOW YIELD", "OK"))}
    worst = sorted((r for r in rows if r["verdict"] != "OK"),
                   key=lambda r: (severity[r["verdict"]], not r["permission"], -r["failures"], r["name"]))[:5]
    return "Source audit: " + "; ".join(f"{v} {counts[v]}" for v in VERDICTS) + "\n" + "\n".join(
        f"{r['name']}: {r['verdict']} ({r['cause']})" for r in worst)


def run(cfg, *, db_path="state.db", now=None, sources=None, transport=None, sleep_fn=None):
    now = now or util.now_utc()
    with _quiet():
        db = DB(db_path, read_only=True)  # missing state fails; never create empty state
        try:
            rows = audit(db, cfg, now, sources=sources, transport=transport, sleep_fn=sleep_fn)
        finally:
            db.close()
        report = markdown(rows, now)
        print(report, end="")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a", encoding="utf-8") as stream:
                stream.write(report)
        if get_secret("TELEGRAM_BOT_TOKEN") and get_secret("TELEGRAM_ADMIN_CHAT_ID"):
            try:
                with httpx.Client(transport=transport, timeout=20) as client:
                    status = send_admin_text(admin_message(rows), http_client=client)
            except Exception:
                status = 0
            if status != 200:
                print(f"Telegram admin: HTTP {status}")
                return 1
    return 0
