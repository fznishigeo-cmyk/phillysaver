"""Offline: the one-time builder only ever creates a PAUSED campaign (MockTransport)."""
import json
import logging
import os
import subprocess
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
SHIPPED = load_config().raw["ads"]["setup"]  # operator 2026-10-22: the deal-tools campaign


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
    def __init__(self, existing=(), reject=False, groups=(), ads=(), keywords=None):
        self.existing, self.reject, self.groups, self.ads = list(existing), reject, list(groups), list(ads)
        self.keywords = keywords or {}  # {ad group name: keyword texts already there}
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
        elif "responsive_search_ad.headlines" in query:
            assert f"campaign.name = '{SHIPPED['campaign_name']}'" in query
            assert "ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'" in query and "ad_group_ad.status != 'REMOVED'" in query
            rows = self.ads
        elif "FROM ad_group_ad" in query:
            assert f"campaign.name = '{SHIPPED['campaign_name']}'" in query
            rows = [{"adGroup": {"name": n, "status": "ENABLED"},
                     "adGroupAd": {"adStrength": "AVERAGE", "policySummary": {"approvalStatus": "APPROVED"}}}
                    for n in self.groups]
        elif "FROM keyword_view" in query:
            assert "ad_group_criterion.negative = FALSE" in query
            rows = [{"adGroup": {"name": "deal spotter"}, "adGroupCriterion": {"qualityInfo": {"qualityScore": 7},
                                                                              "approvalStatus": "APPROVED"}},
                    {"adGroup": {"name": "deal spotter"}, "adGroupCriterion": {"approvalStatus": "UNDER_REVIEW"}}]
        elif "FROM ad_group_criterion" in query:
            assert f"campaign.name = '{SHIPPED['campaign_name']}'" in query
            assert "ad_group_criterion.negative = FALSE" in query and "ad_group_criterion.status != 'REMOVED'" in query
            rows = [{"adGroup": {"name": n}, "adGroupCriterion": {"keyword": {"text": t}}}
                    for n, texts in self.keywords.items() for t in texts]
        elif "FROM ad_group" in query:
            assert f"campaign.name = '{SHIPPED['campaign_name']}'" in query and "!= 'REMOVED'" in query
            # Statuses as setup created them: the main group ENABLED, the tool groups PAUSED.
            rows = [{"adGroup": {"name": n, "resourceName": f"customers/{CID}/adGroups/{i}",
                                 "status": "ENABLED" if n.endswith(" ad group") else "PAUSED"}}
                    for i, n in enumerate(self.groups)]
        else:
            assert f"campaign.name = '{SHIPPED['campaign_name']}'" in query
            rows = [{"campaign": {"status": s, "resourceName": f"customers/{CID}/campaigns/777"}}
                    for s in self.existing]
        return httpx.Response(200, json={"results": rows})

    def setup(self, cfg=None, **kw):
        with httpx.Client(transport=httpx.MockTransport(self)) as client:
            return ads_setup.setup(cfg or config(), NOW, client, **kw)

    def update(self, cfg=None, **kw):
        with httpx.Client(transport=httpx.MockTransport(self)) as client:
            return ads_setup.update(cfg or config(), NOW, client, **kw)

    def add(self, cfg=None, **kw):
        with httpx.Client(transport=httpx.MockTransport(self)) as client:
            return ads_setup.add_missing_keywords(cfg or config(), NOW, client, **kw)


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
    assert len(keywords) == 44 and {k["matchType"] for k in keywords} == {"PHRASE"}
    assert not any("amazon" in k["text"] for k in keywords)
    groups = [c for k, c in ops if k == "adGroupOperation"]
    assert [(g["name"], g["status"]) for g in groups] == [
        ("wowpriced search - deal tools ad group", "ENABLED"), ("deal spotter", "PAUSED"), ("review checker", "PAUSED")]
    assert len({g["resourceName"] for g in groups}) == 3
    ad = next(c for k, c in ops if k == "adGroupAdOperation")["ad"]
    assert ad["finalUrls"] == ["https://wowpriced.com/amazon-deal-checker/"]
    assert len(ad["responsiveSearchAd"]["headlines"]) == 15 and len(ad["responsiveSearchAd"]["descriptions"]) == 4
    assert "created PAUSED campaign" in caplog.text and "billing: APPROVED" in caplog.text
    assert "auto-apply on: none" in caplog.text


