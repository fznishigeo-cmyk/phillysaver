"""Google Ads reporting and the guard's sole write operation: pause a campaign.

OAuth refresh-token flow (GOOGLE_ADS_CLIENT_ID, GOOGLE_ADS_CLIENT_SECRET,
GOOGLE_ADS_REFRESH_TOKEN, from the Cloud project that holds the API access
level). The manager account, when set, goes in the login-customer-id header.
Since 2026-09 the developer token is optional, so none is sent.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

log = logging.getLogger("dealradar.growth")

TOKEN_URL = "https://oauth2.googleapis.com/token"
SEARCH = "https://googleads.googleapis.com/{}/customers/{}/googleAds:search"
ACCESSIBLE = "https://googleads.googleapis.com/{}/customers:listAccessibleCustomers"
_CODE = re.compile(r"[A-Za-z_]{1,64}")
_WORDS = re.compile(r"[^A-Za-z ,.'()-]+")  # a message keeps words only: no digits, ids or tokens


class GoogleAdsError(Exception):
    """An HTTP error carrying only Google's error codes (enum names such as
    invalid_grant or USER_PERMISSION_DENIED): safe for the public logs."""


def _codes(resp: Any, *, codes_only: bool = False) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    err = body.get("error") if isinstance(body, dict) else None
    found = [err] if isinstance(err, str) else []  # OAuth: {"error": "invalid_grant"}
    if isinstance(err, dict):
        found.append(err.get("status") or "")
        note = " ".join(_WORDS.sub(" ", str(err.get("message") or "")).split())[:140]
        for detail in err.get("details") or []:
            for e in detail.get("errors") or []:
                found += [v for v in (e.get("errorCode") or {}).values() if isinstance(v, str)]
    codes = " ".join(dict.fromkeys(c for c in found if _CODE.fullmatch(c)))
    if isinstance(err, dict) and note and not codes_only:
        codes += f' "{note}"'
    return codes


def _check(resp: Any, context: str = "", *, codes_only: bool = False) -> None:
    if resp.status_code >= 400 or (codes_only and not 200 <= resp.status_code < 300):
        raise GoogleAdsError(" ".join(p for p in (f"HTTP {resp.status_code}", _codes(resp, codes_only=codes_only),
                                                  context.strip()) if p))


def _manager_accounts(client: Any, version: str, manager_id: str, headers: dict) -> str:
    """"; under the manager: ...7972 ENABLED, ..." (last four digits and
    status only), or "" when the manager cannot be asked."""
    try:
        resp = client.post(SEARCH.format(version, manager_id), headers=headers, json={
            "query": "SELECT customer_client.id, customer_client.status, customer_client.manager "
                     "FROM customer_client WHERE customer_client.level <= 1"})
        if resp.status_code != 200:
            return ""
        rows = resp.json().get("results") or []
    except Exception:  # a diagnostic only: never the reason the section fails
        return ""
    found = [f"...{str(c.get('id', ''))[-4:]} {c.get('status', '?')}"
             for r in rows for c in [r.get("customerClient") or {}] if not c.get("manager")]
    return "; under the manager: " + (", ".join(found[:6]) or "none")


def access_token(client: Any, client_id: str, client_secret: str, refresh_token: str,
                 *, codes_only: bool = False) -> str:
    resp = client.post(TOKEN_URL, data={"grant_type": "refresh_token", "client_id": client_id,
                                        "client_secret": client_secret,
                                        "refresh_token": refresh_token})
    _check(resp, codes_only=codes_only)
    return resp.json()["access_token"]


def insights(client: Any, token: str, version: str, customer_id: str, login_customer_id: str,
             yesterday: date, start: date) -> dict:
    """Spend, impressions, clicks and conversions for yesterday and
    start..yesterday (the ad account's time zone)."""
    headers = {"Authorization": f"Bearer {token}"}
    # Which accounts this sign-in reaches: yes/no only, so a failed search
    # says whether the account or the manager is the one missing. A version
    # Google does not serve this project answers 404 "Method not found":
    # the next older one is tried (at most three), and the log names the
    # one that answered so config can pin it.
    for _ in range(4):
        seen = client.get(ACCESSIBLE.format(version), headers=dict(headers))
        if seen.status_code != 404 or not re.fullmatch(r"v\d+", version) or version == "v1":
            break
        version = f"v{int(version[1:]) - 1}"
    _check(seen, f" (API {version})")
    log.info("google_ads: API %s", version)
    names = set(seen.json().get("resourceNames") or [])
    context = (f" (sign-in reaches {len(names)} account(s); ad account listed: "
               f"{'yes' if 'customers/' + customer_id in names else 'no'}; manager listed: "
               f"{'yes' if login_customer_id and 'customers/' + login_customer_id in names else 'no'})")
    if login_customer_id:
        headers["login-customer-id"] = login_customer_id
    query = ("SELECT segments.date, metrics.cost_micros, metrics.impressions, metrics.clicks, "
             "metrics.conversions FROM customer "
             f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{yesterday.isoformat()}'")
    rows: list[dict] = []
    page = ""
    while True:
        body = {"query": query, **({"pageToken": page} if page else {})}
        resp = client.post(SEARCH.format(version, customer_id), headers=headers, json=body)
        if resp.status_code >= 400:  # where the ad account sits: each reachable account's
            # last four digits and the accounts under it, by status
            parts = []
            for name in sorted(names)[:4]:
                cid = name.split("/")[-1]
                under = _manager_accounts(client, version, cid, {**headers, "login-customer-id": cid})
                parts.append(f"...{cid[-4:]}" + (under.replace("; under the manager:", " holds:")
                                                  if under else ""))
            context = context[:-1] + "; reachable: " + "; ".join(parts) + ")"
        _check(resp, context)
        doc = resp.json()
        rows += doc.get("results") or []
        page = doc.get("nextPageToken") or ""
        if not page:
            break

    def total(since: date) -> dict:
        picked = [r.get("metrics") or {} for r in rows
                  if (r.get("segments") or {}).get("date", "") >= since.isoformat()]
        return {"spend": sum(int(m.get("costMicros") or 0) for m in picked) / 1e6,
                "impressions": sum(int(m.get("impressions") or 0) for m in picked),
                "clicks": sum(int(m.get("clicks") or 0) for m in picked),
                "conversions": sum(float(m.get("conversions") or 0) for m in picked)}

    return {"yesterday": total(yesterday), "window": total(start)}


def nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise GoogleAdsError("INVALID_RESPONSE")
    return int(value)


class GuardAPI:
    """Explicit dates in the account timezone; paginated, uncached reads.

    No generic mutation interface: callers can only pause an existing campaign.
    """

    def __init__(self, client, cfg):
        self.client = client
        self.version = cfg.growth_google_ads_api_version
        self.customer = cfg.growth_google_ads_customer_id
        if not re.fullmatch(r"v[0-9]+", self.version) or not re.fullmatch(r"[0-9]+", self.customer):
            raise GoogleAdsError("INVALID_CONFIG")
        from ..config import get_secret
        credentials = [get_secret(name) for name in
                       ("GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN")]
        if not all(credentials):
            raise GoogleAdsError("MISSING_CREDENTIALS")
        self.headers = {"Authorization": "Bearer " + access_token(
            client, *credentials, codes_only=True)}
        if cfg.growth_google_ads_login_customer_id:
            self.headers["login-customer-id"] = cfg.growth_google_ads_login_customer_id

    def search(self, query):
        rows, page, seen = [], "", set()
        while True:
            resp = self.client.post(SEARCH.format(self.version, self.customer), headers=self.headers,
                json={"query": query, **({"pageToken": page} if page else {})})
            _check(resp, codes_only=True)
            body = resp.json()
            if not isinstance(body, dict) or not isinstance(body.get("results", []), list):
                raise GoogleAdsError("INVALID_RESPONSE")
            rows.extend(body.get("results", []))
            page = body.get("nextPageToken") or ""
            if not page:
                return rows
            if not isinstance(page, str) or page in seen:
                raise GoogleAdsError("INVALID_PAGINATION")
            seen.add(page)

    def account(self):
        rows = self.search("SELECT customer.time_zone, customer.currency_code FROM customer")
        if len(rows) != 1:
            raise GoogleAdsError("INVALID_RESPONSE")
        return rows[0]["customer"]

    def campaigns(self):
        # No metrics/date segment here: enabled campaigns with zero traffic must appear.
        rows = self.search("SELECT campaign.id, campaign.status FROM campaign "
                           "WHERE campaign.status != 'REMOVED'")
        out = {}
        for row in rows:
            c = row["campaign"]
            cid = str(nonnegative_int(c["id"]))
            if cid in out or c["status"] not in {"ENABLED", "PAUSED"}:
                raise GoogleAdsError("INVALID_RESPONSE")
            out[cid] = {"id": cid, "status": c["status"]}
        return out

    def budgets(self):
        rows = self.search("SELECT campaign.id, campaign.payment_mode, "
            "campaign_budget.amount_micros, campaign_budget.period FROM campaign "
            "WHERE campaign.status = 'ENABLED'")
        return {str(nonnegative_int(r["campaign"]["id"])): {
            "budget_micros": nonnegative_int(r["campaignBudget"].get("amountMicros", 0)),
            "period": r["campaignBudget"].get("period"),
            "payment_mode": r["campaign"].get("paymentMode"),
        } for r in rows}

    def costs(self, start: date, end: date, *, account=False):
        resource = "customer" if account else "campaign"
        fields = "" if account else "campaign.id, "
        rows = self.search(f"SELECT {fields}metrics.cost_micros, metrics.clicks FROM {resource} "
                           f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'")
        out = {}
        for row in rows:
            key = "account" if account else str(nonnegative_int(row["campaign"]["id"]))
            if key in out:
                raise GoogleAdsError("INVALID_RESPONSE")
            m = row["metrics"]
            out[key] = {"cost_micros": nonnegative_int(m.get("costMicros", 0)),
                        "clicks": nonnegative_int(m.get("clicks", 0))}
        return out

    def pause(self, campaign_id: str):
        if not re.fullmatch(r"[0-9]+", campaign_id):
            raise GoogleAdsError("INVALID_CAMPAIGN_ID")
        resource = f"customers/{self.customer}/campaigns/{campaign_id}"
        resp = self.client.post(
            f"https://googleads.googleapis.com/{self.version}/customers/{self.customer}/campaigns:mutate",
            headers=self.headers, json={"operations": [{"updateMask": "status", "update": {
                "resourceName": resource, "status": "PAUSED"}}]})
        _check(resp, codes_only=True)
        body = resp.json()
        if body.get("partialFailureError") or body.get("results") != [{"resourceName": resource}]:
            raise GoogleAdsError("PAUSE_NOT_CONFIRMED")
