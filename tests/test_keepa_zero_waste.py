"""Operator 2026-10-22: "virtually 0 waste ... people visiting the site should still be able to search".

On the 20/min plan the bucket holds 1,200. The next-run reserve used to be
650 (sized for the 60/min plan), so with the 150 visitor floor every run with
a surplus stopped due rechecks at 800 and gave overflow /deal pages and the
focus finder nothing (hourly #313: tokens_short 27, overflow pages 0).
"""
from datetime import datetime, timedelta, timezone

from dealradar.config import load_config
from dealradar.fetch.keepa import DEAL_PAGE_COST
from dealradar.keepa_budget import MIN_FOR_RECHECK, KeepaBudget

NOW = datetime(2026, 10, 22, 4, 0, tzinfo=timezone.utc)
NEXT_RUN_WORST_CASE = 160 + 120 * 4  # discovery pages + top Buy Box reads (config comment)


def budget(tokens, cfg):
    return KeepaBudget(tokens_left=tokens, refill_rate=cfg.keepa_tokens_per_minute, seen_at=NOW,
                       visitor_reserve=cfg.keepa_visitor_reserve_tokens)


def test_shipped_reserves_hold_at_most_a_third_of_the_bucket_and_keep_the_visitor_floor():
    cfg = load_config()
    capacity = budget(0, cfg).capacity()
    assert cfg.keepa_tokens_per_minute == 20 and capacity == 1200
    assert cfg.keepa_visitor_reserve_tokens == 150
    assert cfg.keepa_recheck_surplus_reserve + cfg.keepa_visitor_reserve_tokens <= capacity / 3
    # The next run still gets its worst case: what we leave plus ~20 minutes of refill.
    assert cfg.keepa_recheck_surplus_reserve + 20 * cfg.keepa_tokens_per_minute >= NEXT_RUN_WORST_CASE


def test_at_800_tokens_due_rechecks_and_overflow_pages_may_spend_but_the_visitor_floor_holds():
    cfg = load_config()
    reserve = cfg.keepa_recheck_surplus_reserve
    at_800 = budget(800, cfg)
    assert at_800.allows(max(MIN_FOR_RECHECK, reserve + 1), NOW)       # a due recheck (was refused)
    assert at_800.allows(reserve + DEAL_PAGE_COST, NOW)                 # an overflow /deal page (was refused)
    floor = reserve + cfg.keepa_visitor_reserve_tokens
    assert budget(floor + 1, cfg).allows(reserve + 1, NOW)
    assert not budget(floor, cfg).allows(reserve + 1, NOW)              # never dips into visitors' 150


def test_overflow_is_what_would_be_lost_before_the_next_run():
    cfg = load_config()
    full = budget(1200, cfg)
    assert full.overflow(20, NOW) == 400                                 # 20 min at 20/min into a full bucket
    assert budget(400, cfg).overflow(20, NOW + timedelta(0)) == 0        # leaving ~400 wastes nothing
