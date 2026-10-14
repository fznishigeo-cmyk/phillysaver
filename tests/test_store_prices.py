"""Store API checks use recorded-shape fixtures and the real polite transport."""

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import yaml

from dealradar import dedupe, rank, savings, store_prices as live, util, verify
from dealradar.config import AppConfig, load_config, redact_secrets
from dealradar.db import DB
from dealradar.http_client import PoliteFetcher
from dealradar.models import Evidence, ExtractedDeal, RawPost
from dealradar.parse.extractor import verify_evidence
from dealradar.publish import site
from tests.http_helpers import HostClock

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / "fixtures" / "store_prices"
IDS = {"bestbuy": "6534605", "ebay": "123456789012"}
URLS = {"bestbuy": "https://www.bestbuy.com/site/widget/6534605.p?skuId=6534605",
        "ebay": "https://www.ebay.com/itm/123456789012?campid=5339221823&mkcid=1&mkevt=1"}


def fixture(name):
    return json.loads((FIXTURES / (name + ".json")).read_text())


@pytest.fixture
def cfg(monkeypatch):
    cfg = load_config()
    cfg.feature_flags.update(bestbuy_live=True, ebay_live=True, keepa=False)
    for name in ("BESTBUY_API_KEY", "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"):
        monkeypatch.setenv(name, "synthetic-" + name)
    return cfg


@pytest.fixture
def db():
    db = DB(":memory:")
    yield db
    db.close()


def seed(db, cfg, store="bestbuy", identity=None, **changes):
    identity = identity or IDS[store]
    seen = NOW - timedelta(days=1)
    url = URLS[store].replace(IDS[store], identity)
    text = "Widget. Price $25.00. Regular $100.00."
    post = RawPost("slickdeals_frontpage", identity, "https://slickdeals.net/f/" + identity,
                   "Widget", text, seen, seen)
    ext = ExtractedDeal("Widget " + identity, live.NAMES[store], post.url, post.source_name,
                        product_url=url, final_price=25, regular_price=100, online_only="true",
                        category="electronics", evidence=Evidence(final_price_quote="Price $25.00.",
                                                                   regular_price_quote="Regular $100.00."))
    assert verify_evidence(ext, text)[0]
    key = dedupe.upsert_extracted(db, ext, savings.compute_savings(ext), post, cfg, seen)["dedupe_key"]
    row = db.get_deal(key)
    row.update({"critic": {"plausible": True, "confidence": 1}, **changes})
    db.update_deal(key, payload=row, expires_at=changes.get("expires_at"))
    return db.get_deal(key)


class API:
    def __init__(self, db, cfg, *, status=200, token_status=200, change=None, response=None):
        self.clock, self.calls = HostClock(), []
        self.status, self.token_status = status, token_status
        self.change, self.response = change, response
        self.fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(self.handle),
                                    clock=self.clock, sleep_fn=self.clock.sleep)

    def handle(self, request):
        self.calls.append((self.clock(), request))
        assert request.url.host in {"api.bestbuy.com", "api.ebay.com"}
        assert "hello@wowpriced.com" in request.headers["User-Agent"]
        assert request.url.path != "/robots.txt"  # licensed API, never retailer scraping
        assert "If-None-Match" not in request.headers
        if str(request.url) == live.TOKEN_URL:
            assert request.method == "POST"
            assert parse_qs(request.content.decode()) == {
                "grant_type": ["client_credentials"], "scope": ["https://api.ebay.com/oauth/api_scope"]}
            credentials = base64.b64decode(request.headers["Authorization"].split()[1]).decode()
            assert credentials == "synthetic-EBAY_CLIENT_ID:synthetic-EBAY_CLIENT_SECRET"
            return httpx.Response(self.token_status, json=fixture("oauth"))
        if self.response:
            return self.response(request)
        if request.url.host == "api.bestbuy.com":
            assert request.url.params["apiKey"] == "synthetic-BESTBUY_API_KEY"
            assert request.url.params["show"] == "sku,salePrice,regularPrice,onSale,orderable,onlineAvailability,url"
            assert request.url.params["pageSize"] == "100"
            ids = request.url.path.split("in(")[1].split(")")[0].split(",")
            assert 1 <= len(ids) <= 100
            body = fixture("bestbuy")
            template = body["products"][0]
            body["products"] = [dict(template, sku=int(identity)) for identity in ids]
        else:
            assert request.url.path == "/buy/browse/v1/item/get_item_by_legacy_id"
            assert request.headers["Authorization"] == "Bearer synthetic-offline-access-token"
            assert request.headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_US"
            body = fixture("ebay")
            body["legacyItemId"] = request.url.params["legacy_item_id"]
            body["itemId"] = "v1|" + body["legacyItemId"] + "|0"
        if self.change:
            self.change(body)
        return httpx.Response(self.status, json=body, headers={"ETag": "do-not-cache", "Retry-After": "2"})

    def run(self, db, cfg, now=NOW, **kwargs):
        return live.recheck_store_prices(db, cfg, now, fetcher=self.fetcher, **kwargs)


