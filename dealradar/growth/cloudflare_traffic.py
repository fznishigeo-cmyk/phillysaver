"""Read-only zone traffic: daily unique visitors, page views and requests.

The window's visitors are the sum of daily uniques (a visitor seen on two
days counts twice), not distinct visitors across the whole window.
The token needs Zone:Read for lookup by name, plus Analytics:Read.
"""

import time
from datetime import date
from typing import Any

API = "https://api.cloudflare.com/client/v4"
QUERY = """
query Traffic($zone: String!, $start: Date!, $end: Date!, $days: Int!) {
  viewer {
    zones(filter: {zoneTag: $zone}) {
      httpRequests1dGroups(limit: $days, filter: {date_geq: $start, date_leq: $end}) {
        dimensions { date }
        uniq { uniques }
        sum { pageViews requests }
      }
    }
  }
}
"""


def stats(client: Any, token: str, domain: str, yesterday: date, start: date) -> dict:
    auth = {"Authorization": f"Bearer {token}"}
    resp = client.get(API + "/zones", headers=auth, params={"name": domain})
    resp.raise_for_status()
    body = resp.json()
    if not body.get("success"):
        raise RuntimeError("Cloudflare zone lookup failed")
    zone = next((z["id"] for z in body["result"] if z["name"] == domain), None)
    if zone is None:
        raise RuntimeError("Cloudflare zone not found")
    time.sleep(1)  # at most one request/second to this host
    resp = client.post(API + "/graphql", headers=auth, json={
        "query": QUERY, "variables": {"zone": zone, "start": start.isoformat(),
                                     "end": yesterday.isoformat(),
                                     "days": (yesterday - start).days + 1}})
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError("Cloudflare analytics failed")
    rows = body["data"]["viewer"]["zones"][0]["httpRequests1dGroups"]
    out = {key: {"visitors": 0, "page_views": 0, "requests": 0}
           for key in ("yesterday", "window")}
    for row in rows:
        day = date.fromisoformat(row["dimensions"]["date"])
        if not start <= day <= yesterday:
            continue
        counts = {"visitors": int(row["uniq"]["uniques"]),
                  "page_views": int(row["sum"]["pageViews"]),
                  "requests": int(row["sum"]["requests"])}
        for key in (("yesterday", "window") if day == yesterday else ("window",)):
            for metric, count in counts.items():
                out[key][metric] += count
    return out
