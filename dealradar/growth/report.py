"""Collect every configured section, build the report, deliver it privately.

Each section is skipped (one log line) when its secret or config is missing,
and a failing section never stops the others. Logs carry section names and
ok / skipped / error class only, except safe service-account shape errors:
the Actions logs are public, and other exception messages may echo a response
body or a token.
"""

from __future__ import annotations

import base64
import functools
import html as html_lib
import logging
import re
import time as clock
from datetime import date, datetime, time, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..config import get_secret
from .. import clicks
from ..db import DB
from ..fetch.newsletter import partner_mail, unknown_sender_counts, unknown_sender_text
from ..publish.admin_alerts import send_admin_text
from ..publish.email_digest import smtp_connect
from ..publish import indexnow
from ..util import now_utc
from . import ads_guard, bing, cloudflare_traffic, google, google_ads, meta, site_health, stripe_stats, subscribers

log = logging.getLogger("dealradar.growth")

SEARCH_LAG_DAYS = 3   # Search Console data trails by 2-3 days
TELEGRAM_MAX = 4000   # under Telegram's 4,096-character message limit
TECHNICAL_HEADING = "Technical details (for Claude — you can skip this)"
NOT_CONNECTED = "not connected yet"


def _error(e: Exception) -> str:
    """The class name (plus the HTTP status), never the message."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"{type(e).__name__} {e.response.status_code}"
    return type(e).__name__


def _critic_shadow(db_path, now: datetime) -> dict:
    db = DB(db_path, read_only=True)
    try:
        return {"today": db.critic_shadow_counts(now),
                "week": db.critic_shadow_counts(now, days=7)}
    finally:
        db.close()


def _ai_usage(db_path, now: datetime) -> dict:
    from .. import ai_usage

    db = DB(db_path, read_only=True)
    try:
        sample = None
        if db.conn.execute("SELECT 1 FROM sqlite_master WHERE name='critic_tier_samples'").fetchone():
            sample = dict(db.conn.execute("SELECT COUNT(*) AS judged, COALESCE(SUM(agrees),0) AS agreed"
                                          " FROM critic_tier_samples").fetchone())
        return {"today": ai_usage.daily(db, now),
                "yesterday": ai_usage.daily(db, now.astimezone(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0) - timedelta(microseconds=1)),
                "tiering_fallback": db.meta_get("critic_tiering_fallback") or "",
                "tiering_sample": sample}
    finally:
        db.close()


def _efficiency(db_path, cfg, now: datetime) -> dict:
    from ..efficiency import daily

    db = DB(db_path, read_only=True)
    try:
        return daily(db, cfg, now)
    finally:
        db.close()


def _click_signal(db_path, cfg):
    from ..clicks import report_data
    db = DB(db_path, read_only=True)
    try:
        return report_data(db, cfg)
    finally:
        db.close()


def _ads_guard(db_path, cfg, now):
    db = DB(db_path, read_only=True)
    try:
        return ads_guard.report_data(db, cfg, now)
    finally:
        db.close()


def _legitimacy_audit(db_path, cfg, now: datetime) -> dict:
    from ..legitimacy import daily_stats

    db = DB(db_path, read_only=True)
    try:
        return daily_stats(db, cfg, now)
    finally:
        db.close()


def _ai_yield(db_path, cfg, now: datetime) -> dict:
    from ..ai_yield import report_data

    db = DB(db_path, read_only=True)
    try:
        return report_data(db, cfg, now)
    finally:
        db.close()


def _intake(db_path, cfg, now: datetime) -> dict:
    from ..source_audit import intake_report_data

    db = DB(db_path, read_only=True)
    try:
        return intake_report_data(db, cfg, now)
    finally:
        db.close()


def _price_recheck(db_path, now: datetime) -> dict:
    from ..keepa_rechecks import report_data

    db = DB(db_path, read_only=True)
    try:
        return report_data(db, now)
    finally:
        db.close()


def _unconfirmed_label(db_path) -> dict:
    from ..publish.price_labels import report_data

    db = DB(db_path, read_only=True)
    try:
        return report_data(db)
    finally:
        db.close()


def _non_amazon_freshness(db_path) -> dict:
    from ..non_amazon_freshness import report_data

    db = DB(db_path, read_only=True)
    try:
        return report_data(db)
    finally:
        db.close()


def _product_shadow(db_path, cfg, now) -> dict:
    from ..product_shadow import report_data

    db = DB(db_path, read_only=True)
    try:
        return report_data(db, cfg, now)
    finally:
        db.close()


def _pinterest(db_path, cfg, now) -> dict:
    from .pinterest import report_data

    db = DB(db_path, read_only=True)
    try:
        return report_data(db, cfg, now)
    finally:
        db.close()


def _newsletter_senders(db_path) -> dict:
    db = DB(db_path, read_only=True)
    try:
        return unknown_sender_counts(db)
    finally:
        db.close()


def newsletter_arrivals(received: dict, sources: list) -> dict:
    """Which permission partners' newsletters have arrived (operator,
    2026-10-14: "let me know which newsletters we are still waiting for, so I
    can keep attempting"). One row per partner site, however many feeds it has."""
    partners = {}
    for s in sources:
        if s.crawl_permission and s.method != "email":
            partners.setdefault(s.display_name or s.name, {"site": s.site or s.url, "names": set()})[
                "names"].add(s.name)
    got = sorted(name for name, p in partners.items() if p["names"] & set(received))
    since = min((row.get("first_seen") or row.get("last_seen") or "") for row in received.values()) \
        if received else ""
    return {"since": since, "total": len(partners), "received": got,
            "missing": [{"name": name, "site": partners[name]["site"]}
                        for name in sorted(partners) if name not in got]}


