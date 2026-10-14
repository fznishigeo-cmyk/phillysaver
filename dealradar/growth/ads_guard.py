"""Pause-only ad protection. Private facts stay in encrypted state and admin Telegram.

Official authenticated APIs, not crawling. No email, LLM, Keepa or campaign
creation. See docs/ads-guard.md for timing and attribution limits.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from .. import clicks
from ..config import get_secret
from ..db import DB
from ..publish.admin_alerts import send_admin_text
from ..util import now_utc
from . import google, google_ads

log = logging.getLogger(__name__)
STATE_KEY = "ads_guard"
MICROS = Decimal(1_000_000)


def _number(value):
    if isinstance(value, bool):
        raise ValueError("invalid number")
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("invalid number")
    return result


def settings(cfg, now):
    raw = cfg.raw.get("ads", {})
    raw = raw if isinstance(raw, dict) else {}
    warnings = []
    cap = Decimal(0)
    try:
        authorized = _number(raw.get("authorized_daily_usd", 0))
        hard = _number(raw.get("hard_max_daily_usd", 10))
        # The standing $10 ceiling cannot be lifted by a malformed/edited knob.
        if hard > 10:
            warnings.append("HARD MAX ABOVE $10: clamped to $10")
        hard = min(hard, Decimal(10))
        if authorized > hard:
            warnings.append("AUTHORIZATION ABOVE HARD MAX: clamped to hard maximum")
        cap = min(authorized, hard)
        if authorized:
            # The operator's agreed sentence (2026-10-14) may name who authorized it.
            quote = re.fullmatch(r"AUTHORIZE ADS: \$([0-9]+(?:\.[0-9]{1,2})?) per day"
                                 r"(?: — Authorized by [A-Z][A-Za-z .'-]{0,40})?",
                                 str(raw.get("authorized_quote", "")))
            on = date.fromisoformat(str(raw.get("authorized_on", "")))
            if not quote or _number(quote[1]) != authorized or on > now.date():
                raise ValueError("missing authorization")
    except (ValueError, TypeError, InvalidOperation):
        cap = Decimal(0)
        warnings.append("INVALID AUTHORIZATION: no spending allowed")
    performance = raw.get("performance", {})
    performance = performance if isinstance(performance, dict) else {}
    defaults = {"lookback_days": 30, "zero_signup_cost_usd": 15, "min_deal_clicks": 10,
                "cost_per_deal_click_usd": 1.5, "cost_per_click_min_spend_usd": 20}
    for key, default in defaults.items():
        try:
            defaults[key] = _number(performance.get(key, default))
            if defaults[key] <= 0:
                raise ValueError("invalid threshold")
        except (ValueError, TypeError, InvalidOperation):
            defaults[key] = Decimal(str(default))
            warnings.append("INVALID PERFORMANCE SETTING: using conservative default")
    defaults["lookback_days"] = max(1, min(90, int(defaults["lookback_days"])))
    return {"cap_micros": int((cap * MICROS).to_integral_value(rounding=ROUND_DOWN)),
            "authorized_on": str(raw.get("authorized_on") or ""),
            "authorized_quote": str(raw.get("authorized_quote") or ""),
            "warnings": warnings, **defaults}


class PacedClient:
    """Share one 1 req/s/host clock across OAuth, Ads, GA4 and Telegram.

    A failed request is retried by the next guard run, never in a mutate loop.
    """
    def __init__(self, client, cfg, *, clock=time.monotonic, sleep=time.sleep):
        self.client, self.cfg, self.clock, self.sleep = client, cfg, clock, sleep
        self.last = {}

    def post(self, url, **kwargs):
        host = urlsplit(url).hostname
        delay = self.last.get(host, -float("inf")) + 1 - self.clock()
        if delay > 0:
            self.sleep(delay)
        self.last[host] = self.clock()
        kwargs["headers"] = {"User-Agent": self.cfg.resolved_user_agent(), **kwargs.get("headers", {})}
        return self.client.post(url, **kwargs)


def _error(exc):
    # Only our Ads error enums/status are safe; other exception text may carry secrets.
    return str(exc) if isinstance(exc, google_ads.GoogleAdsError) else type(exc).__name__


def _manual(cfg):
    return (f"Manual steps: open https://ads.google.com/ and select account "
            f"{cfg.growth_google_ads_customer_id}. Go to Campaigns > Campaigns; "
            "filter Status = Enabled; select ALL campaigns; Edit > Pause; verify every "
            "status is Paused. Check today's Cost in the account timezone. Keep campaigns "
            "paused until authorization, budgets and guard access are verified.")


def _alert(client, text):
    if not (get_secret("TELEGRAM_BOT_TOKEN") and get_secret("TELEGRAM_ADMIN_CHAT_ID")):
        log.error("ads_guard: admin alert unavailable (missing credentials)")
        return False
    try:
        # No parse_mode, channel ID, response body or message contents in public logs.
        for start in range(0, len(text), 3800):
            if send_admin_text(text[start:start + 3800], http_client=client) != 200:
                log.error("ads_guard: admin alert failed")
                return False
        return True
    except Exception:
        log.error("ads_guard: admin alert failed")
        return False


def performance_reasons(cost_micros, outcomes, opt):
    cost = Decimal(cost_micros) / MICROS
    if outcomes is None:
        # Missing measurement must not permit unbounded unmeasured spending.
        floor = min(opt["zero_signup_cost_usd"], opt["cost_per_click_min_spend_usd"])
        return ["performance outcomes unavailable at spend threshold"] if cost >= floor else []
    outbound, signups = outcomes["deal_click"], outcomes["sign_up"]
    reasons = []
    if cost >= opt["zero_signup_cost_usd"] and signups == 0 and outbound < opt["min_deal_clicks"]:
        reasons.append("zero sign-ups and insufficient Get deal clicks")
    if cost >= opt["cost_per_click_min_spend_usd"] and (
            not outbound or cost > opt["cost_per_deal_click_usd"] * outbound):
        reasons.append("cost per Get deal click exceeds threshold")
    return reasons


def _click_counts(db, cfg, start, end, tz):
    if not clicks.enabled(cfg):
        return {"status": "off"}
    if db is None:
        return {"status": "unavailable"}
    first = int(datetime.combine(start, datetime.min.time(), tz).timestamp() // 3600)
    last = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), tz).timestamp() // 3600)
    coverage = db.conn.execute("SELECT COUNT(*), COALESCE(SUM(capped),0) "
        "FROM deal_click_coverage WHERE hour>=? AND hour<?", (first, last)).fetchone()
    count = db.conn.execute("SELECT COALESCE(SUM(count),0) FROM deal_clicks WHERE hour>=? AND hour<?",
                           (first, last)).fetchone()[0]
    return {"status": "unattributed", "observed_clicks": count if coverage[0] else None,
            "covered_hours": coverage[0], "expected_hours": last - first, "capped_hours": coverage[1]}


def check(db, cfg, now, client, *, run_key=None):
    """One guard pass. A red result never blocks attempts to pause other campaigns."""
    opt = settings(cfg, now)
    run_key = run_key or now.isoformat()
    errors, reasons, campaigns = [], [], {}
    if not (get_secret("TELEGRAM_BOT_TOKEN") and get_secret("TELEGRAM_ADMIN_CHAT_ID")):
        errors.append("admin Telegram credentials unavailable")
    state = {"pauses": [], "samples": []}
    try:
        if db is None:
            raise ValueError("state unavailable")
        stored = json.loads(db.meta_get(STATE_KEY) or "{}")
        if not isinstance(stored, dict) or any(not isinstance(stored.get(k, []), list)
                                             for k in ("pauses", "samples")):
            raise ValueError("invalid state")
        for event in stored.get("pauses", []):
            if (not isinstance(event, dict) or not {"campaign_id", "confirmed", "notified", "key", "at",
                                                   "day", "reasons"} <= event.keys()
                    or not isinstance(event["reasons"], list)):
                raise ValueError("invalid pause state")
        if any(not isinstance(s, dict) or not {"key", "at"} <= s.keys() for s in stored.get("samples", [])):
            raise ValueError("invalid sample state")
        state.update(stored)
    except Exception:
        errors.append("state unavailable")
        reasons.append("state unavailable")

    def save():
        if db is None:
            return
        try:
            db.meta_set(STATE_KEY, json.dumps(state, separators=(",", ":")))
        except Exception:
            if "state write failed" not in errors:
                errors.append("state write failed")
            if "state write failed" not in reasons:
                reasons.append("state write failed")

    snapshot = {"at": now.isoformat(), "cap_micros": opt["cap_micros"],
                "authorized_on": opt["authorized_on"], "authorized_quote": opt["authorized_quote"],
                "warnings": opt["warnings"], "errors": errors, "campaigns": [],
                "today_cost_micros": None, "timezone": None, "day": None,
                "click_signal": {"status": "unavailable"}}
    if not opt["cap_micros"]:
        reasons.append("no spending authorization (effective cap $0)")
    api = None

    def pause_enabled():
        # Journal before the write. A later status read can confirm an ambiguous
        # timeout. Already-paused campaigns are never written or enabled.
        for c in campaigns.values():
            for event in state["pauses"]:
                if event["campaign_id"] == c["id"] and not event["confirmed"] and c["status"] == "PAUSED":
                    event["confirmed"] = True
            if c["status"] != "ENABLED" or not reasons:
                continue
            key = f"{run_key}:{c['id']}"
            event = next((e for e in state["pauses"] if e["key"] == key or (
                e["campaign_id"] == c["id"] and not e["confirmed"])), None)
            if event is None:
                event = {"key": key, "campaign_id": c["id"], "at": now.isoformat(),
                         "day": snapshot["day"], "reasons": list(dict.fromkeys(reasons)),
                         "confirmed": False, "notified": False}
                state["pauses"].append(event)
            # No in-run retry after a failed/ambiguous mutation.
            if c["id"] in attempted:
                continue
            if not event["confirmed"]:
                # Retry one unresolved intent, not a new receipt for every failure.
                event.update(key=key, at=now.isoformat(), day=snapshot["day"],
                             reasons=list(dict.fromkeys(reasons)))
            attempted.add(c["id"])
            save()
            try:
                api.pause(c["id"])
                event["confirmed"] = True
                c["status"] = "PAUSED"
            except Exception as exc:
                errors.append(f"campaign {c['id']}: pause failed: " + _error(exc))
            save()

    attempted = set()
    state["latest"] = snapshot
    save()
    try:
        api = google_ads.GuardAPI(client, cfg)
        campaigns = api.campaigns()
    except Exception as exc:
        errors.append("campaign read failed: " + _error(exc))

    if api is not None:
        # Read failures independently: a spend/budget failure still leaves IDs to pause.
        try:
            account = api.account()
            tz = ZoneInfo(account["timeZone"])
            today = now.astimezone(tz).date()
            snapshot.update(timezone=account["timeZone"], day=today.isoformat())
            if account["currencyCode"] != "USD":
                raise google_ads.GoogleAdsError("NON_USD_ACCOUNT")
            total = api.costs(today, today, account=True)
            snapshot["today_cost_micros"] = total.get("account", {}).get("cost_micros", 0)
            if snapshot["today_cost_micros"] * 5 >= opt["cap_micros"] * 4:
                reasons.append("today's account spend reached 80% of effective cap")
            daily = api.costs(today, today)
            for cid, c in campaigns.items():
                c["today_cost_micros"] = daily.get(cid, {}).get("cost_micros", 0)
            budgets = api.budgets()
            for cid, c in campaigns.items():
                if c["status"] != "ENABLED":
                    continue
                c.update(budgets[cid])
                if (c["period"] != "DAILY" or c["payment_mode"] != "CLICKS"
                        or c["budget_micros"] <= 0):
                    reasons.append(f"campaign {cid}: unsupported daily spending limit")
                if c["budget_micros"] * 2 > opt["cap_micros"]:
                    reasons.append(f"campaign {cid}: daily budget exceeds cap/2")
            enabled = [c for c in campaigns.values() if c["status"] == "ENABLED"]
            if sum(c["budget_micros"] for c in enabled) * 2 > opt["cap_micros"]:
                reasons.append("combined enabled campaign budgets exceed cap/2")
            # Do not let optional analytics delay an already-required money stop.
            pause_enabled()
            start, end = today - timedelta(days=opt["lookback_days"]), today - timedelta(days=1)
            snapshot.update(performance_start=start.isoformat(), performance_end=end.isoformat())
            history = api.costs(start, end)
            snapshot["click_signal"] = _click_counts(db, cfg, start, end, tz)
            outcomes = {}
            if campaigns:
                try:
                    if not (get_secret("GOOGLE_SA_JSON") and cfg.growth_ga4_property_id):
                        raise ValueError("outcomes not configured")
                    outcomes = google.ga4_ads_outcomes(client,
                        google.access_token(client, get_secret("GOOGLE_SA_JSON")),
                        cfg.growth_ga4_property_id, start, end, account["timeZone"])
                except Exception as exc:
                    snapshot["outcomes_unavailable"] = type(exc).__name__
            for cid, c in campaigns.items():
                c.update(history.get(cid, {"cost_micros": 0, "clicks": 0}))
                matches = [outcomes[k] for k in ((api.customer, cid), ("utm", cid)) if k in outcomes]
                observed = {e: sum(r[e] for r in matches) for e in ("session_start", "deal_click", "sign_up")}
                c["outcomes"] = observed if observed["session_start"] > 0 else None
                c["performance_reasons"] = performance_reasons(c["cost_micros"], c["outcomes"], opt)
                if c["status"] == "ENABLED":
                    reasons.extend(f"campaign {cid}: {r}" for r in c["performance_reasons"])
        except Exception as exc:
            errors.append("spend/budget read failed: " + _error(exc))
            reasons.append("spend/budget read unavailable")
    if errors:
        reasons.append("guard could not verify safe spending")

    pause_enabled()

    for event in state["pauses"]:
        if event["confirmed"] and not event["notified"]:
            event["notified"] = _alert(client,
                f"Ads guard: campaign {event['campaign_id']} PAUSED.\n"
                + "\n".join(event["reasons"]) + "\n" + _manual(cfg))
            if not event["notified"]:
                errors.append("pause notification pending")
            save()
    if errors or opt["warnings"]:
        if not _alert(client, "Ads guard: ACTION REQUIRED\n" + "\n".join(opt["warnings"] + errors)
                      + "\n" + _manual(cfg)):
            errors.append("admin notification failed")

    snapshot["campaigns"] = list(campaigns.values())
    snapshot["reasons"] = list(dict.fromkeys(reasons))
    # Same run key replaces its shadow sample; bounded private history, no API bodies.
    cutoff = (now - timedelta(days=2)).isoformat()
    state["samples"] = [s for s in state["samples"] if s["key"] != run_key and s["at"] >= cutoff]
    state["samples"].append({"key": run_key, **snapshot})
    state["samples"] = state["samples"][-192:]
    cutoff = (now - timedelta(days=31)).isoformat()
    state["pauses"] = [e for e in state["pauses"] if e["at"] >= cutoff or not e["notified"]]
    save()
    log.info("ads_guard: %s", "error" if errors else "ok")
    return 1 if errors else 0


def report_data(db, cfg, now):
    state = json.loads(db.meta_get(STATE_KEY) or "{}")
    latest = state.get("latest")
    opt = settings(cfg, now)
    result = {"current_cap_micros": opt["cap_micros"], "authorized_on": opt["authorized_on"],
              "authorized_quote": opt["authorized_quote"], "warnings": opt["warnings"],
              "latest": latest, "pauses_today": []}
    if latest and latest.get("timezone"):
        today = now.astimezone(ZoneInfo(latest["timezone"])).date().isoformat()
        result["pauses_today"] = [e for e in state.get("pauses", []) if e["confirmed"] and
            datetime.fromisoformat(e["at"]).astimezone(ZoneInfo(latest["timezone"])).date().isoformat() == today]
        result["stale"] = latest["day"] != today or (
            now - datetime.fromisoformat(latest["at"])).total_seconds() > 3600
    return result


def report_lines(data):
    lines = ["== Ads guard =="]
    if not data:
        return lines + ["Guard state unavailable; spending safety is not verified."]
    lines += [f"Effective cap: ${data['current_cap_micros'] / 1e6:.2f}/day",
              f"Authorization date: {data['authorized_on'] or 'none'}",
              f"Exact authorization: {data['authorized_quote'] or 'none'}", *data["warnings"]]
    latest = data.get("latest")
    if not latest:
        return lines + ["Guard has not run; spending safety is not verified."]
    lines += [f"Last check: {latest['at']} ({latest['timezone'] or 'timezone unavailable'})"]
    if data.get("stale"):
        lines.append("STALE guard snapshot: today's live spend is unavailable.")
    spend = latest["today_cost_micros"]
    lines.append(f"Spend on {latest['day'] or 'unknown day'}: "
                 + (f"${spend / 1e6:.2f}" if spend is not None else "unavailable"))
    lines += latest["errors"]
    lines.append(f"Performance window: {latest.get('performance_start', '?')} to "
                 f"{latest.get('performance_end', '?')} (completed account days)")
    for c in latest["campaigns"]:
        budget = c.get("budget_micros")
        cost = c.get("cost_micros")
        outcome = c.get("outcomes")
        lines.append(f"Campaign {c['id']} ({c['status']}): daily budget "
            + (f"${budget / 1e6:.2f}" if budget is not None else "unavailable (not enabled/read failed)")
            + "; today " + (f"${c['today_cost_micros'] / 1e6:.2f}" if "today_cost_micros" in c else "unavailable")
            + "; window cost " + (f"${cost / 1e6:.2f}" if cost is not None else "unavailable")
            + f"; ad clicks {c.get('clicks', 'unavailable')}; "
            + (f"Get deal {outcome['deal_click']}; sign-ups {outcome['sign_up']}" if outcome else "outcomes unavailable"))
        if outcome and cost is not None:
            lines.append("  Cost per Get deal: " + (f"${cost / 1e6 / outcome['deal_click']:.2f}"
                if outcome["deal_click"] else "no measured outbound clicks"))
        lines.extend("  " + r for r in c.get("performance_reasons", []))
    signal = latest["click_signal"]
    lines.append("Click signal: " + signal["status"] + (f"; observed site clicks {signal['observed_clicks']}; "
        f"coverage {signal['covered_hours']}/{signal['expected_hours']} hours; never campaign credit"
        if signal["status"] == "unattributed" else ""))
    lines.append("Campaigns paused today:")
    lines.extend(f"  {e['campaign_id']}: " + "; ".join(e["reasons"]) for e in data["pauses_today"])
    if not data["pauses_today"]:
        lines.append("  none recorded")
    return lines


def run(cfg, *, db_path, now=None, http_client=None, state_unavailable=False):
    now = now or now_utc()
    # httpx INFO logs include URLs (Telegram embeds a secret in its path).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    db = None
    client = http_client or httpx.Client(timeout=30, follow_redirects=False)
    try:
        if not state_unavailable:
            try:
                db = DB(db_path)
            except Exception:
                log.error("ads_guard: state unavailable")
        return check(db, cfg, now, PacedClient(client, cfg),
                     run_key=os.environ.get("DEALRADAR_RUN_KEY") or now.isoformat())
    finally:
        if db is not None:
            db.close()
        if http_client is None:
            client.close()
