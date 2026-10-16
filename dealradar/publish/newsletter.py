"""Best-deals email, daily or weekly; both send switches ship off.

Only eligible deals with written wowpriced pages reach the picker. The
shared parser chooses ids under its existing spend caps; failure uses rank
order. Picks and recipient fingerprints are stored per local day or ISO
week. Durable outgoing claims prevent repeats after uncertain SMTP results.

The Worker holds the double opt-in list. Confirmation emails can start
independently; both senders require a postal address. Failed confirm-sent
reports are retried, with the existing 24-hour guard and durable claims
preventing another email. Preview reads local state without any transport
or DB writes. Membership copy appears only in membership mode.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import logging
import smtplib
import time
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

import httpx

from ..savings import display_pct
from ..persistence import delivery_run_key, message_id, prepare_delivery
from ..config import get_secret
from .email_digest import smtp_connect
from .credits import source_display_name
from .email_policy import email_content_excluded, email_credits, email_deal, email_excluded

log = logging.getLogger("dealradar.publish.newsletter")

#: The model picks the day's top newsletter_top_n among this many deals,
#: best rank first (rank.published_deals order).
PICK_CANDIDATES = 40
#: Output cap for the pick: a short JSON list, with room to think first.
PICK_MAX_TOKENS = 4096
#: The pick's request timeout. ONE attempt, no SDK or parser retries, and
#: never past the run's deadline: a timeout means rank order for the day.
#: With less than this left before the deadline, the next run picks.
PICK_TIMEOUT_S = 30.0
PICK_SYSTEM_PROMPT = (
    "You choose the best deals for a free daily deals email. Each line is one "
    "deal: id, item, store, price facts, verification tier. Pick the {n} a "
    "typical US shopper would most want today: real savings on things people "
    "buy, broad appeal, a mix of stores and kinds of product, never the same "
    "product twice. Use only ids from the list. Respond with ONLY a JSON "
    'object, no prose and no markdown fences: {{"ids": [<{n} ids, best '
    "first>]}}"
)
#: Stands in for the per-subscriber unsubscribe link in the shared bodies.
UNSUB_PLACEHOLDER = "{{unsubscribe_url}}"
#: Confirmation links per run: the pass rides every run and must stay quick.
MAX_CONFIRMATIONS_PER_RUN = 50
#: meta key: JSON {fingerprint: ISO time} of the addresses emailed a
#: confirmation link in the last CONFIRM_GUARD_HOURS (hashes, never addresses).
CONFIRM_META_KEY = "newsletter:confirm_sent"
CONFIRM_GUARD_HOURS = 24
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def meta_key(day: str) -> str:
    """meta key holding the JSON list of fingerprints already sent that day."""
    return f"newsletter:{day}"


def pick_key(day: str) -> str:
    """meta key holding the day's picked deal keys (and who picked them)."""
    return f"newsletter:pick:{day}"