@pytest.mark.parametrize("url,expected", [
    (URLS["bestbuy"], ("bestbuy", IDS["bestbuy"])),
    ("https://bestbuy.com/site/widget-6534605.p", ("bestbuy", IDS["bestbuy"])),
    ("https://bestbuy.com/site/product.p?skuId=6534605", ("bestbuy", IDS["bestbuy"])),
    (URLS["ebay"], ("ebay", IDS["ebay"])),
    ("https://ebay.com/itm/widget/123456789012/", ("ebay", IDS["ebay"])),
    ("https://ebay.com/itm/123456789012?var=123", ("", "")),
    ("https://bestbuy.com/site/widget/6534605.p?skuId=123", ("", "")),
    ("https://ebay.com.evil.test/itm/123456789012", ("", "")),
    ("https://notbestbuy.com/site/widget-6534605.p", ("", "")),
    ("https://bestbuy.com@evil.test/site/widget-6534605.p", ("", "")),
    ("https://[bad", ("", "")), ("", ("", "")),
])
def test_exact_store_identities(url, expected):
    assert live.store_offer({"canonical_url": url}) == expected


@pytest.mark.parametrize("store", ["bestbuy", "ebay"])
@pytest.mark.parametrize("price", [20, 25, 26, 50])
def test_prices_correct_same_row_with_verified_evidence(db, cfg, monkeypatch, store, price):
    before = seed(db, cfg, store)
    def change(body):
        if store == "bestbuy":
            body["products"][0]["salePrice"] = price
        else:
            body["price"]["value"] = str(price)
    api = API(db, cfg, change=change)
    checked = []
    def evidence(ext, text):
        checked.append((ext, text))
        return verify_evidence(ext, text)
    monkeypatch.setattr("dealradar.parse.extractor.verify_evidence", evidence)
    result = api.run(db, cfg)[store]
    row = db.get_deal(before["dedupe_key"])
    assert result == dict(checked=1, confirmed=1, repriced=1, suspect=0, gone=0, errors=0)
    assert row["final_price"] == price and row["regular_price"] == 100
    assert row["id"] == before["id"] and len(db.all_deals()) == 1
    for field in ("canonical_url", "sightings", "critic", "conditions", "first_seen_at"):
        assert row[field] == before[field]
    assert verify.price_confirmed_at(row) == NOW and row["status"] == "alive"
    assert checked and checked[0][0].evidence.final_price_quote in checked[0][1]
    computed = savings.compute_savings(checked[0][0])
    assert row["pct_at_checkout"] == computed.pct_at_checkout
    assert row["abs_savings"] == computed.abs_savings
    history = db.conn.execute("select count(*) from price_history").fetchone()[0]
    calls = len(api.calls)
    assert api.run(db, cfg)[store] == dict.fromkeys(live.COUNTERS, 0)
    assert len(api.calls) == calls and db.get_deal(row["dedupe_key"]) == row
    post = live.reprice_post(store, live.Reading(True, price, 100), row, NOW)
    assert not dedupe.reprice_existing(db, row, post, cfg, NOW)
    assert db.conn.execute("select count(*) from price_history").fetchone()[0] == history


