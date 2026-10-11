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
    assert result["published"] == 2 and result["out_dir"] == str(out)
    for name in ("deals.json", "index.html"):
        assert (out / name).exists(), name
    for name in ("health.json", "health.html"):  # operator-only, never published
        assert not (out / name).exists(), name


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
        {"name": "Hip2Save", "url": "https://hip2save.com/p/2"},
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
    assert result["health"]["sources"] == []
    assert (out / "index.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    assert not (out / "health.html").exists()


def test_health_json_tripped_flag(db, cfg, tmp_path):
    out = tmp_path / "site"
    db.record_source_result("good_src", ok=True, error="", items=12, now=NOW)
    db.record_source_result("bad_src", ok=False, error="boom", items=0, now=NOW,
                            trip_threshold=1, cooldown_h=6.0)
    health = render_site(db, cfg, NOW, out_dir=out, deals=[])["health"]
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
    assert "No qualifying deals right now" in html
    assert 'rel="sponsored noopener"' in html
    assert "health.html" not in html  # source health is never public
    assert "prefers-color-scheme" in html
    # Self-contained: no external scripts/stylesheets.
    assert "<script src=" not in html
    assert 'rel="stylesheet"' not in html


def test_custom_domain_writes_cname_every_render(db, cfg, tmp_path):
    """CNAME is written on every render (Actions-based Pages deploys ignore
    it; the domain lives in Settings -> Pages), never without a domain."""
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
    # The header button leads into sign-up and the trial; the Payment Link
    # only reaches the page as checkout's fallback (me.subscribe_url).
    assert 'id="subscribe"' in html and "m.subscribe_url" not in html
    welcome = (out / "welcome.html").read_text(encoding="utf-8")
    assert welcome.startswith("<!doctype html>")
    assert "<script src=" not in welcome and 'rel="stylesheet"' not in welcome


def test_source_health_is_private_and_reaches_the_run_summary(db, cfg, tmp_path):
    """Operator call: source health is never on the public site; a stale
    copy from an older publish is removed; the run summary gets the table."""
    from dealradar.publish.site import health_markdown

    out = tmp_path / "site"
    out.mkdir()
    (out / "health.json").write_text("{}")
    (out / "health.html").write_text("old")
    db.record_source_result("bad|src", ok=False, error="http_403\nnext", items=0, now=NOW,
                            trip_threshold=1, cooldown_h=6.0)
    res = render_site(db, cfg, NOW, out_dir=out, deals=[])
    assert not (out / "health.json").exists() and not (out / "health.html").exists()
    for page in out.glob("*.html"):
        assert "health.html" not in page.read_text(encoding="utf-8"), page.name
    md = health_markdown(res["health"])
    assert md.startswith("## Source health (1 sources, 1 tripped)")
    assert "| bad\\|src | tripped |" in md and "http_403 next" in md


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
        assert "Next drop in" in html and "Dropping…" in html
        # The old open-ended wait is gone: a drop never sits for minutes.
        assert "Loading new drops" not in html
        # Publish time and cadence come from deals.json meta, not the clock alone.
        assert "m.published_at" in html and "m.generated_at" in html
        assert "m.cadence_minutes" in html
        assert "run_key" in html  # fallback publish time
        # Quiet 30 s poll after a missed mark, 5-minute fallback kept.
        assert "30 * 1000" in html and "5 * 60 * 1000" in html
        assert "just landed" in html  # true-count toast after a new publish

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
            assert "<script src=" not in html and 'rel="stylesheet"' not in html, name
            # Footer links every content page, Home, and the Subscribe button.
            for other in self.PAGES:
                assert f'href="{other}"' in html, (name, other)
            assert 'href="./"' in html, name
            assert "Start 7-day free trial" in html, name
            # Membership starts in the home page's sign-up and trial flow;
            # no content page links the Payment Link.
            assert '"./#signup"' in html and "api_url" in html, name
            assert "subscribe_url" not in html, name

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


class TestPaywallTeaser:
    """docs/paywall.md: with the paywall on, deals.json is a teaser (top N
    alive deals full, the rest locked stubs, dead deals unchanged) and
    render_site hands the full payload back for the member upload."""

    STUB_KEYS = {"locked", "key", "pct_at_checkout", "store", "category",
                 "first_seen_at", "image_url", "status", "conditions", "src"}

    @staticmethod
    def _deals(n):
        # Distinctive names, prices and URLs so a leak is unmistakable.
        return [make_deal(dedupe_key=f"walmart|zz-{i}", item=f"ZZSECRET-ITEM-{i}",
                          canonical_url=f"https://www.walmart.com/ip/zz{i}",
                          image_url=f"https://i5.walmartimages.com/zzimg{i}.jpg",
                          final_price=1000.0 + i, regular_price=5000.0 + i,
                          score=100.0 - i)
                for i in range(n)]

    def test_teaser_file_never_holds_a_locked_deals_facts(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        cfg.paywall_enabled = True
        cfg.paywall_teaser_count = 3
        cfg.paywall_trial_days = 7
        cfg.paywall_api_url = "https://api.example.workers.dev"
        cfg.paywall_google_client_id = "cid-123"
        deals = self._deals(5) + [make_deal(dedupe_key="target|mug", item="ZZSECRET-MUG",
                                            status="dead")]
        res = render_site(db, cfg, NOW, out_dir=out, deals=deals)
        text = (out / "deals.json").read_text(encoding="utf-8")
        for i in (0, 1, 2):
            assert f"ZZSECRET-ITEM-{i}" in text
        for i in (3, 4):  # locked: no name, price, link or headline anywhere
            assert f"ZZSECRET-ITEM-{i}" not in text
            assert f"walmart.com/ip/zz{i}" not in text
            assert f"100{i}.0" not in text and f"1,00{i}.00" not in text
            assert f"500{i}.0" not in text
        assert "ZZSECRET-MUG" in text  # just-missed deals stay public

        # Every HTML page written (the static wall, the category hubs, 404)
        # holds the free deals at most: never a locked deal's name, price,
        # link or photo, and no photo at all. (deals.json's stubs carry
        # image_url by contract; the HTML never does.)
        pages = list(out.rglob("*.html"))
        assert out / "index.html" in pages and out / "deals" / "other" / "index.html" in pages
        for page in pages:
            html = page.read_text(encoding="utf-8")
            for i in (3, 4):
                assert f"ZZSECRET-ITEM-{i}" not in html, page
                assert f"walmart.com/ip/zz{i}" not in html, page
                assert f"100{i}.0" not in html and f"500{i}.0" not in html, page
            assert "zzimg" not in html, page
        assert "ZZSECRET-ITEM-0" in (out / "index.html").read_text(encoding="utf-8")

        doc = json.loads(text)
        meta = doc["meta"]
        assert meta["api_url"] == "https://api.example.workers.dev"
        assert meta["google_client_id"] == "cid-123"
        assert meta["teaser_count"] == 3
        assert meta["locked_count"] == 2
        assert meta["trial_days"] == 7
        assert meta["count"] == 6 and len(doc["deals"]) == 6
        for stub, i in zip(doc["deals"][3:5], (3, 4)):
            assert set(stub) == self.STUB_KEYS
            assert stub["locked"] is True and stub["status"] == "alive"
            assert "dedupe_key" not in stub and len(stub["key"]) == 16
            assert stub["store"] == "Walmart" and stub["pct_at_checkout"] == 80.0
            # The source filter: the reporting sites, never the post URL.
            assert stub["src"] and all("/" not in s for s in stub["src"])
        dead = doc["deals"][5]
        assert dead["status"] == "dead" and dead["item"] == "ZZSECRET-MUG"
        assert "locked" not in dead

        # The full payload: same meta, every deal complete, nothing locked.
        full = res["full_payload"]
        assert full["meta"] == meta
        assert [d["item"] for d in full["deals"]] == (
            [f"ZZSECRET-ITEM-{i}" for i in range(5)] + ["ZZSECRET-MUG"])
        assert all("locked" not in d and d["url"] for d in full["deals"])
        # Source health (operator-only) is untouched by the paywall.
        assert set(res["health"]) == {"generated_at", "sources", "last_run"}

    def test_disabled_writes_the_full_payload(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        cfg.paywall_enabled = False
        res = render_site(db, cfg, NOW, out_dir=out, deals=self._deals(10))
        doc = json.loads((out / "deals.json").read_text(encoding="utf-8"))
        assert doc == res["full_payload"]
        assert doc["meta"]["locked_count"] == 0
        assert doc["meta"]["teaser_count"] == 8 and doc["meta"]["api_url"] == ""
        assert len(doc["deals"]) == 10
        assert all("locked" not in d and d["item"] for d in doc["deals"])


class TestNoDailyEmail:
    """Operator call (2026-10-11): no daily emails for anyone; membership is
    website access. subscribe.html is a plain membership page, the home
    page has no email form, and meta carries no list endpoint while the
    list is off."""

    def test_meta_has_no_list_endpoint_while_the_list_is_off(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        cfg.newsletter_worker_url = "https://wowpriced-subscribe.example.workers.dev"
        cfg.newsletter_enabled = False
        render_site(db, cfg, NOW, out_dir=out, deals=[])
        doc = json.loads((out / "deals.json").read_text(encoding="utf-8"))
        assert doc["meta"]["newsletter_url"] == ""

    def test_subscribe_page_is_a_membership_page(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        render_site(db, cfg, NOW, out_dir=out, deals=[])
        html = (out / "subscribe.html").read_text(encoding="utf-8")
        assert html.startswith("<!doctype html>")
        assert "<form" not in html and 'name="email"' not in html
        assert "Every deal, unlocked" in html and "$9.99/mo" in html
        assert '"./#signup"' in html and "Start 7-day free trial" in html
        assert "unsubscribed=1" in html and "You're unsubscribed" in html
        assert "subscribe_url" not in html and 'href="./"' in html
        assert "<script src=" not in html and 'rel="stylesheet"' not in html

    def test_home_page_has_no_email_form(self, db, cfg, tmp_path):
        render_site(db, cfg, NOW, out_dir=tmp_path)
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert 'id="nlform"' not in html and 'name="email"' not in html.replace('type="email"', "")
        assert '<a href="subscribe.html">Membership</a>' in html
        assert "workers.dev" not in html


class TestPaywallPage:
    """The page side of docs/paywall.md ("Page behavior"): member fetch,
    locked stubs, the CTA panel, the account menu and the auth dialog live
    in index.html and key off deals.json meta (api_url, google_client_id,
    locked_count, trial_days) — never a baked-in Worker URL."""

    @pytest.fixture()
    def html(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        render_site(db, cfg, NOW, out_dir=out, deals=[])
        return (out / "index.html").read_text(encoding="utf-8")

    def test_auth_dialog_and_panel_markup(self, html):
        for el_id in ("authmodal", "authform", "authemail", "authpass", "autherr",
                      "authsubmit", "authswitch", "authclose", "gsibtn",
                      "authstep1", "authstep2", "cobtn", "codisc", "coerr", "cofb",
                      "conotnow", "paywall", "pwsignup", "pwlogin", "pwbuy", "pwcheck",
                      "pwclaim", "pwmanage", "pwnote", "pwerr", "pwfb", "pwfbnote",
                      "authdisc", "cofbnote",
                      "account", "loginlink", "logout", "billing", "acctfoot"):
            assert f'id="{el_id}"' in html, el_id
        assert 'role="dialog"' in html and 'aria-modal="true"' in html
        assert '"Escape"' in html  # Esc closes the dialog
        # The existing membership affordances are untouched.
        assert 'id="subscribe"' in html and 'id="subbar"' in html

    def test_session_and_worker_paths_come_from_meta(self, html):
        assert '"wp.session"' in html
        for path in ("/api/me", "/api/deals", "/api/signup", "/api/login",
                     "/api/google", "/api/logout", "?fresh=1"):
            assert path in html, path
        for field in ("api_url", "google_client_id", "locked_count", "trial_days"):
            assert field in html, field
        # The Worker base URL only ever arrives through deals.json meta.
        assert "workers.dev" not in html
        assert "wowpriced-api" not in html
        assert "Bearer " in html

    def test_gis_loaded_only_by_the_conditional_loader(self, html):
        url = "https://accounts.google.com/gsi/client"
        assert html.count(url) == 1
        assert "<script src=" not in html  # injected by script, never static
        line = next(ln for ln in html.splitlines() if url in ln)
        assert "GIS_SRC" in line
        start = html.index("function loadGis")
        assert "google_client_id" in html[start:start + 400]

    def test_locked_stub_rendering_and_cta_copy(self, html):
        assert "blur(8px)" in html
        assert "d.locked === true" in html
        assert "Sign up free" in html and "Log in" in html
        assert "Unlock all " in html and "I just subscribed" in html
        assert '" with a " + o.days + "-day free trial"' in html
        # The card-less trial and its copy are gone.
        assert "Your free week ended" not in html and "No card needed" not in html
        # Operator call: members see no trial or billing line on the wall,
        # and nothing on the main screen says "cancel".
        assert 'id="trialnote"' not in html and "Manage or cancel" not in html
        assert "Manage membership" in html
        # Stub cards never carry a product link or title text.
        start = html.index("function lockedHtml")
        body = html[start:html.index("function cardHtml")]
        assert "href" not in body and "d.item" not in body and "d.url" not in body


class TestSelfReloadAndPetsTab:
    def test_version_stamped_into_page_and_meta(self, db, cfg, tmp_path):
        import json

        from dealradar.publish.site import render_site, site_template_version
        render_site(db, cfg, NOW, out_dir=tmp_path)
        v = site_template_version()
        assert len(v) == 12
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert f'var SITE_VERSION = "{v}"' in html and "__SITE_VERSION__" not in html
        meta = json.loads((tmp_path / "deals.json").read_text())["meta"]
        assert meta["site_version"] == v
        assert "maybeReloadForNewVersion(meta)" in html

    def test_pets_tab_exists_and_other_no_longer_holds_pets(self, db, cfg, tmp_path):
        from dealradar.publish.site import render_site
        render_site(db, cfg, NOW, out_dir=tmp_path)
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        # [label, categories, hub slug], injected from publish/seo.py CATGROUPS
        assert '["Pets", ["pets"], "pets"]' in html
        assert '["Other", ["office", "books_media", "other"], "other"]' in html


class TestLoginDeepLink:
    def test_hash_opens_the_auth_dialog_only_with_an_api(self, db, cfg, tmp_path):
        from dealradar.publish.site import render_site
        render_site(db, cfg, NOW, out_dir=tmp_path)
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "function openFromHash()" in html
        assert '"#login"' in html and '"#signup"' in html
        assert "state.meta.api_url" in html.split("function openFromHash()")[1][:400]
        assert "setTimeout(openFromHash, 0)" in html


class TestOpaqueKeysAndPublishTime:
    def test_full_deals_carry_key_and_meta_published_at(self, db, cfg, tmp_path):
        import json

        from dealradar.publish.site import render_site
        render_site(db, cfg, NOW, out_dir=tmp_path)
        doc = json.loads((tmp_path / "deals.json").read_text())
        assert doc["meta"]["published_at"] >= doc["meta"]["generated_at"]
        for d in doc["deals"]:
            assert len(d["key"]) == 16 and d["key"] != d["dedupe_key"]
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "d.key || d.dedupe_key" in html and "m.published_at" in html

class TestWelcomeClaim:
    """welcome.html is the Payment Link's after-payment page: with
    ?session_id= it binds the payment to the signed-in account through
    POST /api/claim (docs/paywall.md "Claiming a payment"), or parks the id
    in sessionStorage until the viewer signs in and comes back."""

    @pytest.fixture()
    def html(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        render_site(db, cfg, NOW, out_dir=out, deals=[])
        return (out / "welcome.html").read_text(encoding="utf-8")

    def test_claims_through_the_worker_from_meta(self, html):
        assert html.startswith("<!doctype html>")
        assert "<script src=" not in html and 'rel="stylesheet"' not in html
        assert '"session_id"' in html and "/api/claim" in html
        assert '"wp.session"' in html and "Bearer " in html
        assert "api_url" in html and "workers.dev" not in html
        assert "Membership activated" in html

    def test_signed_out_keeps_the_receipt_and_points_at_sign_in(self, html):
        assert "sessionStorage" in html and '"wp.claim"' in html
        assert "Create your account (or log in) to attach this membership" in html
        assert 'href=\\"./#login\\"' in html  # inside a JS string literal
        assert "portal_url" in html  # the billing-portal line is unchanged

    def test_every_claim_error_code_has_copy(self, html):
        for code in ("bad_session", "unknown_session", "stripe_unavailable",
                     "signin_unavailable", "rate_limited", "unauthorized"):
            assert f"{code}:" in html, code


def _render_pages(db, cfg, out):
    render_site(db, cfg, NOW, out_dir=out, deals=[])
    return {p.name: p.read_text(encoding="utf-8") for p in out.glob("*.html")}


class TestCardRequiredTrial:
    """A free account sees the same top-8 teaser; every other deal unlocks
    only with a Stripe subscription (card up front: Stripe's 7-day trial,
    then $9.99/month). The page builds to the Worker API in docs/paywall.md:
    POST /api/checkout, POST /api/portal, me.trial_available/trial_days."""

    @pytest.fixture()
    def html(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        render_site(db, cfg, NOW, out_dir=out, deals=[])
        return (out / "index.html").read_text(encoding="utf-8")

    def test_signup_dialog_is_a_free_account_then_step_two(self, html):
        assert "Create your free account" in html
        assert '"Free account: " + top + " free deals, new at every drop. Every deal: "' in html
        assert '"-day free trial."' in html
        assert "Step 2 of 2 — start your free trial" in html
        assert '"Start " + days + "-day free trial"' in html
        assert "Not now" in html
        # Step 2 follows a sign-up or a Google sign-in, never a password log-in.
        start = html.index("function signedIn(")
        body = html[start:html.index("function authPost(")]
        assert 'path !== "/api/login"' in body and 'openAuth("checkout")' in body

    def test_disclosure_sits_next_to_every_trial_button(self, html):
        disclosure = ('"Card required. Free for " + days + " days, then $9.99/month, '
                      'renews monthly " +\n      "until you cancel. " + cancelLine()')
        assert disclosure in html
        assert ('"Cancel anytime online" + (contactEmail() ? " or email " + '
                'contactEmail() : "") + "."') in html
        # Both trial buttons (panel and dialog) get a line written right
        # beside them, as visible text (never a title= tooltip): "$0 today"
        # by the panel's (operator's call), every term by the dialog's.
        assert 'id="pwbuy"' in html and 'id="pwnote" class="disc"' in html
        assert 'id="cobtn"' in html and 'id="codisc" class="disc"' in html
        assert 'pn.innerHTML = note ? noteHtml(o) : ""' in html
        assert "disc.textContent = o.note" in html
        assert "title=" not in html[html.index('id="pwbuy"'):html.index('id="pwfb"')]

    def test_used_trial_and_lapsed_get_no_trial_wording(self, html):
        assert "me.trial_available !== false" in html
        assert '"Subscribe — $9.99/month"' in html
        assert "Your membership ended" not in html  # never stated as fact
        assert "No active membership on this account right now." in html
        assert "You're signed in. Start your " in html
        assert "You're signed in. Subscribe to unlock all " in html

    def test_checkout_call_and_its_answers(self, html):
        assert '"/api/checkout"' in html
        start = html.index("function checkout(")
        body = html[start:html.index("function portal(")]
        assert "location.assign" in body
        assert "res.status === 409" in body and "memberDeals(true)" in body
        assert "Free-trial checkout is unavailable right now." in body
        assert "fallback_url" in body
        assert "Subscribe now &mdash; $9.99/month" in html  # the fallback link

    def test_portal_call_falls_back_then_points_at_email(self, html):
        assert '"/api/portal"' in html
        start = html.index("function portal(")
        body = html[start:html.index("function showErr(")]
        assert "location.assign" in body and "portal_url" in body
        assert "say(noPortalText())" in body and "btn.hidden" not in body
        assert 'id="billing" class="linkbtn">Manage membership<' in html

    def test_membership_link_lives_in_the_footer_and_no_member_line(self, html):
        """Operator call: the billing link sits at the bottom of the page as
        "Manage membership"; members get no trial/terms line on the wall."""
        foot = html[html.index("<footer>"):html.index("</footer>")]
        assert 'id="acctfoot"' in foot and 'id="billing"' in foot
        header = html[html.index("<header>"):html.index("</header>")]
        assert 'id="billing"' not in header and "trialnote" not in html
        assert 'getElementById("acctfoot").hidden = !(on && hasBilling())' in html
        assert "checkout=cancel" in html
        assert "Checkout closed — no charge." in html
        start = html.index("function checkoutCancelled()")
        assert "history.replaceState" in html[start:start + 600]


class TestDropClock:
    """The countdown is a prominent element with a progress bar; marks sit
    on fixed wall-clock instants derived from published_at and cadence; a
    drop fetches with a 2.5 s cap, re-deals the wall seeded by the mark
    index and animates it (FLIP) unless the viewer prefers reduced motion."""

    @pytest.fixture()
    def html(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        render_site(db, cfg, NOW, out_dir=out, deals=[])
        return (out / "index.html").read_text(encoding="utf-8")

    def test_big_countdown_with_progress_bar_and_no_live_region(self, html):
        assert 'id="drop" class="drop" role="timer" aria-live="off"' in html
        # The dial: a ring filled by two turning half-discs, a tag that
        # becomes a tick at the drop.
        assert 'id="dropdial" class="dial"' in html and ".dslot{" in html
        assert "function paintDial(f, left)" in html
        assert "paintDial(f, clock.dropping ? null : left);" in html
        # Operator: green at the start, through yellow, red for the last minute.
        dial = _between(html, "function paintDial(", "function tick(")
        assert "left <= lastMin ? 4 : Math.round(142 - 96" in dial and '"--dial"' in dial
        assert 'class="dtick"' in html and ".drop.dropping .dtick{opacity:1" in html
        css = html[html.index(".dropt{"):html.index("}", html.index(".dropt{"))]
        assert "font-size:22px" in css and "font-weight:800" in css
        assert "tabular-nums" in css
        mobile = html[html.index("@media (max-width:640px)"):]
        assert ".dropt{font-size:20px}" in mobile  # still >= 18px on phones

    def test_mark_math(self, html):
        for name in ("function dropPhase(", "function nextMarkAfter(",
                     "function markIndex("):
            assert name in html, name
        assert "((pub % step) + step) % step" in html
        assert "Math.floor((t - phase) / step) + 1) * step" in html
        assert "Math.floor((t - phase) / step)" in html

    def test_drop_contract(self, html):
        assert "DROP_FETCH_MS = 2500, DROP_MAX_MS = 3000" in html
        assert "POLL_MS = 15 * 1000, POLL_SLOW_MS = 30 * 1000, SLOW_AFTER_MS = 6 * 60 * 1000" in html
        start = html.index("function drop(")
        body = html[start:html.index("function loadData(")]
        assert "withTimeout(got, DROP_FETCH_MS)" in body and "markIndex(last" in body
        present = _between(html, "function present(", "function drop(")
        assert "reorganize(true, added + unlocked > 0)" in present
        assert '"Fresh drop" + updatedAgo()' in present

    def test_a_mark_without_a_new_publish_never_re_deals_the_wall(self, html):
        """Operator ask (2026-10-11): the free 8 must change completely; a
        mark that came before its publish re-dealt the same 8 (cards moved
        rows). Now only a new publish is a drop; until it lands the clock
        says "Next drop soon" and polls (HEAD, then the file if it changed)."""
        finish = _between(html, "function finish(", "got.catch(noop);")
        assert "if (pub) { present(pub, deals, seed); return; }" in finish
        assert "clock.waiting = clock.lastPoll = Date.now();" in finish
        assert "reorganize(" not in finish
        assert "present(pub, deals, null);" in _between(html, "function landed(", "function merge(")
        tick = _between(html, "function tick(", "setInterval(tick, 1000)")
        assert "!clock.waiting && loadedOnce" in tick and "poll();" in tick
        poll = _between(html, "function poll(", "function updatedAgo(")
        assert 'method: "HEAD"' in poll and "fetchPublish().then(merge)" in poll
        clockp = _between(html, "function paintClock(", "function paintDial(")
        assert 'wait ? "Next drop" : "Next drop in"' in clockp and 'wait ? "soon"' in clockp
        assert "lateUntil" not in html
        assert '" · updated "' in html and '" min ago"' in html
        # One ticking interval for the whole page, plus the 5-minute fallback.
        assert html.count("setInterval(") == 2
        assert "setInterval(tick, 1000)" in html

    def test_flip_and_reduced_motion(self, html):
        start = html.index("function reorganize(")
        body = html[start:html.index("// ---- paywall")]
        assert "getBoundingClientRect" in body and "FLIP_MS" in body
        assert "reduceMotion()" in body
        assert "prefers-reduced-motion: reduce" in html
        assert "@media (prefers-reduced-motion: reduce)" in html
        # Focus is put back where it was, never moved by a drop.
        assert "rememberFocus()" in body and "restoreFocus(focus)" in body
        assert "preventScroll: true" in html
        # New arrivals lead with a visible tag.
        assert '<span class="newtag">NEW</span>' in html

    def test_images_send_no_referrer(self, html):
        # Some image hosts refuse requests that name another site.
        assert 'referrerpolicy="no-referrer"' in html


class TestWelcomeTrialClaim:
    @pytest.fixture()
    def html(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        render_site(db, cfg, NOW, out_dir=out, deals=[])
        return (out / "welcome.html").read_text(encoding="utf-8")

    def test_trial_and_paid_outcomes(self, html):
        assert "Your free trial has started." in html
        assert "You won't be charged until " in html
        assert "Then $9.99/month, renews monthly " in html
        assert "Membership activated" in html
        assert "/api/portal" in html and "Manage membership" in html

    def test_wrong_account_copy(self, html):
        assert "wrong_account:" in html
        assert ("This payment belongs to a different wowpriced account. "
                "Log in with the email you used at checkout, ") in html


class TestMembershipCopyIsTrue:
    """Every page tells the same story: the top deals are free for everyone;
    membership ($9.99/month, 7-day free trial with a card) unlocks every
    deal on the wall plus the hourly emails."""

    def test_no_page_promises_a_free_wall_or_a_cardless_trial(self, db, cfg, tmp_path):
        pages = _render_pages(db, cfg, tmp_path / "site")
        for name, html in pages.items():
            low = html.lower()
            for stale in ("no card needed", "free and always will be",
                          "the site stays free", "the wall here stays free",
                          "the wall stays free", "your free week ended"):
                assert stale not in low, (name, stale)

    def test_content_pages_explain_the_card_trial(self, db, cfg, tmp_path):
        pages = _render_pages(db, cfg, tmp_path / "site")
        for name in ("about.html", "how-it-works.html"):
            text = " ".join(pages[name].split())
            assert "free for everyone, a new eight at every drop" in text, name
            # Membership is site access only (operator, 2026-10-11): no email.
            assert "new deals every 10 minutes" in text, name
            assert "one email every morning" not in text and "once an hour" not in text, name
            assert "7-day free trial" in text and "card" in text, name
            assert "every deal on the wall" in text, name

    def test_subscribe_page_joins_through_the_trial_flow(self, db, cfg, tmp_path):
        html = _render_pages(db, cfg, tmp_path / "site")["subscribe.html"]
        assert '"./#signup"' in html and "m.api_url" in html
        assert '"Card required. Free for " + days + " days, then $9.99/month' in html
        assert 'id="memberterms"' in html


def _between(html, start, end):
    i = html.index(start)
    return html[i:html.index(end, i)]


class TestTrialCtasAreHonest:
    """Every membership CTA goes through sign-up and POST /api/checkout (the
    card-up-front trial); the Payment Link appears only as checkout's
    fallback, with its own terms. Every trial button sits next to the full
    terms, cancelling is online or by email, and 0 trial days means no
    trial wording at all."""

    @pytest.fixture()
    def pages(self, db, cfg, tmp_path):
        cfg.contact_email = "hello@wowpriced.com"
        return _render_pages(db, cfg, tmp_path / "site")

    def test_ctas_never_link_the_payment_link(self, pages):
        html = pages["index.html"]
        # The Payment Link reaches the page only inside checkout's fallback.
        assert html.count("subscribe_url") == 1
        assert "subscribe_url" in _between(html, "function checkout(", "function noPortalText(")
        assert 'id="pwplain"' not in html
        assert '<a id="subscribe" class="getdeal trialcta" href="#signup"' in html
        assert '<a id="subbar" class="subbar trialcta" href="#signup"' in html
        start = html.index('["subscribe", "subbar"].forEach')
        assert "nextStep()" in html[start:start + 250]
        for name, page in pages.items():
            assert "subscribe_url" not in page or name == "index.html", name

    def test_fallback_replaces_the_trial_button_and_its_terms(self, pages):
        html = pages["index.html"]
        assert ">Subscribe now &mdash; $9.99/month</a>" in html
        assert '"$9.99 charged today, renews monthly until you cancel. " + cancelLine()' in html
        assert "btn.hidden = disc.hidden = !!fb || !!blk" in _between(html, "function paintStep2(", "function setMode(")
        panel = _between(html, "function updatePaywall(", "function repaintPanel(")
        assert "pwbuy: buy && !fb && !blk" in panel and "pn.hidden = !note || !!fb" in panel
        # A lapsed account (maybe only a failed card) is never sold a second
        # subscription through the Payment Link.
        assert "o.lapsed) fb = \"\"" in _between(html, "function checkout(", "function noPortalText(")

    def test_every_trial_offer_carries_every_term(self, pages):
        html = pages["index.html"]
        assert 'renews monthly " +\n      "until you cancel. " + cancelLine()' in html
        assert '"Subscribe — $9.99/month, renews monthly until you cancel. " + cancelLine()' in html
        panel = _between(html, "function updatePaywall(", "function repaintPanel(")
        # The main screen (panel, band) says only "$0 today" next to a trial
        # button, never "cancel" (operator's call); the footer line opens
        # with "Membership — $9.99/month"; the dialog that starts checkout
        # keeps every term, and Stripe's page repeats them.
        assert 'var note = (mode === "out" || buy) && !blk ? shortNote(o) : ""' in panel
        assert 'pn.innerHTML = note ? noteHtml(o) : ""' in panel
        short = _between(html, "function shortNote(", "function fallbackTerms(")
        assert 'o.trial ? "$0 today." : "$9.99/month, renews monthly."' in short
        # The badge is a button: a click takes the next step (sign up, then
        # the trial), like the trial button beside it.
        assert '<button type="button" class="zerotoday" data-cta="1" ' in short
        assert 'if (e.target.closest(".zerotoday")) nextStep();' in html
        assert "<strong>Membership — $9.99/month:</strong>" in html
        assert "card" not in short.lower()  # operator call: no "Card required" on the main screen
        assert "cancel" not in short.lower()
        assert 'disc.textContent = o.note;' in _between(html, "function paintStep2(", "function setMode(")
        mode = _between(html, "function setMode(", "function openAuth(")
        assert 'disc.textContent = up ? o.note : ""' in mode   # the sign-up step
        assert 'id="authdisc" class="disc"' in html
        assert 'getElementById("footterms").textContent' in html   # the footer

    def test_zero_trial_days_mean_no_trial(self, pages):
        html = pages["index.html"]
        days = _between(html, "function trialDays(", "function teaserCount(")
        assert "d == null" in days and "|| 7" not in days
        assert "n == null ? 8 : n" in _between(html, "function teaserCount(", "function dealTotal(")
        assert "&& days > 0" in _between(html, "function offer(", "function lockLabel(")
        assert "num((state.meta || {}).teaser_count) || 8" not in html
        sub = pages["subscribe.html"]
        assert "days > 0" in sub
        assert '"Subscribe — $9.99/month, renews monthly until you cancel. "' in sub

    def test_cancel_by_email_everywhere_from_config(self, pages):
        assert 'var CONTACT_EMAIL = "hello@wowpriced.com"' in pages["index.html"]
        assert ('"Cancel anytime online" + (contactEmail() ? " or email " + '
                'contactEmail() : "") + "."') in pages["index.html"]
        for name in ("about.html", "how-it-works.html", "contact.html"):
            assert 'mailto:hello@wowpriced.com">hello@wowpriced.com</a>' in pages[name], name
            assert 'class="mailalt"' in pages[name], name
        assert 'var CONTACT_EMAIL = "hello@wowpriced.com"' in pages["welcome.html"]
        assert 'var CONTACT_EMAIL = "hello@wowpriced.com"' in pages["subscribe.html"]
        for name, page in pages.items():
            assert "{{CONTACT_EMAIL}}" not in page, name

    def test_unset_contact_email_never_reaches_the_site(self, db, cfg, tmp_path):
        assert cfg.contact_email == "set-me@example.com"
        for name, page in _render_pages(db, cfg, tmp_path / "site").items():
            assert "set-me@example.com" not in page, name
            assert "{{CONTACT_EMAIL}}" not in page, name

    def test_contact_email_that_could_break_a_page_is_left_out(self, db, cfg, tmp_path):
        # It lands inside script strings and attributes: a quote must not.
        cfg.contact_email = 'x"+alert(1)+"@wowpriced.com'
        for name, page in _render_pages(db, cfg, tmp_path / "site").items():
            assert "alert(1)" not in page, name
            assert "{{CONTACT_EMAIL}}" not in page, name
        assert 'var CONTACT_EMAIL = ""' in _render_pages(db, cfg, tmp_path / "s2")["index.html"]

    def test_no_portal_means_email_never_a_receipt(self, pages):
        html = pages["index.html"]
        body = _between(html, "function noPortalText(", "function portal(")
        assert '"Email " + contactEmail() + " and we\'ll take care of your membership."' in body
        for name, page in pages.items():
            assert "reply to your stripe receipt" not in " ".join(page.lower().split()), name
        welcome = pages["welcome.html"]
        assert 'getElementById("managenote").innerHTML = " " + NO_PORTAL' in welcome
        assert "btn.hidden = true" not in welcome

    def test_billing_reaches_lapsed_accounts(self, pages):
        html = pages["index.html"]
        assert 'getElementById("acctfoot").hidden = !(on && hasBilling())' in html
        body = _between(html, "function hasBilling(", "function pendingClaim(")
        assert 'me.member || me.claimed || me.reason === "lapsed"' in body
        panel = _between(html, "function updatePaywall(", "function repaintPanel(")
        assert 'pwmanage: blk ? blk === "payment_issue" : mode === "lapsed"' in panel
        assert '<button type="button" id="pwmanage" class="pwbtn alt" hidden>Manage membership</button>' in html
        assert '["billing", "pwmanage", "comanage"].forEach' in html
        assert "Manage billing" not in html
        assert "Your membership ended" not in html

    def test_unknown_membership_offers_a_retry_not_a_purchase(self, pages):
        html = pages["index.html"]
        panel = _between(html, "function updatePaywall(", "function repaintPanel(")
        assert '(unknownMembership() ? "unknown"' in panel
        assert "Couldn't check your membership right now" in panel
        assert 'mode === "retry" || mode === "unknown"' in panel
        assert 'me.reason !== "unknown"' in _between(html, "function signedIn(", "function authPost(")

    def test_checkout_409_rereads_me_and_never_sells_twice(self, pages):
        body = _between(pages["index.html"], "function checkout(", "function noPortalText(")
        assert "res.status === 409" in body and "memberDeals(true)" in body
        assert 'refuse("found", ui, had)' in body
        html = pages["index.html"]
        assert "Stripe receipt" not in html  # there is no receipt link to open
        found = _between(html, "function blockText(", "function offer(")
        assert "continue with Google using this address" in found
        assert "google_client_id" in found and "we'll attach it" in found
        start = body.index("res.status === 409")
        assert "return;" in body[start:body.index("res.status === 401")]

    def test_pending_payment_link_claim_comes_first(self, pages):
        html = pages["index.html"]
        assert 'CLAIM_KEY = "wp.claim"' in html
        signed = _between(html, "function signedIn(", "function authPost(")
        assert signed.index('location.assign("welcome.html")') < signed.index('openAuth("checkout")')
        step = _between(html, "function nextStep(", "function checkoutCancelled(")
        assert 'pendingClaim()) location.assign("welcome.html")' in step
        assert "claim: \"Your payment on this device isn't attached to this account yet.\"" in html
        assert '<a id="pwclaim" class="pwbtn" href="welcome.html"' in html
        welcome = pages["welcome.html"]
        assert "then come back to this page: your payment is remembered on this device." in welcome
        assert "already_claimed:" in welcome
        assert "This payment is already attached to another wowpriced account. " in welcome

    def test_back_from_stripe_reenables_buttons(self, pages):
        html = pages["index.html"]
        start = html.index('window.addEventListener("pageshow"')
        body = html[start:start + 300]
        assert "e.persisted" in body
        for el_id in ("cobtn", "pwbuy", "billing", "pwmanage"):
            assert f'"{el_id}"' in body, el_id
        welcome = pages["welcome.html"]
        assert 'window.addEventListener("pageshow"' in welcome and "e.persisted" in welcome

    def test_old_worker_trial_gets_no_billing_terms(self, pages):
        assert "trialnote" not in pages["index.html"]  # no member terms line at all
        assert 'me.reason === "trial" && "trial_available" in me' in pages["welcome.html"]
        # ...and no "Manage membership" for a card-less trial: nothing to manage.
        body = _between(pages["index.html"], "function hasBilling(", "function pendingClaim(")
        assert ('me.member && me.reason === "trial" && !("trial_available" in me) && !me.claimed'
                in body)

    def test_checkout_refusals_offer_the_way_on_never_a_purchase(self, pages):
        html = pages["index.html"]
        texts = _between(html, "function blockText(", "function offer(")
        assert ('payment_issue: "There\'s a problem with your last payment. '
                'Update your card in Manage membership."') in texts
        assert ('contact_support: (mail ? "Email " + mail : "Use the contact page") + '
                '" and we\'ll sort it out."') in texts
        body = _between(html, "function checkout(", "function noPortalText(")
        assert 'j.error === "payment_issue" || j.error === "contact_support"' in body
        assert "if (j.me) state.me = j.me;" in body
        refuse = _between(html, "function refuse(", "function noPortalText(")
        assert "auth.block = code;" in refuse and 'auth.fb = "";' in refuse
        # The step-2 dialog and the panel trade the buy button for the way on:
        # Manage membership for a payment issue.
        step2 = _between(html, "function paintStep2(", "function setMode(")
        assert 'getElementById("comanage").hidden = blk ? blk !== "payment_issue" : !o.lapsed' in step2
        assert "err.textContent = blockText(blk)" in step2
        assert '<button type="button" id="comanage" class="pwbtn alt" hidden>Manage membership</button>' in html
        panel = _between(html, "function updatePaywall(", "function repaintPanel(")
        assert '.textContent = blk ? blockText(blk) : {' in panel
        assert 'var fb = buy && !o.lapsed && !blk ? auth.fb : ""' in panel
        # The refusal ends with the account: a new sign-in or a log-out.
        assert 'auth.block = "";' in _between(html, "function dropSession(", "function isMember(")
        assert 'auth.block = "";' in _between(html, "function signedIn(", "function authPost(")
        # A payment issue reaches the billing portal from the header too.
        assert 'auth.block === "payment_issue"' in _between(html, "function hasBilling(",
                                                             "function pendingClaim(")

    def test_checkout_busy_never_offers_the_payment_link(self, pages):
        body = _between(pages["index.html"], "function checkout(", "function refuse(")
        # 503 checkout_unavailable: only the Worker's fallback_url (empty when
        # Stripe could not be asked); me.subscribe_url only for an older
        # Worker without the route.
        assert ('var fb = res.status === 404 ? https((state.me || {}).subscribe_url) '
                ': https(j.fallback_url);') in body
        assert 'if (res.status === 429 || o.lapsed) fb = "";' in body
        assert '(!fb ? "Checkout is busy — try again in a minute."' in body

    def test_already_claimed_receipt_is_dropped_on_this_device(self, pages):
        welcome = pages["welcome.html"]
        assert ('code === "bad_session" || code === "unknown_session" || '
                'code === "already_claimed") ssDel(CLAIM_KEY)') in welcome
        assert "Amazon prices\n     re-checked live" in welcome


class TestHonestShuffleAndPage:
    """The 10-minute reshuffle stays, honestly labelled; the page's account
    row, focus and member lookup hold up."""

    @pytest.fixture()
    def html(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        render_site(db, cfg, NOW, out_dir=out, deals=[])
        return (out / "index.html").read_text(encoding="utf-8")

    def test_no_page_claims_prices_were_checked(self, db, cfg, tmp_path):
        pages = _render_pages(db, cfg, tmp_path / "site")
        for name, page in pages.items():
            assert "prices checked" not in page.lower(), name
        body = _between(pages["index.html"], "function updatedAgo(", "function drop(")
        assert '" · updated "' in body and "publishTime(" in body

    def test_toast_only_for_real_news_or_a_visible_shuffle(self, html):
        body = _between(html, "function present(", "function loadData(")
        assert 'if (added) toast(plural(added, "new deal") + " just landed");' in body
        assert ('else if (unlocked) toast(plural(unlocked, "new free deal") + " unlocked");'
                in body)
        assert ('else if (moved && state.sortKey === "fresh") '
                'toast("Fresh drop" + updatedAgo());') in body
        assert "Deals reshuffled" not in html
        reorg = _between(html, "function reorganize(", "// ---- paywall")
        assert "var changed = cardOrder() !== was;" in reorg and "return changed;" in reorg

    def test_clock_names_the_shuffle_and_sweeps_only_with_news(self, html):
        assert '<span class="droplbl">Next drop in</span>' in html
        assert '"Dropping…"' in html and "Shuffling" not in html and "Next shuffle" not in html
        reorg = _between(html, "function reorganize(", "// ---- paywall")
        assert 'if (sweep) el.classList.add("moved");' in reorg
        assert "reorganize(true, added + unlocked > 0)" in html

    def test_account_row_wraps_on_phones(self, html):
        mobile = _between(html, "@media (max-width:640px)", "@media (max-width:520px)")
        assert (".acct,.acct .in{flex-wrap:wrap;white-space:normal;"
                "justify-content:center") in mobile
        assert ".acct .email{max-width:100%}" in mobile

    def test_member_lookup_always_ends_the_check(self, html):
        # A drop that changed the publish while /api/me was slow must not
        # leave the CTA panel hidden for good.
        assert html.count("memberDeals(false).then(showDeals)") == 2
        assert "state.dataKey === pub.key) showDeals" not in html
        assert "state.dataKey === key) showDeals" not in html

    def test_closing_the_dialog_never_drops_focus(self, html):
        body = _between(html, "function closeAuth(", "function signedIn(")
        assert "auth.opener" in body and 'panel.querySelector("#pwbuy")' in body
        assert "el.offsetParent !== null" in body and "document.body.contains(el)" in body
        # Disabling a focused button drops its focus; checkout gives it back.
        chk = _between(html, "function checkout(", "function noPortalText(")
        assert "had = document.activeElement === ui.btn" in chk and "ui.fb.focus()" in chk


def test_each_render_unlocks_a_new_teaser(db, tmp_path):
    """Two publishes in a row show visitors two different free 8, and the
    rotation lives in state.db meta (no new rows per run)."""
    cfg = AppConfig(paywall_enabled=True, paywall_teaser_count=8, paywall_teaser_pool=48,
                    site_dir=str(tmp_path / "unused"))
    deals = [make_deal(dedupe_key=f"walmart|item-{i}", item=f"Item {i}", store=f"Store {i}",
                       canonical_url=f"https://www.walmart.com/ip/{i}", score=100.0 - i)
             for i in range(30)]
    shown = []
    for run in range(2):
        out = tmp_path / f"site{run}"
        render_site(db, cfg, NOW, out_dir=out, deals=deals)
        doc = json.loads((out / "deals.json").read_text(encoding="utf-8"))
        shown.append({d["key"] for d in doc["deals"] if not d.get("locked")})
    assert len(shown[0]) == len(shown[1]) == 8 and not shown[0] & shown[1]
    assert len(json.loads(db.meta_get("teaser_recent"))) == 16


class TestEyeCatchers:
    """Operator asks (2026-10-11): a stencil deal ticker at the top, the
    trial in the wall itself, the source sites at the bottom."""

    @pytest.fixture()
    def html(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        render_site(db, cfg, NOW, out_dir=out, deals=[])
        return (out / "index.html").read_text(encoding="utf-8")

    def test_ticker_tops_the_page_and_never_names_a_locked_deal(self, html):
        assert html.index('id="ticker"') < html.index("<header>")
        body = _between(html, "function tickerItems(", "function paintTicker(")
        locked = body[body.index("var text = isLocked(d)"):body.index(": esc(name)")]
        assert "d.store" in locked and "d.item" not in locked and "final_price" not in locked
        assert "days free" in locked
        paint = _between(html, "function paintTicker(", "function gridHtml(")
        assert "strip.innerHTML = one + one;" in paint and "if (sig === tickerSig) return;" in paint
        assert 'id="tkpause" aria-pressed="false"' in html and 'id="tklist"' in html
        assert ".tkstrip{animation:none}" in html  # reduced motion: no scroll

    def test_trial_band_sits_between_free_and_locked_deals(self, html):
        grid = _between(html, "function render() {", "content.innerHTML = html;")
        assert 'return (open.length ? gridHtml(open) : "") + trialBand() + gridHtml(shut);' in grid
        assert "banded || !canJoin()" in grid
        assert 'closest(".card.locked, [data-cta]")' in html
        assert "!isMember() && !auth.checking" in _between(html, "function canJoin(", "function trialBand(")

    def test_hairline_runs_on_the_free_picks_and_under_the_pointer(self, html):
        assert 'pathLength="1"' in html and 'traceSvg(d) + "</article>"' in html
        assert '(dead ? "" : traceSvg(d))' in html      # gone cards never trace
        assert "hashStr(String(dealKey(d))) % 5000" in html  # staggered, not lockstep
        free = _between(html, "function isFreePick(", "function lit(")
        assert "traceFree && !isMember() && !auth.checking && !unknownMembership() &&" in free
        assert "isFreePick(d) || (isMember() && isNew(d))" in html
        assert "traceFree = state.deals.some(isLocked);" in html
        assert ".card.lit .trace rect{opacity:1" in html
        hover = html[html.index("@media (hover:hover){"):]
        assert ".card:not(.gone):hover .trace rect" in hover[:200]
        reduced = html[html.index("@media (prefers-reduced-motion: reduce){"):]
        assert ".trace rect{animation:none}" in reduced
        assert '<div class="freetag"><i></i>free this drop</div>' in html

    def test_sources_wall_in_the_footer(self, html):
        foot = html[html.index("<footer>"):html.index("</footer>")]
        assert 'id="srcwall"' in foot and "Deals found across" in foot
        paint = _between(html, "function paintSources(", "// ---- the deal ticker ----")
        assert "/^[a-z0-9.-]+$/i.test(x)" in paint and "h + h" in paint
        assert ".srctrack{animation:none}" in html


def test_source_sites_meta_folds_subdomains_and_ranks(db, cfg, tmp_path):
    deals = []
    for i, url in enumerate(["https://home.woot.com/a", "https://electronics.woot.com/b",
                             "https://www.slickdeals.net/c", "https://hip2save.com/d"]):
        d = make_deal(dedupe_key=f"walmart|s-{i}", canonical_url=f"https://www.walmart.com/ip/s{i}")
        d["sightings"] = [{"source_name": "x", "source_url": url, "seen_at": iso(NOW)}]
        deals.append(d)
    dead = make_deal(dedupe_key="walmart|gone", status="dead")
    dead["sightings"] = [{"source_name": "x", "source_url": "https://deadsite.com/x", "seen_at": iso(NOW)}]
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=deals + [dead])
    meta = json.loads((out / "deals.json").read_text(encoding="utf-8"))["meta"]
    assert meta["source_sites"] == ["woot.com", "hip2save.com", "slickdeals.net"]


def test_search_engines_get_a_sitemap_robots_and_canonical_urls(db, cfg, tmp_path):
    """Operator: "how do i get my website to start showing up in google
    searches?" The site tells search engines what it is and where its pages
    are; the after-checkout page is never indexed."""
    cfg.custom_domain = "wowpriced.com"
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=[])
    index = (out / "index.html").read_text(encoding="utf-8")
    assert '<link rel="canonical" href="https://wowpriced.com/">' in index
    assert '<meta name="description"' in index and 'property="og:title"' in index
    assert '"@type":"WebSite"' in index and "{{SITE_URL}}" not in index
    for name in ("about.html", "how-it-works.html", "subscribe.html", "contact.html"):
        page = (out / name).read_text(encoding="utf-8")
        assert f'<link rel="canonical" href="https://wowpriced.com/{name}">' in page, name
    assert '<meta name="robots" content="noindex">' in (out / "welcome.html").read_text(encoding="utf-8")
    sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
    assert "<loc>https://wowpriced.com/</loc>" in sitemap and "welcome.html" not in sitemap
    # No "today" stamp: fixed pages carry no lastmod (hubs: tests/test_seo.py).
    assert "<lastmod>" not in sitemap and "404.html" not in sitemap
    robots = (out / "robots.txt").read_text(encoding="utf-8")
    assert "Sitemap: https://wowpriced.com/sitemap.xml" in robots
    assert "Disallow: /welcome.html" in robots
