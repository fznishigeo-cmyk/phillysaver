"""Cross-feature recheck regressions, using emitted JS and mocked transport."""

import json
import subprocess
from datetime import timedelta

import pytest

from dealradar import click_price, rank, util
from dealradar.publish import site
from tests.test_click_price_known import cfg, db, product, save, seed, NOW
from tests.test_buybox_top_picks import run, transport
from tests.test_value_per_token_rechecks import minutes


@pytest.mark.parametrize("planner", [False, True])
@pytest.mark.parametrize("same_arrival", [False, True])
def test_browser_visible_unknown_and_static_wall_both_get_buybox(
        db, cfg, monkeypatch, tmp_path, planner, same_arrival):
    cfg.keepa_volatility["enabled"] = planner
    cfg.keepa_recheck_top_n = cfg.keepa_buybox_top_n = 0
    cfg.keepa_recheck_top_live = False
    rows = []
    for i in range(422):
        seen = NOW - timedelta(minutes=120 if same_arrival and i >= 120 else 500 - i)
        row = seed(db, cfg, asin=f"B{i:09d}", seen=seen,
                   price_rechecked_at=util.iso(NOW - timedelta(minutes=5)))
        if i < 120:
            save(db, row, product(2500, asin=row["asin"], buyBoxPrice=2500, buyBoxShipping=0,
                                 lastBuyBoxUpdate=minutes(NOW - timedelta(minutes=5))))
        rows.append(row)
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    published = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    # Run the actual published default ordering. The newest Unknown is card 121,
    # while the server's static wall still includes the oldest Unknowns.
    script = """
const fs = require('node:fs'), vm = require('node:vm');
const home = fs.readFileSync(process.argv[1] + '/index.html', 'utf8');
const payload = JSON.parse(fs.readFileSync(process.argv[1] + '/deals.json', 'utf8'));
const ctx = {state:{meta:payload.meta, catTab:0, sortKey:'top', sortDir:-1, seed:42, newKeys:{}},
  document:{getElementById:()=>({value:''})}, JITTER_H:3, NO_PHOTO_H:12,
  CATGROUPS:[['Trending',null]], eligibilityActive:()=>false,
  isLocked:d=>!!d.locked, dealKey:d=>d.key, num:v=>v == null ? null : Number(v)};
vm.createContext(ctx);
vm.runInContext(home.slice(home.indexOf('  function cmp('), home.indexOf('  function storeHue(')), ctx);
vm.runInContext(home.slice(home.indexOf('  function frontPageView('), home.indexOf('  function render()')), ctx);
vm.runInContext(home.slice(home.indexOf('  function activeSpotlight('), home.indexOf('  function paintSpotlightTime(')), ctx);
console.log(JSON.stringify([42,43].map(seed=>{
  ctx.state.seed = seed;
  return Array.from(ctx.placeGone(ctx.ordered(payload.deals, true)), d=>d.dedupe_key);
})));
"""
    proc = subprocess.run(["node", "-e", script, str(tmp_path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    browsers = json.loads(proc.stdout)
    browser = browsers[0]
    if not same_arrival:
        assert browser[120] == rows[-1]["dedupe_key"]
    static = [d["dedupe_key"] for d in click_price.home_rows(
        rank.pick_order(published["public_deals"], cfg.front_page))[:300]]
    assert rows[120]["dedupe_key"] in static
    assert set(browser[:300]) - set(static)
    calls, _, _ = transport(monkeypatch, db, {d["asin"]: product(2500, asin=d["asin"]) for d in rows})
    run(db, cfg)
    premium = {a for c in calls if c.get("buybox") == "1" for a in c["asin"].split(",")}
    expected = set(static).union(*(b[:click_price.WALL_CHECKS] for b in browsers))
    assert {d["asin"] for d in rows if d["dedupe_key"] in expected} <= premium
    assert all(c.get("buybox") == "1" for c in calls if rows[-1]["asin"] in c["asin"].split(","))


@pytest.mark.parametrize("planner", [False, True])
def test_legacy_buybox_position_301_has_a_stable_fallback(db, cfg, monkeypatch, planner):
    cfg.keepa_volatility["enabled"] = planner
    cfg.keepa_buybox_top_n = cfg.keepa_recheck_wall_n = 301
    cfg.keepa_recheck_top_live = False
    rows = [seed(db, cfg, asin=f"B{i:09d}") for i in range(2)]
    wall = [rows[0]["dedupe_key"], *[f"other-{i}" for i in range(299)], rows[1]["dedupe_key"]]
    db.meta_set(site.WALL_META, json.dumps(wall))
    calls, _, _ = transport(monkeypatch, db, {d["asin"]: product(2500, asin=d["asin"]) for d in rows})
    result = run(db, cfg)
    assert [a for c in calls for a in c["asin"].split(",")] == [d["asin"] for d in rows]
    assert all(c.get("buybox") == "1" for c in calls)
    assert result["tokens"]["requested"] == 6


def test_sparse_unknowns_keep_static_recovery_scope(db, cfg, monkeypatch, tmp_path):
    cfg.keepa_recheck_top_n = cfg.keepa_buybox_top_n = 0
    cfg.keepa_recheck_top_live = False
    rows = [seed(db, cfg, asin=f"B{i:09d}") for i in range(3)]
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    published = site.render_site(db, cfg, NOW, out_dir=tmp_path, deals=rows)
    assert len(published["public_deals"]) == 3
    assert json.loads(db.meta_get(click_price.WALL_META)) == []
    assert set(json.loads(db.meta_get(click_price.VISIBLE_META))) == {d["dedupe_key"] for d in rows}
    records = {d["asin"]: product(2500, asin=d["asin"], buyBoxPrice=2500, buyBoxShipping=0,
        lastBuyBoxUpdate=minutes(NOW - timedelta(minutes=5))) for d in rows}
    calls, _, _ = transport(monkeypatch, db, records)
    result = run(db, cfg)
    assert result["tokens"]["requested"] == 9
    assert all(c.get("buybox") == "1" for c in calls)
    assert len(rank.site_deals(db, cfg, NOW)) == 3
    run(db, cfg)
    assert len(calls) == 1
