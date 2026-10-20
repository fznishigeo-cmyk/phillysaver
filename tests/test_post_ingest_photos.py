"""Photo recovery after Keepa's fetch-time recheck; all requests are offline."""

import hashlib
import json
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from dealradar import photo_recovery as photos, pipeline, util, verify
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.errors import DeadlineExceeded
from dealradar.http_client import PoliteFetcher
from dealradar.keepa_budget import KeepaBudget
from tests import test_e2e_offline as e2e
from tests.history_pins import pre_hide_deal_site_links_raw, pre_keepa_focus_finder_raw
from tests.test_e2e_offline import env
from tests.test_photo_identity import source_post
from tests.test_photos_every_deal import A, B, PIC, seed


def snapshot(path):
    return {str(p.relative_to(path)): p.read_bytes()
            for p in path.rglob("*") if p.is_file()}


def test_flag_defaults_and_shipped_value(tmp_path):
    assert AppConfig().images_post_ingest_keepa is False
    assert load_config().images_post_ingest_keepa is True
    path = tmp_path / "config.yaml"
    for raw in ({}, {"images": {}}, {"images": {"post_ingest_keepa": False}}):
        path.write_text(yaml.safe_dump(raw))
        assert load_config(path).images_post_ingest_keepa is False


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false", [], {}])
def test_flag_requires_a_real_boolean(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"images": {"post_ingest_keepa": value}}))
    with pytest.raises(ValueError, match=r"images.post_ingest_keepa must be a boolean"):
        load_config(path)


@pytest.fixture
def recovery(monkeypatch):
    monkeypatch.setenv("KEEPA_KEY", "offline")
    monkeypatch.setattr(util, "now_utc", lambda: e2e.NOW)
    with closing(DB(":memory:")) as db:
        cfg = AppConfig(images_post_ingest_keepa=True, images_asin_backfill_per_run=2,
                        keepa_reserve_tokens=60, keepa_recheck_surplus_reserve=60,
                        keepa_visitor_reserve_tokens=150, amazon_used_enabled=False)
        cfg.feature_flags["keepa"] = True
        KeepaBudget(1000, 0, e2e.NOW).save(db)
        calls, responses = [], []

        def handler(request):
            assert request.url.host == "api.keepa.com" and request.url.path == "/product"
            params = dict(request.url.params)
            calls.append(params)
            if responses:
                return responses.pop(0)
            asins = params["asin"].split(",")
            return httpx.Response(200, json={"tokensLeft": KeepaBudget.load(db).available(),
                "refillRate": 0, "tokensConsumed": len(asins), "products": [
                    {"asin": asin, "images": [{"l": "product.jpg"}]} for asin in asins]})

        with closing(PoliteFetcher(db, cfg, transport=httpx.MockTransport(handler),
                                  sleep_fn=lambda _: None)) as fetcher:
            def run(**kwargs):
                return photos.backfill_keepa_images(db, db.all_deals(), fetcher, cfg,
                    kwargs.pop("deadline", None), limit=kwargs.pop("limit", 300), **kwargs)
            run.fetcher = fetcher
            yield db, cfg, run, calls, responses


def test_shared_asin_uses_one_slot_and_only_photo_facts_change(recovery):
    db, cfg, run, calls, _ = recovery
    cfg.images_asin_backfill_per_run = 1
    first = seed(db, "first", asin=A, canonical_url="https://slickdeals.net/f/123",
                 codes=["SAVE"], conditions=["subscribe_save"])
    second = seed(db, "second", asin=A, canonical_url="https://www.dealsofamerica.com/123.htm")
    before = {d["dedupe_key"]: d for d in db.all_deals()}
    result = run()
    assert result == dict(requested=1, found=2, tokens=1, rate_limited=False)
    assert [c["asin"] for c in calls] == [A]
    assert calls[0]["history"] == "0" and calls[0]["stats"] == "1"
    assert not {"update", "offers", "buybox"}.intersection(calls[0])
    for key in (first["dedupe_key"], second["dedupe_key"]):
        row = db.get_deal(key)
        assert row["image_url"] == PIC
        # The seed includes a derived rank score, outside stored deal facts.
        assert {k: v for k, v in row.items() if k not in
                ("score", "image_url", "photo_lookup_asin", "photo_lookup_found_none")} == {
                k: v for k, v in before[key].items() if k not in
                ("score", "image_url", "photo_lookup_asin", "photo_lookup_found_none")}
    state = list(db.conn.iterdump())
    assert run()["requested"] == 0 and len(calls) == 1
    assert list(db.conn.iterdump()) == state


