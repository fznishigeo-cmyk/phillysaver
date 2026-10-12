"""Tests for dealradar.publish.site — fully offline, explicit deals lists."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish.site import _serialize_deal, link_mix, render_site, rewrite_affiliate
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


def test_serialized_fields_have_consumers():
    # Maintain this list with the reader when adding a public field. The
    # Worker stores/serves the full payload verbatim, without field readers.
    fields = {
        "dedupe_key",  # index.html dealKey fallback
        "key",  # index.html dealKey; paywall rotation and STUB_KEYS
        "store",  # index.html cards/search/filter; SEO cards; STUB_KEYS
        "item",  # index.html cards/search/ticker; SEO cards
        "brand",  # index.html search haystack
        "url",  # index.html Get deal; SEO card links
        "image_url",  # index.html mediaHtml; STUB_KEYS
        "final_price",  # index.html cards/price sort/ticker; SEO cards
        "regular_price",  # index.html and SEO crossed-out card price
        "pct_at_checkout",  # index.html sort/filter/cards; SEO; STUB_KEYS
        "abs_savings",  # index.html savings sort/cards; SEO cards
        "flags",  # test_e2e_offline.py range-price assertion
        "codes",  # index.html and SEO how-to box; e2e promo assertion
        "needs_steps",  # index.html and SEO how-to box/Get deal
        "how",  # index.html and SEO how-to steps
        "conditions",  # index.html labels/category filter; SEO; STUB_KEYS
        "category",  # index.html filter/click tracking; SEO hubs; STUB_KEYS
        "badge",  # index.html source filter/card badge; SEO cards
        "headline",  # index.html and SEO card explanation
        "status",  # index.html/SEO alive/dead split; paywall rotation
        "alive",  # test_deals_json_has_disclosure_credit_badge_age/dead deal
        "died_at",  # index.html and SEO gone age label
        "gone",  # confirmed-death marker in deals.json
        "first_seen_at",  # index.html freshOrder; SEO hub lastmod; STUB_KEYS
        "age_hours",  # index.html age sort/card age; publish age assertion
        "credits",  # index.html and SEO source credit; publish/e2e assertions
        "src",  # index.html source filter; site source_sites; STUB_KEYS
    }
    assert set(_serialize_deal(make_deal(), NOW)) == fields


def test_public_json_is_compact_and_matches_members_payload(db, cfg, tmp_path):
    cfg.paywall_enabled = True
    cfg.paywall_open_access = True
    out = tmp_path / "site"
    result = render_site(db, cfg, NOW, out_dir=out, deals=[make_deal(
        stack_steps=["Clip the coupon", "Apply SAVE10"],
        image_url="https://images.example/lego.jpg")])
    text = (out / "deals.json").read_text(encoding="utf-8")
    assert json.loads(text) == result["full_payload"]
    assert text == json.dumps(result["full_payload"], separators=(",", ":"))


def test_render_site_writes_all_four_files(db, cfg, tmp_path):
    out = tmp_path / "site"
    deals = [make_deal(), make_deal(dedupe_key="target|mug", store="Target",
                                    item="Coffee Mug", status="dead", death="confirmed",
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
    # Code SAVE10: Get deal opens the source post that explains it.
    assert deal["needs_steps"] is True and deal["url"] == "https://slickdeals.net/f/1"
    # only extracted facts: no raw post text field in the output
    assert "text" not in deal


def test_dead_deal_serialized_as_not_alive(db, cfg, tmp_path):
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out,
                deals=[make_deal(status="dead", death="confirmed")], last_run=None)
    doc = json.loads((out / "deals.json").read_text(encoding="utf-8"))
    assert doc["deals"][0]["alive"] is False


@pytest.mark.parametrize("closed", [False, True])
def test_link_mix_counts_only_public_live_payload(db, cfg, tmp_path, closed):
    cfg.affiliate_amazon_tag = "our-store-20"
    cfg.paywall_enabled = closed
    cfg.paywall_teaser_count = 1
    out = tmp_path / "site"
    result = render_site(db, cfg, NOW, out_dir=out, deals=[
        make_deal(dedupe_key="amazon", canonical_url="https://amazon.com/dp/X", codes=[]),
        make_deal(dedupe_key="post"),
        make_deal(dedupe_key="dead", status="dead"),
    ])
    doc = json.loads((out / "deals.json").read_text())
    assert result["link_mix"] == link_mix(doc["deals"], cfg.affiliate_amazon_tag)
    assert result["link_mix"]["amazon_tagged"] == 1
    assert result["link_mix"]["source_post"] == (0 if closed else 1)
    assert "link_mix" not in doc["meta"]


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

    def test_meta_cadence_is_the_drop_knob_for_every_run_kind(self, db, cfg, tmp_path):
        """Review finding: a 10-minute clock against publishes that land every
        11-15 min (cron drift) said "Next drop soon" in 2 of 3 cycles. The
        step is config.yaml publish.drop_minutes, whichever lane published."""
        fast = self._meta(db, cfg, tmp_path / "fast",
                          {"run_key": "2026-10-09T14:00:fast", "kind": "fast"})
        hourly = self._meta(db, cfg, tmp_path / "hourly",
                            {"run_key": "2026-10-09T14", "kind": "hourly"})
        assert fast["cadence_minutes"] == hourly["cadence_minutes"] == cfg.drop_minutes == 15
        # The page never guesses the publish time: generated_at is always set.
        assert fast["generated_at"] == hourly["generated_at"] == iso(NOW)
        cfg.drop_minutes = 12
        assert self._meta(db, cfg, tmp_path / "none", None)["cadence_minutes"] == 12
        assert load_config().drop_minutes == 15

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
        # The disclosure sits in the footer, verbatim from meta; the run
        # time and deal count are the operator's, never shown (2026-10-12).
        assert html.index('id="metaline"') > html.index("<footer>")
        assert 'getElementById("metaline").textContent = m.disclosure || "";' in html
        assert "Last run" not in html
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
                          codes=[f"ZZCODE{i}"], stack_steps=[f"Clip ZZSTEP{i}"],
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
                                            status="dead", death="confirmed")]
        res = render_site(db, cfg, NOW, out_dir=out, deals=deals)
        text = (out / "deals.json").read_text(encoding="utf-8")
        for i in (0, 1, 2):
            assert f"ZZSECRET-ITEM-{i}" in text and f"ZZCODE{i}" in text
        for i in (3, 4):  # locked: no name, price, link or headline anywhere
            assert f"ZZSECRET-ITEM-{i}" not in text
            assert f"ZZCODE{i}" not in text and f"ZZSTEP{i}" not in text
            assert f"walmart.com/ip/zz{i}" not in text
            assert f"100{i}.0" not in text and f"1,00{i}.00" not in text
            assert f"500{i}.0" not in text
        assert "ZZSECRET-MUG" in text  # just-missed deals stay public

        # Every HTML page written (the static wall, the category hubs, 404)
        # holds the free deals at most: never a locked deal's name, price,
        # link or photo. Only public Product JSON-LD carries photo URLs;
        # static cards still use store tiles. deals.json's locked stubs
        # carry image_url by contract.
        pages = list(out.rglob("*.html"))
        assert out / "index.html" in pages and out / "deals" / "other" / "index.html" in pages
        for page in pages:
            html = page.read_text(encoding="utf-8")
            for i in (3, 4):
                assert f"ZZSECRET-ITEM-{i}" not in html, page
                assert f"ZZCODE{i}" not in html and f"ZZSTEP{i}" not in html, page
                assert f"walmart.com/ip/zz{i}" not in html, page
                assert f"100{i}.0" not in html and f"500{i}.0" not in html, page
                assert f"zzimg{i}" not in html, page
            without_ld = re.sub(r'<script type="application/ld\+json">.*?</script>', "", html, flags=re.S)
            assert "zzimg" not in without_ld, page
        home = (out / "index.html").read_text(encoding="utf-8")
        assert "ZZSECRET-ITEM-0" in home
        # A free deal's code and steps are on the static wall too.
        assert '<code class="code">ZZCODE0</code>' in home and "Clip ZZSTEP0" in home

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


class TestDropCadenceCopy:
    @pytest.mark.parametrize("open_access", [False, True])
    @pytest.mark.parametrize("drop_minutes", [15, 12])
    def test_every_page_uses_configured_cadence(self, db, cfg, tmp_path,
                                              open_access, drop_minutes):
        cfg.paywall_open_access = open_access
        cfg.drop_minutes = drop_minutes
        out = tmp_path / "site"
        render_site(db, cfg, NOW, out_dir=out, deals=[])
        pages = list(out.rglob("*.html"))
        assert (out / "404.html") in pages
        assert any("deals" in p.relative_to(out).parts for p in pages)
        for page in pages:
            html = " ".join(page.read_text(encoding="utf-8").split())
            for stale in ("{{DROP_MINUTES}}", "10 minutes", "10-minute"):
                assert stale not in html, (page, stale)
            assert f"every {drop_minutes} minutes" in html, page
        how = (out / "how-it-works.html").read_text(encoding="utf-8")
        assert f"a {drop_minutes}-minute" in how


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

    def test_the_step_is_the_published_cadence_never_a_fixed_10(self, html):
        step = _between(html, "function dropStepMs(", "function dropPhase(")
        assert "(c > 0 ? c : DROP_MIN) * 60 * 1000" in step and "Math.min" not in step
        assert "var DROP_MIN = 15;" in html and "FAST_LANE_MIN" not in html

    def test_a_page_opened_during_a_wait_waits_and_polls(self, html):
        """Review finding: a page first loaded after its publish's mark had
        passed counted to the mark after next and never polled, so the late
        publish reached it only through the 5-minute fallback."""
        sync = _between(html, "function syncClock(", "function paintClock(")
        assert ("if (pub != null && pub + clock.step <= now && !clock.waiting) "
                "clock.waiting = clock.lastPoll = now;") in sync
        assert sync.index("if (!clock.dropping) {") < sync.index("clock.waiting = clock.lastPoll")

    def test_a_publish_that_lands_before_its_mark_waits_for_it(self, html):
        """With the clock stepping at the real cadence most publishes land
        before their mark; the 5-minute fallback put them up early and cut
        the countdown short. It now runs only while the clock waits."""
        load = _between(html, "function loadData(", "loadData();")
        assert load.index("if (loadedOnce && !clock.waiting) return;") < load.index("fetchPublish()")

    def test_a_drop_never_presents_a_publish_twice_or_parks_after_one(self, html):
        """Review findings: a member's drop presented a publish the fallback
        had already put up (NEW tags wiped, toast replaced), and a drop whose
        publish another path put up parked the clock on "Next drop soon"."""
        body = _between(html, "function drop(", "function loadData(")
        assert "var key0 = state.dataKey" in body
        finish = _between(html, "function finish(", "got.catch(noop);")
        assert "if (pub && isNewPublish(pub)) { present(pub, deals, seed); return; }" in finish
        assert "if (state.dataKey !== key0) { syncClock(); return; }" in finish
        assert finish.index("state.dataKey !== key0") < finish.index("clock.waiting = clock.lastPoll")

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
        polls (HEAD, then the file if it changed). Operator (2026-10-12): it
        must never park on "Next drop soon" (it looked stuck while the hourly
        run held the queue), so it counts on to the next mark, which is a
        drop attempt like any other."""
        finish = _between(html, "function finish(", "got.catch(noop);")
        assert "present(pub, deals, seed); return; }" in finish
        assert "clock.waiting = clock.lastPoll = Date.now();" in finish
        assert "reorganize(" not in finish
        assert "present(pub, deals, null);" in _between(html, "function landed(", "function merge(")
        tick = _between(html, "function tick(", "setInterval(tick, 1000)")
        assert "if (!clock.dropping && loadedOnce && clock.target && now >= clock.target)" in tick
        assert "else if (!clock.dropping && clock.waiting)" in tick and "poll();" in tick
        poll = _between(html, "function poll(", "function updatedAgo(")
        assert 'method: "HEAD"' in poll and "fetchPublish().then(merge)" in poll
        clockp = _between(html, "function paintClock(", "function paintDial(")
        assert 'clock.dropping ? "Dropping…" : mmss(left)' in clockp
        assert '"soon"' not in html and "wait ?" not in clockp
        # drop() moves the target to the next mark before it knows whether
        # a publish came, so a late one is counted toward, never parked on.
        drop = _between(html, "function drop(", "function finish(")
        assert "clock.target = nextMarkAfter(now, clock.phase, clock.step);" in drop
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
        assert "Then your chosen plan renews automatically " in html
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
            assert f"new deals every {cfg.drop_minutes} minutes" in text, name
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
        assert '.textContent = blk ? blockText(blk) : open[mode] || {' in panel
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
        assert 'var firstLocked = list.findIndex(isLocked);' in grid
        assert 'trialBand() + gridHtml(list.slice(firstLocked))' in grid
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
        assert "/^[a-z0-9.-]+$/i.test(x)" in paint
        assert 'document.getElementById(id).innerHTML = h;' in paint and "h + h" not in paint
        assert ".srctrack{animation:none}" in html

    def test_only_the_ticker_loops_and_reduced_motion_stops_it(self, html):
        css = _between(html, "<style>", "</style>")
        looping = re.findall(r"([^{}]+)\{([^{}]*\binfinite\b[^{}]*)\}", css)
        assert len(looping) == 1 and looping[0][0].strip() == ".tkstrip"
        reduced = _between(css, "@media (prefers-reduced-motion: reduce){\n  .trialcta", "\n}")
        assert ".tkstrip{animation:none}" in reduced


