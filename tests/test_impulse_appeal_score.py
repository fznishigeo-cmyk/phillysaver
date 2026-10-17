"""Task 282: private, capped click ratings and shared server/browser spacing."""

import copy
import json
import os
import re
import socket
import subprocess
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from dealradar import appeal, appeal_score, rank
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.parse.claude_client import ClaudeParser
from dealradar.publish import seo, site
from dealradar.util import iso
from tests.test_appeal import tree
from tests.test_front_page import browser, pick_deal
from tests.test_publish_site import NOW


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("task282 must remain offline")
    monkeypatch.setattr(httpx.Client, "send", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr("dealradar.util.now_utc", lambda: NOW)
    monkeypatch.setattr(site, "util_now", lambda: NOW)


@pytest.fixture
def database(tmp_path):
    db = DB(tmp_path / "state.db")
    yield db
    db.close()


def settings():
    cfg = load_config()
    cfg.tracking = {}
    cfg.spend_pacing = False
    cfg.front_page["popular_affordable"].update(enabled=False, shadow=False)
    return cfg


def deal(i=0, **over):
    return pick_deal(**dict(dedupe_key=f"d{i}", item=f"Widget {i} deal", store=f"Store{i % 8}",
                            category=("home", "kitchen", "electronics", "beauty")[i % 4],
                            brand=f"Brand{i}", final_price=19.97, regular_price=80,
                            first_seen_at=iso(NOW), price_rechecked_at=iso(NOW)) | over)


class Messages:
    def __init__(self, reply=None, usage=(100, 20), error=None, stop="end_turn"):
        self.calls, self.reply, self.usage, self.error, self.stop = [], reply, usage, error, stop

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        entries = json.loads(kwargs["messages"][0]["content"])
        result = self.reply(entries) if callable(self.reply) else self.reply
        if result is None:
            result = [{"key": e["key"], "score": 4} for e in entries]
        raw = result if isinstance(result, str) else json.dumps(result)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=raw)], stop_reason=self.stop,
                               usage=SimpleNamespace(input_tokens=self.usage[0], output_tokens=self.usage[1]))


def parser_for(cfg, messages=None):
    messages = messages or Messages()
    return ClaudeParser(cfg, client=SimpleNamespace(messages=messages)), messages


def candidates(n=1):
    return [(deal(i), 100 - i) for i in range(n)]


def db_state(db):
    return {r[0]: sorted([tuple(row) for row in db.conn.execute('SELECT * FROM "' + r[0] + '"')], key=repr)
            for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_shipped_on_defaults_and_missing_is_off(tmp_path):
    cfg = load_config()
    assert cfg.appeal_score == dict(enabled=True, model=cfg.parse_model, candidates=120, ttl_h=72,
                                    max_usd_per_run=.03, max_usd_per_day=.40, factors=appeal_score.FACTORS)
    assert appeal.allure_spacing(cfg.front_page["appeal"]) == (2, 4)
    path = tmp_path / "empty.yaml"
    path.write_text("{}")
    assert not load_config(path).appeal_score["enabled"] and not AppConfig().appeal_score
    assert appeal.allure_spacing({}) == (6, 6)


@pytest.mark.parametrize("key,value", [
    ("enabled", 1), ("model", ""), ("model", "bad model"), ("candidates", -1), ("candidates", True),
    ("candidates", 1.5), ("ttl_h", -1), ("ttl_h", float("inf")), ("max_usd_per_run", -1),
    ("max_usd_per_day", float("nan")), ("max_usd_per_run", True), ("factors", {1: .6}),
    ("factors", {**appeal_score.FACTORS, 5: 0}), ("factors", {**appeal_score.FACTORS, 5: float("inf")}),
    ("unknown", True),
])
def test_invalid_rating_config(tmp_path, key, value):
    import yaml
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"appeal_score": {key: value}}))
    with pytest.raises(ValueError, match="appeal_score"):
        load_config(path)


@pytest.mark.parametrize("key", ["allure_first", "allure_every"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "3", None])
def test_invalid_spacing_config(tmp_path, key, value):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"publish": {"front_page": {"appeal": {key: value}}}}))
    with pytest.raises(ValueError, match=key):
        load_config(path)


