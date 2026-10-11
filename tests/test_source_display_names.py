"""Source branding stays in deal credits while internal IDs stay stable."""

from datetime import datetime, timezone
from urllib.parse import urlsplit

import yaml

from dealradar.config import ROOT, load_sources
from dealradar.publish.site import _serialize_deal


def test_all_sources_have_brand_names_and_homepages():
    raw = yaml.safe_load((ROOT / "sources.yaml").read_text())
    for source in raw["sources"] + raw["backup_sources"]:
        assert source["display_name"].strip()
        assert source["site"].strip()
    primary, backup = load_sources()
    by_site = {}
    for source in primary + backup:
        assert source.display_name.strip()
        assert urlsplit(source.site).scheme == "https"
        assert urlsplit(source.site).netloc
        assert by_site.setdefault(source.site, source.display_name) == source.display_name


def test_source_loader_optional_branding(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text("""sources:
  - name: old_source
    method: rss
    url: https://example.com/feed
  - name: branded_source
    method: rss
    url: https://example.org/feed
    display_name: Example Brand
    site: https://example.org/
""")
    sources, _ = load_sources(path)
    assert sources[0].display_name == "old_source"
    assert sources[0].site == ""
    assert sources[1].name == "branded_source"
    assert sources[1].display_name == "Example Brand"
    assert sources[1].site == "https://example.org/"


def test_serialized_credits_use_brands_and_preserve_post_urls():
    sightings = [
        {"source_name": "krazy_coupon_lady", "source_url": "https://thekrazycouponlady.com/post"},
        {"source_name": "slickdeals_popular", "source_url": "https://slickdeals.net/f/123"},
        {"source_name": "retired_source", "source_url": "https://example.org/post"},
    ]
    result = _serialize_deal({"sightings": sightings}, datetime.now(timezone.utc))
    assert result["credits"] == [
        {"name": "The Krazy Coupon Lady", "url": sightings[0]["source_url"]},
        {"name": "Slickdeals", "url": sightings[1]["source_url"]},
        {"name": "retired_source", "url": sightings[2]["source_url"]},
    ]
    assert result["url"] == sightings[0]["source_url"]
    assert _serialize_deal({}, datetime.now(timezone.utc))["credits"] == []
