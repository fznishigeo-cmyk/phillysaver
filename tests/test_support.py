"""Free-site support switch and email-only sign-up, entirely offline."""
import hashlib
import json
import logging
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import site

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
LINK = "https://buy.stripe.com/test_support"
BODY = ("wowpriced is free for everyone: no paywall, no ads. Every day we check thousands "
        "of prices so you only see real deals. If wowpriced saved you money, please chip "
        "in whatever it's worth to you. Most people never do. If you can, you keep it free for everyone.")
NOTE = ("One-time payment, any amount, through Stripe. wowpriced is a for-profit business: "
        "support is not tax-deductible and buys nothing extra; every deal stays free.")
EMAIL_LINE = "One email with the best deals. Unsubscribe anytime."


def render(tmp_path, cfg, deals=()):
    db = DB(tmp_path / "state.db")
    db.meta_set("teaser_recent", '["before"]')
    try:
        full = site.render_site(db, cfg, NOW, out_dir=tmp_path / "site", deals=list(deals))
        memory = db.meta_get("teaser_recent")
    finally:
        db.close()
    out = tmp_path / "site"
    doc = json.loads((out / "deals.json").read_text())
    return out, doc, full["full_payload"], memory


@pytest.mark.parametrize("link", ["http://buy.stripe.com/a", "https://buy.stripe.com/",
                                  "https://buy.stripe.com.evil/a", "https://evil/a",
                                  "https://user@buy.stripe.com/a", "https://buy.stripe.com:443/a",
                                  'https://buy.stripe.com/a\"', "javascript:alert(1)",
                                  "https://buy.stripe.com/a\nnext", "https://buy.stripe.com\\@evil/a"])
def test_invalid_link_warns_and_hides(tmp_path, caplog, link):
    path = tmp_path / "config.yaml"
    path.write_text("support:\n  enabled: true\n  payment_link: " + json.dumps(link))
    with caplog.at_level(logging.WARNING):
        cfg = load_config(path)
    assert cfg.support_enabled and cfg.support_payment_link == ""
    assert "support.payment_link invalid" in caplog.text
    assert link not in caplog.text
    out, doc, _, _ = render(tmp_path, cfg)
    assert doc["meta"]["support"] == {"enabled": True, "link": "", "monthly_link": ""}
    for name in ("index.html", "subscribe.html"):
        html = (out / name).read_text()
        assert "data-support href" not in html and NOTE not in html


def test_config_defaults_valid_link_and_operator_switch(tmp_path):
    assert AppConfig().support_enabled is False
    assert AppConfig().support_payment_link == ""
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    assert load_config(path).support_enabled is False
    path.write_text(f'support:\n  enabled: true\n  payment_link: " {LINK} "')
    cfg = load_config(path)
    assert cfg.support_enabled and cfg.support_payment_link == LINK
    assert load_config().support_enabled is True
    assert load_config().support_payment_link.startswith("https://buy.stripe.com/")  # operator link 2026-10-12
    assert load_config().newsletter_enabled is False  # email launch is later


