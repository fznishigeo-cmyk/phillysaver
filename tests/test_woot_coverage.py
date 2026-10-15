"""Woot coverage switches and the metadata-only probe, without network access."""

import ast
from contextlib import closing
import json
import logging
from pathlib import Path

import httpx
import pytest
import yaml

from dealradar import pipeline, util
from dealradar.config import AppConfig, load_config
from dealradar.db import DB
from dealradar.errors import DeadlineExceeded
from dealradar.fetch import woot
from dealradar.http_client import PoliteFetcher
from tests.http_helpers import HostClock
from tests.test_e2e_offline import _FakeClaudeClient
from tests.test_non_amazon_freshness import seed, NOW as FRESH_NOW
from tests.test_product_offer_pages import tree
from tests.test_woot import NOW, SRC, _offer


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("WOOT_API_KEY", "synthetic-woot-secret")
    return load_config()


@pytest.fixture
def db():
    database = DB(":memory:")
    yield database
    database.close()


def reader(db, cfg, monkeypatch, responses):
    clock, calls = HostClock(), []
    monkeypatch.setattr(woot.time, "monotonic", clock)

    def handle(request):
        calls.append((clock(), request))
        assert request.headers["x-api-key"] == "synthetic-woot-secret"
        assert "hello@wowpriced.com" in request.headers["User-Agent"]
        assert request.url.host == "developer.woot.com"
        assert "synthetic-woot-secret" not in str(request.url)
        response = responses[(request.url.path.rsplit("/", 1)[-1],
                              int(request.url.params.get("page", 1)))]
        return response if isinstance(response, httpx.Response) else httpx.Response(200, json=response)

    client = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handle),
                           clock=clock, sleep_fn=clock.sleep)
    return client, clock, calls


@pytest.mark.parametrize("last", [[], [_offer("c", 20, 100)]])
def test_paging_stops_at_empty_or_short_page(cfg, db, monkeypatch, last):
    cfg.woot_feed_pages = 10
    client, clock, calls = reader(db, cfg, monkeypatch, {
        ("All", 1): {"Items": [_offer("a", 20, 100), _offer("b", 20, 100)]},
        ("All", 2): {"Offers": last},
    })
    with closing(client):
        posts = woot.WootAdapter().fetch_posts(SRC, client, db, cfg, NOW, None)
    assert [p.uid for p in posts] == ["a", "b"] + [o["OfferId"] for o in last]
    assert len(calls) == 2 and clock.sleeps == [1.0]


def test_paging_cap_and_dedup_across_feeds(cfg, db, monkeypatch, caplog):
    cfg.woot_feed_pages = 2
    cfg.woot_named_feeds = ["All", "Home", "Home", "home"]
    a, b, c, d = [_offer(uid, 20, 100) for uid in "abcd"]
    client, _, calls = reader(db, cfg, monkeypatch, {
        ("All", 1): [a, b],
        ("All", 2): [dict(b, Url=b["Url"] + "?ref=page2"), c],
        ("Home", 1): [a, b],
        # A wholly overlapping named first page must not stop its next page.
        ("Home", 2): [dict(c, OfferId="alias"), d],
    })
    with closing(client), caplog.at_level(logging.INFO, logger=woot.log.name):
        posts = woot.WootAdapter().fetch_posts(SRC, client, db, cfg, NOW, None)
    assert [p.uid for p in posts] == list("abcd")
    assert len(calls) == 4
    assert all(b[0] - a[0] >= 1 for a, b in zip(calls, calls[1:]))
    assert "woot: 4 raw offers -> 4 scored" in caplog.text


def test_ignored_paging_parameter_stops_repeated_page(cfg, db, monkeypatch):
    cfg.woot_feed_pages = 9
    payload = [_offer("a", 20, 100)]
    client, _, calls = reader(db, cfg, monkeypatch, {
        ("All", 1): payload, ("All", 2): payload})
    with closing(client):
        posts = woot.WootAdapter().fetch_posts(SRC, client, db, cfg, NOW, None)
    assert len(calls) == 2 and len(posts) == 1


@pytest.mark.parametrize("deadline,expected", [(0, 0), (0.5, 1), (1.5, 2)])
def test_deadline_keeps_completed_pages_and_never_starts_late(
        cfg, db, monkeypatch, deadline, expected):
    cfg.woot_feed_pages = 5
    cfg.woot_named_feeds = ["Home"]
    client, _, calls = reader(db, cfg, monkeypatch, {
        ("All", i): [_offer(str(i), 20, 100)] for i in range(1, 6)})
    with closing(client):
        if expected == 0:
            with pytest.raises(DeadlineExceeded):
                woot.WootAdapter().fetch_posts(SRC, client, db, cfg, NOW, deadline)
        else:
            posts = woot.WootAdapter().fetch_posts(SRC, client, db, cfg, NOW, deadline)
            assert len(posts) == expected
    assert len(calls) == expected and all(at < deadline for at, _ in calls)