@pytest.mark.parametrize("status", ["ENABLED", "PAUSED"])
def test_an_existing_campaign_is_never_touched(status, caplog):
    api = API(existing=[status], groups=["wowpriced search - deal tools ad group", "deal spotter", "review checker"])
    with caplog.at_level(logging.INFO):
        assert api.setup() == 0
    assert api.mutates == [] and "already exists" in caplog.text
    # Google's own grades, read-only (operator 2026-10-22: "they're optimized, right?").
    assert "ad in deal spotter (ENABLED): strength AVERAGE, APPROVED" in caplog.text
    assert "keywords in deal spotter: no score yet x1, quality 7/10 x1, under_review x1" in caplog.text


@pytest.mark.parametrize("have", [[], ["deal spotter"]])
def test_missing_tool_ad_groups_are_added_paused_to_the_same_campaign(have, caplog):
    # Operator 2026-10-22: Deal Spotter / Review Checker keywords, same campaign and budget.
    api = API(existing=["ENABLED"], groups=["wowpriced search - deal tools ad group", *have])
    with caplog.at_level(logging.INFO):
        assert api.setup() == 0
    check, real = api.mutates
    assert check.pop("validateOnly") is True and check == real
    ops = creates(real)
    kinds = {k for k, _c in ops}
    assert kinds == {"adGroupOperation", "adGroupCriterionOperation", "adGroupAdOperation"}  # no campaign/budget
    groups = [c for k, c in ops if k == "adGroupOperation"]
    expected = [n for n in ("deal spotter", "review checker") if n not in have]
    assert [g["name"] for g in groups] == expected
    assert {g["status"] for g in groups} == {"PAUSED"}
    assert {g["campaign"] for g in groups} == {f"customers/{CID}/campaigns/777"}
    urls = [c["ad"]["finalUrls"] for k, c in ops if k == "adGroupAdOperation"]
    assert urls == [[f"https://wowpriced.com/{n.replace(' ', '-')}/"] for n in expected]
    texts = " ".join(json.dumps(c["ad"]) for k, c in ops if k == "adGroupAdOperation").lower()
    assert "fakespot" not in texts and "reviewmeta" not in texts and "amazon" not in texts
    assert "added %d PAUSED ad group(s)" % len(expected) in caplog.text


def test_validate_only_adds_no_ad_group():
    api = API(existing=["ENABLED"], groups=["wowpriced search - deal tools ad group"])
    assert api.setup(validate_only=True) == 0
    assert [m.get("validateOnly") for m in api.mutates] == [True]


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


GROUP = SHIPPED["ad_groups"][0]


@pytest.mark.parametrize("field,value", [("headlines", ["x" * 31, "a", "b"]), ("descriptions", ["only one"]),
                                         ("final_url", "http://wowpriced.com/"), ("campaign_name", "it's"),
                                         ("keywords", ["amazon price checker"]),
                                         ("ad_groups", [{**GROUP, "keywords": ["Amazon deal grader"]}]),
                                         ("ad_groups", [GROUP, GROUP]), ("ad_groups", [{**GROUP, "name": "x'y"}]),
                                         ("ad_groups", [{**GROUP, "final_url": "http://wowpriced.com/"}]),
                                         ("ad_groups", [{**GROUP, "headlines": ["x" * 31, "a", "b"]}]),
                                         ("ad_groups", [GROUP] * 6),
                                         # Operator 2026-10-23: a full ad, exactly 15 headlines and 4 descriptions.
                                         ("headlines", SHIPPED["headlines"][:14]),
                                         ("headlines", [*SHIPPED["headlines"], "Another Free Checker"]),
                                         ("headlines", [*SHIPPED["headlines"][:14], SHIPPED["headlines"][0].lower()]),
                                         ("descriptions", SHIPPED["descriptions"][:3]),
                                         ("descriptions", [*SHIPPED["descriptions"][:3], "x" * 91]),
                                         ("ad_groups", [{**GROUP, "descriptions": GROUP["descriptions"][:3]}]),
                                         ("ad_groups", [{**GROUP, "headlines": ["Amazon Deal Grader", *GROUP["headlines"][1:]]}]),
                                         *(("headlines", [bad, *SHIPPED["headlines"][1:]]) for bad in (
                                             "Amazon Deal Checker", "Amzn Price Checker", "Amazn Deals Today", "Kindle Deals",
                                             "Prime Day Price Checker", "Alexa Deal Finder", "Fakespot Alternative",
                                             "Review Meta Alternative", "Walmart Price Checker", "Best Deal Checker",
                                             "Save 50% Today", "Deals From $5", "#1 Deal Checker", "Used by 10,000 Shoppers",
                                             "FREE Deal Checker", "Great Deals!", "Great Deals...", "Guaranteed Savings")),
                                         ("descriptions", [*SHIPPED["descriptions"][:3], "Check any Kindle price free."])])
