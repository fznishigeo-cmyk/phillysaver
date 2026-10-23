"""Operator 2026-10-23 ~19:30Z: "slickdeals is super legit we dont need two
sources for them to confirm that the deal is good" (verify.trusted_sources)."""

from __future__ import annotations

import hashlib
import json
import socket
from datetime import timedelta

import httpx

from dealradar import rank, verify
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.publish import seo, site
from dealradar.publish.site import _serialize_deal
from dealradar.publish.telegram_pub import format_message, tier_rank
from tests.history_pins import pre_trusted_sources
from tests.test_front_page import options, pick_deal  # noqa: F401 (fixture)
from tests.test_publish_site import NOW as SITE_NOW, make_deal
from tests.test_pwa import tree_hash
from tests.test_seo import _render, cfg, db  # noqa: F401 (fixtures)
from tests.test_verify_rank import NOW, base_deal, make_cfg, upsert

SLICKDEALS = ["slickdeals_frontpage", "slickdeals_popular", "slickdeals_amazon", "slickdeals_blog"]


def trusted_cfg():
    cfg = make_cfg()
    cfg.trusted_sources = list(SLICKDEALS)
    return cfg


def test_shipped_knob_lists_every_slickdeals_source():
    from dealradar.config import load_sources
    from dealradar.fetch.newsletter import match_sender

    assert load_config().trusted_sources == SLICKDEALS
    primary, backup = load_sources()
    named = {s.name: s for s in primary + backup}
    assert {n for n, s in named.items() if "slickdeals.net" in s.site + s.url} == set(SLICKDEALS)
    assert {named[n].display_name for n in SLICKDEALS} == {"Slickdeals"}
    # Slickdeals' deal-alert emails are credited to one of its feeds, not a source of their own.
    assert match_sender("alerts@slickdeals.net", primary + backup).name in SLICKDEALS


def test_trusted_source_alone_is_trusted(tmp_path):
    db = DB(tmp_path / "state.db")
    cfg = trusted_cfg()
    res = upsert(db, cfg, source="slickdeals_frontpage", uid="a", now=NOW - timedelta(hours=1))
    assert verify.assign_tier(db, db.get_deal(res["dedupe_key"]), cfg, NOW) == "trusted"
    verify.refresh_tiers(db, cfg, NOW)
    assert db.get_deal(res["dedupe_key"])["verify_tier"] == "trusted"
    db.close()


def test_two_slickdeals_feeds_are_still_one_source(tmp_path):
    db = DB(tmp_path / "state.db")
    cfg = trusted_cfg()
    res = upsert(db, cfg, source="slickdeals_frontpage", uid="a", now=NOW - timedelta(hours=2))
    upsert(db, cfg, source="slickdeals_popular", uid="b", now=NOW - timedelta(hours=1))
    assert verify.assign_tier(db, db.get_deal(res["dedupe_key"]), cfg, NOW) == "trusted"
    db.close()


def test_untrusted_single_source_stays_unverified(tmp_path):
    db = DB(tmp_path / "state.db")
    cfg = trusted_cfg()
    res = upsert(db, cfg, source="hip2save", uid="a", now=NOW - timedelta(hours=1))
    assert verify.assign_tier(db, db.get_deal(res["dedupe_key"]), cfg, NOW) == "unverified"
    db.close()


def test_two_sources_still_corroborated(tmp_path):
    db = DB(tmp_path / "state.db")
    cfg = trusted_cfg()
    res = upsert(db, cfg, source="hip2save", uid="a", now=NOW - timedelta(hours=2))
    upsert(db, cfg, source="slickdeals_frontpage", uid="b", now=NOW - timedelta(hours=1))
    assert verify.assign_tier(db, db.get_deal(res["dedupe_key"]), cfg, NOW) == "corroborated"
    db.close()


def test_trusted_sighting_must_be_in_the_window_at_this_price(tmp_path):
    db = DB(tmp_path / "state.db")
    cfg = trusted_cfg()
    res = upsert(db, cfg, source="slickdeals_frontpage", uid="a", now=NOW - timedelta(hours=30))
    assert verify.assign_tier(db, db.get_deal(res["dedupe_key"]), cfg, NOW) == "unverified"
    # Another source restates a different price: Slickdeals' old price vouches for nothing.
    upsert(db, cfg, source="slickdeals_popular", uid="b", now=NOW - timedelta(hours=2), final=25.0)
    upsert(db, cfg, source="hip2save", uid="c", now=NOW - timedelta(hours=1), final=10.0)
    deal = db.get_deal(res["dedupe_key"])
    assert deal["final_price"] == 10.0
    assert verify.assign_tier(db, deal, cfg, NOW) == "unverified"
    db.close()


def test_empty_knob_keeps_the_previous_tier(tmp_path):
    db = DB(tmp_path / "state.db")
    cfg = make_cfg()
    assert cfg.trusted_sources == []
    res = upsert(db, cfg, source="slickdeals_frontpage", uid="a", now=NOW - timedelta(hours=1))
    assert verify.assign_tier(db, db.get_deal(res["dedupe_key"]), cfg, NOW) == "unverified"
    db.close()