@pytest.mark.parametrize("tokens,requested", [(None, 0), (210, 0), (211, 1), (212, 2)])
def test_unknown_bucket_and_visitor_plus_photo_reserve(recovery, tokens, requested):
    db, cfg, run, calls, _ = recovery
    seed(db, "one", asin=A)
    seed(db, "two", asin=B)
    KeepaBudget(tokens, 0, e2e.NOW).save(db)
    result = run()
    assert result["requested"] == result["found"] == requested
    assert sum(len(c["asin"].split(",")) for c in calls) == requested
    if requested:
        assert KeepaBudget.load(db).available() == 210  # 60 lane + 150 visitor tokens remain.


@pytest.mark.parametrize("reason", ["off", "feature_off", "no_key", "deadline", "zero_cap",
                                   "zero_remaining", "rate_limited"])
def test_deferred_work_does_not_touch_state_or_make_requests(recovery, monkeypatch, reason):
    db, cfg, run, calls, _ = recovery
    seed(db, asin=A)
    options = {}
    if reason == "off":
        cfg.images_post_ingest_keepa = False
    elif reason == "feature_off":
        cfg.feature_flags["keepa"] = False
    elif reason == "no_key":
        monkeypatch.delenv("KEEPA_KEY")
    elif reason == "deadline":
        options["deadline"] = 0
    elif reason == "zero_cap":
        cfg.images_asin_backfill_per_run = 0
    elif reason == "zero_remaining":
        options["limit"] = 0
    else:
        options["rate_limited"] = True
    state = list(db.conn.iterdump())
    assert run(**options)["requested"] == 0
    assert calls == [] and list(db.conn.iterdump()) == state


def test_missing_identity_ineligible_and_known_empty_lookup_keep_their_behaviour(recovery):
    db, cfg, run, calls, _ = recovery
    for key, changes in (("no_asin", {}), ("bad_asin", dict(asin="junk")),
                         ("other_store", dict(asin=A, store="Under Armour")),
                         ("used", dict(asin=A, conditions=["used"])),
                         ("photo", dict(asin=A, image_url=PIC)),
                         ("no_photo_found", dict(asin=A, photo_lookup_asin=A,
                                                 photo_lookup_found_none=True))):
        seed(db, key, **changes)
    state = list(db.conn.iterdump())
    assert run()["requested"] == 0 and calls == []
    assert list(db.conn.iterdump()) == state


@pytest.mark.parametrize("products", [[], [{"asin": A, "images": []}],
                                      [{"asin": A, "images": [{"l": "../bad.jpg"}]}]])
def test_answer_without_photo_stays_photoless_without_repeated_optional_lookup(recovery, products):
    db, _, run, calls, responses = recovery
    seed(db, asin=A)
    responses.append(httpx.Response(200, json={"products": products}))
    assert run()["found"] == 0
    row = db.get_deal("a")
    assert not row["image_url"] and row["photo_lookup_found_none"]
    assert row["photo_lookup_asin"] == A
    assert run()["requested"] == 0 and len(calls) == 1