@pytest.mark.parametrize("open_access", [False, True])
def test_support_wins_opens_every_deal_and_pauses_rotation(tmp_path, open_access):
    from tests.test_publish_site import make_deal
    cfg = AppConfig(support_enabled=True, support_payment_link=LINK,
                    paywall_enabled=True, paywall_open_access=open_access,
                    paywall_api_url="https://api.example", paywall_yearly_price_usd=49)
    deals = [make_deal(dedupe_key=f"support-{i}", item=f"Support item {i}") for i in range(12)]
    out, doc, full, memory = render(tmp_path, cfg, deals)
    assert doc["meta"] == full["meta"]
    assert doc["meta"]["support"] == {"enabled": True, "link": LINK, "monthly_link": ""}
    assert doc["meta"]["open_access"] and doc["meta"]["locked_count"] == 0
    assert len(doc["deals"]) == 12 and not any(d.get("locked") for d in doc["deals"])
    assert memory == '["before"]'
    for path in out.rglob("*.html"):
        html = path.read_text()
        assert "{{" not in html, path
        assert "<!--standard-->" not in html and "<!--support-->" not in html
        visible = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", "", html, flags=re.S)
        assert not re.search(r"membership|trial|password|\$0 today|plan picker", visible, re.I), path
    home = (out / "index.html").read_text()
    assert 'id="loginlink"' not in home and 'id="dialogplans"' not in home
    assert 'id="subscribe" class="getdeal" href="/join">Join free' in home
    assert 'id="subbar" class="subbar ready" href="/join" hidden>Join free' in home
    assert "Join free: the best deals in your inbox" in home.replace("\\'", "'")
    assert "You're on the list. When our deals email starts, we'll send you one email to confirm first." in home
    assert "Emails are stored by our Cloudflare Worker and used only for the deals email." in (out / "privacy.html").read_text()
    for name, count in (("index.html", 2), ("subscribe.html", 1)):
        html = (out / name).read_text()
        assert "A quick, honest ask." in html and BODY in html
        assert 'href="/join">Join free</a>' in html
        assert ('<form class="joinform">' in html) == (name == "index.html")
        assert html.count(NOTE) == count
        assert html.count(f'data-support href="{LINK}"') == count
        # Every Stripe button has its disclaimer immediately in the same section.
        for match in re.finditer(r'<a[^>]*data-support[^>]*>', html):
            assert 'target="_blank" rel="noopener"' in match.group()
            assert NOTE in html[match.end():].split("</footer>")[0].split("</div>\n</div>")[0]
    assert "wowpriced is free for everyone." in home
    assert 'track("support_click", {plan: "once"})' in home and 'joinedCard("email")' in home
    assert 'fetch(api + "/subscribe"' in home and 'JSON.stringify({email: input.value.trim()})' in home
    assert 'try { dismissed = Number(localStorage.getItem(KEY))' in home
    assert 'try { localStorage.setItem(KEY, String(Date.now() + WEEK)); }' in home


@pytest.mark.parametrize("mode,yearly,expected", [
    (False, 0, "b10b202643f39562d2606df2850f23dcfb6010d5fa075a6d62f03b4990f86932"),
    (False, 49, "0d27cfc0cfa8dfaff19228d39f23506f46091f856325a24e0486973ce09b122a"),
    (True, 0, "48f485ed197a3d97ac0c8e29afe03b7e39d7ff81886fba62d4be5cb039c3e5e9"),
    (True, 49, "045c159886cb9028b9454572634445503ab4ab0443a108c9bb4c1a3f60c0f730"),
])
def test_support_off_index_bytes_match_task042_baseline(tmp_path, monkeypatch, mode, yearly, expected):
    # Task 042 changes the default ordering in all modes; keep the shared
    # baseline without task 037's head-only hint.
    # Membership behavior stays covered separately; the version stays fixed.
    monkeypatch.setattr(site, "site_template_version", lambda: "baseline")
    out, doc, _, _ = render(tmp_path, AppConfig(paywall_open_access=mode, paywall_yearly_price_usd=yearly))
    html = (out / "index.html").read_bytes()
    hint = b'<link rel="preconnect" href="https://m.media-amazon.com">\n'
    assert html.count(hint) == 1
    assert hashlib.sha256(html.replace(hint, b"")).hexdigest() == expected
    assert doc["meta"]["support"] == {"enabled": False, "link": "", "monthly_link": ""}


def test_variant_choice():
    template = ('<!--standard-->common<!--open-->open<!--/open--><!--paywall-->locked<!--/paywall-->'
                '<!--/standard--><!--support-->support<!--/support-->')
    assert site.copy_variant(template, True, True) == "support"
    assert site.copy_variant(template, False, True) == "support"
    assert site.copy_variant(template, True) == "commonopen"
    assert site.copy_variant(template, False) == "commonlocked"


