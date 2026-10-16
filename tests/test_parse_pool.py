"""Offline scheduling contracts and facts captured from the pre-task-235 loop."""

import dataclasses
import hashlib
import json
import threading
from concurrent.futures import FIRST_COMPLETED, Future, wait as real_wait
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from dealradar.db import DB
from dealradar.errors import SpendCapReached
from dealradar.parse import extractor
from dealradar.parse.claude_client import ClaudeParser
from tests.test_claude_client import FakeCreateResponse, FakeUsage
from tests.test_extractor import NOW, good_deal, make_cfg, make_post


class ScheduledRead(Future):
    def result(self, timeout=None):
        if not self.done():
            self.pool.advance(self.finish_at)
        return super().result(timeout)


class ClockPool:
    """An executor whose independent reads finish at specified fake times.

    Future.result also advances time, so the original batch loop can run on
    this clock without a second implementation of the scheduler under test.
    """

    def __init__(self, monkeypatch, delays, on_submit=None):
        self.now = 0.
        self.delays = delays
        self.on_submit = on_submit
        self.pending = []
        self.submitted = []
        self.finished = []
        monkeypatch.setattr(extractor, "ThreadPoolExecutor", self.executor)
        monkeypatch.setattr(extractor.time, "monotonic", lambda: self.now)
        monkeypatch.setattr(extractor, "wait", self.wait, raising=False)

    def executor(self, max_workers):
        self.workers = max_workers
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.pending:
            self.advance(max(f.finish_at for f in self.pending))

    def submit(self, fn, post, cleaned):
        assert len(self.pending) < self.workers
        read = ScheduledRead()
        read.pool = self
        read.finish_at = self.now + self.delays.get(post.uid, 1)
        read.run = lambda: fn(post, cleaned)
        read.uid = post.uid
        self.pending.append(read)
        self.submitted.append((post.uid, self.now, len(self.pending)))
        if self.on_submit:
            self.on_submit(self)
        return read

    def advance(self, until):
        for read in sorted(list(self.pending), key=lambda f: f.finish_at):
            if read.finish_at > until:
                break
            self.now = max(self.now, read.finish_at)
            self.pending.remove(read)
            try:
                result = read.run()
            except BaseException as error:
                read.set_exception(error)
            else:
                read.set_result(result)
            self.finished.append(read.uid)
        self.now = max(self.now, until)

    def wait(self, futures, *, return_when):
        assert return_when == FIRST_COMPLETED
        if not any(f.done() for f in futures):
            self.advance(min(f.finish_at for f in futures))
        return ({f for f in futures if f.done()}, {f for f in futures if not f.done()})


def posts(n, *, below_floor=False):
    price = 45 if below_floor else 10
    return [make_post(uid=f"p{i}", title=f"p{i}", url=f"https://one.example/p{i}",
                      text=f"Widget {i} was $50 now ${price}") for i in range(n)]


class ReplyParser:
    def __init__(self, cfg, *, capped=(), opinions=()):
        self.cfg = cfg
        self.capped = capped
        self.opinions = opinions
        self.calls = []
        self.spend_usd = 0.

    def extract(self, text, meta, *, kind="extract", model=None):
        key = meta["title"]
        if key in self.capped:
            raise SpendCapReached("offline cap")
        self.calls.append((key, kind))
        self.spend_usd += .001
        if key in self.opinions and kind == "extract":
            return []
        price = 45 if "now $45" in text else 10
        return [good_deal(f"now ${price}").model_copy(update={
            "item": f"Widget {key}", "final_price": float(price)})]


def fingerprint(post):
    return hashlib.sha256(extractor.clean_for_llm(post, 6000).encode()).hexdigest()