@pytest.mark.parametrize("store", ["bestbuy", "ebay"])
@pytest.mark.parametrize("regular", [None, 0, 20, 25, 80])
def test_only_api_regular_above_sale_is_used(db, cfg, store, regular):
    before = seed(db, cfg, store)
    def change(body):
        if store == "bestbuy":
            body["products"][0]["regularPrice"] = regular
        else:
            body["marketingPrice"] = {"originalPrice": {"value": regular, "currency": "USD"}}
    API(db, cfg, change=change).run(db, cfg)
    row = db.get_deal(before["dedupe_key"])
    expected = regular if regular and regular > 25 else None
    assert row["regular_price"] == expected
    if expected is None:
        assert row["pct_at_checkout"] is None and row["abs_savings"] is None
        assert rank.site_deals(db, cfg, NOW) == []  # no old discount survives
    assert row["status"] == "alive"


@pytest.mark.parametrize("store", ["bestbuy", "ebay"])
def test_no_offer_requires_two_readings_one_hour_apart_and_live_resets(db, cfg, store):
    before = seed(db, cfg, store)
    def unavailable(body):
        if store == "bestbuy":
            body["products"][0]["onlineAvailability"] = False
        else:
            body["estimatedAvailabilities"][0]["estimatedAvailableQuantity"] = 0
    api = API(db, cfg, change=unavailable)
    assert api.run(db, cfg)[store]["suspect"] == 1
    row = db.get_deal(before["dedupe_key"])
    assert row["status"] == "alive" and verify.price_confirmed_at(row) is None
    assert api.run(db, cfg, NOW + timedelta(minutes=59))[store]["checked"] == 0
    api.change = None
    assert api.run(db, cfg, NOW + timedelta(hours=1))[store]["confirmed"] == 1
    api.change = unavailable
    assert api.run(db, cfg, NOW + timedelta(hours=5))[store]["suspect"] == 1
    assert api.run(db, cfg, NOW + timedelta(hours=6))[store]["gone"] == 1
    row = db.get_deal(before["dedupe_key"])
    assert row["status"] == "dead" and row["death"] == "confirmed"
    assert api.run(db, cfg, NOW + timedelta(hours=7))[store]["checked"] == 0


@pytest.mark.parametrize("store", ["bestbuy", "ebay"])
@pytest.mark.parametrize("price,verdict", [(20, True), (26.25, True), (26.27, False)])
def test_tolerance_and_higher_price_fallback(db, cfg, monkeypatch, store, price, verdict):
    before = seed(db, cfg, store)
    def change(body):
        if store == "bestbuy":
            body["products"][0]["salePrice"] = price
        else:
            body["price"]["value"] = price
    monkeypatch.setattr(dedupe, "reprice_existing", lambda *a: False)
    api = API(db, cfg, change=change)
    result = api.run(db, cfg)[store]
    assert result["confirmed"] == int(verdict) and result["suspect"] == int(not verdict)
    assert db.get_deal(before["dedupe_key"])["final_price"] == 25
    if not verdict:
        assert api.run(db, cfg, NOW + timedelta(hours=1))[store]["gone"] == 1


@pytest.mark.parametrize("store", ["bestbuy", "ebay"])
@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_http_failures_never_confirm_or_kill_and_retries_are_capped(db, cfg, store, status):
    before = seed(db, cfg, store)
    api = API(db, cfg, status=status)
    result = api.run(db, cfg)[store]
    assert result == dict(checked=0, confirmed=0, repriced=0, suspect=0, gone=0, errors=1)
    row = db.get_deal(before["dedupe_key"])
    assert row["final_price"] == 25 and row["status"] == "alive"
    assert not row.get("gone_suspect_at") and verify.price_confirmed_at(row) is None
    attempts = 1 if status in (401, 403) else cfg.max_retries
    assert len(api.calls) == attempts + (store == "ebay")
    assert all(b[0] - a[0] >= 1 for a, b in zip(api.calls, api.calls[1:]))
    calls = len(api.calls)
    api.run(db, cfg)
    assert len(api.calls) == calls


@pytest.mark.parametrize("status", [401, 429, 503])
def test_oauth_failures_do_not_request_items(db, cfg, status):
    seed(db, cfg, "ebay")
    api = API(db, cfg, token_status=status)
    assert api.run(db, cfg)["ebay"]["errors"] == 1
    assert all(str(req.url) == live.TOKEN_URL for _, req in api.calls)


