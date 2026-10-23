"""Telegram channel alerts via the official Bot API (sendMessage).

Idempotency anchors:
- db.last_alert(key, 'telegram') excludes deals already posted, including
  alerts recorded before the channel switched to curated picks.
- alerts_sent(channel, dedup_token) is UNIQUE; the token encodes key|channel.
The channel carries price-free facts and a live wowpriced deal-page link.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..savings import display_pct
from ..persistence import delivery_run_key, prepare_delivery
from ..config import get_secret
from ..util import parse_iso
from .site import rewrite_affiliate
from .social import _candidates, _word_trim, _without_prices, social_title
from .email_policy import email_credits, email_deal
from . import amazon_history

logger = logging.getLogger("dealradar.publish.telegram")

_TIER_RANKS = {"unverified": 0, "history-checked": 1, "corroborated": 2, "trusted": 2}


def tier_rank(tier: str) -> int:
    """unverified=0 < history-checked=1 < corroborated=trusted=2 (unknown -> 0)."""
    return _TIER_RANKS.get(tier, 0)


#: How a deal was corroborated, in the reader's words (same labels as the
#: site's badges). Every alerted deal already passed the verbatim-price
#: check and the critic, so a single-source deal simply carries no label
#: (a trusted one too: its "via <source>" credit already names it).
_TIER_WORDS = {"corroborated": "2+ sources", "history-checked": "price history"}


def format_message(deal: dict[str, Any], path: str | None = None,
                   disclosure: str = "#ad", *, cfg=None) -> str:
    """Price-free channel copy with a deal path; preserve the email format otherwise."""
    from .. import amazon_reference
    from ..util import now_utc
    deal = amazon_reference.project(deal, cfg, now_utc())
    if path is not None:
        from .traffic import tagged_url

        item = social_title(str(deal.get("item") or ""), str(deal.get("brand") or ""))
        store = _without_prices(str(deal.get("store") or ""))
        pct = deal.get("pct_at_checkout")
        lead = f"{display_pct(pct)}% OFF" if pct is not None else "Price drop"
        disclosure = _word_trim(_without_prices(disclosure), 100) or "#ad"
        url = tagged_url("https://wowpriced.com", path, "telegram", "social")
        return (f"🔥 {lead}: {item}\n"
                f"At {store}, verified by wowpriced: {url}\n"
                f"{disclosure}")

    hide_history = amazon_history.hidden(cfg, deal)
    deal = email_deal(deal)
    final = deal.get("final_price")
    price = f"${final:.2f}" if final is not None else "$?"
    pct = deal.get("pct_at_checkout")
    pct_s = f"{display_pct(pct)}% off" if pct is not None else "Price drop" if amazon_reference.enabled(cfg) else "?% off"
    tier = _TIER_WORDS.get(deal.get("verify_tier") or "", "")
    if deal.get("verify_tier") == "history-checked" and hide_history:
        tier = "verified"
    head = f"{pct_s}, {tier}" if tier else pct_s
    parts = [
        f"{deal.get('item', '')} — "
        + ("" if deal["email_hide_price"] else price + " ")
        + f"({head}) at {deal.get('store', '')}"
    ]
    conditions = [c for c in (deal.get("conditions") or []) if c]
    if conditions:
        parts.append(", ".join(conditions))
    for name, url in email_credits(deal):
        via = f"via {name}"
        if url:
            via = f"{via} {url}"
        parts.append(via)
    product_url = rewrite_affiliate(deal.get("canonical_url") or "")
    if product_url:
        parts.append(product_url)
    return " · ".join(parts)


def _should_send(last: dict[str, Any] | None) -> bool:
    return last is None  # channel posts each deal once, regardless of price or tier


def send_alerts(db, cfg, now: datetime, *, dry_run: bool | None = None, http_client: Any = None,
                sleep_fn: Any = None, deadline: float | None = None,
                run_key: str | None = None) -> dict[str, Any]:
    """Post capped public picks to the channel. Returns {sent, skipped, dry_run}.

    Dry-run semantics: an EXPLICIT dry run (the ``dry_run`` argument or
    ``cfg.telegram_dry_run``) records alerts like a real send, so repeated test
    runs stay quiet. A FALLBACK dry run (bot credentials simply missing)
    records nothing — otherwise every deal first sighted before the operator
    configures the bot would have its one first-sighting alert silently
    consumed, and configuring the bot later would alert nothing.
    ``deadline`` (time.monotonic) stops the send loop; unsent alerts are not
    recorded and therefore retry next run.
    """
    token = get_secret("TELEGRAM_BOT_TOKEN")
    channel = get_secret("TELEGRAM_CHANNEL_ID")
    explicit_dry = bool(dry_run) if dry_run is not None else bool(cfg.telegram_dry_run)
    creds_missing = not (token and channel)
    dry = explicit_dry or creds_missing
    record_in_dry = explicit_dry  # fallback dry runs must not consume alerts

    now = now.astimezone(timezone.utc)
    zone = ZoneInfo("America/New_York")
    local = now.astimezone(zone)
    settings = cfg.telegram_channel
    start, end = settings.get("hours_local", [8, 23])
    result = {"sent": 0, "skipped": 0, "dry_run": dry}
    if not int(start) <= local.hour < int(end):
        return result

    alerts = db.conn.execute(
        "SELECT deal_key, dedup_token, sent_at FROM alerts_sent WHERE channel='telegram'"
    ).fetchall()
    attempted = {row["dedup_token"]: row["sent_at"] for row in alerts}
    outgoing = db.outgoing_for("telegram")
    for entry in outgoing:
        attempted.setdefault(entry["dedup_token"], entry["started_at"])
    sent_at = [at for value in attempted.values() if (at := parse_iso(value))]
    today = sum(at.astimezone(zone).date() == local.date() for at in sent_at)
    cap = min(max(0, int(settings.get("posts_per_run", 1))),
              max(0, int(settings.get("daily_max", 24)) - today))
    gap_seconds = max(0.0, float(settings.get("min_gap_minutes", 30))) * 60
    if not cap or (sent_at and (now - max(sent_at)).total_seconds() < gap_seconds):
        return result
    posted = {row["deal_key"]: True for row in alerts}
    posted.update({row["dedup_token"].removesuffix("|telegram"): True for row in outgoing})
    try:
        candidates = _candidates(db, cfg, posted)
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("telegram: published picks unreadable; nothing posted this run")
        return result
    # The pipeline posts before this run's deploy: only link pages that an
    # earlier deploy already put live, so Telegram's preview never 404s.
    min_age = float(settings.get("min_age_minutes", 20)) * 60
    if min_age > 0:
        candidates = [(deal, path) for deal, path in candidates
                      if (seen := parse_iso(deal.get("first_seen_at") or "")) is not None
                      and (now - seen).total_seconds() >= min_age]
    candidates = candidates[:cap]
    if not creds_missing or explicit_dry:
        selected = db.delivery_batch("telegram", delivery_run_key(now, run_key),
                                     [d["dedupe_key"] for d, _ in candidates])
        candidates = [(d, path) for d, path in candidates if d["dedupe_key"] in selected]

    if sleep_fn is None:
        sleep_fn = time.sleep
    limit = max(1, int(cfg.telegram_messages_per_minute))
    interval = max(1.0, cfg.min_host_interval_s, 60.0 / limit)

    sent = 0
    skipped = 0
    real_posts = 0
    client = http_client
    created_client = False
    try:
        for deal, path in candidates:
            # A positive gap defers the next pick to a later run; never wait
            # minutes inside the pipeline. A zero gap permits capped batches.
            if sent >= cap or (sent and gap_seconds > 0):
                break
            if deadline is not None and time.monotonic() >= deadline:
                logger.warning("run deadline reached; %d alerts deferred to next run",
                               len(candidates) - sent - skipped)
                break
            key = deal.get("dedupe_key", "")
            final = float(deal.get("final_price") or 0.0)
            tier = deal.get("badge") or "unverified"
            last = db.last_alert(key, "telegram")
            if not _should_send(last):
                skipped += 1
                continue
            dedup_token = f"{key}|telegram"
            if db.alert_exists("telegram", dedup_token):
                skipped += 1  # idempotent re-run: already sent exactly this alert
                continue
            msg = format_message(deal, path, settings.get("disclosure", "#ad"), cfg=cfg)
            if dry:
                logger.info("TELEGRAM DRY-RUN: %s", msg)
                print(f"TELEGRAM DRY-RUN: {msg}")
                if record_in_dry:
                    db.record_alert(deal_key=key, channel="telegram",
                                    dedup_token=dedup_token, now=now,
                                    price_at_send=final, tier_at_send=tier)
                sent += 1
                continue
            if client is None:
                client = httpx.Client(timeout=20)
                created_client = True
            if real_posts > 0:
                sleep_fn(interval)  # <= telegram_messages_per_minute
            if deadline is not None and time.monotonic() >= deadline:
                break
            real_posts += 1
            if not prepare_delivery(db, "telegram", dedup_token, now, run_key):
                skipped += 1
                continue
            try:
                resp = client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": channel, "text": msg,
                          "disable_web_page_preview": False},
                )
            except httpx.HTTPError as exc:
                # A timeout may still have delivered it: keep the outgoing claim
                # (never resent) and stop for this run; the deploy must go on.
                logger.error("telegram send failed (%s) for %s; stopping this run",
                             type(exc).__name__, key)
                skipped += 1
                break
            if resp.status_code != 200:
                if 400 <= resp.status_code < 500:
                    db.finish_outgoing("telegram", dedup_token, now, rejected=True)
                logger.error("telegram send failed (%s) for %s",
                             resp.status_code, key)
                skipped += 1
                continue  # uncertain deliveries retain their outgoing claim
            db.record_alert(deal_key=key, channel="telegram",
                            dedup_token=dedup_token, now=now,
                            price_at_send=final, tier_at_send=tier)
            db.finish_outgoing("telegram", dedup_token, now)
            sent += 1
    finally:
        if created_client and client is not None:
            client.close()
    return {"sent": sent, "skipped": skipped, "dry_run": dry}