def test_plan_rejects_what_google_would(field, value):
    with pytest.raises(ValueError):
        ads_setup.plan(config(**{field: value}), NOW)


def test_shipped_plan_and_workflow_and_cli(monkeypatch):
    # Ads off since 2026-10-15 (operator): no budget can be planned without authorization.
    with pytest.raises(ValueError):
        ads_setup.plan(load_config(), NOW)
    assert ads_setup.plan(config(), NOW)["budget_micros"] * 2 <= 10_000_000
    doc = yaml.safe_load((ROOT / ".github/workflows/ads-setup.yml").read_text())
    assert set(doc[True]) == {"workflow_dispatch"}  # manual only, never scheduled
    assert doc[True]["workflow_dispatch"]["inputs"]["validate_only"]["default"] is True
    assert doc["permissions"] == {"contents": "read"}
    step = doc["jobs"]["setup"]["steps"][-1]
    assert doc[True]["workflow_dispatch"]["inputs"]["update_ads"]["default"] is False
    assert doc[True]["workflow_dispatch"]["inputs"]["add_keywords"]["default"] is False
    assert set(step["env"]) == {"GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET",
                                "GOOGLE_ADS_REFRESH_TOKEN", "VALIDATE_ONLY", "UPDATE_ADS", "ADD_KEYWORDS"}
    # Anything but an explicit "false" stays a dry run; only an explicit "true" updates ads or adds keywords.
    for validate, update, add, flags in (
            ("true", "false", "false", " --validate-only"), ("false", "false", "false", ""), ("", "", "", " --validate-only"),
            ("true", "true", "false", " --validate-only --update-ads"), ("false", "true", "false", " --update-ads"),
            ("true", "false", "true", " --validate-only --add-keywords"), ("false", "false", "true", " --add-keywords")):
        out = subprocess.run(["bash", "-ec", 'python() { echo "$*"; }\n' + step["run"]], capture_output=True, text=True,
                             check=True, env={"PATH": os.environ["PATH"], "VALIDATE_ONLY": validate,
                                              "UPDATE_ADS": update, "ADD_KEYWORDS": add})
        assert out.stdout == f"-m dealradar ads-setup{flags}\n"
    called = []
    monkeypatch.setattr(ads_setup, "run", lambda cfg, **kw: called.append(kw) or 0)
    assert cli.main(["ads-setup", "--validate-only"]) == 0
    assert cli.main(["ads-setup", "--update-ads"]) == 0
    assert cli.main(["ads-setup", "--validate-only", "--add-keywords"]) == 0
    assert called == [{"validate_only": True, "update_ads": False, "add_keywords": False},
                      {"validate_only": False, "update_ads": True, "add_keywords": False},
                      {"validate_only": True, "update_ads": False, "add_keywords": True}]
    with pytest.raises(SystemExit):  # one mode per run
        cli.main(["ads-setup", "--update-ads", "--add-keywords"])


def test_ad_quality_report_never_fails_the_check(caplog):
    api = API(existing=["ENABLED"], groups=["wowpriced search - deal tools ad group", "deal spotter", "review checker"])
    real = api.__call__

    def broken(request):
        if request.url.host == "googleads.googleapis.com" and b"keyword_view" in request.content:
            return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT", "message": "bad field"}})
        return real(request)
    with caplog.at_level(logging.INFO), httpx.Client(transport=httpx.MockTransport(broken)) as client:
        assert ads_setup.setup(config(), NOW, client) == 0
    assert "ad quality: unavailable" in caplog.text and api.mutates == []