def _newsletter_arrivals(db_path) -> dict:
    from ..config import load_sources
    db = DB(db_path, read_only=True)
    try:
        primary, backup = load_sources()
        return newsletter_arrivals(partner_mail(db), primary + backup)
    finally:
        db.close()


def _newsletter_lines(data: dict) -> list[str]:
    arrivals = data.get("newsletter_arrivals") or {}
    if not arrivals or "unavailable" in arrivals:
        return []
    since = arrivals["since"] or "today"
    lines = ["", f"Newsletters still waiting on (tracking since {since[:10] if since != 'today' else since})",
             f"Received from {len(arrivals['received'])} of {arrivals['total']} partner sites."]
    if arrivals["missing"]:
        lines.append("Not received yet. Try resubscribing with deals@wowpriced.com:")
        lines += [f"  • {row['name']}: {row['site']}" for row in arrivals["missing"]]
        lines.append("(Some sites only email weekly, so give each one 7 days.)")
    return lines


def _indexnow(db_path, cfg, now: datetime) -> dict:
    db = DB(db_path, read_only=True)
    try:
        return indexnow.metrics(db, cfg, now)
    finally:
        db.close()


def collect(cfg, now: datetime, client: Any, *, db_path=None) -> tuple[dict, dict]:
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
    cf_ops = get_secret("CLOUDFLARE_OPS_TOKEN")
    bing_key = get_secret("BING_WEBMASTER_API_KEY")
    gads = [(name, get_secret(name)) for name in
            ("GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN")]
    token = functools.cache(lambda: google.access_token(client, sa))
    version = cfg.growth_meta_graph_version
    need_google, need_meta = ("GOOGLE_SA_JSON", sa), ("META_SYSTEM_TOKEN", meta_token)
    site = ("growth.search_console_site", cfg.growth_search_console_site)

    sections = {
        "pinterest": ([("social.pinterest.analytics.enabled",
                        (cfg.pinterest.get("analytics") or {}).get("enabled", False)),
                       ("state.db", bool(db_path) and Path(db_path).is_file())],
                      lambda: _pinterest(db_path, cfg, now)),
        "ads_guard": ([("state.db", bool(db_path) and Path(db_path).is_file())],
                      lambda: _ads_guard(db_path, cfg, now)),
        "click_signal": ([("first_party_clicks.enabled", clicks.enabled(cfg)),
                          ("state.db", bool(db_path) and Path(db_path).is_file())],
                         lambda: _click_signal(db_path, cfg)),
        "efficiency": ([("state.db", bool(db_path) and Path(db_path).is_file())],
                       lambda: _efficiency(db_path, cfg, now)),
        "audit": ([("state.db", bool(db_path) and Path(db_path).is_file())],
                  lambda: _legitimacy_audit(db_path, cfg, now)),
        # Only an explicit snapshot (the CLI passes --db): never a stray local file.
        "ai_yield": ([("state.db", bool(db_path) and Path(db_path).is_file())],
                     lambda: _ai_yield(db_path, cfg, now)),
        "indexnow": ([("state.db", bool(db_path) and Path(db_path).is_file())],
                     lambda: _indexnow(db_path, cfg, now)),
        "intake": ([("state.db", bool(db_path) and Path(db_path).is_file())],
                   lambda: _intake(db_path, cfg, now)),
        "price_recheck": ([("state.db", bool(db_path) and Path(db_path).is_file())],
                          lambda: _price_recheck(db_path, now)),
        "unconfirmed_label": ([("state.db", bool(db_path) and Path(db_path).is_file())],
                              lambda: _unconfirmed_label(db_path)),
        "non_amazon_freshness": ([("state.db", bool(db_path) and Path(db_path).is_file())],
                                lambda: _non_amazon_freshness(db_path)),
        "product_shadow": ([("state.db", bool(db_path) and Path(db_path).is_file())],
                           lambda: _product_shadow(db_path, cfg, now)),
        "critic_shadow": ([("state.db", bool(db_path) and Path(db_path).is_file())],
                          lambda: _critic_shadow(db_path, now)),
        "ai_usage": ([("state.db", bool(db_path) and Path(db_path).is_file())],
                     lambda: _ai_usage(db_path, now)),
        "partner_newsletters": ([("state.db", bool(db_path) and Path(db_path).is_file())],
                                lambda: _newsletter_senders(db_path)),
        "newsletter_arrivals": ([("state.db", bool(db_path) and Path(db_path).is_file())],
                                lambda: _newsletter_arrivals(db_path)),
        "site_health": ([("publish.custom_domain", cfg.custom_domain)],
                        lambda: site_health.check(client, cfg, now)),
        "cloudflare_traffic": ([("CLOUDFLARE_OPS_TOKEN", cf_ops),
                                ("publish.custom_domain", cfg.custom_domain)],
                               lambda: cloudflare_traffic.stats(client, cf_ops, cfg.custom_domain,
                                                               yesterday, start)),
        "bing": ([("BING_WEBMASTER_API_KEY", bing_key), ("publish.custom_domain", cfg.custom_domain)],
                 lambda: bing.stats(client, bing_key, cfg.custom_domain, yesterday, start)),
        "ga4": ([need_google, ("growth.ga4_property_id", cfg.growth_ga4_property_id)],
                lambda: google.ga4(client, token(), cfg.growth_ga4_property_id, days)),
        "ga4_stores": ([need_google, ("growth.ga4_property_id", cfg.growth_ga4_property_id)],
                       lambda: google.ga4_stores(client, token(), cfg.growth_ga4_property_id, days)),
        "saved": ([need_google, ("growth.ga4_property_id", cfg.growth_ga4_property_id)],
                  lambda: google.ga4_saved(client, token(), cfg.growth_ga4_property_id, days)),
        "deal_pages": ([need_google, ("growth.ga4_property_id", cfg.growth_ga4_property_id)],
                       lambda: google.ga4_deal_pages(client, token(), cfg.growth_ga4_property_id, days)),
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
            if name in ("cloudflare_traffic", "bing"):
                log.info("%s: skipped", name)
            else:
                log.info("%s: skipped (%s not set)", name, missing)
            continue
        try:
            data[name] = fetch()
            unavailable = isinstance(data[name], dict) and "unavailable" in data[name]
            status[name] = "skipped" if unavailable else "ok"
            log.info("%s: %s", name, status[name])
        except Exception as e:  # one section never stops the others
            status[name] = "error"
            if name in ("cloudflare_traffic", "bing"):
                code = str(e) if isinstance(e, bing.BingError) else ""
                log.warning("%s: error%s", name, " " + code if code else "")
                continue
            if name == "subscribers":
                data[name] = {"unavailable": _error(e)}
            if name == "indexnow":
                data[name] = {**dict.fromkeys(indexnow.METRICS), "unavailable": "state snapshot unavailable"}
            detail = (type(e).__name__ if name == "site_health" else
                      str(e) if isinstance(e, (google.ServiceAccountError, google_ads.GoogleAdsError))
                      else _error(e))
            log.warning("%s: error %s", name, detail)
    status["product"] = status["ga4"]
    if status["product"] == "ok":
        data["product"] = {
            period: {name: data["ga4"][period][event] for name, event in google.PRODUCT_EVENTS.items()}
            for period in ("yesterday", "window")}
        for period in ("yesterday", "window"):
            detail = data.get("deal_pages", {}).get(period, {})
            data["product"][period].update({name: detail.get(name) for name in google.DEAL_PAGE_COUNTS})
            watches = data.get("saved", {}).get(period, {})
            data["product"][period].update({name: watches.get(name) for name in google.WATCH_EVENTS})
        try:
            data["product"]["deal_sessions"] = google.ga4_deal_sessions(client, token(), cfg.growth_ga4_property_id, days)
        except Exception:
            data["product"]["deal_sessions"] = {"unavailable": "device session rate unavailable"}
        try:
            data["product"]["requirements_sessions"] = google.ga4_deal_sessions(
                client, token(), cfg.growth_ga4_property_id, days, requirements=True)
        except Exception:
            data["product"]["requirements_sessions"] = {"unavailable": "device session rate unavailable"}
    log.info("product: %s", status["product"])
    return data, status


def _n(x: int) -> str:
    return f"{x:,}"


def _usd(x: float) -> str:
    return f"${x:,.2f}"


def _pair(fmt, a, b) -> str:
    return f"{fmt(a)} / {fmt(b)}"


def _source_name(source_medium: str) -> str:
    source, _, medium = source_medium.lower().strip().partition(" / ")
    if source == "(direct)":
        return "typed the address or bookmark"
    if source in ("google", "google.com"):
        return {"organic": "Google search", "cpc": "Google ads", "ppc": "Google ads"}.get(medium, "other")
    if source in ("bing", "bing.com"):
        return {"organic": "Bing search", "cpc": "Bing ads", "ppc": "Bing ads"}.get(medium, "other")
    for name, hosts in (("Facebook", ("facebook", "facebook.com", "fb", "fb.com")),
                        ("Instagram", ("instagram", "instagram.com", "ig")),
                        ("Pinterest", ("pinterest", "pinterest.com")),
                        ("TikTok", ("tiktok", "tiktok.com")),
                        ("YouTube", ("youtube", "youtube.com")),
                        ("Reddit", ("reddit", "reddit.com")),
                        ("Threads", ("threads", "threads.net", "threads.com"))):
        if any(source == host or source.endswith("." + host) for host in hosts):
            return name
    if medium == "email":
        return "email"
    return "other"


def _change(current, previous) -> str:
    if current is None or previous is None:
        return NOT_CONNECTED
    delta = current - previous
    return f"up {_n(delta)}" if delta > 0 else f"down {_n(-delta)}" if delta < 0 else "same"


def _plain_summary(cfg, data: dict, yesterday: date) -> list[str]:
    """Use only collected measurements; never turn a missing source into zero."""
    def section(name):
        value = data.get(name) or {}
        return {} if "unavailable" in value else value

    def number(value, fmt=_n):
        return NOT_CONNECTED if value is None else fmt(value)

    def money(value):
        return f"${value:,.0f}" if value == int(value) else _usd(value)

    window = "this week" if cfg.growth_report_days == 7 else f"over the last {cfg.growth_report_days:,} days"

    def periods(a, b, fmt=_n):
        if a is None and b is None:
            return NOT_CONNECTED
        return f"{number(a, fmt)} yesterday · {number(b, fmt)} {window}"

    ga, subs, stores = section("ga4"), section("subscribers"), section("ga4_stores")
    y, w, before = (ga.get(period, {}) for period in ("yesterday", "window", "day_before"))
    # sign_up is the existing GA4 sign-up action count, for both periods.
    # The subscriber list's all-time total must never stand in for this week.
    signups_y = y.get("sign_up") if ga else subs.get("yesterday")
    signups_w = w.get("sign_up") if ga else subs.get("window")
    signups_before = before.get("sign_up") if ga else subs.get("day_before")
    st = section("stripe")
    members = NOT_CONNECTED
    if st.get("active") is not None and not st.get("test_mode"):
        members = f"{_n(st['active'])} ({number(st.get('mrr'), money)} a month)"
    paid = [section(name) for name in ("meta_ads", "google_ads")]
    spend = []
    for period in ("yesterday", "window"):
        values = [a.get(period, {}).get("spend") for a in paid]
        spend.append(sum(values) if all(v is not None for v in values) else None)
    health = section("site_health")
    deals = number(health.get("live"))
    if health.get("live") is not None:
        coverage = number(health.get("coverage"), lambda n: f"{n:,.0f}%")
        deals += f" ({coverage} with a photo)"
    website = NOT_CONNECTED
    if health:
        if health.get("home_status") == 200 and health.get("home_content"):
            website = "working"
            if "stale publish" in health.get("warnings", []):
                website += "; updates delayed"
        elif health.get("home_status"):
            website = "needs attention"
        else:
            website = "could not be checked"
        age = health.get("age_minutes")
        if age is not None and age >= 0:
            minutes = round(age)
            website += f" (updated {minutes:,} {'minute' if minutes == 1 else 'minutes'} ago)"
        else:
            website += f" (update time {NOT_CONNECTED})"
    sources: dict[str, int] = {}
    for source, count in ga.get("sources", []):
        name = _source_name(source)
        sources[name] = sources.get(name, 0) + count
    source_rows = [f"Where visitors came from ({window}, visits): "]
    for item in ([f"{name} {_n(count)}" for name, count in sources.items()] or [NOT_CONNECTED]):
        if source_rows[-1].endswith(": "):
            source_rows[-1] += item
        elif len(source_rows[-1]) + len(item) + 2 <= 100:
            source_rows[-1] += ", " + item
        else:
            source_rows[-1] += ","
            source_rows.append("  " + item)
    lines = [f"wowpriced daily report — {yesterday:%a %b} {yesterday.day}",
             "Real people only. Robots and search engines are NOT counted here.", ""]
    for label, value in (
        ("People who visited:", periods(y.get("totalUsers"), w.get("totalUsers"))),
        ('Clicked "Get deal":', periods(y.get("deal_click") if ga else stores.get("yesterday", {}).get("total"),
                                       w.get("deal_click") if ga else stores.get("window", {}).get("total"))),
        ("Free sign-ups:", periods(signups_y, signups_w)),
        ("Paying members:", members),
        ("Ad spend:", periods(*spend, fmt=money)),
    ):
        lines.append(f"{label:<25}{value}")
    lines += source_rows
    lines += [f"{'Deals on the site:':<25}{deals}", f"{'Website:':<25}{website}", "",
              "Compared with the day before: visitors " + _change(y.get("totalUsers"), before.get("totalUsers"))
              + ", sign-ups " + _change(signups_y, signups_before) + ".", ""]
    cf = section("cloudflare_traffic").get("window", {}).get("page_views")
    if cf is None:
        lines.append(f"Search engine and robot traffic: {NOT_CONNECTED}. These are NOT visitors.")
    else:
        # Cloudflare's existing query includes all traffic, without a robot-only split.
        lines.append(f"Search engines and other robots are included in {_n(cf)} page reads {window} "
                     "(Google, Bing and other robots). That is good for future search traffic, "
                     "but these are NOT visitors.")
    return lines


def _collapse_unmeasured(lines: list[str]) -> list[str]:
    """Collapse missing metric rows, preserving partial values and explanations."""
    result, missing = [], []
    context = ""
    missing_value = r"(?:unavailable|not available|n/a)"
    for line in lines:
        label, sep, values = line.strip().partition(": ")
        if line.startswith("==") or line.endswith(":"):
            context = line.strip("= :")
        # Missing-only blocks sometimes put their value on the following line.
        standalone = re.match(r"(?:unavailable|not available)(?:$| \(| — )", line.strip(), re.I) \
            or line == "Unavailable: no retained publish observations."
        if standalone:
            label, values = context, line.strip()
        # Parentheses describe why a measurement is absent, except an observed
        # zero-session denominator, which remains a real number for the engineer.
        bare = re.sub(r"\((?!0 sessions\))[^()]*\)", "", values)
        pure = re.fullmatch(rf"{missing_value}(?:\s*/\s*{missing_value})*(?:\s*—.*)?\s*", bare, re.I)
        metric_row = label.startswith(("Keepa tokens", "Tokens per useful", "Oldest shown Amazon",
                                       "Shown Amazon", "LLM dollars")) or values.startswith("clicks ") \
            or label == "Events" or (label.startswith(("yesterday ", "window ")) and "actions/view" in values)
        compound = metric_row and "unavailable" in values and not re.search(r"\d", values)
        if metric_row and re.search(r"\d", values):
            pure = False  # Missing ratio with measured costs/counts in parentheses.
        if line.startswith("Keepa deal-finding efficiency is unavailable;") and not re.search(r"\d", bare):
            label, compound = "Keepa deal-finding efficiency and oldest shown Amazon price", True
        if line.startswith("Clicks unavailable,") and not re.search(r"\d", line):
            label, compound, sep = "Search Console totals", True, True
        if standalone or (sep and (pure or compound)):
            if line.startswith("  ") and not standalone:
                label = f"{context} — {label}"
            if label not in missing:
                missing.append(label)
        else:
            result.append(line)
    if missing:
        # Keep the existing section-status footer last.
        index = len(result) - 1 if result and result[-1].startswith("Sections:") else len(result)
        result.insert(index, "not measured yet: " + "; ".join(missing))
    return result


def build_report(cfg, data: dict, status: dict, yesterday: date) -> tuple[str, str]:
    """(subject, plain text): headline numbers first, then one block per section."""
    days = cfg.growth_report_days
    ga, sc, sm, ads, gads, st = (data.get(k) for k in ("ga4", "search_console", "sitemap",
                                                       "meta_ads", "google_ads", "stripe"))
    lines = [f"Growth report for {yesterday:%a %b %d, %Y}: yesterday / last {days} days", ""]
    if senders := data.get("partner_newsletters"):
        lines += [unknown_sender_text(senders), ""]

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
    cf = data.get("cloudflare_traffic")
    if cf:
        y, w = cf["yesterday"], cf["window"]
        lines.append("Cloudflare traffic (mostly robots): uniques "
                     + _pair(_n, y["visitors"], w["visitors"]) + "; page views "
                     + _pair(_n, y["page_views"], w["page_views"]) + "; requests "
                     + _pair(_n, y["requests"], w["requests"]))
        lines.append("Cloudflare page views (yesterday / window): "
                     + _pair(_n, y["page_views"], w["page_views"]) + "; requests "
                     + _pair(_n, y["requests"], w["requests"]))
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
    lines += ["", *ads_guard.report_lines(data.get("ads_guard"))]
    lines += ["", *indexnow.summary_lines(data.get("indexnow") or {
        **dict.fromkeys(indexnow.METRICS), "unavailable": "state snapshot unavailable"})]

    from ..efficiency import lines as efficiency_lines
    efficiency = data.get("efficiency")
    day = efficiency.get("day", "yesterday") if efficiency else "yesterday"
    lines += ["", f"== Efficiency ({day} UTC) ==", *efficiency_lines(efficiency, cfg)]
    from ..clicks import enabled as clicks_enabled, report_lines as click_lines
    if clicks_enabled(cfg):
        lines += ["", *click_lines(data.get("click_signal"))]
    from ..product_shadow import report_lines as product_shadow_lines
    lines += ["", *product_shadow_lines(data.get("product_shadow"))]
    if (cfg.pinterest.get("analytics") or {}).get("enabled", False):
        from .pinterest import report_lines as pinterest_lines
        lines += ["", *pinterest_lines(data.get("pinterest"))]

    lines += ["", "== Money =="]
    mix = data.get("site_health", {}).get("link_mix")
    if mix is not None:
        lines.append(f"Get deal links: {_n(mix['amazon_tagged'])} Amazon (tagged), "
                     f"{_n(mix.get('sovrn', 0))} via Sovrn, "
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

    from ..legitimacy import daily_line
    lines.append(daily_line(data.get("audit")))

    shadow = data.get("critic_shadow")
    from ..ai_usage import lines as usage_lines
    usage = data.get("ai_usage")
    if usage:
        lines += usage_lines(usage["yesterday"]) + usage_lines(usage["today"])
        sample = usage.get("tiering_sample")
        if sample and sample["judged"]:
            lines.append(f"Tiered critic Sonnet sample: {sample['agreed']}/{sample['judged']} agree "
                         "in the retained rolling window")
        if usage["tiering_fallback"]:
            lines.append("Tiered critic fallback: " + usage["tiering_fallback"])
    else:
        lines += usage_lines(None)
    if shadow is not None:
        today, week = shadow["today"], shadow["week"]
        lines.append("critic shadow (Haiku vs Sonnet): "
                     f"agree {today['same_verdict']}/{today['deals_judged']} today, "
                     f"7-day {week['same_verdict']}/{week['deals_judged']}")
    else:
        lines.append("critic shadow (Haiku vs Sonnet): not available")

    yield_data = data.get("ai_yield")
    if yield_data and "unavailable" not in yield_data:
        lines.append("AI yield (UTC, yesterday / last 7 days):")
        lines.append(f"  Observation began {yield_data['observed_since']}; earlier history unavailable")
        for key, label in (("yesterday", "yesterday"), ("week", "7-day")):
            values = yield_data[key]
            if "unavailable" in values:
                lines.append(f"  {label}: n/a ({values['unavailable']})")
                continue
            ratio = values["new_deals_per_usd"]
            ratio_text = f"{ratio:.2f}" if ratio is not None else "n/a"
            lines.append(f"  {label}: new public deals {_n(values['unique_new_public_deals'])}, "
                         f"useful price updates {_n(values['useful_price_updates'])}, "
                         f"new deals/USD {ratio_text}")
            pf = values["prefilter"]
            lines.append(f"  Prefilter: {pf['fully_evaluated']}/{pf['candidates']} evaluated, "
                         f"{pf['publishable_misses']} publishable misses, "
                         f"{values['prefilter_unevaluated']} unevaluated; "
                         f"all AI work {_usd(values['spend_usd'])}")
            coverage = values["coverage"]
            lines.append(f"  Coverage: {coverage['from']} to {coverage['through']}"
                         + (" (partial; earlier history unavailable)" if coverage["partial"] else ""))
            for source, counts in pf["by_source"].items():
                lines.append(f"    {source}: {counts['candidates']} candidates, "
                             f"{counts['publishable_misses']} misses, {counts['unevaluated']} unevaluated")
        ready = yield_data["readiness"]
        lines.append("Prefilter activation: " + ("ready for manual review" if ready["ready_for_review"]
                     else "keep shadow — " + "; ".join(ready["reasons"])))
    else:
        reason = yield_data["unavailable"] if yield_data else "state snapshot unavailable"
        lines.append(f"AI yield: n/a ({reason})")

    from ..publish.price_labels import summary_lines as label_lines
    lines += label_lines(data.get("unconfirmed_label"))
    from ..non_amazon_freshness import summary_lines as freshness_lines
    lines += freshness_lines(data.get("non_amazon_freshness"))
    rechecks = data.get("price_recheck")
    lines.append("Keepa recheck yield (UTC, yesterday / last 7 days):")
    if not rechecks or "unavailable" in rechecks:
        lines.append("  unavailable (no recheck observations yet)")
    else:
        lines.append(f"  Observation began {rechecks['observed_since']}; earlier history unavailable")
        for period in ("yesterday", "week"):
            values = rechecks[period]
            if "unavailable" in values:
                lines.append(f"  {period}: unavailable ({values['unavailable']})")
                continue
            for name, counts in [("overall", values["overall"]), *sorted(values["classes"].items())]:
                ratio = counts["tokens_per_useful_reading"]
                ratio = f"{ratio:.2f}" if ratio is not None else "n/a"
                lines.append(f"  {period} {name}: {counts['tokens']:.1f} tokens "
                             f"({counts['estimated_tokens']:.1f} estimated), {counts['readings']} readings, "
                             f"{counts['useful']} useful ({counts['changed']} changes, {counts['deaths']} deaths), "
                             f"{ratio} tokens/useful reading")

    intake = data.get("intake")
    lines.append(f"Intake ({cfg.intake_mode}):")
    if not intake or "unavailable" in intake:
        reason = intake["unavailable"] if intake else "state snapshot unavailable"
        lines.append(f"  unavailable ({reason})")
    else:
        lines.append(f"  {intake['observed_hours']:.0f} hours observed; shared AI costs estimated from current/recent handled posts")
        for key, label in (("top", "Top"), ("bottom", "Bottom")):
            values = [f"{s['source']} {s['useful_per_usd']:.2f}/USD "
                      f"({s['unique_new_public_deals']} new + {s['useful_price_updates']} updates, "
                      f"{s['posts']} posts, {s['calls']} extraction calls, ${s['usd']:.4f})" for s in intake[key]]
            lines.append(f"  {label}: " + "; ".join(values))
    if intake and intake.get("oldest_deferred_source"):
        lines.append(f"  Oldest deferred: {intake['oldest_deferred_source']}, "
                     f"{intake['oldest_deferred_hours']:.2f} hours")

    lines += ["", "== Product (GA4 event sample, yesterday / window) =="]
    product = data.get("product")
    if product:
        lines += [f"product.{name}: " + _pair(lambda n: _n(n) if n is not None else "unavailable",
                                           product["yesterday"].get(name), product["window"].get(name))
                  for name in google.PRODUCT_EVENTS]
        sessions = product.get("deal_sessions") or {"unavailable": "device session data not returned"}
        if "unavailable" in sessions:
            lines.append("Sessions with Get deal by device: unavailable — " + sessions["unavailable"])
        else:
            lines.append("Sessions with Get deal by device (yesterday / window; GA4 session rate):")
            for device in ("mobile", "desktop", "tablet"):
                def session_rate(period):
                    row = sessions.get(period, {}).get(device)
                    if row is None:
                        return "unavailable"
                    if not row["sessions"]:
                        return "unavailable (0 sessions)"
                    return f"{row['rate']:.1%} of {_n(row['sessions'])} sessions"

                lines.append(f"  {device}: " + _pair(session_rate, "yesterday", "window"))
    else:
        lines.append(f"product: not available ({status.get('product', 'skipped')})")
    lines.append("Install offers and acceptances are actions; standalone_session counts session launches, not retained users.")
    lines.append("Support ask counts are actions, not payments or amounts.")
    lines.append("Search counts are applied queries, empty results and recovery choices; absent event samples are unavailable.")
    sessions = (product or {}).get("requirements_sessions") or {"unavailable": "no observed session data"}
    lines.append("Free discovery: deal_share counts successful copies or native share handoffs, not delivery; "
                 "feed_open counts footer link actions, not subscriptions. share_arrival/feed_arrival count "
                 "tagged deal-page loads (including repeats), not unique people. related_hub_click counts "
                 "contextual store/budget navigation. Missing events are unavailable; no traffic lift is inferred.")
    lines.append("hub_page_click counts pagination link actions; product_family_click counts deal-to-product "
                 "navigation. These are actions, not unique visitors or purchases. Search page-family rows "
                 "include paginated hubs and product_family pages when observed.")
    lines.append("Step-offer outbound session action rate (share of all sessions; not purchases):")
    if "unavailable" in sessions:
        lines.append("  unavailable — " + sessions["unavailable"])
    else:
        for device in ("mobile", "desktop", "tablet"):
            values = []
            for period in ("yesterday", "window"):
                row = sessions.get(period, {}).get(device) or {}
                values.append(f"{row['rate']:.1%} of {_n(row['sessions'])} sessions"
                              if row.get("rate") is not None and row.get("sessions") else "unavailable")
            lines.append(f"  {device}: " + " / ".join(values))
    def observed(value):
        return _n(value) if value is not None else "unavailable (no observed event)"

    for name in google.DEAL_PAGE_COUNTS:
        lines.append(f"product.{name}: " + _pair(observed,
            (product or {}).get("yesterday", {}).get(name), (product or {}).get("window", {}).get(name)))
    detail = data.get("deal_pages")
    if detail and "unavailable" not in detail:
        lines.append("Page actions by device/page family (GA4 sample):")
        for period in ("yesterday", "window"):
            for row in detail[period]["by_device_page"]:
                views, clicks = row["views"], row["outbound_clicks"]
                ratio = f"{clicks / views:.1%}" if views and clicks is not None else "unavailable"
                lines.append(f"  {period} {row['device']}/{row['family']}: "
                             f"{observed(views)} views, {observed(clicks)} outbound actions, "
                             f"actions/view {ratio}, {observed(row['code_copies'])} code copies")
        lines.append("Actions/view counts Get deal and fallback-title actions, including related offers; "
                     "repeat actions can exceed views. These are actions, not purchases. "
                     "Code copies count successful clipboard writes only.")
    else:
        lines.append("Device/page-family actions: unavailable — " + (detail or {}).get("unavailable",
                     status.get("deal_pages", "not in this snapshot")))
    saved = data.get("saved")
    for event in google.WATCH_EVENTS:
        lines.append(f"product.{event}: " + _pair(observed,
            (saved or product or {}).get("yesterday", {}).get(event),
            (saved or product or {}).get("window", {}).get(event)))
    lines.append("Price-watch counts are actions checked when visitors load public deals, not notifications or purchases.")
    if saved:
        lines.append(f"Feature actions (yesterday / last {days} days; event counts):")
        for event in google.SAVED_EVENTS:
            lines.append(f"  {event}: " + _pair(observed, saved["yesterday"].get(event),
                                               saved["window"].get(event)))
        returning = saved.get("returning") or {"unavailable": "visitor classification not returned"}
        if "unavailable" in returning:
            lines.append("Observed returning sessions (last 7 completed days): unavailable — "
                         + returning["unavailable"])
        else:
            count, classified = returning["sessions"], returning["classified_sessions"]
            lines.append(f"Observed returning sessions (last 7 completed days): {_n(count)} / "
                         f"{_n(classified)} classified sessions ({count / classified:.1%}); "
                         f"{_n(returning['unclassified_sessions'])} unclassified sessions excluded")
        lines.append("deal_save includes save, remove and clear actions. Return visits are GA4's "
                     "new/returning classification, not a save-to-purchase cohort or cross-device sync statistic.")
    else:
        lines.append(f"Feature actions and returning sessions: unavailable ({status.get('saved', 'skipped')})")

    if ga:
        y, w = ga["yesterday"], ga["window"]
        lines += ["", "== Google Analytics ==",
                  "Sessions: " + _pair(_n, y["sessions"], w["sessions"]),
                  "New users: " + _pair(_n, y["newUsers"], w["newUsers"]),
                  "Events: " + ", ".join(f"{e} {_pair(observed, y.get(e), w.get(e))}" for e in google.EVENTS),
                  f"Top sources ({days} days, sessions):"]
        lines += [f"  {name or '(not set)'}: {_n(n)}" for name, n in ga["sources"]]
        lines.append(f"Top landing pages ({days} days, sessions):")
        lines += [f"  {name or '(not set)'}: {_n(n)}" for name, n in ga["pages"]]
    if sc:
        def search_number(value):
            return _n(int(value)) if value is not None else "unavailable"

        def search_ctr(value):
            return f"{value * 100:.1f}%" if value is not None else "unavailable"

        position = f"{sc['position']:.1f}" if sc.get("position") is not None else "unavailable"
        lines += ["", f"== Search Console, {sc['start']:%b %d} to {sc['end']:%b %d} "
                      "(its data lags 2-3 days) ==",
                  f"Clicks {search_number(sc['clicks'])}, impressions {search_number(sc['impressions'])}, "
                  f"CTR {search_ctr(sc['ctr'])}, average position {position}",
                  "Top queries (clicks / impressions):"]
        lines += [f"  {q}: {_pair(search_number, c, i)}" for q, c, i in sc["queries"]]
        lines.append("Top pages (clicks / impressions):")
        lines += [f"  {p}: {_pair(search_number, c, i)}" for p, c, i in sc["pages"]]
        lines.append("Search by page family and device (returned Search Console rows):")
        if reason := sc.get("family_device_unavailable"):
            lines.append("  " + reason)
        for row in sc.get("by_family") or []:
            lines.append(f"  {row['family']}: clicks {search_number(row['clicks'])}, "
                         f"impressions {search_number(row['impressions'])}, CTR {search_ctr(row['ctr'])}")
        for row in sc.get("by_device_page") or []:
            lines.append(f"  {row['device']}/{row['family']}: clicks {search_number(row['clicks'])}, "
                         f"impressions {search_number(row['impressions'])}, CTR {search_ctr(row['ctr'])}")
        if not sc.get("by_device_page"):
            lines.append("  unavailable (no family/device sample)")
        lines.append("Search Console may omit rows; absent families/devices are unavailable, not zero. "
                     "CTR is summed clicks / summed impressions; zero impressions has no CTR.")
    else:
        lines += ["", "Search by page family and device: unavailable ("
                  + status.get("search_console", "skipped") + ")"]
    if sm:
        lines += ["", "== Sitemap ==",
                  f"Submitted {sm['url']}" + (" (Google has not read it yet)" if sm["pending"] else ""),
                  f"{_n(sm['submitted'])} URLs, {_n(sm['errors'])} errors, "
                  f"{_n(sm['warnings'])} warnings, last read {sm['last_downloaded'] or 'never'}"]
    bs = data.get("bing")
    if bs:
        lines += ["", "== Bing search =="]
        if bs["has_data"]:
            y, w = bs["yesterday"], bs["window"]
            lines.append("Clicks (yesterday / window): " + _pair(_n, y["clicks"], w["clicks"])
                         + "; impressions " + _pair(_n, y["impressions"], w["impressions"]))
        else:
            lines.append("no Bing data yet")
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
    lines = [*_plain_summary(cfg, data, yesterday), *_newsletter_lines(data), "", TECHNICAL_HEADING, "",
             *_collapse_unmeasured(lines)]
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
            # Device/family rows can exceed one message. Keep the full
            # private report, splitting at a newline when possible.
            top, boundary, details = text.partition("\n" + TECHNICAL_HEADING + "\n")
            parts = [top + "\n", TECHNICAL_HEADING + "\n" + details] if boundary else [text]
            code = 200
            for remaining in parts:
                while remaining:
                    end = len(remaining) if len(remaining) <= TELEGRAM_MAX else \
                        remaining.rfind("\n", 0, TELEGRAM_MAX) + 1 or TELEGRAM_MAX
                    code = send_admin_text(remaining[:end], http_client=client)
                    if code != 200:
                        break
                    remaining = remaining[end:]
                if code != 200:
                    break
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


def save_copy(cfg, subject: str, text: str, day: date, *, client: Any,
              pause_s: float = 1.0) -> bool:
    """One private file per report day (growth.reports_repo, REPORTS_REPO_TOKEN)
    so the operator's assistant can read the report without a mailbox."""
    repo = str((cfg.raw.get("growth") or {}).get("reports_repo") or "")
    token = get_secret("REPORTS_REPO_TOKEN")
    if not (repo and token):
        return False
    url = f"https://api.github.com/repos/{repo}/contents/reports/{day.isoformat()}.md"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": cfg.resolved_user_agent()}
    body = {"message": f"Growth report {day.isoformat()}",
            "content": base64.b64encode(f"# {subject}\n\n{text}".encode()).decode()}
    try:
        current = client.get(url, headers=headers)
        if current.status_code == 200:
            body["sha"] = current.json()["sha"]  # a re-run replaces that day's copy
        elif current.status_code != 404:
            current.raise_for_status()
        clock.sleep(pause_s)  # 1 request/second to one host
        client.put(url, headers=headers, json=body).raise_for_status()
    except Exception as e:
        log.warning("reports repo: error %s", _error(e))
        return False
    log.info("reports repo: saved")
    return True


def run(cfg, now: datetime | None = None, *, http_client: Any = None,
        smtp_factory: Any = None, db_path=None, copy_only: bool = False) -> int:
    """The exit code: 1 only when every configured section failed; a
    delivery failure is logged (channel + error class) but exits 0."""
    now = now or now_utc()
    client = http_client or httpx.Client(timeout=30)
    try:
        data, status = collect(cfg, now, client, db_path=db_path)
        ran = [s for s in status.values() if s != "skipped"]
        if not ran:
            log.info("no section configured; nothing to send")
            return 0
        yesterday = now.astimezone(ZoneInfo(cfg.timezone)).date() - timedelta(days=1)
        subject, text = build_report(cfg, data, status, yesterday)
        if not copy_only:
            deliver(cfg, subject, text, client=client, smtp_factory=smtp_factory)
        save_copy(cfg, subject, text, yesterday, client=client)
    finally:
        if http_client is None:
            client.close()
    return 1 if all(s == "error" for s in ran) else 0
