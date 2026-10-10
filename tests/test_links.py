"""Deal links: anchor hrefs survive HTML cleaning; the site always links.

Live incident: Slickdeals posts carry the product URL only inside <a href>,
the old cleaner dropped it, and deals rendered with no hyperlink at all.
"""

from __future__ import annotations

from datetime import datetime, timezone

from dealradar.publish.site import _serialize_deal
from dealradar.util import html_to_text

NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)


class TestAnchorHrefsSurvive:
    def test_product_link_kept_after_anchor_text(self):
        html = ('<p>Grab the <a href="https://www.amazon.com/dp/B0X">MERACH '
                "W50 Walking Pad</a> for $199.99 with code ADMLRJ88.</p>")
        text = html_to_text(html)
        assert "MERACH W50 Walking Pad (https://www.amazon.com/dp/B0X)" in text
        assert "$199.99" in text

    def test_non_http_and_missing_hrefs_add_nothing(self):
        assert html_to_text('<a href="javascript:x()">click</a>') == "click"
        assert html_to_text("<a>bare anchor</a>") == "bare anchor"
        assert html_to_text('<a href="/relative/path">rel</a>') == "rel"

    def test_links_inside_skipped_tags_stay_dropped(self):
        html = '<script><a href="https://evil.example">x</a></script>ok'
        assert html_to_text(html) == "ok"


class TestSiteAlwaysLinks:
    def _deal(self, **over):
        base = {
            "dedupe_key": "si|amazon|widget", "store": "Amazon",
            "item": "Widget", "status": "alive", "flags": [],
            "conditions": [], "sightings": [
                {"source_name": "slickdeals_frontpage",
                 "source_url": "https://slickdeals.net/f/123-widget",
                 "seen_at": "2026-10-10T11:00:00+00:00",
                 "final_price": 9.99}],
        }
        base.update(over)
        return base

    def test_canonical_url_wins(self):
        d = self._deal(canonical_url="https://www.amazon.com/dp/B0X")
        assert _serialize_deal(d, NOW)["url"] == "https://www.amazon.com/dp/B0X"

    def test_falls_back_to_source_post(self):
        d = self._deal(canonical_url="")
        assert _serialize_deal(d, NOW)["url"] == \
            "https://slickdeals.net/f/123-widget"

    def test_no_canonical_and_no_sightings_means_no_link(self):
        d = self._deal(canonical_url="", sightings=[])
        assert _serialize_deal(d, NOW)["url"] == ""
