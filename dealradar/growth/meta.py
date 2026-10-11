"""Meta Graph API (read-only): ad-account insights, Page and Instagram followers.

The system-user token travels in the Authorization header, never in a URL.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from urllib.parse import quote

GRAPH = "https://graph.facebook.com/{}/{}"
ACTIONS = ("complete_registration", "initiate_checkout", "start_trial")


def _action(actions: dict[str, float], name: str) -> int:
    # The aggregated type when Meta reports it, else the pixel's own; never both.
    for key in (name, "offsite_conversion.fb_pixel_" + name):
        if key in actions:
            return int(actions[key])
    return 0


def ad_insights(client: Any, token: str, version: str, account_id: str,
                yesterday: date, start: date) -> dict:
    """Spend, impressions, clicks, CTR (%), CPC and conversions for yesterday
    and start..yesterday (the ad account's time zone)."""
    out = {}
    for key, since in (("yesterday", yesterday), ("window", start)):
        resp = client.get(
            GRAPH.format(version, "act_" + quote(account_id, safe="")) + "/insights",
            headers={"Authorization": f"Bearer {token}"},
            params={"fields": "spend,impressions,clicks,ctr,cpc,actions",
                    "time_range": json.dumps({"since": since.isoformat(),
                                              "until": yesterday.isoformat()})})
        resp.raise_for_status()
        row = (resp.json().get("data") or [{}])[0]
        actions = {a.get("action_type"): float(a.get("value") or 0)
                   for a in row.get("actions") or []}
        out[key] = {"spend": float(row.get("spend") or 0.0),
                    "impressions": int(row.get("impressions") or 0),
                    "clicks": int(row.get("clicks") or 0),
                    "ctr": float(row.get("ctr") or 0.0),
                    "cpc": float(row.get("cpc") or 0.0),
                    **{name: _action(actions, name) for name in ACTIONS}}
    return out


def followers(client: Any, token: str, version: str, object_id: str) -> int:
    """followers_count of a Facebook Page or an Instagram professional account."""
    resp = client.get(GRAPH.format(version, quote(object_id, safe="")),
                      headers={"Authorization": f"Bearer {token}"},
                      params={"fields": "followers_count"})
    resp.raise_for_status()
    return int(resp.json().get("followers_count") or 0)
