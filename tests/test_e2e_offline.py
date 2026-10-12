"""Offline end-to-end: the real pipeline over recorded feed fixtures with a canned
LLM client. Exercises RSS fetch -> posts_seen idempotency -> evidence verification
-> savings math -> dedupe/merge -> corroboration -> ranking -> site render ->
Telegram dry-run -> re-run idempotency. No network, no real sleeping.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from dealradar import pipeline
from dealradar.db import DB
from dealradar.models import SourceConfig

FIX = Path(__file__).parent / "fixtures"
ROBOTS = "User-agent: *\nAllow: /\n"


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
                          final_price_quote="just $29.99", pct_quote=""),
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


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "SMTP_HOST",
                "DEALS_CLAUDE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    site_dir = tmp_path / "site"
    db_path = tmp_path / "state.db"
    cfg_path = tmp_path / "config.yaml"
    root_cfg = Path(__file__).resolve().parent.parent / "config.yaml"
    text = root_cfg.read_text().replace('site_dir: "site"', f'site_dir: "{site_dir}"')
    # The offline fake Claude implements messages.parse (not create), so this
    # end-to-end run exercises the structured path; production keeps it off.
    text = text.replace("retries_on_invalid_json: 2",
                        "retries_on_invalid_json: 2\n  structured_output: true")
    cfg_path.write_text(text)
    return {"db": str(db_path), "cfg": str(cfg_path), "site": site_dir}


def _run(env):
    return pipeline.run(
        db_path=env["db"], config_path=env["cfg"], dry_run_telegram=True,
        transport=httpx.MockTransport(_handler), sources_override=SOURCES,
        skip_validation=True, claude_client=_FakeClaudeClient(),
    )


def test_offline_end_to_end(env, capsys, monkeypatch, tmp_path):
    step_summary = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    summary = _run(env)

    assert summary["items_fetched"] == 5
    assert summary["posts_new"] == 5
    # config.yaml pins parse.model; unset it falls back to the cheapest-model
    # heuristic (covered in test_claude_client).
    assert summary["parse"]["parser"] == "claude-haiku-5-5"
    assert summary["parse"]["spend_usd"] > 0

    # 3 deals clear the savings floors: air fryer (77%), knife set (81.7%),
    # headphones (70.0% range). The critic judges all 3 and flags the knife
    # set ("$150 value" fake MSRP) -> suspect_msrp -> only 2 publish.
    assert summary["critic"] == {"judged": 3, "suspect": 1, "reused": 0, "calls": 1}
    assert summary["published"] == 2
    assert summary["link_mix"]["source_post"] == 2
    assert summary["link_mix"]["amazon_untagged_flag"] is False
    assert summary["telegram"]["dry_run"] is True
    assert summary["telegram"]["sent"] == 2

    out = capsys.readouterr().out
    assert "TELEGRAM DRY-RUN:" in out

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
    # Promo code FRYDAY: Get deal opens the source post with the steps and
    # the card shows the code.
    assert fryer["needs_steps"] is True and "FRYDAY" in fryer["codes"]
    assert fryer["url"] in {c["url"] for c in fryer["credits"]}
    # canonical retailer URL resolved through the redirector, tracking params stripped
    db = DB(env["db"])
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


def test_newsletter_pick_rides_the_shared_parser_and_its_spend_is_recorded(env, monkeypatch, tmp_path):
    """The free list's daily pick gets the run's one parser (same caps), and
    whatever it spends is in the run's recorded total (the daily cap reads it)."""
    from dealradar.publish import newsletter

    seen = {}

    def fake_send(db, cfg, now, *, deadline=None, parser=None, **kw):
        seen["parser"] = parser
        parser._account(_FakeResponse(None), parser.model, kind="newsletter_pick")
        return {"sent": 0, "skipped": "test", "day": ""}

    monkeypatch.setattr(newsletter, "send_daily_newsletter", fake_send)
    step_summary = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    summary = _run(env)
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


def test_rerun_is_idempotent(env, capsys):
    first = _run(env)
    assert first["telegram"]["sent"] == 2
    second = _run(env)
    assert second["posts_new"] == 0          # content hashes already known
    assert second["telegram"]["sent"] == 0   # alerts_sent dedup
    assert second["published"] == 2          # same deals, no duplicates
    assert second["critic"]["judged"] == 0   # verdicts persisted; no re-spend

    import sqlite3
    conn = sqlite3.connect(env["db"])
    n_deals = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    n_sight = conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
    n_hist = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    conn.close()
    # 4 distinct deals total (patio excluded), 5 sightings (fryer has 2), stable across reruns
    assert n_deals == 4
    assert n_sight == 5
    assert n_hist == 5
    # The re-run lists nothing new for IndexNow (inside seo.indexnow_min_hours).
    assert json.loads((env["site"].parent / "indexnow_urls.json").read_text()) == []
