"""Display admission cannot change evidence, recheck lanes or pushed offers."""
import hashlib
import json
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from dealradar import pipeline, rank, util, verify
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.growth import report
from dealradar.publish import members, newsletter, price_labels, site, social
from dealradar.publish.seo_audit import read_pages
from tests.test_price_accuracy_engine import NOW, seed
from tests.test_publish_site import make_deal


@pytest.fixture
def cfg(tmp_path):
    cfg = load_config()
    cfg.used_min_pct = 80
    cfg.site_dir = str(tmp_path / "site")
    cfg.seo_hub_min_items = cfg.seo_store_page_min_deals = cfg.seo_price_page_min_deals = 1
    cfg.seo_home_min_items = cfg.seo_best_today_min_items = 1
    cfg.traffic_hub_pagination_enabled = True
    cfg.feature_flags["keepa"] = False
    return cfg


@pytest.fixture
def db():
    db = DB(":memory:")
    yield db
    db.close()


def offer(key="stale", hours=30, **changes):
    return make_deal(dedupe_key=key, item="Widget " + key, store="Amazon", category="home",
                     canonical_url="https://www.amazon.com/dp/B000000001", asin="B000000001",
                     image_url="https://m.media-amazon.com/images/I/widget.jpg", codes=[], conditions=[],
                     first_seen_at=util.iso(NOW - timedelta(hours=hours)), keepa_price_seen_at="",
                     **changes)


def render(db, cfg, rows, monkeypatch, now=NOW):
    monkeypatch.setattr(site, "util_now", lambda: now)
    return site.render_site(db, cfg, now, deals=rows)


def test_config_off_by_default_on_in_shipped_file(tmp_path):
    assert not AppConfig().unconfirmed_label_enabled
    cfg = load_config()
    assert cfg.unconfirmed_label_enabled
    assert cfg.unconfirmed_label_max_h_new == cfg.unconfirmed_label_max_h_used == 48
    path = tmp_path / "config.yaml"
    path.write_text("site:\n  unconfirmed_label:\n    enabled: false\n    max_h_new: 36\n    max_h_used: 40\n")
    cfg = load_config(path)
    assert not cfg.unconfirmed_label_enabled
    assert (cfg.unconfirmed_label_max_h_new, cfg.unconfirmed_label_max_h_used) == (36, 40)


@pytest.mark.parametrize("change,reason", [
    ({"status": "dead"}, "not_alive"),
    ({"gone_suspect_at": util.iso(NOW)}, "suspect"),
    ({"death": "post_says_over"}, "suspect"),
    ({"critic": {"plausible": False}}, "suspect"),
    ({"flags": ["suspect_msrp"]}, "suspect"),
    ({"audit": {"outcome": "reject"}}, "suspect"),
    ({"flags": ["unbacked_price"]}, "publish_gate"),
    ({"online_only": "unknown"}, "publish_gate"),
    ({"currency": "EUR"}, "publish_gate"),
    ({"region": "UK"}, "publish_gate"),
    ({"pct_at_checkout": 10}, "publish_gate"),
    ({"abs_savings": 0}, "publish_gate"),
    ({"source_name": "keepa", "critic": None}, "publish_gate"),
    ({"conditions": ["used"], "pct_at_checkout": 69}, "publish_gate"),
    ({"store": "Walmart", "canonical_url": ""}, "confirmed_or_in_grace"),
    ({"price_rechecked_at": util.iso(NOW)}, "confirmed_or_in_grace"),
    ({"first_seen_at": ""}, "missing_clock"),
    ({"first_seen_at": util.iso(NOW + timedelta(hours=1))}, "future_clock"),
])
def test_each_exclusion(cfg, change, reason):
    row = dict(offer(), **change)
    assert price_labels.exclusion(row, cfg, NOW) == reason
    assert not price_labels.qualifies(row, cfg, NOW)