def legacy_outcome(tmp_path, monkeypatch, mode):
    """Fixed replies through the real spend client; no API or model discovery."""
    ClockPool(monkeypatch, {})
    cfg = make_cfg(parse_concurrency=3, prefilter_below_floor=mode, min_pct=40,
                   site_show_from_pct=40, second_opinion_model="sonnet",
                   parse_model="haiku", spend_pacing=False,
                   model_pricing_per_mtok={"default": {"input": .1, "output": .5},
                                          "sonnet": {"input": 2., "output": 10.}})
    inputs = posts(10)
    inputs[1] = dataclasses.replace(inputs[1], text=inputs[0].text)
    inputs[2].structured = good_deal("now $10").model_copy(
        update={"category": "home"}).model_dump(mode="json")
    inputs[3].text = "Cached Widget now $10"
    inputs[4].text = "Widget was $50 now $45"
    inputs[5].text = "Opinion Widget was $50 now $10"
    inputs[6].text = "A plain announcement"
    inputs[7].text = "Incomplete announcement"
    inputs[9] = dataclasses.replace(inputs[9], text=inputs[5].text)

    class Messages:
        def create(self, **kwargs):
            prompt = kwargs["messages"][0]["content"]
            deals = [good_deal("now $10")]
            if "was $50 now $45" in prompt:
                deals = [good_deal("now $45").model_copy(update={"final_price": 45.})]
            if "Opinion Widget" in prompt and kwargs["model"] != "sonnet":
                deals = []
            if "A plain announcement" in prompt or "Incomplete announcement" in prompt:
                deals = []
            if "Widget 8 " in prompt:
                deals += [good_deal("invented now $10")]
            return FakeCreateResponse(json.dumps({"deals": [d.model_dump(mode="json") for d in deals]}),
                                      FakeUsage(100, 50),
                                      stop_reason="refusal" if "Incomplete announcement" in prompt else "end_turn")

    monkeypatch.setattr("dealradar.util.now_utc", lambda: NOW)
    with closing(DB(tmp_path / "state.db")) as db:
        db.cache_post_parse(fingerprint(inputs[3]), [good_deal("now $10").model_dump(mode="json")], NOW)
        parser = ClaudeParser(cfg, client=SimpleNamespace(messages=Messages()))
        parser.bind_ledger(db, "task235-fixture")
        pairs, stats = extractor.parse_posts(inputs, parser, cfg, NOW, {}, db=db)
        stats = {k: v for k, v in stats.items() if k not in {"parse_seconds", "max_in_flight", "deadline_hit"}}
        result = {
            "pairs": [(p.uid, dataclasses.asdict(d)) for p, d in pairs],
            "stats": stats,
            "parsed": [(p.uid, p.parsed) for p in inputs],
            "cache": [dict(r) for r in db.conn.execute("SELECT * FROM post_parses ORDER BY rowid")],
            "second_opinion_yield": [dict(r) for r in db.conn.execute("SELECT * FROM second_opinion_yield ORDER BY rowid")],
            "spend_by_kind": parser.spend_by_kind,
            "extract_by_source": parser.extract_by_source,
            "spend_usd": parser.spend_usd,
            "ledger_spend_usd": db.spend_usd_for_day(NOW),
            "calls": [dict(r) for r in db.conn.execute(
                "SELECT model, kind, source_name, status, reserved_usd, cost_usd, input_tokens,"
                " output_tokens, cache_read_tokens, cache_write_tokens FROM llm_calls"
                " ORDER BY model, kind, reserved_usd")],
        }
        assert parser._reserved_usd == pytest.approx(0., abs=1e-15)
        assert db.conn.execute("SELECT COUNT(*) FROM llm_reservations").fetchone()[0] == 0
        assert not db.conn.execute("SELECT 1 FROM llm_calls WHERE status='reserved'").fetchone()
    return json.loads(json.dumps(result))


@pytest.mark.parametrize("mode", ["shadow", "on"])
def test_matches_legacy_facts_stats_cache_and_spend(tmp_path, monkeypatch, mode):
    reference = json.loads((Path(__file__).parent / "fixtures" / "parse_pool_legacy.json").read_text())
    assert legacy_outcome(tmp_path, monkeypatch, mode) == reference["outcomes"][mode]


