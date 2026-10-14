"""Non-Amazon clocks, publication guards and permitted source rereads, offline."""

from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import logging
import subprocess
from pathlib import Path

import httpx
import pytest

from dealradar import dedupe, non_amazon_freshness as fresh, rank, savings, util, verify
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.http_client import PoliteFetcher
from dealradar.models import Evidence, ExtractedDeal, RawPost, SourceConfig
from dealradar.parse.extractor import clean_for_llm, parse_posts
from dealradar.parse.schema import LLMDeal
from dealradar.publish import price_labels, site, social
from tests.http_helpers import HostClock
from tests.test_store_prices import NOW, API, seed as store_seed


@pytest.fixture
def cfg(tmp_path):
    cfg = load_config()
    cfg.feature_flags.update(bestbuy_live=False, ebay_live=False, keepa=False)
    cfg.site_dir = str(tmp_path / "site")
    return cfg


@pytest.fixture
def db():
    db = DB(":memory:")
    yield db
    db.close()


def seed(db, cfg, *, key="one", hours=30, store="Walmart", price=20, source="partner", url=None, product_url=None):
    at = NOW - timedelta(hours=hours)
    url = url or "https://partner.test/posts/" + key
    text = f"Widget {key}. Price ${price:.2f}. Regular $100.00."
    post = RawPost(source, key, url, "Widget " + key, text, at, at)
    ext = ExtractedDeal("Widget " + key, store, url, source,
        product_url=product_url or "https://www.walmart.com/ip/" + key, final_price=price,
        regular_price=100, online_only="true", category="home",
        evidence=Evidence(final_price_quote=f"Price ${price:.2f}.", regular_price_quote="Regular $100.00."))
    result = dedupe.upsert_extracted(db, ext, savings.compute_savings(ext), post, cfg, at)
    deal = db.get_deal(result["dedupe_key"])
    deal.update(critic={"plausible": True, "confidence": 1}, image_url="https://images.test/widget.jpg")
    db.update_deal(result["dedupe_key"], payload=deal)
    return db.get_deal(result["dedupe_key"]), post, ext


def client(db, cfg, handler):
    clock = HostClock()
    calls = []

    def route(request):
        calls.append((clock(), request))
        return handler(request)

    return PoliteFetcher(db, cfg, transport=httpx.MockTransport(route),
                         clock=clock, sleep_fn=clock.sleep), calls


def source(**kw):
    return SourceConfig("partner", "rss", "https://partner.test/feed", site="https://partner.test",
                        **kw)


def reread(db, cfg, reader, sources=None, posts=(), now=NOW, run_key="run"):
    return fresh.source_rechecks(db, cfg, sources or [source(allowed_html=True)], posts,
                                 reader, now, run_key)


def test_buckets_and_api_paths(db, cfg):
    rows = [seed(db, cfg, key=str(i), hours=h)[0]
            for i, h in enumerate((0, 11.99, 12, 23.99, 24, 47.99, 48, 96, 96.01))]
    rows += [dict(rows[0], store="Amazon"), dict(rows[0], status="dead"), dict(rows[0], locked=True)]
    values = fresh.measure(rows, cfg, NOW)["Walmart"]
    assert [values[k] for k in fresh.BUCKETS] == [2, 2, 2, 2, 1]
    assert values["api_path"] == 0 and values["source_only"] == 9
    ebay = dict(rows[0], store="eBay", canonical_url="https://ebay.com/itm/123456789012")
    assert fresh.measure([ebay], cfg, NOW)["eBay"]["api_path"] == 1
    assert fresh.measure([dict(ebay, codes=["SAVE"])], cfg, NOW)["eBay"]["source_only"] == 1


def test_evidence_clock_ignores_attempts_wrong_prices_future_and_prior_prices(db, cfg):
    deal, _, _ = seed(db, cfg)
    deal.update(last_seen_at=util.iso(NOW), recheck_tried_at=util.iso(NOW))
    deal["sightings"].append(dict(seen_at=util.iso(NOW), final_price=21))
    assert fresh.age_h(deal, NOW) == 30
    deal["sightings"].append(dict(seen_at=util.iso(NOW + timedelta(hours=2)), final_price=20))
    assert fresh.age_h(deal, NOW) == 30
    deal["price_since"] = util.iso(NOW - timedelta(hours=1))
    assert fresh.hidden(deal, cfg, NOW)
    assert fresh.measure([deal], cfg, NOW)["Walmart"]["unknown"] == 1
    deal["price_rechecked_at"] = util.iso(NOW)
    assert fresh.hidden(deal, cfg, NOW)  # no official Walmart check path
    deal.update(store="Best Buy", canonical_url="https://bestbuy.com/site/widget/1234567.p")
    assert fresh.age_h(deal, NOW) == 0
    deal["gone_suspect_at"] = util.iso(NOW)
    assert fresh.hidden(deal, cfg, NOW)


