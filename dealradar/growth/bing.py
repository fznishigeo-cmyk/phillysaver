"""Read-only Bing search traffic. The query-string API key must stay private.

API dates use Microsoft's /Date(milliseconds[timezone offset])/ format:
https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getrankandtrafficstats
"""

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

API = "https://ssl.bing.com/webmaster/api.svc/json/GetRankAndTrafficStats"
_DATE = re.compile(r"/Date\((-?\d+)([+-]\d{4})?\)/")


class BingError(Exception):
    """Only a status code, never a URL, API response or transport message."""


def _day(value: str) -> date:
    match = _DATE.fullmatch(value)
    if not match:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    stamp, offset = match.groups()
    tz = timezone.utc
    if offset:
        minutes = int(offset[1:3]) * 60 + int(offset[3:])
        tz = timezone(timedelta(minutes=minutes if offset[0] == "+" else -minutes))
    return datetime.fromtimestamp(int(stamp) / 1000, tz).date()


def stats(client: Any, key: str, domain: str, yesterday: date, start: date) -> dict:
    # httpx's INFO request line includes the full URL, including the API key.
    http_log = logging.getLogger("httpx")
    level = http_log.level
    http_log.setLevel(logging.WARNING)
    try:
        resp = client.get(API, params={"siteUrl": f"https://{domain}/", "apikey": key})
        if resp.status_code != 200:
            raise BingError(f"HTTP {resp.status_code}")
        rows = resp.json()["d"] or []
        out = {"yesterday": {"clicks": 0, "impressions": 0},
               "window": {"clicks": 0, "impressions": 0}, "has_data": False}
        for row in rows:
            day = _day(row["Date"])
            if not start <= day <= yesterday:
                continue
            out["has_data"] = True
            for period in (("yesterday", "window") if day == yesterday else ("window",)):
                out[period]["clicks"] += int(row["Clicks"])
                out[period]["impressions"] += int(row["Impressions"])
        return out
    except BingError:
        raise
    except Exception:
        raise BingError() from None
    finally:
        http_log.setLevel(level)