def fingerprint(email: str) -> str:
    """What the progress list records instead of the address."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]


def _brand(cfg) -> str:
    domain = getattr(cfg, "custom_domain", "") or ""
    return domain.split(".")[0] if domain else "dealradar"


def _sender(cfg, host: str) -> str:
    raw = (get_secret("SMTP_FROM") or get_secret("SMTP_USER")
           or f"{_brand(cfg)}@{host}")
    name, address = parseaddr(raw)
    return formataddr((_brand(cfg), address)) if name else address


def _top_n(cfg) -> int:
    return max(1, int(getattr(cfg, "newsletter_top_n", 10)))


def _weekly(cfg) -> bool:
    return cfg.newsletter_cadence == "weekly"


def _period(cfg, day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}" if _weekly(cfg) else day.isoformat()


def _cadence_text(cfg) -> str:
    if _weekly(cfg):
        weekday = WEEKDAY_NAMES[WEEKDAYS.index(cfg.newsletter_weekday)]
        return f"the week's top {_top_n(cfg)} deals, every {weekday}"
    return f"the day's top {_top_n(cfg)} deals, once a day"


def _site_url(cfg, path: str) -> str:
    domain = cfg.custom_domain
    site = urlsplit(f"https://{domain}")
    target = urlsplit(path)
    if (not domain or site.hostname != domain or site.netloc != domain
            or site.path or site.query or site.fragment or not target.path.startswith("/")
            or target.scheme or target.netloc or target.query or target.fragment
            or "\\" in path or any(c.isspace() for c in path)):
        return ""
    return f"https://{domain}{path}?ref=email"


def eligible_deals(deals: list[dict[str, Any]], cfg, db=None) -> list[dict[str, Any]]:
    """Filter before picking; paths come only from the publisher's written pages."""
    try:
        paths = json.loads(db.meta_get("seo_deal_paths") or "{}") if db else {}
    except ValueError:
        paths = {}
    paths = paths if isinstance(paths, dict) else {}
    eligible = []
    for deal in deals:
        if email_excluded(deal, cfg):
            continue
        key = str(deal.get("dedupe_key") or "")
        path = deal.get("deal_path") or paths.get(hashlib.sha256(key.encode()).hexdigest()[:8], "")
        if not isinstance(path, str) or not path.startswith("/deal/") or not _site_url(cfg, path):
            continue
        # Remembered paths outlive their pages (locked or rotated out).
        if not deal.get("deal_path") and not (Path(cfg.site_dir) / path.strip("/") / "index.html").is_file():
            continue
        eligible.append(dict(deal, deal_path=path))
    return eligible


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
    seen: dict[str, dict[str, str]] = {}
    for row in rows:
        email = str(row.get("email") or "").strip().lower()
        token = str(row.get("token") or "")
        if email and token:
            record = {"email": email, "token": token}
            if pending:
                record["ts"] = str(row.get("ts") or "")
            seen.setdefault(email, record)
    return [record for _, record in sorted(seen.items())]


def _deal_url(deal: dict[str, Any]) -> str:
    # Legacy compatibility for callers outside the newsletter renderer.
    canon = deal.get("canonical_url") or ""
    if canon:
        return canon
    sightings = deal.get("sightings") or []
    return (sightings[0].get("source_url") or "") if sightings else ""


