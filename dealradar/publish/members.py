"""Members: Stripe subscribers receive the paid product by email.

No server of our own: Stripe hosts checkout (a Payment Link in config),
this module reads the active/trialing subscriber list from Stripe's API
each run (STRIPE_SECRET_KEY — a read-only restricted key) and caches it in
meta, and members get ONE bundled email per window carrying every new
verified deal, BCC'd over the same SMTP settings as the digest.

Idempotency: alerts_sent('email_members', <dedupe_key>) is recorded per
deal only after a successful send, so re-runs resend nothing and a failed
send retries next run. Only extracted facts + a link + source credit ever
leave this module.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..config import get_secret
from ..util import iso, parse_iso
from .telegram_pub import format_message
from .email_policy import email_line_html

log = logging.getLogger("dealradar.publish.members")

CHANNEL = "email_members"
META_MEMBERS = "members_emails"
META_LAST_EMAIL = "members_last_email_at"
STRIPE_SUBSCRIPTIONS = "https://api.stripe.com/v1/subscriptions"
#: Subscription states that count as paying members.
MEMBER_STATUSES = ("active", "trialing")
#: Cap per email so a backlog never becomes a wall of text; the rest wait.
MAX_DEALS_PER_EMAIL = 40


def fetch_member_emails(http_client: Any = None) -> list[str] | None:
    """Current member emails from Stripe; None when unconfigured or failed
    (the caller then keeps its cached list)."""
    key = get_secret("STRIPE_SECRET_KEY")
    if not key:
        return None
    client = http_client or httpx.Client(timeout=20)
    emails: set[str] = set()
    try:
        for status in MEMBER_STATUSES:
            after = None
            while True:
                params = {"status": status, "limit": "100",
                          "expand[]": "data.customer"}
                if after:
                    params["starting_after"] = after
                resp = client.get(STRIPE_SUBSCRIPTIONS, params=params,
                                  auth=(key, ""))
                resp.raise_for_status()
                body = resp.json()
                data = body.get("data") or []
                for sub in data:
                    customer = sub.get("customer")
                    email = (customer.get("email")
                             if isinstance(customer, dict) else "") or ""
                    if email:
                        emails.add(email.strip().lower())
                if not body.get("has_more") or not data:
                    break
                after = data[-1].get("id")
    except Exception as e:  # transport, auth, schema drift: keep the cache
        log.warning("stripe member list unavailable: %s", e)
        return None
    return sorted(emails)


def member_list(db, http_client: Any = None) -> list[str]:
    fresh = fetch_member_emails(http_client)
    if fresh is not None:
        db.meta_set(META_MEMBERS, json.dumps(fresh))
        return fresh
    cached = db.meta_get(META_MEMBERS)
    try:
        return list(json.loads(cached)) if cached else []
    except ValueError:
        return []


def _brand(cfg) -> str:
    domain = getattr(cfg, "custom_domain", "") or ""
    return domain.split(".")[0] if domain else "dealradar"


def _cap(cfg) -> int:
    return max(1, int(getattr(cfg, "members_max_deals_per_email", MAX_DEALS_PER_EMAIL)))


def build_member_email(deals: list[dict[str, Any]], cfg) -> tuple[str, str, str]:
    """(subject, text_body, html_body) for one bundle of new deals: the best
    _cap(cfg) of them, and a line saying how many more are on the wall."""
    more = max(0, len(deals) - _cap(cfg))
    deals = deals[:_cap(cfg)]
    top = max((float(d.get("pct_at_checkout") or 0.0) for d in deals),
              default=0.0)
    n = len(deals)
    subject = (f"{_brand(cfg)}: {n} new verified deal{'s' if n != 1 else ''} "
               f"(up to {top:.0f}% off)")
    lines = [format_message(d) for d in deals]
    footer = []
    domain = getattr(cfg, "custom_domain", "") or ""
    if more:
        footer.append(f"+{more} more new deal{'s' if more != 1 else ''} on the live wall"
                      + (f": https://{domain}/" if domain else "."))
    elif domain:
        footer.append(f"Live wall: https://{domain}/")
    portal = getattr(cfg, "portal_url", "") or ""
    if portal:
        footer.append(f"Manage membership: {portal}")
    if cfg.ftc_disclosure:
        footer.append(cfg.ftc_disclosure)
    text_body = "\n".join(lines) + "\n\n" + "\n".join(footer) + "\n"
    items = "\n".join(f"<li>{email_line_html(line, d)}</li>" for d, line in zip(deals, lines))
    foot_html = "<br>".join(html_lib.escape(f) for f in footer)
    html_body = (f"<html><body><ul>\n{items}\n</ul>"
                 f"<p>{foot_html}</p></body></html>")
    return subject, text_body, html_body


def send_member_alerts(db, cfg, now: datetime, *,
                       deals: list[dict[str, Any]] | None = None,
                       smtp_factory: Any = None,
                       http_client: Any = None) -> dict[str, Any]:
    """Email members every new verified deal, bundled, at most once per
    cfg.members_email_every_minutes (a day or more: once per local day from
    cfg.members_email_hour_local, carrying what arrived since the last one).
    Returns {members, deals, sent, skipped}."""
    stats: dict[str, Any] = {"members": 0, "deals": 0, "sent": 0, "skipped": ""}
    if not getattr(cfg, "members_email_enabled", True):
        stats["skipped"] = "member email off"  # membership is site access only
        return stats
    members = member_list(db, http_client)
    stats["members"] = len(members)
    if not members:
        stats["skipped"] = "no members"
        return stats
    host = get_secret("SMTP_HOST")
    if not host:
        stats["skipped"] = "smtp not configured"
        return stats
    if deals is None:
        from dealradar import rank  # lazy: sibling module, avoid import at load

        deals = rank.published_deals(db, cfg, now)
    last = parse_iso(db.meta_get(META_LAST_EMAIL) or "")
    window = timedelta(minutes=int(getattr(cfg, "members_email_every_minutes", 60)))
    daily = window >= timedelta(days=1)
    fresh = [d for d in deals if not db.alert_exists(CHANNEL, d["dedupe_key"])]
    if daily:
        # The day's email carries the day's deals: what arrived since the
        # last one (the last 24 h before the first), never an old backlog.
        since = last or now - timedelta(days=1)
        fresh = [d for d in fresh
                 if (parse_iso(d.get("first_seen_at") or "") or now) >= since]
    stats["deals"] = len(fresh)
    if not fresh:
        return stats
    if daily:
        # At most one email per local day (members_email_timezone), on the
        # first run at or after members_email_hour_local: anchored to the
        # clock, so late runs never push it a little later every day.
        tz = ZoneInfo(getattr(cfg, "members_email_timezone", "America/New_York"))
        local = now.astimezone(tz)
        if ((last is not None and last.astimezone(tz).date() >= local.date())
                or local.hour < int(getattr(cfg, "members_email_hour_local", 0))):
            stats["skipped"] = "within email window"  # they ride the next one
            return stats
    elif last is not None and now - last < window:
        stats["skipped"] = "within email window"  # they ride the next one
        return stats

    subject, text_body, html_body = build_member_email(fresh, cfg)
    port = int(get_secret("SMTP_PORT") or "587")
    user = get_secret("SMTP_USER")
    sender = get_secret("SMTP_FROM") or user or f"{_brand(cfg)}@{host}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = sender  # members ride BCC: one message, no address leaks
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    factory = smtp_factory or smtplib.SMTP
    try:
        server = factory(host, port)
        server.starttls()
        if user:
            server.login(user, get_secret("SMTP_PASSWORD"))
        server.send_message(msg, from_addr=sender, to_addrs=members)
        server.quit()
    except Exception as e:
        log.warning("member email failed (retries next run): %s", e)
        stats["skipped"] = "send failed"
        return stats

    sent_deals = fresh[:_cap(cfg)]
    for d in sent_deals:
        db.record_alert(deal_key=d["dedupe_key"], channel=CHANNEL,
                        dedup_token=d["dedupe_key"], now=now,
                        price_at_send=d.get("final_price"),
                        tier_at_send=d.get("verify_tier") or "")
    db.meta_set(META_LAST_EMAIL, iso(now))
    stats["deals"] = len(sent_deals)
    stats["sent"] = len(members)
    log.info("member email: %d deals to %d members", len(sent_deals), len(members))
    return stats