def test_batches_only_best_80_and_actual_model_ledger(database):
    cfg = settings()
    cfg.appeal_score = dict(cfg.appeal_score, candidates=80)  # batching fixture written for 80 (shipped 120 since 10-17)
    cfg.parse_model = cfg.critic_model  # appeal is still billed at the cheaper configured model
    parser, messages = parser_for(cfg)
    rows = list(reversed(candidates(90)))
    ratings = appeal_score.ratings(database, cfg, NOW, rows, parser=parser, run_key="batches")
    assert set(ratings) == {f"d{i}" for i in range(80)}
    assert len(messages.calls) == 4
    for call in messages.calls:
        entries = json.loads(call["messages"][0]["content"])
        assert len(entries) == 20
        assert all(set(e) == {"key", "item", "store", "final_price", "regular_price", "pct_at_checkout", "category"}
                   for e in entries)
        assert call["model"] == cfg.appeal_score["model"] != parser.model
    rate = cfg.model_pricing_per_mtok[cfg.appeal_score["model"]]
    expected = 4 * (100 * rate["input"] + 20 * rate["output"]) / 1e6
    assert parser.spend_by_kind["appeal_score"] == dict(calls=4, usd=pytest.approx(expected))
    assert database.kind_spend_usd_for_run("appeal_score", "batches") == pytest.approx(expected)
    assert {r[0] for r in database.conn.execute("SELECT model FROM llm_calls")} == {cfg.appeal_score["model"]}


def test_cache_price_change_ttl_and_rerun(database):
    cfg = settings()
    parser, messages = parser_for(cfg)
    rows = candidates()
    assert appeal_score.ratings(database, cfg, NOW, rows, parser=parser, run_key="one") == {"d0": 4}
    before = db_state(database)
    assert appeal_score.ratings(database, cfg, NOW, rows, parser=parser, run_key="one") == {"d0": 4}
    assert db_state(database) == before and len(messages.calls) == 1
    assert appeal_score.ratings(database, cfg, NOW + timedelta(hours=71), rows, parser=parser, run_key="hit") == {"d0": 4}
    assert len(messages.calls) == 1
    changed = [(dict(rows[0][0], final_price=19.96), 100)]
    assert appeal_score.ratings(database, cfg, NOW, changed, parser=parser, run_key="price") == {"d0": 4}
    assert len(messages.calls) == 2
    assert database.conn.execute("SELECT COUNT(*) FROM appeal_scores").fetchone()[0] == 2
    assert appeal_score.ratings(database, cfg, NOW + timedelta(hours=72), rows, parser=parser, run_key="expired") == {"d0": 4}
    assert len(messages.calls) == 3
    # Even a changed price on the same run key cannot create another paid call or row.
    before = db_state(database)
    assert appeal_score.ratings(database, cfg, NOW, [(deal(final_price=1), 100)], parser=parser, run_key="one") == {}
    assert len(messages.calls) == 3 and db_state(database) == before


@pytest.mark.parametrize("cap", ["own_run", "own_day", "global_run", "global_day", "paced"])
def test_caps_stop_calls_and_same_run_does_not_retry(database, cap):
    cfg = settings()
    if cap == "own_run":
        cfg.appeal_score["max_usd_per_run"] = 0
    elif cap == "own_day":
        cfg.appeal_score["max_usd_per_day"] = 0
    elif cap == "global_run":
        cfg.spend_cap_usd_per_run = 0
    elif cap == "global_day":
        cfg.spend_cap_usd_per_day = cfg.spend_cap_boost_usd_per_day = 0
    else:
        cfg.spend_cap_usd_per_day = .00025
        cfg.spend_cap_boost_usd_per_day = 0
        cfg.spend_pacing = True
    parser, messages = parser_for(cfg)
    assert appeal_score.ratings(database, cfg, NOW, candidates(), parser=parser, run_key=cap) == {}
    before = db_state(database)
    assert appeal_score.ratings(database, cfg, NOW, candidates(), parser=parser, run_key=cap) == {}
    assert not messages.calls and db_state(database) == before