@pytest.mark.parametrize("status", [429, 503])
def test_api_failure_leaves_deal_retryable_and_stops_the_pass(recovery, status):
    db, cfg, run, calls, responses = recovery
    seed(db, asin=A)
    responses.append(httpx.Response(status, json={"tokensLeft": 0},
        request=httpx.Request("GET", "https://api.keepa.com/product")))
    before = db.get_deal("a")
    result = run()
    assert result["rate_limited"] is (status == 429)
    assert result["found"] == 0 and len(calls) == 1
    assert db.get_deal("a") == before
    KeepaBudget(1000, 0, e2e.NOW).save(db)
    assert run()["found"] == 1


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("old_asin", [A, A.lower()])
def test_fetch_time_read_is_not_bought_again_for_the_same_asin(recovery, failed, old_asin):
    db, cfg, run, calls, responses = recovery
    seed(db, "old", asin=old_asin)
    if failed:
        responses.append(httpx.Response(503))
    # Uses the same fetcher as the subsequent catch-up pass.
    pipeline._recheck_prices(db, cfg, e2e.NOW, fetcher=run.fetcher)
    assert len(calls) == 1
    seed(db, "new", asin=A)  # a newly ingested post can share the old identity
    assert run()["requested"] == 0
    assert len(calls) == 1


def test_empty_photo_marker_does_not_block_later_price_recheck(recovery):
    db, cfg, run, calls, responses = recovery
    seed(db, asin=A)
    responses.append(httpx.Response(200, json={"products": [], "tokensConsumed": 1}))
    assert run()["requested"] == 1
    assert db.get_deal("a")["photo_lookup_found_none"]
    pipeline._recheck_prices(db, cfg, e2e.NOW, fetcher=run.fetcher)
    assert len(calls) == 2
    row = db.get_deal("a")
    assert row["image_url"] == PIC and not row["photo_lookup_found_none"]