@pytest.mark.parametrize("used,grace,outer", [(False, 12, 48), (True, 24, 48)])
def test_exact_grace_and_outer_boundaries_and_original_confirmation_clock(cfg, used, grace, outer):
    row = offer()
    row["conditions"] = ["used"] if used else []
    for age, expected in [(grace, False), (grace + .01, True), (outer - .01, True),
                          (outer, False), (outer + 1, False)]:
        row["first_seen_at"] = util.iso(NOW - timedelta(hours=age))
        assert price_labels.qualifies(row, cfg, NOW) is expected
    row["first_seen_at"] = util.iso(NOW - timedelta(days=30))
    row["price_rechecked_at"] = util.iso(NOW - timedelta(hours=30))
    assert price_labels.qualifies(row, cfg, NOW)
    row["price_since"] = util.iso(NOW - timedelta(hours=29))
    assert not price_labels.qualifies(row, cfg, NOW)  # stale reading cannot reset the first-seen clock
    row["price_rechecked_at"] = util.iso(NOW)
    assert not price_labels.qualifies(row, cfg, NOW)


def test_no_asin_is_eligible_and_off_diagnostic_counts_potential(cfg):
    row = dict(offer(), asin="", canonical_url="https://source.test/click/123")
    assert price_labels.qualifies(row, cfg, NOW)
    cfg.unconfirmed_label_enabled = False
    assert not price_labels.qualifies(row, cfg, NOW)
    data = price_labels.diagnostic([row], cfg, NOW)
    assert data["qualifying"] == data["still_hidden"] == {"new": 1, "used": 0, "total": 1}
    assert data["reasons"]["missing_asin"]["total"] == 1


def test_full_publish_sort_label_pages_push_exclusions_and_clean_seo(db, cfg, monkeypatch):
    fresh = offer("confirmed", 1, price_rechecked_at=util.iso(NOW))
    stale = offer()
    used = dict(offer("used"), conditions=["used"])
    expired = offer("outer", 49)
    suspect = offer("suspect", gone_suspect_at=util.iso(NOW))
    result = render(db, cfg, [stale, used, expired, suspect, fresh], monkeypatch)
    public = json.loads((Path(cfg.site_dir) / "deals.json").read_text())["deals"]
    assert [d["dedupe_key"] for d in public] == ["confirmed", "stale", "used"]
    assert all(d.get("price_unconfirmed") and not d.get("pick") and "ps" not in d for d in public[1:])
    assert not public[0].get("price_unconfirmed")
    pages = read_pages(Path(cfg.site_dir))
    labeled_path = public[1]["deal_path"]
    page = pages[labeled_path]
    assert page.unconfirmed and not page.products() and not page.indexable and page.live_offers() == 0
    assert "Availability: Live offer" not in page.scope.text()
    assert "We couldn't re-check this price recently; it may have changed." in page.scope.text()
    assert page.first(cls="detail-price").text().startswith("Sale price:")
    assert not page.schema_mismatch()
    for path, page in pages.items():
        labels = [bool(page.first(cls="price-unconfirmed", scope=c)) for c in page.cards]
        assert labels == sorted(labels), path
        if path == "/best-deals-today/":
            assert not any(labels)
        if path.startswith("/deal/"):
            continue
        if page.indexable and labels:
            assert page.live_offers() >= 1
    feed = (Path(cfg.site_dir) / "feed.xml").read_text()
    assert public[0]["deal_path"] in feed
    assert all(d["deal_path"] not in feed for d in public[1:])
    due = json.loads(site.indexnow.due_list_path(cfg.site_dir).read_text())
    assert not any(d["deal_path"] in url for d in public[1:] for url in due)
    audit = result["seo_audit"]
    assert all(audit[k] == 0 for k in ("empty_indexable", "schema_mismatches", "broken_internal_links",
                                      "sitemap_missing", "sitemap_extra"))
    data = result["unconfirmed_label"]
    assert data["labeled_shown"] == {"new": 1, "used": 1, "total": 2}
    assert data["still_hidden"]["total"] == 2
    assert data == price_labels.report_data(db)
    assert "labeled_shown: new 1, used 1" in "\n".join(price_labels.summary_lines(data))
    assert "unconfirmed_label" not in json.loads((Path(cfg.site_dir) / "deals.json").read_text())["meta"]
    # A marker always disqualifies stock, even if stock-evidence checking is off.
    cfg.social_require_stock_evidence = False
    assert not social.plenty_of_stock(db, public[1], cfg)
    assert not pages[labeled_path].first(cls="traffic-share")


