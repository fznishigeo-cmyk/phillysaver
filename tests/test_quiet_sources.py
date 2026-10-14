"""Public source privacy across new/stored deals and every generated page."""

import json

import pytest

from dealradar.publish.site import _serialize_deal, render_site
from tests.test_publish_site import NOW, cfg, db, make_deal
from tests.test_seo_deal_pages import _page, _path


@pytest.mark.parametrize("name,url", [
    ("keepa_amazon_drops", ""),  # new adapter output
    ("Keepa", "https://keepa.com/#!product/1-B0TESTASIN1"),
    ("KEEPA_AMAZON_DROPS", "https://www.keepa.com/#!product/1-B0TESTASIN1"),
    ("historical", "https://KEEPA.com/#!product/1-B0TESTASIN1"),
])
def test_amazon_credit_replaces_new_and_historical_sightings(name, url):
    other = make_deal()["sightings"]
    d = make_deal(sightings=[{"source_name": name, "source_url": url}] + other)
    public = _serialize_deal(d, NOW)
    assert public["credits"] == [{"name": "Amazon price drop", "url": ""}] + \
        _serialize_deal(make_deal(), NOW)["credits"]
    assert d["sightings"][0] == {"source_name": name, "source_url": url}
    assert "keepa" not in json.dumps(public).lower()


@pytest.mark.parametrize("support,open_access", [(True, True), (False, True), (False, False)])
def test_every_published_file_keeps_sources_quiet(db, cfg, tmp_path, support, open_access):
    cfg.support_enabled = support
    cfg.paywall_enabled = True
    cfg.paywall_open_access = open_access
    cfg.paywall_teaser_count = 3
    cfg.custom_domain = "wowpriced.com"
    cfg.affiliate_amazon_tag = "example-20"
    amazon = make_deal(dedupe_key="amazon-new", store="Amazon", item="New blender",
                       canonical_url="https://www.amazon.com/dp/B0TESTASIN1", codes=[],
                       category="kitchen", conditions=[], sightings=[{
                           "source_name": "keepa_amazon_drops",
                           "source_url": "https://keepa.com/#!product/1-B0TESTASIN1"}])
    used = dict(amazon, dedupe_key="amazon-used", item="Used blender", conditions=["used", "open_box"],
                sightings=[{"source_name": "Keepa", "source_url": ""}])
    other = make_deal(dedupe_key="non-amazon", category="kitchen")
    locked = dict(amazon, dedupe_key="amazon-locked", item="Another blender")
    dead = dict(amazon, dedupe_key="amazon-ended", item="Ended blender", status="dead",
                death="confirmed", died_at=NOW.isoformat())
    out = tmp_path / "site"
    result = render_site(db, cfg, NOW, out_dir=out, deals=[amazon, used, other, locked, dead])
    for path in out.rglob("*"):
        if path.is_file():
            content = path.read_bytes().lower()
            assert b"keepa" not in content, path.relative_to(out)
            assert b"srcwall" not in content and b"srcsel" not in content, path.relative_to(out)
    doc = json.loads((out / "deals.json").read_text())
    assert "source_sites" not in doc["meta"]
    assert all("src" not in d for d in doc["deals"])
    assert "keepa" not in json.dumps(result["full_payload"]).lower()
    assert all("src" not in d for d in result["full_payload"]["deals"])
    assert doc["deals"][3].get("locked", False) == (not support and not open_access)
    assert doc["deals"][2]["credits"] == _serialize_deal(other, NOW)["credits"]
    home = (out / "index.html").read_text()
    assert "hours every day" not in home
    assert 'via <a href="https://slickdeals.net/f/1" rel="noopener">Slickdeals</a>' in home
    assert 'via <a href="https://hip2save.com/p/2" rel="noopener">Hip2Save</a>' in home
    assert '<span class="credit">via Amazon price drop</span>' in home
    for deal, basis in [(amazon, "90-day average price on Amazon"), (used, "new price on Amazon")]:
        page = _page(out, _path(deal)).read_text()
        assert basis in page
        assert '<p class="detail-sources">Sources: Amazon price drop</p>' in page
        assert '<a href="">Amazon price drop' not in page
        assert '?tag=example-20' in page
    assert '<p class="detail-sources">Sources: Amazon price drop</p>' in _page(out, _path(dead)).read_text()
    other_page = _page(out, _path(other)).read_text()
    assert '<p class="detail-sources">Sources: <a href="https://slickdeals.net/f/1" rel="noopener">Slickdeals</a>, ' in other_page
    assert '<a href="https://hip2save.com/p/2" rel="noopener">Hip2Save</a></p>' in other_page
    assert "recorded prices" in (out / "about.html").read_text()
    assert "Review the included accessories" in (out / "deals/used-open-box/index.html").read_text()
