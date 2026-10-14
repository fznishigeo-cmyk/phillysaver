"""Offline legitimacy rules, real critic integration, publication and replay."""

from datetime import timedelta

import pytest

from dealradar import legitimacy, pipeline, rank, verify
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.growth.report import _legitimacy_audit
from dealradar.parse.claude_client import ClaudeParser, estimate_critic_cost
from dealradar.util import iso
from tests.test_quality_trio import NOW, _FakeClient, _CriticMessages, _cfg


@pytest.fixture
def db(tmp_path):
    db = DB(tmp_path / "audit.db")
    yield db
    db.close()


def add(db, key="widget", **over):
    d = dict(dedupe_key=key, store="Acme", item="Widget", regular_price=100.,
             final_price=20., pct_at_checkout=80., abs_savings=80., conditions=[],
             online_only="true", flags=[], canonical_url="", asin="")
    d.update(over)
    db.insert_deal(dedupe_key=key, store=d["store"], item=d["item"], normalized_item=key,
                   canonical_url=d["canonical_url"], payload=d, final_price=d["final_price"],
                   pct_at_checkout=d["pct_at_checkout"], abs_savings=d["abs_savings"],
                   online_only="true", expires_at="", now=NOW)
    return db.get_deal(key)


def amazon(db, key="widget", **over):
    return add(db, key, **(dict(store="Amazon", asin="B000000001",
                               regular_price_basis="90-day average on Amazon",
                               keepa_price_seen_at=iso(NOW),
                               audit_reference={"asin": "B000000001", "price": 100.,
                                                "basis": "90-day average on Amazon",
                                                "condition": "new"}) | over))


def history(db, d, prices=(100., 100., 100., 100., 100.), *, gap_h=24):
    for i, price in enumerate(prices):
        db.record_price(d["dedupe_key"], d["store"], price,
                        NOW - timedelta(hours=(len(prices) - i) * gap_h), f"history-{i}")


def audit(db, d, cfg=None):
    return verify.audit_verdict(db, d, cfg or _cfg(), NOW)


@pytest.mark.parametrize("label", ["List Price on Amazon", "30-day average on Amazon", "90-day average on Amazon"])
def test_amazon_bases_are_verified_without_network(db, monkeypatch, label):
    monkeypatch.setattr(verify, "keepa_products", lambda *a, **k: pytest.fail("new Keepa call"))
    d = amazon(db, regular_price_basis=label, audit_reference=None)
    assert audit(db, d) == {"outcome": "verified", "reason": "amazon_reference"}


@pytest.mark.parametrize("over", [
    {"regular_price_basis": "MSRP", "audit_reference": None},
    {"keepa_price_seen_at": "", "audit_reference": None},
    {"store": "Woot"}, {"asin": ""},
    {"conditions": ["used - like new"]}, {"quantity_or_unit": "pack of 6"},
    {"flags": ["per_unit"]}, {"price_is_range": True},
    {"final_price": 61., "pct_at_checkout": 39.},
])
def test_uncertain_amazon_basis_needs_critic(db, over):
    assert audit(db, amazon(db, **over))["outcome"] == "needs_critic"


def test_stored_amazon_reference_rejects_inflated_claim(db):
    d = amazon(db, regular_price=200.)
    assert audit(db, d) == {"outcome": "reject", "reason": "amazon_reference_exceeded"}
    cfg = _cfg()
    cfg.audit_reference_max_excess_pct = 20
    d["regular_price"] = 120.
    assert audit(db, d, cfg)["outcome"] == "verified"
    d["regular_price"] = 120.01
    assert audit(db, d, cfg)["outcome"] == "reject"


@pytest.mark.parametrize("flags,expected", [(["unit_mismatch"], "reject"), (["pack_mismatch"], "reject"),
                                           (["unknown_shipping"], "verified"), ([], "verified")])
def test_unit_flags_precede_supported_basis(db, flags, expected):
    assert audit(db, amazon(db, flags=flags))["outcome"] == expected


@pytest.mark.parametrize("pct,item,expected", [(85., "Widget", "verified"), (85.01, "Widget", "needs_critic"),
                                              (90., "Widget clearance", "verified"),
                                              (90., "ClearanceColor widget", "needs_critic")])
def test_extreme_new_discount_boundary_and_clearance(db, pct, item, expected):
    assert audit(db, amazon(db, pct_at_checkout=pct, final_price=10., item=item))["outcome"] == expected


