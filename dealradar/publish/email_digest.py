"""Daily email digest: one email per local day, single recipient, SMTP only.

Idempotency: alerts_sent('email_digest', <local date ISO>) is UNIQUE — the
digest is recorded after a successful send. A durable outgoing claim prevents
repeating an uncertain delivery; unconfigured runs consume no delivery.
"""

from __future__ import annotations

from ..persistence import delivery_run_key, message_id, prepare_delivery

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
from .email_policy import email_line_html

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
            "<html><body><p>" + html_lib.escape(line) + "</p><p>"
            + html_lib.escape(cfg.ftc_disclosure) + "</p></body></html>"
        )
        return subject, text_body + cfg.ftc_disclosure + "\n", html_body
    lines = [format_message(d, cfg=cfg) for d in deals]
    text_body = "\n".join(lines) + "\n\n" + cfg.ftc_disclosure + "\n"
    items = "\n".join(f"<li>{email_line_html(line, d)}</li>" for d, line in zip(deals, lines))
    html_body = (f"<html><body><ul>\n{items}\n</ul>"
                 f"<p>{html_lib.escape(cfg.ftc_disclosure)}</p></body></html>")
    return subject, text_body, html_body


def smtp_connect(host: str, port: int, *, smtp_factory: Any = None):
    """Open and authenticate the transport the SMTP_* secrets describe.

    Port 465 is implicit TLS (SMTP_SSL), 587 upgrades with STARTTLS, any
    other port is used as is; SMTP_USER (when set) logs in. The caller
    sends and quits. Shared by the digest and the newsletter so the TLS
    rules live in one place; `smtp_factory` (tests) replaces the socket.
    """
    if smtp_factory is not None:
        server = smtp_factory(host, port)
    elif port == 465:
        # Implicit-TLS providers (e.g. gmail:465); 587 uses STARTTLS below.
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
    try:
        if port == 587:
            server.starttls()
        user = get_secret("SMTP_USER")
        if user:
            server.login(user, get_secret("SMTP_PASSWORD"))
    except Exception:
        try:
            server.quit()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
        raise
    return server


def maybe_send_digest(db, cfg, now: datetime, *,
                      deals: list[dict[str, Any]] | None = None,
                      smtp_factory: Any = None,
                      run_key: str | None = None) -> bool:
    """Send the daily digest once per local day, after cfg.digest_hour_local."""
    local = now.astimezone(ZoneInfo(cfg.digest_timezone))
    if local.hour < cfg.digest_hour_local:
        return False
    dedup_token = local.date().isoformat()
    if db.alert_exists("email_digest", dedup_token) or db.outgoing_exists("email_digest", dedup_token):
        return False  # already sent today
    host = get_secret("SMTP_HOST")
    if not cfg.digest_recipient or not host:
        # Deliberately NOT recorded: configuring later still sends that day.
        logger.info("digest skipped: digest_recipient or SMTP_HOST not configured")
        return False

    if deals is None:
        from dealradar import rank  # lazy: sibling module, avoid import at load

        deals = rank.published_deals(db, cfg, now)
    from .price_labels import push_excluded
    deals = [d for d in deals if not push_excluded(d, cfg, now)]
    fresh = []
    for deal in deals:
        first_seen = parse_iso(deal.get("first_seen_at") or "")
        if first_seen is not None and clamp_window_24h(first_seen, now):
            fresh.append(deal)

    selected = db.delivery_batch("email_digest", delivery_run_key(now, run_key), [dedup_token])
    if dedup_token not in selected:
        return False
    subject, text_body, html_body = build_digest(fresh, cfg, now)
    port = int(get_secret("SMTP_PORT") or "587")
    user = get_secret("SMTP_USER")
    sender = get_secret("SMTP_FROM") or user or f"dealradar@{host}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = cfg.digest_recipient  # single recipient only, never a list
    msg["Message-ID"] = message_id("email_digest", dedup_token)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        server = smtp_connect(host, port, smtp_factory=smtp_factory)
        try:
            if not prepare_delivery(db, "email_digest", dedup_token, now, run_key):
                return False
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
    except Exception:
        logger.exception("digest send failed")
        return False

    db.record_alert(deal_key="digest", channel="email_digest",
                    dedup_token=dedup_token, now=now)
    db.finish_outgoing("email_digest", dedup_token, now)
    logger.info("digest sent to %s (%d deals)", cfg.digest_recipient, len(fresh))
    return True
