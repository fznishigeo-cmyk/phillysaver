"""Collect every configured section, build the report, deliver it privately.

Each section is skipped (one log line) when its secret or config is missing,
and a failing section never stops the others. Logs carry section names and
ok / skipped / error class only, except safe service-account shape errors:
the Actions logs are public, and other exception messages may echo a response
body or a token.
"""

from __future__ import annotations

import functools
import html as html_lib
import logging
from datetime import date, datetime, time, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..config import get_secret
from ..publish.admin_alerts import send_admin_text
from ..publish.email_digest import smtp_connect
from ..util import now_utc
from . import google, google_ads, meta, site_health, stripe_stats, subscribers

log = logging.getLogger("dealradar.growth")

SEARCH_LAG_DAYS = 3   # Search Console data trails by 2-3 days
TELEGRAM_MAX = 4000   # under Telegram's 4,096-character message limit


def _error(e: Exception) -> str:
    """The class name (plus the HTTP status), never the message."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"{type(e).__name__} {e.response.status_code}"
    return type(e).__name__


def collect(cfg, now: datetime, client: Any) -> tuple[dict, dict]:
    """(section -> data, section -> 'ok' | 'skipped' | 'error')."""
    tz = ZoneInfo(cfg.timezone)
    today = now.astimezone(tz).date()
    days = cfg.growth_report_days
    yesterday, start = today - timedelta(days=1), today - timedelta(days=days)
    sc_end = today - timedelta(days=SEARCH_LAG_DAYS)

    def epoch(d: date) -> int:
        return int(datetime.combine(d, time(), tz).timestamp())

    sa = get_secret("GOOGLE_SA_JSON")
    meta_token = get_secret("META_SYSTEM_TOKEN")
    stripe_key = get_secret("STRIPE_SECRET_KEY")
    cf_token = get_secret("CLOUDFLARE_API_TOKEN")
    cf_account = get_secret("CLOUDFLARE_ACCOUNT_ID")
    gads = [(name, get_secret(name)) for name in
            ("GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN")]
    token = functools.cache(lambda: google.access_token(client, sa))
    version = cfg.growth_meta_graph_version
    need_google, need_meta = ("GOOGLE_SA_JSON", sa), ("META_SYSTEM_TOKEN", meta_token)
    site = ("growth.search_console_site", cfg.growth_search_console_site)

    sections = {
        "site_health": ([("publish.custom_domain", cfg.custom_domain)],
                        lambda: site_health.check(client, cfg, now)),
        "ga4": ([need_google, ("growth.ga4_property_id", cfg.growth_ga4_property_id)],
                lambda: google.ga4(client, token(), cfg.growth_ga4_property_id, days)),
        "ga4_stores": ([need_google, ("growth.ga4_property_id", cfg.growth_ga4_property_id)],
                       lambda: google.ga4_stores(client, token(), cfg.growth_ga4_property_id, days)),
        "search_console": ([need_google, site], lambda: google.search_console(
            client, token(), cfg.growth_search_console_site,
            sc_end - timedelta(days=days - 1), sc_end)),
        "sitemap": ([need_google, site, ("growth.sitemap_url", cfg.growth_sitemap_url)],
                    lambda: google.submit_sitemap(client, token(), cfg.growth_search_console_site,
                                                  cfg.growth_sitemap_url)),
        "meta_ads": ([need_meta, ("growth.meta_ad_account_id", cfg.growth_meta_ad_account_id)],
                     lambda: meta.ad_insights(client, meta_token, version,
                                              cfg.growth_meta_ad_account_id, yesterday, start)),
        "facebook": ([need_meta, ("growth.meta_page_id", cfg.growth_meta_page_id)],
                     lambda: meta.followers(client, meta_token, version, cfg.growth_meta_page_id)),
        "instagram": ([need_meta, ("growth.instagram_user_id", cfg.growth_instagram_user_id)],
                      lambda: meta.followers(client, meta_token, version,
                                             cfg.growth_instagram_user_id)),
        "google_ads": ([*gads, ("growth.google_ads_customer_id", cfg.growth_google_ads_customer_id)],
                       lambda: google_ads.insights(
                           client, google_ads.access_token(client, *(v for _, v in gads)),
                           cfg.growth_google_ads_api_version, cfg.growth_google_ads_customer_id,
                           cfg.growth_google_ads_login_customer_id, yesterday, start)),
        "subscribers": ([("CLOUDFLARE_API_TOKEN", cf_token), ("CLOUDFLARE_ACCOUNT_ID", cf_account)],
                        lambda: subscribers.counts(client, cf_token, cf_account,
                            datetime.combine(yesterday, time(), tz), datetime.combine(today, time(), tz))),
        "stripe": ([("STRIPE_SECRET_KEY", stripe_key)], lambda: stripe_stats.stats(
            client, stripe_key, yesterday_start=epoch(yesterday), window_start=epoch(start),
            today_start=epoch(today), monthly_price=cfg.growth_monthly_price_usd)),
    }
    data: dict[str, Any] = {}
    status: dict[str, str] = {}
    for name, (needs, fetch) in sections.items():
        missing = next((n for n, value in needs if not value), "")
        if missing:
            status[name] = "skipped"
            if name == "subscribers":
                data[name] = {"unavailable": f"{missing} not set"}
            log.info("%s: skipped (%s not set)", name, missing)
            continue
        try:
            data[name] = fetch()
            unavailable = isinstance(data[name], dict) and "unavailable" in data[name]
            status[name] = "skipped" if unavailable else "ok"
            log.info("%s: %s", name, status[name])
        except Exception as e:  # one section never stops the others
            status[name] = "error"
            if name == "subscribers":
                data[name] = {"unavailable": _error(e)}
            detail = (type(e).__name__ if name == "site_health" else
                      str(e) if isinstance(e, (google.ServiceAccountError, google_ads.GoogleAdsError))
                      else _error(e))
            log.warning("%s: error %s", name, detail)
    return data, status


def _n(x: int) -> str:
    return f"{x:,}"


def _usd(x: float) -> str:
    return f"${x:,.2f}"


def _pair(fmt, a, b) -> str:
    return f"{fmt(a)} / {fmt(b)}"


def build_report(cfg, data: dict, status: dict, yesterday: date) -> tuple[str, str]:
    """(subject, plain text): headline numbers first, then one block per section."""
    days = cfg.growth_report_days
    ga, sc, sm, ads, gads, st = (data.get(k) for k in ("ga4", "search_console", "sitemap",
                                                       "meta_ads", "google_ads", "stripe"))
    lines = [f"Growth report for {yesterday:%a %b %d, %Y}: yesterday / last {days} days", ""]

    if ga:
        lines.append("Sessions: " + _pair(_n, ga["yesterday"]["sessions"], ga["window"]["sessions"]))
        lines.append(f"Clicks out yesterday: {_n(ga['yesterday']['deal_click'])}")
    stores, subs = data.get("ga4_stores"), data.get("subscribers")
    if not ga and stores and "unavailable" not in stores:
        lines.append(f"Clicks out yesterday: {_n(stores['yesterday']['total'])}")
    if subs and "unavailable" not in subs:
        lines.append(f"Join free yesterday: {_n(subs['yesterday'])}")
    if ga:
        lines.append("Visitors: " + _pair(_n, ga["yesterday"]["totalUsers"], ga["window"]["totalUsers"]))
        lines.append("Sign-ups: " + _pair(_n, ga["yesterday"]["sign_up"], ga["window"]["sign_up"]))
    trials = ((st["trials_yesterday"], st["trials_window"]) if st else
              (ga["yesterday"]["start_trial"], ga["window"]["start_trial"]) if ga else None)
    if trials:
        lines.append("Trials started: " + _pair(_n, *trials))
    if st:
        lines.append(f"Paying members: {_n(st['active'])} (MRR estimate {_usd(st['mrr'])})")
    paid = [a for a in (ads, gads) if a]  # Meta + Google
    if paid:
        spend = tuple(sum(a[k]["spend"] for a in paid) for k in ("yesterday", "window"))
        lines.append("Ad spend: " + _pair(_usd, *spend))
        if trials:
            lines.append("Cost per trial: " + " / ".join(
                _usd(s / t) if t else "n/a" for s, t in zip(spend, trials)))

    if "site_health" in data:
        lines += site_health.lines(data["site_health"])

    lines += ["", "== Money =="]
    mix = data.get("site_health", {}).get("link_mix")
    if mix is not None:
        lines.append(f"Get deal links: {_n(mix['amazon_tagged'])} Amazon (tagged), "
                     f"{_n(mix['other_store'])} other stores, {_n(mix['source_post'])} source posts, "
                     f"{_n(mix['none'])} none")
        lines.append("Other store hosts: " + (", ".join(
            f"{host} {_n(n)}" for host, n in list(mix["by_store"].items())[:5]) or "none"))
        if mix["amazon_untagged"] or mix["amazon_short"]:
            lines.append(("⚠ " if mix["amazon_untagged_flag"] else "")
                         + f"Amazon links: {_n(mix['amazon_untagged'])} untagged, "
                         f"{_n(mix['amazon_short'])} short")
    else:
        lines.append("Get deal links: not available")
    if stores and "unavailable" not in stores:
        for key, label in (("yesterday", "yesterday"), ("window", f"last {days} days")):
            lines.append(f"Clicks out by store ({label}): total {_n(stores[key]['total'])}")
            lines += [f"  {name or '(not set)'}: {_n(n)}" for name, n in stores[key]["stores"]]
    elif stores:
        lines.append(stores["unavailable"])
    else:
        lines.append(f"Clicks out by store: not available ({status.get('ga4_stores', 'skipped')})")
    if subs and "unavailable" not in subs:
        lines.append(f"Join free list: total {_n(subs['total'])}, new yesterday {_n(subs['yesterday'])}")
    else:
        reason = subs["unavailable"] if subs else status.get("subscribers", "skipped")
        lines.append(f"subscribers: not available ({reason})")

    if ga:
        y, w = ga["yesterday"], ga["window"]
        lines += ["", "== Google Analytics ==",
                  "Sessions: " + _pair(_n, y["sessions"], w["sessions"]),
                  "New users: " + _pair(_n, y["newUsers"], w["newUsers"]),
                  "Events: " + ", ".join(f"{e} {_pair(_n, y[e], w[e])}" for e in google.EVENTS),
                  f"Top sources ({days} days, sessions):"]
        lines += [f"  {name or '(not set)'}: {_n(n)}" for name, n in ga["sources"]]
        lines.append(f"Top landing pages ({days} days, sessions):")
        lines += [f"  {name or '(not set)'}: {_n(n)}" for name, n in ga["pages"]]
    if sc:
        lines += ["", f"== Search Console, {sc['start']:%b %d} to {sc['end']:%b %d} "
                      "(its data lags 2-3 days) ==",
                  f"Clicks {_n(sc['clicks'])}, impressions {_n(sc['impressions'])}, "
                  f"CTR {sc['ctr'] * 100:.1f}%, average position {sc['position']:.1f}",
                  "Top queries (clicks / impressions):"]
        lines += [f"  {q}: {_pair(_n, c, i)}" for q, c, i in sc["queries"]]
        lines.append("Top pages (clicks / impressions):")
        lines += [f"  {p}: {_pair(_n, c, i)}" for p, c, i in sc["pages"]]
    if sm:
        lines += ["", "== Sitemap ==",
                  f"Submitted {sm['url']}" + (" (Google has not read it yet)" if sm["pending"] else ""),
                  f"{_n(sm['submitted'])} URLs, {_n(sm['errors'])} errors, "
                  f"{_n(sm['warnings'])} warnings, last read {sm['last_downloaded'] or 'never'}"]
    if ads or "facebook" in data or "instagram" in data:
        lines += ["", "== Meta =="]
        if ads:
            y, w = ads["yesterday"], ads["window"]
            lines += [f"Impressions {_pair(_n, y['impressions'], w['impressions'])}, "
                      f"clicks {_pair(_n, y['clicks'], w['clicks'])}",
                      f"CTR {y['ctr']:.2f}% / {w['ctr']:.2f}%, CPC {_pair(_usd, y['cpc'], w['cpc'])}",
                      "Registrations " + _pair(_n, y["complete_registration"], w["complete_registration"])
                      + ", checkouts " + _pair(_n, y["initiate_checkout"], w["initiate_checkout"])
                      + ", trials " + _pair(_n, y["start_trial"], w["start_trial"])]
        if "facebook" in data:
            lines.append(f"Facebook Page followers: {_n(data['facebook'])}")
        if "instagram" in data:
            lines.append(f"Instagram followers: {_n(data['instagram'])}")
    if gads:
        y, w = gads["yesterday"], gads["window"]
        lines += ["", "== Google Ads ==",
                  f"Spend {_pair(_usd, y['spend'], w['spend'])}, "
                  f"impressions {_pair(_n, y['impressions'], w['impressions'])}, "
                  f"clicks {_pair(_n, y['clicks'], w['clicks'])}, "
                  f"conversions {y['conversions']:,.0f} / {w['conversions']:,.0f}"]
    if st:
        lines += ["", "== Stripe" + (" (TEST MODE key)" if st["test_mode"] else "") + " ==",
                  f"On trial now: {_n(st['trialing'])}",
                  f"Trials converted ({days} days): {_n(st['converted'])}",
                  f"Cancellations ({days} days): {_n(st['canceled'])}"]
    lines += ["", "Sections: " + ", ".join(f"{k} {v}" for k, v in status.items())]
    brand = cfg.custom_domain or "dealradar"
    return f"{brand} growth report {yesterday.isoformat()}", "\n".join(lines) + "\n"


def _send_email(host: str, to: str, subject: str, text: str, smtp_factory: Any) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = get_secret("SMTP_FROM") or get_secret("SMTP_USER") or f"dealradar@{host}"
    msg["To"] = to  # single recipient, the operator
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText('<html><body><pre style="font:14px/1.45 ui-monospace,monospace">'
                        + html_lib.escape(text) + "</pre></body></html>", "html", "utf-8"))
    server = smtp_connect(host, int(get_secret("SMTP_PORT") or "587"), smtp_factory=smtp_factory)
    try:
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass


def deliver(cfg, subject: str, text: str, *, client: Any,
            smtp_factory: Any = None) -> tuple[int, int]:
    """Private channels only: (configured, delivered)."""
    configured = delivered = 0
    host = get_secret("SMTP_HOST")
    to = get_secret("REPORT_EMAIL_TO") or cfg.contact_email
    if host and to:
        configured += 1
        try:
            _send_email(host, to, subject, text, smtp_factory)
            delivered += 1
            log.info("email: sent")
        except Exception as e:
            log.warning("email: error %s", _error(e))
    if get_secret("TELEGRAM_BOT_TOKEN") and get_secret("TELEGRAM_ADMIN_CHAT_ID"):
        configured += 1
        try:
            code = send_admin_text(text[:TELEGRAM_MAX], http_client=client)
        except Exception as e:
            log.warning("telegram: error %s", _error(e))
        else:
            if code == 200:
                delivered += 1
                log.info("telegram: sent")
            else:
                log.warning("telegram: error HTTP %s", code)
    if not configured:
        log.info("no private channel configured")
    return configured, delivered


def run(cfg, now: datetime | None = None, *, http_client: Any = None,
        smtp_factory: Any = None) -> int:
    """The exit code: 1 only when every configured section failed; a
    delivery failure is logged (channel + error class) but exits 0."""
    now = now or now_utc()
    client = http_client or httpx.Client(timeout=30)
    try:
        data, status = collect(cfg, now, client)
        ran = [s for s in status.values() if s != "skipped"]
        if not ran:
            log.info("no section configured; nothing to send")
            return 0
        yesterday = now.astimezone(ZoneInfo(cfg.timezone)).date() - timedelta(days=1)
        subject, text = build_report(cfg, data, status, yesterday)
        deliver(cfg, subject, text, client=client, smtp_factory=smtp_factory)
    finally:
        if http_client is None:
            client.close()
    return 1 if all(s == "error" for s in ran) else 0