@pytest.mark.parametrize("sent", [False, True])
def test_deadline_exception_saves_only_tokens_that_may_have_been_spent(recovery, monkeypatch, sent):
    db, _, run, calls, _ = recovery
    seed(db, asin=A)

    def timeout(*args, **kwargs):
        raise DeadlineExceeded("photo deadline", request_sent=sent)

    monkeypatch.setattr(run.fetcher, "api_get", timeout)
    assert run()["tokens"] == int(sent)
    assert KeepaBudget.load(db).available() == 1000 - int(sent)
    assert not calls and not db.get_deal("a").get("photo_lookup_found_none")


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("fast", [False, True])
def test_new_post_identity_gets_photo_after_fetch_time_recheck(env, monkeypatch, enabled, fast):
    path = Path(env["cfg"])
    raw = yaml.safe_load(path.read_text())
    raw["images"]["post_ingest_keepa"] = enabled
    raw["publish"]["site_show_from_pct"] = 50
    # The OFF digest was recorded with the tool pages in preview and before task 330.
    raw["tool_pages"] = {"enabled": True, "public": False}
    raw["phone_timer_raise"] = False
    raw["tool_pages_hero"] = False
    raw["search_buttons_1019"] = False
    raw["tool_pages_1020"] = False
    raw["fresh_more_plain"] = False
    raw["search_discounts_button"] = False
    raw["card_click_1020"] = False
    raw["buttons_to_tools"] = False
    raw["amazon_price_cap"] = False
    raw["tools_new_tab"] = False
    raw["checker_similar_heading"] = False
    raw["tool_faq_wording"] = False
    raw["tool_buttons_outside"] = False
    pre_hide_deal_site_links_raw(raw)  # recorded while ASIN deals still opened the source post
    pre_keepa_focus_finder_raw(raw)
    raw["search"]["tokens_per_day"] = 10000  # recorded before the 2026-10-20 raise
    path.write_text(yaml.safe_dump(raw))
    fetch = pipeline._fetch_all
    canned = e2e._canned_deals
    calls, inventory_at_recheck = [], []

    def fetch_with_early_recheck(sources, fetcher, db, cfg, now, deadline, fast_lane,
                                 feed_confirmed, early_recheck, store_checks):
        inventory_at_recheck.append(len(db.all_deals()))
        early_recheck.update(pipeline._recheck_prices(db, cfg, now, fetcher=fetcher,
                                                     image_backfill=fast_lane))
        posts, errors = fetch(sources, fetcher, db, cfg, now, deadline, fast_lane,
                               feed_confirmed, early_recheck, store_checks)
        return posts + [source_post("dealsofamerica", db)], errors

    def deals(text):
        if "Rigwarl Waterproof" in text:
            return [dict(item="Rigwarl Waterproof Touchscreen Winter Gloves", store="Amazon",
                         product_url="https://www.dealsofamerica.com/Rigwarl-Gloves/817253.htm",
                         regular_price=50, final_price=20, online_only="true", category="clothing",
                         codes=["GLOVE20"], confidence=.95,
                         evidence=dict(regular_price_quote="regularly $50", final_price_quote="for $20"))]
        return canned(text)

    def handler(request):
        if request.url.host == "api.keepa.com":
            calls.append(dict(request.url.params))
            assert request.url.path == "/product"
            assert request.url.params["asin"] == A
            return httpx.Response(200, json={"tokensLeft": 999, "refillRate": 0,
                "tokensConsumed": 1, "products": [{"asin": A, "images": [{"l": "product.jpg"}],
                    "stats": {"current": [99999]}}]})
        return e2e._handler(request)

    def categorize(self, *, messages, **kwargs):
        entries = json.loads(messages[0]["content"])
        response = e2e._FakeResponse(None)
        response.content = [SimpleNamespace(type="text", text=json.dumps([
            {"i": entry["i"], "category": "fashion" if "Rigwarl" in entry["title"] else
             "electronics" if "Headphones" in entry["title"] else "kitchen"}
            for entry in entries]))]
        return response

    monkeypatch.setattr(pipeline, "_fetch_all", fetch_with_early_recheck)
    monkeypatch.setattr(e2e, "_canned_deals", deals)
    monkeypatch.setattr(e2e._FakeMessages, "create", categorize)
    # Model a card arriving after the 25 one-time tier checks are exhausted.
    monkeypatch.setattr(verify, "KEEPA_CHECKS_PER_PASS", 0)
    monkeypatch.setenv("KEEPA_KEY", "offline")
    with closing(DB(env["db"])) as db:
        KeepaBudget(1000, 0, e2e.NOW).save(db)
    result = pipeline.run(db_path=env["db"], config_path=env["cfg"], fast_lane=fast,
        dry_run_telegram=True, transport=httpx.MockTransport(handler),
        sources_override=e2e.SOURCES, skip_validation=True, claude_client=e2e._FakeClaudeClient())
    assert inventory_at_recheck == [0]
    with closing(DB(env["db"])) as db:
        row = next(d for d in db.all_deals() if "Rigwarl" in d["item"])
    assert row["asin"] == A and row["product_identity_url"].endswith(A)
    assert row["canonical_url"].startswith("https://www.dealsofamerica.com/")
    assert bool(row["image_url"]) is enabled
    assert len(calls) == int(enabled)
    assert row["final_price"] == 20 and row["regular_price"] == 50  # API's price is not used.
    doc = json.loads((env["site"] / "deals.json").read_text())
    public = next(d for d in doc["deals"] if "Rigwarl" in d["item"])
    assert public["url"] == row["canonical_url"] and public["needs_steps"]
    assert public["image_url"] == (PIC if enabled else "")
    assert "unlicensed.jpg" not in json.dumps(doc)
    if enabled:
        assert result["post_ingest_photos"]["found"] == 1
        assert calls[0]["history"] == "0"
        assert not {"update", "offers", "buybox"}.intersection(calls[0])
    elif not fast:
        files = snapshot(env["site"])
        digest = hashlib.sha256(b"".join(name.encode() + b"\0" + body
            for name, body in sorted(files.items()))).hexdigest()
        # Recorded before task 331's production edits, across every public file.
        assert digest == "10ceb2be0cbdd75c85e5b3d75f9782ff6d4b140970004f0b4e4ed62a4a223a22"