def test_trial_copy_names_the_price(db, cfg, tmp_path):
    """Operator (2026-10-12): the sticky panel and the in-wall band say the
    $9.99/month next to the free days."""
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=[])
    html = (out / "index.html").read_text(encoding="utf-8")
    assert '"your first " + o.days + " days free, then " + prices()' in html
    assert '"-day free trial, then " + prices() + "."' in html
    assert '" — your first " + o.days + " days free, then " + prices() + "."' in html


def test_visitors_see_a_rounded_deal_count(db, cfg, tmp_path):
    """Operator (2026-10-12): "1,400+ live deals", never the exact count."""
    from dealradar.publish import seo

    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=[])
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'n < 100 ? String(n) : (Math.floor(n / 100) * 100).toLocaleString("en-US") + "+"' in html
    assert '(live < 100 ? plural(live, "live deal") : roughCount(live) + " live deals")' in html
    assert "var total = roughCount(dealTotal())" in html
    assert "all \" + dealTotal()" not in html and "+ dealTotal() +" not in html
    alive = [{"status": "alive", "item": f"x{i}", "pct": 50} for i in range(seo._STATIC_WALL_MAX + 1432)]
    assert "1,000+ more deals on the live wall" in seo.static_wall(alive, True)
    assert "400+ more deals on the live wall" in seo.static_wall(alive[:seo._STATIC_WALL_MAX + 432], True)