def test_extreme_threshold_is_configured_and_used_remains_ambiguous(db):
    d = amazon(db, pct_at_checkout=90., final_price=10.)
    cfg = _cfg()
    cfg.audit_extreme_pct = 92
    assert audit(db, d, cfg)["outcome"] == "verified"
    d["conditions"] = ["open box"]
    assert audit(db, d, cfg)["reason"] == "used_comparison"


@pytest.mark.parametrize("prices,gap_h,expected", [
    ([100.] * 5, 24, "verified"), ([100.] * 4, 24, "needs_critic"),
    ([20.] * 5, 24, "needs_critic"), ([100.] * 5, 1, "needs_critic"),
    ([20., 20., 20., 20., 100.], 24, "needs_critic"),
    ([20., 100., 20., 100., 20.], 24, "verified"),
    ([89.99] * 5, 24, "needs_critic"), ([90.] * 5, 24, "verified"),
])
def test_recorded_regular_price_support(db, prices, gap_h, expected):
    d = add(db)
    history(db, d, prices, gap_h=gap_h)
    assert audit(db, d)["outcome"] == expected


def test_history_same_key_only_future_excluded_and_min_pct_controls_floor(db):
    d = add(db)
    other = add(db, "other")
    history(db, other)
    history(db, d, gap_h=-24)
    assert audit(db, d)["outcome"] == "needs_critic"
    db.conn.execute("DELETE FROM price_history")
    history(db, d)
    cfg = _cfg()
    cfg.min_pct = 81
    assert audit(db, d, cfg)["outcome"] == "needs_critic"
    cfg.min_pct = 80
    assert audit(db, d, cfg)["reason"] == "recorded_regular_price"


@pytest.mark.parametrize("sources,expected", [
    ([("one", "one.example", 20.), ("two", "two.example", 20.)], "corroborated_price_only"),
    ([("one", "one.example", 20.), ("feed", "one.example", 20.)], "round_regular_without_history"),
    ([("one", "one.example", 20.), ("two", "two.example", 21.)], "round_regular_without_history"),
])
def test_corroboration_requires_independent_sites_at_same_price(db, sources, expected):
    d = add(db)
    for i, (name, host, price) in enumerate(sources):
        db.add_sighting(d["id"], source_name=name, source_url=f"https://{host}/post",
                        content_hash=str(i), seen_at=NOW, final_price=price)
    assert audit(db, d) == {"outcome": "needs_critic", "reason": expected}


def test_round_price_rule_false_cases(db):
    d = add(db, regular_price=99.99)
    assert audit(db, d)["reason"] == "unsupported_regular_price"
    d["regular_price"] = 100.
    history(db, d, [100.])
    assert audit(db, d)["reason"] == "unsupported_regular_price"


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
@pytest.mark.parametrize("plausible", [True, False])
def test_shadow_agreement_and_enforce_skip_reject(db, mode, plausible):
    cfg = _cfg(critic_shadow_model="")
    cfg.audit_mode = mode
    amazon(db)
    add(db, "bad", flags=["unit_mismatch"])
    add(db, "unknown", regular_price=99.)
    msgs = _CriticMessages(plausible)
    parser = ClaudeParser(cfg, client=_FakeClient(msgs))
    stats = {}
    result = pipeline._run_critic(db, cfg, parser, NOW, audit_stats=stats)
    assert stats["outcomes"] == {"verified": 1, "reject": 1, "needs_critic": 1}
    assert result["judged"] == (3 if mode == "shadow" else 1)
    assert stats["critic_calls_avoided"] == 0  # this mixed batch still makes one call
    assert stats["would_avoid_deals"] == 2
    assert stats["estimated_usd_if_enforced"] > 0
    bad = db.get_deal("bad")
    if mode == "shadow":
        assert stats["estimated_usd_saved"] == 0
        assert stats["agreement_by_reason"]["amazon_reference"]["rate"] == int(plausible)
        assert stats["agreement_by_reason"]["unit_pack_mismatch"]["rate"] == int(not plausible)
        assert ("suspect_msrp" in bad["flags"]) == (not plausible)
    else:
        assert stats["critic_deals_avoided"] == 2
        assert stats["estimated_usd_saved"] > 0
        assert stats["agreement_by_reason"] == {}
        assert bad.get("critic") is None
        assert not rank.is_publishable(bad, cfg)[0]
        assert rank.is_publishable(db.get_deal("widget"), cfg)[0]
    assert rank.is_publishable(db.get_deal("unknown"), cfg)[0] == plausible


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_full_batch_calls_saved_and_replay_is_idempotent(db, mode):
    cfg = _cfg(critic_shadow_model="")
    cfg.audit_mode = mode
    amazon(db)
    msgs = _CriticMessages(True)
    parser = ClaudeParser(cfg, client=_FakeClient(msgs))
    stats = {}
    pipeline._run_critic(db, cfg, parser, NOW, audit_stats=stats)
    assert stats["would_avoid_calls"] == 1
    assert stats["critic_calls_avoided"] == int(mode == "enforce")
    assert len(msgs.calls) == int(mode == "shadow")
    counts = [db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("deals", "price_history", "legitimacy_audits", "legitimacy_batches")]
    pipeline._run_critic(db, cfg, parser, NOW, audit_stats=stats)
    assert stats["outcomes"] == {"verified": 0, "reject": 0, "needs_critic": 0}
    assert stats["would_avoid_calls"] == stats["critic_calls_avoided"] == 0
    assert counts == [db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("deals", "price_history", "legitimacy_audits", "legitimacy_batches")]
    daily = _legitimacy_audit(db.path, cfg, NOW + timedelta(days=1))
    assert daily["outcomes"]["verified"] == 1
    assert daily["would_avoid_calls"] == 1
    assert "agreement by reason:" in legitimacy.daily_line(daily)