def _row_html(deal: dict[str, Any], url: str = "") -> str:
    esc = html_lib.escape
    item = esc(deal.get("item") or "")
    final = deal.get("final_price")
    reg = deal.get("regular_price")
    pct = deal.get("pct_at_checkout")
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
        price += f' <span style="color:#107a3d;font-weight:700">{display_pct(pct)}% off</span>'
    price += f' <span style="color:#616a75">at {esc(deal.get("store") or "")}</span>'
    actions = ""
    if url:
        actions += (f'<a href="{esc(url)}" style="display:inline-block;background:#0e7569;'
                    'color:#ffffff;font-weight:700;padding:7px 12px;border-radius:8px;'
                    'text-decoration:none">See the deal</a> ')
    for src_name in dict.fromkeys(source_display_name(n) for n, _ in email_credits(deal)):
        actions += f'<span style="color:#616a75;font-size:12px">via {esc(src_name)}</span>'
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
    deals = [email_deal(d, cfg) for d in eligible_deals(deals, cfg)[:_top_n(cfg)]]
    domain = getattr(cfg, "custom_domain", "") or ""
    brand = domain.split(".")[0] if domain else "wowpriced"
    site = _site_url(cfg, "/")
    wall = _site_url(cfg, "/best-deals-today/")
    local = now.astimezone(ZoneInfo(cfg.newsletter_timezone))
    n = len(deals)
    top = f"top {n} deal{'s' if n != 1 else ''}"
    period = "This week's" if _weekly(cfg) else "Today's"
    subject = f"{brand}: {period} {top}"
    heading = f"{period} {top} — {local:%A, %b} {local.day}, {local.year}"
    because = f"You get this because you subscribed at {domain or brand} for {_cadence_text(cfg)}."
    disclosure = getattr(cfg, "ftc_disclosure", "") or ""
    address = cfg.newsletter_mailing_address
    eastern = now.astimezone(ZoneInfo("America/New_York"))
    as_of = (f"Prices as of {eastern.hour % 12 or 12}:{eastern:%M} {eastern:%p} ET, "
             f"{eastern:%b} {eastern.day}. Prices change fast; the deal page shows the latest check.")
    commission = "We may earn a commission when you buy through links on wowpriced.com."
    # Members get every deal: the free list's email ends with the trial.
    days = int(getattr(cfg, "paywall_trial_days", 7) or 0)
    join = f"Start the {days}-day free trial" if days > 0 else "Become a member"
    # Only when membership is offered: the site has no sign-up without the
    # paywall Worker, and with the paywall off every deal is already free.
    member = (not getattr(cfg, "support_enabled", False)
              and getattr(cfg, "paywall_enabled", True)
              and bool(getattr(cfg, "paywall_api_url", "")))
    cta = f"Members get every deal. {join}: {site}#signup" if site and member else ""

    lines = [as_of, commission, "", f"{brand}: {heading}", ""]
    for i, d in enumerate(deals, 1):
        final = d.get("final_price")
        bits = []
        if d.get("regular_price"):
            bits.append(f"was ${d['regular_price']:,.2f}")
        if d.get("pct_at_checkout") is not None:
            bits.append(f"{display_pct(d['pct_at_checkout'])}% off")
        line = (f"{i}. {d.get('item') or ''} — "
                + ("" if d["email_hide_price"] else f"${final:,.2f}" if final is not None else "Price not listed")
                + (f" ({', '.join(bits)})" if bits else "")
                + f" at {d.get('store') or ''}")
        lines.append(line)
        url = _site_url(cfg, d["deal_path"])
        if url:
            lines.append(f"   See the deal: {url}")
        for src_name in dict.fromkeys(source_display_name(n) for n, _ in email_credits(d)):
            lines.append(f"   via {src_name}")
        lines.append("")
    if cta:
        lines += [cta, ""]
    if wall:
        lines.append(f"Live wall: {wall}")
    lines.append(f"{because} Unsubscribe any time: {UNSUB_PLACEHOLDER}")
    if disclosure:
        lines.append(disclosure)
    lines.append(address)
    text_body = "\n".join(lines) + "\n"

    esc = html_lib.escape
    # External thumbnails would make a retailer request on email open. Only
    # already-hosted first-party assets are suitable; never fetch or proxy here.
    rows = []
    for d in deals:
        image = d.get("image_url") or ""
        origin = urlsplit(image)
        if origin.scheme != "https" or origin.netloc != domain:
            image = ""
        rows.append(_row_html(dict(d, image_url=image), _site_url(cfg, d["deal_path"])))
    rows = "\n".join(rows)
    muted = "color:#616a75;font-size:12px;line-height:1.5;margin:10px 0 0"
    html_body = (
        '<!doctype html><html><body style="margin:0;background:#f3f5f7;color:#1a1d21;'
        'font:15px/1.5 system-ui,-apple-system,\'Segoe UI\',sans-serif">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        '<tr><td align="center" style="padding:20px 12px">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        'style="max-width:600px;width:100%;background:#ffffff;border:1px solid #e2e6eb;'
        'border-radius:14px;padding:20px 22px">'
        f'<tr><td><p style="font-size:12px">{esc(as_of)}</p><p style="font-size:12px">{esc(commission)}</p>'
        f'<div style="font-size:22px;font-weight:800">{esc(brand)}'
        '<span style="color:#0e7569">●</span></div>'
        f'<div style="color:#616a75;font-size:13px">{esc(heading)}</div></td></tr>'
        f'<tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        f"{rows}</table></td></tr>"
        "<tr><td>"
        + ('<p style="margin:16px 0 0;font-weight:700">Members get every deal.</p>'
           f'<p style="margin:8px 0 0"><a href="{esc(site)}#signup" style="display:'
           'inline-block;background:#0e7569;color:#ffffff;font-weight:700;'
           'padding:10px 16px;border-radius:8px;text-decoration:none">'
           f"{esc(join)}</a></p>" if cta else "")
        + (f'<p style="{muted}"><a href="{esc(wall)}" style="color:#0b63c4">'
           f"Live wall</a></p>" if wall else "")
        + f'<p style="{muted}">{esc(because)} '
        f'<a href="{UNSUB_PLACEHOLDER}" style="color:#616a75">Unsubscribe</a> any time.</p>'
        + (f'<p style="{muted}">{esc(disclosure)}</p>' if disclosure else "")
        + f'<p style="{muted}">{esc(address)}</p>'
        + "</td></tr></table></td></tr></table></body></html>"
    )
    return subject, text_body, html_body