def test_fresh_drops_lead_with_photos(db, cfg, tmp_path):
    """Operator (2026-10-12): the top of the wall showed store tiles. In
    fresh drops a deal without a photo counts as NO_PHOTO_H hours older
    (still on the wall, never hidden), new arrivals included."""
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=[])
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "var NO_PHOTO_H = 12;" in html
    body = _between(html, "function freshOrder(", "function ordered(")
    assert "(state.newKeys[k] ? -1e9 : 0) + s + (d.image_url ? 0 : NO_PHOTO_H)" in body


def test_hover_never_moves_what_the_pointer_rests_on(db, cfg, tmp_path):
    """Operator report (2026-10-11): the page "moves up every few seconds".
    A card that lifts on :hover slides out from under a resting pointer,
    loses :hover, drops back and bobs on its own; the hovered element may
    restyle, never move (a child may: the logo mark grows in place)."""
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=[])
    for page in out.glob("*.html"):
        css = page.read_text(encoding="utf-8")
        for rule in re.findall(r":hover\s*\{[^}]*\}", css):
            assert "transform" not in rule and "translate" not in rule, (page.name, rule)


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


class TestOpenAccess:
    """Operator, 2026-10-11: every visitor sees every deal in full, nothing
    blurred (config.yaml paywall.open_access). Sign-up stays as it is; no
    page calls a deal members-only or says membership unlocks it."""

    @staticmethod
    def _open(cfg, on=True):
        cfg.paywall_enabled = True
        cfg.paywall_open_access = on
        cfg.paywall_teaser_count = 3
        cfg.paywall_api_url = "https://api.example.workers.dev"
        return cfg

    @staticmethod
    def _deals(n):
        return [make_deal(dedupe_key=f"walmart|open-{i}", item=f"OPEN-ITEM-{i}",
                          canonical_url=f"https://www.walmart.com/ip/open{i}",
                          score=100.0 - i) for i in range(n)]

    def test_every_deal_is_full_and_the_rotation_pauses(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        db.meta_set("teaser_recent", '["k-before"]')
        render_site(db, self._open(cfg), NOW, out_dir=out, deals=self._deals(6))
        doc = json.loads((out / "deals.json").read_text(encoding="utf-8"))
        assert len(doc["deals"]) == 6
        assert not any(d.get("locked") for d in doc["deals"])
        assert all(d["url"] and d["item"].startswith("OPEN-ITEM-") for d in doc["deals"])
        assert doc["meta"]["open_access"] is True and doc["meta"]["locked_count"] == 0
        # Sign-up is unchanged: the Worker and the trial stay in meta.
        assert doc["meta"]["api_url"] == "https://api.example.workers.dev"
        assert doc["meta"]["trial_days"] == 7
        # The free-8 memory is left as it was, to resume when it closes.
        assert db.meta_get("teaser_recent") == '["k-before"]'

    def test_closing_it_again_brings_the_locks_back(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        render_site(db, self._open(cfg, on=False), NOW, out_dir=out, deals=self._deals(6))
        doc = json.loads((out / "deals.json").read_text(encoding="utf-8"))
        assert doc["meta"]["open_access"] is False and doc["meta"]["locked_count"] == 3
        assert sum(1 for d in doc["deals"] if d.get("locked")) == 3

    def test_no_page_calls_a_deal_members_only(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        render_site(db, self._open(cfg), NOW, out_dir=out, deals=self._deals(6))
        pages = {p.relative_to(out).as_posix(): p.read_text(encoding="utf-8")
                 for p in out.rglob("*.html")}
        for name, html in pages.items():
            # What a visitor reads: the page's script draws locked cards only
            # for locked stubs, and there are none.
            text = " ".join(re.sub(r"<script\b.*?</script>", "", html, flags=re.S).split())
            assert "<!--paywall-->" not in html and "<!--open-->" not in html, name
            for claim in ("Eight deals stay free", "Eight top deals", "a new eight",
                          "unlocks every deal", "Every deal, unlocked", "members only",
                          "Members-only deals", "new set of free deals",
                          "the membership below fixes it", "Free right now"):
                assert claim not in text, (name, claim)
            for stale in ("free and always will be", "the site stays free",
                          "the wall stays free"):
                assert stale not in text.lower(), (name, stale)
        home = " ".join(pages["index.html"].split())
        assert "Every deal on the wall is open to everyone right now" in home
        assert "Membership — $9.99/month." in home and 'id="footterms"' in home
        assert "OPEN-ITEM-5" in home  # the static wall carries every deal
        assert "<h1>Membership</h1>" in pages["subscribe.html"]
        assert "Start 7-day free trial" in pages["subscribe.html"]
        # The sign-up dialog drops "unlock" while open (meta.open_access).
        assert "function openAccess()" in pages["index.html"]
        assert '"Free account first, then "' in pages["index.html"]

    def test_paywall_wording_when_closed(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        render_site(db, self._open(cfg, on=False), NOW, out_dir=out, deals=self._deals(6))
        for p in out.rglob("*.html"):
            html = p.read_text(encoding="utf-8")
            assert "<!--paywall-->" not in html and "<!--open-->" not in html, p.name
            assert "open to everyone right now" not in html, p.name
        home = " ".join((out / "index.html").read_text(encoding="utf-8").split())
        assert "Eight deals stay free for everyone, new at every drop." in home

    def test_static_pages_cap_the_cards(self, db, cfg, tmp_path):
        from dealradar.publish import seo

        out = tmp_path / "site"
        n = seo._STATIC_WALL_MAX + 5
        render_site(db, self._open(cfg), NOW, out_dir=out, deals=self._deals(n))
        home = (out / "index.html").read_text(encoding="utf-8")
        wall = home.split('<div id="content">', 1)[1].split('<div id="paywall"', 1)[0]
        assert wall.count('<article class="card') == seo._STATIC_WALL_MAX
        assert "5 more deals on the live wall" in wall
        doc = json.loads((out / "deals.json").read_text(encoding="utf-8"))
        assert len(doc["deals"]) == n  # the live wall still has every one

    def test_review_fixes(self, db, cfg, tmp_path):
        from dealradar.publish.site import site_template_version

        out = tmp_path / "site"
        render_site(db, self._open(cfg), NOW, out_dir=out, deals=self._deals(3))
        welcome = (out / "welcome.html").read_text(encoding="utf-8")
        assert "Every deal on the wall is yours now" not in welcome
        assert "Thanks for joining." in welcome
        home = (out / "index.html").read_text(encoding="utf-8")
        assert "8 free deals, new at every drop" not in home
        assert '<p id="authsub" class="meta">Free account first, then your 7-day free trial.</p>' in home
        # Sign-up keeps its in-wall band while open, with nothing to unlock,
        # and the sticky trial panel follows every non-member down the wall.
        assert "function openBand()" in home and "openBand() + gridHtml" in home
        assert "updatePaywall(rows.some(isLocked) || openNudge())" in home
        assert "openAccess() && !!apiUrl() && !isMember()" in home
        # Members read the public file while open (the Worker copy can lag).
        assert "(pub.meta || {}).open_access ||" in home
        # A flip of the mode changes the version, so open tabs reload.
        meta = json.loads((out / "deals.json").read_text(encoding="utf-8"))["meta"]
        assert meta["site_version"] == site_template_version() + "-open"
        assert f'var SITE_VERSION = "{site_template_version()}-open"' in home
        closed = tmp_path / "closed"
        render_site(db, self._open(cfg, on=False), NOW, out_dir=closed, deals=self._deals(3))
        meta = json.loads((closed / "deals.json").read_text(encoding="utf-8"))["meta"]
        assert meta["site_version"] == site_template_version()
        assert "Every deal on the wall is yours now" in (closed / "welcome.html").read_text(encoding="utf-8")


class TestWallPagination:
    @pytest.fixture()
    def html(self, db, cfg, tmp_path):
        out = tmp_path / "site"
        render_site(db, cfg, NOW, out_dir=out, deals=[])
        return (out / "index.html").read_text(encoding="utf-8")

    def test_one_budget_caps_live_and_inline_gone_cards(self, html):
        assert "var PAGE_CARDS = 60;" in html
        assert "shown: PAGE_CARDS" in html
        body = _between(html, "function render() {", "function showMore()")
        assert "remaining = state.shown" in body
        assert "list = list.slice(0, remaining);" in body
        assert "remaining -= list.length;" in body
        assert "strong.length && remaining > 0" in body
        assert "rows = placeGone(rows);" in body
        assert 'secth missed' not in body
        assert body.index("grid(headliners)") < body.index("grid(strong)")
        # The band eligibility is decided using the full section, so growing
        # a partial grid never puts a band ahead of already visible cards.
        assert body.index("var openBandHere") < body.index("list = list.slice")
        assert "if (openBandHere && list.length > 8)" in body

    def test_all_tab_scrolls_a_little_categories_scroll_on(self, html):
        # Operator, 2026-10-12: All loads one more page by itself, then a click
        # (the footer is reachable); category tabs scroll without end.
        body = _between(html, "function render() {", "// ---- reorganizing the wall")
        assert "rows.length > state.shown" in body
        assert ('<div class="more"><button type="button" id="moremore" class="pwbtn alt">Show more deals</button>'
                in body) and '<button type="button" id="totop" class="totop">Back to top</button></div>' in body
        assert 'button.addEventListener("click", showMore)' in body and "armMore(more);" in body
        assert 'window.scrollTo({top: 0, behavior: reduceMotion() ? "auto" : "smooth"})' in body
        assert "moreObserver.disconnect()" in body
        arm = _between(html, "function armMore(more)", "function showMore()")
        assert "if (state.catTab === 0 && state.shown >= AUTO_CARDS) return;" in arm
        assert "entry.isIntersecting && document.body.contains(entry.target)" in arm
        assert 'rootMargin: "1500px"' in arm and "moreObserver.observe(more)" in arm
        assert "var AUTO_CARDS = 120;" in html
        more = _between(html, "function showMore()", "// ---- reorganizing the wall")
        assert "state.shown += PAGE_CARDS;" in more and "render();" in more
        assert "rememberFocus()" in more and "restoreFocus" in more
        for forbidden in ("reorganize(", "toast(", "state.seed =", "present("):
            assert forbidden not in more

    def test_cta_precedes_more(self, html):
        body = _between(html, "function render() {", "function showMore()")
        assert '.missed' not in body
        assert 'var more = content.querySelector(".more");' in body
        assert "content.insertBefore(panel, more)" in body

    def test_filters_sort_and_category_reset_but_publishes_do_not(self, html):
        reset = _between(html, "function resetWall()", "// Easy sorting:")
        assert "state.shown = PAGE_CARDS;" in reset and "render();" in reset
        assert '["storesel", "minpct", "srcsel"]' in reset
        search = _between(reset, "function updateSearch()", "function clearSearch()")
        assert "resetWall();" in search
        assert 'searchTimer = setTimeout(updateSearch, 150)' in reset
        assert 'el.addEventListener("input", resetWall)' in reset
        assert 'el.addEventListener("change", resetWall)' in reset
        sort = _between(html, 'sortsel.addEventListener("change"', "(function restoreSort()")
        assert "resetWall();" in sort
        tabs = _between(html, "function renderTabs()", "function filtered()")
        assert "state.shown = PAGE_CARDS;" in tabs
        publish = _between(html, "function applyPublish(", "function loadData(")
        assert "state.shown" not in publish and "resetWall" not in publish

    def test_site_check_reports_rendered_and_total(self):
        script = (Path(__file__).resolve().parents[1] / "scripts/site_check.js").read_text()
        assert "cards rendered ${shown.cards} of ${alive.length} live" in script
        assert "cards on the page" not in script


ENGAGEMENT_SWITCHES = ("new_since_visit", "weekly_finds", "visit_streak", "surprise_me", "hot_meter")


def test_engagement_config_defaults_and_independent_switches(tmp_path):
    path = tmp_path / "engagement.yaml"
    path.write_text("{}")
    assert load_config(path).engagement == dict.fromkeys(ENGAGEMENT_SWITCHES, True)
    assert load_config().engagement == dict.fromkeys(ENGAGEMENT_SWITCHES, True)
    for name in ENGAGEMENT_SWITCHES:
        path.write_text(f"engagement:\n  {name}: false\n")
        assert load_config(path).engagement == {key: key != name for key in ENGAGEMENT_SWITCHES}


@pytest.mark.parametrize("mode", ["support", "open", "paywall"])
@pytest.mark.parametrize("disabled", [None, *ENGAGEMENT_SWITCHES, "all"])
def test_engagement_switches_reach_public_and_member_meta(db, cfg, tmp_path, mode, disabled):
    cfg.engagement = {name: disabled not in (name, "all") for name in ENGAGEMENT_SWITCHES}
    cfg.support_enabled = mode == "support"
    cfg.paywall_open_access = mode == "open"
    result = render_site(db, cfg, NOW, out_dir=tmp_path, deals=[make_deal()])
    public = json.loads((tmp_path / "deals.json").read_text())
    assert public["meta"]["engagement"] == cfg.engagement
    assert result["full_payload"]["meta"]["engagement"] == cfg.engagement
    home = (tmp_path / "index.html").read_text()
    assert "{{" not in home
    tag = re.search(r'<button id="surprise"[^>]*>', home).group()
    assert ("hidden" in tag) is (not cfg.engagement["surprise_me"])
    header = re.search(r"<header>.*?</header>", home, re.S).group()
    assert "weeklyfinds" not in header and "visitstreak" not in header
    assert "engagement" not in header
    for reserved in ("engagement-slot", "engagement-clock", "visit-header", "engagement-chip"):
        assert reserved not in home
    assert ".sr-only{position:absolute;width:1px;height:1px;" in home
    # Turning engagement on must leave the pre-task header markup intact.
    cfg.engagement = {}
    render_site(db, cfg, NOW, out_dir=tmp_path / "baseline", deals=[make_deal()])
    baseline = (tmp_path / "baseline" / "index.html").read_text()
    assert header == re.search(r"<header>.*?</header>", baseline, re.S).group()
    cfg.engagement = public["meta"]["engagement"]
    explanation = (tmp_path / "how-it-works.html").read_text()
    assert ("Flames mark real signals:" in explanation) is cfg.engagement["hot_meter"]
    if cfg.engagement["hot_meter"]:
        assert "at least two are needed" in explanation
        assert "Two signals show two flames; all three show three." in explanation


def test_engagement_published_logic_offline(db, cfg, tmp_path):
    import shutil
    import subprocess

    if not shutil.which("node"):
        pytest.skip("node unavailable")
    cfg.engagement = dict.fromkeys(ENGAGEMENT_SWITCHES, True)
    cfg.support_enabled = True
    render_site(db, cfg, NOW, out_dir=tmp_path, deals=[make_deal()])
    home = tmp_path / "index.html"
    for i, code in enumerate(re.findall(r"<script>(.*?)</script>", home.read_text(), re.S)):
        script = tmp_path / f"engagement-{i}.js"
        script.write_text(code)
        subprocess.run(["node", "--check", str(script)], check=True, capture_output=True, text=True)
    result = subprocess.run(["node", str(Path(__file__).with_name("engagement_logic.cjs")), str(home)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_surprise_event_dispatch_and_private_fields_stay_local(tmp_path):
    import shutil
    import subprocess

    from dealradar.publish.tracking import head_snippet
    from tests.test_tracking import HARNESS, SAMPLE, _script

    cfg = AppConfig(tracking=SAMPLE, engagement=dict.fromkeys(ENGAGEMENT_SWITCHES, True))
    snippet = head_snippet(cfg)
    assert "surprise_click:" in snippet
    cfg.engagement["surprise_me"] = False
    assert "surprise_click:" not in head_snippet(cfg)
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    (tmp_path / "snippet.js").write_text(_script(snippet))
    (tmp_path / "harness.cjs").write_text(HARNESS)
    (tmp_path / "scenarios.json").write_text(json.dumps({"surprise": {"calls": [[
        "surprise_click", {"finds": 100, "streak": 5, "lastVisit": "private", "key": "private"}
    ]]}}))
    result = subprocess.run(["node", "harness.cjs", "snippet.js", "scenarios.json"], cwd=tmp_path,
                            capture_output=True, text=True, check=True)
    events = json.loads(result.stdout)["surprise"]
    assert any(event[:2] == ["event", "surprise_click"] for event in events["dataLayer"])
    assert any(event[:2] == ["trackCustom", "SurpriseClick"] for event in events["fbq"])
    assert "private" not in result.stdout and "lastVisit" not in result.stdout


def test_tab_title_stays_short():
    """Operator, 2026-10-12: the tab cut off at "40-"; it says only this."""
    html = (Path(__file__).resolve().parents[1] / "dealradar/publish/templates/index.html").read_text()
    assert "<title>wowpriced — verified deals</title>" in html
    assert "document.title" not in html


def test_shown_counts_top_out_at_1000():
    """Operator, 2026-10-12: "1,000+ live deals" and keep it that way."""
    import subprocess
    html = (Path(__file__).resolve().parents[1] / "dealradar/publish/templates/index.html").read_text()
    start = html.index("  function roughCount(n) {")
    fn = html[start:html.index("\n  }\n", start) + 4]
    js = fn + "console.log([42, 140, 999, 1000, 2140, 9000].map(roughCount).join('|'));"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "42|100+|900+|1,000+|1,000+|1,000+"
