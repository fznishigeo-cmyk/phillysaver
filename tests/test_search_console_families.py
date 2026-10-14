"""Search Console pagination and missing/observed-zero family/device data."""

import json
from datetime import date

import httpx
import pytest

from dealradar.growth import google, report
from tests.test_growth_report import cfg, GOOGLE_TOKEN, SC_TOTAL

START, END = date(2026, 10, 2), date(2026, 10, 8)


def _fetch(monkeypatch, rows, *, totals=SC_TOTAL, failure=False):
    requests = []
    monkeypatch.setattr(google, "SC_PAGE_SIZE", 2)

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if body["dimensions"] == []:
            return httpx.Response(200, json=totals)
        if body["dimensions"] != ["page", "device"]:
            return httpx.Response(200, json={"rows": []})
        assert body["dataState"] == "final" and body["rowLimit"] == 2
        if failure and body["startRow"]:
            return httpx.Response(403, text="SECRET-TOKEN /private-path")
        offset = body["startRow"]
        return httpx.Response(200, json={"rows": rows[offset:offset + body["rowLimit"]]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = google.search_console(client, GOOGLE_TOKEN, "sc-domain:wowpriced.com", START, END)
    return result, requests


def _row(data, family, device=None):
    return next(r for r in data["by_device_page" if device else "by_family"]
                if r["family"] == family and (not device or r["device"] == device))


def test_paginated_rows_aggregate_weighted_ctr_and_custom_price_family(cfg, monkeypatch):
    rows = [
        {"keys": ["https://wowpriced.com/", "MOBILE"], "clicks": 3, "impressions": 10, "ctr": .3},
        {"keys": ["https://wowpriced.com/index.html", "MOBILE"], "clicks": 2, "impressions": 90, "ctr": 2 / 90},
        {"keys": ["https://wowpriced.com/deals/under-100/?from=PRIVATE", "DESKTOP"], "clicks": 0, "impressions": 8},
        {"keys": ["https://wowpriced.com/deal/private-item-12345678/", "TABLET"], "clicks": 0, "impressions": 0},
    ]
    data, requests = _fetch(monkeypatch, rows)
    assert [r["startRow"] for r in requests if r["dimensions"] == ["page", "device"]] == [0, 2, 4]
    assert _row(data, "home", "mobile") == {"family": "home", "device": "mobile", "clicks": 5,
                                            "impressions": 100, "ctr": .05}
    assert _row(data, "home")["ctr"] == .05
    assert _row(data, "price", "desktop")["clicks"] == 0
    assert _row(data, "price", "desktop")["ctr"] == 0.0
    assert _row(data, "deal", "tablet")["impressions"] == 0
    assert _row(data, "deal", "tablet")["ctr"] is None
    assert _row(data, "category", "mobile")["clicks"] is None
    assert _row(data, "store")["impressions"] is None
    _, text = report.build_report(cfg, {"search_console": data}, {}, END)
    assert "mobile/home: clicks 5, impressions 100, CTR 5.0%" in text
    assert "desktop/price: clicks 0, impressions 8, CTR 0.0%" in text
    assert "tablet/deal: clicks 0, impressions 0, CTR unavailable" in text
    missing = next(line for line in text.splitlines() if line.startswith("not measured yet:"))
    assert "— mobile/category" in missing
    assert "mobile/category: clicks unavailable" not in text
    assert "PRIVATE" not in text and "private-item" not in json.dumps(data, default=str)


def test_no_rows_and_old_report_samples_are_unavailable(cfg, monkeypatch):
    data, _requests = _fetch(monkeypatch, [], totals={"rows": []})
    assert all(data[k] is None for k in ("clicks", "impressions", "ctr", "position"))
    assert all(row["clicks"] is None for row in data["by_device_page"])
    _, text = report.build_report(cfg, {"search_console": data}, {}, END)
    missing = next(line for line in text.splitlines() if line.startswith("not measured yet:"))
    assert "Search Console totals" in missing and "— home" in missing
    assert "Clicks unavailable, impressions unavailable" not in text
    assert "home: clicks unavailable" not in text
    for snapshot in ({}, {"search_console": {k: v for k, v in data.items()
                                             if k not in ("by_family", "by_device_page")}}):
        _, text = report.build_report(cfg, snapshot, {"search_console": "skipped"}, END)
        assert "unavailable" in text
        assert "family: clicks 0" not in text


@pytest.mark.parametrize("fault", ["missing_metric", "http_error", "repeated_row"])
def test_partial_family_device_fetch_is_unavailable_and_totals_survive(cfg, monkeypatch, caplog, fault):
    row = {"keys": ["https://wowpriced.com/", "MOBILE"], "clicks": 1, "impressions": 10}
    rows = [row, {**row, "keys": ["https://wowpriced.com/stores/amazon/", "MOBILE"]},
            {**row, "keys": ["https://wowpriced.com/deals/electronics/", "MOBILE"]}]
    if fault == "missing_metric":
        del rows[-1]["clicks"]
    elif fault == "repeated_row":
        rows[-1] = row
    data, _requests = _fetch(monkeypatch, rows, failure=fault == "http_error")
    assert data["clicks"] == SC_TOTAL["rows"][0]["clicks"]
    assert "family_device_unavailable" in data
    assert all(row["clicks"] is None for row in data["by_device_page"])
    _, text = report.build_report(cfg, {"search_console": data}, {}, END)
    assert "family/device unavailable" in text
    assert "SECRET-TOKEN" not in text + caplog.text and "private-path" not in text