def send_daily_newsletter(db, cfg, now: datetime, *,
                          deals: list[dict[str, Any]] | None = None,
                          smtp_factory: Any = None,
                          http_client: Any = None,
                          deadline: float | None = None,
                          parser: Any = None,
                          run_key: str | None = None) -> dict[str, Any]:
    """The day's email to every subscriber not yet sent today, at most
    cfg.newsletter_max_per_run per run and never past ``deadline`` (a
    time.monotonic() value: the run's budget). Durable outgoing claims prevent
    repeating uncertain deliveries. ``parser`` is the run's
    shared ClaudeParser for todays_top (None: rank order). Returns {sent,
    skipped, day}; the `skipped` reason is "" whenever messages went out."""
    local = now.astimezone(ZoneInfo(cfg.newsletter_timezone))
    day = _period(cfg, local.date())
    stats: dict[str, Any] = {"sent": 0, "skipped": "", "day": day}

    def skip(reason: str, emit=log.info) -> dict[str, Any]:
        emit("newsletter skipped: %s", reason)
        stats["skipped"] = reason
        return stats

    if not cfg.newsletter_mailing_address.strip():
        return skip("newsletter.mailing_address not configured")
    if not cfg.newsletter_enabled:
        return skip("disabled in config", log.debug)
    if _weekly(cfg) and local.weekday() != WEEKDAYS.index(cfg.newsletter_weekday):
        return skip(f"not newsletter weekday ({cfg.newsletter_weekday})", log.debug)
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
    pending = [s for s in subscribers if fingerprint(s["email"]) not in done
               and not db.outgoing_exists("newsletter", f"{day}|{fingerprint(s['email'])}")]
    if not pending:
        return skip("everyone already sent today", log.debug)
    selected = db.delivery_batch("newsletter", delivery_run_key(now, run_key),
                                 [fingerprint(s["email"]) for s in pending[:cfg.newsletter_max_per_run]])
    pending = [s for s in pending if fingerprint(s["email"]) in selected]
    if not pending or not db.delivery_budget("newsletter", delivery_run_key(now, run_key)):
        return skip("this run's recipient batch is complete", log.debug)
    if not done:
        # First send of a new day: yesterday's progress list has done its
        # job, so it shrinks to nothing rather than piling up in state.db.
        prev = meta_key(_period(cfg, local.date() - timedelta(days=7 if _weekly(cfg) else 1)))
        if db.meta_get(prev) not in (None, "[]"):
            db.meta_set(prev, "[]")

    if deals is None:
        from dealradar import rank  # lazy: sibling module, avoid import at load

        deals = rank.published_deals(db, cfg, now)
    from .price_labels import push_excluded
    deals = eligible_deals([d for d in deals if not push_excluded(d, cfg, now)], cfg, db)
    if not deals:
        return skip("no published deals yet today")  # tried again next run
    if deadline is not None and time.monotonic() > deadline:
        return skip("run deadline reached")  # the pick and the sends ride the next run
    deals = todays_top(db, cfg, deals, local.date(), parser, deadline)
    if deals is None:
        return skip("run deadline near: today's pick rides the next run")
    if not deals:
        return skip("today's picks are no longer live")

    subject, text_body, html_body = build_newsletter(deals, cfg, now)
    if email_content_excluded(cfg, subject, text_body, html_body):
        return skip("excluded store token in email", log.warning)
    port = int(get_secret("SMTP_PORT") or "587")
    sender = _sender(cfg, host)
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
            sub_text = text_body.replace(UNSUB_PLACEHOLDER, unsub)
            sub_html = html_body.replace(UNSUB_PLACEHOLDER, html_lib.escape(unsub))
            if email_content_excluded(cfg, subject, sub_text, sub_html):
                log.warning("newsletter skipped: excluded store token in subscriber email")
                continue
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = sender
            msg["To"] = sub["email"]  # one message per subscriber, never a list
            operation = f"{day}|{fingerprint(sub['email'])}"
            msg["Message-ID"] = message_id("newsletter", operation)
            # RFC 8058 one-click unsubscribe: mail clients show their own button.
            msg["List-Unsubscribe"] = f"<{unsub}>"
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
            msg.attach(MIMEText(sub_text, "plain", "utf-8"))
            msg.attach(MIMEText(sub_html, "html", "utf-8"))
            try:
                if not prepare_delivery(db, "newsletter", operation, now, run_key):
                    continue
                server.send_message(msg)
            except smtplib.SMTPServerDisconnected as e:
                log.warning("newsletter: connection lost after %d sends (%s); "
                            "the rest ride the next run", stats["sent"], e)
                break
            except Exception as e:
                log.warning("newsletter: send to %s failed: %s",
                            _mask(sub["email"]), e)
                continue
            done.add(fingerprint(sub["email"]))
            # Recorded after EVERY send: a crash mid-batch resends nothing.
            db.meta_set(key, json.dumps(sorted(done)))
            db.finish_outgoing("newsletter", operation, now)
            stats["sent"] += 1
    finally:
        try:
            server.quit()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
    log.info("newsletter %s: %d sent this run (%d deals), %d of %d subscribers done",
             day, stats["sent"], len(deals), len(done),
             len(subscribers))
    return stats


