"""2026-10-22: why requested Buy Box readings were not fresh.

Hourly #319 asked for 97 Buy Box readings and only 3 counted as fresh (fast
#806: 109/110). The run summary now counts the reason for every reading that
fresh_reading rejects; no decision changes.
"""
from datetime import timedelta

import pytest

from dealradar import buybox
from tests.test_buybox_top_picks import cfg, record, remember, run, transport  # noqa: F401
from tests.test_price_accuracy_engine import NOW, db, product, seed  # noqa: F401
from tests.test_value_per_token_rechecks import minutes


def test_reasons_follow_fresh_reading_in_order(db, cfg):
    deal = seed(db, cfg)
    ok = record(deal, 30, age=5)
    assert buybox.stale_reason(deal, ok, cfg, NOW) is None
    assert buybox.fresh_reading(deal, ok, cfg, NOW) is not None
    cases = {
        "no_buybox_price": product(2500, asin=deal["asin"]),
        "after_run_start": record(deal, 30, age=-3),
        "older_than_interval": record(deal, 30, age=cfg.keepa_buybox_interval_min + 1),
    }
    no_time = record(deal, 30, age=5)
    no_time["stats"].pop("lastBuyBoxUpdate", None)
    cases["no_buybox_time"] = no_time
    for reason, p in cases.items():
        assert buybox.stale_reason(deal, p, cfg, NOW) == reason
        assert buybox.fresh_reading(deal, p, cfg, NOW) is None
    confirmed = dict(deal, price_rechecked_at=(NOW - timedelta(minutes=2)).isoformat().replace("+00:00", "Z"))
    assert buybox.stale_reason(confirmed, ok, cfg, NOW) in {"older_than_last_confirm", "before_price_since"}
    used = dict(deal, conditions=["used"])
    assert buybox.stale_reason(used, ok, cfg, NOW) == "not_eligible"


@pytest.mark.parametrize("age,fresh", [(5, True), (-3, False)])
def test_run_summary_counts_stale_readings(db, cfg, monkeypatch, age, fresh):
    row = seed(db, cfg)
    remember(db, top=[row], wall=[row])
    transport(monkeypatch, db, {row["asin"]: record(row, 30, age=age)})
    result = run(db, cfg)
    assert result["buybox_top"]["requested"] == 1
    assert result["buybox_top"]["with_buybox"] == int(fresh)
    assert result["buybox_stale"] == ({} if fresh else {"after_run_start": 1})
