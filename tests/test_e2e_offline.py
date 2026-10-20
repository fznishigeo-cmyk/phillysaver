"""Offline end-to-end: the real pipeline over recorded feed fixtures with a canned
LLM client. Exercises RSS fetch -> posts_seen idempotency -> evidence verification
-> savings math -> dedupe/merge -> corroboration -> ranking -> site render ->
Telegram dry-run -> re-run idempotency. No network, no real sleeping.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from dealradar import pipeline
from dealradar.db import DB
from dealradar.models import SourceConfig
import yaml
from tests.history_pins import pre_history_removal_raw, pre_hide_deal_site_links_raw

FIX = Path(__file__).parent / "fixtures"
ROBOTS = "User-agent: *\nAllow: /\n"
NOW = datetime(2026, 10, 9, 14, 0, 0, tzinfo=timezone.utc)


def _handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if path == "/robots.txt":
        return httpx.Response(200, text=ROBOTS)
    if host == "blogone.example" and path == "/feed/":
        return httpx.Response(200, content=(FIX / "e2e_feed_one.xml").read_bytes(),
                              headers={"ETag": '"feed-one-v1"'})
    if host == "feedtwo.example" and path == "/rss":
        return httpx.Response(200, content=(FIX / "e2e_feed_two.xml").read_bytes())
    if host == "go.redirector.example":
        return httpx.Response(
            301, headers={"Location": "https://www.acmekitchen.example/product/airfryer-8qt?utm_source=feed"})
    if host == "www.acmekitchen.example":
        return httpx.Response(200, text="ok")
    return httpx.Response(404)


SOURCES = [
    SourceConfig(name="blogone", method="rss", url="https://blogone.example/feed/",
                 region="US", allowed_html=False, fast_lane=False),
    SourceConfig(name="feedtwo", method="rss", url="https://feedtwo.example/rss",
                 region="US", allowed_html=False, fast_lane=False),
]


def _canned_deals(prompt_text: str) -> list[dict]:
    """Keyed on marker substrings of the post text; quotes are verbatim."""
    if "FRYDAY at checkout" in prompt_text:  # feed one, post 1
        return [dict(
            item="Ninja Foodi 8-qt Air Fryer", store="Acme Kitchen",
            product_url="https://go.redirector.example/r/airfryer",
            online_only="true", regular_price=129.99, final_price=29.99,
            shipping_cost_or_threshold="free over $25", codes=["FRYDAY"],
            expires_at_raw="ends today", confidence=0.95, category="kitchen",
            evidence=dict(regular_price_quote="regularly $129.99",
                          final_price_quote="just $29.99", pct_quote="",
                          shipping_quote="free shipping on orders over $25"),
        )]
    if "Chef Pro Knife Set" in prompt_text:  # feed one, roundup post -> 2 deals
        return [
            dict(item="Chef Pro Knife Set", store="Acme Kitchen",
                 online_only="true", regular_price=150.0, final_price=27.50,
                 codes=["CHOP"], confidence=0.9,
                 evidence=dict(regular_price_quote="$150 value",
                               final_price_quote="for $27.50", pct_quote="")),
            dict(item="Copper Pan Duo", store="Acme Kitchen",
                 online_only="true", regular_price=59.99, final_price=44.99,
                 confidence=0.9,
                 evidence=dict(regular_price_quote="was $59.99",
                               final_price_quote="now $44.99", pct_quote="")),
        ]
    if "Garden Oasis patio set" in prompt_text:  # in-store YMMV -> excluded
        return [dict(
            item="Garden Oasis patio set", store="Garden Oasis",
            online_only="false", regular_price=499.99, final_price=49.97,
            conditions=["in_store_only", "ymmv", "clearance_aisle"], confidence=0.9,
            evidence=dict(regular_price_quote="from $499.99",
                          final_price_quote="at $49.97", pct_quote=""),
        )]
    if "regular price $129.99" in prompt_text:  # feed two, corroborating sighting
        return [dict(
            item="Ninja Foodi Air Fryer 8 qt", store="Acme Kitchen",
            online_only="true", regular_price=129.99, final_price=29.99,
            codes=["FRYDAY"], confidence=0.9,
            evidence=dict(regular_price_quote="regular price $129.99",
                          final_price_quote="for $29.99", pct_quote=""),
        )]
    if "Mega Headphones X200" in prompt_text:  # as-low-as range
        return [dict(
            item="Mega Headphones X200", store="SoundHut",
            online_only="true", regular_price=199.99, final_price=39.99,
            price_is_range=True, price_upper_bound=59.99, confidence=0.85,
            evidence=dict(regular_price_quote="list price $199.99",
                          final_price_quote="as low as $39.99", pct_quote="",
                          price_upper_bound_quote="most colors $59.99"),
        )]
    return []


class _FakeModel:
    def __init__(self, id: str, created_at: str) -> None:
        self.id = id
        self.created_at = created_at


class _FakeModels:
    def list(self):
        return [
            _FakeModel("claude-opus-5-5", "2026-03-01"),
            _FakeModel("claude-haiku-5-5", "2026-02-01"),
            _FakeModel("claude-haiku-4-5", "2025-10-01"),
            _FakeModel("claude-sonnet-5-5", "2026-01-15"),
        ]


class _Usage:
    input_tokens = 1200
    output_tokens = 300


class _FakeResponse:
    def __init__(self, parsed_output) -> None:
        self.parsed_output = parsed_output
        self.usage = _Usage()
        self.stop_reason = "end_turn"
        self.content = []


class _FakeMessages:
    def create(self, *, model, max_tokens, system, messages):
        from types import SimpleNamespace

        assert "Return ONLY a JSON array" in system
        entries = json.loads(messages[0]["content"])
        categories = {"Ninja Foodi 8-qt Air Fryer": "kitchen",
                      "Chef Pro Knife Set": "kitchen", "Copper Pan Duo": "kitchen",
                      "Mega Headphones X200": "electronics"}
        response = _FakeResponse(None)
        response.content = [SimpleNamespace(type="text", text=json.dumps([
            {"i": e["i"], "category": categories[e["title"]]} for e in entries]))]
        return response

    def parse(self, *, model, max_tokens, system, messages, output_format, **kw):
        from dealradar.parse.schema import CriticVerdicts
        import json

        text = messages[0]["content"]
        if output_format is CriticVerdicts:
            entries = json.loads(text)
            return _FakeResponse(CriticVerdicts.model_validate({"verdicts": [
                {"index": entry["index"],
                 "plausible": "Chef Pro Knife Set" not in entry["item"],
                 "confidence": 0.9, "reason": "judged by the offline fake critic"}
                for entry in entries]}))
        deals = _canned_deals(text)
        return _FakeResponse(output_format.model_validate({"deals": deals}))


class _FakeClaudeClient:
    models = _FakeModels()
    messages = _FakeMessages()


def _offline_env(tmp_path, monkeypatch):
    monkeypatch.setattr("dealradar.util.now_utc", lambda: NOW)
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "SMTP_HOST",
                "DEALS_CLAUDE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    # A public test link key makes the resolved retailer pick earn offline.
    monkeypatch.setenv("SOVRN_API_KEY", "0123456789abcdef0123456789abcdef")
    fetch_all = pipeline._fetch_all

    def fetch_with_photos(*args, **kwargs):
        posts, errors = fetch_all(*args, **kwargs)
        # Recorded retailer photos make both public picks channel-eligible.
        return [dataclasses.replace(
            post, image_url=("https://images.example/airfryer.jpg" if "Ninja Foodi" in post.title
                             else "https://images.example/headphones.jpg" if "X200" in post.title
                             else "")) for post in posts], errors

    monkeypatch.setattr(pipeline, "_fetch_all", fetch_with_photos)
    site_dir = tmp_path / "site"
    db_path = tmp_path / "state.db"
    cfg_path = tmp_path / "config.yaml"
    root_cfg = Path(__file__).resolve().parent.parent / "config.yaml"
    text = root_cfg.read_text().replace('site_dir: "site"', f'site_dir: "{site_dir}"')
    # Legacy captures predate task 282; the rating-specific rerun opts in below.
    text = text.replace("appeal_score:\n  enabled: true", "appeal_score:\n  enabled: false")
    # The offline fake Claude implements messages.parse (not create), so this
    # end-to-end run exercises the structured path; production keeps it off.
    text = text.replace("retries_on_invalid_json: 2",
                        "retries_on_invalid_json: 2\n  structured_output: true")
    # Every offline deal is first seen in this run; skip the page-age gate.
    text = text.replace("min_age_minutes: 20", "min_age_minutes: 0")
    cfg_path.write_text(text)
    return {"db": str(db_path), "cfg": str(cfg_path), "site": site_dir}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    result = _offline_env(tmp_path, monkeypatch)
    path = Path(result["cfg"])
    # These legacy pipeline expectations predate tiering; new cases use the shipped config.
    path.write_text(path.read_text().replace('critic_tiering:\n    enabled: true',
                                             'critic_tiering:\n    enabled: false'))
    return result


def keep_source_posts(env):
    """Recorded offline facts include source-only offers, which task 351 hides."""
    path = Path(env["cfg"])
    path.write_text(yaml.safe_dump(pre_hide_deal_site_links_raw(yaml.safe_load(path.read_text()))))


def _run(env):
    return pipeline.run(
        db_path=env["db"], config_path=env["cfg"], dry_run_telegram=True,
        transport=httpx.MockTransport(_handler), sources_override=SOURCES,
        skip_validation=True, claude_client=_FakeClaudeClient(),
    )


def test_offline_end_to_end(env, capsys, monkeypatch, tmp_path):
    # Recorded public facts include a source-only offer; retain the OFF case.
    path = Path(env["cfg"])
    path.write_text(yaml.safe_dump(pre_hide_deal_site_links_raw(yaml.safe_load(path.read_text()))))
    step_summary = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    summary = _run(env)

    assert summary["items_fetched"] == 5
    assert summary["posts_new"] == 5
    assert summary["store_checks"] == {
        store: dict(checked=0, confirmed=0, repriced=0, suspect=0, gone=0, errors=0)
        for store in ("bestbuy", "ebay")}
    assert summary["state_db_bytes"] == Path(env["db"]).stat().st_size > 0
    # config.yaml pins parse.model; unset it falls back to the cheapest-model
    # heuristic (covered in test_claude_client).
    assert summary["parse"]["parser"] == "claude-haiku-5-5"
    assert summary["parse"]["spend_usd"] > 0

    # 3 deals clear the savings floors: air fryer (77%), knife set (81.7%),
    # headphones (70.0% range). The critic judges all 3 and flags the knife
    # set ("$150 value" fake MSRP) -> suspect_msrp -> only 2 publish.
    assert summary["critic"] == {"judged": 3, "suspect": 1, "reused": 0, "calls": 1}
    assert summary["published"] == 2
    # The later corroborating post omits shipping; do not revive an old promise.
    assert summary["checkout"] == {"public_live": 2, "known_shipping": 0,
        "with_requirements": 2, "covered": 2, "known_shipping_share": 0,
        "requirement_coverage": 1}
    assert set(summary["seo_audit"]["indexable_by_family"]) == {
        "home", "category", "store", "price", "best_today", "deal", "product_family", "product", "event"}
    assert all(summary["seo_audit"][k] == 0 for k in (
        "empty_indexable", "schema_mismatches", "broken_internal_links", "sitemap_missing", "sitemap_extra"))
    assert summary["link_mix"]["source_post"] == 1
    assert summary["link_mix"]["sovrn"] == 1
    assert summary["link_mix"]["earning"] == summary["link_mix"]["not_earning"] == 1
    assert summary["link_mix"]["trackers_resolved"] == 0
    assert summary["link_mix"]["amazon_untagged_flag"] is False
    assert summary["telegram"]["dry_run"] is True
    assert summary["telegram"]["sent"] == 1

    out = capsys.readouterr().out
    assert out.count("TELEGRAM DRY-RUN:") == 1
    assert "TELEGRAM DRY-RUN: 🔥 " in out
    assert ", verified by wowpriced: https://wowpriced.com/deal/" in out
    assert "\n#ad" in out

    deals_json = json.loads((env["site"] / "deals.json").read_text())
    assert deals_json["meta"]["disclosure"]
    # Spend and model are the operator's: never in the public file.
    last_run = deals_json["meta"]["last_run"]
    assert last_run["run_key"] and "spend_usd" not in last_run and "parser_model" not in last_run
    assert len(deals_json["deals"]) == 2
    by_item = {d["item"]: d for d in deals_json["deals"]}
    assert not any("Knife" in i for i in by_item)  # critic-flagged, never shipped
    fryer = next(d for i, d in by_item.items() if "Ninja Foodi" in i)
    assert fryer["badge"] == "corroborated"  # two independent sources in 24 h
    assert len(fryer["credits"]) == 2
    assert fryer["category"] == "kitchen"    # LLM category tag reaches the site
    # Promo code FRYDAY: Get deal uses the earning store link and the card
    # keeps the code and both source credits.
    assert fryer["needs_steps"] is True and "FRYDAY" in fryer["codes"]
    assert fryer["earns"] is True and urlsplit(fryer["url"]).hostname == "redirect.viglink.com"
    assert "acmekitchen.example" in parse_qs(urlsplit(fryer["url"]).query)["u"][0]
    # canonical retailer URL resolved through the redirector, tracking params stripped
    db = DB(env["db"])
    # The audit ledger includes excluded deals and post versions with no
    # publishable result. Its gate counts run after the real critic.
    counters = db.conn.execute(
        "SELECT SUM(extracted), SUM(duplicate), SUM(below_floor), SUM(critic), SUM(other)"
        " FROM source_audit_posts WHERE parsed_at IS NOT NULL").fetchone()
    assert tuple(counters) == (6, 1, 1, 1, 1)
    canon = next(d["canonical_url"] for d in db.all_deals() if "Ninja Foodi" in d["item"])
    db.close()
    assert "acmekitchen.example" in canon
    assert "utm" not in canon

    headphones = next(d for i, d in by_item.items() if "X200" in i)
    assert "range" in headphones["flags"]
    assert headphones["pct_at_checkout"] == pytest.approx(70.0, abs=0.1)

    assert (env["site"] / "index.html").exists()
    # Source health is the operator's: the run summary, never the site.
    assert not (env["site"] / "health.html").exists()
    assert not (env["site"] / "health.json").exists()
    md = step_summary.read_text()
    assert "## Source health (2 sources, 0 tripped)" in md
    assert "| blogone | ok |" in md and "| feedtwo | ok |" in md
    assert "## LLM spend by kind" in md
    assert "| extract | 5 |" in md and "| critic | 1 |" in md
    assert "## Extraction spend by source" in md
    assert summary["spend_by_kind"]["extract"]["calls"] == 5
    assert summary["spend_by_kind"]["critic"]["calls"] == 1
    assert sum(s["usd"] for s in summary["spend_by_kind"].values()) == pytest.approx(
        summary["spend_usd"])
    assert sum(s["calls"] for s in summary["extract_by_source"].values()) == 5
    assert sum(s["usd"] for s in summary["extract_by_source"].values()) == pytest.approx(
        summary["spend_by_kind"]["extract"]["usd"])
    for private in ("Ninja Foodi", "Chef Pro Knife", "FRYDAY", "$29.99", "BEGIN POST"):
        assert private not in md
    assert "spend_by_kind" not in last_run and "extract_by_source" not in last_run


def test_pipeline_uses_configured_state_retention(env):
    import yaml
    from datetime import timedelta

    cfg_path = Path(env["cfg"])
    cfg = yaml.safe_load(cfg_path.read_text())
    for name in ("llm_calls_days", "run_stats_days", "http_cache_days", "outgoing_days"):
        cfg["retention"][name] = 1
    cfg_path.write_text(yaml.safe_dump(cfg))
    at = NOW - timedelta(days=3)
    db = DB(env["db"])
    db.start_run("old", "fast", at)
    db.store_validators("https://old.example/feed", '"old"', "", at)
    for status in ("settled", "reserved"):
        db.reserve_llm_call("old-" + status, "old", "fixture", .1, at,
                            {at.date().isoformat(): 1}, 1)
        if status == "settled":
            db.settle_llm_call("old-" + status, .01, at)
    for status in ("sent", "rejected", "pending"):
        assert db.claim_outgoing("test", "old-" + status, "old", at)
        if status != "pending":
            db.finish_outgoing("test", "old-" + status, at, rejected=status == "rejected")
    db.close()

    summary = _run(env)

    assert summary["state_db_bytes"] == Path(env["db"]).stat().st_size > 0
    db = DB(env["db"])
    assert db.conn.execute("SELECT 1 FROM run_stats WHERE run_key='old'").fetchone()  # permanent replay anchor under 337
    assert db.get_validators("https://old.example/feed") == ("", "")
    assert not db.conn.execute("SELECT 1 FROM llm_calls WHERE call_key='old-settled'").fetchone()
    assert db.conn.execute("SELECT status FROM llm_calls WHERE call_key='old-reserved'").fetchone()[0] == "reserved"
    assert not db.conn.execute(
        "SELECT 1 FROM outgoing WHERE dedup_token='old-rejected'").fetchone()
    assert db.outgoing_exists("test", "old-sent")  # never re-post a live deal
    assert db.outgoing_exists("test", "old-pending")
    db.close()


def test_newsletter_pick_rides_the_shared_parser_and_its_spend_is_recorded(env, monkeypatch, tmp_path):
    """The free list's daily pick gets the run's one parser (same caps), and
    whatever it spends is in the run's recorded total (the daily cap reads it)."""
    from dealradar.publish import newsletter

    seen = {}

    def fake_send(db, cfg, now, *, deadline=None, parser=None, **kw):
        seen["parser"] = parser
        seen["run_key"] = kw.get("run_key")
        parser._account(_FakeResponse(None), parser.model, kind="newsletter_pick")
        return {"sent": 0, "skipped": "test", "day": ""}

    monkeypatch.setattr(newsletter, "send_daily_newsletter", fake_send)
    step_summary = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    summary = _run(env)
    assert seen["run_key"] == summary["run_key"]
    parser = seen["parser"]
    assert parser is not None and parser.model == "claude-haiku-5-5"
    assert parser.spend_usd > summary["parse"]["spend_usd"] > 0

    import sqlite3
    conn = sqlite3.connect(env["db"])
    (recorded,) = conn.execute("SELECT spend_usd FROM run_stats").fetchone()
    conn.close()
    assert recorded == pytest.approx(parser.spend_usd)
    assert summary["spend_usd"] == pytest.approx(recorded)
    assert summary["spend_by_kind"]["newsletter_pick"]["calls"] == 1
    assert "| newsletter_pick | 1 |" in step_summary.read_text()


