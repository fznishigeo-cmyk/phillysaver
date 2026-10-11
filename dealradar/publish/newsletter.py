"""Free daily newsletter: the day's best verified deals, one email per
subscriber per local day, with no server of our own.

The list lives in a Cloudflare Worker + KV (cloudflare/subscribe-worker):
the site's form POSTs to it, and each run at/after cfg.newsletter_hour_local
this module reads the list through the Worker's GET /list (bearer
NEWSLETTER_SECRET), builds ONE body from the top published deals and sends
it to every address over the same SMTP transport as the digest, at most
cfg.newsletter_max_per_run messages per run.

Idempotency: meta "newsletter:<local date>" holds the JSON list of
fingerprints (hashes, never addresses: state.db lives on a git branch) of
the subscribers already sent that day, updated after EVERY successful
send. A re-run of the same run key sends nothing twice, a capped batch
continues next run, and a failed recipient (or an unreachable list) is
simply retried next run. Nothing is recorded for a skipped day, so
configuring things later still sends. Only extracted facts, a product
link, the retailer's thumbnail URL and the source credit leave this module.

Double opt-in: GET /list returns confirmed addresses only. A fresh sign-up
sits on GET /list?pending=1 until send_confirmations (every run, the fast
lane included) emails it ONE confirmation link (<worker_url>/confirm?e=&t=)
and reports that through POST /list/confirm-sent. The Worker's
confirm_sent_at is the record; meta CONFIRM_META_KEY keeps the fingerprints
emailed in the last 24 h so a failed confirm-sent POST cannot mean a second
email the same day.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import logging
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

from ..config import get_secret
from .email_digest import smtp_connect
from .site import rewrite_affiliate

log = logging.getLogger("dealradar.publish.newsletter")

#: Top deals per email, best score first (rank.published_deals order).
MAX_DEALS = 25
#: Stands in for the per-subscriber unsubscribe link in the shared bodies.
UNSUB_PLACEHOLDER = "{{unsubscribe_url}}"
#: Confirmation links per run: the pass rides every run and must stay quick.
MAX_CONFIRMATIONS_PER_RUN = 50
#: meta key: JSON {fingerprint: ISO time} of the addresses emailed a
#: confirmation link in the last CONFIRM_GUARD_HOURS (hashes, never addresses).
CONFIRM_META_KEY = "newsletter:confirm_sent"
CONFIRM_GUARD_HOURS = 24


def meta_key(day: str) -> str:
    """meta key holding the JSON list of fingerprints already sent that day."""
    return f"newsletter:{day}"


def fingerprint(email: str) -> str:
    """What the progress list records instead of the address."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]


def _brand(cfg) -> str:
    domain = getattr(cfg, "custom_domain", "") or ""
    return domain.split(".")[0] if domain else "dealradar"


def _mask(email: str) -> str:
    """a***@host — subscriber addresses never land in the run log whole."""
    local, _, host = email.partition("@")
    return f"{local[:1]}***@{host}"


def fetch_subscribers(worker_url: str, secret: str,
                      http_client: Any = None, *,
                      pending: bool = False) -> list[dict[str, str]] | None:
    """[{email, token}] from the Worker, sorted; None when it is unreachable
    (the caller skips this run and tries again next run). GET /list is the
    confirmed subscribers; ``pending`` asks /list?pending=1 instead: the
    sign-ups not yet emailed a confirmation link."""
    url = worker_url.rstrip("/") + ("/list?pending=1" if pending else "/list")
    headers = {"Authorization": f"Bearer {secret}"}
    try:
        if http_client is not None:
            resp = http_client.get(url, headers=headers)
        else:
            with httpx.Client(timeout=20) as client:
                resp = client.get(url, headers=headers)
        resp.raise_for_status()
        rows = resp.json().get("emails") or []
    except Exception as e:  # transport, auth, schema drift: retry next run
        log.warning("newsletter %s list unavailable (retries next run): %s",
                    "pending" if pending else "subscriber", e)
        return None
    seen: dict[str, str] = {}
    for row in rows:
        email = str(row.get("email") or "").strip().lower()
        token = str(row.get("token") or "")
        if email and token:
            seen.setdefault(email, token)
    return [{"email": e, "token": t} for e, t in sorted(seen.items())]


def _deal_url(deal: dict[str, Any]) -> str:
    # Same rule as site.py: the product link through the (identity)
    # affiliate hook; without one, the source post that reported it.
    canon = deal.get("canonical_url") or ""
    if canon:
        return rewrite_affiliate(canon)
    sightings = deal.get("sightings") or []
    return (sightings[0].get("source_url") or "") if sightings else ""


