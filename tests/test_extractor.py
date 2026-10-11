"""Tests for dealradar.parse.extractor: cleaning, expiry resolution, parse loop."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from dealradar import util
from dealradar.config import AppConfig
from dealradar.errors import SpendCapReached
from dealradar.models import RawPost
from dealradar.parse.extractor import clean_for_llm, parse_posts, resolve_expires
from dealradar.parse.schema import LLMDeal, LLMEvidence

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)
TZ = "America/New_York"


def make_post(**over) -> RawPost:
    kw = dict(
        source_name="hip2save",
        uid="p1",
        url="https://hip2save.com/deal/1",
        title="Big deal",
        text="Widget was $20.00 now $10.00",
        published_at=NOW,
        fetched_at=NOW,
    )
    kw.update(over)
    return RawPost(**kw)


def make_cfg(**over) -> AppConfig:
    cfg = AppConfig()
    cfg.max_posts_per_run = 400
    cfg.max_chars_per_post = 6000
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


class TestConcurrentParse:
    """parse.concurrency runs several LLM reads at once; semantics (order of
    results, cap/deadline handling, idempotent hash recording) must hold."""

    def _posts(self, n):
        return [make_post(uid=f"p{i}", url=f"https://hip2save.com/deal/{i}",
                          text=f"Widget {i} was $20.00 now $10.00")
                for i in range(n)]

    def test_pool_parses_every_post_in_submission_order(self):
        posts = self._posts(7)
        parser = FakeParser(results=[[] for _ in range(7)])
        pairs, stats = parse_posts(posts, parser, make_cfg(parse_concurrency=3),
                                   NOW, {})
        assert stats["posts_sent"] == 7
        assert len(parser.calls) == 7
        assert stats["processed_hashes"] == [p.content_hash for p in posts]
        assert pairs == []

    def test_pool_spend_cap_keeps_paid_batchmates_and_stops_submitting(self):
        posts = self._posts(9)
        parser = FakeParser(results=[], raise_cap_at=4)  # 5th read raises
        _, stats = parse_posts(posts, parser, make_cfg(parse_concurrency=3),
                               NOW, {})
        assert stats["aborted_spend_cap"] is True
        # Batch 1 (3) fully paid; batch 2 partly; batch 3 never submitted.
        assert 3 <= stats["posts_sent"] <= 5
        first_six = {p.content_hash for p in posts[:6]}
        assert set(stats["processed_hashes"]) <= first_six
        assert all(p.content_hash not in stats["processed_hashes"]
                   for p in posts[6:])


class FakeParser:
    """Stands in for ClaudeParser: canned per-post results, optional cap raise."""

    def __init__(self, results=None, spend_usd=0.123, raise_cap_at=None):
        self.results = list(results or [])
        self.spend_usd = spend_usd
        self.raise_cap_at = raise_cap_at
        self.calls: list[tuple[str, dict]] = []

    def extract(self, text: str, meta: dict) -> list[LLMDeal]:
        if self.raise_cap_at is not None and len(self.calls) >= self.raise_cap_at:
            raise SpendCapReached("cap hit")
        self.calls.append((text, meta))
        return self.results.pop(0) if self.results else []


# ------------------------------------------------------------ clean_for_llm

class TestCleanForLLM:
    def test_collapses_whitespace_but_preserves_single_newlines(self):
        post = make_post(text="a   b\t\tc  \n\n\n  d \r\n e")
        assert clean_for_llm(post, 6000) == "a b c\nd\ne"

    def test_short_text_untouched_by_truncation(self):
        post = make_post(text="now $10.00")
        assert clean_for_llm(post, 6000) == "now $10.00"

    def test_truncates_at_6000_on_whitespace_boundary(self):
        # 9-char unit "abcdefgh " -> 6000 is mid-word, forcing a backtrack
        post = make_post(text="abcdefgh " * 1000)
        cleaned = clean_for_llm(post, 6000)
        assert len(cleaned) <= 6000
        assert len(cleaned) > 5900          # truncated near the cap, not far below
        assert set(cleaned.split()) == {"abcdefgh"}  # no half words
        assert not cleaned[-1].isspace()
        # the result is a prefix of the fully cleaned text
        full = clean_for_llm(post, 10_000_000)
        assert full.startswith(cleaned)
        assert full[len(cleaned)].isspace()

    def test_single_giant_token_hard_cut(self):
        post = make_post(text="x" * 7000)
        cleaned = clean_for_llm(post, 6000)
        assert len(cleaned) == 6000


# ---------------------------------------------------------- resolve_expires

def eod(y, m, d, tz=TZ):
    from zoneinfo import ZoneInfo
    return util.iso(datetime(y, m, d, 23, 59, 59, tzinfo=ZoneInfo(tz)))


class TestResolveExpires:
    @pytest.mark.parametrize("raw,expected", [
        ("", ""),
        ("   ", ""),
        ("ends today", util.iso(util.end_of_day(TZ, NOW))),
        ("today", util.iso(util.end_of_day(TZ, NOW))),
        ("tonight only!", util.iso(util.end_of_day(TZ, NOW))),
        ("2026-10-12T15:00:00+00:00", "2026-10-12T15:00:00+00:00"),
        ("2026-10-12T15:00:00", "2026-10-12T19:00:00+00:00"),  # naive -> tz_name (EDT)
        ("through 10/12", eod(2026, 10, 12)),                  # year from `now` in tz
        ("10/12/2026", eod(2026, 10, 12)),
        ("ends 1/2/27", eod(2027, 1, 2)),                      # 2-digit year, EST offset
        ("Oct 12", eod(2026, 10, 12)),
        ("through October 12th, 2026", eod(2026, 10, 12)),
        ("ends Dec 1", eod(2026, 12, 1)),
        ("while supplies last", ""),
        ("whenever", ""),
        ("13/45", ""),                                          # impossible date
    ])
    def test_table(self, raw, expected):
        assert resolve_expires(raw, TZ, NOW) == expected

    def test_tz_matters(self):
        # Same wording, different tz -> different UTC instant
        east = resolve_expires("ends today", "America/New_York", NOW)
        west = resolve_expires("ends today", "America/Los_Angeles", NOW)
        assert east != west


# -------------------------------------------------------------- parse_posts

def good_deal(final_quote: str = "now $10.00") -> LLMDeal:
    return LLMDeal(
        item="Widget", store="Amazon", final_price=10.0,
        evidence=LLMEvidence(final_price_quote=final_quote),
    )


class TestParsePosts:
    def test_happy_path_single_post(self):
        parser = FakeParser(results=[[good_deal()]], spend_usd=0.05)
        posts = [make_post()]
        pairs, stats = parse_posts(posts, parser, make_cfg(), NOW, {})
        assert len(pairs) == 1
        post, deal = pairs[0]
        assert post is posts[0]
        assert deal.item == "Widget"
        assert deal.source_url == post.url
        assert stats == {
            "posts_sent": 1, "deals_extracted": 1, "deals_dropped_evidence": 0,
            "second_opinions": 0, "spend_usd": 0.05,
            "aborted_spend_cap": False,
            "processed_hashes": [posts[0].content_hash],
        }

    def test_parser_gets_the_cleaned_text_and_meta(self):
        parser = FakeParser()
        post = make_post(text="a    b\n\nc", title="T")
        parse_posts([post], parser, make_cfg(), NOW, {})
        text, meta = parser.calls[0]
        assert text == "a b\nc"
        assert meta == {"source_name": "hip2save",
                        "source_url": post.url, "title": "T"}

    def test_max_posts_per_run_enforced_and_logged(self, caplog):
        parser = FakeParser(results=[[], [], []])
        posts = [make_post(uid=f"p{i}") for i in range(3)]
        with caplog.at_level("WARNING", logger="dealradar.parse.extractor"):
            _, stats = parse_posts(posts, parser, make_cfg(max_posts_per_run=2),
                                   NOW, {})
        assert len(parser.calls) == 2
        assert stats["posts_sent"] == 2
        assert any("dropping 1 of 3" in r.message for r in caplog.records)

    def test_spend_cap_stops_immediately(self):
        parser = FakeParser(results=[[good_deal()], [good_deal()]],
                            raise_cap_at=1, spend_usd=0.5)
        posts = [make_post(uid="p1"), make_post(uid="p2"), make_post(uid="p3")]
        pairs, stats = parse_posts(posts, parser, make_cfg(), NOW, {})
        assert stats["aborted_spend_cap"] is True
        assert stats["posts_sent"] == 1
        assert len(pairs) == 1
        assert len(parser.calls) == 1  # third post never attempted either
        assert stats["spend_usd"] == 0.5

    def test_evidence_failures_dropped_and_counted(self, caplog):
        bogus = LLMDeal(item="Ghost", store="Nowhere", final_price=99.99,
                        evidence=LLMEvidence(final_price_quote="only $99.99"))
        parser = FakeParser(results=[[good_deal(), bogus]])
        with caplog.at_level("WARNING", logger="dealradar.parse.extractor"):
            pairs, stats = parse_posts([make_post()], parser, make_cfg(), NOW, {})
        assert len(pairs) == 1
        assert pairs[0][1].item == "Widget"
        assert stats["deals_extracted"] == 2
        assert stats["deals_dropped_evidence"] == 1
        assert any("evidence check failed" in r.message for r in caplog.records)
        # The post keeps every deal the LLM read, the dropped one too: its
        # name still competes for the post's photos, its link still counts
        # as shared (dedupe, pipeline). Nothing of it is stored.
        assert pairs[0][0].parsed == [("Widget", ""), ("Ghost", "")]

    def test_evidence_verified_against_same_cleaned_truncated_text(self):
        # The quote exists in the raw text but is beyond the truncation cap, so
        # verification against the cleaned text sent to the model must fail.
        body = ("filler " * 1000).strip() + " now $10.00"
        deal = good_deal(final_quote="now $10.00")
        parser = FakeParser(results=[[deal]])
        cfg = make_cfg(max_chars_per_post=100)
        pairs, stats = parse_posts([make_post(text=body)], parser, cfg, NOW, {})
        assert pairs == []
        assert stats["deals_dropped_evidence"] == 1
        assert "now $10.00" not in parser.calls[0][0]

    def test_tz_by_source_drives_expiry_resolution(self):
        deal = good_deal()
        deal.expires_at_raw = "ends today"
        parser = FakeParser(results=[[deal]])
        tz_by_source = {"hip2save": "America/Los_Angeles"}
        pairs, _ = parse_posts([make_post()], parser, make_cfg(), NOW, tz_by_source)
        assert pairs[0][1].expires_at == util.iso(
            util.end_of_day("America/Los_Angeles", NOW))

    def test_roundup_post_yields_three_deals(self):
        text = (FIXTURES / "parse_roundup_post.txt").read_text()
        post = make_post(text=text, title="Weekend Roundup")
        roundup = [
            LLMDeal(item="Anker 735 USB-C Charger", store="Amazon",
                    regular_price=29.99, final_price=14.99, codes=["ANKER50"],
                    online_only="true",
                    evidence=LLMEvidence(regular_price_quote="was $29.99",
                                         final_price_quote="now only $14.99")),
            LLMDeal(item="LEGO City Starter Set", store="Walmart",
                    regular_price=50.00, final_price=25.00, pct_off=50.0,
                    expires_at_raw="through 10/12",
                    evidence=LLMEvidence(regular_price_quote="(reg. $50.00)",
                                         final_price_quote="now $25.00",
                                         pct_quote="50% off")),
            LLMDeal(item="Crest 3D White Toothpaste 3-pack", store="Target",
                    regular_price=12.99, final_price=8.99,
                    final_price_unit="pack", expires_at_raw="Ends today",
                    evidence=LLMEvidence(regular_price_quote="normally $12.99",
                                         final_price_quote="$8.99 for the pack")),
        ]
        parser = FakeParser(results=[roundup])
        pairs, stats = parse_posts([post], parser, make_cfg(), NOW, {})
        assert len(pairs) == 3
        assert stats["posts_sent"] == 1
        assert stats["deals_extracted"] == 3
        assert stats["deals_dropped_evidence"] == 0
        stores = [d.store for _, d in pairs]
        assert stores == ["Amazon", "Walmart", "Target"]
        # every deal carries source credit from the single source post
        assert {d.source_url for _, d in pairs} == {post.url}
        # expiry wording resolved per deal
        assert pairs[1][1].expires_at == eod(2026, 10, 12)
        assert pairs[2][1].expires_at == util.iso(util.end_of_day(TZ, NOW))
