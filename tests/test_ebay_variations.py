"""Group lookups stay bounded and can only confirm well-formed API evidence."""

import json
import logging
from datetime import timedelta

import httpx
import pytest

from dealradar import dedupe, non_amazon_freshness, store_prices as live, util, verify
from dealradar.parse.extractor import verify_evidence
from tests.test_store_prices import API, IDS, NOW, cfg, db, fixture, seed


def variation(price=25, variant="987654321098", **changes):
    return dict(fixture("ebay"), itemId="v1|" + IDS["ebay"] + "|" + variant,
                price={"value": str(price), "currency": "USD"}, **changes)


def group_api(db, cfg, body, *, status=200, errors=None):
    def response(request):
        assert request.headers["Authorization"] == "Bearer synthetic-offline-access-token"
        assert request.headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_US"
        if str(request.url).split("?")[0] == live.EBAY_URL:
            assert dict(request.url.params) == {"legacy_item_id": IDS["ebay"]}
            return httpx.Response(400, json={"errors": errors if errors is not None else [
                {"domain": "API_BROWSE", "errorId": 11006}]})
        assert str(request.url).split("?")[0] == live.EBAY_GROUP_URL
        assert dict(request.url.params) == {"item_group_id": IDS["ebay"]}
        return httpx.Response(status, json=body)
    return API(db, cfg, response=response)


@pytest.mark.parametrize("price", [20, 25, 25.01])
def test_group_confirms_our_price_and_evidence_clock_without_repricing(db, cfg, price):
    before = seed(db, cfg, "ebay", shipping_cost_or_threshold="$5.00")
    api = group_api(db, cfg, {"items": [variation(40, variant="1"), variation(price)]})
    result = api.run(db, cfg, run_key="group")["ebay"]
    assert result == dict(checked=1, confirmed=1, repriced=0, suspect=0, gone=0,
                          errors=0, diagnostics={"product:http_400:11006": 1})
    row = db.get_deal(before["dedupe_key"])
    for field in ("final_price", "regular_price", "sightings", "canonical_url", "critic"):
        assert row[field] == before[field]
    assert verify.price_confirmed_at(row) == NOW
    assert non_amazon_freshness.evidence_at(row, NOW) == NOW
    assert len(api.calls) == 3
    assert all(b[0] - a[0] >= 1 for a, b in zip(api.calls, api.calls[1:]))
    assert api.run(db, cfg, NOW + timedelta(minutes=10), run_key="group")["ebay"]["checked"] == 0
    assert len(api.calls) == 3
    assert not api.fetcher._api_bodies
    assert db.conn.execute("select count(*) from http_cache").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["empty", "out_of_stock", "zero", "ended"])
def test_group_unavailable_uses_two_reading_death_path(db, cfg, kind):
    before = seed(db, cfg, "ebay")
    changes = {
        "empty": {},
        "out_of_stock": {"estimatedAvailabilities": [{"estimatedAvailabilityStatus": "OUT_OF_STOCK"}]},
        "zero": {"estimatedAvailabilities": [{"estimatedAvailableQuantity": 0}]},
        "ended": {"itemEndDate": util.iso(NOW)},
    }[kind]
    api = group_api(db, cfg, {"items": [] if kind == "empty" else [variation(**changes)]})
    assert api.run(db, cfg)["ebay"]["suspect"] == 1
    row = db.get_deal(before["dedupe_key"])
    assert row["status"] == "alive" and verify.price_confirmed_at(row) is None
    assert api.run(db, cfg, NOW + timedelta(minutes=59))["ebay"]["checked"] == 0
    assert api.run(db, cfg, NOW + timedelta(hours=1))["ebay"]["gone"] == 1
    row = db.get_deal(before["dedupe_key"])
    assert row["status"] == "dead" and row["death"] == "confirmed"