@pytest.mark.parametrize("workers", [2, 3, 6])
@pytest.mark.parametrize("slow", [0, 1])
def test_fast_reads_refill_while_second_opinion_is_slow(monkeypatch, workers, slow):
    inputs = posts(workers + 5)
    clock = ClockPool(monkeypatch, {f"p{slow}": 30})
    cfg = make_cfg(parse_concurrency=workers, min_pct=40, second_opinion_model="sonnet")
    parser = ReplyParser(cfg, opinions={f"p{slow}"})
    recorded = []
    verify = extractor.verify_evidence

    def observe(deal, text):
        recorded.append((deal.source_url.rsplit("/", 1)[-1], clock.now))
        return verify(deal, text)

    monkeypatch.setattr(extractor, "verify_evidence", observe)
    pairs, stats = extractor.parse_posts(inputs, parser, cfg, NOW, {})
    assert [uid for uid, _, _ in clock.submitted] == [p.uid for p in inputs]
    assert clock.submitted[workers][1] == 1  # before the slow read finishes
    assert all(at < 30 for _, at, _ in clock.submitted)
    assert [uid for uid, _ in recorded] == [p.uid for p in inputs]
    assert all(at == 30 for _, at in recorded[slow:])
    assert [p.uid for p, _ in pairs] == [p.uid for p in inputs]
    assert stats["processed_hashes"] == [p.content_hash for p in inputs]
    assert stats["max_in_flight"] == workers
    assert stats["parse_seconds"] == 30
    assert stats["second_opinion"]["calls"] == 1
    assert not clock.pending


def test_shared_reads_span_the_window_and_do_not_take_slots(monkeypatch):
    inputs = posts(5)
    inputs[1].text = inputs[0].text
    inputs[4].text = inputs[2].text
    clock = ClockPool(monkeypatch, {"p0": 20})
    cfg = make_cfg(parse_concurrency=2)
    parser = ReplyParser(cfg)
    pairs, stats = extractor.parse_posts(inputs, parser, cfg, NOW, {})
    assert clock.submitted == [("p0", 0, 1), ("p2", 0, 2), ("p3", 1, 2)]
    assert [p.uid for p, _ in pairs] == [p.uid for p in inputs]
    assert stats["posts_sent"] == 3
    assert stats["reused_posts"] == stats["reused_deals"] == 2
    assert stats["max_in_flight"] == 2


def test_structured_cached_and_skipped_posts_do_not_take_slots(tmp_path, monkeypatch):
    inputs = posts(7)
    inputs[1].structured = good_deal("now $10").model_copy(
        update={"category": "home"}).model_dump(mode="json")
    inputs[3].text = "Widget was $50 now $45"
    inputs[5].text = inputs[0].text
    cfg = make_cfg(parse_concurrency=2, prefilter_below_floor="on")
    clock = ClockPool(monkeypatch, {"p0": 10, "p4": 10})
    with closing(DB(tmp_path / "state.db")) as db:
        db.cache_post_parse(fingerprint(inputs[2]), [good_deal("now $10").model_dump(mode="json")], NOW)
        parser = ReplyParser(cfg)
        pairs, stats = extractor.parse_posts(inputs, parser, cfg, NOW, {}, db=db)
        assert [p.uid for p, _ in pairs] == ["p0", "p1", "p2", "p4", "p5", "p6"]
        assert clock.submitted == [("p0", 0, 1), ("p4", 0, 2), ("p6", 10, 1)]
        assert stats["processed_hashes"] == [p.content_hash for p in inputs]
        assert stats["posts_sent"] == 3 and stats["reused_posts"] == 2
        assert stats["structured_parse"]["posts"] == 1
        assert stats["post_counts"][inputs[3].content_hash]["evaluation_gap"] == "skipped"


