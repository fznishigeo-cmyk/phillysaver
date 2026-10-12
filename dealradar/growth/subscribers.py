"""Read-only Join free counts. KV metadata holds ts; values are never fetched.

Key listings necessarily contain addresses and confirmation tokens. Consume
only timestamps, return only counts, and never log the response or key names.
"""

from datetime import datetime
from typing import Any
from urllib.parse import quote

API = "https://api.cloudflare.com/client/v4/accounts/{}/storage/kv/namespaces"
NAMES = ("wowpriced_subs", "wowpriced-subscribe-wowpriced_subs")


def counts(client: Any, token: str, account: str, yesterday: datetime, today: datetime) -> dict:
    base = API.format(quote(account, safe=""))
    auth = {"Authorization": f"Bearer {token}"}

    def read(url, params):
        resp = client.get(url, headers=auth, params=params)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success"):
            raise RuntimeError("Cloudflare API failed")
        return body

    page, namespace = 1, None
    while namespace is None:
        body = read(base, {"page": page, "per_page": 100})
        namespace = next((n["id"] for n in body["result"] if n["title"] in NAMES), None)
        info = body.get("result_info") or {}
        if namespace:
            break
        if "total_pages" in info:
            if page >= info["total_pages"]:
                break
        elif len(body["result"]) < 100:
            break
        page += 1
    if namespace is None:
        return {"unavailable": "KV namespace not found"}

    total, new, cursor = 0, 0, ""
    seen = set()
    while True:
        params = {"prefix": "sub:", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        body = read(base + "/" + quote(namespace, safe="") + "/keys", params)
        for key in body["result"]:
            total += 1
            stamp = (key.get("metadata") or {}).get("ts")
            try:
                ts = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if yesterday <= ts < today:
                    new += 1
            except (AttributeError, TypeError, ValueError):
                continue
        cursor = (body.get("result_info") or {}).get("cursor")
        if not cursor:
            break
        if cursor in seen:
            raise RuntimeError("Cloudflare pagination repeated")
        seen.add(cursor)
    return {"total": total, "yesterday": new}
