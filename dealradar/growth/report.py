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
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..config import get_secret
from ..db import DB
from ..fetch.newsletter import unknown_sender_counts, unknown_sender_text
from ..publish.admin_alerts import send_admin_text
from ..publish.email_digest import smtp_connect
from ..util import now_utc
from . import bing, cloudflare_traffic, google, google_ads, meta, site_health, stripe_stats, subscribers

log = logging.getLogger("dealradar.growth")

SEARCH_LAG_DAYS = 3   # Search Console data trails by 2-3 days
TELEGRAM_MAX = 4000   # under Telegram's 4,096-character message limit


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


def _ai_yield(db_path, cfg, now: datetime) -> dict:
    from ..ai_yield import report_data

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
        # Only an explicit snapshot (the CLI passes --db): never a stray local file.
        "ai_yield": ([("state.db", bool(db_path) and Path(db_path).is_file())],
                     lambda: _ai_yield(db_path, cfg, now)),
        "critic_shadow": ([("state.db", bool(db_path) and Path(db_path).is_file())],
                          lambda: _critic_shadow(db_path, now)),
        "partner_newsletters": ([("state.db", bool(db_path) and Path(db_path).is_file())],
                                lambda: _newsletter_senders(db_path)),
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
    log.info("product: %s", status["product"])
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
        lines.append(f"Visitors (Cloudflare): yesterday {_n(y['visitors'])}, last {days} days "
                     f"{_n(w['visitors'])}; page views {_n(w['page_views'])}")
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

    shadow = data.get("critic_shadow")
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

    lines += ["", "== Product (GA4 event sample, yesterday / window) =="]
    product = data.get("product")
    if product:
        lines += [f"product.{name}: " + _pair(_n, product["yesterday"][name], product["window"][name])
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
                  "Events: " + ", ".join(f"{e} {_pair(_n, y[e], w[e])}" for e in google.EVENTS),
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
            remaining = text
            code = 200
            while remaining:
                end = len(remaining) if len(remaining) <= TELEGRAM_MAX else \
                    remaining.rfind("\n", 0, TELEGRAM_MAX) + 1 or TELEGRAM_MAX
                code = send_admin_text(remaining[:end], http_client=client)
                if code != 200:
                    break
                remaining = remaining[end:]
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
        smtp_factory: Any = None, db_path=None) -> int:
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
        deliver(cfg, subject, text, client=client, smtp_factory=smtp_factory)
    finally:
        if http_client is None:
            client.close()
    return 1 if all(s == "error" for s in ran) else 0