def test_deadline_mid_stream_drains_paid_reads_and_leaves_gaps(monkeypatch, caplog):
    inputs = posts(9, below_floor=True)
    clock = ClockPool(monkeypatch, {"p0": 10})
    cfg = make_cfg(parse_concurrency=3, prefilter_below_floor="shadow", request_timeout_s=2)
    pairs, stats = extractor.parse_posts(inputs, ReplyParser(cfg), cfg, NOW, {}, deadline=8)
    assert [uid for uid, _, _ in clock.submitted] == [p.uid for p in inputs[:7]]
    assert [p.uid for p, _ in pairs] == [p.uid for p in inputs[:7]]
    assert stats["processed_hashes"] == [p.content_hash for p in inputs[:7]]
    assert all(stats["post_counts"][p.content_hash]["evaluation_gap"] == "deadline" for p in inputs[7:])
    assert stats["parse_seconds"] == 10 and not clock.pending
    assert not stats["aborted_spend_cap"]
    assert "stopping parse with 2 posts left" in caplog.text


def test_deadline_is_checked_before_every_submit(monkeypatch):
    inputs = posts(4)
    clock = ClockPool(monkeypatch, {"p0": 10}, on_submit=lambda c: setattr(c, "now", 7))
    cfg = make_cfg(parse_concurrency=3, request_timeout_s=2)
    _, stats = extractor.parse_posts(inputs, ReplyParser(cfg), cfg, NOW, {}, deadline=8)
    assert clock.submitted == [("p0", 0, 1)]
    assert stats["processed_hashes"] == [inputs[0].content_hash]
    assert stats["max_in_flight"] == 1


def test_deadline_is_rechecked_after_cache_lookup(tmp_path, monkeypatch):
    inputs = posts(1, below_floor=True)
    clock = ClockPool(monkeypatch, {})
    cfg = make_cfg(parse_concurrency=3, prefilter_below_floor="shadow", request_timeout_s=2)
    with closing(DB(tmp_path / "state.db")) as db:
        def lookup(*args):
            clock.now = 7
            return None

        monkeypatch.setattr(db, "cached_post_parse", lookup)
        _, stats = extractor.parse_posts(inputs, ReplyParser(cfg), cfg, NOW, {}, deadline=8, db=db)
    assert clock.submitted == []
    assert stats["processed_hashes"] == []
    assert stats["max_in_flight"] == 0
    assert stats["post_counts"][inputs[0].content_hash]["evaluation_gap"] == "deadline"


def test_cap_finishing_during_cache_lookup_prevents_refill(tmp_path, monkeypatch):
    inputs = posts(4)
    clock = ClockPool(monkeypatch, {"p0": 10, "p1": 1})
    cfg = make_cfg(parse_concurrency=3)
    with closing(DB(tmp_path / "state.db")) as db:
        lookup = db.cached_post_parse

        def observe(key, *args):
            if key == fingerprint(inputs[2]):
                clock.advance(1)
            return lookup(key, *args)

        monkeypatch.setattr(db, "cached_post_parse", observe)
        pairs, stats = extractor.parse_posts(inputs, ReplyParser(cfg, capped={"p1"}), cfg, NOW, {}, db=db)
    assert [uid for uid, _, _ in clock.submitted] == ["p0", "p1"]
    assert [p.uid for p, _ in pairs] == ["p0"]
    assert stats["aborted_spend_cap"] and not clock.pending


def test_out_of_order_spend_cap_stops_refills_but_records_paid_reads(monkeypatch):
    inputs = posts(8, below_floor=True)
    inputs[3].text = inputs[0].text
    inputs[4].text = inputs[1].text
    clock = ClockPool(monkeypatch, {"p0": 10, "p1": 1, "p2": 2})
    cfg = make_cfg(parse_concurrency=3, prefilter_below_floor="shadow")
    parser = ReplyParser(cfg, capped={"p1"})
    pairs, stats = extractor.parse_posts(inputs, parser, cfg, NOW, {})
    assert [uid for uid, _, _ in clock.submitted] == ["p0", "p1", "p2"]
    assert [p.uid for p, _ in pairs] == ["p0", "p2", "p3"]
    assert stats["processed_hashes"] == [inputs[i].content_hash for i in (0, 2, 3)]
    assert stats["posts_sent"] == 2 and stats["reused_posts"] == 1
    assert stats["aborted_spend_cap"] and stats["spend_usd"] == .002
    assert all(stats["post_counts"][inputs[i].content_hash]["evaluation_gap"] == "spend_cap"
               for i in (1, 4, 5, 6, 7))
    assert stats["parse_seconds"] == 10 and not clock.pending