@pytest.mark.parametrize("can_reprice", [True, False])
def test_group_dearer_uses_existing_evidence_reprice_or_suspect(db, cfg, monkeypatch, can_reprice):
    before = seed(db, cfg, "ebay")
    checked = []
    def evidence(ext, text):
        checked.append((ext, text))
        return verify_evidence(ext, text)
    monkeypatch.setattr("dealradar.parse.extractor.verify_evidence", evidence)
    if not can_reprice:
        monkeypatch.setattr(dedupe, "reprice_existing", lambda *a: False)
    api = group_api(db, cfg, {"items": [variation(60, variant="1"), variation(40)]})
    result = api.run(db, cfg)["ebay"]
    assert result["repriced"] == result["confirmed"] == int(can_reprice)
    assert result["suspect"] == int(not can_reprice)
    row = db.get_deal(before["dedupe_key"])
    assert row["final_price"] == (40 if can_reprice else 25)
    if can_reprice:
        assert row["regular_price"] is None and row["pct_at_checkout"] is None
        assert non_amazon_freshness.evidence_at(row, NOW) == NOW
        assert checked and checked[0][0].evidence.final_price_quote == "Price at eBay: $40.00."
        assert checked[0][0].evidence.final_price_quote in checked[0][1]
    else:
        assert verify.price_confirmed_at(row) is None
        assert api.run(db, cfg, NOW + timedelta(hours=1))["ebay"]["gone"] == 1


def test_group_skips_ineligible_variations_before_taking_minimum(db, cfg):
    before = seed(db, cfg, "ebay")
    euro = variation(1)
    euro["price"]["currency"] = "EUR"
    items = [variation(1, buyingOptions=["AUCTION"]), euro,
             variation(1, itemEndDate=util.iso(NOW - timedelta(seconds=1))),
             variation(1, estimatedAvailabilities=[{"estimatedAvailableQuantity": 0}]),
             variation(40, estimatedAvailabilities=[{"estimatedAvailabilityStatus": "LIMITED_STOCK"}]),
             variation(60)]
    for n, item in enumerate(items):
        item["itemId"] = "v1|" + IDS["ebay"] + "|" + str(n + 1)
    assert group_api(db, cfg, {"items": items}).run(db, cfg)["ebay"]["repriced"] == 1
    assert db.get_deal(before["dedupe_key"])["final_price"] == 40


@pytest.mark.parametrize("body", [None, [], {}, {"items": None}, {"items": {}},
                                 {"items": [None]}, {"items": [variation(), {}]}])
def test_malformed_group_has_no_opinion_and_a_diagnostic(db, cfg, body):
    before = seed(db, cfg, "ebay")
    result = group_api(db, cfg, body).run(db, cfg)["ebay"]
    assert result["errors"] == 1 and result["checked"] == 0
    assert result["diagnostics"]["group:http_200:invalid_response"] == 1
    row = db.get_deal(before["dedupe_key"])
    assert row["final_price"] == 25 and not row.get("gone_suspect_at")
    assert verify.price_confirmed_at(row) is None


@pytest.mark.parametrize("change", [
    {"legacyItemId": "wrong"}, {"itemId": "v1|999999999999|987654321098"}, {"itemId": None},
    {"price": {}}, {"price": {"currency": "USD", "value": "NaN"}},
    {"price": {"currency": "USD", "value": -1}}, {"price": {"currency": "USD", "value": True}},
    {"price": {"currency": None, "value": 25}},
    {"buyingOptions": None}, {"buyingOptions": []}, {"buyingOptions": [False]},
    {"itemEndDate": "bad date"}, {"itemEndDate": 123},
    {"estimatedAvailabilities": None}, {"estimatedAvailabilities": []},
    {"estimatedAvailabilities": [None]}, {"estimatedAvailabilities": [{}]},
    {"estimatedAvailabilities": [{"estimatedAvailabilityStatus": "UNKNOWN"}]},
    {"estimatedAvailabilities": [{"estimatedAvailableQuantity": False}]},
    {"estimatedAvailabilities": [{"estimatedAvailableQuantity": -1}]},
    {"estimatedAvailabilities": [{"estimatedAvailableQuantity": "3"}]},
])
def test_one_malformed_variation_invalidates_whole_group(db, cfg, change):
    before = seed(db, cfg, "ebay")
    malformed = dict(variation(), **change)
    result = group_api(db, cfg, {"items": [variation(), malformed]}).run(db, cfg)["ebay"]
    assert result["errors"] == 1 and result["checked"] == 0
    row = db.get_deal(before["dedupe_key"])
    assert row["final_price"] == 25 and not row.get("gone_suspect_at")
    assert verify.price_confirmed_at(row) is None


