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


class TestPaywallTeaser:
    """docs/paywall.md: with the paywall on, deals.json is a teaser (top N
    alive deals full, the rest locked stubs, dead deals unchanged) and
    render_site hands the full payload back for the member upload."""

    STUB_KEYS = {"locked", "key", "pct_at_checkout", "store", "category",
                 "first_seen_at", "image_url", "status", "conditions"}

    @staticmethod
    def _deals(n):
        # Distinctive names, prices and URLs so a leak is unmistakable.
        return [make_deal(dedupe_key=f"walmart|zz-{i}", item=f"ZZSECRET-ITEM-{i}",
                          canonical_url=f"https://www.walmart.com/ip/zz{i}",
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
        dead = doc["deals"][5]
        assert dead["status"] == "dead" and dead["item"] == "ZZSECRET-MUG"
        assert "locked" not in dead

        # The full payload: same meta, every deal complete, nothing locked.
        full = res["full_payload"]
        assert full["meta"] == meta
        assert [d["item"] for d in full["deals"]] == (
            [f"ZZSECRET-ITEM-{i}" for i in range(5)] + ["ZZSECRET-MUG"])
        assert all("locked" not in d and d["url"] for d in full["deals"])
        # health.json is untouched by the paywall.
        health = json.loads((out / "health.json").read_text(encoding="utf-8"))
        assert set(health) == {"generated_at", "sources", "last_run"}

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


class TestNewsletterSite:
    """The free daily email list reaches the page through deals.json meta and
    the standalone subscribe.html template."""

    def test_meta_newsletter_url_and_subscribe_page(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        cfg.newsletter_worker_url = "https://wowpriced-subscribe.example.workers.dev"
        render_site(db, cfg, NOW, out_dir=out, deals=[])
        doc = json.loads((out / "deals.json").read_text(encoding="utf-8"))
        assert doc["meta"]["newsletter_url"] == \
            "https://wowpriced-subscribe.example.workers.dev"
        html = (out / "subscribe.html").read_text(encoding="utf-8")
        assert html.startswith("<!doctype html>")
        # The form posts to the Worker from meta (never a hardcoded endpoint)
        # and the page explains free daily vs the paid hourly membership.
        assert "newsletter_url" in html and '"/subscribe"' in html
        assert 'name="email"' in html and 'method="post"' in html
        assert "Email signup is being set up" in html
        assert "ok=1" in html and "unsubscribed=1" in html
        assert "$9.99/mo" in html and "subscribe_url" in html
        assert 'href="./"' in html  # back home
        assert "prefers-color-scheme" in html
        assert "<script src=" not in html and "<link" not in html

    def test_meta_newsletter_url_empty_until_configured(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        render_site(db, cfg, NOW, out_dir=out, deals=[])
        doc = json.loads((out / "deals.json").read_text(encoding="utf-8"))
        assert doc["meta"]["newsletter_url"] == ""
        assert (out / "subscribe.html").exists()


class TestSubscribePageDoubleOptIn:
    """subscribe.html with double opt-in: ?ok=1 points at the inbox, the
    Worker's confirm page lands here on ?confirmed=1, ?unsubscribed=1 is
    unchanged and the copy explains the confirmation step."""

    def test_states_and_copy(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        cfg.newsletter_worker_url = "https://wowpriced-subscribe.example.workers.dev"
        render_site(db, cfg, NOW, out_dir=out, deals=[])
        html = (out / "subscribe.html").read_text(encoding="utf-8")
        assert "ok=1" in html
        assert "Check your inbox and click the confirmation link" in html
        assert "You're on the list" not in html  # the old promise is gone
        assert "confirmed=1" in html and "You're confirmed" in html
        assert "unsubscribed=1" in html and "You're unsubscribed" in html
        assert "err=email" in html
        assert "one confirmation link first" in html  # the intro copy
        assert "Nothing is sent until you confirm" in html


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
                      "pwplain", "pwnote", "pwerr", "pwfb",
                      "account", "loginlink", "logout", "billing", "trialnote"):
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
        assert "-day free trial. Card required, then $9.99/month." in html
        # The card-less trial and its copy are gone.
        assert "Your free week ended" not in html and "No card needed" not in html
        assert "Free trial — " in html and "Manage or cancel" in html
        # Stub cards never carry a product link or title text.
        start = html.index("function lockedHtml")
        body = html[start:html.index("function cardHtml")]
        assert "href" not in body and "d.item" not in body and "d.url" not in body


class TestFooterNewsletterForm:
    def test_form_present_and_wired_from_meta(self, db, cfg, tmp_path):
        from dealradar.publish.site import render_site
        render_site(db, cfg, NOW, out_dir=tmp_path)
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert 'id="nlform"' in html and 'name="email"' in html
        assert 'href="subscribe.html"' in html
        assert "m.newsletter_url" in html          # action comes from meta, never hardcoded
        assert "workers.dev" not in html


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
        assert '["Pets", ["pets"]]' in html
        assert '["Other", ["office", "books_media", "other"]]' in html


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
        assert "<script src=" not in html and "<link" not in html
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
        assert "Free account: today's top " in html
        assert "-day free trial, card required." in html
        assert "Step 2 of 2 — start your free trial" in html
        assert '"Start " + days + "-day free trial"' in html
        assert "Not now" in html
        # Step 2 follows a sign-up or a Google sign-in, never a password log-in.
        start = html.index("function signedIn(")
        body = html[start:html.index("function authPost(")]
        assert 'path !== "/api/login"' in body and 'openAuth("checkout")' in body

    def test_disclosure_sits_next_to_every_trial_button(self, html):
        disclosure = ('"Card required. Free for " + days + " days, then $9.99/month, '
                      'renews monthly " +\n      "until you cancel. Cancel anytime online."')
        assert disclosure in html
        # Both trial buttons (panel and dialog) get the line written right
        # beside them, as visible text (never a title= tooltip).
        assert 'id="pwbuy"' in html and 'id="pwnote" class="disc"' in html
        assert 'id="cobtn"' in html and 'id="codisc" class="disc"' in html
        assert "pn.textContent = note" in html
        assert 'getElementById("codisc").textContent = o.note' in html
        assert "title=" not in html[html.index('id="pwbuy"'):html.index('id="pwfb"')]

    def test_used_trial_and_lapsed_get_no_trial_wording(self, html):
        assert "me.trial_available !== false" in html
        assert '"Subscribe — $9.99/month"' in html
        assert "Your membership ended. Restart for $9.99/month." in html
        assert "You're signed in. Start your " in html
        assert "You're signed in. Subscribe to unlock all " in html

    def test_checkout_call_and_its_answers(self, html):
        assert '"/api/checkout"' in html
        start = html.index("function checkout(")
        body = html[start:html.index("function portal(")]
        assert "location.assign" in body
        assert '"already_member"' in body and "409" in body
        assert "Free-trial checkout is unavailable right now." in body
        assert "fallback_url" in body
        assert "Subscribe now &mdash; $9.99/month" in html  # the fallback link

    def test_portal_call_falls_back_then_hides(self, html):
        assert '"/api/portal"' in html
        start = html.index("function portal(")
        body = html[start:html.index("function showErr(")]
        assert "location.assign" in body and "portal_url" in body
        assert "btn.hidden = true" in body
        assert 'id="billing"' in html and ">Manage or cancel<" in html

    def test_trial_member_note_and_cancel_toast(self, html):
        assert '" $9.99/month unless you cancel."' in html
        assert "checkout=cancel" in html
        assert "Checkout cancelled — no charge." in html
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
        assert 'id="dropfill"' in html and ".dropbar{" in html
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
        assert "LATE_MS = 6 * 60 * 1000" in html
        start = html.index("function drop(")
        body = html[start:html.index("function loadData(")]
        assert "withTimeout(got, DROP_FETCH_MS)" in body
        assert "markIndex(last" in body and "reorganize(true)" in body
        assert '"Deals reshuffled" + checkedAgo()' in body
        assert '" · prices checked "' in html and '" min ago"' in html
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
        assert "/api/portal" in html and "Manage or cancel" in html

    def test_wrong_account_copy(self, html):
        assert "wrong_account:" in html
        assert ("This payment belongs to a different wowpriced account. "
                "Log in with the email you used at checkout.") in html


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
            assert "top 8 deals" in text and "free for everyone" in text, name
            assert "7-day free trial" in text and "card" in text, name
            assert "every deal on the wall" in text, name

    def test_subscribe_page_joins_through_the_trial_flow(self, db, cfg, tmp_path):
        html = _render_pages(db, cfg, tmp_path / "site")["subscribe.html"]
        assert '"./#signup"' in html and "m.api_url" in html
        assert "7-day free trial" in html and "card required" in html