@pytest.mark.parametrize("hours,hidden,aged", [(23.99, False, False), (24, False, True),
    (48, False, True), (48.01, True, True)])
def test_boundaries_never_kill_for_age(db, cfg, hours, hidden, aged):
    deal, _, _ = seed(db, cfg, hours=hours)
    assert fresh.hidden(deal, cfg, NOW) is hidden
    assert fresh.promotion_excluded(deal, cfg, NOW) is aged
    assert price_labels.push_excluded(deal, cfg, NOW) is aged
    assert bool(rank.site_deals(db, cfg, NOW)) is not hidden
    assert bool(rank.published_deals(db, cfg, NOW)) is not aged
    assert db.get_deal(deal["dedupe_key"])["status"] == "alive"


def test_store_override_and_switch(db, cfg):
    deal, _, _ = seed(db, cfg, hours=40)
    cfg.non_amazon_stores = {"wal mart": {"stale_hide_h": 36}}
    assert fresh.hidden(deal, cfg, NOW)
    cfg.non_amazon_freshness_enabled = False
    assert not fresh.hidden(deal, cfg, NOW)
    assert not fresh.promotion_excluded(deal, cfg, NOW)


@pytest.mark.parametrize("price", [20, 15])
def test_later_corroboration_refreshes_same_or_lower_price(db, cfg, price):
    deal, post, ext = seed(db, cfg, hours=60)
    post = replace(post, source_name="other", published_at=NOW, fetched_at=NOW,
                   text=f"Price ${price:.2f}. Regular $100.00.")
    ext = replace(ext, source_name="other", final_price=price,
                  evidence=Evidence(final_price_quote=f"Price ${price:.2f}.", regular_price_quote="Regular $100.00."))
    dedupe.upsert_extracted(db, ext, savings.compute_savings(ext), post, cfg, NOW)
    assert fresh.age_h(db.get_deal(deal["dedupe_key"]), NOW) == 0
    assert db.get_deal(deal["dedupe_key"])["final_price"] == price


