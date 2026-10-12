"""Google Ads API (read-only): spend, impressions, clicks and conversions.

OAuth refresh-token flow (GOOGLE_ADS_CLIENT_ID, GOOGLE_ADS_CLIENT_SECRET,
GOOGLE_ADS_REFRESH_TOKEN, from the Cloud project that holds the API access
level). The manager account, when set, goes in the login-customer-id header.
Since 2026-09 the developer token is optional, so none is sent.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

TOKEN_URL = "https://oauth2.googleapis.com/token"
SEARCH = "https://googleads.googleapis.com/{}/customers/{}/googleAds:search"
_CODE = re.compile(r"[A-Za-z_]{1,64}")


class GoogleAdsError(Exception):
    """An HTTP error carrying only Google's error codes (enum names such as
    invalid_grant or USER_PERMISSION_DENIED): safe for the public logs."""


def _codes(resp: Any) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    err = body.get("error") if isinstance(body, dict) else None
    found = [err] if isinstance(err, str) else []  # OAuth: {"error": "invalid_grant"}
    if isinstance(err, dict):
        found.append(err.get("status") or "")
        for detail in err.get("details") or []:
            for e in detail.get("errors") or []:
                found += [v for v in (e.get("errorCode") or {}).values() if isinstance(v, str)]
    return " ".join(dict.fromkeys(c for c in found if _CODE.fullmatch(c)))


def _check(resp: Any) -> None:
    if resp.status_code >= 400:
        raise GoogleAdsError(f"HTTP {resp.status_code} {_codes(resp)}".strip())


def access_token(client: Any, client_id: str, client_secret: str, refresh_token: str) -> str:
    resp = client.post(TOKEN_URL, data={"grant_type": "refresh_token", "client_id": client_id,
                                        "client_secret": client_secret,
                                        "refresh_token": refresh_token})
    _check(resp)
    return resp.json()["access_token"]


def insights(client: Any, token: str, version: str, customer_id: str, login_customer_id: str,
             yesterday: date, start: date) -> dict:
    """Spend, impressions, clicks and conversions for yesterday and
    start..yesterday (the ad account's time zone)."""
    headers = {"Authorization": f"Bearer {token}"}
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
        _check(resp)
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