def _slickdeals_deal(**changes):
    seen = "2026-10-09T12:00:00+00:00"
    return make_deal(**{"verify_tier": "trusted", "codes": [], "sightings": [
        {"source_name": "slickdeals_popular", "source_url": "https://slickdeals.net/f/1",
         "seen_at": seen, "final_price": 10.0}], **changes})


def test_public_text_names_the_source_and_never_claims_two(cfg):
    cfg.trusted_sources = list(SLICKDEALS)
    headline = _serialize_deal(_slickdeals_deal(), SITE_NOW, cfg=cfg)["headline"]
    assert headline.endswith(" Posted on Slickdeals.")
    assert "not yet confirmed" not in headline and "sources" not in headline
    # A tier the knob no longer backs reads exactly as an unverified deal.
    cfg.trusted_sources = []
    assert _serialize_deal(_slickdeals_deal(), SITE_NOW, cfg=cfg)["headline"] == \
        _serialize_deal(_slickdeals_deal(verify_tier="unverified"), SITE_NOW, cfg=cfg)["headline"]


def _published(out, deal):
    page = out / seo.deal_path(deal["item"], hashlib.sha256(
        deal["dedupe_key"].encode()).hexdigest()[:8]).strip("/") / "index.html"
    return json.loads((out / "deals.json").read_text())["deals"], page.read_text()


def test_rendered_card_and_deal_page(db, cfg, tmp_path):
    cfg.paywall_open_access = True
    cfg.trusted_sources = list(SLICKDEALS)
    deal = _slickdeals_deal()
    _render(db, cfg, tmp_path, [deal], SITE_NOW)
    (public,), page = _published(tmp_path, deal)
    assert public["badge"] == "trusted" and public["headline"].endswith(" Posted on Slickdeals.")
    assert "<p>single source: Posted on Slickdeals.</p>" in page
    assert "not yet independently confirmed" not in page and "2+ sources" not in page


#: The whole shipped site after verify.refresh_tiers, recorded at af1674b
#: (before verify.trusted_sources): one Slickdeals-only and one Hip2Save-only deal.
RECORDED_OFF = "e1ae53d2edd0acd74f641377110d99416e4083f984c1bce7c94d3f374a5fcc75"


def _tiered_site(tmp_path, cfg, monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("trusted sources are offline")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    tmp_path.mkdir()
    db = DB(tmp_path / "state.db")
    upsert(db, cfg, source="slickdeals_popular", uid="a", now=NOW - timedelta(hours=1))
    upsert(db, cfg, source="hip2save", uid="b", item="Gadget Max 300",
           url="https://www.target.com/p/gadget", now=NOW - timedelta(hours=1))
    for deal in db.all_deals():  # judged and photographed, so both publish
        deal.update(critic={"plausible": True, "confidence": 0.9, "reason": "recorded"},
                    image_url=f"https://images.example/{deal['dedupe_key'][:8]}.jpg")
        db.update_deal(deal["dedupe_key"], payload={k: v for k, v in deal.items() if k != "id"})
    try:
        verify.refresh_tiers(db, cfg, NOW)
        site.render_site(db, cfg, NOW, out_dir=tmp_path / "site")
    finally:
        db.close()
    return tmp_path / "site"


def test_empty_knob_is_the_recorded_site(tmp_path, monkeypatch):
    off = _tiered_site(tmp_path / "off", pre_trusted_sources(load_config()), monkeypatch)
    assert tree_hash(off) == RECORDED_OFF
    on = _tiered_site(tmp_path / "on", load_config(), monkeypatch)
    assert tree_hash(on) != RECORDED_OFF
    public = json.loads((on / "deals.json").read_text())["deals"]
    assert sorted((d["badge"], d["headline"].rsplit(". ", 1)[-1]) for d in public) == [
        ("trusted", "Posted on Slickdeals."), ("unverified", "Reported by one source — not yet confirmed.")]


def test_ranks_like_corroborated(cfg, options):  # noqa: F811
    shipped = load_config()
    trusted, corroborated = base_deal(verify_tier="trusted"), base_deal(verify_tier="corroborated")
    assert rank.score(trusted, shipped, NOW) == rank.score(corroborated, shipped, NOW)
    assert rank.score(trusted, shipped, NOW) > rank.score(base_deal(), shipped, NOW)
    options["appeal"]["enabled"] = False
    cfg.front_page = options
    deal = pick_deal()
    picked = rank.pick_score(dict(deal, verify_tier="trusted"), cfg, SITE_NOW)
    assert picked is not None and picked == rank.pick_score(dict(deal, verify_tier="corroborated"), cfg, SITE_NOW)
    assert picked > rank.pick_score(dict(deal, verify_tier="unverified"), cfg, SITE_NOW)
    assert tier_rank("trusted") == tier_rank("corroborated")


def test_alert_copy_carries_no_multi_source_label():
    message = format_message(_slickdeals_deal(), cfg=load_config())
    assert "2+ sources" not in message and "(80% off)" in message
