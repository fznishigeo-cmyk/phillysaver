"""Rule hits are transient observations and never change prices or mapper results."""

from contextlib import contextmanager

import pytest

from dealradar import accuracy_lite_counts, savings, util
from tests.test_savings_accuracy_lite import deal
from tests.test_keepa_renewed_title_lock import config, fetch
from tests.test_woot_condition_lock import cfg, db, state
from tests.test_woot import SRC, _adapter
from tests.test_woot_reprice import NOW, check, offer, stored


ZERO = dict(minimum_spend=0, subscribe_save_unknown=0, rewards_deferred=0,
            woot_condition_skips=0, renewed_title_lane=0)


@contextmanager
def recording():
    counts, token = accuracy_lite_counts.start()
    try:
        yield counts
    finally:
        accuracy_lite_counts.finish(token)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("changes,expected", [
    ({"regular_price": 20, "stack_steps": ["$5 off $25"]}, {"minimum_spend": 1}),
    ({"regular_price": 20, "final_price": 15, "stack_steps": ["$5 off $25"]},
     {"minimum_spend": 1}),
    ({"regular_price": 100, "stack_steps": ["Subscribe & Save $8.49", "S&S $3.99",
                                           "Subscribe & Save an extra $2", "unknown"]},
     {"subscribe_save_unknown": 2}),
    ({"regular_price": 100, "final_price": 50,
      "rebates": ["$10 Kohl's Cash", "$5 REWARDS", "$3 mail-in rebate", "$2 gift card"]},
     {"rewards_deferred": 2}),
])
def test_savings_rule_counts_preserve_results(enabled, changes, expected):
    candidate = deal(**changes)
    baseline = savings.compute_savings(candidate, accuracy_lite=enabled)
    with recording() as counts:
        assert savings.compute_savings(candidate, accuracy_lite=enabled) == baseline
    assert counts == (ZERO | expected if enabled else ZERO)


@pytest.mark.parametrize("enabled", [False, True])
def test_woot_recheck_and_intake_count_each_skip(db, cfg, monkeypatch, enabled):
    cfg.woot_condition_lock = enabled
    monkeypatch.setenv("WOOT_API_KEY", "offline-woot-key")
    stored(db, cfg, conditions=[], woot_condition="New", woot_offer_id="jeans")
    before = state(db)
    with recording() as counts:
        posts = _adapter({"Items": [offer(condition="Refurbished")]}).fetch_posts(
            SRC, None, db, cfg, NOW, None)
    assert counts == ZERO | {"woot_condition_skips": 2 if enabled else 0}
    if enabled:
        assert posts == [] and state(db) == before
    else:
        assert len(posts) == 1


@pytest.mark.parametrize("changed_url", [False, True])
@pytest.mark.parametrize("ended", [False, True])
def test_woot_expiry_is_not_a_condition_skip(db, cfg, changed_url, ended):
    stored(db, cfg, conditions=[], woot_condition="New", woot_offer_id="jeans")
    item = offer(condition="Refurbished")
    if changed_url:
        item["Url"] = "https://www.woot.com/offers/changed"
    if ended:
        item["EndDate"] = util.iso(NOW)
    with recording() as counts:
        check(db, cfg, [item])
    assert counts == ZERO | {"woot_condition_skips": 0 if ended else 1}


@pytest.mark.parametrize("enabled", [False, True])
def test_renewed_new_lane_counts_preserve_posts(monkeypatch, enabled):
    baseline = fetch("Widget (Renewed)", config(enabled, used=True), monkeypatch, warehouse=True)
    with recording() as counts:
        assert fetch("Widget (Renewed)", config(enabled, used=True), monkeypatch,
                     warehouse=True) == baseline
        assert len(fetch("Renewed Hope Cream", config(enabled), monkeypatch)) == 1
    assert counts == ZERO | {"renewed_title_lane": int(enabled)}


def test_counters_reset_and_restore_after_exception():
    with recording() as outer:
        accuracy_lite_counts.increment("minimum_spend")
        with pytest.raises(RuntimeError), recording() as inner:
            assert inner == ZERO
            accuracy_lite_counts.increment("rewards_deferred")
            raise RuntimeError("failed run")
        accuracy_lite_counts.increment("minimum_spend")
    accuracy_lite_counts.increment("minimum_spend")  # no active run
    assert outer == ZERO | {"minimum_spend": 2}
    assert inner == ZERO | {"rewards_deferred": 1}
    with recording() as next_run:
        assert next_run == ZERO
