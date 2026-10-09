"""Telegram channel alerts via the official Bot API (sendMessage).

Idempotency anchors:
- db.last_alert(key, 'telegram') decides whether a deal deserves a (re)send.
- alerts_sent(channel, dedup_token) is UNIQUE; the token encodes key|price|tier
  so identical re-runs never send twice.
Only extracted facts + a link + source credit ever leave this module.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import httpx

from ..config import get_secret
from .site import rewrite_affiliate

logger = logging.getLogger("dealradar.publish.telegram")

_TIER_RANKS = {"unverified": 0, "history-checked": 1, "corroborated": 2}


def tier_rank(tier: str) -> int:
    """unverified=0 < history-checked=1 < corroborated=2 (unknown -> 0)."""
    return _TIER_RANKS.get(tier, 0)


def format_message(deal: dict[str, Any]) -> str:
    """'ITEM — $FINAL (PCT% off, TIER) at STORE · conditions · via SOURCE · URL'."""
    final = deal.get("final_price")
    price = f"${final:.2f}" if final is not None else "$?"
    pct = deal.get("pct_at_checkout")
    pct_s = f"{pct:.0f}% off" if pct is not None else "?% off"
    tier = deal.get("verify_tier") or "unverified"
    parts = [
        f"{deal.get('item', '')} — {price} ({pct_s}, {tier}) at {deal.get('store', '')}"
    ]
    conditions = [c for c in (deal.get("conditions") or []) if c]
    if conditions:
        parts.append(", ".join(conditions))
    sightings = deal.get("sightings") or []
    if sightings:
        first = sightings[0]
        via = f"via {first.get('source_name', '')}"
        if first.get("source_url"):
            via = f"{via} {first['source_url']}"
        parts.append(via)
    product_url = rewrite_affiliate(deal.get("canonical_url") or "")
    if product_url:
        parts.append(product_url)
    return " · ".join(parts)


def _should_send(deal: dict[str, Any], last: dict[str, Any] | None,
                 resend_price_drop_pct: float) -> bool:
    if last is None:
        return True  # first sighting of a published deal
    final = float(deal.get("final_price") or 0.0)
    last_price = last.get("price_at_send")
    if last_price is not None:
        threshold = float(last_price) * (1.0 - resend_price_drop_pct / 100.0)
        if final <= threshold + 1e-9:  # an EXACTLY-10% drop must resend
            return True
    tier = deal.get("verify_tier") or "unverified"
    return tier_rank(tier) > tier_rank(last.get("tier_at_send") or "")


def send_alerts(db, cfg, now: datetime, *, deals: list[dict[str, Any]] | None = None,
                dry_run: bool | None = None, http_client: Any = None,
                sleep_fn: Any = None, deadline: float | None = None) -> dict[str, Any]:
    """Alert the channel about new/improved deals. Returns {sent, skipped, dry_run}.

    Dry-run semantics: an EXPLICIT dry run (the ``dry_run`` argument or
    ``cfg.telegram_dry_run``) records alerts like a real send, so repeated test
    runs stay quiet. A FALLBACK dry run (bot credentials simply missing)
    records nothing — otherwise every deal first sighted before the operator
    configures the bot would have its one first-sighting alert silently
    consumed, and configuring the bot later would alert nothing.
    ``deadline`` (time.monotonic) stops the send loop; unsent alerts are not
    recorded and therefore retry next run.
    """
    if deals is None:
        from dealradar import rank  # lazy: sibling module, avoid import at load

        deals = rank.published_deals(db, cfg, now)

    token = get_secret("TELEGRAM_BOT_TOKEN")
    channel = get_secret("TELEGRAM_CHANNEL_ID")
    explicit_dry = bool(dry_run) if dry_run is not None else bool(cfg.telegram_dry_run)
    creds_missing = not (token and channel)
    dry = explicit_dry or creds_missing
    record_in_dry = explicit_dry  # fallback dry runs must not consume alerts

    if sleep_fn is None:
        sleep_fn = time.sleep
    limit = max(1, int(cfg.telegram_messages_per_minute))
    interval = 60.0 / limit

    sent = 0
    skipped = 0
    real_posts = 0
    client = http_client
    created_client = False
    try:
        for deal in deals:
            if deadline is not None and time.monotonic() >= deadline:
                logger.warning("run deadline reached; %d alerts deferred to next run",
                               len(deals) - sent - skipped)
                break
            key = deal.get("dedupe_key", "")
            final = float(deal.get("final_price") or 0.0)
            tier = deal.get("verify_tier") or "unverified"
            last = db.last_alert(key, "telegram")
            if not _should_send(deal, last, cfg.resend_price_drop_pct):
                skipped += 1
                continue
            dedup_token = f"{key}|{final:.2f}|{tier}"
            if db.alert_exists("telegram", dedup_token):
                skipped += 1  # idempotent re-run: already sent exactly this alert
                continue
            msg = format_message(deal)
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
            real_posts += 1
            resp = client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": channel, "text": msg,
                      "disable_web_page_preview": True},
            )
            if resp.status_code != 200:
                logger.error("telegram send failed (%s) for %s; will retry next run",
                             resp.status_code, key)
                skipped += 1
                continue  # NOT recorded -> retried next run
            db.record_alert(deal_key=key, channel="telegram",
                            dedup_token=dedup_token, now=now,
                            price_at_send=final, tier_at_send=tier)
            sent += 1
    finally:
        if created_client and client is not None:
            client.close()
    return {"sent": sent, "skipped": skipped, "dry_run": dry}
