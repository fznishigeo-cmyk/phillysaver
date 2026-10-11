"""Analytics and ad tags (publish/tracking.py), the privacy page and the
conversion hooks: fully offline (the consent behaviour runs under node)."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from dealradar.config import ROOT, AppConfig, load_config
from dealradar.db import DB
from dealradar.publish.site import render_site
from dealradar.publish.tracking import PATTERNS, apply_tracking, head_snippet, inject

NOW = datetime(2026, 10, 11, 14, 0, 0, tzinfo=timezone.utc)
START, END = "<!-- wp-tracking -->", "<!-- /wp-tracking -->"

SAMPLE = {
    "ga4_measurement_id": "G-ABC123XYZ9",
    "meta_pixel_id": "123456789012345",
    "tiktok_pixel_id": "C4ABCDEFGH12345678",
    "pinterest_tag_id": "2612345678901",
    "google_ads_id": "AW-123456789",
    "google_ads_trial_label": "AbC-dEf_123",
    "bing_uet_id": "187654321",
    "google_site_verification": "abcDEF123_-xyzXYZ7890abcdEFGH",
    "bing_site_verification": "0123456789ABCDEF0123456789ABCDEF",
    "pinterest_domain_verify": "0123456789abcdef0123456789abcdef",
    "facebook_domain_verification": "abcdefghij0123456789klmnopqrst",
}
HOSTS = ("googletagmanager.com", "connect.facebook.net", "analytics.tiktok.com",
         "s.pinimg.com", "bat.bing.com")
NESTED = "<!doctype html>\n<html><head><title>Hub</title>\n</head><body><p>hub</p></body></html>\n"


@pytest.fixture()
def db(tmp_path):
    database = DB(tmp_path / "tracking_test.db")
    yield database
    database.close()


@pytest.fixture()
def cfg(tmp_path):
    return AppConfig(site_dir=str(tmp_path / "site_default"))


def _pages(out: Path) -> dict[str, str]:
    return {str(p.relative_to(out)): p.read_text(encoding="utf-8") for p in out.rglob("*.html")}


def _script(snippet: str) -> str:
    return snippet[snippet.index("<script>\n") + 9:snippet.index("</script>")]


def _between(text: str, start: str, end: str) -> str:
    i = text.index(start)
    return text[i:text.index(end, i)]


# ---- config ----

def test_shipped_config_lists_every_key_and_only_valid_values():
    raw = yaml.safe_load((ROOT / "config.yaml").read_text())["tracking"]
    assert set(raw) == set(PATTERNS)
    # Empty ships nothing; a pasted ID must be a real one (a bad paste fails here).
    pasted = {k: str(v).strip() for k, v in raw.items() if v not in (None, "")}
    for key, val in pasted.items():
        assert re.fullmatch(PATTERNS[key], val, re.ASCII), key
    assert load_config().tracking == pasted


def test_loader_reads_the_block(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("tracking:\n  ga4_measurement_id: ' G-ABC123 '\n"
                    "  meta_pixel_id: 1234567\n  tiktok_pixel_id: ''\n")
    assert load_config(path).tracking == {"ga4_measurement_id": "G-ABC123",
                                          "meta_pixel_id": "1234567"}
    path.write_text("tracking: nonsense\n")
    assert load_config(path).tracking == {}


# ---- the post-pass ----

def test_no_ids_means_no_tag_and_no_third_party_url(db, cfg, tmp_path):
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=[])
    assert head_snippet(cfg) == "" and apply_tracking(out, cfg) == 0
    pages = _pages(out)
    assert "privacy.html" in pages
    for name, page in pages.items():
        assert "wp-tracking" not in page and "window.wpTrack = function" not in page, name
        assert "verification" not in page and "msvalidate" not in page, name
        for host in HOSTS:
            assert host not in page, (name, host)


def test_sample_ids_put_the_snippet_in_every_page_exactly_once(db, cfg, tmp_path):
    cfg.tracking = dict(SAMPLE)
    out = tmp_path / "site"
    (out / "deals" / "x").mkdir(parents=True)
    (out / "deals" / "x" / "index.html").write_text(NESTED, encoding="utf-8")
    render_site(db, cfg, NOW, out_dir=out, deals=[])
    render_site(db, cfg, NOW, out_dir=out, deals=[])
    assert apply_tracking(out, cfg) == 0  # a second pass changes nothing
    pages = _pages(out)
    assert {"deals/x/index.html", "index.html", "welcome.html", "privacy.html"} <= set(pages)
    for name, page in pages.items():
        assert page.count(START) == 1 and page.count(END) == 1, name
        assert page.index(END) < page.lower().index("</head>"), name
        for host in HOSTS:
            assert page.count(host) == 1, (name, host)
        for meta in ('<meta name="google-site-verification" content="abcDEF123_-xyzXYZ7890abcdEFGH">',
                     '<meta name="msvalidate.01" content="0123456789ABCDEF0123456789ABCDEF">',
                     '<meta name="p:domain_verify" content="0123456789abcdef0123456789abcdef">',
                     '<meta name="facebook-domain-verification" content="abcdefghij0123456789klmnopqrst">'):
            assert page.count(meta) == 1, (name, meta)
    # Still self-contained: every tag loads from script, none from markup.
    for name in ("index.html", "about.html", "welcome.html", "privacy.html"):
        assert "<script src=" not in pages[name] and 'rel="stylesheet"' not in pages[name], name


def test_changed_ids_replace_the_block_and_cleared_ids_remove_it():
    one = AppConfig(tracking=dict(SAMPLE))
    two = AppConfig(tracking={"ga4_measurement_id": "G-OTHER1"})
    once = inject(NESTED, head_snippet(one))
    swapped = inject(once, head_snippet(two))
    assert swapped.count(START) == 1 and "G-OTHER1" in swapped
    assert "connect.facebook.net" not in swapped and "G-ABC123XYZ9" not in swapped
    assert inject(swapped, head_snippet(AppConfig())) == NESTED
    assert inject("<p>no head</p>", head_snippet(one)) == "<p>no head</p>"


@pytest.mark.parametrize("key,bad", [
    ("ga4_measurement_id", 'G-123"><script>alert(1)</script>'),
    ("ga4_measurement_id", "UA-12345-1"),
    ("meta_pixel_id", "12345abc"),
    ("meta_pixel_id", "١٢٣٤٥٦٧"),  # non-ASCII digits
    ("tiktok_pixel_id", "C4AB</script>"),
    ("pinterest_tag_id", "26123 45678"),
    ("google_ads_id", "AW-12x456"),
    ("google_ads_trial_label", "a'b\"c"),
    ("bing_uet_id", "1234567;alert(1)"),
    ("google_site_verification", '"><img src=x onerror=alert(1)>'),
    ("bing_site_verification", "0123456789ABCDEF"),
    ("pinterest_domain_verify", "zz23456789abcdef0123456789abcdef"),
    ("facebook_domain_verification", "abc def ghi jkl"),
])
def test_invalid_ids_are_skipped_with_a_warning(caplog, key, bad):
    caplog.set_level(logging.WARNING, logger="dealradar.publish.tracking")
    snippet = head_snippet(AppConfig(tracking={key: bad}))
    assert snippet == ""
    assert f"tracking.{key} is not a valid value" in caplog.text
    assert bad.strip() not in caplog.text  # the key is logged, never the value


def test_one_bad_id_leaves_the_good_ones():
    snippet = head_snippet(AppConfig(tracking={"ga4_measurement_id": "G-ABC123XYZ9",
                                               "meta_pixel_id": '1"><script>'}))
    assert "googletagmanager.com/gtag/js" in snippet and "facebook" not in snippet
    assert "<script>" not in snippet.replace("<script>\n", "", 1)


# ---- consent ----

def _gated(js: str, at: int) -> bool:
    """The code at index `at` sits inside an `if (ads) {` block."""
    gate = js.rfind("if (ads) {", 0, at)
    if gate < 0:
        return False
    seg = js[gate + len("if (ads) "):at]
    return seg.count("{") - seg.count("}") >= 1


@pytest.mark.parametrize("ids", [SAMPLE, {"google_ads_id": "AW-123456789"},
                                 {k: v for k, v in SAMPLE.items() if k != "ga4_measurement_id"}])
def test_the_consent_gate_wraps_every_ad_pixel_loader(ids):
    js = _script(head_snippet(AppConfig(tracking=dict(ids))))
    assert 'localStorage.getItem("wp.adsOptOut") === "1"' in js
    assert "navigator.globalPrivacyControl !== true" in js
    loads = [m.start() for m in re.finditer(r"(?<!function )(?<!\.)\bload\(", js)]
    assert loads
    for at in loads:
        line = js[js.rfind("\n", 0, at):js.find("\n", at)]
        if '"G-' in line:
            assert not _gated(js, at), line  # GA4 is for everyone
        else:
            assert _gated(js, at), line
    for needle in ('fbq("set", "autoConfig", false', 'fbq("init"', 'ttq.load("', 'pintrk("load"',
                   "window.uetq = window.uetq", 'gtag("config", "AW-'):
        if needle in js:
            assert _gated(js, js.index(needle)), needle
    if "G-ABC123XYZ9" in js:
        assert "{ allow_google_signals: false, allow_ad_personalization_signals: false }" in js
    if 'fbq("init"' in js:  # Meta's automatic click/page scraping is off before init
        assert js.index('fbq("set", "autoConfig", false, "') < js.index('fbq("init"')


HARNESS = r"""
const vm = require("vm");
const fs = require("fs");
const code = fs.readFileSync(process.argv[2], "utf8");
const scenarios = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const plain = (a) => Array.from(a, (x) => (x && typeof x === "object" && "length" in x ? Array.from(x) : x));
const out = {};
for (const [name, sc] of Object.entries(scenarios)) {
  const srcs = [];
  const store = sc.storage || {};
  const ctx = {
    navigator: sc.gpc ? { globalPrivacyControl: true } : {},
    localStorage: sc.blocked ? { getItem() { throw new Error("blocked"); } }
                             : { getItem: (k) => (k in store ? store[k] : null) },
    document: { createElement: () => ({}), head: { appendChild: (s) => srcs.push(s.src) } },
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  if (sc.breakMeta) ctx.fbq = () => { throw new Error("meta down"); };
  let error = null;
  try { for (const c of sc.calls || []) ctx.wpTrack(c[0], c[1]); } catch (e) { error = String(e); }
  out[name] = {
    srcs, error,
    dataLayer: ctx.dataLayer ? ctx.dataLayer.map((a) => Array.from(a)) : null,
    fbq: ctx.fbq && ctx.fbq.queue ? ctx.fbq.queue.map((a) => Array.from(a)) : null,
    ttq: ctx.ttq ? plain(ctx.ttq) : null,
    pintrk: ctx.pintrk ? ctx.pintrk.queue : null,
    uetq: ctx.uetq ? Array.from(ctx.uetq) : null,
  };
}
process.stdout.write(JSON.stringify(out));
"""

CALLS = [["deal_click", {"store": "Walmart", "category": "home", "item": "ZZSECRET",
                         "final_price": 12.34, "url": "https://zz.example/secret"}],
         ["start_trial", None], ["sign_up", {"method": "email"}], ["begin_checkout"],
         ["not_an_event", {"store": "x"}]]


def _run_node(tmp_path: Path, ids: dict, scenarios: dict) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    (tmp_path / "snippet.js").write_text(_script(head_snippet(AppConfig(tracking=dict(ids)))))
    (tmp_path / "scenarios.json").write_text(json.dumps(scenarios))
    (tmp_path / "harness.cjs").write_text(HARNESS)
    res = subprocess.run(["node", "harness.cjs", "snippet.js", "scenarios.json"], cwd=tmp_path,
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_consent_and_event_fan_out_in_a_browser_like_run(tmp_path):
    out = _run_node(tmp_path, SAMPLE, {
        "open": {"calls": CALLS},
        "gpc": {"gpc": True, "calls": CALLS},
        "opted_out": {"storage": {"wp.adsOptOut": "1"}, "calls": CALLS},
        "storage_blocked": {"blocked": True},
        "meta_down": {"breakMeta": True, "calls": [["start_trial", None]]},
    })
    ga4 = "https://www.googletagmanager.com/gtag/js?id=G-ABC123XYZ9"
    o = out["open"]
    assert o["error"] is None
    assert sorted(o["srcs"]) == sorted([
        ga4, "https://connect.facebook.net/en_US/fbevents.js",
        "https://analytics.tiktok.com/i18n/pixel/events.js?sdkid=C4ABCDEFGH12345678&lib=ttq",
        "https://s.pinimg.com/ct/core.js", "https://bat.bing.com/bat.js"])
    assert ["config", "G-ABC123XYZ9", {}] in o["dataLayer"]
    assert ["config", "AW-123456789"] in o["dataLayer"]
    # Only store, category and method ever leave the page.
    dumped = json.dumps(o)
    assert "ZZSECRET" not in dumped and "12.34" not in dumped and "zz.example" not in dumped
    assert ["event", "deal_click", {"store": "Walmart", "category": "home",
                                    "send_to": "G-ABC123XYZ9"}] in o["dataLayer"]
    assert ["event", "conversion", {"send_to": "AW-123456789/AbC-dEf_123", "value": 0,
                                    "currency": "USD"}] in o["dataLayer"]
    assert o["fbq"][:2] == [["set", "autoConfig", False, "123456789012345"],
                            ["init", "123456789012345"]]
    assert ["trackCustom", "DealClick", {"store": "Walmart", "category": "home"}] in o["fbq"]
    assert ["track", "StartTrial", {"value": 0, "currency": "USD", "predicted_ltv": 9.99}] in o["fbq"]
    assert ["track", "CompleteRegistration", {"method": "email"}] in o["fbq"]
    assert ["track", "InitiateCheckout", {}] in o["fbq"]
    assert ["track", "Subscribe", {"value": 0, "currency": "USD"}] in o["ttq"]
    assert ["track", "CompleteRegistration", {"method": "email"}] in o["ttq"]
    assert ["track", "checkout", {"value": 0, "currency": "USD", "order_quantity": 1}] in o["pintrk"]
    assert ["track", "signup", {"method": "email"}] in o["pintrk"]
    assert [c[1] for c in o["pintrk"] if c[0] == "track"] == ["checkout", "signup"]
    uet = o["uetq"]
    assert uet[uet.index("subscribe") + 1] == {"revenue_value": 0, "currency": "USD"}
    assert uet[uet.index("outbound_click") + 1] == {"event_category": "home", "event_label": "Walmart"}
    # GPC or the privacy page's opt-out: GA4 only, without its ad features;
    # no ad pixel is created or requested, and events reach GA4 alone.
    for name in ("gpc", "opted_out"):
        s = out[name]
        assert s["error"] is None and s["srcs"] == [ga4], name
        assert ["config", "G-ABC123XYZ9", {"allow_google_signals": False,
                                           "allow_ad_personalization_signals": False}] in s["dataLayer"]
        assert not any(c[:2] == ["config", "AW-123456789"] for c in s["dataLayer"]), name
        assert not any(c[:2] == ["event", "conversion"] for c in s["dataLayer"]), name
        assert s["fbq"] is None and s["ttq"] is None and s["pintrk"] is None and s["uetq"] is None
    # Storage that throws is no opt-out and no crash.
    assert len(out["storage_blocked"]["srcs"]) == 5 and out["storage_blocked"]["error"] is None
    # One tag failing never stops the others.
    down = out["meta_down"]
    assert down["error"] is None and ["track", "Subscribe", {"value": 0, "currency": "USD"}] in down["ttq"]


def test_ad_tags_alone_under_gpc_make_no_request_and_wptrack_is_a_no_op(tmp_path):
    ids = {k: v for k, v in SAMPLE.items() if k != "ga4_measurement_id"}
    out = _run_node(tmp_path, ids, {"gpc": {"gpc": True, "calls": CALLS}})["gpc"]
    assert out["srcs"] == [] and out["error"] is None
    assert out["dataLayer"] == []
    assert out["fbq"] is None and out["ttq"] is None and out["pintrk"] is None and out["uetq"] is None


# ---- the privacy page ----

def test_privacy_page_is_published_linked_and_in_the_sitemap(db, cfg, tmp_path):
    cfg.custom_domain = "wowpriced.com"
    cfg.contact_email = "hello@wowpriced.com"
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=[])
    assert "<loc>https://wowpriced.com/privacy.html</loc>" in (out / "sitemap.xml").read_text()
    pages = _pages(out)
    page = pages["privacy.html"]
    assert page.startswith("<!doctype html>") and "prefers-color-scheme" in page
    assert '<link rel="canonical" href="https://wowpriced.com/privacy.html">' in page
    assert "{{" not in page and 'href="mailto:hello@wowpriced.com"' in page
    assert "<script src=" not in page and 'rel="stylesheet"' not in page
    for text in ("Your privacy choices", "Global Privacy Control", "Stripe", "card number",
                 "Last updated", "Google Analytics", "Google Ads", "Meta", "TikTok", "Pinterest",
                 "Microsoft", "delete"):
        assert text in page, text
    # The on-page switch writes the very key the head snippet reads.
    assert 'var KEY = "wp.adsOptOut"' in page and 'id="adsbtn"' in page
    assert "navigator.globalPrivacyControl === true" in page and 'aria-pressed' in page
    assert '"wp.adsOptOut"' in _script(head_snippet(AppConfig(tracking=dict(SAMPLE))))
    # Root-absolute links only (a nested page may link here and back).
    for href in re.findall(r'href="([^"]*)"', page):
        assert href.startswith(("/", "mailto:", "https://", "data:")), href
    low = page.lower()
    for banned in ("krazy", "kcl", "southern savers", "gdpr", "ccpa", "compliant"):
        assert banned not in low, banned
    # Every page with a footer links the privacy page.
    footed = [n for n, p in pages.items() if "<footer" in p]
    assert {"index.html", "about.html", "careers.html", "contact.html", "hacks.html",
            "how-it-works.html", "privacy.html"} <= set(footed)
    for name in footed:
        foot = pages[name][pages[name].index("<footer"):pages[name].index("</footer>")]
        assert 'href="/privacy.html">Privacy</a>' in foot, name


# ---- conversion hooks ----

def test_event_hooks_at_sign_up_checkout_deal_click_and_welcome(db, cfg, tmp_path):
    cfg.tracking = dict(SAMPLE)
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=[])
    html = (out / "index.html").read_text(encoding="utf-8")
    helper = _between(html, "function track(", "\n  }\n")
    assert 'typeof window.wpTrack === "function"' in helper and "catch (e)" in helper
    post = _between(html, "function authPost(", "function loadGis(")
    assert post.index('if (path === "/api/signup") track("sign_up", { method: "email" });') \
        < post.index("signedIn(j.token, j.me, path)")
    body = _between(html, "function checkout(", "function refuse(")
    assert body.index('track("begin_checkout");') < body.index("location.assign(https(j.url));")
    assert '["pwfb", "cofb"].forEach' in html and 'addEventListener("click", function () { track("begin_checkout"); })' in html
    click = _between(html, 'e.target.closest(\'.card a[rel~="sponsored"]\')', "nextStep();")
    call = _between(click, 'track("deal_click", {', "});")
    assert re.findall(r"(\w+):", call) == ["store", "category"]
    welcome = (out / "welcome.html").read_text(encoding="utf-8")
    hook = _between(welcome, 'if (/^cs_[A-Za-z0-9_]{8,200}$/.test(fromUrl)', 'window.wpTrack("start_trial")')
    assert 'var TRIAL_KEY = "wp.trial." + fromUrl;' in hook and "if (!lsGet(TRIAL_KEY))" in hook
    assert hook.index('localStorage.setItem(TRIAL_KEY, "1")') < hook.index("if (lsGet(TRIAL_KEY))")
    # The dispatcher is defined in the head, before any page script calls it.
    for page in (html, welcome):
        assert page.index("window.wpTrack = function") < page.index("<body>")


# ---- the checkout receipt never reaches a tag ----

SCRUB = r"""
const vm = require("vm");
const out = {};
for (const search of ["?session_id=cs_live_ABCdef123456", ""]) {
  const urls = [];
  const ctx = { location: { search, pathname: "/welcome.html" }, URLSearchParams,
                history: { replaceState: (s, t, url) => urls.push(url) } };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(require("fs").readFileSync(process.argv[2], "utf8"), ctx);
  out[search || "none"] = { id: ctx.wpCheckoutId, urls };
}
process.stdout.write(JSON.stringify(out));
"""


def test_welcome_takes_the_session_id_off_the_url_before_any_tag(db, cfg, tmp_path):
    cfg.tracking = dict(SAMPLE)
    out = tmp_path / "site"
    render_site(db, cfg, NOW, out_dir=out, deals=[])
    page = (out / "welcome.html").read_text(encoding="utf-8")
    # The scrub is a head script ahead of the tags' block, and nothing after
    # it reads the address: the page script takes the id from its stash.
    scrub = _between(page[:page.index(START)], "<script>\n", "</script>")[len("<script>\n"):]
    assert 'history.replaceState(null, "", location.pathname)' in scrub
    assert "location.search" not in page[page.index(START):] and "location.href" not in page
    assert 'var fromUrl = window.wpCheckoutId || "";' in page
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    (tmp_path / "scrub.js").write_text(scrub)
    (tmp_path / "harness.cjs").write_text(SCRUB)
    res = subprocess.run(["node", "harness.cjs", "scrub.js"], cwd=tmp_path,
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout) == {
        "?session_id=cs_live_ABCdef123456": {"id": "cs_live_ABCdef123456", "urls": ["/welcome.html"]},
        "none": {"id": "", "urls": []}}
