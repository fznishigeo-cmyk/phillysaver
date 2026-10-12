"""Sovrn publishing and reporting, entirely offline."""

import hashlib
import json
from copy import deepcopy
from urllib.parse import parse_qs, quote, urlsplit

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.growth.report import build_report
from dealradar.publish import newsletter
from dealradar.publish.site import _serialize_deal, link_mix, render_site, rewrite_affiliate
from tests.test_publish_site import NOW, make_deal

KEY = "0123456789abcdef0123456789abcdef"


def rewrite(url, **kwargs):
    return rewrite_affiliate(url, "wowpriced-20", sovrn_key=KEY, sovrn_enabled=True, **kwargs)


def test_encoding_cuid_and_rerun_do_not_mutate_or_double_wrap():
    url = "https://www.target.com/p/widget/-/A-123?size=XL&color=blue%20green#options"
    deal = make_deal(canonical_url=url, codes=[])
    before = deepcopy(deal)
    out = _serialize_deal(deal, NOW, sovrn_key=KEY, sovrn_enabled=True)
    cuid = hashlib.sha256(deal["dedupe_key"].encode()).hexdigest()[:16]
    assert out["url"] == f"https://redirect.viglink.com?key={KEY}&u={quote(url, safe='')}&cuid={cuid}"
    assert out["key"] == cuid and out["credits"] == _serialize_deal(deal, NOW)["credits"]
    assert _serialize_deal(deal, NOW, sovrn_key=KEY, sovrn_enabled=True) == out
    assert rewrite(out["url"]) == out["url"] and deal == before


@pytest.mark.parametrize("host", [
    "amazon.com", "www.amazon.com", "smile.amazon.com", "amazon.co.uk",
    "www.amazon.com.au", "music.amazon.de", "amzn.to", "a.co", "link.amazon",
    "wowpriced.com", "www.wowpriced.com", "slickdeals.net", "www.dealnews.com",
    "hip2save.com", "thekrazycouponlady.com", "moneysavingmom.com",
    "passionatepennypincher.com", "couponcravings.com", "couponflick.com",
    "redirect.viglink.com", "sovrn.co",
])
def test_excluded_hosts(host):
    url = f"https://{host}/product?utm_source=credit"
    if host in {"amazon.com", "www.amazon.com", "smile.amazon.com"}:
        assert rewrite(url) == url + "&tag=wowpriced-20"
    else:
        assert rewrite(url) == url
    assert rewrite_affiliate(url, sovrn_enabled=True, sovrn_key=KEY) == url


@pytest.mark.parametrize("url", ["", "/relative", "http://target.com/p/1", "https://[invalid"])
def test_only_absolute_https(url):
    assert rewrite(url) == url


def test_credit_host_and_subdomains_excluded_even_on_another_path():
    url = "https://shop.custom-blog.example/product?utm_source=credit"
    assert rewrite(url, credit_urls=["https://custom-blog.example/post"]) == url
    assert rewrite("https://custom-blog.example.evil/product", credit_urls=["https://custom-blog.example/post"]).startswith("https://redirect.viglink.com?")


def test_source_post_steps_and_override_precedence():
    override = {"walmart.com/12345": "https://sovrn.co/onboarding"}
    plain = make_deal(codes=[])
    assert _serialize_deal(plain, NOW, overrides=override, sovrn_key=KEY, sovrn_enabled=True)["url"] == override["walmart.com/12345"]
    steps = make_deal(codes=["SAVE10"])
    out = _serialize_deal(steps, NOW, overrides=override, sovrn_key=KEY, sovrn_enabled=True)
    assert out["url"] == steps["sightings"][0]["source_url"]
    assert out["needs_steps"] and out["codes"] == ["SAVE10"]
    assert _serialize_deal(dict(plain, canonical_url=""), NOW, sovrn_key=KEY, sovrn_enabled=True)["url"] == plain["sightings"][0]["source_url"]
    blog = dict(plain, canonical_url="https://hip2save.com/product")
    assert _serialize_deal(blog, NOW, sovrn_key=KEY, sovrn_enabled=True)["url"] == blog["canonical_url"]


@pytest.mark.parametrize("param", [
    "irgwc", "afsrc", "sourceid", "veh", "wmlspartner", "affiliates_ad_id",
    "campaign_id", "sharedid", "clickid", "irclickid", "cjevent", "cjdata",
    "ranMID", "ranEAID", "ranSiteID", "siteID", "sscid", "utm_source", "UTM_custom",
])
def test_strip_each_tracking_family_keep_product_options(param):
    url = f"https://macys.com/product?{param}=third-party&size=XL&pid=123&size=L&blank=&variant=red%26blue"
    merchant = parse_qs(urlsplit(rewrite(url)).query)["u"][0]
    assert merchant == "https://macys.com/product?size=XL&pid=123&size=L&blank=&variant=red%26blue"


