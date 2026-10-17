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

    def test_plain_price_links_the_product(self):
        d = self._deal(canonical_url="https://www.amazon.com/dp/B0X",
                       stack_steps=["Sale $9.99"])
        out = _serialize_deal(d, NOW)
        assert out["url"] == "https://www.amazon.com/dp/B0X"
        assert out["needs_steps"] is False and out["how"] == []

    def test_a_code_links_the_post_that_explains_it(self):
        # Live: a $2.99 thermometer whose Amazon page said $8.99.
        d = self._deal(canonical_url="https://www.amazon.com/dp/B0X",
                       codes=["TEMP70"], stack_steps=["Sale $8.99", "Code TEMP70"])
        out = _serialize_deal(d, NOW)
        assert out["needs_steps"] is True
        assert out["url"] == "https://slickdeals.net/f/123-widget"
        assert out["how"] == ["Sale $8.99", "Code TEMP70"]
        assert out["codes"] == ["TEMP70"]
        assert "Code TEMP70." not in out["headline"]  # the how box shows it

    def test_steps_and_conditions_link_the_post(self):
        for over in ({"stack_steps": ["Clip the 20% coupon"]},
                     {"conditions": ["subscribe_save"]},
                     {"conditions": ["in_cart"]},
                     {"rebates": ["$5 mail-in rebate"]}):
            d = self._deal(canonical_url="https://www.amazon.com/dp/B0X", **over)
            out = _serialize_deal(d, NOW)
            assert out["needs_steps"] is True, over
            assert out["url"] == "https://slickdeals.net/f/123-widget", over

    def test_conditions_say_how_when_the_post_gave_no_steps(self):
        d = self._deal(conditions=["subscribe_save", "targeted", "ymmv"])
        assert _serialize_deal(d, NOW)["how"] == [
            "Choose Subscribe & Save", "Price may vary by account"]

    def test_prime_only_alone_links_the_product(self):
        d = self._deal(canonical_url="https://www.amazon.com/dp/B0X",
                       conditions=["prime_only"])
        assert _serialize_deal(d, NOW)["url"] == "https://www.amazon.com/dp/B0X"

    def test_steps_without_a_post_keep_the_product_link(self):
        d = self._deal(canonical_url="https://www.amazon.com/dp/B0X",
                       codes=["TEMP70"], sightings=[])
        assert _serialize_deal(d, NOW)["url"] == "https://www.amazon.com/dp/B0X"

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