# ---------------------------------------------------------------- the day's top


def _ask_model(candidates: list[dict[str, Any]], n: int, parser,
               deadline: float | None = None) -> list[Any] | None:
    """The parse model's ids (1-based, best first) for the day's top ``n`` of
    ``candidates``, or None when it gave no usable answer: spend cap reached
    (the parser's own gate raises before any call), API failure or timeout,
    refusal, cut-off or unreadable JSON. ONE attempt: no parser retry and no
    SDK retries, its timeout PICK_TIMEOUT_S capped at what is left before
    ``deadline`` (time.monotonic). Spend is accounted on the shared parser
    at the model's own price."""
    from ..parse.claude_client import _strip_fences  # lazy: the anthropic SDK

    lines = []
    for i, d in enumerate(candidates, 1):
        final, reg = d.get("final_price"), d.get("regular_price")
        pct = d.get("pct_at_checkout")
        facts = ((f"${final:,.2f}" if final is not None else "price ?")
                 + (f", was ${reg:,.2f}" if reg else "")
                 + (f", {display_pct(pct)}% off" if pct is not None else ""))
        conds = ", ".join(d.get("conditions") or [])
        lines.append(f"{i}. {d.get('item') or ''} | {d.get('store') or ''} | {facts}"
                     f" | {d.get('verify_tier') or 'unverified'}"
                     + (f" | {conds}" if conds else ""))
    prompt = f"Pick the {n} best of these {len(candidates)} deals:\n" + "\n".join(lines)
    timeout = PICK_TIMEOUT_S
    if deadline is not None:
        timeout = max(0.0, min(timeout, deadline - time.monotonic()))
    client = parser.client
    kwargs = dict(model=parser.model, max_tokens=PICK_MAX_TOKENS,
                  system=PICK_SYSTEM_PROMPT.format(n=n),
                  messages=[{"role": "user", "content": prompt}], timeout=timeout)
    try:
        create = client.messages.create
        resp = parser._request(lambda: create(**kwargs), kwargs=kwargs,
                               kind="newsletter_pick", retry=False)
    except Exception as e:  # spend cap, API failure or timeout: rank order today
        log.warning("newsletter pick: no model answer (%s: %s); rank order",
                    type(e).__name__, e)
        return None
    if resp is None:
        return None
    if getattr(resp, "stop_reason", None) in ("refusal", "max_tokens"):
        log.warning("newsletter pick: model stopped (%s); rank order",
                    resp.stop_reason)
        return None
    raw = "".join(getattr(b, "text", "") for b in getattr(resp, "content", []) or []
                  if getattr(b, "type", "") == "text")
    try:
        data = json.loads(_strip_fences(raw))
    except ValueError:
        log.warning("newsletter pick: unreadable answer; rank order")
        return None
    ids = data.get("ids") if isinstance(data, dict) else data
    return ids if isinstance(ids, list) else None


