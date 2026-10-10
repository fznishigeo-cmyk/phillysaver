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


class TestGrantedSourceImages:
    """Image display is permission-gated: only sources whose written grant
    covers their images (image_permission) ever contribute one, and a
    post-level image never lands on a multi-deal roundup's cards."""

    HTML = ('<p>Deal! <img src="https://feeds.feedburner.com/~r/x/pix.gif">'
            '<img src="https://hip2save.com/wp-content/mixer.jpg" alt="m">'
            " Martha Stewart mixer $71.99</p>")

    def test_first_real_image_skips_trackers(self):
        from dealradar.fetch.rss import _first_image
        assert _first_image(self.HTML) == \
            "https://hip2save.com/wp-content/mixer.jpg"
        assert _first_image("<p>no images</p>") == ""
        assert _first_image('<img src="https://a.example/logo.svg">') == ""

    def test_config_flag_parses_and_defaults_off(self):
        from dealradar.config import _source_from_dict
        base = {"name": "x", "method": "rss", "url": "https://x.example/feed/"}
        assert _source_from_dict(base).image_permission is False
        assert _source_from_dict({**base, "image_permission": True
                                  }).image_permission is True

    def test_roundup_posts_lose_the_post_level_image(self):
        from dealradar.parse.extractor import _post_for_deals
        from dealradar.models import RawPost
        post = RawPost(source_name="s", uid="1", url="https://s.example/p",
                       title="t", text="x", published_at=None, fetched_at=NOW,
                       image_url="https://s.example/img.jpg")
        assert _post_for_deals(post, 1).image_url == "https://s.example/img.jpg"
        assert _post_for_deals(post, 3).image_url == ""
        assert _post_for_deals(post, 0).image_url == ""


class TestEnrichmentTrigger:
    def test_priceless_long_summary_triggers_enrichment(self):
        """Live case: Hip2Save's 800-char teaser for a 63%-off lounge set
        carried no price, looked "complete" to the length heuristic, and
        the deal was lost. No '$' in a deal post's body => fetch the
        article."""
        from dealradar.fetch.rss import _needs_enrichment
        teaser = ("This cozy Ekouaer lounge set is such a steal right now at "
                  "Amazon and makes the perfect gift idea for the holidays. "
                  "It comes in tons of colors and sizes and shoppers rave "
                  "about how soft it is. ") * 5  # long, but zero prices
        assert _needs_enrichment(teaser, teaser) is True

    def test_priced_long_summary_is_left_alone(self):
        from dealradar.fetch.rss import _needs_enrichment
        body = ("Grab this Ekouaer lounge set for just $14.99 (regularly "
                "$39.99) with promo code 5HXNMK18 at checkout. ") * 12
        assert _needs_enrichment(body, body) is False
