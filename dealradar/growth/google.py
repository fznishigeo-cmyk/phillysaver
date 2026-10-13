"""Google: service-account token, GA4 Data API, Search Console, sitemap.

The token is the OAuth2 JWT-bearer flow (RS256). The JWT is signed by
`openssl dgst -sha256 -sign` (present on the runners) with the private key
in a 0600 temp file that is removed at once: no crypto dependency.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import date
from typing import Any
from urllib.parse import quote

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = ("https://www.googleapis.com/auth/analytics.readonly",
          "https://www.googleapis.com/auth/webmasters")
GA4_URL = "https://analyticsdata.googleapis.com/v1beta/properties/{}:batchRunReports"
SC_SITES = "https://searchconsole.googleapis.com/webmasters/v3/sites/"
PRODUCT_EVENTS = {"home_load_attempts": "home_load_attempt", "first_load_failures": "first_load_failed",
                  "static_fallback_shown": "static_fallback_shown", "first_load_recoveries": "first_load_recovered",
                  "install_offer_shown": "install_offer_shown", "install_accepted": "install_accepted",
                  "standalone_session": "standalone_session"}
EVENTS = ("sign_up", "begin_checkout", "start_trial", "deal_click", "support_click", *PRODUCT_EVENTS.values())
SAVED_EVENTS = ("deal_save", "saved_view", "saved_deal_click")
DEAL_PAGE_COUNTS = ("deal_detail_views", "deal_detail_outbound_clicks", "deal_code_copies")
STORE_REGISTRATION = ("Register the GA4 custom dimension: Admin → Custom definitions → Create, "
                      "name store, scope Event, parameter store")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64json(obj: dict) -> str:
    return _b64url(json.dumps(obj, separators=(",", ":")).encode())


def rs256_sign(data: bytes, private_key_pem: str) -> bytes:
    fd, path = tempfile.mkstemp(suffix=".pem")  # created 0600
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(private_key_pem)
        return subprocess.run(["openssl", "dgst", "-sha256", "-sign", path],
                              input=data, capture_output=True, check=True,
                              timeout=30).stdout
    finally:
        os.unlink(path)


def build_jwt(sa: dict, now: int) -> str:
    header = {"alg": "RS256", "typ": "JWT"}
    if sa.get("private_key_id"):
        header["kid"] = sa["private_key_id"]
    claims = {"iss": sa["client_email"], "scope": " ".join(SCOPES),
              "aud": TOKEN_URL, "iat": now, "exp": now + 3600}
    signing_input = f"{_b64json(header)}.{_b64json(claims)}"
    return signing_input + "." + _b64url(rs256_sign(signing_input.encode(),
                                                     sa["private_key"]))


class ServiceAccountError(ValueError):
    """Invalid service-account JSON; the message contains only its shape."""


def load_service_account(sa_json: str) -> dict:
    text = sa_json.strip().removeprefix("\ufeff").strip()
    message = ("GOOGLE_SA_JSON must be a JSON object with client_email and private_key "
               f"(character count: {len(text)}, starts with {{: {text.startswith('{')}, "
               f"ends with .json: {text.endswith('.json')})")
    try:
        sa = json.loads(text)
    except json.JSONDecodeError:
        raise ServiceAccountError(message) from None
    if not isinstance(sa, dict) or "client_email" not in sa or "private_key" not in sa:
        raise ServiceAccountError(message)
    return sa


def access_token(client: Any, sa_json: str) -> str:
    jwt = build_jwt(load_service_account(sa_json), int(time.time()))
    resp = client.post(TOKEN_URL, data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": jwt})
    resp.raise_for_status()
    return resp.json()["access_token"]


def _rows(report: dict):
    """(dimension name -> value, [metric values]) per row."""
    dims = [h.get("name") for h in report.get("dimensionHeaders") or []]
    for row in report.get("rows") or []:
        values = [v.get("value", "") for v in row.get("dimensionValues") or []]
        yield (dict(zip(dims, values)),
               [int(float(m.get("value") or 0)) for m in row.get("metricValues") or []])


def ga4(client: Any, token: str, property_id: str, days: int) -> dict:
    """Totals and event counts for yesterday and the last `days` days (GA4
    relative dates, the property's time zone), top 5 sources and landing
    pages over the window."""
    ranges = [{"startDate": "yesterday", "endDate": "yesterday", "name": "yesterday"},
              {"startDate": f"{days}daysAgo", "endDate": "yesterday", "name": "window"}]

    def top(dim: str) -> dict:
        return {"dateRanges": ranges[1:], "dimensions": [{"name": dim}],
                "metrics": [{"name": "sessions"}], "limit": 5,
                "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}]}

    body = {"requests": [
        {"dateRanges": ranges,
         "metrics": [{"name": m} for m in ("sessions", "totalUsers", "newUsers")]},
        {"dateRanges": ranges, "dimensions": [{"name": "eventName"}],
         "metrics": [{"name": "eventCount"}],
         "dimensionFilter": {"filter": {"fieldName": "eventName",
                                        "inListFilter": {"values": list(EVENTS)}}}},
        top("sessionSourceMedium"), top("landingPage")]}
    resp = client.post(GA4_URL.format(quote(property_id, safe="")), json=body,
                       headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    totals, events, sources, pages = resp.json()["reports"]

    out: dict[str, Any] = {}
    for key in ("yesterday", "window"):
        out[key] = {"sessions": 0, "totalUsers": 0, "newUsers": 0,
                    **{e: 0 for e in EVENTS}}
    for dims, (sessions, users, new) in _rows(totals):
        if dims.get("dateRange") in out:
            out[dims["dateRange"]].update(sessions=sessions, totalUsers=users, newUsers=new)
    for dims, (count,) in _rows(events):
        if dims.get("dateRange") in out and dims.get("eventName") in EVENTS:
            out[dims["dateRange"]][dims["eventName"]] = count
    out["sources"] = [(d.get("sessionSourceMedium", ""), m[0]) for d, m in _rows(sources)]
    out["pages"] = [(d.get("landingPage", ""), m[0]) for d, m in _rows(pages)]
    return out


def ga4_stores(client: Any, token: str, property_id: str, days: int) -> dict:
    """Top ten stores and all-store totals, isolated from the main GA4 query."""
    requests = []
    for start in ("yesterday", f"{days}daysAgo"):
        requests.append({
            "dateRanges": [{"startDate": start, "endDate": "yesterday"}],
            "dimensions": [{"name": "customEvent:store"}],
            "metrics": [{"name": "eventCount"}],
            "dimensionFilter": {"filter": {"fieldName": "eventName",
                "stringFilter": {"matchType": "EXACT", "value": "deal_click"}}},
            "metricAggregations": ["TOTAL"], "limit": 10,
            "orderBys": [{"metric": {"metricName": "eventCount"}, "desc": True}],
        })
    resp = client.post(GA4_URL.format(quote(property_id, safe="")),
                       json={"requests": requests},
                       headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 400:
        message = str(resp.json().get("error", {}).get("message", "")).lower()
        if "customevent:store" in message and any(
                hint in message for hint in ("not a valid dimension", "unknown", "not registered",
                                              "unrecognized", "not found")):
            return {"unavailable": STORE_REGISTRATION}
    resp.raise_for_status()
    out = {}
    for key, result in zip(("yesterday", "window"), resp.json()["reports"]):
        totals = result.get("totals") or []
        out[key] = {
            "total": int(float(totals[0]["metricValues"][0]["value"])) if totals else 0,
            "stores": [(d.get("customEvent:store", ""), m[0]) for d, m in _rows(result)],
        }
    return out


def ga4_saved(client: Any, token: str, property_id: str, days: int) -> dict:
    """Observed feature actions and seven-day visitor-classified sessions.

    A missing event or visitor classification is unavailable, not an
    inferred zero. This measures return visits, never a purchase cohort.
    """
    url = GA4_URL.format(quote(property_id, safe=""))
    headers = {"Authorization": f"Bearer {token}"}
    ranges = [{"startDate": "yesterday", "endDate": "yesterday", "name": "yesterday"},
              {"startDate": f"{days}daysAgo", "endDate": "yesterday", "name": "window"}]
    resp = client.post(url, headers=headers, json={"requests": [{
        "dateRanges": ranges, "dimensions": [{"name": "eventName"}],
        "metrics": [{"name": "eventCount"}],
        "dimensionFilter": {"filter": {"fieldName": "eventName",
            "inListFilter": {"values": list(SAVED_EVENTS)}}}}]})
    resp.raise_for_status()
    out = {key: dict.fromkeys(SAVED_EVENTS) for key in ("yesterday", "window")}
    results = resp.json().get("reports") or []
    for dims, values in _rows(results[0] if results else {}):
        key, event = dims.get("dateRange"), dims.get("eventName")
        if key in out and event in SAVED_EVENTS and len(values) == 1 and values[0] >= 0:
            out[key][event] = values[0]

    # Isolate this dimension so missing classification never hides action counts.
    out["returning"] = {"unavailable": "visitor classification not returned"}
    try:
        resp = client.post(url, headers=headers, json={"requests": [{
            "dateRanges": [{"startDate": "7daysAgo", "endDate": "yesterday"}],
            "dimensions": [{"name": "newVsReturning"}],
            "metrics": [{"name": "sessions"}], "limit": 10}]})
        resp.raise_for_status()
        results = resp.json().get("reports") or []
        result = results[0] if results else {}
        classified, returning, unclassified = 0, 0, 0
        for dims, values in _rows(result):
            if "newVsReturning" not in dims or len(values) != 1 or values[0] < 0:
                continue
            kind, count = dims["newVsReturning"], values[0]
            if kind in ("new", "returning"):
                classified += count
                if kind == "returning":
                    returning += count
            else:
                unclassified += count
        if classified:
            out["returning"] = {"sessions": returning, "classified_sessions": classified,
                                "unclassified_sessions": unclassified}
    except Exception as e:
        # Responses may echo a token; only the class is safe to report.
        out["returning"] = {"unavailable": f"visitor classification unavailable ({type(e).__name__})"}
    return out


def _page_family(path: str) -> str:
    if path in ("/", "/index.html"):
        return "home"
    if re.fullmatch(r"/deal/[^/]+/", path):
        return "deal"
    if re.fullmatch(r"/stores/[^/]+/", path):
        return "store"
    if re.fullmatch(r"/deals/under-(10|25|50)/", path):
        return "price"
    if re.fullmatch(r"/deals/[^/]+/", path):
        return "category"
    return "best_today" if path == "/best-deals-today/" else ""


def ga4_deal_pages(client: Any, token: str, property_id: str, days: int) -> dict:
    """Observed actions by standard device/path dimensions, no console setup.

    Aggregate paths into bounded families before returning them. No rows,
    thresholded data or sampling means unavailable, never an invented zero.
    """
    ranges = [{"startDate": "yesterday", "endDate": "yesterday", "name": "yesterday"},
              {"startDate": f"{days}daysAgo", "endDate": "yesterday", "name": "window"}]
    request = {"dateRanges": ranges,
               "dimensions": [{"name": dim} for dim in ("eventName", "deviceCategory", "pagePath")],
               "metrics": [{"name": "eventCount"}], "limit": 10000,
               "dimensionFilter": {"filter": {"fieldName": "eventName", "inListFilter": {
                   "values": ["deal_detail_view", "deal_click", "deal_code_copy", "page_view"]}}}}
    out = {key: {**dict.fromkeys(DEAL_PAGE_COUNTS), "by_device_page": []}
           for key in ("yesterday", "window")}
    groups: dict[str, dict[tuple[str, str], dict]] = {key: {} for key in out}
    offset = 0
    while True:
        resp = client.post(GA4_URL.format(quote(property_id, safe="")),
                           json={"requests": [{**request, "offset": offset}]},
                           headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        reports = resp.json().get("reports") or []
        result = reports[0] if reports else {}
        metadata = result.get("metadata") or {}
        if metadata.get("subjectToThresholding") or metadata.get("samplingMetadatas") \
                or metadata.get("dataLossFromOtherRow"):
            return {"unavailable": "GA4 detail sample is thresholded, sampled or incomplete"}
        for dims, values in _rows(result):
            period, event = dims.get("dateRange"), dims.get("eventName")
            family = _page_family(dims.get("pagePath", ""))
            if period not in out or not family or len(values) != 1 or values[0] < 0:
                continue
            count = values[0]
            device = dims.get("deviceCategory")
            device = device if device in ("mobile", "desktop", "tablet") else "unknown"
            metric = "views" if event == ("deal_detail_view" if family == "deal" else "page_view") else \
                "outbound_clicks" if event == "deal_click" else "code_copies" if event == "deal_code_copy" else ""
            if not metric:
                continue
            row = groups[period].setdefault((device, family),
                {"device": device, "family": family, "views": None, "outbound_clicks": None, "code_copies": None})
            row[metric] = (row[metric] or 0) + count
            name = "deal_code_copies" if metric == "code_copies" else \
                "deal_detail_views" if family == "deal" and metric == "views" else \
                "deal_detail_outbound_clicks" if family == "deal" and metric == "outbound_clicks" else ""
            if name:
                out[period][name] = (out[period][name] or 0) + count
        rows = result.get("rows") or []
        offset += len(rows)
        if offset >= int(result.get("rowCount") or len(rows)):
            break
        if not rows:
            return {"unavailable": "GA4 detail rows are incomplete"}
    for key in out:
        out[key]["by_device_page"] = [groups[key][pair] for pair in sorted(groups[key])]
    return out


def _site_url(site: str) -> str:
    return SC_SITES + quote(site, safe="")


def search_console(client: Any, token: str, site: str, start: date, end: date) -> dict:
    """Clicks, impressions, CTR, position; top 10 queries and top 5 pages."""
    url = _site_url(site) + "/searchAnalytics/query"

    def query(dims: list[str], limit: int) -> list[dict]:
        resp = client.post(url, headers={"Authorization": f"Bearer {token}"}, json={
            "startDate": start.isoformat(), "endDate": end.isoformat(),
            "dimensions": dims, "rowLimit": limit})
        resp.raise_for_status()
        return resp.json().get("rows") or []

    total = (query([], 1) or [{}])[0]

    def top(rows: list[dict]) -> list[tuple[str, int, int]]:
        return [((r.get("keys") or [""])[0], int(r.get("clicks") or 0),
                 int(r.get("impressions") or 0)) for r in rows]

    return {"start": start, "end": end,
            "clicks": int(total.get("clicks") or 0),
            "impressions": int(total.get("impressions") or 0),
            "ctr": float(total.get("ctr") or 0.0),
            "position": float(total.get("position") or 0.0),
            "queries": top(query(["query"], 10)),
            "pages": top(query(["page"], 5))}


def submit_sitemap(client: Any, token: str, site: str, sitemap_url: str) -> dict:
    """sitemaps.submit (PUT), then sitemaps.get for its status. Google has
    no instant-indexing API for ordinary pages; this is the indexing job."""
    url = _site_url(site) + "/sitemaps/" + quote(sitemap_url, safe="")
    auth = {"Authorization": f"Bearer {token}"}
    client.put(url, headers=auth).raise_for_status()
    resp = client.get(url, headers=auth)
    resp.raise_for_status()
    body = resp.json()
    return {"url": sitemap_url,
            "errors": int(body.get("errors") or 0),
            "warnings": int(body.get("warnings") or 0),
            "last_downloaded": str(body.get("lastDownloaded") or ""),
            "pending": bool(body.get("isPending")),
            "submitted": sum(int(c.get("submitted") or 0) for c in body.get("contents") or [])}