def test_audit_tracks_evidence_rejects_without_storing_rejected_content(env, monkeypatch):
    original = _canned_deals

    def with_bad_evidence(prompt):
        deals = original(prompt)
        if "FRYDAY at checkout" in prompt:
            deals.append(dict(item="REJECTED PRIVATE ITEM", store="Nowhere",
                              final_price=99.99, online_only="true",
                              evidence=dict(final_price_quote="only $99.99")))
        return deals

    monkeypatch.setattr("tests.test_e2e_offline._canned_deals", with_bad_evidence)
    result = _run(env)
    assert result["parse"]["deals_dropped_evidence"] == 1
    db = DB(env["db"])
    counters = db.conn.execute(
        "SELECT SUM(extracted), SUM(evidence) FROM source_audit_posts").fetchone()
    assert tuple(counters) == (7, 1)
    assert "REJECTED PRIVATE ITEM" not in "\n".join(db.conn.iterdump())
    db.close()


@pytest.mark.parametrize("audit_mode", ["shadow", "enforce"])
def test_rerun_is_idempotent(env, capsys, audit_mode):
    from dealradar.config import load_config
    from dealradar.publish import indexnow
    from tests.test_posted_deal_redirects import _insert, _path, _post, _prune, _render, META

    pruned_post = env.get("pruned_post", False)
    path = Path(env["cfg"])
    path.write_text(path.read_text().replace("mode: shadow                 # enforce", f"mode: {audit_mode}                 # enforce"))
    # An old posted page disappears before this run. Its private category
    # survives a real retention prune, and both runs must emit identical rules.
    if pruned_post:
        db = DB(env["db"])
        cfg = load_config(env["cfg"])
        # A previously posted page must qualify for publication before pruning,
        # including the shipped one-hour photo cutoff for this two-hour-old deal.
        expired = _insert(db, dedupe_key="old-posted", item="Old kitchen deal", category="kitchen",
                          image_url="https://images.example/old-kitchen.jpg")
        _post(db, expired)
        _render(db, cfg, env["site"], now=NOW, deals=[expired])
        assert (env["site"] / _path(expired).strip("/") / "index.html").is_file()
        assert _path(expired) in json.loads(db.meta_get(META))
        _prune(db, [expired], now=NOW)
        db.close()
    first = _run(env)
    assert first["audit"]["mode"] == audit_mode
    assert first["telegram"]["sent"] == 1
    db = DB(env["db"])
    cfg = load_config(env["cfg"])
    redirects = env["site"] / "_redirects"
    checker_before = {p.name: p.read_bytes() for p in (env["site"] / "check" / "data").glob("*.json")}
    if cfg.checker["enabled"]:
        assert len(checker_before) == cfg.checker["shards"] + 2  # meta.json and the device index
        assert first["checker"]["aliases"] > 0
    redirects_before = redirects.read_bytes() if redirects.exists() else b""
    assert redirects_before == (f"{_path(expired)} /deals/home-kitchen/ 301\n".encode() if pruned_post else b"")
    redirect_state = db.meta_get(META)
    assert first["posted_redirects"] == {"rules": int(pruned_post), "added_today": int(pruned_post), "dropped_by_cap": 0}
    assert cfg.front_page["appeal"]["enabled"]
    requests = []

    def receipt(request):
        requests.append(request)
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(receipt)) as client:
        assert indexnow.submit_indexnow(db, cfg, client, deployed=True,
                                       run_key=first["run_key"], now=NOW) == 0
    assert len(requests) == 1
    before = {table: db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in ("deals", "sightings", "price_history", "alerts_sent", "source_audit_posts",
                            "indexnow_pages", "indexnow_batches", "legitimacy_audits", "legitimacy_batches",
                            "products", "product_keys", "product_offers", "meta", "run_stats")}
    run_stats_before = [tuple(row) for row in db.conn.execute(
        "SELECT run_key,posts_left,deadline_hit FROM run_stats ORDER BY run_key")]
    assert run_stats_before == [(first["run_key"], 0, 0)]
    audit_before = [tuple(row) for row in db.conn.execute("SELECT * FROM source_audit_posts ORDER BY content_hash")]
    receipts_before = [tuple(row) for row in db.conn.execute("SELECT * FROM indexnow_pages ORDER BY url")]
    history_before = [tuple(row) for row in db.conn.execute("SELECT * FROM price_history ORDER BY id")]
    db.close()
    second = _run(env)
    assert second["appeal"] == first["appeal"] == {
        "niche_in_wall": 0, "niche_in_top30": 0, "niche_examples": []}
    assert second["run_key"] == first["run_key"]
    assert second["posts_new"] == 0          # content hashes already known
    assert second["telegram"]["sent"] == 0   # alerts_sent dedup
    assert second["published"] == first["published"] == (1 if cfg.hide_deal_site_links else 2)
    if cfg.hide_deal_site_links:
        assert second["hidden_deal_site_links"] == first["hidden_deal_site_links"] == 1
    assert second["critic"]["judged"] == 0   # verdicts persisted; no re-spend
    db = DB(env["db"])
    assert (redirects.read_bytes() if redirects.exists() else b"") == redirects_before
    assert db.meta_get(META) == redirect_state
    assert second["posted_redirects"] == first["posted_redirects"]
    assert {p.name: p.read_bytes() for p in (env["site"] / "check" / "data").glob("*.json")} == checker_before
    if cfg.checker["enabled"]:
        assert second["checker"] == first["checker"]
    with httpx.Client(transport=httpx.MockTransport(receipt)) as client:
        assert indexnow.submit_indexnow(db, cfg, client, deployed=True,
                                       run_key=second["run_key"], now=NOW) == 0
    assert len(requests) == 1
    assert [tuple(row) for row in db.conn.execute("SELECT * FROM indexnow_pages ORDER BY url")] == receipts_before
    assert [tuple(row) for row in db.conn.execute("SELECT * FROM price_history ORDER BY id")] == history_before
    assert [tuple(row) for row in db.conn.execute(
        "SELECT run_key,posts_left,deadline_hit FROM run_stats ORDER BY run_key")] == run_stats_before
    db.close()

    import sqlite3
    conn = sqlite3.connect(env["db"])
    n_deals = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    n_sight = conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
    n_hist = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    after = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
             for table in before}
    assert [tuple(row) for row in conn.execute("SELECT * FROM source_audit_posts ORDER BY content_hash")] == audit_before
    (token,) = conn.execute("SELECT dedup_token FROM alerts_sent WHERE channel='telegram'").fetchone()
    conn.close()
    assert after == before and token.endswith("|telegram")
    # 4 distinct deals total (patio excluded), 5 sightings (fryer has 2), stable across reruns
    assert n_deals == 4
    assert n_sight == 5
    assert n_hist == 5
    # Receipt, rather than publishing the due-list, suppresses this re-run.
    assert json.loads((env["site"].parent / "indexnow_urls.json").read_text()) == []


