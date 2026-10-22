"""Read-only source coverage report. Outputs contain no source/deal content.

Older state has no discovery/evidence/merge ledger. Report unavailable history
as n/a, rather than inventing zeroes. Retained sightings supply current gates.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import math
import os
import re
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx

from .state_growers import posts_relation, known_sql, audit_relation, audit_known_sql
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
        self.granted_host = _permission_host(source, self.cfg)
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

    def _api_response(self, req, result, headers):
        self.statuses.append(result.status)
        self.robots_blocked |= result.blocked_by_robots
        if result.error:
            self.error = _reason(result.error)
        return super()._api_response(req, result, headers)


def _table_exists(db, name):
    return bool(db.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _intake_store(value):
    # Common written store aliases share one ceiling (Amazon / Amazon.com,
    # Macy's / Macys); no retailer is inferred from unparsed post text.
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().casefold().removesuffix(".com"))


def record_intake_cost(db, cfg, now, run_key, extract_by_source, posts, processed, spend_before=0.0):
    """Compact, cumulative costs; a no-spend rerun adds nothing.

    Direct extraction uses the parser's model-priced accounting. Allocate
    remaining ledger spend (critic, category, second opinion, etc.) by handled
    posts, falling back to extraction calls or recent handled posts when
    work revisits existing inventory. This is an estimate, never an
    affiliate-income signal. Failed runs retain their costs too.
    """
    key = "intake_cost:" + run_key
    previous = json.loads(db.meta_get(key) or json.dumps({"usd": spend_before, "samples": []}))
    total = db.spend_usd_for_run(run_key)
    delta = max(0.0, total - previous["usd"])
    if delta:
        sample = {"at": util.iso(now), "sources": {}}
        direct = {s: float(v["usd"]) for s, v in extract_by_source.items()}
        direct_total = sum(direct.values())
        scale = min(1.0, delta / direct_total) if direct_total else 1.0
        handled = Counter(p.source_name for p in posts if p.content_hash in processed)
        if not handled:
            handled = Counter({s: v.get("calls", 0) for s, v in extract_by_source.items()})
        if not sum(handled.values()):
            handled = Counter({r["source_name"]: r["n"] for r in db.conn.execute(
                f"SELECT source_name,COUNT(*) AS n FROM {posts_relation(db)} AS posts_seen WHERE first_seen_at>=?"
                " AND first_seen_at<=? GROUP BY source_name",
                (util.iso(now - timedelta(days=cfg.intake_window_days)), util.iso(now)))})
        shared = delta - direct_total * scale
        samples = sum(handled.values())
        for source in direct.keys() | handled.keys():
            row = sample["sources"].setdefault(source, {"usd": 0.0, "calls": 0})
            row["usd"] += direct.get(source, 0.0) * scale + (
                shared * handled[source] / samples if samples else 0.0)
            row["calls"] += extract_by_source.get(source, {}).get("calls", 0)
        sample["unattributed_usd"] = shared if not samples else 0.0
        previous["samples"].append(sample)
        previous["usd"] = total
        db.meta_set(key, json.dumps(previous, separators=(",", ":")))
    cutoff = util.iso(now - timedelta(days=cfg.intake_window_days))
    for row in db.conn.execute("SELECT k,v FROM meta WHERE k LIKE 'intake_cost:%'").fetchall():
        costs = json.loads(row["v"])
        retained = [s for s in costs["samples"] if s["at"] >= cutoff]
        if not retained:
            db.conn.execute("DELETE FROM meta WHERE k=?", (row["k"],))
        elif retained != costs["samples"]:
            costs["samples"] = retained
            db.meta_set(row["k"], json.dumps(costs, separators=(",", ":")))
    db.conn.commit()


def intake_history(db, cfg, now):
    """067's deduped useful events over a rolling, covered window, facts only."""
    result = {"sources": {}, "window_days": cfg.intake_window_days}
    since = util.parse_iso(db.meta_get("ai_yield_since") or "")
    costs_since = util.parse_iso(db.meta_get("intake_since") or "")
    if since is None or costs_since is None or not _table_exists(db, "ai_yield_events"):
        return dict(result, unavailable="067 yield / intake cost history unavailable")
    retained = util.parse_iso(db.meta_get("ai_yield_retained_since") or "") or since
    start = max(now - timedelta(days=cfg.intake_window_days), since, costs_since, retained)
    result.update(observed_hours=max(0.0, (now - start).total_seconds() / 3600),
                  from_at=util.iso(start), through=util.iso(now), unattributed_usd=0.0)
    if result["observed_hours"] < cfg.intake_min_history_hours:
        return dict(result, unavailable="067 yield / intake cost data is too young")

    def bucket(source):
        return result["sources"].setdefault(source, {"posts": 0, "calls": 0, "usd": 0.0,
            "unique_new_public_deals": 0, "useful_price_updates": 0, "stores": Counter()})

    # No per-source claims for legacy spend or for a run without an allocation.
    recorded_costs = {}
    for row in db.conn.execute("SELECT k,v FROM meta WHERE k LIKE 'intake_cost:%'"):
        costs = json.loads(row["v"])
        recorded_costs[row["k"].removeprefix("intake_cost:")] = costs["usd"]
        for sample in costs["samples"]:
            if not util.iso(start) <= sample["at"] <= util.iso(now):
                continue
            result["unattributed_usd"] += sample.get("unattributed_usd", 0.0)
            for source, values in sample["sources"].items():
                b = bucket(source)
                b["usd"] += values["usd"]
                b["calls"] += values["calls"]
    if not _table_exists(db, "llm_calls") or not _table_exists(db, "llm_legacy_spend"):
        return dict(result, unavailable="ledger history unavailable")
    paid_runs = db.conn.execute(
        "SELECT run_key FROM llm_calls WHERE status!='released' AND COALESCE(cost_usd,reserved_usd)>0"
        " AND COALESCE(billed_at,started_at)>=? AND COALESCE(billed_at,started_at)<=?"
        " UNION SELECT run_key FROM llm_legacy_spend WHERE usd>0 AND billed_at>=? AND billed_at<=?",
        (util.iso(start), util.iso(now), util.iso(start), util.iso(now)))
    if any(recorded_costs.get(r["run_key"], 0) + 1e-9 < db.spend_usd_for_run(r["run_key"]) for r in paid_runs):
        return dict(result, unavailable="intake cost observation gap")
    for row in db.conn.execute(f"SELECT source_name,COUNT(*) AS n FROM {posts_relation(db)} AS posts_seen"
                               " WHERE first_seen_at>=? AND first_seen_at<=? GROUP BY source_name",
                               (util.iso(start), util.iso(now))):
        bucket(row["source_name"])["posts"] = row["n"]
    stores = {hashlib.sha256(d["dedupe_key"].encode()).hexdigest(): _intake_store(d.get("store"))
              for d in db.all_deals()}
    for row in db.conn.execute("SELECT identity,kind,source_name FROM ai_yield_events"
                               " WHERE kind IN ('new','update') AND observed_at>=? AND observed_at<=?",
                               (util.iso(start), util.iso(now))):
        b = bucket(row["source_name"])
        b["unique_new_public_deals" if row["kind"] == "new" else "useful_price_updates"] += 1
        if store := stores.get(row["identity"]):
            b["stores"][store] += 1
    for b in result["sources"].values():
        useful = b["unique_new_public_deals"] + b["useful_price_updates"]
        b["useful_per_usd"] = useful / b["usd"] if b["usd"] > 0 else None
        b["low_sample"] = b["posts"] < cfg.intake_min_posts or b["useful_per_usd"] is None
        # Missing/retired store identities are unknown, never guessed.
        b["store_mix"] = {s: n / useful for s, n in b.pop("stores").items()} if useful else {}
    if result["unattributed_usd"] > 1e-9:
        result["unavailable"] = "some ledger spend cannot be attributed to sources"
    return result