def _credit(deal: dict[str, Any]) -> tuple[str, str]:
    sightings = deal.get("sightings") or []
    if not sightings:
        return "", ""
    return (sightings[0].get("source_name") or "",
            sightings[0].get("source_url") or "")


def _row_html(deal: dict[str, Any]) -> str:
    esc = html_lib.escape
    url = _deal_url(deal)
    item = esc(deal.get("item") or "")
    final = deal.get("final_price")
    reg = deal.get("regular_price")
    pct = deal.get("pct_at_checkout")
    src_name, src_url = _credit(deal)
    cell = "padding:12px 0;border-bottom:1px solid #e2e6eb;vertical-align:top"

    thumb = ""
    if deal.get("image_url"):
        img = (f'<img src="{esc(deal["image_url"])}" width="80" height="80" alt=""'
               ' style="display:block;width:80px;height:80px;object-fit:contain;'
               'border:1px solid #e2e6eb;border-radius:8px;background:#fff">')
        thumb = f'<a href="{esc(url)}">{img}</a>' if url else img
    title = (f'<a href="{esc(url)}" style="color:#1a1d21;text-decoration:none">'
             f"{item}</a>" if url else item)
    price = f'<span style="font-size:18px;font-weight:800">${final:,.2f}</span>' \
        if final is not None else ""
    if reg:
        price += f' <s style="color:#616a75">${reg:,.2f}</s>'
    if pct is not None:
        price += f' <span style="color:#107a3d;font-weight:700">{pct:.0f}% off</span>'
    price += f' <span style="color:#616a75">at {esc(deal.get("store") or "")}</span>'
    actions = ""
    if url:
        actions += (f'<a href="{esc(url)}" style="display:inline-block;background:#0e7569;'
                    'color:#ffffff;font-weight:700;padding:7px 12px;border-radius:8px;'
                    'text-decoration:none">Get deal</a> ')
    if src_name:
        via = (f'<a href="{esc(src_url)}" style="color:#616a75">{esc(src_name)}</a>'
               if src_url else esc(src_name))
        actions += f'<span style="color:#616a75;font-size:12px">via {via}</span>'
    return (f'<tr><td style="{cell};width:88px">{thumb}</td>'
            f'<td style="{cell};padding-left:12px">'
            f'<div style="font-size:15px;font-weight:600;line-height:1.35">{title}</div>'
            f'<div style="margin:4px 0 8px">{price}</div>'
            f"<div>{actions}</div></td></tr>")


def build_newsletter(deals: list[dict[str, Any]], cfg,
                     now: datetime) -> tuple[str, str, str]:
    """(subject, text_body, html_body) for the day's email, built ONCE for
    everyone: both bodies carry UNSUB_PLACEHOLDER, swapped per subscriber at
    send time. Facts, links and credit only."""
    deals = deals[:MAX_DEALS]
    brand = _brand(cfg)
    domain = getattr(cfg, "custom_domain", "") or ""
    site = f"https://{domain}/" if domain else ""
    local = now.astimezone(ZoneInfo(cfg.newsletter_timezone))
    n = len(deals)
    top = max((float(d.get("pct_at_checkout") or 0.0) for d in deals),
              default=0.0)
    subject = (f"{brand}: {n} verified deal{'s' if n != 1 else ''}, "
               f"up to {top:.0f}% off")
    heading = f"The day's best verified deals — {local.strftime('%A, %b %d')}"
    because = f"You get this because you subscribed at {domain or brand}."
    disclosure = getattr(cfg, "ftc_disclosure", "") or ""

    lines = [f"{brand}: {heading}", ""]
    for i, d in enumerate(deals, 1):
        final = d.get("final_price")
        bits = []
        if d.get("regular_price"):
            bits.append(f"was ${d['regular_price']:,.2f}")
        if d.get("pct_at_checkout") is not None:
            bits.append(f"{d['pct_at_checkout']:.0f}% off")
        line = (f"{i}. {d.get('item') or ''} — "
                + (f"${final:,.2f}" if final is not None else "$?")
                + (f" ({', '.join(bits)})" if bits else "")
                + f" at {d.get('store') or ''}")
        lines.append(line)
        url = _deal_url(d)
        if url:
            lines.append(f"   Get deal: {url}")
        src_name, src_url = _credit(d)
        if src_name:
            lines.append(f"   via {src_name}" + (f": {src_url}" if src_url else ""))
        lines.append("")
    if site:
        lines.append(f"Live wall: {site}")
    lines.append(f"{because} Unsubscribe any time: {UNSUB_PLACEHOLDER}")
    if disclosure:
        lines.append(disclosure)
    text_body = "\n".join(lines) + "\n"

    esc = html_lib.escape
    rows = "\n".join(_row_html(d) for d in deals)
    muted = "color:#616a75;font-size:12px;line-height:1.5;margin:10px 0 0"
    html_body = (
        '<!doctype html><html><body style="margin:0;background:#f3f5f7;color:#1a1d21;'
        'font:15px/1.5 system-ui,-apple-system,\'Segoe UI\',sans-serif">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        '<tr><td align="center" style="padding:20px 12px">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        'style="max-width:600px;width:100%;background:#ffffff;border:1px solid #e2e6eb;'
        'border-radius:14px;padding:20px 22px">'
        f'<tr><td><div style="font-size:22px;font-weight:800">{esc(brand)}'
        '<span style="color:#0e7569">●</span></div>'
        f'<div style="color:#616a75;font-size:13px">{esc(heading)}</div></td></tr>'
        f'<tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        f"{rows}</table></td></tr>"
        "<tr><td>"
        + (f'<p style="{muted}"><a href="{esc(site)}" style="color:#0b63c4">'
           f"Open the live deal wall</a></p>" if site else "")
        + f'<p style="{muted}">{esc(because)} '
        f'<a href="{UNSUB_PLACEHOLDER}" style="color:#616a75">Unsubscribe</a> any time.</p>'
        + (f'<p style="{muted}">{esc(disclosure)}</p>' if disclosure else "")
        + "</td></tr></table></td></tr></table></body></html>"
    )
    return subject, text_body, html_body