@pytest.mark.parametrize("single_price", [True, False])
def test_later_pages_reconfirm_even_beyond_selection_cap(cfg, db, monkeypatch, single_price):
    from dealradar.non_amazon_freshness import hidden

    cfg.woot_feed_pages = 2
    cfg.woot_max_items_per_run = cfg.woot_max_new_items_per_run = 0
    deal, _, _ = seed(db, cfg, store="Woot", hours=60,
                      product_url="https://www.woot.com/offers/later")
    missing, _, _ = seed(db, cfg, key="missing", store="Woot", hours=60,
                         product_url="https://www.woot.com/offers/absent")
    assert hidden(deal, cfg, FRESH_NOW)
    later = _offer("later", 20 if single_price else [20, 21], 100,
                   EndDate="2026-12-01T00:00:00Z")
    client, _, calls = reader(db, cfg, monkeypatch, {
        ("All", 1): [_offer("first", 20, 100)], ("All", 2): [later]})
    with closing(client):
        assert woot.WootAdapter().fetch_posts(SRC, client, db, cfg, FRESH_NOW, None) == []
    row = db.get_deal(deal["dedupe_key"])
    assert not hidden(row, cfg, FRESH_NOW)
    assert row["woot_listed_at"] == util.iso(FRESH_NOW)
    assert bool(row.get("price_rechecked_at")) is single_price
    assert db.get_deal(missing["dedupe_key"]) == missing
    assert len(calls) == 2


def test_extra_page_error_does_not_discard_first_page_or_follow_redirect(cfg, db, monkeypatch):
    cfg.woot_feed_pages = 3
    client, _, calls = reader(db, cfg, monkeypatch, {
        ("All", 1): [_offer("a", 20, 100)],
        ("All", 2): httpx.Response(302, headers={"Location": "https://other.test/secret"}),
    })
    with closing(client):
        posts = woot.WootAdapter().fetch_posts(SRC, client, db, cfg, NOW, None)
    assert [p.uid for p in posts] == ["a"] and len(calls) == 2


def test_conditional_gets_remain_per_page(cfg, db, monkeypatch):
    cfg.woot_feed_pages = 2
    responses = {("All", page): httpx.Response(200, headers={"ETag": f'"{page}"'},
                    json=[_offer(str(page), 20, 100)]) for page in (1, 2)}
    client, _, calls = reader(db, cfg, monkeypatch, responses)
    with closing(client):
        first = woot.WootAdapter().fetch_posts(SRC, client, db, cfg, NOW, None)
        responses.update({("All", page): httpx.Response(304) for page in (1, 2)})
        second = woot.WootAdapter().fetch_posts(SRC, client, db, cfg, NOW, None)
    assert [p.content_hash for p in first] == [p.content_hash for p in second]
    assert [req.headers.get("If-None-Match") for _, req in calls] == [None, None, '"1"', '"2"']


@pytest.mark.parametrize("feeds", ["Home", ["https://other.test"], ["../Home"], ["Home?key=x"], [123]])
def test_config_rejects_non_feed_segments(tmp_path, feeds):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"woot": {"named_feeds": feeds}}))
    with pytest.raises(ValueError, match="woot.named_feeds"):
        load_config(path)


def test_config_defaults_and_overrides(tmp_path):
    for cfg in (AppConfig(), load_config()):
        assert cfg.woot_feed_pages == 1 and cfg.woot_named_feeds == []
    path = tmp_path / "config.yaml"
    path.write_text("woot:\n  feed_pages: 3\n  named_feeds: [Home, Tools, Home]\n")
    cfg = load_config(path)
    assert cfg.woot_feed_pages == 3 and cfg.woot_named_feeds == ["Home", "Tools"]


