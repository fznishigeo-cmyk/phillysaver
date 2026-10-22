"""One-time builder for the first Search campaign, always created PAUSED.

The operator enables it in Google Ads. Nothing here enables a campaign,
changes an existing one, or raises a budget; the guard keeps watching it.
The only addition to an existing campaign is a missing ads.setup.ad_groups
entry, created PAUSED inside the same campaign and budget.
Settings live in config.yaml ads.setup. See docs/ads-guard.md.
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation

import httpx

from ..util import now_utc
from . import ads_guard, google_ads

log = logging.getLogger(__name__)
MUTATE = "https://googleads.googleapis.com/{}/customers/{}/googleAds:mutate"
US, ENGLISH = "geoTargetConstants/2840", "languageConstants/1000"


def plan(cfg, now) -> dict:
    """The checked ads.setup block; ValueError names the first problem."""
    raw = (cfg.raw.get("ads") or {}).get("setup") or {}
    try:
        budget = int(Decimal(str(raw.get("daily_budget_usd", 0))) * ads_guard.MICROS)
        cpc = int(Decimal(str(raw.get("max_cpc_usd", 0))) * ads_guard.MICROS)
    except InvalidOperation:
        raise ValueError("budget or max cpc is not a number") from None
    if not 0 < budget <= ads_guard.settings(cfg, now)["cap_micros"] // 2:
        raise ValueError("daily budget must be above 0 and at most half the authorized cap")
    if not 0 < cpc <= budget:
        raise ValueError("max cpc must be above 0 and at most the daily budget")
    name = str(raw.get("campaign_name") or "")
    if not re.fullmatch(r"[A-Za-z0-9 _-]{1,80}", name):
        raise ValueError("campaign name must be letters, digits, spaces, _ or -")
    p = {"name": name, "budget_micros": budget, "cpc_micros": cpc,
         "final_url": str(raw.get("final_url") or ""), "suffix": str(raw.get("final_url_suffix") or "")}
    if not p["final_url"].startswith("https://"):
        raise ValueError("final url must be https")
    p.update(_texts(raw, ("keywords", "negative_keywords", "headlines", "descriptions")))
    groups = raw.get("ad_groups") or []
    if not isinstance(groups, list) or len(groups) > 5:
        raise ValueError("ad_groups: a list of at most 5")
    p["ad_groups"] = []
    for g in groups:
        g = g if isinstance(g, dict) else {}
        group = {"name": str(g.get("name") or ""), "final_url": str(g.get("final_url") or "")}
        if not re.fullmatch(r"[A-Za-z0-9 _-]{1,80}", group["name"]):
            raise ValueError("ad group name must be letters, digits, spaces, _ or -")
        if group["name"] in {name + " ad group", *(x["name"] for x in p["ad_groups"])}:
            raise ValueError(f"ad group name used twice: {group['name']}")
        if not group["final_url"].startswith("https://"):
            raise ValueError("ad group final url must be https")
        group.update(_texts(g, ("keywords", "headlines", "descriptions")))
        p["ad_groups"].append(group)
    return p


def _texts(raw, keys) -> dict:
    out = {}
    for key, low, limit in (("keywords", 1, 80), ("negative_keywords", 0, 80),
                            ("headlines", 3, 30), ("descriptions", 2, 90)):
        if key not in keys:
            continue
        items = [str(t) for t in raw.get(key) or []]
        if len(items) < low or any(not t or len(t) > limit for t in items):
            raise ValueError(f"{key}: need {low}+ items of at most {limit} characters")
        out[key] = items
    # Amazon Associates bans bidding on Amazon terms (paid-search policy).
    if any("amazon" in t.lower() for t in out.get("keywords", [])):
        raise ValueError("keywords: no keyword may contain amazon")
    if len(out["headlines"]) > 15 or len(out["descriptions"]) > 4:
        raise ValueError("at most 15 headlines and 4 descriptions")
    return out


def operations(customer: str, p: dict) -> list:
    """One atomic mutate: budget, PAUSED campaign, targeting, ad group, keywords, ad."""
    c = f"customers/{customer}"
    budget, campaign, group = f"{c}/campaignBudgets/-1", f"{c}/campaigns/-2", f"{c}/adGroups/-3"

    def criterion(**fields):
        return {"campaignCriterionOperation": {"create": {"campaign": campaign, **fields}}}

    return [
        {"campaignBudgetOperation": {"create": {
            "resourceName": budget, "name": p["name"] + " budget", "amountMicros": str(p["budget_micros"]),
            "deliveryMethod": "STANDARD", "explicitlyShared": False}}},
        {"campaignOperation": {"create": {
            "resourceName": campaign, "name": p["name"], "status": "PAUSED",
            "advertisingChannelType": "SEARCH", "campaignBudget": budget,
            "targetSpend": {"cpcBidCeilingMicros": str(p["cpc_micros"])},  # Maximize clicks
            "networkSettings": {"targetGoogleSearch": True, "targetSearchNetwork": False,
                                "targetContentNetwork": False, "targetPartnerSearchNetwork": False},
            "geoTargetTypeSetting": {"positiveGeoTargetType": "PRESENCE",
                                     "negativeGeoTargetType": "PRESENCE"},
            "finalUrlSuffix": p["suffix"],
            "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"}}},
        criterion(location={"geoTargetConstant": US}),
        criterion(language={"languageConstant": ENGLISH}),
        *(criterion(negative=True, keyword={"text": t, "matchType": "BROAD"})
          for t in p["negative_keywords"]),
        *group_operations(campaign, group, {**p, "name": p["name"] + " ad group"}, "ENABLED"),
        *(op for i, g in enumerate(p["ad_groups"])
          for op in group_operations(campaign, f"{c}/adGroups/-{4 + i}", g, "PAUSED")),
    ]


def group_operations(campaign: str, group: str, g: dict, status: str) -> list:
    """An ad group with its phrase keywords and one responsive search ad."""
    return [
        {"adGroupOperation": {"create": {
            "resourceName": group, "name": g["name"], "campaign": campaign,
            "status": status, "type": "SEARCH_STANDARD"}}},
        *({"adGroupCriterionOperation": {"create": {
            "adGroup": group, "status": "ENABLED", "keyword": {"text": t, "matchType": "PHRASE"}}}}
          for t in g["keywords"]),
        {"adGroupAdOperation": {"create": {"adGroup": group, "status": "ENABLED", "ad": {
            "finalUrls": [g["final_url"]], "responsiveSearchAd": {
                "headlines": [{"text": t} for t in g["headlines"]],
                "descriptions": [{"text": t} for t in g["descriptions"]]}}}}},
    ]


def _mutate(client, url, headers, body, context) -> None:
    """Google's codes plus each error's field path, words and policy topic (no ids)."""
    resp = client.post(url, headers=headers, json=body)
    if 200 <= resp.status_code < 300:
        return
    found = []
    try:
        for detail in resp.json()["error"].get("details") or []:
            for e in detail.get("errors") or []:
                path = ".".join(str(p.get("fieldName", "")) for p in
                                (e.get("location") or {}).get("fieldPathElements") or [])
                topics = [str(t.get("topic", "")) for t in ((e.get("details") or {}).get(
                    "policyFindingDetails") or {}).get("policyTopicEntries") or []]
                note = " ".join(google_ads._WORDS.sub(" ", str(e.get("message") or "")).split())[:100]
                found.append(" ".join(x for x in (path, note, ",".join(topics)) if x))
    except (ValueError, KeyError, TypeError, AttributeError):
        pass
    try:
        google_ads._check(resp, context)
    except google_ads.GoogleAdsError as exc:
        raise google_ads.GoogleAdsError("; ".join([str(exc), *found[:8]])) from None


def _report(api) -> None:
    """Diagnostics only: billing and auto-apply recommendations (enum names, no ids)."""
    for label, query, field in (
            ("billing", "SELECT billing_setup.status FROM billing_setup", "billingSetup"),
            ("auto-apply on", "SELECT recommendation_subscription.type FROM recommendation_subscription "
                              "WHERE recommendation_subscription.status = 'ENABLED'",
             "recommendationSubscription")):
        try:
            found = sorted({str(r[field].get("status") or r[field].get("type")) for r in api.search(query)})
            log.info("ads_setup: %s: %s", label, ", ".join(found) or "none")
        except Exception as exc:
            log.warning("ads_setup: %s: unavailable (%s)", label, ads_guard._error(exc))


def setup(cfg, now, client, *, validate_only=False) -> int:
    try:
        p = plan(cfg, now)
    except ValueError as exc:
        log.error("ads_setup: %s; nothing created", exc)
        return 1
    try:
        api = google_ads.GuardAPI(client, cfg)
        found = api.search("SELECT campaign.resource_name, campaign.status FROM campaign WHERE campaign.name = "
                           f"'{p['name']}' AND campaign.status != 'REMOVED'")
        url = MUTATE.format(api.version, api.customer)
        if found:
            have = {r["adGroup"].get("name") for r in api.search(
                "SELECT ad_group.name FROM ad_group WHERE campaign.name = "
                f"'{p['name']}' AND ad_group.status != 'REMOVED'")}
            missing = [g for g in p["ad_groups"] if g["name"] not in have]
            if not missing:
                log.info("ads_setup: campaign already exists (%s); nothing created",
                         found[0]["campaign"].get("status"))
                _report(api)
                return 0
            # Same campaign, same budget: never touches the campaign, its budget or status.
            campaign = found[0]["campaign"]["resourceName"]
            ops = [op for i, g in enumerate(missing)
                   for op in group_operations(campaign, f"customers/{api.customer}/adGroups/-{1 + i}", g, "PAUSED")]
            _mutate(client, url, api.headers, {"mutateOperations": ops, "validateOnly": True}, "validate")
            if not validate_only:
                _mutate(client, url, api.headers, {"mutateOperations": ops}, "create")
            log.info("ads_setup: %s %d PAUSED ad group(s) in the existing campaign (%s): %s; "
                     "enable them in Google Ads", "validated (not added)" if validate_only else "added",
                     len(missing), found[0]["campaign"].get("status"), ", ".join(g["name"] for g in missing))
            _report(api)
            return 0
        body = {"mutateOperations": operations(api.customer, p)}
        _mutate(client, url, api.headers, {**body, "validateOnly": True}, "validate")
        if not validate_only:
            _mutate(client, url, api.headers, body, "create")
    except Exception as exc:
        log.error("ads_setup: failed, nothing created: %s", ads_guard._error(exc))
        return 1
    log.info("ads_setup: %s PAUSED campaign: %d keywords, %d negatives, 1 ad, %d paused extra ad group(s); "
             "enable it in Google Ads", "validated (not created)" if validate_only else "created",
             len(p["keywords"]), len(p["negative_keywords"]), len(p["ad_groups"]))
    _report(api)
    return 0


def run(cfg, *, now=None, http_client=None, validate_only=False) -> int:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    client = http_client or httpx.Client(timeout=30, follow_redirects=False)
    try:
        return setup(cfg, now or now_utc(), ads_guard.PacedClient(client, cfg),
                     validate_only=validate_only)
    finally:
        if http_client is None:
            client.close()
