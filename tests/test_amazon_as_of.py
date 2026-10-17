"""Amazon absolute check times and price disclaimers, entirely offline."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import seo, site
from dealradar.util import iso
from tests.test_e2e_offline import env, test_rerun_is_idempotent as check_rerun
from tests.test_product_offer_pages import tree
from tests.test_publish_site import make_deal
from tests.test_seo_deal_pages import _page, _path

NOW = datetime(2026, 10, 15, 20, 15, tzinfo=timezone.utc)
CHECKED = NOW - timedelta(hours=2)
FOOTER = ("Amazon prices are accurate as of the time shown and may change; "
          "the price on Amazon at checkout applies.")
DISCLAIMER = ("Product prices and availability are accurate as of the date/time indicated and are "
              "subject to change. Any price and availability information displayed on Amazon.com at the "
              "time of purchase will apply to the purchase of this product.")


def offer(**changes):
    deal = make_deal(dedupe_key="amazon|widget", store="Amazon",
        item="Kitchen Widget", category="kitchen", asin="B012345678",
        canonical_url="https://www.amazon.com/dp/B012345678", codes=[], conditions=[],
        first_seen_at=iso(NOW - timedelta(hours=4)), price_rechecked_at=iso(CHECKED))
    deal.update(changes)
    return deal


def sample_deals():
    return [offer(), offer(dedupe_key="amazon|used", item="Used Widget", conditions=["used"]),
            offer(dedupe_key="amazon|unknown", item="Unchecked Widget", price_rechecked_at=""),
            offer(dedupe_key="walmart|widget", store="Walmart",
                  canonical_url="https://www.walmart.com/ip/12345"),
            offer(dedupe_key="woot|widget", store="Woot",
                  canonical_url="https://www.woot.com/offers/widget")]


def config(mode):
    cfg = load_config() if mode == "shipped" else AppConfig(custom_domain="wowpriced.com")
    cfg.dark_polish = False  # recorded before task 254
    if mode != "shipped":
        cfg.support_enabled = mode == "support"
        cfg.paywall_open_access = mode == "open"
    cfg.paywall_teaser_count = 2
    cfg.seo_hub_min_items = 1
    return cfg


def render(out, cfg, rows=None):
    db = DB(":memory:")
    try:
        return site.render_site(db, cfg, NOW, out_dir=out,
                                deals=sample_deals() if rows is None else rows,
                                last_run={"run_key": "as-of"})
    finally:
        db.close()


def digest(files):
    result = hashlib.sha256()
    for name, content in sorted(files.items()):
        result.update(name.encode() + b"\0" + content)
    return result.hexdigest()


def test_config_default_off_shipped_on_and_overrides(tmp_path):
    assert not AppConfig().amazon_as_of_enabled
    assert load_config().amazon_as_of_enabled
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    assert not load_config(path).amazon_as_of_enabled
    for enabled in (False, True):
        path.write_text(json.dumps({"publish": {"amazon_as_of": {"enabled": enabled}}}))
        assert load_config(path).amazon_as_of_enabled is enabled


@pytest.mark.parametrize("mode,expected", [
    ("membership", "629ac4664aa7f1dc5200d7f4915621ba4534cec704e8eab1bede03e020025d27"),
    ("open", "ace847b5c9443d878a2fe7186162804934e879cd3176cba094378f5d010515d0"),
    ("support", "a348c507188dfd049f65c04ec8567dd4edfba3aff0b4c2b76f9a4d2dfe01998e"),
    ("shipped", "0c19df6ebd4329d4b529861321bd865a9b657609ca80a2ac163e698f556e0223"),
])
@pytest.mark.usefixtures("pre_extension_privacy")
def test_off_matches_every_prechange_published_byte(tmp_path, monkeypatch, mode, expected):
    # Captured on untouched HEAD with these same checked/unchecked mixed-store offers.
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = config(mode)
    cfg.amazon_history = "show"  # predates task 121b
    cfg.front_page.get("checkout_price_deals", {})["enabled"] = False  # predates task 121b
    cfg.seo_product_pages_enabled = False  # predates product pages
    cfg.seo_event_pages_enabled = False  # predates event pages
    cfg.amazon_as_of_enabled = False
    cfg.photoless_last = False  # predates photos-first
    out = tmp_path / "site"
    render(out, cfg)
    assert digest(tree(out)) == expected


@pytest.mark.parametrize("stamp,expected", [
    ("2026-10-15T18:15:00Z", "Oct 15, 2:15 PM EDT"),
    ("2026-11-01T05:30:00Z", "Nov 1, 1:30 AM EDT"),
    ("2026-11-01T06:30:00Z", "Nov 1, 1:30 AM EST"),
    ("2026-11-02T05:05:00Z", "Nov 2, 12:05 AM EST"),
])
def test_card_absolute_time_and_relative_label_share_the_check_clock(stamp, expected):
    from dealradar.util import parse_iso

    checked = parse_iso(stamp)
    now = checked + timedelta(hours=2)
    cfg = AppConfig(amazon_as_of_enabled=True)
    raw = offer(price_rechecked_at=stamp, first_seen_at=iso(checked - timedelta(days=1)))
    public = site._serialize_deal(raw, now, cfg=cfg)
    assert public["checked_at"] == iso(checked)
    assert public["amazon_as_of"] == expected
    markup = seo.card_html(public, now=now)
    as_of = f'Price as of {expected}. {DISCLAIMER}'
    assert f'title="{as_of}"' in markup
    assert f'<span class="sr-only"> ({as_of}) </span>' in markup
    assert f'data-checked-at="{iso(checked)}"' in markup
    visible = re.sub(r'<span class="sr-only">.*?</span>', "", markup)
    cfg.amazon_as_of_enabled = False
    cfg.photoless_last = False  # predates photos-first
    before = seo.card_html(site._serialize_deal(raw, now, cfg=cfg), now=now)
    assert visible.replace(f' title="{as_of}"', "") == before
    assert '<span class="pcl">price checked </span>2h ago' in visible


def test_latest_confirmation_drives_both_labels():
    raw = offer(keepa_price_seen_at=iso(CHECKED + timedelta(minutes=45)))
    public = site._serialize_deal(raw, NOW, cfg=AppConfig(amazon_as_of_enabled=True))
    assert public["checked_at"] == iso(CHECKED + timedelta(minutes=45))
    assert public["amazon_as_of"] == "Oct 15, 3:00 PM EDT"


@pytest.mark.parametrize("changes", [
    {"store": "Walmart", "canonical_url": "https://www.walmart.com/ip/1"},
    {"store": "Woot", "canonical_url": "https://www.woot.com/offers/widget"},
    {"store": "eBay", "canonical_url": "https://www.ebay.com/itm/123456789012"},
])
def test_other_store_cards_unchanged_even_with_an_asin(changes):
    raw = offer(**changes)
    off = site._serialize_deal(raw, NOW, cfg=AppConfig())
    on = site._serialize_deal(raw, NOW, cfg=AppConfig(amazon_as_of_enabled=True))
    assert on == off and "amazon_as_of" not in on
    assert seo.card_html(on, now=NOW) == seo.card_html(off, now=NOW)


@pytest.mark.parametrize("changes", [
    {"price_rechecked_at": ""},
    {"price_rechecked_at": "invalid"},
    {"price_rechecked_at": iso(NOW + timedelta(minutes=1))},
    {"price_since": iso(CHECKED + timedelta(minutes=1))},
    {"gone_suspect_at": iso(NOW)},
])
def test_no_timestamp_is_invented(changes):
    public = site._serialize_deal(offer(**changes), NOW, cfg=AppConfig(amazon_as_of_enabled=True))
    assert "checked_at" not in public and "amazon_as_of" not in public
    assert "Price as of" not in seo.card_html(public, now=NOW)
    markup = seo._deal_page("{{CONTENT}}", public, "/deal/widget/", "https://wowpriced.com",
                           "Disclosure", [], {}, [], NOW)
    assert "Amazon price as of" not in markup and "<summary>Details</summary>" not in markup


def test_deal_disclosure_and_every_shared_footer(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = config("support")
    cfg.amazon_as_of_enabled = True
    out = tmp_path / "site"
    render(out, cfg)
    markup = _page(out, _path(offer())).read_text()
    assert ('<p class="detail-price">Sale price: <strong>$10.00</strong> USD</p>'
            '<p class="detail-checked amazon-as-of">Amazon price as of Oct 15, 2:15 PM EDT</p>'
            '<details class="amazon-price-details"><summary>Details</summary>'
            f'<p>{DISCLAIMER}</p></details>') in markup
    assert "Amazon price as of" not in _page(out, _path(sample_deals()[2])).read_text()
    assert "Amazon price as of" not in _page(out, _path(sample_deals()[3])).read_text()
    assert (out / "deals/home-kitchen/index.html").is_file()
    for path in out.rglob("*.html"):
        page = path.read_text()
        footer = re.search(r'<footer>.*?</footer>', page, re.S)[0]
        assert footer.count(FOOTER) == 1, path
        # The home script refreshes metaline; the added sentence must survive.
        assert FOOTER not in re.search(r'<span id="metaline">(.*?)</span>', footer, re.S)[1]
        assert "keepa" not in page.lower(), path
    assert ".amazon-price-details summary{cursor:pointer;min-height:44px;" in markup
    updated = markup.replace("Oct 15, 2:15 PM EDT", "Oct 15, 3:15 PM EDT")
    assert seo.seo_audit.Page(markup, "/deal/widget/").fingerprint() == seo.seo_audit.Page(
        updated, "/deal/widget/").fingerprint()


def test_browser_card_and_both_relative_clock_updates_preserve_hidden_time(tmp_path, monkeypatch):
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = config("open")
    cfg.amazon_as_of_enabled = True
    out = tmp_path / "site"
    render(out, cfg)
    subprocess.run(["node", str(Path(__file__).with_name("amazon_as_of_logic.cjs")), str(out)],
                   check=True, capture_output=True, text=True, timeout=30)


def test_same_publish_is_stable_and_switch_off_restores_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = config("support")
    cfg.amazon_as_of_enabled = False
    cfg.photoless_last = False  # predates photos-first
    out = tmp_path / "site"
    render(out, cfg)
    before = tree(out)
    cfg.amazon_as_of_enabled = True
    render(out, cfg)
    enabled = tree(out)
    assert json.loads(enabled["deals.json"])["meta"]["site_version"] != json.loads(
        before["deals.json"])["meta"]["site_version"]
    render(out, cfg)
    assert tree(out) == enabled
    cfg.amazon_as_of_enabled = False
    cfg.photoless_last = False  # predates photos-first
    render(out, cfg)
    assert tree(out) == before


def test_same_pipeline_run_key_remains_idempotent_with_switch_on(env, capsys):
    assert load_config(env["cfg"]).amazon_as_of_enabled
    check_rerun(env, capsys, "shadow")
