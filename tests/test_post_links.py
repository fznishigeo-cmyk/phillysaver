"""Offline Amazon post-link recovery using the real polite fetcher."""
import time
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from dealradar import pipeline, util
from dealradar.config import AppConfig
from dealradar.db import DB
from dealradar.http_client import PoliteFetcher
from tests.test_pipeline_sources import TestProductImageBackfill as PhotoFixture
from tests.test_short_links import credit

A = "B0GLP6BWD9"
B = "B012345678"


def anchor(asin, text="Buy now"):
    return f'<a href="https://www.amazon.com/gp/product/{asin}?tag=credit">{text}</a>'


@pytest.fixture
def setup(tmp_path):
    db = DB(tmp_path / "state.db")
    cfg = AppConfig()
    cfg.max_retries = 0
    pages, calls, blocked = {}, [], set()

    def handler(request):
        if request.url.path == "/robots.txt":
            policy = "Disallow: /" if request.url.host in blocked else "Allow: /"
            return httpx.Response(200, text=f"User-agent: *\n{policy}\n")
        calls.append((request.method, str(request.url)))
        value = pages.get(str(request.url), "")
        if isinstance(value, Exception):
            raise value
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, text=value)

    fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handler),
                            sleep_fn=lambda _: None)
    yield db, cfg, fetcher, pages, calls, blocked
    fetcher.close()
    db.close()


def seed(db, key, post="https://deals.example/post", **kw):
    PhotoFixture._seed(db, key, kw.pop("link", ""), **kw)
    db.update_deal(key, payload={**db.get_deal(key), "store": "Amazon"})
    db.conn.execute("UPDATE deals SET store='Amazon' WHERE dedupe_key=?", (key,))
    db.conn.commit()
    credit(db, key, post)


def run(setup, **kw):
    db, cfg, fetcher, *_ = setup
    return pipeline._links_from_posts(db, fetcher, None, cfg,
                                      deals=db.all_deals(), **kw)


def test_single_product_and_one_attempt(setup):
    db, cfg, fetcher, pages, calls, _ = setup
    seed(db, "candy", flags=["image_backfill_tried", "product_image_tried", "unknown_shipping"])
    pages["https://deals.example/post"] = anchor(A) + anchor(A, "Another buy link")
    stats = {}
    assert run(setup, stats=stats) == 1
    row = db.get_deal("candy")
    assert row["asin"] == A
    assert row["canonical_url"] == f"https://www.amazon.com/dp/{A}"
    assert row["dedupe_key"] == "candy"
    assert row["flags"] == ["unknown_shipping", "post_link_tried"]
    assert stats == {"tried": 1, "found": 1}
    assert run(setup) == 0
    assert calls == [("GET", "https://deals.example/post")]
    # Page content never enters persisted facts.
    assert "Another buy link" not in db.conn.execute("SELECT payload FROM deals").fetchone()[0]


@pytest.mark.parametrize("text,other,expected", [
    ("Nature Made Vitamin D3", "Potato Chips", A),
    ("Nature Made Vitamin", "Potato Chips", A),  # exactly 60% of five words
    ("Nature Made", "Potato Chips", ""),
    ("Nature Made Vitamin D3", "Nature Made Vitamin D3", ""),
])
def test_roundup_unique_best_and_threshold(setup, text, other, expected):
    db, _, _, pages, *_ = setup
    seed(db, "Nature Made Vitamin D3 100")
    pages["https://deals.example/post"] = anchor(A, f"<b>{text}</b>") + anchor(B, other)
    assert run(setup) == bool(expected)
    row = db.get_deal("Nature Made Vitamin D3 100")
    assert row.get("asin", "") == expected
    assert bool(row["canonical_url"]) == bool(expected)
    assert row["flags"] == ["post_link_tried"]


def test_shared_post_matches_each_deal_and_fetches_once(setup):
    db, _, _, pages, calls, _ = setup
    seed(db, "Potato Chips")
    seed(db, "Vitamin D3")
    pages["https://deals.example/post"] = anchor(A, "Potato Chips") + anchor(B, "Vitamin D3")
    stats = {}
    assert run(setup, stats=stats) == 2
    assert stats == {"tried": 2, "found": 2}
    assert db.get_deal("Potato Chips")["asin"] == A
    assert db.get_deal("Vitamin D3")["asin"] == B
    assert calls == [("GET", "https://deals.example/post")]


@pytest.mark.parametrize("host", ["amzn.to", "a.co"])
def test_short_link_resolves_without_fetching_amazon(setup, host):
    db, _, _, pages, calls, _ = setup
    seed(db, "candy")
    pages["https://deals.example/post"] = f'<a href="https://{host}/abc">Buy</a>' * 2
    pages[f"https://{host}/abc"] = httpx.Response(302, headers={
        "Location": f"https://www.amazon.co.uk/dp/{A}?tag=credit"})
    assert run(setup) == 1
    assert db.get_deal("candy")["canonical_url"] == f"https://www.amazon.com/dp/{A}"
    assert calls == [("GET", "https://deals.example/post"), ("HEAD", f"https://{host}/abc")]


def test_robots_block_flags_without_fetching_post(setup):
    db, _, _, _, calls, blocked = setup
    blocked.add("deals.example")
    seed(db, "candy")
    assert run(setup) == 0
    assert calls == []
    assert db.get_deal("candy")["flags"] == ["post_link_tried"]