NAMES = ["wowpriced search - deal tools ad group", "deal spotter", "review checker"]
COPY = dict(zip(NAMES, [SHIPPED, *SHIPPED["ad_groups"]]))
RSA_TEXT = "responsiveSearchAd.headlines,responsiveSearchAd.descriptions"


def test_shipped_copy_fills_every_ad_and_follows_the_ad_rules():
    # Operator 2026-10-23 ("WHY NOT A+"): 15 headlines, 4 descriptions, keywords in headlines.
    assert ads_setup.plan(config(), NOW)
    assert [g["name"] for g in SHIPPED["ad_groups"]] == NAMES[1:]
    urls = [g["final_url"] for g in COPY.values()]
    assert urls == ["https://wowpriced.com/amazon-deal-checker/", "https://wowpriced.com/deal-spotter/",
                    "https://wowpriced.com/review-checker/"]  # landing pages unchanged
    for name, g in COPY.items():
        heads, descs = g["headlines"], g["descriptions"]
        assert len(heads) == 15 and all(len(t) <= 30 for t in heads), name
        assert len(descs) == 4 and all(len(t) <= 90 for t in descs), name
        assert len({t.lower() for t in heads}) == 15 and len({t.lower() for t in descs}) == 4, name
        text = " ".join(heads + descs)
        for banned in ("amazon", "amzn", "kindle", "prime", "alexa", "fakespot", "reviewmeta", "review meta",
                       "walmart", "best", "#", "%", "$", "!", "guarantee"):
            assert banned not in text.lower(), (name, banned)
        assert not any(c.isdigit() for c in text), name
        assert sum(any(k in t.lower() for k in g["keywords"]) for t in heads) >= 3, name


def rsa(name, ad_id, status="ENABLED", live=False):
    """One responsive search ad row as Google returns it; old copy unless live."""
    g = COPY[name]
    texts = g if live else {"headlines": g["headlines"][:8], "descriptions": g["descriptions"][:2]}
    return {"adGroup": {"name": name, "status": status},
            "adGroupAd": {"status": status, "adStrength": "POOR", "ad": {
                "resourceName": f"customers/{CID}/ads/{ad_id}",
                "responsiveSearchAd": {k: [{"text": t, "assetPerformanceLabel": "PENDING"} for t in texts[k]]
                                       for k in ("headlines", "descriptions")}}}}


def swaps(body):
    """{ad resource: (headlines, descriptions)}; fails on any other operation or field."""
    out = {}
    for item in body["mutateOperations"]:
        assert set(item) == {"adOperation"}
        op = item["adOperation"]
        assert set(op) == {"updateMask", "update"} and op["updateMask"] == RSA_TEXT
        assert set(op["update"]) == {"resourceName", "responsiveSearchAd"}
        ad = op["update"]["responsiveSearchAd"]
        assert set(ad) == {"headlines", "descriptions"}
        assert all(set(t) == {"text"} for t in ad["headlines"] + ad["descriptions"])  # nothing pinned
        out[op["update"]["resourceName"]] = ([t["text"] for t in ad["headlines"]], [t["text"] for t in ad["descriptions"]])
    return out


def three_ads():
    return [rsa(NAMES[0], 11), rsa(NAMES[1], 22, status="PAUSED"), rsa(NAMES[2], 33)]


