"""Opt-in Haiku-first judging, bounded Sonnet sampling and durable fallback."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from datetime import timezone

from . import legitimacy, rank
from .errors import SpendCapReached
from .parse.claude_client import critique_deals
from .util import iso
from . import util

log = logging.getLogger(__name__)
FALLBACK = "critic_tiering_fallback"


def confident(verdict, minimum):
    return (isinstance(verdict, dict) and type(verdict.get("plausible")) is bool
            and isinstance(verdict.get("confidence"), (int, float))
            and math.isfinite(verdict["confidence"])
            and minimum <= verdict["confidence"] <= 1)


def high_visibility(db, cfg, now, stored, *, settled_only=False):
    """Use existing pick and wall ranking before the new verdict gate.

    Include the previous wall as well as projected arrivals: publishing photos
    or a new rank must not silently promote an old Haiku verdict to a top pick.
    """
    from .non_amazon_freshness import hidden, label, promotion_excluded
    from .verify import price_unconfirmed

    rows = [dict(d) for d in stored if rank.is_publishable(
        d, cfg, require_critic=settled_only, min_pct=min(cfg.min_pct, cfg.site_show_from_pct),
        allow_spotlight_price=True)[0]]
    scores = [(d, value) for d in rows
              if (value := rank.pick_score(d, cfg, now, require_confirmation=False)) is not None]
    scores.sort(key=lambda pair: pair[1], reverse=True)
    keys = {d["dedupe_key"] for d, _ in scores[:cfg.keepa_recheck_top_n]}
    wall = [d for d in rows if not hidden(d, cfg, now) and not price_unconfirmed(d, cfg, now)]
    for d in wall:
        if promotion_excluded(d, cfg, now):
            d["price_aged"] = label(d, now)
    wall.sort(key=lambda d: (bool(d.get("price_aged")), -rank.score(d, cfg, now)))
    picked = rank.top_picks([(d, rank.pick_score(d, cfg, now)) for d in wall], cfg.front_page, now, cfg=cfg)
    for n, d in enumerate(picked, 1):
        d["pick"] = n
    keys.update(d["dedupe_key"] for d in rank.pick_order(wall, cfg.front_page)[:cfg.keepa_recheck_wall_n])
    for name in (() if settled_only else ("published_picks", "published_wall")):
        try:
            saved = json.loads(db.meta_get(name) or "[]")
            if isinstance(saved, list):
                keys.update(k for k in saved if isinstance(k, str))
        except ValueError:
            pass
    keys.update(d["dedupe_key"] for d in rows
                if (d.get("final_price") or 0) > cfg.critic_tiering["high_price_usd"])
    return keys


def protect_promoted(db, cfg, parser, now, save, *, deadline=None):
    """A rejection can promote the next ordinary deal onto the visible wall.

    Recompute until every displayed candidate has the stronger verdict. Capped
    or invalid calls leave those deals pending; no cached Haiku approval can
    slip into a vacated top slot while Sonnet is unavailable.
    """
    from .dedupe import _DEAL_FIELDS

    if cfg.critic_tiering["haiku_only"] and not fallback_reason(db, cfg):
        return 0  # Haiku's verdict is final; nothing waits on Sonnet
    escalated, capped = 0, False
    while True:
        stored = db.all_deals(status="alive")
        high = high_visibility(db, cfg, now, stored, settled_only=True)
        promoted = [d for d in stored if d["dedupe_key"] in high
                    and d.get("regular_price") and d.get("final_price") is not None
                    and not (cfg.audit_mode == "enforce" and legitimacy.valid_code_basis(d, cfg))
                    and (d.get("critic") or {}).get("model") == cfg.critic_tiering["model"]]
        if not promoted:
            return escalated
        for deal in promoted:
            deal["flags"] = list(dict.fromkeys([*(deal.get("flags") or []), "critic_tier_pending"]))
            db.update_deal(deal["dedupe_key"], payload={k: deal.get(k) for k in _DEAL_FIELDS if k in deal})
        escalated += len(promoted)
        for start in range(0, len(promoted), cfg.critic_batch_size):
            if capped:
                break
            batch = promoted[start:start + cfg.critic_batch_size]
            try:
                if deadline is not None and time.monotonic() + 2 * cfg.request_timeout_s + 2 > deadline:
                    capped = True
                    log.warning("tiered critic: promoted wall candidates await Sonnet time")
                    break
                verdicts = critique_deals(parser, batch)
            except SpendCapReached:
                capped = True
                log.warning("tiered critic: promoted wall candidates await Sonnet budget")
                break
            for deal, verdict in zip(batch, verdicts):
                if confident(verdict, 0):
                    save(deal, dict(verdict, model=cfg.critic_model))


def fallback_reason(db, cfg):
    reason = db.meta_get(FALLBACK)
    if reason:
        return reason
    options = cfg.critic_tiering
    results = [r[0] for r in db.conn.execute(
        "SELECT agrees FROM critic_tier_samples ORDER BY observed_at DESC, rowid DESC LIMIT ?",
        (options["window_deals"],))]
    if len(results) >= options["min_judged_deals"]:
        misses = len(results) - sum(results)
        if misses / len(results) > options["max_disagreement"]:
            reason = (f"Sonnet sample disagreed on {misses}/{len(results)} judged deals "
                      f"(limit {options['max_disagreement']:.0%}); Sonnet-for-all")
            db.meta_set(FALLBACK, reason)
    return reason or ""


def _sample_key(deal, cfg):
    return hashlib.sha256(json.dumps([legitimacy.fingerprint(deal, cfg),
        cfg.critic_tiering["model"], cfg.critic_model]).encode()).hexdigest()


def _claim_sample(db, batch, cfg, now):
    key = hashlib.sha256("".join(sorted(_sample_key(d, cfg) for d in batch)).encode()).hexdigest()
    day = now.astimezone(timezone.utc).date().isoformat()
    with db.conn:
        db.conn.execute("BEGIN IMMEDIATE")
        used = db.conn.execute("SELECT COUNT(*) FROM critic_tier_batches WHERE day=?", (day,)).fetchone()[0]
        if used >= cfg.critic_tiering["shadow_batches_per_day"]:
            return False
        return db.conn.execute("INSERT OR IGNORE INTO critic_tier_batches VALUES (?,?)", (key, day)).rowcount == 1


def run(db, cfg, parser, now, pending, high, save, *, fallback="", deadline=None):
    options = cfg.critic_tiering
    size = cfg.critic_batch_size
    stats = {"haiku_approved": 0, "escalated": 0, "sample_judged": 0,
             "sample_disagreed": 0, "fallback": fallback}
    escalated, approved = [], []

    def sonnet(rows):
        for start in range(0, len(rows), size):
            batch = rows[start:start + size]
            try:
                if deadline is not None and time.monotonic() + 2 * cfg.request_timeout_s + 2 > deadline:
                    log.warning("tiered critic: Sonnet deadline reached; escalations remain unpublished")
                    return
                verdicts = critique_deals(parser, batch)
            except SpendCapReached:
                log.warning("tiered critic: Sonnet budget exhausted; escalations remain unpublished")
                return
            for deal, verdict in zip(batch, verdicts):
                if confident(verdict, 0):
                    save(deal, dict(verdict, model=cfg.critic_model))

    if fallback:
        log.warning("tiered critic fallback: %s", fallback)
        sonnet(pending)
        return stats
    for start in range(0, len(pending), size):
        batch = pending[start:start + size]
        try:
            if deadline is not None and time.monotonic() + 2 * cfg.request_timeout_s + 2 > deadline:
                log.warning("tiered critic: Haiku deadline reached; remaining deals stay unjudged")
                break
            verdicts = critique_deals(parser, batch, model=options["model"])
        except SpendCapReached:
            log.warning("tiered critic: Haiku budget exhausted; remaining deals stay unjudged")
            break
        for deal, verdict in zip(batch, verdicts):
            if options["haiku_only"] and confident(verdict, options["min_confidence"]):
                # Operator 2026-10-22 ("do both on haiku"): a confident Haiku verdict
                # is final, front page and rejections included. Only an unsure or
                # invalid one still goes to Sonnet; Sonnet also samples approvals
                # below, and a failed sample still trips the Sonnet-for-all fallback.
                save(deal, dict(verdict, model=options["model"]))
                if verdict["plausible"]:
                    approved.append((deal, verdict))
                    stats["haiku_approved"] += 1
                continue
            if (not confident(verdict, options["min_confidence"])
                    or not verdict["plausible"] or deal["dedupe_key"] in high):
                escalated.append(deal)
                stats["escalated"] += 1
            else:
                save(deal, dict(verdict, model=options["model"]))
                approved.append((deal, verdict))
                stats["haiku_approved"] += 1
    # Repack escalations across Haiku batches, up to the existing batch cap.
    sonnet(escalated)
    # A deterministic hash sample avoids always selecting the first source.
    approved.sort(key=lambda pair: _sample_key(pair[0], cfg))
    sampled = {r[0] for r in db.conn.execute("SELECT event_key FROM critic_tier_samples")}
    approved = [(d, v) for d, v in approved if _sample_key(d, cfg) not in sampled]
    for start in range(0, len(approved), size):
        day = util.now_utc().astimezone(timezone.utc).date().isoformat()
        used = db.conn.execute("SELECT COUNT(*) FROM critic_tier_batches WHERE day=?", (day,)).fetchone()[0]
        if used >= options["shadow_batches_per_day"]:
            break
        pairs = approved[start:start + size]
        batch = [d for d, _ in pairs]
        try:
            if deadline is not None and time.monotonic() + 2 * cfg.request_timeout_s + 2 > deadline:
                break
            verdicts = critique_deals(parser, batch, shadow=True, model=cfg.critic_model,
                reserve_shadow=lambda: _claim_sample(db, batch, cfg, util.now_utc()), retry=False)
        except SpendCapReached:
            break
        for (deal, cheap), verdict in zip(pairs, verdicts):
            if not confident(verdict, .7):
                continue  # invalid/uncertain answers are never counted as agreements
            agrees = cheap["plausible"] == verdict["plausible"]
            db.conn.execute("INSERT OR IGNORE INTO critic_tier_samples VALUES (?,?,?)",
                            (_sample_key(deal, cfg), iso(util.now_utc()), int(agrees)))
            stats["sample_judged"] += 1
            stats["sample_disagreed"] += not agrees
            # A paid Sonnet rejection is authoritative, even below the trip threshold.
            save(deal, dict(verdict, model=cfg.critic_model))
        db.conn.commit()
        reason = fallback_reason(db, cfg)
        if reason:
            stats["fallback"] = reason
            log.warning("tiered critic fallback: %s", reason)
            # Withdraw ALL prior cheap approvals now, including earlier runs.
            # They retry through Sonnet under the existing caps next run.
            for deal in db.all_deals(status="alive"):
                if (deal.get("critic") or {}).get("model") == options["model"]:
                    from .dedupe import _DEAL_FIELDS

                    flags = list(dict.fromkeys([*(deal.get("flags") or []), "critic_tier_pending"]))
                    payload = {k: deal.get(k) for k in _DEAL_FIELDS if k in deal}
                    db.update_deal(deal["dedupe_key"], payload=dict(payload, flags=flags))
            break
    # The rolling comparison needs at most N facts; the fallback reason persists.
    db.conn.execute("DELETE FROM critic_tier_samples WHERE rowid NOT IN"
                    " (SELECT rowid FROM critic_tier_samples ORDER BY observed_at DESC, rowid DESC LIMIT ?)",
                    (options["window_deals"],))
    db.conn.commit()
    return stats