@pytest.mark.parametrize("store", ["bestbuy", "ebay"])
@pytest.mark.parametrize("kind", ["empty", "bad_json", "wrong_shape", "timeout", "redirect", "404", "304"])
def test_inconclusive_responses_do_not_change_price_or_death(db, cfg, store, kind):
    before = seed(db, cfg, store)
    def response(req):
        if kind == "timeout":
            raise httpx.ReadTimeout("synthetic timeout " + str(req.url), request=req)
        return {
            "empty": httpx.Response(200, json={} ),
            "bad_json": httpx.Response(200, text="<html>challenge</html>"),
            "wrong_shape": httpx.Response(200, json=[]),
            "redirect": httpx.Response(302, headers={"Location": "https://www.ebay.com/splashui/challenge"}),
            "404": httpx.Response(404, json={"errors": []}),
            "304": httpx.Response(304),
        }[kind]
    api = API(db, cfg, response=response)
    assert api.run(db, cfg)[store]["errors"] == 1
    row = db.get_deal(before["dedupe_key"])
    assert row["final_price"] == before["final_price"] and not row.get("gone_suspect_at")


@pytest.mark.parametrize("store", ["bestbuy", "ebay"])
def test_disabled_or_missing_secrets_are_noop_and_log_once(db, cfg, monkeypatch, caplog, store):
    before = seed(db, cfg, store)
    api = API(db, cfg)
    cfg.feature_flags[store + "_live"] = False
    assert api.run(db, cfg)[store] == dict.fromkeys(live.COUNTERS, 0)
    cfg.feature_flags[store + "_live"] = True
    monkeypatch.delenv(live.SECRETS[store][-1])
    with caplog.at_level(logging.INFO):
        assert api.run(db, cfg)[store] == dict.fromkeys(live.COUNTERS, 0)
    assert caplog.text.count(store + " live checks skipped: missing secrets") == 1
    assert not api.calls and db.get_deal(before["dedupe_key"]) == before


def test_bestbuy_batches_100_and_hard_request_cap(db, cfg):
    cfg.bestbuy_max_requests_per_run = 2
    for n in range(205):
        seed(db, cfg, identity=str(7000000 + n))
    api = API(db, cfg)
    assert api.run(db, cfg)["bestbuy"]["checked"] == 200
    assert len(api.calls) == 2 and api.calls[1][0] - api.calls[0][0] >= 1
    assert not api.fetcher._api_bodies
    assert db.conn.execute("select count(*) from http_cache").fetchone()[0] == 0


@pytest.mark.parametrize("store", ["bestbuy", "ebay"])
def test_caps_include_retries_and_oauth(db, cfg, store):
    setattr(cfg, store + "_max_requests_per_run", 2)
    for n in range(3):
        seed(db, cfg, store, str(int(IDS[store]) + n))
    api = API(db, cfg, status=503)
    api.run(db, cfg)
    assert len(api.calls) == 2


def test_ebay_token_reused_until_expiry_only_in_memory(db, cfg):
    api = API(db, cfg)
    client = live.StoreAPI("ebay", cfg, api.fetcher)
    assert client.ebay(IDS["ebay"], NOW).price == 25
    assert client.ebay(IDS["ebay"], NOW).price == 25
    assert sum(str(req.url) == live.TOKEN_URL for _, req in api.calls) == 1
    api.clock.sleep(7200)
    assert client.ebay(IDS["ebay"], NOW).price == 25
    assert sum(str(req.url) == live.TOKEN_URL for _, req in api.calls) == 2
    assert "synthetic-offline-access-token" not in "\n".join(db.conn.iterdump())


def test_visibility_priority_cadence_and_deadline(db, cfg):
    rows = {name: seed(db, cfg, "ebay", str(123456789012 + n))
            for n, name in enumerate(("rest", "wall", "top", "suspect"))}
    suspect = rows["suspect"]
    db.update_deal(suspect["dedupe_key"], payload=dict(suspect,
                   gone_suspect_at=util.iso(NOW - timedelta(hours=1))))
    db.meta_set(site.PICKS_META, json.dumps([rows["top"]["dedupe_key"]]))
    db.meta_set(site.WALL_META, json.dumps([rows["wall"]["dedupe_key"]]))
    api = API(db, cfg)
    assert api.run(db, cfg)["ebay"]["checked"] == 4
    assert [req.url.params["legacy_item_id"] for _, req in api.calls if req.method == "GET"] == [
        live.store_offer(rows[name])[1] for name in ("suspect", "top", "wall", "rest")]
    assert api.run(db, cfg, NOW + timedelta(hours=4, seconds=-1))["ebay"]["checked"] == 0
    assert api.run(db, cfg, NOW + timedelta(hours=4))["ebay"]["checked"] == 4
    api.calls.clear()
    assert api.run(db, cfg, NOW + timedelta(hours=8), deadline=api.clock())["ebay"]["checked"] == 0
    assert not api.calls