def intake_weights(sources, history, cfg):
    """Floor, explicit exploration, then useful/USD; hard source/store ceilings.

    Store ceilings use observed useful supply, including non-earning stores.
    When supply cannot fill those ceilings the remainder stays unallocated.
    """
    names = list(dict.fromkeys(s.name for s in sources))
    if not names:
        return {}
    stats = history["sources"] if "unavailable" not in history else {}
    weights = dict.fromkeys(names, cfg.intake_floor_share / len(names))
    cap = max(cfg.intake_max_source_share, 1 / len(names))
    mixes = {s: stats.get(s, {}).get("store_mix", {}) for s in names}

    def allocate(amount, scores):
        for _ in range(len(names) + len({store for mix in mixes.values() for store in mix}) + 1):
            stores = Counter()
            for s, w in weights.items():
                for store, share in mixes[s].items():
                    stores[store] += w * share
            room = {s: min([max(0.0, cap - weights[s]), *[
                max(0.0, cfg.intake_max_store_share - stores[store]) / share
                for store, share in mixes[s].items() if share > 0]]) for s in names}
            active = {s: scores.get(s, 0.0) for s in names if room[s] > 1e-10 and scores.get(s, 0.0) > 0}
            if not active or amount <= 1e-10:
                break
            # Scale the whole increment to the tightest store constraint;
            # independently clipping sources would overshoot a shared store.
            increments = {s: min(room[s], amount * v / sum(active.values())) for s, v in active.items()}
            projected = Counter()
            for s, inc in increments.items():
                for store, share in mixes[s].items():
                    projected[store] += inc * share
            scale = min([1.0, *[max(0.0, cfg.intake_max_store_share - stores[s]) / inc
                                  for s, inc in projected.items() if inc > 0]])
            used = 0.0
            for s, inc in increments.items():
                weights[s] += inc * scale
                used += inc * scale
            amount -= used
        return amount

    low = {s: 1.0 for s in names if stats.get(s, {}).get("low_sample", True)}
    allocate(cfg.intake_exploration_share, low or dict.fromkeys(names, 1.0))
    scores = {s: stats[s]["useful_per_usd"] for s in names if s in stats and not stats[s]["low_sample"]}
    remaining = allocate(1 - cfg.intake_floor_share - cfg.intake_exploration_share,
                         scores if any(scores.values()) else dict.fromkeys(names, 1.0))
    allocate(remaining, dict.fromkeys(names, 1.0))
    return weights