def test_caps_include_other_parser_instances_and_previous_runs(database):
    cfg = settings()
    first, messages = parser_for(cfg, Messages(usage=(10000, 1000)))
    appeal_score.ratings(database, cfg, NOW, candidates(), parser=first, run_key="earlier")
    spent = database.kind_spend_usd_for_day("appeal_score", NOW)
    assert spent > 0
    cfg.appeal_score["max_usd_per_day"] = spent
    second, more = parser_for(cfg)
    assert appeal_score.ratings(database, cfg, NOW, [(deal(1), 100)], parser=second, run_key="later") == {}
    assert len(messages.calls) == 1 and not more.calls


def test_cap_stops_between_batches_and_unrated_candidates_keep_factor_one(database, monkeypatch):
    cfg = settings()
    parser, messages = parser_for(cfg, Messages(usage=(200000, 0)))
    monkeypatch.setattr(parser, "_cost_bound", lambda kwargs: .02)
    ratings = appeal_score.ratings(database, cfg, NOW, candidates(40), parser=parser, run_key="partial")
    assert len(messages.calls) == 1 and ratings == {f"d{i}": 4 for i in range(20)}
    assert database.kind_spend_usd_for_run("appeal_score", "partial") == pytest.approx(.02)
    assert appeal_score.factor(deal(39), cfg) == 1.0


@pytest.mark.parametrize("scope", ["run", "day"])
def test_other_llm_kinds_consume_the_same_global_caps(database, scope):
    cfg = settings()
    database.reserve_llm_call("extract", "shared", cfg.parse_model, .5, NOW,
                              {NOW.date().isoformat(): 1}, 1, kind="extract")
    if scope == "day":
        cfg.spend_cap_usd_per_day = cfg.spend_cap_boost_usd_per_day = .5
    parser, messages = parser_for(cfg)
    run_key = "shared" if scope == "run" else "another"
    assert appeal_score.ratings(database, cfg, NOW, candidates(), parser=parser, run_key=run_key) == {}
    assert not messages.calls


@pytest.mark.parametrize("reply", [
    "not JSON PRIVATE_REASON", {"key": "0", "score": 5}, [{"key": "0", "score": True}],
    [{"key": "0", "score": 0}], [{"key": "0", "score": 6}], [{"key": "0", "score": 4.0}],
    [{"key": "0", "score": "4"}], [{"key": "0", "score": 4, "reason": "PRIVATE_REASON"}],
    [{"key": "0", "score": 4}, {"key": "0", "score": 5}], [{"key": [], "score": 4}],
    [{"key": "unknown", "score": 5}],
])
def test_bad_replies_are_unrated_and_reasons_never_survive(database, caplog, reply):
    cfg = settings()
    parser, messages = parser_for(cfg, Messages(reply))
    assert appeal_score.ratings(database, cfg, NOW, candidates(), parser=parser, run_key="bad") == {}
    before = db_state(database)
    assert appeal_score.ratings(database, cfg, NOW, candidates(), parser=parser, run_key="bad") == {}
    assert len(messages.calls) == 1 and db_state(database) == before
    assert database.conn.execute("SELECT COUNT(*) FROM appeal_scores").fetchone()[0] == 0
    assert "PRIVATE_REASON" not in caplog.text and "PRIVATE_REASON" not in str(before)


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_incomplete_reply_is_billed_without_caching(database, stop):
    cfg = settings()
    parser, _ = parser_for(cfg, Messages(stop=stop))
    assert appeal_score.ratings(database, cfg, NOW, candidates(), parser=parser, run_key=stop) == {}
    assert database.kind_spend_usd_for_run("appeal_score", stop) > 0


def test_api_errors_log_type_only_and_are_not_retried(database, caplog):
    cfg = settings()
    error = anthropic.APIConnectionError(message="PRIVATE_REASON score=1", request=httpx.Request("POST", "https://api.example"))
    parser, messages = parser_for(cfg, Messages(error=error))
    assert appeal_score.ratings(database, cfg, NOW, candidates(), parser=parser, run_key="error") == {}
    assert len(messages.calls) == 1 and database.kind_spend_usd_for_run("appeal_score", "error") > 0
    assert "APIConnectionError" in caplog.text and "PRIVATE_REASON" not in caplog.text and "score=1" not in caplog.text