@pytest.mark.parametrize("store", ["bestbuy", "ebay"])
def test_nondefault_rate_limit_applies_to_every_attempt(db, cfg, store):
    setattr(cfg, store + "_requests_per_second", .25)
    seed(db, cfg, store)
    api = API(db, cfg, status=503)
    api.run(db, cfg)
    assert all(b[0] - a[0] >= 4 for a, b in zip(api.calls, api.calls[1:]))


@pytest.mark.parametrize("kind", ["ended", "unavailable_error", "out_of_stock"])
def test_ebay_ended_shapes(db, cfg, kind):
    seed(db, cfg, "ebay")
    def response(req):
        if kind == "unavailable_error":
            return httpx.Response(404, json=fixture("ebay_unavailable"))
        body = fixture("ebay")
        if kind == "ended":
            body["itemEndDate"] = util.iso(NOW - timedelta(seconds=1))
        else:
            body["estimatedAvailabilities"] = [{"estimatedAvailabilityStatus": "OUT_OF_STOCK"}]
        return httpx.Response(200, json=body)
    assert API(db, cfg, response=response).run(db, cfg)["ebay"]["suspect"] == 1


@pytest.mark.parametrize("field,value", [("salePrice", "NaN"), ("salePrice", -1),
                                        ("salePrice", True), ("orderable", None),
                                        ("onlineAvailability", None)])
def test_bestbuy_malformed_price_or_availability_is_unknown(field, value):
    row = fixture("bestbuy")["products"][0]
    row[field] = value
    assert live.bestbuy_reading(row) is None


def test_ebay_identity_currency_auction_and_missing_price():
    base = fixture("ebay")
    for change in ({"legacyItemId": "wrong"}, {"price": {"value": "25", "currency": "EUR"}},
                   {"buyingOptions": ["AUCTION"]}, {"price": {}}, {"price": {"value": "Infinity", "currency": "USD"}}):
        assert live.ebay_reading(dict(base, **change), IDS["ebay"], NOW) is None


@pytest.mark.parametrize("store", ["bestbuy", "ebay"])
def test_evidence_failure_and_changed_identity_prevent_reprice(db, cfg, store):
    before = seed(db, cfg, store)
    post = live.reprice_post(store, live.Reading(True, 20, 100), before, NOW)
    post.text = "Wrong price $22.00."
    assert not dedupe.reprice_existing(db, before, post, cfg, NOW)
    post = live.reprice_post(store, live.Reading(True, 20, 100), before, NOW)
    post.structured["product_url"] = post.url.replace(IDS[store], "999999999999")
    assert not dedupe.reprice_existing(db, before, post, cfg, NOW)
    assert db.get_deal(before["dedupe_key"]) == before


@pytest.mark.parametrize("changes", [{"codes": ["SAVE"]}, {"conditions": ["membership_only"]},
                                    {"conditions": ["multi_buy"]}, {"rebates": ["$5 rebate"]},
                                    {"flags": ["range"]}, {"conditions": ["open_box"]}])
def test_bestbuy_cannot_replace_conditional_or_open_box_price(db, cfg, changes):
    before = seed(db, cfg, **changes)
    api = API(db, cfg)
    assert api.run(db, cfg)["bestbuy"]["confirmed"] == 0
    row = db.get_deal(before["dedupe_key"])
    assert row["final_price"] == 25 and row["conditions"] == before["conditions"]
    assert verify.price_confirmed_at(row) is None


def test_ebay_exact_used_listing_can_be_checked_and_keeps_condition(db, cfg):
    before = seed(db, cfg, "ebay", conditions=["used"])
    api = API(db, cfg)
    assert api.run(db, cfg)["ebay"]["confirmed"] == 1
    assert db.get_deal(before["dedupe_key"])["conditions"] == ["used"]