def intake_deferred(db, sources, now):
    """Pending discovery ages plus time since a source was last attempted."""
    names = {s.name for s in sources if s.enabled}
    health = {r["source_name"]: r for r in db.all_source_health()}
    since = util.parse_iso(db.meta_get("intake_since") or "") or now
    ages = {s: max(0.0, (now - (util.parse_iso(health.get(s, {}).get("updated_at") or "") or since))
                   .total_seconds() / 3600) for s in names}
    if _table_exists(db, "source_audit_posts"):
        for row in db.conn.execute(f"SELECT source_name,MIN(first_seen_at) AS oldest FROM {audit_relation(db)} a"
                                   f" WHERE NOT {known_sql(db, 'a.content_hash')}"
                                   " GROUP BY source_name"):
            if row["source_name"] in names:
                age = max(0.0, (now - util.parse_iso(row["oldest"])).total_seconds() / 3600)
                ages[row["source_name"]] = max(ages[row["source_name"]], age)
    source = max(sorted(ages), key=ages.get) if ages else None
    return {"oldest_deferred_source": source if source and ages[source] > 0 else None,
            "oldest_deferred_hours": ages.get(source, 0.0)}


def intake_source_order(sources, weights, history, db, cfg, now, fast_lane):
    """Reorder active slots only; run_source still owns every breaker decision."""
    active = [s for s in sources if s.enabled and (not fast_lane or s.fast_lane)
              and not db.is_tripped(s.name, now)]
    health = {r["source_name"]: r for r in db.all_source_health()}
    since = util.parse_iso(db.meta_get("intake_since") or "") or now
    ages = {s.name: max(0.0, (now - (util.parse_iso(health.get(s.name, {}).get("updated_at") or "") or since))
                        .total_seconds() / 3600) for s in active}
    due = sorted((s.name for s in active if ages[s.name] >= cfg.intake_max_wait_hours),
                 key=lambda s: -ages[s])
    low = [s.name for s in active if history["sources"].get(s.name, {}).get("low_sample", True)]
    explore = set(low[:math.ceil(len(active) * cfg.intake_exploration_share)])
    positions = {s.name: i for i, s in enumerate(sources)}
    ordered = iter(sorted(active, key=lambda s: (
        0 if s.name in due else 1, due.index(s.name) if s.name in due else 0,
        bool(s.fast_lane) if not fast_lane else False,
        0 if s.name in explore else 1, -weights.get(s.name, 0), positions[s.name])))
    active_names = {s.name for s in active}
    return [next(ordered) if s.name in active_names else s for s in sources], due