def test_support_browser_logic_offline(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    out, _, _, _ = render(tmp_path, AppConfig(support_enabled=True, paywall_api_url="https://api.test"))
    html = (out / "index.html").read_text()
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    for i, code in enumerate(scripts):
        script = tmp_path / f"inline-{i}.js"
        script.write_text(code)
        subprocess.run(["node", "--check", str(script)], check=True, capture_output=True, text=True)
    shared = tmp_path / "support.js"
    join = (out / "join.html").read_text()
    join_scripts = re.findall(r"<script>(.*?)</script>", join, re.S)
    shared.write_text(next(code for code in join_scripts if "window.wpSupport =" in code))
    assert shared.read_text() == next(code for code in scripts if "window.wpSupport =" in code)
    subprocess.run(["node", str(Path(__file__).with_name("support_logic.cjs")), str(shared), str(out / "index.html")],
                   check=True, capture_output=True, text=True)


def test_hourly_check_joins_without_submitting_and_skips_plans():
    script = (Path(__file__).resolve().parents[1] / "scripts/site_check.js").read_text()
    step6 = script.split("await step(6,", 1)[1].split("await step(8,", 1)[0]
    assert 'support ? "Join page"' in step6
    assert 'href.endsWith("/join")' in step6 and 'readSite("join")' in step6
    assert 'response.status === 200' in step6 and 'type=["' in step6
    assert 'input[type="email"]' in step6 and 'page.keyboard.press("Escape")' in step6
    assert "plansExpected = !support" in step6 and '"Yearly choice (n/a)"' in step6
    assert '.submit(' not in step6 and 'method: "POST"' not in step6


def test_support_click_reaches_tracking_and_legacy_dispatch_is_unchanged(tmp_path):
    from dealradar.publish.tracking import head_snippet
    from tests.test_tracking import HARNESS, SAMPLE, _script

    cfg = AppConfig(tracking=SAMPLE)
    before = head_snippet(cfg)
    assert "support_click:" not in before
    cfg.support_enabled = True
    snippet = head_snippet(cfg)
    assert "support_click:" in snippet
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    (tmp_path / "snippet.js").write_text(_script(snippet))
    (tmp_path / "harness.cjs").write_text(HARNESS)
    (tmp_path / "scenarios.json").write_text(json.dumps({"support": {"calls": [["support_click", {}]]}}))
    result = subprocess.run(["node", "harness.cjs", "snippet.js", "scenarios.json"],
                            cwd=tmp_path, capture_output=True, text=True, check=True)
    result = json.loads(result.stdout)["support"]
    assert any(event[:2] == ["event", "support_click"] for event in result["dataLayer"])
    assert any(event[:2] == ["trackCustom", "SupportClick"] for event in result["fbq"])
    cfg.support_enabled = False
    assert head_snippet(cfg) == before


MONTHLY = "https://buy.stripe.com/test_monthly"


def test_monthly_link_adds_a_second_button_with_renewal_terms(tmp_path):
    cfg = AppConfig(support_enabled=True, support_payment_link=LINK, support_monthly_link=MONTHLY)
    out, doc, _, _ = render(tmp_path, cfg)
    assert doc["meta"]["support"] == {"enabled": True, "link": LINK, "monthly_link": MONTHLY}
    for name in ("index.html", "subscribe.html"):
        html = (out / name).read_text()
        assert f'data-support href="{LINK}"' in html and ">Support once</a>" in html
        assert f'data-support-monthly href="{MONTHLY}"' in html and ">Support monthly</a>" in html
        assert site.SUPPORT_NOTE_MONTHLY in html and "renewing each month until you cancel" in html
        assert "not tax-deductible" in html
    # A monthly link alone never shows: the one-time link leads.
    cfg = AppConfig(support_enabled=True, support_monthly_link=MONTHLY)
    (tmp_path / "solo").mkdir()
    out, doc, _, _ = render(tmp_path / "solo", cfg)
    assert doc["meta"]["support"]["monthly_link"] == ""
    assert "data-support-monthly href" not in (out / "index.html").read_text()


def test_shipped_config_has_both_support_links():
    cfg = load_config()
    assert cfg.support_payment_link.startswith("https://buy.stripe.com/")
    assert cfg.support_monthly_link.startswith("https://buy.stripe.com/")
    assert cfg.support_monthly_link != cfg.support_payment_link


@pytest.mark.parametrize("link", ["", LINK])
def test_join_page_and_every_join_link(tmp_path, link):
    from tests.test_publish_site import make_deal
    cfg = AppConfig(support_enabled=True, support_payment_link=link,
                    custom_domain="wowpriced.com", paywall_api_url="https://api.test",
                    seo_indexnow_key="0123456789abcdef")
    out, _, _, _ = render(tmp_path, cfg, [make_deal()])
    join = (out / "join.html").read_text()
    assert "<title>Join wowpriced</title>" in join
    assert '<link rel="canonical" href="https://wowpriced.com/join.html">' in join
    assert "The day's best deals in one email. Free." in join
    assert '<h1 id="join-title">Join wowpriced</h1>' in join
    assert '<label for="join-email">Email</label><input id="join-email" name="email" type="email"' in join
    assert 'autocomplete="email" required>' in join and len(re.findall(r'<input\b[^>]*type="email"', join)) == 1
    assert site.JOIN_FORM in join and EMAIL_LINE in join
    assert '<a href="/privacy.html">privacy policy</a>' in join
    assert "max-width:480px" in join and "border-radius:12px" in join
    assert ".wrap::before" not in join  # clean, white /join: no decorative shape
    assert "noindex" not in join
    assert "Disallow: /join" not in (out / "robots.txt").read_text()
    assert "<loc>https://wowpriced.com/join.html</loc>" in (out / "sitemap.xml").read_text()
    assert "https://wowpriced.com/join.html" in json.loads(site.seo.due_list_path(out).read_text())
    assert 'fetch(api + "/subscribe"' in join and 'JSON.stringify({email: input.value.trim()})' in join
    assert 'joinedCard("email")' in join
    for path in out.rglob("*.html"):
        html = path.read_text()
        assert 'id="authmodal"' not in html, path
        visible = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", "", html, flags=re.S)
        for attrs, text in re.findall(r"<a\b([^>]*)>(.*?)</a>", visible, re.S):
            if re.sub(r"<[^>]+>", "", text).strip() == "Join free":
                assert 'href="/join"' in attrs, path
        if path.name not in {"join.html", "index.html"}:
            assert 'id="join-email"' not in visible, path
            assert ('<form class="joinform" data-join-footer>' in visible) == ('<footer>' in visible and path.name != '404.html'), path
    home = (out / "index.html").read_text()
    for selector in ('id="subscribe"', 'id="subbar"', 'class="pwbtn alt"'):
        assert re.search(r'<a ' + selector + r'[^>]*href="/join"', home)
    assert 'location.replace("/join")' in home
    assert '["#join", "#signup", "#login"]' in home


@pytest.mark.parametrize("open_access", [False, True])
def test_switch_back_removes_join_page_and_preserves_membership_dialog(tmp_path, open_access):
    out, _, _, _ = render(tmp_path, AppConfig(support_enabled=True, custom_domain="wowpriced.com"))
    assert (out / "join.html").exists()
    out, _, _, _ = render(tmp_path, AppConfig(paywall_enabled=True, paywall_open_access=open_access,
                                           custom_domain="wowpriced.com"))
    assert not (out / "join.html").exists()
    home = (out / "index.html").read_text()
    assert 'id="authmodal"' in home and 'id="authform"' in home
    assert 'href="#signup"' in home and 'location.replace("/join")' not in home
    assert "join.html" not in (out / "sitemap.xml").read_text()


def test_support_button_styles_and_theme_contrast(tmp_path):
    out, _, _, _ = render(tmp_path, AppConfig(support_enabled=True))
    home = (out / "index.html").read_text()
    assert "border:1.5px solid var(--accent);background:transparent" in home
    assert "border-radius:999px;padding:10px 22px;font-weight:500" in home
    assert "#subscribe.getdeal{min-height:52px}" in home
    assert "transition:background-color 150ms,color 150ms,border-color 150ms" in home
    assert "font-weight:600;border-radius:10px;box-shadow:none" in home
    assert ".pwbtn.alt,#surprise" in home
    assert "outline:2px solid var(--accent);outline-offset:3px" in home
    assert ".tag,.secth{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}" in home
    for name in ("index.html", "join.html", "deals/electronics/index.html"):
        html = (out / name).read_text()
        # Any hover transform must explicitly leave cards/buttons in place.
        for selector, declarations in re.findall(r"([^{}]+)\{([^{}]+)\}", html):
            if ":hover" in selector and re.search(r"\.card|\.getdeal|\.pwbtn|#surprise|#subscribe|\.subbar", selector):
                assert not re.search(r"transform:(?!none)[^;]+", declarations), selector

    def luminance(hex_color):
        channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
        return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))

    # Solid, outline and hover button text in light, dark and the ink header.
    for foreground, background in (("#ffffff", "#0e7569"), ("#ffffff", "#0f7b6e"),
                                   ("#0e7569", "#f3f5f7"), ("#0e7569", "#ffffff"),
                                   ("#07211d", "#2fb3a4"), ("#07211d", "#42c2b2"),
                                   ("#2fb3a4", "#0d1117"), ("#2fb3a4", "#161c23"),
                                   ("#2fc4b2", "#163b40"), ("#04201c", "#2fc4b2")):
        assert foreground in home and background in home
        light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
        assert (light + .05) / (dark + .05) >= 4.5, (foreground, background)