@pytest.mark.parametrize("failure", ["no_parser", "disabled", "spend_cap", "malformed"])
def test_enforce_never_publishes_without_a_code_basis_or_critic(db, failure):
    cfg = _cfg(critic_shadow_model="", min_pct=70.)
    cfg.audit_mode = "enforce"
    amazon(db)
    add(db, "unknown")
    add(db, "site-only", final_price=35., pct_at_checkout=65.)
    cfg.site_show_from_pct = 60.
    parser = ClaudeParser(cfg, client=_FakeClient(_CriticMessages(True)))
    if failure == "no_parser":
        parser = None
    elif failure == "disabled":
        cfg.critic_enabled = False
    elif failure == "spend_cap":
        parser.spend_usd = parser.run_cap_usd
    else:
        parser.client.messages.parse = lambda **kw: object()
    pipeline._run_critic(db, cfg, parser, NOW)
    assert [d["dedupe_key"] for d in rank.site_deals(db, cfg, NOW)] == ["widget"]
    assert [d["dedupe_key"] for d in rank.published_deals(db, cfg, NOW)] == ["widget"]


@pytest.mark.parametrize("change", [{"regular_price": 200.}, {"final_price": 21.},
                                    {"conditions": ["open_box"]}, {"flags": ["pack_mismatch"]}])
def test_changed_facts_invalidate_code_verdict(db, change):
    cfg = _cfg()
    cfg.audit_mode = "enforce"
    amazon(db)
    pipeline._run_critic(db, cfg, None, NOW)
    d = db.get_deal("widget")
    assert rank.is_publishable(d, cfg)[0]
    d.update(change)
    assert not rank.is_publishable(d, cfg)[0]


def test_shadow_restore_does_not_keep_audit_owned_rejection(db):
    cfg = _cfg(critic_shadow_model="")
    cfg.audit_mode = "enforce"
    add(db, flags=["unit_mismatch"])
    pipeline._run_critic(db, cfg, None, NOW)
    assert "suspect_msrp" in db.get_deal("widget")["flags"]
    cfg.audit_mode = "shadow"
    parser = ClaudeParser(cfg, client=_FakeClient(_CriticMessages(True)))
    pipeline._run_critic(db, cfg, parser, NOW)
    assert "suspect_msrp" not in db.get_deal("widget")["flags"]


def test_cost_estimate_prices_the_selected_critic_not_the_parse_model(db):
    cfg = _cfg()
    parser = ClaudeParser(cfg, client=_FakeClient(_CriticMessages(True)))
    d = add(db)
    cost = estimate_critic_cost(parser, [d])
    cfg.model_pricing_per_mtok[cfg.parse_model] = {"input": 999., "output": 999.}
    assert estimate_critic_cost(parser, [d]) == cost
    cfg.model_pricing_per_mtok[cfg.critic_model] = {
        k: v * 2 for k, v in cfg.model_pricing_per_mtok[cfg.critic_model].items()}
    assert estimate_critic_cost(parser, [d]) == pytest.approx(2 * cost)


@pytest.mark.parametrize("setting", ["mode: invalid", "extreme_pct: 101", "near_price_pct: .nan",
                                     "history_min_near: 1.5", "round_price_multiple: 0"])
def test_invalid_config_rejected(tmp_path, setting):
    path = tmp_path / "config.yaml"
    path.write_text("audit:\n  " + setting)
    with pytest.raises(ValueError, match="audit"):
        load_config(path)