@pytest.mark.parametrize("rating,factor", list(appeal_score.FACTORS.items()) + [(None, 1)])
def test_rating_factors_and_shared_ceiling(rating, factor):
    cfg = settings()
    cfg.front_page["appeal"]["want"]["enabled"] = False
    cfg.front_page["appeal"]["season"] = []
    d = deal(_appeal_score=rating)
    baseline = rank.pick_score(deal(), cfg, NOW)
    assert rank.pick_score(d, cfg, NOW) == pytest.approx(baseline * factor)
    cfg.front_page["appeal"]["want"]["enabled"] = True
    d["item"] = "Wireless charger"
    lifted = rank.pick_score(dict(d, _appeal_score=None), cfg, NOW)
    expected = lifted * factor if factor < 1 else lifted
    assert rank.pick_score(d, cfg, NOW) == pytest.approx(expected)
    # Adult exclusions receive neither the model lift nor the title lift.
    adult = dict(d, item="Adult sex toy", _appeal_score=5)
    assert rank.pick_score(adult, cfg, NOW) == rank.pick_score(dict(adult, _appeal_score=None), cfg, NOW)


def test_popularity_quota_uses_capped_marginal_with_ratings():
    cfg = settings()
    cfg.front_page["popular_affordable"].update(enabled=True, min_monthly_sold=500, weight=1.4)
    for rating in (1, 2, 3, 4, 5, None):
        d = deal(store="Amazon", asin="B000000001", canonical_url="https://amazon.com/dp/B000000001",
                 _appeal_score=rating, click_price_check=dict(asin="B000000001", final=19.97, buybox=19.97, at=iso(NOW)),
                 keepa_popularity=dict(asin="B000000001", monthly_sold=600, sold_at=iso(NOW)))
        assert rank.pick_popularity_factor(d, cfg, NOW) == pytest.approx(
            rank.pick_score(d, cfg, NOW) / rank.pick_score(d, cfg, NOW, popularity=False))


@pytest.mark.parametrize("gadget_rating", [4, 5])
def test_shaker_stays_on_wall_gadget_moves_up_and_public_scores_are_unchanged(database, tmp_path, caplog, gadget_rating):
    cfg = settings()
    cfg.front_page["appeal"]["want"]["enabled"] = False
    cfg.front_page["appeal"]["season"] = []
    cfg.front_page["top_picks"].update(max_same_store=120, max_same_category=120, max_same_brand=120,
                                        max_title_twins=120, max_clothing_per_window=0)
    rows = [deal(i) for i in range(40)]
    rows[0].update(item="4-piece plastic shaker set", final_price=19.97)
    rows[39].update(item="Clicky gadget", pct_at_checkout=79)
    baseline = copy.deepcopy(rows)
    cfg.appeal_score["enabled"] = False
    site.render_site(database, cfg, NOW, out_dir=tmp_path / "before", deals=rows)
    before = json.loads((tmp_path / "before/deals.json").read_text())
    cfg.appeal_score["enabled"] = True
    def reply(entries):
        return [{"key": e["key"], "score": 1 if "shaker" in e["item"] else gadget_rating if "gadget" in e["item"] else 3}
                for e in entries]
    parser, messages = parser_for(cfg, Messages(reply))
    first = site.render_site(database, cfg, NOW, out_dir=tmp_path / "after", deals=rows,
                             last_run={"run_key": "publish"}, ai_parser=parser)
    doc = json.loads((tmp_path / "after/deals.json").read_text())
    picks = sorted((d for d in doc["deals"] if d.get("pick")), key=lambda d: d["pick"])
    assert picks[0]["dedupe_key"] == "d39" and all(d["dedupe_key"] != "d0" for d in picks[:24])
    assert {d["dedupe_key"] for d in doc["deals"]} == {d["dedupe_key"] for d in rows}
    assert [d["dedupe_key"] for d in picks[1:]] == [f"d{i}" for i in range(1, 39)]
    assert {d["key"]: d.get("ps") for d in doc["deals"]} == {d["key"]: d.get("ps") for d in before["deals"]}
    assert rows == baseline
    for call in messages.calls:
        for entry in json.loads(call["messages"][0]["content"]):
            public = next(d for d in doc["deals"] if d["item"] == entry["item"])
            assert entry["regular_price"] == public.get("regular_price")
            assert entry["pct_at_checkout"] == public.get("pct_at_checkout")
    before_state, before_tree = db_state(database), tree(tmp_path / "after")
    second = site.render_site(database, cfg, NOW, out_dir=tmp_path / "after", deals=rows,
                              last_run={"run_key": "publish"}, ai_parser=parser)
    assert db_state(database) == before_state and tree(tmp_path / "after") == before_tree
    assert first["full_payload"] == second["full_payload"] and len(messages.calls) == 2
    for data in list(before_tree.values()) + [json.dumps(first["full_payload"]).encode(), caplog.text.encode()]:
        assert b"appeal_score" not in data and b'"rated_at"' not in data