def test_only_labels_cannot_meet_seo_floors(db, cfg, monkeypatch):
    render(db, cfg, [offer()], monkeypatch)
    for path, page in read_pages(Path(cfg.site_dir)).items():
        if path == "/" or path.startswith(("/deal/", "/deals/")) or path == "/best-deals-today/":
            assert not page.indexable, path
            assert page.live_offers() == 0
    assert not list((Path(cfg.site_dir) / "stores").glob("*/index.html"))


@pytest.mark.parametrize("foreground,background", [("68717c", "ffffff"), ("68717c", "f3f5f7"),
    ("a4adb7", "161c23"), ("a4adb7", "0d1117")])
def test_label_aa_contrast(foreground, background):
    def luminance(color):
        channels = [int(color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        return sum((n / 12.92 if n <= .04045 else ((n + .055) / 1.055) ** 2.4) * weight
                   for n, weight in zip(channels, (.2126, .7152, .0722)))
    a, b = sorted([luminance(foreground), luminance(background)])
    assert (b + .05) / (a + .05) >= 4.5
    assert "#" + foreground in price_labels.STYLE


def test_recheck_sets_and_scheduler_metadata_unchanged_and_confirmation_removes_label(db, cfg, monkeypatch):
    cfg.min_abs_savings = 1
    hidden = seed(db, cfg, seen=NOW - timedelta(hours=30))
    fresh = seed(db, cfg, asin="B000000002", price_rechecked_at=util.iso(NOW))
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg.unconfirmed_label_enabled = False
    site.render_site(db, cfg, NOW)
    before = {key: db.meta_get(key) for key in (site.PICKS_META, site.WALL_META)}
    scheduling_set = rank.site_deals(db, cfg, NOW)
    cfg.unconfirmed_label_enabled = True
    result = site.render_site(db, cfg, NOW)
    assert {key: db.meta_get(key) for key in before} == before
    assert rank.site_deals(db, cfg, NOW) == scheduling_set
    assert hidden["dedupe_key"] not in {d["dedupe_key"] for d in scheduling_set}
    assert verify.price_unconfirmed(db.get_deal(hidden["dedupe_key"]), cfg, NOW)
    assert result["unconfirmed_label"]["labeled_shown"]["new"] == 1
    assert hidden["dedupe_key"] not in {d["dedupe_key"] for d in rank.published_deals(db, cfg, NOW)}
    hidden["price_rechecked_at"] = util.iso(NOW)
    db.update_deal(hidden["dedupe_key"], payload=hidden)
    result = site.render_site(db, cfg, NOW)
    assert result["unconfirmed_label"]["labeled_shown"]["total"] == 0
    assert all(not d.get("price_unconfirmed") for d in result["full_payload"]["deals"])


def test_member_alert_explicit_raw_and_serialized_inputs_cannot_push_labels(db, cfg, monkeypatch):
    from tests.test_members import Factory
    monkeypatch.setattr(members, "member_list", lambda *args: ["member@example.test"])
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    cfg.members_email_enabled = True
    factory = Factory()
    for row in [offer(), dict(offer(), price_unconfirmed=True)]:
        result = members.send_member_alerts(db, cfg, NOW, deals=[row], smtp_factory=factory)
        assert result["deals"] == 0 and result["sent"] == 0
    assert factory.servers == []


def test_scheduler_other_reasons_are_named_without_changing_recovery(db, cfg):
    cfg.used_min_pct = 70  # seed's verified $25/$100 offer clears this fixture's floor
    # Past the volatility scheduler's 48 h FAST/hidden target (task 095), so
    # the row is recovery-due rather than inside its cadence.
    row = seed(db, cfg, seen=NOW - timedelta(hours=60), conditions=["used"],
               price_rechecked_at=util.iso(NOW - timedelta(hours=50)), pct_at_checkout=85)
    result = pipeline._recheck_prices(db, cfg, NOW)
    reasons = result["hidden_unconfirmed"]["reasons"]
    assert "other" not in reasons
    assert sum(v["used"] for v in reasons.values()) == 1
    assert reasons["used_hidden_recovery_due"]["used"] == 1


def test_browser_label_sort_ticker_watch_and_surprise(db, cfg, monkeypatch):
    render(db, cfg, [offer(), offer("confirmed", 1)], monkeypatch)
    proc = subprocess.run(["node", "tests/unconfirmed_label_logic.cjs", cfg.site_dir], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_picks_and_all_social_candidates_reject_explicit_marker(db, cfg, monkeypatch):
    rows = [offer(), offer("confirmed", 1, price_rechecked_at=util.iso(NOW))]
    result = render(db, cfg, rows, monkeypatch)
    public = result["full_payload"]["deals"]
    labeled = next(d for d in public if d.get("price_unconfirmed"))
    assert rank.pick_score(dict(rows[0], price_unconfirmed=True), cfg, NOW, require_confirmation=False) is None
    assert rank.top_picks([(labeled, 1000)], cfg.front_page) == []
    assert rank.daily_spotlight([dict(rows[0], price_unconfirmed=True)], cfg, NOW) == []
    labeled["pick"] = 1  # Even an old/tampered pick cannot bypass the shared social guard.
    path = Path(cfg.site_dir) / "deals.json"
    path.write_text(json.dumps({"deals": [labeled]}))
    assert social._candidates(db, cfg, {}) == []  # Pinterest, Meta and Telegram share this gate.


def test_existing_indexnow_queue_and_listing_price_hashes_ignore_labels(db, cfg, monkeypatch):
    cfg.seo_indexnow_min_hours = 0
    row = offer("changes", 1, price_rechecked_at=util.iso(NOW))
    other = offer("confirmed", 1, price_rechecked_at=util.iso(NOW))
    result = render(db, cfg, [row, other], monkeypatch)
    public = result["full_payload"]["deals"]
    path = public[0]["deal_path"]
    url = "https://" + cfg.custom_domain + path
    assert db.indexnow_pages()[url]["pending_since"]
    row.update(first_seen_at=util.iso(NOW - timedelta(hours=30)), price_rechecked_at="")
    render(db, cfg, [row, other], monkeypatch)
    manifest = json.loads(site.indexnow.manifest_path(cfg.site_dir).read_text())
    assert url not in manifest["versions"] and url not in manifest["files"]
    assert url not in json.loads(site.indexnow.due_list_path(cfg.site_dir).read_text())
    hashes = {url: row["content_hash"] for url, row in db.indexnow_pages().items()}
    row.update(final_price=8, pct_at_checkout=84, abs_savings=42)
    render(db, cfg, [row, other], monkeypatch)
    assert hashes == {url: row["content_hash"] for url, row in db.indexnow_pages().items()}
    row["price_rechecked_at"] = util.iso(NOW)
    render(db, cfg, [row, other], monkeypatch)
    assert url in json.loads(site.indexnow.manifest_path(cfg.site_dir).read_text())["versions"]


def test_label_render_rerun_is_idempotent_and_report_is_private(db, cfg, monkeypatch, tmp_path):
    row = seed(db, cfg, seen=NOW - timedelta(hours=30))
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    first = site.render_site(db, cfg, NOW)
    before = {str(p.relative_to(cfg.site_dir)): p.read_bytes() for p in Path(cfg.site_dir).rglob("*") if p.is_file()}
    second = site.render_site(db, cfg, NOW)
    assert before == {str(p.relative_to(cfg.site_dir)): p.read_bytes() for p in Path(cfg.site_dir).rglob("*") if p.is_file()}
    assert first == second
    assert not db.get_deal(row["dedupe_key"]).get("price_unconfirmed")
    snapshot = tmp_path / "snapshot.db"
    other = DB(snapshot)
    db.conn.backup(other.conn)
    other.close()
    assert report._unconfirmed_label(snapshot) == first["unconfirmed_label"]


def test_labeled_overflow_page_cannot_meet_confirmed_floor(db, cfg, monkeypatch):
    rows = [offer(str(i)) for i in range(61)] + [offer("confirmed", 1)]
    render(db, cfg, rows, monkeypatch)
    pages = read_pages(Path(cfg.site_dir))
    paginated = {path: page for path, page in pages.items() if "/page/2/" in path}
    assert {"/stores/amazon/page/2/", "/deals/under-25/page/2/", "/deals/home-kitchen/page/2/"} <= paginated.keys()
    for page in paginated.values():
        assert page.live_offers() == 0 and not page.indexable


@pytest.mark.parametrize("mode,expected", [
    ("membership", "9e1242be7ddc1a90247c59045b1ecc24f79b8d62b2098bb630fd728aa041e2a9"),
    ("open", "2f287cd9848346bf33d938d75d94a9494e279dc21236f79080a87b635a5e5d7b"),
    ("support", "1199278786483e8defc5c9a49e509913748a36520002f126dd1624df9075ca9f"),
    ("shipped", "3a0081492ea0aa70749a8e326dfd35f72adab2133ac36d16a367e1dc3223e1d6"),
])
def test_off_matches_all_prechange_published_bytes(db, tmp_path, monkeypatch, mode, expected):
    # Baselines rendered from untouched HEAD, including a hidden Amazon offer.
    from tests.test_publish_site import NOW as snapshot_time

    cfg = load_config() if mode == "shipped" else AppConfig(custom_domain="wowpriced.com", paywall_enabled=True)
    cfg.unconfirmed_label_enabled = False
    if mode == "shipped":
        cfg.non_amazon_freshness_enabled = False  # this pin predates task 102's independent age gate
        cfg.traffic_feed_details, cfg.traffic_feed_max_items = True, 50  # predates the RSS trim
        cfg.raw["first_party_clicks"]["enabled"] = False  # predates the click signal switch-on
        cfg.front_page["top_picks"] = {k: v for k, v in cfg.front_page["top_picks"].items()
                                       if k != "non_amazon_max_age_h"}  # predates the pick age
    if mode != "shipped":
        cfg.support_enabled = mode == "support"
        cfg.paywall_open_access = mode == "open"
    monkeypatch.setattr(site, "util_now", lambda: snapshot_time)
    rows = [make_deal(dedupe_key=f"item-{i}", category="home", conditions=[], codes=[]) for i in range(20)]
    rows += [make_deal(dedupe_key="hidden", store="Amazon", first_seen_at="2026-10-01T00:00:00Z")]
    out = tmp_path / "site"
    site.render_site(db, cfg, snapshot_time, out_dir=out, deals=rows)
    files = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(out.rglob("*")) if p.is_file()}
    assert hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest() == expected


def test_newsletter_does_not_select_or_send_raw_unconfirmed_prices(db, cfg, monkeypatch):
    cfg.newsletter_enabled = True
    cfg.newsletter_hour_local = 0
    cfg.newsletter_worker_url = "https://newsletter.example.test"
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("NEWSLETTER_SECRET", "offline-test-secret")
    monkeypatch.setattr(newsletter, "fetch_subscribers", lambda *args: [{"email": "subscriber@example.test"}])
    monkeypatch.setattr(newsletter, "todays_top", lambda *args, **kwargs: pytest.fail("must not pick unconfirmed prices"))
    result = newsletter.send_daily_newsletter(db, cfg, NOW, deals=[offer()],
        smtp_factory=lambda *args: pytest.fail("must not connect to SMTP"))
    assert result["sent"] == 0 and result["skipped"] == "no published deals yet today"


def test_closed_paywall_rotation_and_recheck_memory_ignore_labeled_stubs(db, cfg, monkeypatch):
    cfg.support_enabled = cfg.paywall_open_access = False
    cfg.paywall_enabled = True
    cfg.paywall_teaser_count = 1
    rows = [offer(), offer("confirmed", 1, price_rechecked_at=util.iso(NOW))]
    recent = json.dumps([site._serialize_deal(d, NOW)["key"] for d in rows])
    db.meta_set("teaser_recent", recent)
    cfg.unconfirmed_label_enabled = False
    render(db, cfg, rows, monkeypatch)
    keys = ("teaser_recent", site.PICKS_META, site.WALL_META)
    before = {key: db.meta_get(key) for key in keys}
    cfg.unconfirmed_label_enabled = True
    db.meta_set("teaser_recent", recent)
    render(db, cfg, rows, monkeypatch)
    assert {key: db.meta_get(key) for key in keys} == before
    public = json.loads((Path(cfg.site_dir) / "deals.json").read_text())["deals"]
    assert public[-1]["price_unconfirmed"] and public[-1]["locked"]
