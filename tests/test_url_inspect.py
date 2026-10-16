"""Search Console URL Inspection: request shape, parsing and the home-page flag (offline)."""
from dealradar.growth import google
from dealradar.growth.inspect import lines
from tests.test_workflows import WORKFLOWS, load_workflow, single_job


class Resp:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class Client:
    def __init__(self, body):
        self.body, self.calls = body, []

    def post(self, url, headers=None, json=None):
        self.calls.append((url, headers, json))
        return Resp(self.body)


def test_inspect_url_request_and_parse():
    body = {"inspectionResult": {"indexStatusResult": {
        "verdict": "NEUTRAL", "coverageState": "Discovered - currently not indexed",
        "robotsTxtState": "ALLOWED", "indexingState": "INDEXING_ALLOWED", "pageFetchState": "SUCCESSFUL",
        "googleCanonical": "", "lastCrawlTime": ""}}}
    client = Client(body)
    out = google.inspect_url(client, "tok", "sc-domain:wowpriced.com", "https://wowpriced.com/")
    url, headers, payload = client.calls[0]
    assert url == google.SC_INSPECT and headers == {"Authorization": "Bearer tok"}
    assert payload == {"inspectionUrl": "https://wowpriced.com/", "siteUrl": "sc-domain:wowpriced.com",
                       "languageCode": "en-US"}
    assert out["coverageState"] == "Discovered - currently not indexed" and out["verdict"] == "NEUTRAL"
    text = lines([out], "https://wowpriced.com/")
    assert text[-1].startswith("HOME PAGE NOT INDEXED AS ITSELF: Discovered")


def test_indexed_home_is_quiet_and_wrong_canonical_is_flagged():
    home = "https://wowpriced.com/"
    ok = {"url": home, **{k: "" for k in google.INSPECT_FIELDS}, "verdict": "PASS", "googleCanonical": home}
    assert not any(l.startswith("HOME PAGE") for l in lines([ok], home))
    other = dict(ok, googleCanonical="https://wowpriced.com/about")
    assert lines([other], home)[-1].startswith("HOME PAGE NOT INDEXED AS ITSELF")


def test_workflow_is_read_only_and_daily():
    doc = load_workflow(WORKFLOWS / "seo-inspect.yml")
    assert doc["permissions"] == {"contents": "read"}
    job = single_job(doc)
    assert job["timeout-minutes"] == 5
    run = next(s for s in job["steps"] if s.get("name") == "Inspect key pages in Google's index")
    assert run["env"] == {"GOOGLE_SA_JSON": "${{ secrets.GOOGLE_SA_JSON }}"}
    assert "python -m dealradar.growth.inspect" in run["run"]