@pytest.mark.parametrize("mode", ["membership", "open", "support", "shipped"])
def test_false_and_missing_preserve_all_public_member_bytes_and_calls(database, tmp_path, mode):
    cfg = settings()
    cfg.support_enabled = mode in {"support", "shipped"}
    cfg.paywall_open_access = mode in {"open", "shipped"}
    cfg.paywall_teaser_count = 2
    rows = [deal(i, item="Satin perfume" if i % 3 == 0 else f"Widget {i}") for i in range(12)]
    database.conn.execute("INSERT INTO appeal_scores VALUES (?,?,?,?)", ("d0", 19.97, 1, iso(NOW)))
    database.conn.commit()
    cfg.appeal_score["enabled"] = False
    parser, messages = parser_for(cfg)
    first = site.render_site(database, cfg, NOW, out_dir=tmp_path / "site", deals=rows, ai_parser=parser)
    before = tree(tmp_path / "site")
    cfg.appeal_score = {}
    database.meta_set("teaser_recent", "[]")
    second = site.render_site(database, cfg, NOW, out_dir=tmp_path / "site", deals=rows, ai_parser=parser)
    assert before == tree(tmp_path / "site") and first["full_payload"] == second["full_payload"]
    assert not messages.calls and database.conn.execute("SELECT COUNT(*) FROM appeal_scores").fetchone()[0] == 1


def test_existing_adult_exclusions_stay_out_of_first_screens(database, tmp_path):
    cfg = settings()
    adult = deal(99, item="Adult sex toy perfume", category="beauty", pct_at_checkout=99,
                 _appeal_score=5)
    rows = [adult] + [deal(i) for i in range(40)]
    parser, messages = parser_for(cfg)
    result = site.render_site(database, cfg, NOW, out_dir=tmp_path / 'site', deals=rows,
                              ai_parser=parser, last_run={'run_key': 'adult-exclusions'})
    public = result['full_payload']['deals']
    assert any(d['dedupe_key'] == adult['dedupe_key'] for d in public)
    assert all(d['dedupe_key'] != adult['dedupe_key'] for d in public if d.get('pick'))
    for call in messages.calls:
        assert adult['item'] not in call['messages'][0]['content']
    assert adult['dedupe_key'] not in [d['dedupe_key'] for d in seo.home_order(public, cfg.front_page)[:24]]
    scored = [(adult, 999)] + [(d, 100) for d in rows[1:]]
    assert adult not in rank.top_picks(scored, cfg.front_page, NOW, cfg=cfg)


