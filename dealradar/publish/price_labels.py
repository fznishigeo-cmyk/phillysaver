"""Display-only exception to the Amazon confirmation limit; no recheck policy."""

from datetime import timedelta
import json
import re

from ..util import iso, parse_iso
from ..verify import is_used_deal, price_confirmed_at, price_unconfirmed

META = "unconfirmed_label_report"
STYLE = """.price-unconfirmed{color:#68717c;font-size:12.5px;font-weight:400;line-height:1.5;margin:2px 0 6px}
@media(prefers-color-scheme:dark){.price-unconfirmed{color:#a4adb7}}
"""
BLOCK = re.compile(r"/\*unconfirmed\*/(.*?)/\*/unconfirmed\*/", re.S)


def template(text, enabled):
    return BLOCK.sub(lambda m: m[1] if enabled else "", text)


def suspect(deal):
    verdict = deal.get("critic") or {}
    return bool(deal.get("gone_suspect_at") or deal.get("death")
                or "suspect_msrp" in (deal.get("flags") or [])
                or (isinstance(verdict, dict) and verdict.get("plausible") is False)
                or (deal.get("audit") or {}).get("outcome") == "reject")


def exclusion(deal, cfg, now):
    """Empty only for an otherwise publishable offer within the outer clock."""
    from ..rank import is_publishable

    if deal.get("status", "alive") != "alive":
        return "not_alive"
    if suspect(deal):
        return "suspect"
    if not is_publishable(deal, cfg, min_pct=min(cfg.min_pct, cfg.site_show_from_pct))[0]:
        return "publish_gate"
    if not price_unconfirmed(deal, cfg, now):
        return "confirmed_or_in_grace"
    at = price_confirmed_at(deal) or parse_iso(deal.get("first_seen_at") or "")
    if at is None:
        return "missing_clock"
    if at > now:
        return "future_clock"
    hours = cfg.unconfirmed_label_max_h_used if is_used_deal(deal) else cfg.unconfirmed_label_max_h_new
    return "outer_limit" if now - at >= timedelta(hours=hours) else ""


def qualifies(deal, cfg, now):
    return cfg.unconfirmed_label_enabled and not exclusion(deal, cfg, now)


def push_excluded(deal, cfg, now):
    # Raw alert candidates have no display marker. Even those past the outer
    # limit must stay out of pushes while the display exception is enabled.
    from ..non_amazon_freshness import promotion_excluded
    from ..buybox import would_unconfirm
    return bool(promotion_excluded(deal, cfg, now) or deal.get("price_unconfirmed") or
                (cfg.keepa_buybox_rule == "on" and would_unconfirm(deal, now)) or
                (cfg.unconfirmed_label_enabled and price_unconfirmed(deal, cfg, now)))


def recovery_reason(deal):
    """Name the old scheduler diagnostic's 'other', without changing its gates."""
    if not deal.get("asin"):
        return "missing_asin"
    if price_confirmed_at(deal) is None:
        return "current_price_never_confirmed"
    return "used_hidden_recovery_due" if is_used_deal(deal) else "new_hidden_recovery_due"


def counts(rows):
    used = sum(is_used_deal(d) for d in rows)
    return {"new": len(rows) - used, "used": used, "total": len(rows)}


def diagnostic(rows, cfg, now, shown=()):
    """Current hide-only population, eligibility even when off, and public count."""
    from ..rank import is_publishable

    hidden = [d for d in rows if d.get("status") == "alive" and price_unconfirmed(d, cfg, now)
              and is_publishable(d, cfg, min_pct=min(cfg.min_pct, cfg.site_show_from_pct))[0]]
    reasons, excluded, qualifying = {}, {}, []
    for deal in hidden:
        reason = ("suspect" if suspect(deal) else
                  "missing_asin" if not deal.get("asin") else
                  "never_checked" if not any(deal.get(k) for k in (
                      "price_rechecked_at", "keepa_price_seen_at", "keepa_warehouse_seen_at")) else
                  recovery_reason(deal))
        reasons.setdefault(reason, []).append(deal)
        why = exclusion(deal, cfg, now)
        if why:
            excluded.setdefault(why, []).append(deal)
        else:
            qualifying.append(deal)
    shown = [d for d in shown if d.get("price_unconfirmed") and not d.get("locked")]
    shown_keys = {d["dedupe_key"] for d in shown}
    return {"at": iso(now), "enabled": cfg.unconfirmed_label_enabled,
            "hidden_before_label": counts(hidden), "qualifying": counts(qualifying),
            "labeled_shown": counts(shown),
            "still_hidden": counts([d for d in hidden if d["dedupe_key"] not in shown_keys]),
            "reasons": {k: counts(v) for k, v in sorted(reasons.items())},
            "exclusions": {k: counts(v) for k, v in sorted(excluded.items())}}


def report_data(db):
    try:
        return json.loads(db.meta_get(META) or "null") or {"unavailable": "no publish snapshot"}
    except (TypeError, ValueError):
        return {"unavailable": "invalid publish snapshot"}


def summary_lines(values):
    if not values or "unavailable" in values:
        return ["Unconfirmed prices: unavailable (no publish snapshot)"]
    lines = ["Unconfirmed prices (publish snapshot " + values["at"] + "):"]
    for name in ("labeled_shown", "still_hidden", "qualifying"):
        row = values[name]
        lines.append(f"  {name}: new {row['new']}, used {row['used']}, total {row['total']}")
    for group in ("reasons", "exclusions", "recheck_reasons"):
        for name, row in values.get(group, {}).items():
            lines.append(f"  {group}.{name}: new {row['new']}, used {row['used']}")
    return lines
