"""Permanent social arrivals, using only offline publication and private state."""

import json
from datetime import timedelta

import pytest
import yaml

from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.publish import seo
from dealradar.publish.site import render_site
from tests.test_publish_site import NOW, make_deal
from tests.test_seo_deal_pages import _page, _path

META = seo._POSTED_REDIRECTS_META


@pytest.fixture(autouse=True)
def frozen_publish_time(monkeypatch):
    monkeypatch.setattr("dealradar.publish.site.util_now", lambda: NOW)


@pytest.fixture
def db(tmp_path):
    database = DB(tmp_path / "state.db")
    yield database
    database.close()


@pytest.fixture
def cfg():
    return AppConfig(custom_domain="wowpriced.com", paywall_open_access=True)


def _insert(db, **overrides):
    d = make_deal(codes=[], **overrides)
    db.insert_deal(dedupe_key=d["dedupe_key"], store=d["store"], item=d["item"],
                   normalized_item=d["item"].lower(), canonical_url=d["canonical_url"], payload=d,
                   final_price=d["final_price"], pct_at_checkout=d["pct_at_checkout"],
                   abs_savings=d["abs_savings"], online_only="true", expires_at="", now=NOW)
    return d


def _post(db, d, channel="pinterest"):
    db.record_alert(deal_key=d["dedupe_key"], channel=channel,
                    dedup_token=d["dedupe_key"] + "|" + channel, now=NOW)


def _prune(db, deals, now=NOW):
    for d in deals:
        db.update_deal(d["dedupe_key"], status="dead",
                       payload=dict(d, died_at=(now - timedelta(days=15)).isoformat()))
    db.prune(now)
    assert all(db.get_deal(d["dedupe_key"]) is None for d in deals)


def _render(db, cfg, out, now=NOW, deals=None):
    return render_site(db, cfg, now, out_dir=out, deals=deals)


@pytest.mark.parametrize("channel", ["pinterest", "facebook", "instagram", "telegram",
                                      "meta:facebook:live", "meta:instagram:live"])
def test_posted_pruned_url_survives_fresh_deploys_and_unposted_does_not(db, cfg, tmp_path, channel):
    posted = _insert(db, dedupe_key="posted", category="kitchen")
    unposted = _insert(db, dedupe_key="unposted", item="Unposted product", category="auto")
    _post(db, posted, channel)
    _render(db, cfg, tmp_path / "before")
    assert not (tmp_path / "before/_redirects").exists()
    assert set(json.loads(db.meta_get(META))) == {_path(posted)}
    _prune(db, [posted, unposted])
    result = _render(db, cfg, tmp_path / "after")
    expected = f"{_path(posted)} /deals/home-kitchen/ 301\n".encode()
    assert (tmp_path / "after/_redirects").read_bytes() == expected
    assert not _page(tmp_path / "after", _path(posted)).exists()
    assert not _page(tmp_path / "after", _path(unposted)).exists()
    assert db.meta_get("seo_deal_paths") == "{}"
    assert result["posted_redirects"] == {"rules": 1, "added_today": 1, "dropped_by_cap": 0}
    state = db.meta_get(META)
    assert json.loads(state) == {_path(posted): {"target": "/deals/home-kitchen/", "added_at": NOW.isoformat()}}
    assert "posted_redirects" not in (tmp_path / "after/deals.json").read_text()
    assert _path(posted) not in (tmp_path / "after/sitemap.xml").read_text()
    _render(db, cfg, tmp_path / "next", now=NOW + timedelta(days=1))
    assert (tmp_path / "next/_redirects").read_bytes() == expected
    assert db.meta_get(META) == state


@pytest.mark.parametrize("ledger,network", [("pinterest_posted", None),
                                          ("meta_posted", "facebook"), ("meta_posted", "instagram")])
def test_existing_social_success_ledgers_are_read_without_changing_posters(db, cfg, tmp_path, ledger, network):
    deal = _insert(db, category="auto")
    entry = {deal["dedupe_key"]: {"id": "offline-id", "at": NOW.isoformat()}}
    db.meta_set(ledger, json.dumps({network: entry} if network else entry))
    before = db.meta_get(ledger)
    _render(db, cfg, tmp_path / "before")
    _prune(db, [deal])
    _render(db, cfg, tmp_path / "after")
    assert (tmp_path / "after/_redirects").read_text() == f"{_path(deal)} /deals/automotive/ 301\n"
    assert db.meta_get(ledger) == before


@pytest.mark.parametrize("channel", ["email_digest", "member_email", "admin", "meta:facebook:draft",
                                      "meta:instagram:draft"])
def test_non_social_alerts_and_meta_drafts_do_not_create_redirects(db, cfg, tmp_path, channel):
    deal = _insert(db)
    _post(db, deal, channel)
    db.meta_set("meta_drafts", json.dumps({"facebook": {deal["dedupe_key"]: {"path": _path(deal)}}}))
    _render(db, cfg, tmp_path / "before")
    _prune(db, [deal])
    _render(db, cfg, tmp_path / "after")
    assert db.meta_get(META) is None
    assert not (tmp_path / "after/_redirects").exists()