def test_default_shadow_and_absent_daily_sample(db):
    cfg = load_config()
    assert cfg.audit_mode == "shadow"
    assert "unavailable" in legitimacy.daily_stats(db, cfg, NOW)


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_known_batch_reduction_and_new_candidates_do_not_recount_skips(db, mode):
    cfg = _cfg(critic_shadow_model="", audit_mode=mode)
    for i in range(8):
        amazon(db, f"amazon-{i}", asin=f"B{i:09d}", audit_reference=None)
    for i in range(8):
        add(db, f"unknown-{i}", regular_price=99.)
    for i in range(8):
        add(db, f"bad-{i}", flags=["unit_mismatch"])
    parser = ClaudeParser(cfg, client=_FakeClient(_CriticMessages(True)))
    stats = {}
    critic = pipeline._run_critic(db, cfg, parser, NOW, audit_stats=stats)
    assert critic["calls"] == (3 if mode == "shadow" else 1)
    assert stats["would_avoid_calls"] == 2
    assert stats["would_avoid_deals"] == 16
    add(db, "new-candidate", regular_price=99.)
    pipeline._run_critic(db, cfg, parser, NOW, audit_stats=stats)
    assert stats["would_avoid_deals"] == stats["critic_calls_avoided"] == 0


def test_late_critic_agreement_is_reported_on_comparison_day(db):
    cfg = _cfg(critic_shadow_model="")
    amazon(db)
    pipeline._run_critic(db, cfg, None, NOW)
    parser = ClaudeParser(cfg, client=_FakeClient(_CriticMessages(True)))
    pipeline._run_critic(db, cfg, parser, NOW + timedelta(days=1))
    first = legitimacy.daily_stats(db, cfg, NOW + timedelta(days=1))
    second = legitimacy.daily_stats(db, cfg, NOW + timedelta(days=2))
    assert first["outcomes"]["verified"] == 1 and first["agreement_by_reason"] == {}
    assert second["outcomes"]["verified"] == 0
    assert second["agreement_by_reason"]["amazon_reference"]["rate"] == 1


def test_missing_critic_response_is_not_agreement(db):
    cfg = _cfg(critic_shadow_model="")
    amazon(db)
    parser = ClaudeParser(cfg, client=_FakeClient(_CriticMessages(True)))
    parser.client.messages.parse = lambda **kw: object()
    stats = {}
    pipeline._run_critic(db, cfg, parser, NOW, audit_stats=stats)
    assert stats["agreement_by_reason"] == {}
    assert db.get_deal("widget").get("critic") is None


def test_enforce_rejection_cannot_be_disabled_by_legacy_publish_knob(db):
    cfg = _cfg(audit_mode="enforce", exclude_suspect_msrp=False)
    add(db, flags=["unit_mismatch"], critic={"plausible": True, "confidence": .9})
    pipeline._run_critic(db, cfg, None, NOW)
    assert not rank.is_publishable(db.get_deal("widget"), cfg)[0]


def test_existing_critic_reuse_is_not_counted_as_audit_savings(db):
    cfg = _cfg(audit_mode="enforce", critic_shadow_model="")
    amazon(db, "previous", critic={"plausible": True, "confidence": .9})
    amazon(db, "current")
    parser = ClaudeParser(cfg, client=_FakeClient(_CriticMessages(True)))
    stats = {}
    critic = pipeline._run_critic(db, cfg, parser, NOW, audit_stats=stats)
    assert critic["reused"] == 1 and critic["calls"] == 0
    assert stats["would_avoid_deals"] == stats["critic_calls_avoided"] == 0


def test_photo_work_does_not_invalidate_an_audit(db):
    cfg = _cfg(audit_mode="enforce")
    amazon(db)
    pipeline._run_critic(db, cfg, None, NOW)
    d = db.get_deal("widget")
    d["flags"].append("image_backfill_tried")
    assert rank.is_publishable(d, cfg)[0]


def test_delayed_enforcement_savings_are_reported_without_new_outcomes(db):
    cfg = _cfg(audit_mode="enforce", critic_shadow_model="")
    amazon(db)
    pipeline._run_critic(db, cfg, None, NOW)
    parser = ClaudeParser(cfg, client=_FakeClient(_CriticMessages(True)))
    pipeline._run_critic(db, cfg, parser, NOW + timedelta(days=1))
    data = legitimacy.daily_stats(db, cfg, NOW + timedelta(days=2))
    assert not sum(data["outcomes"].values())
    assert data["critic_calls_avoided"] == 1
    assert data["models"] == [cfg.critic_model]