def send_daily_newsletter(db, cfg, now: datetime, *,
                          deals: list[dict[str, Any]] | None = None,
                          smtp_factory: Any = None,
                          http_client: Any = None,
                          deadline: float | None = None) -> dict[str, Any]:
    """The day's email to every subscriber not yet sent today, at most
    cfg.newsletter_max_per_run per run and never past ``deadline`` (a
    time.monotonic() value: the run's budget; a job killed by its timeout
    would lose the state.db commit and re-send). Returns {sent, skipped,
    day}; the `skipped` reason is "" whenever messages went out."""
    local = now.astimezone(ZoneInfo(cfg.newsletter_timezone))
    day = local.date().isoformat()
    stats: dict[str, Any] = {"sent": 0, "skipped": "", "day": day}

    def skip(reason: str, emit=log.info) -> dict[str, Any]:
        emit("newsletter skipped: %s", reason)
        stats["skipped"] = reason
        return stats

    if not getattr(cfg, "newsletter_enabled", True):
        return skip("disabled in config", log.debug)
    if local.hour < cfg.newsletter_hour_local:
        return skip(f"before {cfg.newsletter_hour_local:02d}:00 "
                    f"{cfg.newsletter_timezone}", log.debug)
    worker_url = (getattr(cfg, "newsletter_worker_url", "") or "").rstrip("/")
    secret = get_secret("NEWSLETTER_SECRET")
    host = get_secret("SMTP_HOST")
    missing = [name for name, value in (("newsletter.worker_url", worker_url),
                                        ("NEWSLETTER_SECRET", secret),
                                        ("SMTP_HOST", host)) if not value]
    if missing:
        # Deliberately nothing recorded: configuring later still sends today.
        return skip(", ".join(missing) + " not configured")

    subscribers = fetch_subscribers(worker_url, secret, http_client)
    if subscribers is None:
        return skip("subscriber list unavailable")
    if not subscribers:
        return skip("no subscribers", log.debug)
    key = meta_key(day)
    try:
        done = set(json.loads(db.meta_get(key) or "[]"))
    except ValueError:
        done = set()
    pending = [s for s in subscribers if fingerprint(s["email"]) not in done]
    if not pending:
        return skip("everyone already sent today", log.debug)
    pending = pending[:cfg.newsletter_max_per_run]
    if not done:
        # First send of a new day: yesterday's progress list has done its
        # job, so it shrinks to nothing rather than piling up in state.db.
        prev = meta_key((local.date() - timedelta(days=1)).isoformat())
        if db.meta_get(prev) not in (None, "[]"):
            db.meta_set(prev, "[]")

    if deals is None:
        from dealradar import rank  # lazy: sibling module, avoid import at load

        deals = rank.published_deals(db, cfg, now)
    if not deals:
        return skip("no published deals yet today")  # tried again next run

    subject, text_body, html_body = build_newsletter(deals, cfg, now)
    port = int(get_secret("SMTP_PORT") or "587")
    sender = (get_secret("SMTP_FROM") or get_secret("SMTP_USER")
              or f"{_brand(cfg)}@{host}")
    try:
        server = smtp_connect(host, port, smtp_factory=smtp_factory)
    except Exception as e:
        return skip(f"smtp connect failed: {e}", log.warning)
    try:
        for sub in pending:
            if deadline is not None and time.monotonic() > deadline:
                log.info("newsletter: run deadline reached after %d sends; "
                         "the rest ride the next run", stats["sent"])
                break
            unsub = (f"{worker_url}/unsubscribe?"
                     + urlencode({"e": sub["email"], "t": sub["token"]}))
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = sender
            msg["To"] = sub["email"]  # one message per subscriber, never a list
            # RFC 8058 one-click unsubscribe: mail clients show their own button.
            msg["List-Unsubscribe"] = f"<{unsub}>"
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
            msg.attach(MIMEText(text_body.replace(UNSUB_PLACEHOLDER, unsub),
                                "plain", "utf-8"))
            msg.attach(MIMEText(html_body.replace(UNSUB_PLACEHOLDER,
                                                  html_lib.escape(unsub)),
                                "html", "utf-8"))
            try:
                server.send_message(msg)
            except smtplib.SMTPServerDisconnected as e:
                log.warning("newsletter: connection lost after %d sends (%s); "
                            "the rest ride the next run", stats["sent"], e)
                break
            except Exception as e:
                log.warning("newsletter: send to %s failed (retries next run): %s",
                            _mask(sub["email"]), e)
                continue
            done.add(fingerprint(sub["email"]))
            # Recorded after EVERY send: a crash mid-batch resends nothing.
            db.meta_set(key, json.dumps(sorted(done)))
            stats["sent"] += 1
    finally:
        try:
            server.quit()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
    log.info("newsletter %s: %d sent this run (%d deals), %d of %d subscribers done",
             day, stats["sent"], min(len(deals), MAX_DEALS), len(done),
             len(subscribers))
    return stats


