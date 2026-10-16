"""Public copy stays discreet in templates and every published page family."""

import re
from html import unescape

import pytest

from dealradar.config import load_config
from dealradar.publish import site
from tests.test_publish_site import make_deal
from tests.test_pwa import baseline_config, sample_deals
from tests.test_support import render


# Whole names avoid treating the required API hostname's "workers.dev" as prose.
# Meta robots tags do not contain "robots.txt"; the actual file is exempt below.
INTERNAL = re.compile(
    r"\b(?:Cloudflare|Worker|GitHub|Keepa|Python|Claude|Codex|LLM)\b"  # AI allowed by operator, 2026-10-16.
    r"|small\s+crew|part-time\s+developer|robots\.txt"
    r"|polite\s+fetch|\bcrawler\b|checked\s+in\s+code|fake-list-price\s+critic",
    re.I,
)


@pytest.mark.parametrize("path", sorted(site.TEMPLATES_DIR.iterdir()), ids=lambda p: p.name)
def test_public_templates_do_not_expose_internal_details(path):
    assert path.is_file()
    assert INTERNAL.search(path.read_text()) is None, path.name


@pytest.mark.parametrize("mode", ["membership", "open", "support", "shipped"])
@pytest.mark.parametrize("pwa", [False, True])
@pytest.mark.parametrize("checking", [False, True])
def test_published_pages_do_not_expose_internal_details(tmp_path, mode, pwa, checking):
    cfg = baseline_config(mode)
    cfg.pwa_enabled = pwa
    cfg.checker["enabled"] = checking
    shipped = load_config()
    cfg.ftc_disclosure = shipped.ftc_disclosure
    cfg.affiliate_disclosure = shipped.affiliate_disclosure
    cfg.contact_email = "hello@wowpriced.com"
    cfg.drop_minutes = 20
    cfg.seo_price_page_min_deals = 1  # include price pages with only eight free offers
    deals = sample_deals() + [make_deal(
        dedupe_key="amazon-price-drop", store="Amazon", conditions=["used"], codes=[],
        canonical_url="https://www.amazon.com/dp/B0TESTASIN1",
        sightings=[{"source_name": "Keepa", "source_url": "https://keepa.com/"}],
    )]
    out, doc, _, _ = render(tmp_path, cfg, deals)
    pages = {str(p.relative_to(out)): p for p in out.rglob("*.html")}
    assert {"index.html", "about.html", "careers.html", "contact.html", "hacks.html",
            "how-it-works.html", "privacy.html", "disclosure.html", "subscribe.html",
            "welcome.html", "404.html", "deals/electronics/index.html",
            "stores/walmart/index.html", "deals/under-25/index.html",
            "best-deals-today/index.html"} <= pages.keys()
    assert any(name.startswith("deal/") for name in pages)
    assert ("check/index.html" in pages) is checking
    for path in out.rglob("*"):
        if not path.is_file() or path.suffix == ".png" or path.name == "robots.txt":
            continue
        assert INTERNAL.search(path.read_text()) is None, path.relative_to(out)

    disclosure = doc["meta"]["disclosure"]
    for page in pages.values():
        html = page.read_text()
        assert disclosure in unescape(re.sub(r"<[^>]+>", "", html)), page.name
    policy = pages["privacy.html"].read_text()
    for fact in ("service providers that host the site and send email", "Affiliate links",
                 "Retention", "Children", "Changes and questions", "Global Privacy Control",
                 "ad personalization disabled", "ask us to delete"):
        assert fact in policy
    careers = re.search(r"<main>(.*?)</main>", pages["careers.html"].read_text(), re.S)[1]
    assert careers.count('href="mailto:hello@wowpriced.com"') == 1
    assert "Help shoppers find real deals." in careers
    assert "role" not in careers and "hours" not in careers
    home = pages["index.html"].read_text()
    assert "refreshed every 20 minutes" in home.lower()
    assert 'via <a href="https://slickdeals.net/f/1" rel="noopener">Slickdeals</a>' in home
    if cfg.support_enabled:
        assert "join.html" in pages
        for name in ("index.html", "join.html"):
            html = pages[name].read_text()
            assert "Sign up for the latest deals" in html
            assert "Continue with email" in html
            assert "We use your email only to send deals. Unsubscribe anytime." in html


def test_category_tab_markers_are_uniform_circles():
    html = (site.TEMPLATES_DIR / "index.html").read_text()
    marker = re.search(r"\.cattab::before\{([^}]+)\}", html)[1]
    assert "border-radius:50%" in marker
    assert not re.search(r"\.cattab:nth-child\([^)]*\)::before", html)
    assert re.search(r"\.cattab\.active::before\{[^}]*background:", html)