@pytest.mark.parametrize("category,target", [("kitchen", "/deals/home-kitchen/"),
                                           ("auto", "/deals/automotive/"),
                                           ("unknown", "/deals/other/")])
def test_target_matches_detail_hub_and_ignores_used_condition(db, cfg, tmp_path, category, target):
    deal = _insert(db, category=category, conditions=["used"])
    _post(db, deal)
    _render(db, cfg, tmp_path / "before")
    detail = _page(tmp_path / "before", _path(deal)).read_text()
    assert f'<p>Browse <a href="{target}">' in detail
    _prune(db, [deal])
    _render(db, cfg, tmp_path / "after")
    assert (tmp_path / "after/_redirects").read_text() == f"{_path(deal)} {target} 301\n"


def test_ended_page_stays_until_pruned_and_revived_page_wins(db, cfg, tmp_path):
    deal = _insert(db, category="kitchen")
    _post(db, deal)
    out = tmp_path / "site"
    _render(db, cfg, out)
    db.update_deal(deal["dedupe_key"], status="dead", payload=dict(deal, died_at=NOW.isoformat()))
    _render(db, cfg, out)
    assert "This deal has ended" in _page(out, _path(deal)).read_text()
    assert not (out / "_redirects").exists()
    _prune(db, [deal])
    _render(db, cfg, out)
    assert (out / "_redirects").is_file()
    _insert(db, category="kitchen")
    result = _render(db, cfg, out)
    assert _page(out, _path(deal)).is_file()
    assert not (out / "_redirects").exists()
    assert result["posted_redirects"]["rules"] == 0


def test_cap_drops_oldest_activation_first_and_reruns_are_stable(db, cfg, tmp_path):
    cfg.seo_posted_deal_redirects_max_rules = 2
    deals = [_insert(db, dedupe_key=f"posted-{i}", item=f"Item {i}", category="auto") for i in range(3)]
    for deal in deals:
        _post(db, deal)
    out = tmp_path / "site"
    _render(db, cfg, out)
    for day, deal in enumerate(deals):
        _prune(db, [deal], now=NOW + timedelta(days=day))
        result = _render(db, cfg, out, now=NOW + timedelta(days=day))
    assert result["posted_redirects"] == {"rules": 2, "added_today": 1, "dropped_by_cap": 1}
    expected = ''.join(f"{p} /deals/automotive/ 301\n" for p in sorted(_path(d) for d in deals[1:])).encode()
    assert (out / "_redirects").read_bytes() == expected
    state = db.meta_get(META)
    counts = db.conn.execute("SELECT COUNT(*) FROM alerts_sent").fetchone()[0]
    for _ in range(2):
        result = _render(db, cfg, out, now=NOW + timedelta(days=2))
        assert (out / "_redirects").read_bytes() == expected and db.meta_get(META) == state
        assert result["posted_redirects"]["dropped_by_cap"] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM alerts_sent").fetchone()[0] == counts


def test_cap_does_not_readmit_still_stored_paths_or_count_live_pages(db, cfg, tmp_path):
    cfg.seo_posted_deal_redirects_max_rules = 1
    deals = [_insert(db, dedupe_key=f"posted-{i}", item=f"Item {i}") for i in range(3)]
    for deal in deals:
        _post(db, deal)
    out = tmp_path / "site"
    _render(db, cfg, out)
    assert len(json.loads(db.meta_get(META))) == 3
    cfg.seo_deal_pages_max = 1
    result = _render(db, cfg, out)
    assert result["posted_redirects"] == {"rules": 1, "added_today": 1, "dropped_by_cap": 1}
    before, state = (out / "_redirects").read_bytes(), db.meta_get(META)
    for _ in range(2):
        _render(db, cfg, out)
        assert (out / "_redirects").read_bytes() == before and db.meta_get(META) == state
    dropped = [d for d in deals if json.loads(state).get(_path(d), {}).get("dropped")]
    assert len(dropped) == 1
    _prune(db, dropped)
    _render(db, cfg, out)
    assert (out / "_redirects").read_bytes() == before
    assert _path(dropped[0]) not in json.loads(db.meta_get(META))


def test_newly_posted_path_redirects_if_next_publish_caps_it_out(db, cfg, tmp_path):
    deals = [_insert(db, dedupe_key=f"posted-{i}", item=f"Item {i}", category="kitchen") for i in range(3)]
    _post(db, deals[0])
    out = tmp_path / "site"
    _render(db, cfg, out, deals=deals)
    _post(db, deals[2])  # Posters run after publication, so no pending entry yet.
    cfg.seo_deal_pages_max = 1
    _render(db, cfg, out, deals=deals)
    assert (out / "_redirects").read_text() == f"{_path(deals[2])} /deals/home-kitchen/ 301\n"