def intake_due_posts(db, names, cfg, now):
    """A fresh fetch cannot conceal starvation at the AI read budget."""
    last = {r["source_name"]: util.parse_iso(r["last_read"]) for r in db.conn.execute(
        f"SELECT source_name,MAX(first_seen_at) AS last_read FROM {posts_relation(db)} AS posts_seen GROUP BY source_name")}
    since = util.parse_iso(db.meta_get("intake_since") or "") or now
    ages = {s: max(0.0, (now - last.get(s, since)).total_seconds() / 3600) for s in names}
    return sorted((s for s in names if ages[s] >= cfg.intake_max_wait_hours), key=lambda s: -ages[s])


def intake_post_order(posts, weights, history, cfg, due=()):
    """A capped weighted prefix. Deferred posts remain unknown and retry normally."""
    queues = {}
    for post in posts:
        queues.setdefault(post.source_name, []).append(post)
    if not queues or cfg.max_posts_per_run <= 0:
        return []
    used, stores, selected = Counter(), Counter(), []
    cap = max(cfg.intake_max_source_share, 1 / len(queues))
    source_limit = math.ceil(cfg.max_posts_per_run * cap)
    store_limit = math.ceil(cfg.max_posts_per_run * cfg.intake_max_store_share)

    def mix(source):
        structured = queues[source][used[source]].structured or {}
        store = _intake_store(structured.get("store"))
        return {store: 1.0} if store else history["sources"].get(source, {}).get("store_mix", {})

    while len(selected) < cfg.max_posts_per_run:
        eligible = [s for s, queue in queues.items() if used[s] < len(queue)
                    and used[s] < min(source_limit, math.ceil(cfg.max_posts_per_run * weights.get(s, 1 / len(queues))))
                    and all(stores[store] + share <= store_limit + 1e-9 for store, share in mix(s).items())]
        if not eligible:
            break
        overdue = [s for s in due if s in eligible and not used[s]]
        source = overdue[0] if overdue else min(eligible, key=lambda s: (used[s] + 1) / max(weights.get(s, 1 / len(queues)), 1e-9))
        for store, share in mix(source).items():
            stores[store] += share
        selected.append(queues[source][used[source]])
        used[source] += 1
    return selected


def intake_report_data(db, cfg, now):
    history = intake_history(db, cfg, now)
    primary, backup = load_sources()
    sources = [s for s in primary + backup if s.enabled]
    result = {"mode": cfg.intake_mode, **intake_deferred(db, sources, now)}
    if "unavailable" in history:
        return dict(result, unavailable=history["unavailable"])
    samples = [dict(source=s, **v) for s, v in history["sources"].items() if not v["low_sample"]]
    if not samples:
        return dict(result, unavailable="insufficient source samples")
    samples.sort(key=lambda v: (-v["useful_per_usd"], v["source"]))
    return dict(result, observed_hours=history["observed_hours"], top=samples[:3], bottom=list(reversed(samples[-3:])))


def _history(db, source, now, days, since_tracking):
    since = util.iso(now - timedelta(days=days))
    end = util.iso(now)
    parsed = db.conn.execute(
        f"SELECT COUNT(*) FROM {posts_relation(db)} AS posts_seen WHERE source_name=? AND first_seen_at>=?"
        " AND first_seen_at<=?", (source.name, since, end)).fetchone()[0]
    tracked = _table_exists(db, "source_audit_posts")
    seen_sql = f"SELECT content_hash FROM {posts_relation(db)} AS posts_seen WHERE source_name=? AND first_seen_at>=? AND first_seen_at<=?"
    args = (source.name, since, end)
    if tracked:
        seen_sql += " AND NOT " + audit_known_sql(db,"posts_seen.source_name","posts_seen.content_hash")
        seen_sql += f" UNION SELECT content_hash FROM {audit_relation(db)} WHERE source_name=? AND first_seen_at>=? AND first_seen_at<=?"
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
            f" FROM {audit_relation(db)} WHERE source_name=? AND parsed_at>=? AND parsed_at<=?",
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
            skipped = configured.adapter == "telegram" or bool(configured.min_interval_minutes)
            try:
                if skipped:
                    # A site owner's rate limit (min_interval_minutes) is spent by the hourly run.
                    failure = ("probe skipped (owner's rate limit)" if configured.min_interval_minutes
                               else "probe skipped (shared login session)")
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
