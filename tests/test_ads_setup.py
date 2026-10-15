"""Offline: the one-time builder only ever creates a PAUSED campaign (MockTransport)."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import yaml

from dealradar import cli
from dealradar.config import AppConfig, load_config
from dealradar.growth import ads_setup

NOW = datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc)
CID = "4412275803"
ROOT = Path(__file__).resolve().parents[1]


def config(cap=10, **setup):
    shipped = load_config().raw["ads"]["setup"]
    return AppConfig(growth_google_ads_customer_id=CID, growth_google_ads_api_version="v25",
        raw={"ads": {"authorized_daily_usd": cap, "hard_max_daily_usd": 10,
                     "authorized_on": "2026-10-14", "authorized_quote": f"AUTHORIZE ADS: ${cap} per day",
                     "setup": {**shipped, **setup}}})


@pytest.fixture(autouse=True)
def secrets(monkeypatch):
    for name in ("GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN"):
        monkeypatch.setenv(name, "FAKE_" + name)


class API:
    def __init__(self, existing=(), reject=False):
        self.existing, self.reject = list(existing), reject
        self.requests, self.mutates = [], []

    def __call__(self, request):
        self.requests.append(request)
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "FAKE_ADS_TOKEN"})
        assert request.url.host == "googleads.googleapis.com"
        assert request.headers["Authorization"] == "Bearer FAKE_ADS_TOKEN"
        body = json.loads(request.content)
        if request.url.path.endswith("googleAds:mutate"):
            assert request.url.path == f"/v25/customers/{CID}/googleAds:mutate"
            self.mutates.append(body)
            if self.reject:
                return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT",
                    "message": "Request contains an invalid argument.", "details": [{"errors": [
                        {"errorCode": {"policyFindingError": "POLICY_FINDING"},
                         "message": "A policy was violated. See PolicyFindingDetails for more detail.",
                         "location": {"fieldPathElements": [{"fieldName": "mutate_operations", "index": 9},
                                                            {"fieldName": "ad_group_ad_operation"}]},
                         "details": {"policyFindingDetails": {"policyTopicEntries": [
                             {"topic": "TRADEMARKS_IN_AD_TEXT", "type": "PROHIBITED"}]}}}]}]}})
            return httpx.Response(200, json={"mutateOperationResponses": []})
        query = body["query"]
        if "FROM billing_setup" in query:
            rows = [{"billingSetup": {"status": "APPROVED"}}]
        elif "FROM recommendation_subscription" in query:
            rows = []
        else:
            assert "campaign.name = 'wowpriced search - generic deals'" in query
            rows = [{"campaign": {"status": s}} for s in self.existing]
        return httpx.Response(200, json={"results": rows})

    def setup(self, cfg=None, **kw):
        with httpx.Client(transport=httpx.MockTransport(self)) as client:
            return ads_setup.setup(cfg or config(), NOW, client, **kw)


def creates(body):
    return [(kind, op["create"]) for item in body["mutateOperations"] for kind, op in item.items()]


def test_creates_one_paused_campaign_after_google_validates_it(caplog):
    api = API()
    with caplog.at_level(logging.INFO):
        assert api.setup() == 0
    check, real = api.mutates
    assert check.pop("validateOnly") is True and check == real and "validateOnly" not in real
    ops = creates(real)
    assert all(set(item) == {kind} for item, (kind, _c) in zip(real["mutateOperations"], ops))
    kinds = [k for k, _c in ops]
    assert kinds.count("campaignOperation") == kinds.count("campaignBudgetOperation") == 1
    budget = next(c for k, c in ops if k == "campaignBudgetOperation")
    assert budget["amountMicros"] == "5000000" and budget["explicitlyShared"] is False
    campaign = next(c for k, c in ops if k == "campaignOperation")
    assert campaign["status"] == "PAUSED" and campaign["advertisingChannelType"] == "SEARCH"
    assert campaign["targetSpend"] == {"cpcBidCeilingMicros": "400000"}
    assert campaign["networkSettings"] == {"targetGoogleSearch": True, "targetSearchNetwork": False,
        "targetContentNetwork": False, "targetPartnerSearchNetwork": False}
    assert "{campaignid}" in campaign["finalUrlSuffix"] and "paymentMode" not in campaign
    criteria = [c for k, c in ops if k == "campaignCriterionOperation"]
    assert {"geoTargetConstant": "geoTargetConstants/2840"} in [c.get("location") for c in criteria]
    negatives = [c["keyword"] for c in criteria if c.get("negative")]
    assert len(negatives) == 15 and {n["matchType"] for n in negatives} == {"BROAD"}
    assert {"text": "amazon", "matchType": "BROAD"} in negatives
    keywords = [c["keyword"] for k, c in ops if k == "adGroupCriterionOperation"]
    assert len(keywords) == 10 and {k["matchType"] for k in keywords} == {"PHRASE"}
    ad = next(c for k, c in ops if k == "adGroupAdOperation")["ad"]
    assert ad["finalUrls"] == ["https://wowpriced.com/"]
    assert len(ad["responsiveSearchAd"]["headlines"]) == 8
    assert "created PAUSED campaign" in caplog.text and "billing: APPROVED" in caplog.text
    assert "auto-apply on: none" in caplog.text


@pytest.mark.parametrize("status", ["ENABLED", "PAUSED"])
def test_an_existing_campaign_is_never_touched(status, caplog):
    api = API(existing=[status])
    with caplog.at_level(logging.INFO):
        assert api.setup() == 0
    assert api.mutates == [] and "already exists" in caplog.text


@pytest.mark.parametrize("cap,budget", [(0, 5), (8, 5), (10, 5.01), (10, 0)])
def test_budget_must_fit_half_the_authorized_cap(cap, budget, caplog):
    api = API()
    assert api.setup(config(cap, daily_budget_usd=budget)) == 1
    assert api.requests == [] and "nothing created" in caplog.text


def test_google_rejection_creates_nothing(caplog):
    api = API(reject=True)
    assert api.setup() == 1
    assert len(api.mutates) == 1 and api.mutates[0]["validateOnly"] is True
    assert "POLICY_FINDING" in caplog.text and "nothing created" in caplog.text
    assert "mutate_operations.ad_group_ad_operation" in caplog.text
    assert "TRADEMARKS_IN_AD_TEXT" in caplog.text


def test_validate_only_never_creates():
    api = API()
    assert api.setup(validate_only=True) == 0
    assert [m.get("validateOnly") for m in api.mutates] == [True]


@pytest.mark.parametrize("field,value", [("headlines", ["x" * 31, "a", "b"]), ("descriptions", ["only one"]),
                                         ("final_url", "http://wowpriced.com/"), ("campaign_name", "it's")])
def test_plan_rejects_what_google_would(field, value):
    with pytest.raises(ValueError):
        ads_setup.plan(config(**{field: value}), NOW)


def test_shipped_plan_and_workflow_and_cli(monkeypatch):
    p = ads_setup.plan(load_config(), NOW)
    assert p["budget_micros"] * 2 <= 10_000_000
    doc = yaml.safe_load((ROOT / ".github/workflows/ads-setup.yml").read_text())
    assert set(doc[True]) == {"workflow_dispatch"}  # manual only, never scheduled
    assert doc[True]["workflow_dispatch"]["inputs"]["validate_only"]["default"] is True
    assert doc["permissions"] == {"contents": "read"}
    step = doc["jobs"]["setup"]["steps"][-1]
    assert set(step["env"]) == {"GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET",
                                "GOOGLE_ADS_REFRESH_TOKEN", "VALIDATE_ONLY"}
    called = []
    monkeypatch.setattr(ads_setup, "run", lambda cfg, **kw: called.append(kw) or 0)
    assert cli.main(["ads-setup", "--validate-only"]) == 0
    assert called == [{"validate_only": True}]
