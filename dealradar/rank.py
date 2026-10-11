"""Ranking and publish gating.

A deal is publishable only when it is alive, explicitly online-only, priced in
USD for the US, and clears both savings floors (pct_at_checkout >= cfg.min_pct
and abs_savings >= cfg.min_abs_savings). Everything else stays in the DB but
is never published.

Score = pct_at_checkout
      + tier boost (corroborated > history-checked > unverified)
      + capped absolute-savings bonus
      - per-condition penalty
      - age penalty per hour since first sighting.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .util import parse_iso

log = logging.getLogger("dealradar.rank")


def _age_hours(first_seen_at: str, now: datetime) -> float:
    dt = parse_iso(first_seen_at)
    if dt is None:
        return 0.0
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def score(deal: dict[str, Any], cfg, now: datetime) -> float:
    """Ranking score for one deal row dict (higher is better)."""
    pct = float(deal.get("pct_at_checkout") or 0.0)
    boost = float(cfg.tier_boost.get(deal.get("verify_tier", "unverified"), 0))
    abs_bonus = min(float(deal.get("abs_savings") or 0.0), 100.0) \
        * cfg.abs_savings_weight
    cond_penalty = len(deal.get("conditions") or []) * cfg.condition_penalty
    age_penalty = _age_hours(deal.get("first_seen_at", ""), now) \
        * cfg.age_penalty_per_hour
    return pct + boost + abs_bonus - cond_penalty - age_penalty


def is_publishable(deal: dict[str, Any], cfg,
                   min_pct: float | None = None) -> tuple[bool, str]:
    """All publish gates; returns (False, reason) naming the first failure.

    ``min_pct`` overrides cfg.min_pct (the site's lower "strong deals" tier
    reuses every other gate unchanged); alerts always use cfg.min_pct.
    """
    status = deal.get("status", "alive")
    if status != "alive":
        return False, f"status: {status!r} is not 'alive'"

    online_only = deal.get("online_only", "unknown")
    if online_only != "true":
        return False, (f"online_only: {online_only!r} (only explicit 'true' "
                       "is publishable)")

    currency = deal.get("currency") or "USD"
    if currency != "USD":
        return False, f"currency: {currency!r} is not 'USD'"

    if getattr(cfg, "exclude_suspect_msrp", True) \
            and "suspect_msrp" in (deal.get("flags") or []):
        return False, ("flags: suspect_msrp (deal critic judged the claimed "
                       "regular price implausible)")

    region = deal.get("region") or "US"
    if region != "US":
        return False, f"region: {region!r} is not 'US'"

    floor = cfg.min_pct if min_pct is None else min_pct
    pct = deal.get("pct_at_checkout")
    if pct is None or pct < floor:
        return False, f"pct_at_checkout: {pct!r} < min_pct {floor}"

    # Sanity: "100% off" while money changes hands is a contradiction
    # (seen live: a $254.98 phone published as 100% off via a rebate rule).
    final = deal.get("final_price")
    if pct >= 99.95 and final is not None and final > 0.01:
        return False, (f"inconsistent: pct_at_checkout {pct:g} claims free "
                       f"but final_price is ${final:g}")

    # Used/open-box supply is plentiful: such deals clear a HIGHER absolute
    # floor, and the site's lower "strong deals" tier never softens it.
    if {"used", "open_box"} & set(deal.get("conditions") or []):
        used_floor = getattr(cfg, "used_min_pct", 80.0)
        if pct < used_floor:
            return False, (f"pct_at_checkout: {pct!r} < used_min_pct "
                           f"{used_floor} (used/open-box)")

    abs_savings = deal.get("abs_savings")
    if abs_savings is None or abs_savings < cfg.min_abs_savings:
        return False, (f"abs_savings: {abs_savings!r} < min_abs_savings "
                       f"{cfg.min_abs_savings}")

    return True, ""


def published_deals(db, cfg, now: datetime) -> list[dict[str, Any]]:
    """Alive deals that pass every publish gate, scored, best first.

    This is the ALERT set (Telegram, digest): dead deals never alert.
    """
    out: list[dict[str, Any]] = []
    for deal in db.all_deals(status="alive"):
        ok, reason = is_publishable(deal, cfg)
        if not ok:
            log.debug("deal %s not publishable: %s",
                      deal.get("dedupe_key", "?"), reason)
            continue
        deal["verify_tier"] = deal.get("verify_tier", "unverified")
        deal["score"] = score(deal, cfg, now)
        out.append(deal)
    out.sort(key=lambda d: d["score"], reverse=True)
    return out


def site_deals(db, cfg, now: datetime) -> list[dict[str, Any]]:
    """The SITE set: every deal clearing the gates at the SITE floor
    (cfg.site_show_from_pct, never above cfg.min_pct), alive or recently
    dead (dead ones shown marked dead; the spec wants them visible on the
    page, never alerted). Deals between the site floor and cfg.min_pct are
    the page's "strong deals" tier — on the page, never alerted. Retention
    prunes dead rows cfg.deals_days after they died; a live deal is never
    removed for its age."""
    from datetime import timedelta

    site_floor = min(cfg.min_pct,
                     getattr(cfg, "site_show_from_pct", cfg.min_pct))
    max_dead_age = timedelta(hours=getattr(cfg, "show_dead_hours", 6.0))
    out: list[dict[str, Any]] = []
    for status in ("alive", "dead"):
        for deal in db.all_deals(status=status):
            if status == "dead":
                died = parse_iso(deal.get("died_at") or "")
                # No stamp, or dead too long: off the page. A fresh death
                # stays briefly as honest "just missed it" proof.
                if died is None or now - died > max_dead_age:
                    continue
            probe = dict(deal)
            probe["status"] = "alive"  # dead rows: test the other gates only
            ok, _reason = is_publishable(probe, cfg, min_pct=site_floor)
            if not ok:
                continue
            deal["verify_tier"] = deal.get("verify_tier", "unverified")
            deal["score"] = score(deal, cfg, now)
            out.append(deal)
    out.sort(key=lambda d: d["score"], reverse=True)
    return out