@pytest.mark.parametrize("enabled,key", [(False, KEY), (True, ""), (True, "bad"), (True, KEY.upper())])
def test_disabled_or_invalid_key_leaves_url_untouched(enabled, key):
    url = "https://target.com/product?utm_source=credit"
    assert rewrite_affiliate(url, sovrn_enabled=enabled, sovrn_key=key) == url


@pytest.mark.parametrize("key", ["bad-key-PRIVATE", "x" * 31, "f" * 33, KEY[:-1] + "g"])
def test_config_invalid_key_warns_without_echoing_it(tmp_path, caplog, key):
    path = tmp_path / "config.yaml"
    path.write_text(f'affiliate:\n  sovrn_enabled: true\n  sovrn_key: "{key}"\n')
    cfg = load_config(path)
    assert cfg.affiliate_sovrn_key == ""
    assert "affiliate.sovrn_key ignored" in caplog.text and key not in caplog.text


@pytest.mark.parametrize("pasted", [KEY.upper(), " " + KEY + " ", "'" + KEY + "'", KEY + "\n"])
def test_config_forgives_a_pasted_keys_spaces_quotes_and_capitals(tmp_path, monkeypatch, pasted):
    monkeypatch.setenv("SOVRN_API_KEY", pasted)
    path = tmp_path / "config.yaml"
    path.write_text("affiliate:\n  sovrn_enabled: true\n")
    assert load_config(path).affiliate_sovrn_key == KEY


def test_config_switch_and_valid_key(tmp_path, caplog):
    path = tmp_path / "config.yaml"
    path.write_text(f'affiliate:\n  sovrn_enabled: true\n  sovrn_key: "{KEY}"\n')
    cfg = load_config(path)
    assert cfg.affiliate_sovrn_enabled and cfg.affiliate_sovrn_key == KEY
    path.write_text('affiliate:\n  sovrn_enabled: false\n  sovrn_key: ""\n')
    cfg = load_config(path)
    assert not cfg.affiliate_sovrn_enabled and not cfg.affiliate_sovrn_key
    # Shipped off until a probe confirms the redirect works for a site under review.
    assert not caplog.text and not load_config().affiliate_sovrn_enabled


def test_emails_untouched_with_site_sovrn_enabled():
    for url in ("https://target.com/product?utm_source=credit", "https://amazon.com/dp/B012345678"):
        assert newsletter._deal_url({"canonical_url": url}) == url
    assert newsletter._deal_url({"sightings": [{"source_url": "https://hip2save.com/post"}]}) == "https://hip2save.com/post"


def test_render_uses_sovrn_in_public_member_and_seo_payloads(tmp_path):
    cfg = AppConfig(affiliate_sovrn_key=KEY, affiliate_sovrn_enabled=True,
                    custom_domain="wowpriced.com", paywall_open_access=True)
    db = DB(tmp_path / "state.db")
    result = render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=[make_deal(codes=[])])
    db.close()
    doc = json.loads((tmp_path / "site/deals.json").read_text())
    url = doc["deals"][0]["url"]
    assert url.startswith("https://redirect.viglink.com?")
    assert result["full_payload"]["deals"][0]["url"] == url
    assert result["link_mix"]["sovrn"] == 1
    html = next((tmp_path / "site/deal").glob("*/index.html")).read_text()
    assert url.replace("&", "&amp;") in html


def test_link_mix_and_morning_report():
    deals = [{"status": "alive", "url": rewrite("https://target.com/product")},
             {"status": "alive", "url": "https://sovrn.co/onboarding"},
             {"status": "alive", "url": "https://macys.com/product"}]
    mix = link_mix(deals)
    assert mix["sovrn"] == 2 and mix["other_store"] == 1
    assert mix["by_store"] == {"macys.com": 1}
    health = dict(link_mix=mix, warnings=[], photos=0, live=3, coverage=0,
                  missing_stores=[], missing_sources=[], broken=0, checked=0,
                  broken_stores=[], age_minutes=1, locked_count=0,
                  home_status=200, home_content=True)
    _, text = build_report(AppConfig(), {"site_health": health}, {}, NOW.date())
    assert "2 via Sovrn" in text


def test_link_key_comes_from_the_github_secret(tmp_path, monkeypatch):
    from dealradar.config import load_config
    p = tmp_path / "c.yaml"
    p.write_text("affiliate:\n  sovrn_enabled: true\n")
    monkeypatch.setenv("SOVRN_API_KEY", "0123456789abcdef0123456789abcdef")
    assert load_config(p).affiliate_sovrn_key == "0123456789abcdef0123456789abcdef"
    monkeypatch.setenv("SOVRN_API_KEY", "not-a-key")
    assert load_config(p).affiliate_sovrn_key == ""


def test_pipeline_workflows_pass_the_sovrn_secret():
    from pathlib import Path
    for wf in ("fast.yml", "hourly.yml"):
        text = (Path(__file__).resolve().parent.parent / ".github/workflows" / wf).read_text()
        assert "SOVRN_API_KEY: ${{ secrets.SOVRN_API_KEY2 || secrets.SOVRN_API_KEY }}" in text