def _asks_model(deals: list[dict[str, Any]], cfg, parser: Any) -> bool:
    """Whether pick_top would call the model for ``deals``."""
    return parser is not None and min(len(deals), PICK_CANDIDATES) > _top_n(cfg)


def pick_top(deals: list[dict[str, Any]], cfg, parser: Any = None,
             deadline: float | None = None) -> tuple[list[dict[str, Any]], str]:
    """(the day's top newsletter_top_n of ``deals``, who picked them).
    ``deals`` is in rank order, best first. "model": the parse model chose
    among the best PICK_CANDIDATES; unknown ids and repeats are dropped and
    the list is padded from rank order. "rank": no parser, nothing to choose
    between, or no usable answer. ``deadline``: see _ask_model."""
    n = _top_n(cfg)
    candidates = deals[:PICK_CANDIDATES]
    if not _asks_model(deals, cfg, parser):
        return candidates[:n], "rank"
    chosen: list[int] = []
    for i in _ask_model(candidates, n, parser, deadline) or []:
        if (isinstance(i, int) and not isinstance(i, bool)
                and 1 <= i <= len(candidates) and i - 1 not in chosen):
            chosen.append(i - 1)
    chosen = chosen[:n]
    how = "model" if chosen else "rank"
    chosen += [i for i in range(len(candidates)) if i not in chosen][:n - len(chosen)]
    return [candidates[i] for i in chosen], how


def todays_top(db, cfg, deals: list[dict[str, Any]], day: date,
               parser: Any = None,
               deadline: float | None = None) -> list[dict[str, Any]] | None:
    """The day's top deals, with current facts from ``deals``. The first
    call of a local day picks (pick_top) and stores the deal keys before
    anything is sent; every later call that day reuses them, so a rerun or
    a retried send never asks the model again. A picked deal that is no
    longer published drops out rather than being replaced. None: the model
    would be asked with less than PICK_TIMEOUT_S left before ``deadline``
    (time.monotonic); nothing is stored and the next run picks."""
    deals = eligible_deals(deals, cfg, db)
    key = pick_key(_period(cfg, day))
    try:
        stored = json.loads(db.meta_get(key) or "null")
    except ValueError:
        stored = None
    if isinstance(stored, dict) and isinstance(stored.get("keys"), list):
        live = {d.get("dedupe_key"): d for d in deals}
        return [live[k] for k in stored["keys"] if k in live]
    if (_asks_model(deals, cfg, parser) and deadline is not None
            and deadline - time.monotonic() < PICK_TIMEOUT_S):
        return None
    picked, how = pick_top(deals, cfg, parser, deadline)
    db.meta_set(key, json.dumps({"keys": [d.get("dedupe_key") for d in picked],
                                 "by": how}))
    # Yesterday's pick has done its job: it shrinks rather than piling up.
    prev = pick_key(_period(cfg, day - timedelta(days=7 if _weekly(cfg) else 1)))
    if db.meta_get(prev):
        db.meta_set(prev, "")
    log.info("newsletter %s: top %d picked by %s", day.isoformat(), len(picked), how)
    return picked


def preview_newsletter(db, cfg, now: datetime, out: str | Path) -> tuple[Path, Path]:
    """Render the next issue from local state, without picks, deliveries or writes to DB."""
    from .. import rank
    from .price_labels import push_excluded

    local = now.astimezone(ZoneInfo(cfg.newsletter_timezone))
    day = local.date()
    if _weekly(cfg):
        day += timedelta(days=(WEEKDAYS.index(cfg.newsletter_weekday) - day.weekday()) % 7)
    deals = eligible_deals([d for d in rank.published_deals(db, cfg, now)
                            if not push_excluded(d, cfg, now)], cfg, db)
    try:
        stored = json.loads(db.meta_get(pick_key(_period(cfg, day))) or "null")
    except ValueError:
        stored = None
    if isinstance(stored, dict) and isinstance(stored.get("keys"), list):
        live = {d.get("dedupe_key"): d for d in deals}
        deals = [live[k] for k in stored["keys"] if k in live]
    _, text, html = build_newsletter(deals, cfg, now)
    output = Path(out)
    output.mkdir(parents=True, exist_ok=True)
    html_path, text_path = output / "newsletter-preview.html", output / "newsletter-preview.txt"
    html_path.write_text(html, encoding="utf-8")
    text_path.write_text(text, encoding="utf-8")
    return html_path, text_path


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