@pytest.mark.parametrize("store", ["bestbuy", "ebay"])
def test_post_expiry_queues_api_but_never_counts_as_first_reading(db, cfg, store):
    before = seed(db, cfg, store, expires_at=util.iso(NOW - timedelta(minutes=1)))
    assert verify.mark_expired_dead(db, cfg, NOW) == 0
    row = db.get_deal(before["dedupe_key"])
    assert row["status"] == "alive" and row["death"] == "stated_expiry"
    api = API(db, cfg, change=lambda body: body.update(itemEndDate=util.iso(NOW))
              if store == "ebay" else body["products"][0].update(onlineAvailability=False))
    assert api.run(db, cfg, NOW + timedelta(hours=2))[store]["suspect"] == 1
    assert db.get_deal(before["dedupe_key"])["status"] == "alive"


@pytest.mark.parametrize("store", ["bestbuy", "ebay"])
def test_public_checked_clock_pages_and_affiliate_links(db, cfg, tmp_path, store):
    before = seed(db, cfg, store)
    API(db, cfg).run(db, cfg)
    site.render_site(db, cfg, NOW + timedelta(minutes=25), out_dir=tmp_path)
    doc = json.loads((tmp_path / "deals.json").read_text())
    row = doc["deals"][0]
    assert row["checked_at"] == util.iso(NOW)
    assert 'price checked </span>25m ago' in (tmp_path / "index.html").read_text()
    page = (tmp_path / row["deal_path"].lstrip("/") / "index.html").read_text()
    assert 'Price checked <time datetime="2026-10-14T12:00:00+00:00">' in page
    if store == "ebay":
        assert "campid=5339221823" in page
    assert db.get_deal(before["dedupe_key"])["canonical_url"] == before["canonical_url"]


def test_credentials_and_api_content_never_persist_or_log(db, cfg, caplog):
    seed(db, cfg)
    seed(db, cfg, "ebay")
    def change(body):
        body.update(description="DO NOT RETAIN CONTENT", image="DO NOT RETAIN IMAGE")
    with caplog.at_level(logging.DEBUG):
        API(db, cfg, change=change).run(db, cfg)
    data = "\n".join(db.conn.iterdump()) + caplog.text
    for secret in ("synthetic-BESTBUY_API_KEY", "synthetic-EBAY_CLIENT_ID",
                   "synthetic-EBAY_CLIENT_SECRET", "synthetic-offline-access-token",
                   "DO NOT RETAIN CONTENT", "DO NOT RETAIN IMAGE"):
        assert secret not in data
    for name in ("BESTBUY_API_KEY", "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"):
        assert "synthetic-" not in redact_secrets("synthetic-" + name)


def test_configuration_defaults_overrides_and_workflow_env(tmp_path):
    default = load_config()
    assert default.feature_flags["bestbuy_live"] is False and default.feature_flags["ebay_live"] is False
    for cfg in (default, AppConfig()):
        assert cfg.bestbuy_tolerance_pct == cfg.ebay_tolerance_pct == 5
        assert cfg.bestbuy_requests_per_second == 5
    path = tmp_path / "config.yaml"
    values = {"tolerance_pct": 3, "recheck_h": 6, "requests_per_second": .5, "max_requests_per_run": 7}
    path.write_text(yaml.safe_dump({"bestbuy": values, "ebay": values}))
    changed = load_config(path)
    for store in ("bestbuy", "ebay"):
        assert {k: getattr(changed, store + "_" + k) for k in values} == values
    for workflow in ("fast", "hourly"):
        content = (Path(__file__).parents[1] / ".github/workflows" / (workflow + ".yml")).read_text()
        for name in ("BESTBUY_API_KEY", "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"):
            assert name + ": ${{ secrets." + name + " }}" in content


@pytest.mark.parametrize("change", [
    {"itemEndDate": 123}, {"itemEndDate": "nonsense"}, {"buyingOptions": 42},
    {"estimatedAvailabilities": {}}, {"estimatedAvailabilities": [None]},
    {"estimatedAvailabilities": [{"estimatedAvailableQuantity": False}]},
    {"estimatedAvailabilities": [{"estimatedAvailabilityStatus": "UNKNOWN"}]},
    {"itemId": "v1|123456789012|999"},
])
def test_malformed_ebay_fields_never_prove_a_price_or_death(change):
    assert live.ebay_reading(dict(fixture("ebay"), **change), IDS["ebay"], NOW) is None


