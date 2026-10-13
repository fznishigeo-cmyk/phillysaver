"""Admin alerting: low fetch volume and tripped-source warnings.

Warnings go to a SEPARATE admin chat (TELEGRAM_ADMIN_CHAT_ID) via the same
bot token. Without credentials the warnings are logged only. The same
warnings go out at most once a day (operator, 2026-10-13: hourly repeats were
noise); a change in the warnings (e.g. 11 -> 12 sources tripped) goes out at
once. Idempotent via alerts_sent('admin', '<YYYY-MM-DD>|<hash of text>|admin').
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any

import httpx

from ..config import get_secret

logger = logging.getLogger("dealradar.publish.admin")


def check_and_alert(db, cfg, now: datetime, *, items_fetched: int,
                    http_client: Any = None, kind: str = "hourly") -> list[str]:
    """Compute health warnings and alert the admin chat (same text: once a day).

    The low-volume check runs only for hourly runs: a 15-minute fast-lane run
    fetches a handful of fast_lane sources by design and would false-alarm
    against the 7-day hourly average every time.
    """
    warnings: list[str] = []
    if kind == "hourly":
        avg = db.avg_items_7d(now, kind=kind)
        if avg is not None and avg > 0 and items_fetched < cfg.min_items_ratio * avg:
            warnings.append(f"low volume: {items_fetched} vs 7-day avg {avg:.1f}")
    tripped = db.tripped_count(now)
    if tripped >= cfg.tripped_sources_warn:
        warnings.append(f"{tripped} sources tripped")
    if not warnings:
        return []

    digest = hashlib.sha256("\n".join(warnings).encode()).hexdigest()[:12]
    dedup_token = f"{now.strftime('%Y-%m-%d')}|{digest}|admin"
    if db.alert_exists("admin", dedup_token):
        logger.info("admin alert already sent today: %s", "; ".join(warnings))
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

    status = send_admin_text(text, http_client=http_client)
    if status != 200:
        # NOT recorded -> retried on the next run this hour.
        logger.error("admin alert send failed (%s); will retry", status)
        return warnings

    db.record_alert(deal_key="admin", channel="admin",
                    dedup_token=dedup_token, now=now)
    logger.info("admin alert sent: %s", "; ".join(warnings))
    return warnings


def send_admin_text(text: str, *, http_client: Any = None) -> int:
    """POST `text` to the admin chat; returns Telegram's HTTP status. The
    caller checks TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_CHAT_ID first."""
    token = get_secret("TELEGRAM_BOT_TOKEN")
    admin_chat = get_secret("TELEGRAM_ADMIN_CHAT_ID")
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
        return resp.status_code
    finally:
        if created_client and client is not None:
            client.close()
