"""Admin alerting: low fetch volume and tripped-source warnings.

Warnings go to a SEPARATE admin chat (TELEGRAM_ADMIN_CHAT_ID) via the same
bot token. Without credentials the warnings are logged only. Idempotent per
hour via alerts_sent('admin', '<YYYY-MM-DDTHH>|admin').
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from ..config import get_secret

logger = logging.getLogger("dealradar.publish.admin")


def check_and_alert(db, cfg, now: datetime, *, items_fetched: int,
                    http_client: Any = None) -> list[str]:
    """Compute health warnings and alert the admin chat (at most once/hour)."""
    warnings: list[str] = []
    avg = db.avg_items_7d(now)
    if avg is not None and avg > 0 and items_fetched < cfg.min_items_ratio * avg:
        warnings.append(f"low volume: {items_fetched} vs 7-day avg {avg:.1f}")
    tripped = db.tripped_count(now)
    if tripped >= cfg.tripped_sources_warn:
        warnings.append(f"{tripped} sources tripped")
    if not warnings:
        return []

    dedup_token = now.strftime("%Y-%m-%dT%H") + "|admin"
    if db.alert_exists("admin", dedup_token):
        logger.info("admin alert already handled this hour: %s", "; ".join(warnings))
        return warnings

    text = "dealradar admin warnings:\n" + "\n".join(warnings)
    token = get_secret("TELEGRAM_BOT_TOKEN")
    admin_chat = get_secret("TELEGRAM_ADMIN_CHAT_ID")
    if not (token and admin_chat):
        for w in warnings:
            logger.warning("admin alert (no credentials, log only): %s", w)
        db.record_alert(deal_key="admin", channel="admin",
                        dedup_token=dedup_token, now=now)
        return warnings

    client = http_client
    created_client = False
    try:
        if client is None:
            client = httpx.Client(timeout=20)
            created_client = True
        resp = client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": admin_chat, "text": text,
                  "disable_web_page_preview": True},
        )
        if resp.status_code != 200:
            # NOT recorded -> retried on the next run this hour.
            logger.error("admin alert send failed (%s); will retry", resp.status_code)
            return warnings
    finally:
        if created_client and client is not None:
            client.close()

    db.record_alert(deal_key="admin", channel="admin",
                    dedup_token=dedup_token, now=now)
    logger.info("admin alert sent: %s", "; ".join(warnings))
    return warnings
