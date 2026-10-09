"""Daily email digest: one email per local day, single recipient, SMTP only.

Idempotency: alerts_sent('email_digest', <local date ISO>) is UNIQUE — the
digest is recorded only after a successful send, so a failed or unconfigured
run still sends later that same day once things are fixed.
"""

from __future__ import annotations

import html as html_lib
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from zoneinfo import ZoneInfo

from ..config import get_secret
from ..util import clamp_window_24h, parse_iso
from .telegram_pub import format_message

logger = logging.getLogger("dealradar.publish.email")


def build_digest(deals: list[dict[str, Any]], cfg, now: datetime) -> tuple[str, str, str]:
    """Return (subject, text_body, html_body) for the daily digest."""
    local = now.astimezone(ZoneInfo(cfg.digest_timezone))
    subject = (
        f"dealradar digest — {len(deals)} deals >={cfg.min_pct:g}% — "
        f"{local.strftime('%b %d')}"
    )
    if not deals:
        line = "no qualifying deals in the last 24 h"
        text_body = line + "\n"
        html_body = (
            "<html><body><p>" + html_lib.escape(line) + "</p></body></html>"
        )
        return subject, text_body, html_body
    lines = [format_message(d) for d in deals]
    text_body = "\n".join(lines) + "\n"
    items = "\n".join(f"<li>{html_lib.escape(line)}</li>" for line in lines)
    html_body = f"<html><body><ul>\n{items}\n</ul></body></html>"
    return subject, text_body, html_body


def maybe_send_digest(db, cfg, now: datetime, *,
                      deals: list[dict[str, Any]] | None = None,
                      smtp_factory: Any = None) -> bool:
    """Send the daily digest once per local day, after cfg.digest_hour_local."""
    local = now.astimezone(ZoneInfo(cfg.digest_timezone))
    if local.hour < cfg.digest_hour_local:
        return False
    dedup_token = local.date().isoformat()
    if db.alert_exists("email_digest", dedup_token):
        return False  # already sent today
    host = get_secret("SMTP_HOST")
    if not cfg.digest_recipient or not host:
        # Deliberately NOT recorded: configuring later still sends that day.
        logger.info("digest skipped: digest_recipient or SMTP_HOST not configured")
        return False

    if deals is None:
        from dealradar import rank  # lazy: sibling module, avoid import at load

        deals = rank.published_deals(db, cfg, now)
    fresh = []
    for deal in deals:
        first_seen = parse_iso(deal.get("first_seen_at") or "")
        if first_seen is not None and clamp_window_24h(first_seen, now):
            fresh.append(deal)

    subject, text_body, html_body = build_digest(fresh, cfg, now)
    port = int(get_secret("SMTP_PORT") or "587")
    user = get_secret("SMTP_USER")
    sender = get_secret("SMTP_FROM") or user or f"dealradar@{host}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = cfg.digest_recipient  # single recipient only, never a list
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    factory = smtp_factory or smtplib.SMTP
    try:
        server = factory(host, port)
        try:
            if port == 587:
                server.starttls()
            if user:
                server.login(user, get_secret("SMTP_PASSWORD"))
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
    except Exception:
        logger.exception("digest send failed; will retry next run")
        return False

    db.record_alert(deal_key="digest", channel="email_digest",
                    dedup_token=dedup_token, now=now)
    logger.info("digest sent to %s (%d deals)", cfg.digest_recipient, len(fresh))
    return True
