"""eBay Partner Network: every https ebay.com Get deal link carries our campaign."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

from dealradar.config import load_config
from dealradar.publish.site import _link_destination, link_mix, rewrite_affiliate
from tests.test_sovrn_links import KEY

CAMPID = "5339221823"


def _q(url):
    return dict(parse_qsl(urlsplit(url).query))


def test_ebay_link_carries_our_campaign_replacing_a_sources():
    url = ("https://www.ebay.com/itm/1234567890?var=55&campid=1111111111&mkcid=1"
           "&mkrid=711-53200-19255-0&toolid=10001&mkevt=1&customid=theirs")
    out = rewrite_affiliate(url, "wowpriced-20", ebay_campid=CAMPID, cuid="abc123",
                            sovrn_key=KEY, sovrn_enabled=True)
    parts = urlsplit(out)
    assert parts.hostname == "www.ebay.com" and parts.path == "/itm/1234567890"
    q = _q(out)
    assert q == {"var": "55", "mkcid": "1", "mkrid": "711-53200-19255-0", "siteid": "0",
                 "campid": CAMPID, "customid": "abc123", "toolid": "10001", "mkevt": "1"}
    assert urlsplit(out).query.count("campid=") == 1


def test_other_ebay_sites_http_and_no_campaign_are_untouched():
    for url in ("https://www.ebay.co.uk/itm/1", "http://www.ebay.com/itm/1"):
        assert rewrite_affiliate(url, ebay_campid=CAMPID) == url
    assert rewrite_affiliate("https://www.ebay.com/itm/1") == "https://www.ebay.com/itm/1"
    assert rewrite_affiliate("https://www.ebay.com/itm/1", ebay_campid="12345") == \
        "https://www.ebay.com/itm/1"


def test_epn_link_earns_and_a_sources_campaign_does_not():
    ours = rewrite_affiliate("https://www.ebay.com/itm/9", ebay_campid=CAMPID)
    theirs = "https://www.ebay.com/itm/9?campid=2222222222&mkevt=1"
    assert _link_destination({"url": ours}, "", (), CAMPID) == ("ebay_epn", "ebay.com", True)
    assert _link_destination({"url": theirs}, "", (), CAMPID)[::2] == ("other_store", False)
    mix = link_mix([{"url": ours, "status": "alive"}, {"url": theirs, "status": "alive"}],
                   ebay_campid=CAMPID)
    assert mix["ebay_epn"] == 1 and mix["earning"] == 1 and mix["not_earning"] == 1


def test_production_config_has_the_campaign():
    assert load_config().affiliate_ebay_campaign_id == CAMPID