@pytest.mark.parametrize("status", [400, 404, 429, 503])
def test_group_http_failure_is_one_followup_and_never_death(db, cfg, caplog, status):
    before = seed(db, cfg, "ebay")
    body = {"items": [], "errors": [{"domain": "API_BROWSE", "errorId": 11001,
            "message": "PRIVATE-MESSAGE " + IDS["ebay"] + " synthetic-offline-access-token"}]}
    api = group_api(db, cfg, body, status=status)
    with caplog.at_level(logging.DEBUG):
        result = api.run(db, cfg)["ebay"]
    assert result["errors"] == 1 and result["checked"] == 0
    assert result["diagnostics"][f"group:http_{status}:11001"] == 1
    assert len(api.calls) == 3
    assert not db.get_deal(before["dedupe_key"]).get("gone_suspect_at")
    for forbidden in ("PRIVATE-MESSAGE", IDS["ebay"], "synthetic-offline-access-token", "api.ebay.com"):
        assert forbidden not in caplog.text


@pytest.mark.parametrize("cap", [1, 2, 3])
@pytest.mark.parametrize("kind", ["run", "day"])
def test_caps_include_oauth_listing_and_group_and_survive_rerun(db, cfg, cap, kind):
    seed(db, cfg, "ebay")
    setattr(cfg, "ebay_max_requests_per_" + kind, cap)
    api = group_api(db, cfg, {"items": [variation()]})
    result = api.run(db, cfg, run_key="capped")["ebay"]
    assert result["confirmed"] == int(cap == 3)
    if cap < 3:
        assert result["errors"] == 1
        assert result["diagnostics"]["quota:" + ("run_cap" if kind == "run" else "daily_cap")] == 1
    assert len(api.calls) == cap
    assert json.loads(db.meta_get("store_api_run:ebay"))["requests"] == cap
    assert json.loads(db.meta_get("store_api_daily:ebay"))["requests"] == cap
    api.run(db, cfg, NOW + timedelta(minutes=10), run_key="capped")
    assert len(api.calls) == cap


@pytest.mark.parametrize("errors,suffix", [
    (None, ""), ({"errorId": 11006}, ""),
    ([{"domain": "API_BROWSE", "errorId": "11006"},
      {"domain": "API_BROWSE", "errorId": 11006.0},
      {"domain": "API_BROWSE", "errorId": True},
      {"domain": "OTHER", "errorId": 12345}, None], ""),
    ([{"domain": "API_BROWSE", "errorId": 11006},
      {"domain": "ACCESS", "errorId": 1001},
      {"domain": "API_BROWSE", "errorId": 11001},
      {"domain": "API_BROWSE", "errorId": 11002}], ":11006:1001:11001"),
])
def test_http_diagnostics_only_allow_three_integer_browse_codes(db, cfg, caplog, errors, suffix):
    seed(db, cfg, "ebay")
    private = "PRIVATE-MESSAGE https://secret.example/ " + IDS["ebay"] + " synthetic-EBAY_CLIENT_SECRET"
    body = {"errors": errors, "message": private, "itemId": IDS["ebay"], "title": "PRIVATE-TITLE"}
    if isinstance(errors, list):
        for error in errors:
            if isinstance(error, dict):
                error.update(message=private, longMessage=private, parameters=[{"value": private}])
    api = API(db, cfg, response=lambda request: httpx.Response(403, json=body))
    with caplog.at_level(logging.DEBUG):
        result = api.run(db, cfg)["ebay"]
    assert result["diagnostics"] == {"product:http_403" + suffix: 1}
    assert result["errors"] == 1
    for forbidden in ("PRIVATE", "secret.example", IDS["ebay"], "synthetic-EBAY_CLIENT",
                      "synthetic-offline-access-token", "api.ebay.com"):
        assert forbidden not in caplog.text