@pytest.mark.parametrize("audit_mode", ["shadow", "enforce"])
def test_posted_pruned_rerun_is_idempotent(env, capsys, audit_mode):
    test_rerun_is_idempotent(dict(env, pruned_post=True), capsys, audit_mode)


@pytest.mark.parametrize("audit_mode", ["shadow", "enforce"])
def test_ai_take_rerun_is_idempotent(env, monkeypatch, audit_mode):
    from tests.test_ai_take import Messages
    from tests.test_checker_index import add, files

    path = Path(env["cfg"])
    path.write_text(path.read_text().replace("amazon_reference: list_price", "amazon_reference: history")  # recorded before task 276
                    .replace("ai_take:\n    enabled: false", "ai_take:\n    enabled: true")
                    .replace("mode: shadow                 # enforce", f"mode: {audit_mode}                 # enforce"))
    # AI takes live on product pages, which the 2026-10-17 history removal turned off.
    path.write_text(yaml.safe_dump(pre_history_removal_raw(yaml.safe_load(path.read_text()))))
    messages = Messages()
    calls = []

    class CombinedMessages(_FakeMessages):
        def create(self, **kwargs):
            calls.append("create")
            if kwargs["system"].startswith("Write one plain-English price take"):
                return messages.create(**kwargs)
            return super().create(**kwargs)

        def parse(self, **kwargs):
            calls.append("parse")
            return super().parse(**kwargs)

    monkeypatch.setattr(_FakeClaudeClient, "messages", CombinedMessages())
    db = DB(env["db"])
    add(db, "bestbuy", item="Copper Pan Duo")
    db.close()
    first = _run(env)
    assert first["ai_take"]["requested"] == first["ai_take"]["verified"] == 1
    assert len(messages.calls) == 1
    public_before = files(env["site"])
    db = DB(env["db"])
    tables = [r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    before = {table: db.conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables}
    cached_before = [tuple(r) for r in db.conn.execute("SELECT * FROM meta WHERE k LIKE 'ai_take%' ORDER BY k")]
    ledger_before = [tuple(r) for r in db.conn.execute("SELECT * FROM llm_calls ORDER BY call_key")]
    db.close()
    calls.clear()
    second = _run(env)
    assert second["run_key"] == first["run_key"]
    assert second["ai_take"]["requested"] == second["ai_take"]["verified"] == 0
    assert second["ai_take"]["cached"] == 1
    assert calls == [] and len(messages.calls) == 1
    public_after = files(env["site"])
    before_json, after_json = (json.loads(tree.pop("deals.json")) for tree in (public_before, public_after))
    before_run = before_json["meta"].pop("last_run")
    after_run = after_json["meta"].pop("last_run")
    assert before_run["posts_new"] > 0 and after_run["posts_new"] == 0
    assert before_json == after_json and public_after == public_before
    db = DB(env["db"])
    assert {table: db.conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables} == before
    assert [tuple(r) for r in db.conn.execute("SELECT * FROM meta WHERE k LIKE 'ai_take%' ORDER BY k")] == cached_before
    assert [tuple(r) for r in db.conn.execute("SELECT * FROM llm_calls ORDER BY call_key")] == ledger_before
    db.close()


@pytest.mark.parametrize("audit_mode", ["shadow", "enforce"])
def test_appeal_score_rerun_is_idempotent(env, monkeypatch, audit_mode):
    from tests.test_impulse_appeal_score import Messages

    path = Path(env["cfg"])
    raw = yaml.safe_load(path.read_text())
    raw["appeal_score"]["enabled"] = True
    raw["audit"]["mode"] = audit_mode
    path.write_text(yaml.safe_dump(raw))
    ratings = Messages()

    class CombinedMessages(_FakeMessages):
        def create(self, **kwargs):
            if kwargs["system"].startswith("Would a deal-hunting woman"):
                return ratings.create(**kwargs)
            return super().create(**kwargs)

    monkeypatch.setattr(_FakeClaudeClient, "messages", CombinedMessages())
    first = _run(env)
    assert first["spend_by_kind"]["appeal_score"]["calls"] == len(ratings.calls) == 1
    assert first["spend_by_kind"]["appeal_score"]["usd"] > 0
    db = DB(env["db"])
    tables = [r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    before = {table: db.conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables}
    cached = [tuple(r) for r in db.conn.execute("SELECT * FROM appeal_scores ORDER BY dedupe_key")]
    assert cached and all(row[2] == 4 for row in cached)
    ledger = [tuple(r) for r in db.conn.execute("SELECT * FROM llm_calls ORDER BY call_key")]
    db.close()
    second = _run(env)
    assert second["run_key"] == first["run_key"] and len(ratings.calls) == 1
    db = DB(env["db"])
    assert {table: db.conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables} == before
    assert [tuple(r) for r in db.conn.execute("SELECT * FROM appeal_scores ORDER BY dedupe_key")] == cached
    assert [tuple(r) for r in db.conn.execute("SELECT * FROM llm_calls ORDER BY call_key")] == ledger
    db.close()
    assert b"appeal_score" not in (env["site"] / "deals.json").read_bytes()


@pytest.mark.parametrize("signal,dead_items", [
    ("unrelated", set()), ("title", {"Chef Pro Knife Set", "Copper Pan Duo"}),
    ("evidence", {"Chef Pro Knife Set"}),
])
def test_end_signals_are_deal_specific_and_counted_once(env, monkeypatch, caplog,
                                                       signal, dead_items):
    import dataclasses
    import logging

    fetch_all = pipeline._fetch_all

    def fetch(*args, **kwargs):
        posts, errors = fetch_all(*args, **kwargs)
        changed = []
        for post in posts:
            title, text = post.title, post.text
            if "Chef Pro Knife Set" in text:
                if signal == "title":
                    title = "EXPIRED: " + title
                elif signal == "evidence":
                    text = text.replace("for $27.50", "for $27.50 — sold out")
            text += "\nNavigation: expired deals\nSidebar: Dead Sea salt\nComment: OOS"
            changed.append(dataclasses.replace(post, title=title, text=text))
        return changed, errors

    monkeypatch.setattr(pipeline, "_fetch_all", fetch)
    with caplog.at_level(logging.INFO, logger="dealradar.pipeline"):
        first = _run(env)
    db = DB(env["db"])
    assert {d["item"] for d in db.all_deals(status="dead")} == dead_items
    assert db.conn.execute("SELECT SUM(dead) FROM source_audit_posts").fetchone()[0] == len(dead_items)
    db.close()
    assert first["deaths_by_reason"] == (
        {"post_says_over": len(dead_items)} if dead_items else {})
    assert any(r.message == f"deaths by reason: {first['deaths_by_reason']}"
               for r in caplog.records)
    assert "deaths_by_reason" not in json.loads(
        (env["site"] / "deals.json").read_text())["meta"]["last_run"]
    second = _run(env)
    assert second["deaths_by_reason"] == {}


def test_deaths_summary_counts_api_expiry_and_confirmation_but_not_old_deaths(env,
                                                                           monkeypatch):
    from datetime import timedelta

    from dealradar import dedupe, util, verify
    from dealradar.fetch.woot import _recheck_offers
    from dealradar.keepa_budget import KeepaBudget
    from tests.test_dedupe import make_cfg, make_extracted, make_post, make_savings

    now = util.now_utc()
    db = DB(env["db"])
    # Shipped click-price mode routes this Unknown through budgeted recovery;
    # the mocked verdict still needs a funded bucket, as a real run would have.
    KeepaBudget(500, 20, now).save(db)
    keys = {}
    for item, store, url in [
        ("confirmed", "Amazon", "https://www.amazon.com/dp/B012345678"),
        ("woot", "Woot", "https://home.woot.com/offers/lamp"),
        ("expiry", "Target", "https://www.target.com/p/lamp"),
        ("old", "Target", "https://www.target.com/p/old"),
    ]:
        ext = make_extracted(item=item, store=store, product_url=url, category="home",
                             expires_at=util.iso(now - timedelta(minutes=1))
                             if item == "expiry" else "")
        keys[item] = dedupe.upsert_extracted(
            db, ext, make_savings(), make_post(uid=item), make_cfg(), now)["dedupe_key"]
    row = db.get_deal(keys["confirmed"])
    db.update_deal(keys["confirmed"], payload=dict(
        row, gone_suspect_at=util.iso(now - timedelta(hours=2)),
        price_rechecked_at=util.iso(now - timedelta(hours=2))))
    verify.mark_unconfirmed_dead(db, db.get_deal(keys["old"]), "post_says_over", now)
    db.close()

    def fetch(_sources, _fetcher, database, _cfg, at, *_args):
        # Woot's own end date can kill during fetch, before expiry marking.
        _recheck_offers(database, [{"Url": "https://home.woot.com/offers/lamp",
                                   "EndDate": util.iso(at - timedelta(minutes=1))}], at)
        return [], {}

    monkeypatch.setattr(pipeline, "_fetch_all", fetch)
    monkeypatch.setattr(verify, "keepa_check", lambda *a, **kw: None)
    monkeypatch.setattr(verify, "recheck_amazon_prices", lambda deals, *a, **kw:
                        {d["dedupe_key"]: False for d in deals})
    first = _run(env)
    assert first["deaths_by_reason"] == {
        "api_ended": 1, "stated_expiry": 1, "confirmed": 1}
    assert _run(env)["deaths_by_reason"] == {}


def test_compare_failure_does_not_stop_fast_lane_rerun(env, monkeypatch, caplog):
    from dealradar import compare
    path = Path(env['cfg'])
    # Keep every other paid feature at its shipped settings.
    path.write_text(path.read_text().replace('  refresh: false           # paid step', '  refresh: true            # paid step'))
    attempts = []
    def broken(*args, **kwargs):
        attempts.append(True)
        raise ValueError('private exception payload')
    monkeypatch.setattr(compare, 'refresh', broken)
    for _ in range(2):
        pipeline.run(fast_lane=True, db_path=env['db'], config_path=env['cfg'],
            dry_run_telegram=True, transport=httpx.MockTransport(_handler),
            sources_override=[], skip_validation=True, claude_client=_FakeClaudeClient())
        assert (env['site'] / 'index.html').is_file()
    assert attempts
    assert 'compare refresh unavailable (ValueError)' in caplog.text
    assert 'private exception payload' not in caplog.text