@pytest.mark.parametrize("spacing", [(3, 4), (None, None), (1, 1), (1, 4), (3, 1)])
@pytest.mark.parametrize("gone_allure", [False, True])
def test_allure_server_and_actual_js_agree(database, tmp_path, spacing, gone_allure):
    cfg = settings()
    cfg.support_enabled = False
    cfg.engagement = {}
    cfg.drop_rotation_windows = 0
    options = cfg.front_page["appeal"]
    first, every = spacing
    if first is None:
        options.pop("allure_first")
        options.pop("allure_every")
        first, every = 7, 6
    else:
        options.update(allure_first=first, allure_every=every)
    rows = [dict(deal(i, item="Satin perfume"), key=f"a{i}", allure=True) for i in range(8)]
    rows += [dict(deal(i + 100), key=f"n{i}") for i in range(40)]
    rows += [dict(deal(i + 200), key=f"gone{i}", status="dead", allure=gone_allure) for i in range(4)]
    for i, row in enumerate(rows):
        row["pick"] = i + 1
    expected = [d["key"] for d in seo.home_order(rows, cfg.front_page)]
    results = browser(tmp_path, cfg, database, [dict(rows=rows, options=cfg.front_page,
                      state={"catTab": tab, "sortKey": "top", "seed": seed})
                      for tab in (0, 1) for seed in (0, 1, 2)])
    # Trending retains pick order; All deals intentionally uses seeded arrival
    # order. Both still use the exact same spacing function on that order.
    for result in results[:3]:
        assert result["rendered"] == expected
    emitted = (tmp_path / "site/index.html").read_text()
    block = emitted[emitted.index("  function cmp("):emitted.index("  function storeHue(")]
    block += emitted[emitted.index("  function placeGone("):emitted.index("  function render(")]
    js = "const vm=require('node:vm'),fs=require('node:fs');const data=JSON.parse(fs.readFileSync(0,'utf8'));" \
         "const ctx={};vm.runInNewContext(data.code,ctx);process.stdout.write(JSON.stringify(ctx.spaceAllure(data.rows).map(d=>d.key)));"
    result = subprocess.run(["node", "-e", js], input=json.dumps(dict(code=block, rows=rows)),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == expected
    positions = [i for i, key in enumerate(expected) if key.startswith("a") or gone_allure and key.startswith("gone")]
    assert positions[0] == first - 1 and all(b - a >= every for a, b in zip(positions, positions[1:]))
    gone = [i for i, key in enumerate(expected) if key.startswith("gone")]
    assert all(i >= 8 for i in gone) and all(b - a >= 8 for a, b in zip(gone, gone[1:]))
    for result in results:
        positions = [i for i, key in enumerate(result["rendered"])
                     if key.startswith("a") or gone_allure and key.startswith("gone")]
        assert all(pos >= first - 1 for pos in positions)
        assert all(b - a >= every for a, b in zip(positions, positions[1:]))
    if spacing == (None, None):
        explicit = copy.deepcopy(options) | dict(allure_first=7, allure_every=6)
        raw = (site.TEMPLATES_DIR / "index.html").read_text()
        assert appeal.template(raw, options) == appeal.template(raw, explicit)
        assert appeal.space_allure(rows, options) == appeal.space_allure(rows, explicit)


@pytest.mark.parametrize("spacing", [(3, 4), (7, 6)])
def test_rotating_picks_and_promo_repair_obey_the_configured_allure_limits(database, tmp_path, spacing):
    cfg = settings()
    cfg.front_page["appeal"].update(allure_first=spacing[0], allure_every=spacing[1])
    cfg.support_enabled = False
    cfg.engagement = {}
    rows = [dict(deal(i), key=str(i), pick=i + 1, pick_checkout=i % 5 == 0,
                 allure=i % 9 == 0) for i in range(120)]
    results = browser(tmp_path, cfg, database, [dict(rows=rows, options=cfg.front_page,
                      state={"catTab": 0, "sortKey": "top", "seed": seed}) for seed in range(6)])
    for result in results:
        keys = result["rendered"]
        positions = [i for i, key in enumerate(keys) if rows[int(key)]["allure"]]
        assert positions and positions[0] >= spacing[0] - 1
        assert all(b - a >= spacing[1] for a, b in zip(positions, positions[1:]))
        assert len(set(keys)) == len(keys)
        for start in range(len(keys)):
            assert sum(rows[int(key)]["pick_checkout"] for key in keys[start:start + 5]) <= 1


def test_real_chromium_smoke(database, tmp_path):
    cfg = settings()
    cfg.join_prompt = False
    cfg.manifest_refresh["enabled"] = False
    cfg.drop_rotation_windows = 0  # exact server/browser ordering; separate tests cover rotation
    rows = [deal(i, item="Satin perfume" if i < 8 else f"Widget {i}") for i in range(48)]
    for key in ("max_same_category", "max_same_store", "max_same_brand", "max_title_twins"):
        cfg.front_page["top_picks"][key] = 120
    site.render_site(database, cfg, NOW, out_dir=tmp_path / "site", deals=rows)
    doc = json.loads((tmp_path / "site/deals.json").read_text())
    (tmp_path / "site/expected.json").write_text(json.dumps(
        [d["key"] for d in seo.home_order(doc["deals"], cfg.front_page, click_price=True)]))
    result = subprocess.run(["node", str(Path(__file__).with_name("impulse_appeal_browser.cjs")), str(tmp_path / "site")],
                            capture_output=True, text=True, timeout=55, env={**os.environ, "TMPDIR": str(tmp_path)})
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