@pytest.mark.parametrize("validate_only", [True, False])
def test_update_ads_swaps_only_the_ad_text_after_google_validates(validate_only, caplog):
    api = API(existing=["ENABLED"], groups=NAMES, ads=three_ads())
    with caplog.at_level(logging.INFO):
        assert api.update(validate_only=validate_only) == 0
    assert [m.get("validateOnly") for m in api.mutates] == ([True] if validate_only else [True, None])
    if not validate_only:
        check, real = api.mutates
        assert {k: v for k, v in check.items() if k != "validateOnly"} == real
    assert swaps(api.mutates[0]) == {
        f"customers/{CID}/ads/{i}": (COPY[n]["headlines"], COPY[n]["descriptions"]) for i, n in zip((11, 22, 33), NAMES)}
    # No status (nothing paused gets enabled), URL, keyword, bid, budget or campaign is written.
    sent = json.dumps(api.mutates)
    for field in ("status", "finalUrls", "keyword", "campaign", "Budget", "cpc", "adGroup"):
        assert field not in sent
    assert {r.url.path.rsplit("/", 1)[-1] for r in api.requests if r.url.host == "googleads.googleapis.com"} == {
        "googleAds:search", "googleAds:mutate"}
    assert "ad in deal spotter (group PAUSED, ad PAUSED): strength POOR, 8 headlines, 2 descriptions" in caplog.text
    assert ("validated (not updated)" if validate_only else "updated") + " 3 ad(s) to 15 headlines" in caplog.text
    # Names, counts, statuses and strength only: no ids, resource names or credentials
    # (run() silences httpx, whose request lines carry the account number).
    ours = " ".join(r.getMessage() for r in caplog.records if r.name == ads_setup.log.name)
    for secret in (CID, "/ads/", "FAKE_", "Bearer"):
        assert secret not in ours


@pytest.mark.parametrize("live,changed", [((True, True, True), []), ((True, False, True), [22])])
def test_update_ads_skips_ads_already_showing_the_copy(live, changed, caplog):
    # Idempotent: a re-run after the update writes nothing (and sends no ad back to review).
    ads = [rsa(n, i, live=x) for n, i, x in zip(NAMES, (11, 22, 33), live)]
    api = API(existing=["ENABLED"], groups=NAMES, ads=ads)
    with caplog.at_level(logging.INFO):
        assert api.update() == 0
    assert [list(swaps(m)) for m in api.mutates] == ([[f"customers/{CID}/ads/{i}" for i in changed]] * 2 if changed else [])
    assert ("already show the config copy" in caplog.text) == (not changed)


@pytest.mark.parametrize("ads", [[], three_ads()[:2], [*three_ads(), rsa(NAMES[1], 44)]])
def test_update_ads_needs_exactly_one_ad_per_group(ads, caplog):
    api = API(existing=["ENABLED"], groups=NAMES, ads=ads)
    assert api.update() == 1
    assert api.mutates == [] and "need exactly one responsive search ad in" in caplog.text
    assert "nothing updated" in caplog.text


def test_update_ads_rejection_or_unplannable_copy_changes_nothing(caplog):
    api = API(existing=["ENABLED"], groups=NAMES, ads=three_ads(), reject=True)
    assert api.update() == 1
    assert [m.get("validateOnly") for m in api.mutates] == [True]
    assert "TRADEMARKS_IN_AD_TEXT" in caplog.text and "failed, nothing updated" in caplog.text
    api = API(existing=["ENABLED"], groups=NAMES, ads=three_ads())
    assert api.update(config(headlines=SHIPPED["headlines"][:8])) == 1
    assert api.requests == []


def test_run_routes_update_ads_to_the_update_path(monkeypatch):
    calls = []
    for name in ("setup", "update", "add_missing_keywords"):
        monkeypatch.setattr(ads_setup, name, lambda cfg, now, client, name=name, **kw: calls.append((name, kw)) or 0)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        assert ads_setup.run(config(), http_client=client, update_ads=True, validate_only=True) == 0
        assert ads_setup.run(config(), http_client=client) == 0
        assert ads_setup.run(config(), http_client=client, add_keywords=True, validate_only=True) == 0
    assert calls == [("update", {"validate_only": True}), ("setup", {"validate_only": False}),
                     ("add_missing_keywords", {"validate_only": True})]


# Operator A50 2026-10-23 ("add phrases": cheaper search words, still $0.40 max/click,
# $5/day): the phrases appended to each group's keywords after the live ones.
LIVE = dict(zip(NAMES, (10, 7, 7)))  # keywords each group had before A50


def adds(body):
    """{ad group resource: [keyword texts]}; fails on any other operation or field."""
    out = {}
    for item in body["mutateOperations"]:
        assert set(item) == {"adGroupCriterionOperation"}
        op = item["adGroupCriterionOperation"]
        assert set(op) == {"create"} and set(op["create"]) == {"adGroup", "status", "keyword"}  # no bid
        assert op["create"]["status"] == "ENABLED" and set(op["create"]["keyword"]) == {"text", "matchType"}
        assert op["create"]["keyword"]["matchType"] == "PHRASE"
        out.setdefault(op["create"]["adGroup"], []).append(op["create"]["keyword"]["text"])
    return out