def build_confirmation(cfg, confirm_url: str, ts: str = "") -> tuple[str, str, str]:
    """(subject, text_body, html_body) for ONE address: the link and nothing
    else. No deals: nothing is owed to someone who may never have asked."""
    domain = getattr(cfg, "custom_domain", "") or ""
    brand = domain.split(".")[0] if domain else "wowpriced"
    subject = f"Confirm your {brand} email"
    lead = f"Click the link below to start getting {_cadence_text(cfg)}."
    entered = ""
    try:
        signed_up = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        signed_up = signed_up.astimezone(ZoneInfo(cfg.newsletter_timezone))
        entered = (f"This address was entered at wowpriced.com on "
                   f"{signed_up:%b} {signed_up.day}, {signed_up.year}.")
    except ValueError:
        pass  # Old records without a timestamp must not invent a sign-up date.
    ignore = "If you didn't sign up, ignore this and you won't hear from us."
    address = cfg.newsletter_mailing_address
    text_body = f"{subject}\n\n{lead}\n{confirm_url}\n\n{entered}\n{ignore}\n{address}\n"
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
        f'<p style="font-size:12px">{esc(entered)}</p><p style="font-size:12px">{esc(address)}</p>'
        "</td></tr></table></td></tr></table></body></html>"
    )
    return subject, text_body, html_body


def send_confirmations(db, cfg, now: datetime, *,
                       smtp_factory: Any = None,
                       http_client: Any = None,
                       deadline: float | None = None,
                       run_key: str | None = None) -> dict[str, Any]:
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

    if not cfg.newsletter_mailing_address.strip():
        return skip("newsletter.mailing_address not configured")
    if not (cfg.newsletter_confirmations_enabled or cfg.newsletter_enabled):
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
    already = [s["email"] for s in pending if fingerprint(s["email"]) in recent
               or db.outgoing_status("confirmation", fingerprint(s["email"] + "|" + s["token"])) == "sent"]
    todo = [s for s in pending
            if fingerprint(s["email"]) not in recent and not db.outgoing_exists(
                "confirmation", fingerprint(s["email"] + "|" + s["token"]))]
    selected = db.delivery_batch("confirmation", delivery_run_key(now, run_key),
                                 [fingerprint(s["email"] + "|" + s["token"])
                                  for s in todo[:MAX_CONFIRMATIONS_PER_RUN]])
    todo = [s for s in todo if fingerprint(s["email"] + "|" + s["token"]) in selected]
    sent: list[str] = []
    if todo:
        port = int(get_secret("SMTP_PORT") or "587")
        sender = _sender(cfg, host)
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
                subject, text_body, html_body = build_confirmation(cfg, confirm, sub.get("ts", ""))
                if email_content_excluded(cfg, subject, text_body, html_body):
                    log.warning("confirmations skipped: excluded store token in email")
                    continue
                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"] = sender
                msg["To"] = sub["email"]
                operation = fingerprint(sub["email"] + "|" + sub["token"])
                msg["Message-ID"] = message_id("confirmation", operation)
                msg.attach(MIMEText(text_body, "plain", "utf-8"))
                msg.attach(MIMEText(html_body, "html", "utf-8"))
                try:
                    if not prepare_delivery(db, "confirmation", operation, now, run_key):
                        continue
                    server.send_message(msg)
                except smtplib.SMTPServerDisconnected as e:
                    log.warning("confirmations: connection lost after %d sends (%s); "
                                "the rest ride the next run", len(sent), e)
                    break
                except Exception as e:
                    log.warning("confirmations: send to %s failed: %s",
                                _mask(sub["email"]), e)
                    continue
                recent[fingerprint(sub["email"])] = now.isoformat()
                # Recorded after EVERY send: a crash mid-batch resends nothing.
                db.meta_set(CONFIRM_META_KEY, json.dumps(recent, sort_keys=True))
                db.finish_outgoing("confirmation", operation, now)
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