def test_defaults_match_legacy_run_summary_and_all_published_bytes(cfg, tmp_path, monkeypatch, caplog):
    # The old reader, captured before task 104: one All request, no deduping.
    def legacy(client, cfg, key, deadline):
        response = client.api_get(woot.FEED_URL, headers={"x-api-key": key},
                                  deadline=deadline, respect_robots=False)
        response.raise_for_status()
        return response.json()["Items"]

    monkeypatch.setattr("dealradar.util.now_utc", lambda: NOW)
    monkeypatch.setattr("dealradar.publish.site.util_now", lambda: NOW)
    monkeypatch.setattr(woot.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(woot.time, "sleep", lambda _: None)
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "SMTP_HOST", "KEEPA_KEY",
                "BESTBUY_API_KEY", "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "DEALS_CLAUDE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    offer = _offer("a", 20, 100, Title="Ninja Foodi 8-qt Air Fryer",
                   Photo="https://images.example/airfryer.jpg")
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        assert str(request.url) == woot.FEED_URL
        return httpx.Response(200, json={"Items": [offer, offer]})

    current = woot.WootAdapter._fetch_items
    summaries, outputs, logs = [], [], []
    caplog.set_level(logging.INFO, logger=woot.log.name)
    for index, fetch in enumerate((legacy, current)):
        monkeypatch.setattr(woot.WootAdapter, "_fetch_items", staticmethod(fetch))
        out = tmp_path / str(index)
        config = yaml.safe_load(Path("config.yaml").read_text())
        config["publish"]["site_dir"] = str(out)
        config["parse"]["structured_output"] = True
        if index == 0:
            config["woot"].pop("feed_pages")
            config["woot"].pop("named_feeds")
        config_path = tmp_path / f"{index}.yaml"
        config_path.write_text(yaml.safe_dump(config))
        caplog.clear()
        summaries.append(pipeline.run(
            db_path=str(tmp_path / f"{index}.db"), config_path=str(config_path),
            transport=httpx.MockTransport(handle), sources_override=[SRC],
            skip_validation=True, dry_run_telegram=True, claude_client=_FakeClaudeClient()))
        outputs.append(tree(out))
        logs.append([r.getMessage() for r in caplog.records if r.name == woot.log.name])
    assert sum(str(req.url) == woot.FEED_URL for req in calls) == 2
    assert summaries[0]["published"] > 0
    assert json.dumps(summaries[0], sort_keys=True) == json.dumps(summaries[1], sort_keys=True)
    assert outputs[0] and outputs[0] == outputs[1]
    assert logs[0] == logs[1]


def probe_script():
    workflow = yaml.safe_load(Path(".github/workflows/woot-probe.yml").read_text())
    step = workflow["jobs"]["probe"]["steps"][-1]
    script = step["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    return workflow, step, script


def test_probe_script_is_manual_read_only_and_prints_only_metadata():
    workflow, step, script = probe_script()
    assert workflow.get("on", workflow.get(True)) == {"workflow_dispatch": None}
    assert workflow["permissions"] == {"contents": "read"}
    assert step["env"] == {"WOOT_API_KEY": "${{ secrets.WOOT_API_KEY }}"}
    parsed = ast.parse(script)
    calls = [node for node in ast.walk(parsed) if isinstance(node, ast.Call)]
    # Scan actual output expressions, not comments or request inputs.
    output = "\n".join(ast.unparse(node) for node in calls
                       if isinstance(node.func, ast.Name) and node.func.id == "print")
    for forbidden in ("response.text", "response.content", "response.headers", "str(response)",
                      "Title", "SalePrice", "OfferId", "WOOT_API_KEY", "FEED_URL", "http://", "https://"):
        assert forbidden not in output
    assert not any(isinstance(node.func, ast.Attribute) and node.func.attr in
                   ("post", "put", "patch", "delete", "raise_for_status") for node in calls)
    assert "candidates[:15]" in script and "time.sleep(1)" in script


@pytest.mark.parametrize("status", [200, 302, 403, 429, 500, "network", "invalid"])
def test_probe_logs_no_secret_offer_content_or_urls(cfg, monkeypatch, capsys, status):
    _, _, script = probe_script()
    secret = "synthetic-woot-secret"
    title, price, url = "PRIVATE_TITLE_SENTINEL", "923.45", "https://private.test/offer"
    clock, calls = HostClock(), []
    monkeypatch.setattr(woot.time, "sleep", clock.sleep)
    payload = {
        "Items": [dict(_offer("private-id", float(price), 1234), Title=title, Url=url)],
        "Page": 2, "TotalCount": "5001", "Next": url + "?key=" + secret,
        "Pagination": {"PageSize": 5000, "HasNext": True, "NextToken": secret},
        "TotalPrice": price, "NextTitle": title, "NextCount": secret,
        url: title, secret: secret,
    }

    def handle(request):
        calls.append((clock(), request))
        assert request.headers["x-api-key"] == secret
        assert secret not in str(request.url)
        assert request.method == "GET" and request.url.host == "developer.woot.com"
        assert "hello@wowpriced.com" in request.headers["User-Agent"]
        if status == "network":
            raise httpx.ConnectError(secret + title + url + price)
        if status == "invalid":
            return httpx.Response(200, text=secret + title + url + price)
        return httpx.Response(status, json=payload, headers={"Location": url})

    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    disabled = logging.root.manager.disable
    try:
        exec(compile(script, "woot-probe", "exec"), {})
    finally:
        logging.disable(disabled)
    output = capsys.readouterr().out
    for forbidden in (secret, title, price, url, "private-id", "http://", "https://"):
        assert forbidden not in output
    assert len(calls) == (9 if status in (200, "invalid") else 1)
    assert len(calls) <= 15 and all(b[0] - a[0] >= 1 for a, b in zip(calls, calls[1:]))
    assert "guesses" in output and "lookup: skipped" in output
    if status == 200:
        rows = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
        assert rows[0]["paging"]["TotalCount"] == "5001"
        assert rows[0]["paging"]["Pagination.HasNext"] is True
        assert rows[1]["overlap_all_count"] == 1 and rows[1]["new_item_count"] == 0