def test_real_pool_settles_reservations_after_out_of_order_cap(tmp_path, monkeypatch):
    inputs = posts(6)
    cfg = make_cfg(parse_concurrency=3, parse_model="haiku", spend_pacing=False,
                   spend_cap_usd_per_run=.011,
                   model_pricing_per_mtok={"default": {"input": .1, "output": .5}})
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    capped, second_finished = threading.Event(), threading.Event()
    attempted, paid = [], []

    class Messages:
        def create(self, **kwargs):
            index = 0 if "Widget 0 was" in kwargs["messages"][0]["content"] else 1
            paid.append(index)
            entered[index].set()
            assert release[index].wait(5)
            return FakeCreateResponse(json.dumps({"deals": [good_deal("now $10").model_dump(mode="json")]}),
                                      FakeUsage(100, 50))

    class Parser(ClaudeParser):
        def extract(self, text, meta, **kwargs):
            index = int(meta["title"][1:])
            if index in (1, 2):
                assert entered[index - 1].wait(5)
            attempted.append(index)
            try:
                return super().extract(text, meta, **kwargs)
            except SpendCapReached:
                capped.set()
                raise
            finally:
                if index == 1:
                    second_finished.set()

    def wait(futures, **kwargs):
        # The cap is observed while both paid calls are reserved. Let the
        # younger paid call settle first, keeping the recording head blocked.
        if capped.is_set():
            release[0 if second_finished.is_set() else 1].set()
        return real_wait(futures, **kwargs)

    monkeypatch.setattr(extractor, "wait", wait)
    monkeypatch.setattr("dealradar.util.now_utc", lambda: NOW)
    with closing(DB(tmp_path / "state.db")) as db:
        parser = Parser(cfg, client=SimpleNamespace(messages=Messages()))
        parser.bind_ledger(db, "task235-cap")
        try:
            pairs, stats = extractor.parse_posts(inputs, parser, cfg, NOW, {}, db=db)
        finally:
            for event in release:
                event.set()
        assert attempted == [0, 1, 2] and paid == [0, 1]
        assert [p.uid for p, _ in pairs] == ["p0", "p1"]
        assert stats["processed_hashes"] == [p.content_hash for p in inputs[:2]]
        assert stats["aborted_spend_cap"] and stats["max_in_flight"] == 3
        assert stats["spend_usd"] == db.spend_usd_for_day(NOW) == .00007
        assert parser._reserved_usd == pytest.approx(0., abs=1e-15)
        assert [r[0] for r in db.conn.execute("SELECT status FROM llm_calls")] == ["settled", "settled"]
        assert db.conn.execute("SELECT COUNT(*) FROM llm_reservations").fetchone()[0] == 0


def test_post_limit_preserves_unsubmitted_prefilter_gaps(monkeypatch):
    inputs = posts(6, below_floor=True)
    clock = ClockPool(monkeypatch, {})
    cfg = make_cfg(parse_concurrency=3, max_posts_per_run=2, prefilter_below_floor="shadow")
    _, stats = extractor.parse_posts(inputs, ReplyParser(cfg), cfg, NOW, {})
    assert [uid for uid, _, _ in clock.submitted] == ["p0", "p1"]
    assert stats["processed_hashes"] == [p.content_hash for p in inputs[:2]]
    assert all(stats["post_counts"][p.content_hash]["evaluation_gap"] == "max_posts" for p in inputs[2:])


@pytest.mark.parametrize("count", [0, 3])
def test_no_paid_reads_has_zero_in_flight(monkeypatch, count):
    inputs = posts(count, below_floor=True)
    clock = ClockPool(monkeypatch, {})
    cfg = make_cfg(parse_concurrency=3, prefilter_below_floor="on")
    _, stats = extractor.parse_posts(inputs, ReplyParser(cfg), cfg, NOW, {})
    assert stats["max_in_flight"] == stats["parse_seconds"] == 0
    assert stats["processed_hashes"] == [p.content_hash for p in inputs]
    assert clock.submitted == []