@pytest.mark.parametrize("failure", [httpx.Response(404), httpx.Response(503),
                                     httpx.ReadTimeout("timeout")])
def test_failed_post_is_tried_once(setup, failure):
    db, _, _, pages, calls, _ = setup
    seed(db, "candy")
    pages["https://deals.example/post"] = failure
    assert run(setup) == 0
    assert db.get_deal("candy")["flags"] == ["post_link_tried"]
    assert run(setup) == 0
    assert len(calls) == 1


def test_cap_newest_first_and_cached_posts_after_cap(setup):
    db, _, _, pages, calls, _ = setup
    now = util.now_utc()
    for i in range(45):
        post = f"https://deals.example/{i}"
        seed(db, str(i), post)
        db.conn.execute("UPDATE deals SET first_seen_at=? WHERE dedupe_key=?",
                        (util.iso(now + timedelta(minutes=i)), str(i)))
        pages[post] = anchor(A)
    # The oldest deal shares the newest deal's post and needs no extra fetch.
    seed(db, "shared", "https://deals.example/44")
    db.conn.execute("UPDATE deals SET first_seen_at=? WHERE dedupe_key='shared'",
                    (util.iso(now - timedelta(days=1)),))
    db.conn.commit()
    stats = {}
    assert run(setup, stats=stats) == 41
    assert calls == [("GET", f"https://deals.example/{i}") for i in range(44, 4, -1)]
    assert stats == {"tried": 41, "found": 41}
    assert db.get_deal("4")["flags"] == []
    assert run(setup) == 5
    assert run(setup) == 0


def test_skips_ineligible_and_non_site_deals(setup):
    db, cfg, fetcher, pages, calls, _ = setup
    seed(db, "photo", image="https://images.example/product.jpg")
    seed(db, "asin", link=f"https://www.amazon.com/dp/{A}")
    db.update_deal("asin", payload={**db.get_deal("asin"), "asin": A})
    seed(db, "tried", flags=["post_link_tried"])
    seed(db, "dead", died_at=util.iso(util.now_utc()))
    seed(db, "retailer", link="https://walmart.com/ip/123")
    db.update_deal("retailer", payload={**db.get_deal("retailer"), "store": "Walmart"})
    db.conn.execute("UPDATE deals SET store='Walmart' WHERE dedupe_key='retailer'")
    db.conn.commit()
    seed(db, "off-site")
    assert pipeline._links_from_posts(db, fetcher, None, cfg,
        deals=[d for d in db.all_deals() if d["dedupe_key"] != "off-site"]) == 0
    assert calls == []
    assert db.get_deal("off-site")["flags"] == []


def test_amazon_canonical_without_asin_is_eligible(setup):
    db, _, _, pages, *_ = setup
    seed(db, "candy", link="https://www.amazon.com/s?k=candy")
    pages["https://deals.example/post"] = anchor(A)
    assert run(setup) == 1
    assert db.get_deal("candy")["asin"] == A


def test_deadline_preserves_unattempted_deal(setup):
    db, cfg, fetcher, _, calls, _ = setup
    seed(db, "candy")
    assert pipeline._links_from_posts(db, fetcher, time.monotonic() - 1, cfg,
                                      deals=db.all_deals()) == 0
    assert calls == []
    assert db.get_deal("candy")["flags"] == []


def test_official_feed_only_policy_is_preserved(setup):
    db, _, _, _, calls, _ = setup
    seed(db, "candy", "https://slickdeals.net/post")
    assert run(setup) == 0
    assert calls == []
    assert db.get_deal("candy")["flags"] == ["post_link_tried"]


def test_pipeline_fast_only_summary_and_keepa_order(tmp_path, monkeypatch):
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "SMTP_HOST", "DEALS_CLAUDE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text((Path(__file__).resolve().parents[1] / "config.yaml").read_text().replace(
        'site_dir: "site"', f'site_dir: "{tmp_path / "site"}"'))
    db_path = tmp_path / "state.db"
    db = DB(db_path)
    seed(db, "candy")
    db.close()
    calls, checked = [], []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        calls.append(str(request.url))
        return httpx.Response(200, text=anchor(A))

    def keepa(db, cfg, now, **kw):
        checked.append(db.get_deal("candy").get("asin", ""))
        return {}

    monkeypatch.setattr(pipeline, "_recheck_prices", keepa)

    def invoke(fast):
        return pipeline.run(fast_lane=fast, db_path=str(db_path), config_path=str(cfg_path),
                            transport=httpx.MockTransport(handler), sources_override=[],
                            skip_validation=True, dry_run_telegram=True)

    assert invoke(False)["post_links"] == {"tried": 0, "found": 0}
    assert calls == [] and checked == [""]
    assert invoke(True)["post_links"] == {"tried": 1, "found": 1}
    assert checked[-1] == A
    assert invoke(True)["post_links"] == {"tried": 0, "found": 0}
    assert calls == ["https://deals.example/post"]


def test_a_roundup_follows_at_most_ten_short_links(setup):
    """Each short link is a polite 1 s hop: a roundup's dozens are capped."""
    db, _, _, pages, calls, _ = setup
    seed(db, "candy")
    pages["https://deals.example/post"] = "".join(
        f'<a href="https://amzn.to/l{i}">Item {i}</a>' for i in range(25))
    run(setup)
    assert sum(1 for _, url in calls if "amzn.to" in url) == pipeline._POST_SHORT_LINKS == 10