def test_html_reread_parse_reprice_conditional_refresh_and_idempotency(db, cfg):
    deal, post, ext = seed(db, cfg)

    def handle(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        assert request.url.host == "partner.test"
        if request.headers.get("If-None-Match") == '"v2"':
            return httpx.Response(304)
        return httpx.Response(200, text="<article><h1>Widget one</h1><p>Price $25.00. Regular $100.00.</p></article>",
                              headers={"ETag": '"v2"'})

    fetcher, calls = client(db, cfg, handle)
    posts, counts = reread(db, cfg, fetcher)
    assert counts["attempted"] == 1 and posts[0].price_recheck
    fact = LLMDeal(item=ext.item, store=ext.store, product_url=ext.product_url,
                   final_price=25, regular_price=100, online_only="true", category="home",
                   evidence={"final_price_quote": "Price $25.00.", "regular_price_quote": "Regular $100.00."})
    # Existing parser cache stores facts only and is evidence-checked again.
    fingerprint = hashlib.sha256(clean_for_llm(posts[0], cfg.max_chars_per_post).encode()).hexdigest()
    db.cache_post_parse(fingerprint, [fact.model_dump(mode="json")], NOW)

    class NoCalls:
        def extract(self, *args, **kwargs):
            raise AssertionError("cached facts must not call an LLM")

    pairs, stats = parse_posts(posts, NoCalls(), cfg, NOW, {}, db=db)
    assert stats["reused_posts"] == 1
    for p, e in pairs:
        dedupe.upsert_extracted(db, e, savings.compute_savings(e), p, cfg, NOW)
    updated = db.get_deal(deal["dedupe_key"])
    assert updated["final_price"] == 25 and updated["pct_at_checkout"] == 75
    assert fresh.age_h(updated, NOW) == 0
    assert reread(db, cfg, fetcher)[0] == []
    assert len(calls) == 2 and calls[1][0] - calls[0][0] >= 1
    later = NOW + timedelta(hours=13)
    posts, stats = reread(db, cfg, fetcher, now=later, run_key="next")
    assert posts == [] and stats["attempted"] == 1
    assert fresh.age_h(db.get_deal(deal["dedupe_key"]), later) == 13  # a 304 proves nothing new
    assert "<article>" not in "\n".join(db.conn.iterdump())


def test_unchanged_reread_of_the_same_post_never_refreshes(db, cfg):
    # Operator, 2026-10-14: a four-day-old Walmart post still showed its old
    # price. The partner's unchanged post is not news about the store's price.
    deal, post, ext = seed(db, cfg, hours=30)
    reread_post = replace(post, title="Widget one", text="<p>Price $20.00. Regular $100.00.</p>",
                          published_at=NOW, fetched_at=NOW, price_recheck=True)
    assert reread_post.content_hash != post.content_hash
    dedupe.upsert_extracted(db, ext, savings.compute_savings(ext), reread_post, cfg, NOW)
    row = db.get_deal(deal["dedupe_key"])
    assert row["final_price"] == 20 and len(row["sightings"]) == 2
    assert fresh.age_h(row, NOW) == 30
    other = replace(post, source_name="other", url="https://other.test/p/1", published_at=NOW, fetched_at=NOW)
    dedupe.upsert_extracted(db, replace(ext, source_name="other"), savings.compute_savings(ext), other, cfg, NOW)
    assert fresh.age_h(db.get_deal(deal["dedupe_key"]), NOW) == 0  # a different post corroborates


@pytest.mark.parametrize("status", [304, 403, 429, 500])
def test_failure_or_unproven_304_never_refreshes(db, cfg, status):
    deal, _, _ = seed(db, cfg)
    fetcher, _ = client(db, cfg, lambda r: httpx.Response(404) if r.url.path == "/robots.txt"
                         else httpx.Response(status))
    reread(db, cfg, fetcher)
    assert fresh.age_h(db.get_deal(deal["dedupe_key"]), NOW) == 30


def test_robots_and_breaker_block_reads(db, cfg):
    seed(db, cfg)
    fetcher, calls = client(db, cfg, lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /"))
    assert reread(db, cfg, fetcher)[0] == []
    assert [r.url.path for _, r in calls] == ["/robots.txt"]
    assert db.all_source_health()[0]["consecutive_failures"] == 1
    for _ in range(cfg.circuit_breaker_failures):
        db.record_source_result("partner", ok=False, error="http_403", items=0, now=NOW,
                                trip_threshold=cfg.circuit_breaker_failures, cooldown_h=24)
    reread(db, cfg, fetcher, now=NOW + timedelta(hours=13), run_key="next")
    assert len(calls) == 1


def test_redirect_to_retailer_is_never_followed(db, cfg):
    seed(db, cfg)
    fetcher, calls = client(db, cfg, lambda r: httpx.Response(404) if r.url.path == "/robots.txt"
        else httpx.Response(302, headers={"Location": "https://www.walmart.com/ip/123"}))
    assert reread(db, cfg, fetcher)[0] == []
    assert {r.url.host for _, r in calls} == {"partner.test"}


@pytest.mark.parametrize("allowed", [False, True])
def test_slickdeals_rss_only_even_if_html_misconfigured(db, cfg, allowed):
    deal, post, _ = seed(db, cfg, source="slickdeals", url="https://slickdeals.net/f/123")
    src = SourceConfig("slickdeals", "rss", "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1",
                       site="https://slickdeals.net", allowed_html=allowed)
    fetcher, calls = client(db, cfg, lambda r: pytest.fail("present feed item requires no request"))
    posts, counts = reread(db, cfg, fetcher, [src], [replace(post, fetched_at=NOW)])
    assert posts[0].price_recheck and counts["posts"] == 1 and not calls
    assert posts[0].text == post.text


def test_feed_only_absent_item_does_not_fetch_html_or_refresh(db, cfg):
    deal, _, _ = seed(db, cfg)
    feed = '<rss version="2.0"><channel><item><title>Elsewhere</title><link>https://partner.test/elsewhere</link></item></channel></rss>'
    fetcher, calls = client(db, cfg, lambda r: httpx.Response(404) if r.url.path == "/robots.txt"
                          else httpx.Response(200, text=feed, headers={"ETag": '"feed"'}))
    posts, stats = reread(db, cfg, fetcher, [source(allowed_html=False)])
    assert posts == []
    assert [r.url.path for _, r in calls] == ["/robots.txt", "/feed"]
    assert fresh.age_h(db.get_deal(deal["dedupe_key"]), NOW) == 30


def test_disallowed_and_disabled_sources_cannot_be_read(db, cfg):
    seed(db, cfg)
    fetcher, calls = client(db, cfg, lambda r: pytest.fail("disallowed source"))
    for src in [replace(source(), method="html"), replace(source(), enabled=False)]:
        assert reread(db, cfg, fetcher, [src])[0] == []
    assert not calls


def test_recheck_bound_and_same_run_retry(db, cfg):
    cfg.non_amazon_source_rechecks_per_run = 2
    for i in range(4):
        seed(db, cfg, key=str(i))
    fetcher, calls = client(db, cfg, lambda r: httpx.Response(404) if r.url.path == "/robots.txt"
                          else httpx.Response(200, text="<h1>Widget</h1><p>Price $20.00.</p>"))
    assert reread(db, cfg, fetcher)[1]["attempted"] == 2
    # Entire run has a hard cap, including a resumed invocation.
    assert reread(db, cfg, fetcher)[1]["attempted"] == 0
    assert len(calls) == 3


@pytest.mark.parametrize("marker", ["expired", "sold out", "no longer available", "price went up", "deal is dead"])
def test_dead_marker_ends_source_only_deal(db, cfg, marker):
    deal, _, _ = seed(db, cfg)
    fetcher, _ = client(db, cfg, lambda r: httpx.Response(404) if r.url.path == "/robots.txt"
        else httpx.Response(200, text=f"<h1>Widget one — {marker}</h1><p>Price $20.00.</p>"))
    posts, _ = reread(db, cfg, fetcher)
    verify.mark_ended_post(db, posts[0], NOW, cfg)
    assert db.get_deal(deal["dedupe_key"])["status"] == "dead"
    assert not rank.site_deals(db, cfg, NOW)


@pytest.mark.parametrize("amazon_label", [True, False])
def test_publish_label_sort_hide_and_promotion_exclusions(db, cfg, monkeypatch, amazon_label):
    cfg.unconfirmed_label_enabled = amazon_label
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    rows = [seed(db, cfg, key=k, hours=h)[0] for k, h in (("aged", 30), ("old", 60), ("fresh", 1))]
    result = site.render_site(db, cfg, NOW, deals=rows)
    public = result["public_deals"]
    assert [d["item"] for d in public] == ["Widget fresh", "Widget aged"]
    aged = public[-1]
    assert aged["price_aged"] == "Posted 1 day ago" and not aged.get("pick") and "ps" not in aged
    html = (Path(cfg.site_dir) / "index.html").read_text()
    assert '<div class="price-aged">Posted 1 day ago</div>' in html
    best = (Path(cfg.site_dir) / "best-deals-today/index.html").read_text()
    assert "Widget aged" not in best and "Widget old" not in best
    assert all(d[0]["item"] != "Widget aged" for d in social._candidates(db, cfg, {}))
    assert len(rank.published_deals(db, cfg, NOW)) == 1
    assert len(db.all_deals(status="alive")) == 3
    subprocess.run(["node", "tests/non_amazon_freshness.cjs", cfg.site_dir], check=True, capture_output=True, text=True)


def test_report_before_after_and_private_snapshot(db, cfg):
    rows = [seed(db, cfg, key=k, hours=h)[0] for k, h in (("fresh", 1), ("aged", 30), ("old", 60))]
    before = fresh.measure(rows, cfg, NOW)
    value = fresh.record_snapshot(db, cfg, NOW, before, rows[:2] + [dict(rows[2], locked=True)], {})
    assert value["before"]["Walmart"]["48-96h"] == 1
    assert value["after"]["Walmart"]["48-96h"] == 0
    assert fresh.report_data(db) == value
    assert "before / Walmart" in "\n".join(fresh.summary_lines(value))
    empty = fresh.record_snapshot(db, cfg, NOW, before, [], {})
    assert empty["after"]["Walmart"]["total"] == 0
    assert "after / Walmart: 0 / 0 / 0 / 0 / 0 / 0; 0 / 0" in "\n".join(fresh.summary_lines(empty))


@pytest.mark.parametrize("status,code,expected", [(401, "invalid_client", "invalid_client"),
    (403, "secret-response-text", "other"), (500, "secret-response-text", "other")])
def test_token_diagnostics_are_allowlisted(db, cfg, monkeypatch, caplog, status, code, expected):
    for key in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"):
        monkeypatch.setenv(key, "private-" + key)
    cfg.feature_flags["ebay_live"] = True
    store_seed(db, cfg, "ebay")
    fetcher, _ = client(db, cfg, lambda r: httpx.Response(status, json={"error": code,
        "error_description": "secret-response-text private-EBAY_CLIENT_SECRET"}))
    from dealradar.store_prices import recheck_store_prices
    with caplog.at_level(logging.DEBUG):
        stats = recheck_store_prices(db, cfg, NOW, fetcher=fetcher)
    assert stats["ebay"]["diagnostics"][f"token:http_{status}:{expected}"] >= 1
    text = caplog.text + "\n".join(db.conn.iterdump())
    assert "secret-response-text" not in text and "private-EBAY_CLIENT" not in text


def test_api_every_run_daily_limit_and_retry_receipt(db, cfg, monkeypatch):
    from dealradar.store_prices import recheck_store_prices
    for key in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"):
        monkeypatch.setenv(key, "synthetic-" + key)
    cfg.feature_flags["ebay_live"] = True
    cfg.ebay_max_requests_per_day = 4
    store_seed(db, cfg, "ebay")
    api = API(db, cfg)
    assert recheck_store_prices(db, cfg, NOW, fetcher=api.fetcher, run_key="one")["ebay"]["checked"] == 1
    assert recheck_store_prices(db, cfg, NOW + timedelta(minutes=1), fetcher=api.fetcher, run_key="one")["ebay"]["checked"] == 0
    assert recheck_store_prices(db, cfg, NOW + timedelta(minutes=10), fetcher=api.fetcher, run_key="two")["ebay"]["checked"] == 1
    assert recheck_store_prices(db, cfg, NOW + timedelta(minutes=20), fetcher=api.fetcher, run_key="three")["ebay"]["checked"] == 0
    assert len(api.calls) == 4


def test_documented_before_after_table(db, cfg):
    urls = {"Walmart": "https://walmart.com/ip/{}", "Target": "https://target.com/p/-/A-{}",
            "Best Buy": "https://bestbuy.com/site/widget/{}.p", "eBay": "https://ebay.com/itm/{}",
            "Macy's": "https://macys.com/shop/product/widget?ID={}"}
    initial, latest = [], []
    for store, url in urls.items():
        for i, hours in enumerate((6, 18, 36, 60, 120)):
            deal, post, ext = seed(db, cfg, key=store + str(i), store=store, hours=hours,
                                  product_url=url.format(123456789012 + i))
            initial.append(deal)
            if hours == 120:
                post = replace(post, source_name="second_partner", fetched_at=NOW, published_at=NOW)
                dedupe.upsert_extracted(db, ext, savings.compute_savings(ext), post, cfg, NOW)
            latest.append(db.get_deal(deal["dedupe_key"]))
    before = fresh.measure(initial, cfg, NOW)
    after = fresh.measure([d for d in latest if not fresh.hidden(d, cfg, NOW)], cfg, NOW)
    for name in urls:
        assert [before[name][k] for k in fresh.BUCKETS] == [1, 1, 1, 1, 1]
        assert [after[name][k] for k in fresh.BUCKETS] == [2, 1, 1, 0, 0]
        assert before[name]["api_path"] == (5 if name in ("eBay", "Best Buy") else 0)
    assert len(db.all_deals(status="alive")) == 25


def test_api_failure_keeps_partner_fallback_and_source_marker_hides_suspect(db, cfg, monkeypatch):
    monkeypatch.setenv("EBAY_CLIENT_ID", "synthetic-client")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "synthetic-secret")
    cfg.feature_flags["ebay_live"] = True
    deal, _, _ = seed(db, cfg, store="eBay", product_url="https://ebay.com/itm/123456789012")
    fetcher, _ = client(db, cfg, lambda r: httpx.Response(404) if r.url.path == "/robots.txt"
        else httpx.Response(200, text="<h1>Widget one sold out</h1>"))
    posts, _ = reread(db, cfg, fetcher)
    assert posts
    verify.mark_ended_post(db, posts[0], NOW, cfg)
    row = db.get_deal(deal["dedupe_key"])
    assert row["status"] == "alive" and row["gone_suspect_at"]
    assert not rank.site_deals(db, cfg, NOW)


def test_unrelated_roundup_end_does_not_kill_another_deal(db, cfg):
    url = "https://partner.test/posts/roundup"
    for key in ("one", "two"):
        seed(db, cfg, key=key, url=url)
    post = RawPost("partner", url, url, "One deal is dead", "Price $20.00.", NOW, NOW)
    verify.mark_ended_post(db, post, NOW, cfg)
    assert len(db.all_deals(status="alive")) == 2


def test_article_status_without_changed_title_ends_offer(db, cfg):
    deal, _, _ = seed(db, cfg)
    fetcher, _ = client(db, cfg, lambda r: httpx.Response(404) if r.url.path == "/robots.txt"
        else httpx.Response(200, text="<article><h1>Widget one</h1><p>Expired</p><p>Price $20.00.</p></article>"))
    posts, _ = reread(db, cfg, fetcher)
    verify.mark_ended_post(db, posts[0], NOW, cfg)
    assert db.get_deal(deal["dedupe_key"])["status"] == "dead"


def test_recheck_cannot_fetch_retailer_html_or_feed(db, cfg):
    seed(db, cfg, url="https://walmart.com/ip/one")
    src = source(allowed_html=True)
    src.site, src.url = "https://walmart.com", "https://walmart.com/feed"
    fetcher, calls = client(db, cfg, lambda r: pytest.fail("retailer request"))
    assert reread(db, cfg, fetcher, [src])[0] == []
    assert not calls


def test_before_snapshot_uses_actual_public_set(db, cfg):
    shown, _, _ = seed(db, cfg, key="shown", hours=36)
    seed(db, cfg, key="never_public", hours=60)
    baseline, scope = fresh.before_snapshot(db, cfg, NOW)
    assert "unavailable" in scope and baseline["Walmart"]["total"] == 2
    fresh.record_snapshot(db, cfg, NOW, baseline, [shown], {})
    before, scope = fresh.before_snapshot(db, cfg, NOW)
    assert scope == "previous publish" and before["Walmart"]["total"] == 1


def test_new_bestbuy_url_has_official_path():
    from dealradar.store_prices import store_offer
    assert store_offer({"canonical_url": "https://www.bestbuy.com/product/widget/ABC/sku/6685669"}) == ("bestbuy", "6685669")


def test_hidden_alive_offer_loses_old_detail_page_and_can_reappear(db, cfg, monkeypatch):
    deal, post, ext = seed(db, cfg, hours=47)
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    first = site.render_site(db, cfg, NOW)
    path = Path(cfg.site_dir) / first["public_deals"][0]["deal_path"].strip("/") / "index.html"
    assert path.exists()
    later = NOW + timedelta(hours=2)
    monkeypatch.setattr(site, "util_now", lambda: later)
    second = site.render_site(db, cfg, later)
    assert second["published"] == 0 and not path.exists()
    assert db.get_deal(deal["dedupe_key"])["status"] == "alive"
    post = replace(post, source_name="other", fetched_at=later, published_at=later)
    dedupe.upsert_extracted(db, ext, savings.compute_savings(ext), post, cfg, later)
    assert site.render_site(db, cfg, later)["published"] == 1 and path.exists()


def test_deadline_does_not_trip_unattempted_sources(db, cfg):
    seed(db, cfg)
    fetcher, calls = client(db, cfg, lambda r: pytest.fail("no time remains"))
    posts, counts = fresh.source_rechecks(db, cfg, [source(allowed_html=True)], [], fetcher,
                                         NOW, "deadline", deadline=0)
    assert not posts and not calls and not counts["attempted"] and not db.all_source_health()


def test_confirmed_gone_card_keeps_its_existing_display(db, cfg, monkeypatch):
    monkeypatch.setattr(site, "util_now", lambda: NOW)
    rows = [seed(db, cfg, key=str(i), hours=1)[0] for i in range(10)]
    gone = dict(rows[-1], status="dead", death="confirmed", died_at=util.iso(NOW))
    result = site.render_site(db, cfg, NOW, deals=rows[:-1] + [gone])
    public = next(d for d in result["public_deals"] if d["status"] == "dead")
    assert public["gone"] and not public.get("price_aged")