@pytest.mark.parametrize("validate_only", [True, False])
def test_add_keywords_adds_only_the_missing_phrases_after_google_validates(validate_only, caplog):
    live = {n: COPY[n]["keywords"][:k] for n, k in LIVE.items()}
    live[NAMES[0]] = [t.upper() for t in live[NAMES[0]]]  # Google may show other case
    live[NAMES[2]] = [*live[NAMES[2]], "Review Analyzer"]  # one added by hand already
    api = API(existing=["ENABLED"], groups=NAMES, keywords=live)
    with caplog.at_level(logging.INFO):
        assert api.add(validate_only=validate_only) == 0
    assert [m.get("validateOnly") for m in api.mutates] == ([True] if validate_only else [True, None])
    if not validate_only:
        check, real = api.mutates
        assert {k: v for k, v in check.items() if k != "validateOnly"} == real
    added = [t for n in NAMES for t in COPY[n]["keywords"][LIVE[n]:]]
    assert len(added) == len({t.lower() for t in added}) == 20  # no phrase in two groups
    assert adds(api.mutates[0]) == {
        f"customers/{CID}/adGroups/0": COPY[NAMES[0]]["keywords"][10:],
        f"customers/{CID}/adGroups/1": COPY[NAMES[1]]["keywords"][7:],
        f"customers/{CID}/adGroups/2": [t for t in COPY[NAMES[2]]["keywords"][7:] if t != "review analyzer"]}
    # No campaign, budget, bidding, ad group status, ad or negative is written.
    sent = json.dumps(api.mutates)
    for field in ("campaign", "Budget", "targetSpend", "cpc", "Bid", "PAUSED", "adGroupOperation", "adGroupAd",
                  "negative", "BROAD", "EXACT"):
        assert field not in sent
    verb = "validated (not added)" if validate_only else "added"
    assert f"keywords for {NAMES[0]} (ENABLED): 11 {verb}" in caplog.text
    assert f"keywords for deal spotter (PAUSED): 5 {verb}" in caplog.text
    assert f"keywords for review checker (PAUSED): 3 {verb}" in caplog.text
    assert f"{verb} 19 PHRASE keyword(s) with no bid" in caplog.text
    assert "keywords in deal spotter: no score yet x1" in caplog.text  # Google's grades after
    ours = " ".join(r.getMessage() for r in caplog.records if r.name == ads_setup.log.name)
    for secret in (CID, "/adGroups/", "FAKE_", "Bearer"):
        assert secret not in ours


def test_add_keywords_rerun_with_every_keyword_present_writes_nothing(caplog):
    api = API(existing=["PAUSED"], groups=NAMES, keywords={n: COPY[n]["keywords"] for n in NAMES})
    with caplog.at_level(logging.INFO):
        assert api.add() == 0
    assert api.mutates == [] and "all 44 keywords already present; nothing added" in caplog.text


def test_add_keywords_skips_a_missing_ad_group(caplog):
    api = API(existing=["ENABLED"], groups=NAMES[:2], keywords={n: COPY[n]["keywords"][:k] for n, k in LIVE.items()})
    with caplog.at_level(logging.INFO):
        assert api.add() == 0
    assert "no ad group review checker in the campaign; skipped" in caplog.text
    assert [len(texts) for texts in adds(api.mutates[1]).values()] == [11, 5]


def test_add_keywords_without_the_campaign_or_after_a_rejection_adds_nothing(caplog):
    api = API(groups=NAMES)
    assert api.add() == 1
    assert api.mutates == [] and "campaign not found; nothing added" in caplog.text
    api = API(existing=["ENABLED"], groups=NAMES, reject=True)
    assert api.add() == 1
    assert [m.get("validateOnly") for m in api.mutates] == [True] and "failed, nothing added" in caplog.text
    api = API(existing=["ENABLED"], groups=NAMES)
    assert api.add(config(keywords=["amazon deal checker"])) == 1  # plan() rules apply
    assert api.requests == []
