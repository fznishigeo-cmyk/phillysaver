"""Tests for dealradar.publish.site — fully offline, explicit deals lists."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.publish.site import render_site, rewrite_affiliate
from dealradar.util import iso

NOW = datetime(2026, 10, 9, 14, 0, 0, tzinfo=timezone.utc)

DISCLOSURE = (
    "Disclosure: some outbound links credit the deal source and may be "
    "affiliate links. Prices change fast and are not guaranteed."
)


@pytest.fixture()
def db(tmp_path):
    database = DB(tmp_path / "site_test.db")
    yield database
    database.close()


@pytest.fixture()
def cfg(tmp_path):
    return AppConfig(
        ftc_disclosure=DISCLOSURE,
        site_dir=str(tmp_path / "site_default"),
        min_pct=70.0,
        min_abs_savings=5.0,
    )


def make_deal(**overrides):
    seen = "2026-10-09T12:00:00+00:00"  # 2 h before NOW
    deal = {
        "dedupe_key": "walmart|lego set",
        "store": "Walmart",
        "item": "LEGO Star Wars Set",
        "brand": "LEGO",
        "canonical_url": "https://www.walmart.com/ip/12345",
        "final_price": 10.0,
        "regular_price": 50.0,
        "pct_at_checkout": 80.0,
        "pct_with_rebates": None,
        "abs_savings": 40.0,
        "basis": "quoted_regular",
        "flags": [],
        "codes": ["SAVE10"],
        "stack_steps": [],
        "rebates": [],
        "deferred_value": "",
        "conditions": ["online only"],
        "online_only": "true",
        "shipping_cost_or_threshold": "",
        "quantity_or_unit": "",
        "region": "US",
        "currency": "USD",
        "expires_at": "",
        "verify_tier": "corroborated",
        "status": "alive",
        "first_seen_at": seen,
        "last_seen_at": seen,
        "confidence": 0.9,
        "score": 92.5,
        "sightings": [
            {"source_name": "Slickdeals", "source_url": "https://slickdeals.net/f/1",
             "seen_at": seen, "final_price": 10.0},
            {"source_name": "hip2save", "source_url": "https://hip2save.com/p/2",
             "seen_at": seen, "final_price": 10.0},
        ],
    }
    deal.update(overrides)
    return deal


def test_rewrite_affiliate_is_identity():
    url = "https://www.walmart.com/ip/12345?athcpid=abc"
    assert rewrite_affiliate(url) == url
    assert rewrite_affiliate("") == ""


def test_render_site_writes_all_four_files(db, cfg, tmp_path):
    out = tmp_path / "site"
    deals = [make_deal(), make_deal(dedupe_key="target|mug", store="Target",
                                    item="Coffee Mug", status="dead",
                                    verify_tier="unverified")]
    result = render_site(db, cfg, NOW, out_dir=out, deals=deals,
                         last_run={"finished_at": iso(NOW)})
    assert result == {"published": 2, "out_dir": str(out)}
    for name in ("deals.json", "health.json", "index.html", "health.html"):
        assert (out / name).exists(), name


def test_deals_json_has_disclosure_credit_badge_age(db, cfg, tmp_path):
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=[make_deal()],
                last_run={"finished_at": iso(NOW)})
    doc = json.loads((out / "deals.json").read_text(encoding="utf-8"))

    meta = doc["meta"]
    assert meta["disclosure"] == DISCLOSURE
    assert meta["generated_at"] == iso(NOW)
    assert meta["count"] == 1
    assert meta["floors"] == {"min_pct": 70.0, "min_abs_savings": 5.0,
                              "site_show_from_pct": 70.0}
    assert meta["last_run"]["finished_at"] == iso(NOW)

    (deal,) = doc["deals"]
    # credit is required: one entry per sighting with name + url
    assert deal["credits"] == [
        {"name": "Slickdeals", "url": "https://slickdeals.net/f/1"},
        {"name": "hip2save", "url": "https://hip2save.com/p/2"},
    ]
    assert deal["badge"] == "corroborated"
    assert deal["alive"] is True
    assert deal["status"] == "alive"
    assert deal["age_hours"] == pytest.approx(2.0)
    # product link passed through the (identity) affiliate hook
    assert deal["url"] == "https://www.walmart.com/ip/12345"
    # only extracted facts: no raw post text field in the output
    assert "text" not in deal


def test_dead_deal_serialized_as_not_alive(db, cfg, tmp_path):
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out,
                deals=[make_deal(status="dead")], last_run=None)
    doc = json.loads((out / "deals.json").read_text(encoding="utf-8"))
    assert doc["deals"][0]["alive"] is False


def test_zero_deals_still_writes_valid_json_and_templates(db, cfg, tmp_path):
    out = tmp_path / "empty_site"
    result = render_site(db, cfg, NOW, out_dir=out, deals=[])
    assert result["published"] == 0
    doc = json.loads((out / "deals.json").read_text(encoding="utf-8"))
    assert doc["deals"] == []
    assert doc["meta"]["count"] == 0
    assert doc["meta"]["last_run"] == {}
    health = json.loads((out / "health.json").read_text(encoding="utf-8"))
    assert health["sources"] == []
    assert (out / "index.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    assert (out / "health.html").exists()


def test_health_json_tripped_flag(db, cfg, tmp_path):
    out = tmp_path / "site"
    db.record_source_result("good_src", ok=True, error="", items=12, now=NOW)
    db.record_source_result("bad_src", ok=False, error="boom", items=0, now=NOW,
                            trip_threshold=1, cooldown_h=6.0)
    render_site(db, cfg, NOW, out_dir=out, deals=[])
    health = json.loads((out / "health.json").read_text(encoding="utf-8"))
    rows = {r["source_name"]: r for r in health["sources"]}
    assert rows["good_src"]["tripped"] is False
    assert rows["good_src"]["items_last_run"] == 12
    assert rows["bad_src"]["tripped"] is True
    assert rows["bad_src"]["last_error"] == "boom"
    assert health["generated_at"] == iso(NOW)


def test_index_template_references_disclosure_from_meta(db, cfg, tmp_path):
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=[])
    html = (out / "index.html").read_text(encoding="utf-8")
    # The FTC line must come from deals.json meta at render time, not be baked in.
    assert "disclosure" in html
    assert "m.disclosure" in html or "meta.disclosure" in html
    assert DISCLOSURE not in html
    # Required UI affordances live in the template.
    assert "No qualifying deals this hour" in html
    assert 'rel="sponsored noopener"' in html
    assert "health.html" in html
    assert "prefers-color-scheme" in html
    # Self-contained: no external scripts/stylesheets.
    assert "<script src=" not in html
    assert "<link" not in html


def test_custom_domain_writes_cname_every_render(db, cfg, tmp_path):
    """GitHub Pages drops a custom domain unless the deployed artifact
    carries CNAME; the pipeline redeploys every run, so render writes it."""
    out = tmp_path / "site"
    cfg.custom_domain = "wowpriced.com"
    render_site(db, cfg, NOW, out_dir=out, deals=[])
    assert (out / "CNAME").read_text(encoding="utf-8") == "wowpriced.com\n"
    # No domain configured -> no file (plain github.io hosting).
    cfg.custom_domain = ""
    out2 = tmp_path / "site2"
    render_site(db, cfg, NOW, out_dir=out2, deals=[])
    assert not (out2 / "CNAME").exists()


def test_membership_links_reach_page_meta_and_welcome_page(db, cfg, tmp_path):
    out = tmp_path / "site"
    cfg.subscribe_url = "https://buy.stripe.com/test_123"
    cfg.portal_url = "https://billing.stripe.com/p/login/x"
    render_site(db, cfg, NOW, out_dir=out, deals=[])
    doc = json.loads((out / "deals.json").read_text(encoding="utf-8"))
    assert doc["meta"]["subscribe_url"] == "https://buy.stripe.com/test_123"
    assert doc["meta"]["portal_url"] == "https://billing.stripe.com/p/login/x"
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'id="subscribe"' in html and "m.subscribe_url" in html
    welcome = (out / "welcome.html").read_text(encoding="utf-8")
    assert welcome.startswith("<!doctype html>")
    assert "<script src=" not in welcome and "<link" not in welcome


def test_health_template_is_self_contained(db, cfg, tmp_path):
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=[])
    html = (out / "health.html").read_text(encoding="utf-8")
    assert "health.json" in html
    assert "prefers-color-scheme" in html
    assert "<script src=" not in html
    assert "<link" not in html


def test_out_dir_defaults_to_cfg_site_dir(db, cfg):
    result = render_site(db, cfg, NOW, deals=[make_deal()])
    assert result["out_dir"] == cfg.site_dir
    doc = json.loads(
        (Path(cfg.site_dir) / "deals.json").read_text(encoding="utf-8")
    )
    assert doc["meta"]["count"] == 1


class TestLiveHomePage:
    """Meta fields and template hooks behind the rotating, counting-down
    home page (fresh-drops sort, "Next drop in" countdown, floor-derived
    tagline)."""

    @staticmethod
    def _meta(db, cfg, out, last_run):
        render_site(db, cfg, NOW, out_dir=out, deals=[], last_run=last_run)
        return json.loads((out / "deals.json").read_text(encoding="utf-8"))["meta"]

    def test_meta_cadence_follows_run_kind(self, db, cfg, tmp_path):
        fast = self._meta(db, cfg, tmp_path / "fast",
                          {"run_key": "2026-10-09T14:00:fast", "kind": "fast"})
        assert fast["cadence_minutes"] == 10
        hourly = self._meta(db, cfg, tmp_path / "hourly",
                            {"run_key": "2026-10-09T14", "kind": "hourly"})
        assert hourly["cadence_minutes"] == 60
        # The page never guesses the publish time: generated_at is always set.
        assert fast["generated_at"] == hourly["generated_at"] == iso(NOW)

    def test_meta_cadence_defaults_to_fast_lane_without_last_run(self, db, cfg, tmp_path):
        assert self._meta(db, cfg, tmp_path / "none", None)["cadence_minutes"] == 10

    @pytest.fixture()
    def html(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        render_site(db, cfg, NOW, out_dir=out, deals=[])
        return (out / "index.html").read_text(encoding="utf-8")

    def test_index_has_countdown_driven_by_meta(self, html):
        assert 'id="countdown"' in html
        assert "Next drop in" in html and "Loading new drops" in html
        # Publish time and cadence come from deals.json meta, not the clock alone.
        assert "m.generated_at" in html and "m.cadence_minutes" in html
        assert "run_key" in html  # fallback publish time
        # Polling: 30 s while a drop is loading, 5-minute fallback kept.
        assert "30 * 1000" in html and "5 * 60 * 1000" in html
        assert "just dropped" in html  # toast after a new publish

    def test_index_defaults_to_fresh_drops_sort(self, html):
        start = html.index('id="sortsel"')
        options = re.findall(r'<option value="([^"]+)"',
                             html[start:html.index("</select>", start)])
        assert options[0] == "fresh:-1"
        assert options[1:] == ["pct:-1", "abs:-1", "price:1", "age:1"]
        assert 'sortKey: "fresh"' in html
        # Rotation is seeded by the publish (run key), never random.
        assert "Math.random" not in html
        assert "first_seen_at" in html
        # Per-viewer conveniences: every localStorage touch is guarded.
        assert "since your last visit" in html
        for line in html.splitlines():
            if "localStorage." in line:
                assert "try {" in line, line

    def test_index_has_no_hardcoded_floor(self, html):
        # The tagline and empty-state copy derive from meta.floors.min_pct.
        assert "50–90%" not in html and "50-90%" not in html
        # (CSS uses 50% for circles/centering; the copy and script must not.)
        assert "50%" not in html[html.index("</style>"):]
        assert "floors.min_pct" in html
        # "Last run" + disclosure moved to the footer, verbatim from meta.
        assert html.index('id="metaline"') > html.index("<footer>")
        assert "m.disclosure" in html
        assert DISCLOSURE not in html


class TestContentPages:
    """The five static content pages (about, careers, contact, how-it-works,
    hacks) are copied alongside index.html; contact.html gets the operator's
    email substituted in, and the unset placeholder never reaches the site."""

    PAGES = ("about.html", "careers.html", "contact.html",
             "how-it-works.html", "hacks.html")

    def _render(self, db, cfg, out):
        render_site(db, cfg, NOW, out_dir=out, deals=[])
        return {n: (out / n).read_text(encoding="utf-8") for n in self.PAGES}

    def test_pages_written_and_self_contained(self, db, cfg, tmp_path):
        pages = self._render(db, cfg, tmp_path / "site")
        for name, html in pages.items():
            assert html.startswith("<!doctype html>"), name
            assert "<title>" in html and 'name="description"' in html, name
            assert "prefers-color-scheme" in html, name
            assert "<script src=" not in html and "<link" not in html, name
            # Footer links every content page, Home, and the Subscribe button.
            for other in self.PAGES:
                assert f'href="{other}"' in html, (name, other)
            assert 'href="./"' in html, name
            assert "Get every deal by email" in html, name
            assert "subscribe_url" in html, name

    def test_contact_email_substituted_when_configured(self, db, cfg, tmp_path):
        cfg.contact_email = "hello@wowpriced.example"
        html = self._render(db, cfg, tmp_path / "site")["contact.html"]
        assert "mailto:hello@wowpriced.example" in html
        assert "{{CONTACT_EMAIL}}" not in html

    def test_contact_email_blank_when_placeholder(self, db, cfg, tmp_path):
        # AppConfig default is the placeholder; the page then hides its line.
        assert cfg.contact_email == "set-me@example.com"
        html = self._render(db, cfg, tmp_path / "site")["contact.html"]
        assert 'href="mailto:"' in html
        assert "{{CONTACT_EMAIL}}" not in html
        assert "emailline" in html  # the JS hook that hides the empty line

    def test_placeholder_and_competitor_names_never_appear(self, db, cfg, tmp_path):
        pages = self._render(db, cfg, tmp_path / "site")
        for name, html in pages.items():
            assert "set-me@example.com" not in html, name
            low = html.lower()
            for banned in ("krazy", "kcl", "southern savers"):
                assert banned not in low, (name, banned)


class TestSiteNavigation:
    def test_home_footer_links_every_content_page(self, db, cfg, tmp_path):
        from dealradar.publish.site import render_site
        render_site(db, cfg, NOW, out_dir=tmp_path)
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        for page in ("about.html", "how-it-works.html", "hacks.html",
                     "careers.html", "contact.html"):
            assert f'href="{page}"' in html
            assert (tmp_path / page).exists()

    def test_no_page_hardcodes_the_old_floor(self, db, cfg, tmp_path):
        from dealradar.publish.site import render_site
        render_site(db, cfg, NOW, out_dir=tmp_path)
        for path in tmp_path.glob("*.html"):
            text = path.read_text(encoding="utf-8")
            assert "50–90%" not in text and "50-90%" not in text, path.name