def test_bestbuy_missing_sku_and_conflicting_duplicates_are_unknown(db, cfg):
    row = seed(db, cfg)
    second = seed(db, cfg, identity="7777777")
    def response(req):
        body = fixture("bestbuy")
        body["products"].append(dict(body["products"][0], salePrice=75))
        return httpx.Response(200, json=body)
    result = API(db, cfg, response=response).run(db, cfg)["bestbuy"]
    assert result["errors"] == 2 and result["checked"] == 0
    for before in (row, second):
        assert not db.get_deal(before["dedupe_key"]).get("gone_suspect_at")


def test_retry_recovers_and_deadline_cannot_be_crossed_by_host_wait(db, cfg):
    seed(db, cfg, "ebay")
    count = 0
    def response(req):
        nonlocal count
        count += 1
        return httpx.Response(503 if count == 1 else 200, json=fixture("ebay"))
    api = API(db, cfg, response=response)
    assert api.run(db, cfg)["ebay"]["confirmed"] == 1
    assert len(api.calls) == 3
    api = API(db, cfg)
    # OAuth can run at t=0; the item request must wait until t=1.
    assert api.run(db, cfg, NOW + timedelta(hours=4), deadline=.5)["ebay"]["checked"] == 0
    assert len(api.calls) == 1 and str(api.calls[0][1].url) == live.TOKEN_URL


@pytest.mark.parametrize("store", ["bestbuy", "ebay"])
def test_shipping_recomputed_and_critic_rejection_not_laundered(db, cfg, store):
    before = seed(db, cfg, store, shipping_cost_or_threshold="$5.00",
                  flags=["suspect_msrp"], critic={"plausible": False})
    post = live.reprice_post(store, live.Reading(True, 10, 80), before, NOW)
    assert dedupe.reprice_existing(db, before, post, cfg, NOW)
    row = db.get_deal(before["dedupe_key"])
    assert row["final_price"] == 15 and row["shipping_cost_or_threshold"] == "$5.00"
    assert row["critic"] == {"plausible": False} and "suspect_msrp" in row["flags"]
    assert not rank.site_deals(db, cfg, NOW)


@pytest.mark.parametrize("store", ["bestbuy", "ebay"])
@pytest.mark.parametrize("fast_lane", [False, True])
def test_pipeline_checks_stored_deals_before_publish_and_rerun_is_idempotent(
        db, cfg, monkeypatch, tmp_path, store, fast_lane):
    from dealradar import pipeline
    for name in ("DEALS_CLAUDE_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID",
                 "SMTP_HOST", "NEWSLETTER_SECRET", "STRIPE_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(util, "now_utc", lambda: NOW)
    config = tmp_path / "config.yaml"
    raw = dict(cfg.raw)
    raw["publish"] = dict(raw["publish"], site_dir=str(tmp_path / "site"))
    raw["parse"] = dict(raw["parse"], recategorize_other_per_run=0)
    config.write_text(yaml.safe_dump(raw))
    database = tmp_path / "offline.db"
    stored = DB(database)
    before = seed(stored, cfg, store, image_url="https://images.example/widget.jpg")
    stored.meta_set("recategorized_v2", "1")
    stored.close()
    api = API(db, cfg, change=lambda body: body["products"][0].update(salePrice=20)
              if store == "bestbuy" else body["price"].update(value="20.00"))
    def route(req):
        if req.url.host in {"api.bestbuy.com", "api.ebay.com"}:
            return api.handle(req)
        return httpx.Response(404)
    def run():
        return pipeline.run(db_path=str(database), config_path=str(config),
                            fast_lane=fast_lane, now=NOW, sources_override=[],
                            skip_validation=True, dry_run_telegram=True,
                            transport=httpx.MockTransport(route))
    result = run()
    assert result["store_checks"][store]["repriced"] == 1
    assert result["store_checks"][store]["confirmed"] == 1
    doc = json.loads((tmp_path / "site/deals.json").read_text())
    assert len(doc["deals"]) == 1 and doc["deals"][0]["final_price"] == 20
    assert doc["deals"][0]["checked_at"] == util.iso(NOW)
    calls = len(api.calls)
    assert run()["store_checks"][store]["checked"] == 0
    assert len(api.calls) == calls
    stored = DB(database)
    assert len(stored.all_deals()) == 1
    assert stored.get_deal(before["dedupe_key"])["sightings"] == before["sightings"]
    stored.close()