# ---------------------------------------------------------------- double opt-in


def post_confirm_sent(worker_url: str, secret: str, emails: list[str],
                      http_client: Any = None) -> bool:
    """Tell the Worker these addresses got their confirmation link, so it
    stops listing them under /list?pending=1. False on any failure: the
    24 h guard in state.db keeps the next run from emailing them again."""
    url = worker_url.rstrip("/") + "/list/confirm-sent"
    headers = {"Authorization": f"Bearer {secret}"}
    body = {"emails": emails}
    try:
        if http_client is not None:
            resp = http_client.post(url, json=body, headers=headers)
        else:
            with httpx.Client(timeout=20) as client:
                resp = client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.warning("confirmations: confirm-sent POST failed for %d addresses "
                    "(retried next run; the 24 h guard prevents a repeat): %s",
                    len(emails), e)
        return False


def _confirm_guard(db, now: datetime) -> dict[str, str]:
    """{fingerprint: ISO time} of the confirmation links emailed in the last
    CONFIRM_GUARD_HOURS; older or unreadable entries drop out as it is read."""
    try:
        raw = json.loads(db.meta_get(CONFIRM_META_KEY) or "{}")
    except ValueError:
        raw = {}
    cutoff = now - timedelta(hours=CONFIRM_GUARD_HOURS)
    recent: dict[str, str] = {}
    for fp, ts in (raw.items() if isinstance(raw, dict) else ()):
        try:
            if datetime.fromisoformat(str(ts)) > cutoff:
                recent[str(fp)] = str(ts)
        except ValueError:
            continue
    return recent


def build_confirmation(cfg, confirm_url: str) -> tuple[str, str, str]:
    """(subject, text_body, html_body) for ONE address: the link and nothing
    else. No deals: nothing is owed to someone who may never have asked."""
    brand = _brand(cfg)
    subject = f"Confirm your {brand} email"
    lead = ("Click the link below to start getting the daily email: "
            "the day's best verified deals, once a day.")
    ignore = "If you didn't sign up, ignore this and you won't hear from us."
    text_body = f"{subject}\n\n{lead}\n{confirm_url}\n\n{ignore}\n"
    esc = html_lib.escape
    html_body = (
        '<!doctype html><html><body style="margin:0;background:#f3f5f7;color:#1a1d21;'
        'font:15px/1.5 system-ui,-apple-system,\'Segoe UI\',sans-serif">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        '<tr><td align="center" style="padding:20px 12px">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        'style="max-width:600px;width:100%;background:#ffffff;border:1px solid #e2e6eb;'
        'border-radius:14px;padding:20px 22px"><tr><td>'
        f'<div style="font-size:22px;font-weight:800">{esc(brand)}'
        '<span style="color:#0e7569">●</span></div>'
        f"<p>{esc(lead)}</p>"
        f'<p><a href="{esc(confirm_url)}" style="display:inline-block;background:#0e7569;'
        'color:#ffffff;font-weight:700;padding:10px 16px;border-radius:8px;'
        'text-decoration:none">Confirm my email</a></p>'
        f'<p style="color:#616a75;font-size:12px;line-height:1.5">{esc(ignore)}</p>'
        "</td></tr></table></td></tr></table></body></html>"
    )
    return subject, text_body, html_body


