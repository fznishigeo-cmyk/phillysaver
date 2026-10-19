"""Published copy stays finished and shopper-facing across page families."""

import re

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import email_digest, members, newsletter, seo_audit, site
from tests.test_product_price_pages import seed
from tests.test_publish_site import NOW, make_deal
from tests.test_pwa import baseline_config, sample_deals


UNFINISHED = re.compile(r"coming soon|launch|beta|starting soon|be first|lorem|TODO", re.I)
TRUST_LINKS = {
    "/about.html": "About", "/how-it-works.html": "How we verify",
    "/contact.html": "Contact", "/privacy.html": "Privacy", "/disclosure.html": "Disclosure",
}


def visible_text(html):
    return seo_audit.Page(html, "/").first(tag="body").text()


@pytest.mark.parametrize("mode", ["membership", "open", "support", "shipped"])
@pytest.mark.parametrize("empty", [False, True])
def test_no_published_page_has_unfinished_visible_copy(tmp_path, monkeypatch, mode, empty):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = load_config() if mode == "shipped" else baseline_config(mode)
    cfg.seo_product_pages_enabled = cfg.traffic_product_families_enabled = True
    cfg.amazon_history = "show"  # Cover /p/ pages as well as the shipped hidden-history mode elsewhere.
    cfg.paywall_teaser_count = 100
    cfg.seo_store_page_min_deals = cfg.seo_price_page_min_deals = 1
    db = DB(tmp_path / "state.db")
    try:
        rows = [] if empty else seed(db, 2) + sample_deals() + [make_deal(
            dedupe_key=f"family-{store}", item="Everyday storage basket", brand="Acme",
            store=store, canonical_url=f"https://{store.lower()}.example/product/1",
            image_url="https://images.example.invalid/basket.jpg",
            codes=[], conditions=[]) for store in ("Walmart", "Target", "BestBuy")]
        out = tmp_path / "site"
        site.render_site(db, cfg, NOW, out_dir=out, deals=rows)
    finally:
        db.close()
    paths = {p.relative_to(out).as_posix(): p for p in out.rglob("*.html")}
    assert {"index.html", "404.html", "best-deals-today/index.html"} <= paths.keys()
    # Task 276 suppresses the history reader in the shipped List Price mode.
    assert ("p/index.html" in paths) is (cfg.amazon_reference == "history")
    if not empty:
        assert any(p.startswith("p/") and p != "p/index.html" for p in paths) is (cfg.amazon_reference == "history")
        assert any(p.startswith("products/") for p in paths)
        assert any(p.startswith("stores/") for p in paths)
        assert any(p.startswith("deal/") for p in paths)
    for name, path in paths.items():
        html = path.read_text()
        assert UNFINISHED.search(visible_text(html)) is None, name
        page = seo_audit.Page(html, name)
        footer = page.first(tag="footer")
        assert footer and f"© {NOW.year} wowpriced" in footer.text(), name
        links = {n.attrs.get("href"): n.text() for n in footer.nodes() if n.tag == "a"}
        assert TRUST_LINKS.items() <= links.items(), name
    if empty:
        best = paths["best-deals-today/index.html"].read_text()
        assert "The 0 best of 0" not in visible_text(best)
        assert '<div class="grid"></div>' not in best
        assert 'href="/">Explore current verified deals</a>' in best


def test_copy_guard_reads_text_and_ignores_scripts_attributes_and_hidden_content():
    html = ('<html><head><title>launch</title><style>.beta{}</style></head><body>'
            '<!-- TODO --><script>const launch = "coming soon";</script>'
            '<input placeholder="be first"><p hidden>lorem</p><p style="display:none">beta</p>'
            '<p aria-hidden="true">starting soon</p><p>Browse verified deals</p></body></html>')
    assert UNFINISHED.search(visible_text(html)) is None
    for term in ("COMING SOON", "Launch", "beta", "starting soon", "Be First", "lorem", "TODO"):
        assert UNFINISHED.search(visible_text(f"<body><p>{term}</p></body>")), term


@pytest.mark.parametrize("custom_domain", [False, True])
def test_email_copy_has_no_unfinished_wording_or_placeholder_prices(custom_domain):
    cfg = load_config() if custom_domain else AppConfig()
    rows = sample_deals()
    messages = [email_digest.build_digest(rows, cfg, NOW), email_digest.build_digest([], cfg, NOW),
                members.build_member_email(rows, cfg), newsletter.build_newsletter(rows, cfg, NOW),
                newsletter.build_confirmation(cfg, "https://wowpriced.com/subscribe.html?confirmed=1")]
    for subject, text, html in messages:
        assert subject.startswith(("wowpriced", "Confirm your wowpriced"))
        assert UNFINISHED.search(subject + text + visible_text(html)) is None
        assert "$?" not in text
    if not custom_domain:
        # These helpers still supply the original transport fallback sender.
        assert newsletter._brand(cfg) == members._brand(cfg) == "dealradar"