def test_first_send_is_remembered_before_a_rerun_or_prune(db, cfg, tmp_path):
    deal = _insert(db, category="kitchen")
    result = _render(db, cfg, tmp_path / "before")
    assert db.meta_get(META) is None
    _post(db, deal, "telegram")
    seo.remember_posted_pages(db, cfg, result["public_deals"])
    assert json.loads(db.meta_get(META)) == {_path(deal): {"target": "/deals/home-kitchen/", "added_at": None}}
    before = list(db.conn.iterdump())
    seo.remember_posted_pages(db, cfg, result["public_deals"])
    assert list(db.conn.iterdump()) == before
    _prune(db, [deal])
    _render(db, cfg, tmp_path / "after")
    assert (tmp_path / "after/_redirects").read_text() == f"{_path(deal)} /deals/home-kitchen/ 301\n"


@pytest.mark.parametrize("mode", ["support", "open", "closed"])
def test_disabled_and_enabled_without_posts_publish_identical_trees(tmp_path, mode):
    trees = []
    for enabled in (False, True):
        cfg = AppConfig(custom_domain="wowpriced.com", support_enabled=mode == "support",
                        paywall_open_access=mode == "open", seo_posted_deal_redirects_enabled=enabled)
        db = DB(tmp_path / f"{enabled}.db")
        out = tmp_path / str(enabled)
        _render(db, cfg, out, deals=[make_deal(dedupe_key=f"item-{i}") for i in range(12)])
        assert db.meta_get(META) is None and not (out / "_redirects").exists()
        trees.append({p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()})
        db.close()
    assert trees[0] == trees[1]


def test_disabled_removes_stale_file_without_reading_or_writing_redirect_state(db, cfg, tmp_path, monkeypatch):
    deal = _insert(db)
    _post(db, deal)
    _render(db, cfg, tmp_path)
    _prune(db, [deal])
    _render(db, cfg, tmp_path)
    state = db.meta_get(META)
    cfg.seo_posted_deal_redirects_enabled = False
    get, set_ = db.meta_get, db.meta_set

    def guarded_get(key):
        assert key != META
        return get(key)

    def guarded_set(key, value):
        assert key != META
        return set_(key, value)

    monkeypatch.setattr(db, "meta_get", guarded_get)
    monkeypatch.setattr(db, "meta_set", guarded_set)
    _render(db, cfg, tmp_path)
    seo.remember_posted_pages(db, cfg, [])
    assert not (tmp_path / "_redirects").exists() and get(META) == state


@pytest.mark.parametrize("path", ["/", "/deals/automotive/", "/deal/item-1234567/", "/deal/item-zzzzzzzz/",
                                   "/deal/../item-12345678/", "/deal/item-12345678/?x=1",
                                   "/deal/item-12345678/\n/ / 301", "https://wowpriced.com/deal/item-12345678/"])
def test_invalid_remembered_paths_are_never_redirected(db, cfg, tmp_path, path):
    db.meta_set(META, json.dumps({path: {"target": "/deals/automotive/", "added_at": NOW.isoformat()}}))
    _render(db, cfg, tmp_path, deals=[])
    assert not (tmp_path / "_redirects").exists() and db.meta_get(META) == "{}"


@pytest.mark.parametrize("rule", [{"target": "https://example.com/", "added_at": NOW.isoformat()},
                                  {"target": ["/"], "added_at": None},
                                  {"target": "/", "added_at": 42},
                                  {"target": "/", "added_at": "bad-date"}])
def test_invalid_state_cannot_inject_redirect_targets(db, cfg, tmp_path, rule):
    db.meta_set(META, json.dumps({"/deal/item-12345678/": rule}))
    _render(db, cfg, tmp_path, deals=[])
    assert not (tmp_path / "_redirects").exists()


@pytest.mark.parametrize("max_rules", [1, 2, 1500, 1900])
def test_config_accepts_integer_limits_and_ships_enabled(tmp_path, max_rules):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"seo": {"posted_deal_redirects": {"max_rules": max_rules}}}))
    cfg = load_config(path)
    assert cfg.seo_posted_deal_redirects_enabled and cfg.seo_posted_deal_redirects_max_rules == max_rules
    shipped = load_config()
    assert shipped.seo_posted_deal_redirects_enabled and shipped.seo_posted_deal_redirects_max_rules == 1500


@pytest.mark.parametrize("settings", [None, [], {"enabled": "false"}, {"enabled": 1},
                                       *({"max_rules": v} for v in (0, -1, 1901, True, 2.5, "2", None))])
def test_config_rejects_invalid_redirect_settings(tmp_path, settings):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"seo": {"posted_deal_redirects": settings}}))
    with pytest.raises(ValueError, match="seo.posted_deal_redirects"):
        load_config(path)