def send_confirmations(db, cfg, now: datetime, *,
                       smtp_factory: Any = None,
                       http_client: Any = None,
                       deadline: float | None = None) -> dict[str, Any]:
    """One confirmation link to each pending sign-up the Worker lists, at
    most MAX_CONFIRMATIONS_PER_RUN per run and never past ``deadline``
    (time.monotonic), then POST /list/confirm-sent so the Worker never
    lists them again. Runs every run, the fast lane included. Returns
    {sent, skipped}; `skipped` is "" whenever messages went out."""
    stats: dict[str, Any] = {"sent": 0, "skipped": ""}

    def skip(reason: str, emit=log.info) -> dict[str, Any]:
        emit("confirmations skipped: %s", reason)
        stats["skipped"] = reason
        return stats

    if not getattr(cfg, "newsletter_enabled", True):
        return skip("disabled in config", log.debug)
    worker_url = (getattr(cfg, "newsletter_worker_url", "") or "").rstrip("/")
    secret = get_secret("NEWSLETTER_SECRET")
    host = get_secret("SMTP_HOST")
    missing = [name for name, value in (("newsletter.worker_url", worker_url),
                                        ("NEWSLETTER_SECRET", secret),
                                        ("SMTP_HOST", host)) if not value]
    if missing:
        return skip(", ".join(missing) + " not configured")

    pending = fetch_subscribers(worker_url, secret, http_client, pending=True)
    if pending is None:
        return skip("pending list unavailable")
    if not pending:
        return skip("no pending sign-ups", log.debug)
    recent = _confirm_guard(db, now)
    # Emailed within 24 h yet still listed: the confirm-sent POST failed
    # last time. No second email; the Worker is simply told again below.
    already = [s["email"] for s in pending if fingerprint(s["email"]) in recent]
    todo = [s for s in pending
            if fingerprint(s["email"]) not in recent][:MAX_CONFIRMATIONS_PER_RUN]
    sent: list[str] = []
    if todo:
        port = int(get_secret("SMTP_PORT") or "587")
        sender = (get_secret("SMTP_FROM") or get_secret("SMTP_USER")
                  or f"{_brand(cfg)}@{host}")
        try:
            server = smtp_connect(host, port, smtp_factory=smtp_factory)
        except Exception as e:
            return skip(f"smtp connect failed: {e}", log.warning)
        try:
            for sub in todo:
                if deadline is not None and time.monotonic() > deadline:
                    log.info("confirmations: run deadline reached after %d sends; "
                             "the rest ride the next run", len(sent))
                    break
                confirm = (f"{worker_url}/confirm?"
                           + urlencode({"e": sub["email"], "t": sub["token"]}))
                subject, text_body, html_body = build_confirmation(cfg, confirm)
                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"] = sender
                msg["To"] = sub["email"]
                msg.attach(MIMEText(text_body, "plain", "utf-8"))
                msg.attach(MIMEText(html_body, "html", "utf-8"))
                try:
                    server.send_message(msg)
                except smtplib.SMTPServerDisconnected as e:
                    log.warning("confirmations: connection lost after %d sends (%s); "
                                "the rest ride the next run", len(sent), e)
                    break
                except Exception as e:
                    log.warning("confirmations: send to %s failed (retries next run): %s",
                                _mask(sub["email"]), e)
                    continue
                recent[fingerprint(sub["email"])] = now.isoformat()
                # Recorded after EVERY send: a crash mid-batch resends nothing.
                db.meta_set(CONFIRM_META_KEY, json.dumps(recent, sort_keys=True))
                sent.append(sub["email"])
        finally:
            try:
                server.quit()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
    stats["sent"] = len(sent)
    if sent or already:
        post_confirm_sent(worker_url, secret, sent + already, http_client)
    if sent:
        log.info("confirmations: %d of %d pending sent this run, %d re-marked",
                 len(sent), len(pending), len(already))
        return stats
    if not todo:
        return skip("every pending sign-up emailed in the last 24 h", log.debug)
    return skip("nothing sent this run")  # deadline, drop or refusals: next run