class TestAmazonAffiliateTag:
    """Operator (2026-10-12): affiliate commissions are the income. With a
    Store ID every https amazon.com link carries it; nothing else changes."""

    def test_tags_amazon_links_only(self):
        from dealradar.publish.site import rewrite_affiliate as r
        tag = "wowpriced-20"
        assert r("https://www.amazon.com/dp/B0ABCDEF12", tag) == "https://www.amazon.com/dp/B0ABCDEF12?tag=wowpriced-20"
        assert r("https://www.amazon.com/dp/B0ABCDEF12?th=1&tag=other-20", tag) == \
            "https://www.amazon.com/dp/B0ABCDEF12?th=1&tag=wowpriced-20"
        for url in ("https://www.walmart.com/ip/1", "http://www.amazon.com/dp/B0ABCDEF12",
                    "https://www.amazon.com.evil.com/dp/X", "https://slickdeals.net/f/1", ""):
            assert r(url, tag) == url
        assert r("https://www.amazon.com/dp/B0ABCDEF12") == "https://www.amazon.com/dp/B0ABCDEF12"

    def test_site_links_and_disclosure_follow_the_store_id(self, tmp_path):
        import json
        from dealradar.config import load_config
        from dealradar.db import DB
        from dealradar.publish.site import render_site
        from tests.test_publish_site import NOW, make_deal

        cfg = load_config()
        cfg.keepa_click_price_rule = "off"  # affiliate fixture recorded before task 275
        deals = [make_deal(dedupe_key="a", canonical_url="https://www.amazon.com/dp/B0ABCDEF12", store="Amazon",
                           codes=[], stack=[], conditions=[])]
        for tag, out in (("", tmp_path / "off"), ("wowpriced-20", tmp_path / "on")):
            cfg.affiliate_amazon_tag = tag
            render_site(DB(tmp_path / f"{tag or 'off'}.db"), cfg, NOW, out_dir=out, deals=deals)
            doc = json.loads((out / "deals.json").read_text())
            (deal,) = doc["deals"]
            assert not deal["needs_steps"]
            url = deal["url"]
            if tag:
                assert "As an Amazon Associate, wowpriced earns from qualifying purchases." in doc["meta"]["disclosure"]
                assert url == "https://www.amazon.com/dp/B0ABCDEF12?tag=wowpriced-20"
            else:
                assert "tag=" not in url and doc["meta"]["disclosure"] == cfg.ftc_disclosure

    def test_code_deals_go_to_amazon_with_our_tag_and_keep_the_steps(self):
        """Operator (2026-10-12): a code/coupon deal links the source post,
        except when its store link carries our Amazon tag; the card keeps the
        code and the "via" link to the post either way."""
        from tests.test_publish_site import NOW, make_deal
        sightings = [{"source_name": "Slickdeals", "source_url": "https://slickdeals.net/f/1",
                      "seen_at": "2026-10-09T12:00:00+00:00", "final_price": 10.0}]
        amazon = make_deal(canonical_url="https://www.amazon.com/dp/B0ABCDEF12", store="Amazon",
                           codes=["SAVE10"], sightings=sightings)
        tagged = _serialize_deal(amazon, NOW, "wowpriced-20")
        assert tagged["needs_steps"] and tagged["codes"] == ["SAVE10"]
        assert tagged["url"] == "https://www.amazon.com/dp/B0ABCDEF12?tag=wowpriced-20"
        assert tagged["credits"][0]["url"] == "https://slickdeals.net/f/1"
        # No Store ID, or another store: the post that explains the steps.
        assert _serialize_deal(amazon, NOW, "")["url"] == "https://slickdeals.net/f/1"
        walmart = dict(amazon, canonical_url="https://www.walmart.com/ip/1", store="Walmart")
        assert _serialize_deal(walmart, NOW, "wowpriced-20")["url"] == "https://slickdeals.net/f/1"

    def test_link_override_sends_one_store_page_to_its_network_link(self):
        """Sovrn onboarding (2026-10-12): one Walmart page's Get deal goes to
        the operator's sovrn.co link; slug and tracking query never matter."""
        from tests.test_publish_site import NOW, make_deal
        from dealradar.config import load_config
        o = {"walmart.com/222918738": "https://sovrn.co/168n3ev"}
        d = make_deal(canonical_url="https://www.walmart.com/ip/BestOffice-Shelf/222918738?wmlspartner=x",
                      store="Walmart", codes=[])
        assert _serialize_deal(d, NOW, "wowpriced-20", o)["url"] == "https://sovrn.co/168n3ev"
        other = dict(d, canonical_url="https://www.walmart.com/ip/111")
        assert _serialize_deal(other, NOW, "wowpriced-20", o)["url"] == "https://www.walmart.com/ip/111"
        assert load_config().affiliate_link_overrides == o

    def test_emails_never_carry_the_tag(self):
        from dealradar.publish import newsletter
        assert "tag=" not in newsletter._deal_url({"canonical_url": "https://www.amazon.com/dp/B0ABCDEF12"})

    def test_config_reads_and_validates_the_store_id(self, tmp_path):
        from dealradar.config import load_config
        p = tmp_path / "c.yaml"
        p.write_text("affiliate:\n  amazon_tag: wowpriced-20\n  disclosure: X\n")
        c = load_config(p)
        assert (c.affiliate_amazon_tag, c.affiliate_disclosure) == ("wowpriced-20", "X")
        p.write_text("affiliate:\n  amazon_tag: 'bad tag&x=1'\n")
        assert load_config(p).affiliate_amazon_tag == ""
        assert load_config().affiliate_amazon_tag == "wowpriced-20"  # the operator's Store ID (2026-10-12)