def test_unreported_error_after_two_http_errors_gets_its_own_reason(db, cfg):
    for n in range(3):
        seed(db, cfg, "ebay", str(int(IDS["ebay"]) + n))
    calls = []
    def response(request):
        calls.append(request)
        return httpx.Response(200 if len(calls) == 3 else 404, json={})
    result = API(db, cfg, response=response).run(db, cfg)["ebay"]
    assert result["errors"] == 3 and result["checked"] == 0
    assert result["diagnostics"] == {"product:http_404": 2, "product:http_200:invalid_response": 1}


def test_explicit_variation_is_not_checked(db, cfg):
    before = seed(db, cfg, "ebay")
    db.update_deal(before["dedupe_key"], canonical_url="https://ebay.com/itm/123456789012?var=987654321098")
    api = group_api(db, cfg, {"items": [variation()]})
    assert api.run(db, cfg)["ebay"] == dict.fromkeys(live.COUNTERS, 0)
    assert not api.calls


def test_group_does_not_confirm_checkout_conditions(db, cfg):
    before = seed(db, cfg, "ebay", codes=["SAVE"])
    result = group_api(db, cfg, {"items": [variation(20)]}).run(db, cfg)["ebay"]
    assert result["confirmed"] == result["repriced"] == 0
    assert verify.price_confirmed_at(db.get_deal(before["dedupe_key"])) is None


@pytest.mark.parametrize("errors", [[], [{"domain": "API_BROWSE", "errorId": 11019}],
                                  [{"domain": "OTHER", "errorId": "unknown"}]])
def test_any_listing_400_can_fall_back_to_group(db, cfg, errors):
    seed(db, cfg, "ebay")
    api = group_api(db, cfg, {"items": [variation()]}, errors=errors)
    assert api.run(db, cfg)["ebay"]["confirmed"] == 1
    assert len(api.calls) == 3


def test_group_transport_failure_is_diagnosed_without_retry_or_private_text(db, cfg, caplog):
    seed(db, cfg, "ebay")
    def response(request):
        if request.url.path.endswith("get_item_by_legacy_id"):
            return httpx.Response(400)
        raise httpx.ReadTimeout("PRIVATE " + str(request.url), request=request)
    api = API(db, cfg, response=response)
    with caplog.at_level(logging.DEBUG):
        result = api.run(db, cfg)["ebay"]
    assert result["errors"] == 1 and result["checked"] == 0
    assert result["diagnostics"]["group:transport"] == 1
    assert len(api.calls) == 3
    assert "PRIVATE" not in caplog.text and IDS["ebay"] not in caplog.text


def test_group_wait_cannot_cross_deadline(db, cfg):
    before = seed(db, cfg, "ebay")
    api = group_api(db, cfg, {"items": [variation()]})
    assert api.run(db, cfg, deadline=1.5)["ebay"]["checked"] == 0
    assert len(api.calls) == 2  # OAuth at zero, listing at one, group must wait until two.
    assert verify.price_confirmed_at(db.get_deal(before["dedupe_key"])) is None


def test_same_group_reading_is_compared_to_each_stored_price(db, cfg):
    before = seed(db, cfg, "ebay")
    second = seed(db, cfg, "ebay", str(int(IDS["ebay"]) + 1), final_price=50)
    db.update_deal(second["dedupe_key"], canonical_url=before["canonical_url"], final_price=50)
    api = group_api(db, cfg, {"items": [variation(40)]})
    result = api.run(db, cfg)["ebay"]
    assert result["checked"] == result["confirmed"] == 2
    assert result["repriced"] == 1
    assert db.get_deal(before["dedupe_key"])["final_price"] == 40
    assert db.get_deal(second["dedupe_key"])["final_price"] == 50
    assert len(api.calls) == 3
