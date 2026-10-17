"""Fixed unpaid task-224 capture; keep shipped publication switches intact."""
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

from dealradar import pipeline
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.dedupe import upsert_extracted
from dealradar.parse.claude_client import ClaudeParser
from dealradar.parse.extractor import parse_posts
from dealradar.parse.schema import CriticVerdicts, LLMDeal, LLMDeals
from dealradar.publish import site
from dealradar.savings import compute_savings
from tests.test_amazon_as_of import NOW, sample_deals
from tests.test_claude_client import FakeParseResponse, FakeUsage
from tests.test_extractor import make_post


def capture(out, monkeypatch, setting="omitted"):
    monkeypatch.setattr("dealradar.util.now_utc", lambda: NOW)
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    cfg = load_config()
    cfg.offscreen_cards = False  # recorded before task 237's phone-only CSS
    cfg.dark_polish = False  # recorded before task 254
    cfg.amazon_history = "hide"  # recorded before amz378_998
    if setting != "omitted":
        cfg.bounded_prefilter["enabled"] = setting
    cfg.parse_structured_output = True
    cfg.parse_concurrency = 1  # Deterministic request order, same policy/models.
    calls = []
    attempts = {}

    class Messages:
        def parse(self, **kwargs):
            calls.append({k: v for k, v in kwargs.items() if k != "output_format"})
            if kwargs["output_format"] is CriticVerdicts:
                entries = json.loads(kwargs["messages"][0]["content"])
                reply = CriticVerdicts(verdicts=[dict(index=e["index"], plausible=True,
                    confidence=.95, reason="offline fixture") for e in entries])
            else:
                text = kwargs["messages"][0]["content"]
                item = next(name for name in ("Plain", "Rescue", "Regular", "Malformed") if name in text)
                attempts[item] = attempts.get(item, 0) + 1
                deals = []
                if item == "Regular" or item == "Rescue" and attempts[item] == 2:
                    deals = [LLMDeal(item=item + " widget", store="Walmart", category="home",
                        online_only="true", regular_price=100, final_price=20,
                        product_url="https://www.walmart.com/ip/" + item,
                        evidence=dict(regular_price_quote="was $100", final_price_quote="now $20"))]
                reply = LLMDeals(deals=deals)
            return FakeParseResponse(reply, FakeUsage(100, 30))

    parser = ClaudeParser(cfg, client=SimpleNamespace(messages=Messages()))
    db = DB(out / "private.db")
    try:
        parser.bind_ledger(db, "task224-baseline")
        posts = [make_post(uid=str(i), text=text) for i, text in enumerate([
            "Plain widget was $100 now $90", "Rescue widget was $100 now $20",
            "Regular widget was $100 now $20", "Malformed widget was $1,00 now $90"])]
        pairs, stats = parse_posts(posts, parser, cfg, NOW, {}, db=db)
        # Private counters added after this baseline (tasks 234/235).
        for key in ("parse_seconds", "max_in_flight", "deadline_hit"):
            stats.pop(key, None)
        for post, deal in pairs:
            upsert_extracted(db, deal, compute_savings(deal), post, cfg, NOW)
        critic = pipeline._run_critic(db, cfg, parser, NOW)
        ledger = [dict(row) for row in db.conn.execute("SELECT * FROM llm_calls ORDER BY rowid")]
        for row in ledger:
            row.pop("call_key")
        result = dict(calls=calls, ledger=ledger, stats=stats, critic=critic,
                      pairs=[dataclasses.asdict(deal) for _, deal in pairs],
                      spend=parser.spend_usd, spend_by_kind=parser.spend_by_kind,
                      decisions=db.all_deals(), public={})
        for mode in ("membership", "open", "support", "shipped"):
            publication = load_config()  # Retain 119/121 and every other shipped switch.
            publication.offscreen_cards = False  # recorded before task 237
            publication.dark_polish = False  # recorded before task 254
            publication.amazon_history = "hide"  # recorded before amz378_998
            if mode != "shipped":
                publication.support_enabled = mode == "support"
                publication.paywall_open_access = mode == "open"
            publication.paywall_teaser_count = 2
            publication.seo_hub_min_items = 1
            if setting != "omitted":
                publication.bounded_prefilter["enabled"] = setting
            public_db = DB(":memory:")
            try:
                folder = out / mode
                published = site.render_site(public_db, publication, NOW, out_dir=folder,
                    deals=[*db.all_deals(), *sample_deals()], last_run={"run_key": "task224-baseline"})
                result["public"][mode] = dict(member=published["full_payload"], files={
                    str(p.relative_to(folder)): p.read_bytes().hex()
                    for p in sorted(folder.rglob("*")) if p.is_file()})
            finally:
                public_db.close()
        return json.loads(json.dumps(result))
    finally:
        db.close()
