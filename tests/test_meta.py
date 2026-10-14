"""Meta posting with MockTransport only: no network or public writes."""

import json
from datetime import timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from dealradar import cli, util
from dealradar.config import SECRET_NAMES, load_config, redact_secrets
from dealradar.db import DB
from dealradar.publish import meta, pinterest, social
from dealradar.publish.site import render_site
from tests.test_pinterest import candidate, published
from tests.test_publish_site import NOW, make_deal

SYSTEM = "test-meta-system-token-sentinel"
PAGE = "test-meta-page-token-sentinel"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("META_SYSTEM_TOKEN", SYSTEM)
    monkeypatch.setattr(meta.time, "sleep", lambda seconds: None)
    config = load_config()
    # Fixture deals live only in deals.json; the stock-evidence rule
    # (sightings in the DB) is covered in tests/test_social_stock.py.
    config.social_require_stock_evidence = False
    config.site_dir = str(tmp_path / "site")
    # These tests exercise the poster itself, starting from draft mode
    # (production went live 2026-10-13; `live(cfg)` switches a test over).
    config.meta["enabled"] = True
    for network in ("facebook", "instagram"):
        config.meta[network]["mode"] = "draft"
    return config


@pytest.fixture
def db(tmp_path):
    database = DB(tmp_path / "meta-test.db")
    yield database
    database.close()


def history(db, key):
    return json.loads(db.meta_get(key) or "{}")


def live(cfg):
    for network in ("facebook", "instagram"):
        cfg.meta[network].update(mode="live", hours_utc=[NOW.hour])


def handler(db, cfg, calls, *, reject=None, statuses=None):
    statuses = iter(statuses or ["FINISHED"] * 20)

    def handle(request):
        calls.append(request)
        assert request.url.host == "graph.facebook.com"
        assert "access_token" not in request.url.params
        assert "hello@wowpriced.com" in request.headers["User-Agent"]
        path = request.url.path
        if path.endswith("/photos"):
            assert request.headers["Authorization"] == f"Bearer {PAGE}"
        else:
            assert request.headers["Authorization"] == f"Bearer {SYSTEM}"
        if path == f"/v26.0/{cfg.meta['page_id']}":
            assert request.method == "GET" and request.url.params["fields"] == "access_token"
            return httpx.Response(200, json={"access_token": PAGE})
        fields = parse_qs(request.content.decode()) if request.method == "POST" else {}
        if reject:
            response = reject(request, fields)
            if response is not None:
                return response
        if path.endswith("/photos"):
            assert request.method == "POST" and fields["published"] == ["true"]
            assert fields["url"] == ["https://images.example/product.jpg"]
            assert fields["caption"][0].endswith("\n#ad")
            return httpx.Response(200, json={"id": f"photo-{len(calls)}"})
        if path.endswith("/media"):
            assert request.method == "POST"
            assert fields["image_url"] == ["https://images.example/product.jpg"]
            return httpx.Response(200, json={"id": f"container-{len(calls)}"})
        if path.endswith("/media_publish"):
            assert request.method == "POST" and fields["creation_id"][0].startswith("container-")
            return httpx.Response(200, json={"id": f"media-{len(calls)}"})
        assert request.method == "GET" and request.url.params["fields"] == "status_code"
        return httpx.Response(200, json={"status_code": next(statuses)})

    return handle


def run(db, cfg, calls, *, now=NOW, **kwargs):
    assert meta.post(db, cfg, now=now, transport=httpx.MockTransport(
        handler(db, cfg, calls, **kwargs))) == 0


@pytest.mark.parametrize("network", ["facebook", "instagram"])
def test_caption_facts_prices_word_trim_and_disclosure(cfg, network):
    item = "Fantastic product $19.99 USD 50 100 USD " * 50
    deal = candidate("first", 1, item=item, store="Amazon $9.99", pct_at_checkout=79.6)
    cfg.meta["instagram"]["hashtags"] = "#deals " * 1000
    text = meta.caption(deal, "/deal/first/", cfg, network)
    assert text.startswith("🔥 80% OFF: Fantastic product")
    assert "19.99" not in text and "9.99" not in text and "$" not in text
    assert "USD" not in text and "100" not in text
    assert text.endswith("\n#ad")
    name = text.splitlines()[0].removeprefix("🔥 80% OFF: ")
    assert len(name) <= 70 and name.split()[-1] in ("Fantastic", "product")
    if network == "instagram":
        assert len(text) <= 2200 and "https://" not in text
        assert "Tap the link in our bio for today's deals." in text
    else:
        assert text.endswith("At Amazon, verified by wowpriced: https://wowpriced.com/deal/first/\n#ad")


def test_config_cli_and_shared_pinterest_helpers(db, cfg, monkeypatch):
    assert cfg.meta["graph_base"] == "https://graph.facebook.com/v26.0"
    assert cfg.meta["page_id"] == "1382622794936174"
    assert cfg.meta["ig_user_id"] == "17841412621006331"
    production = load_config().meta
    assert production["facebook"]["mode"] == production["instagram"]["mode"] == "live"
    assert cfg.meta["facebook"]["hours_utc"] == [13, 17, 21, 1]
    assert cfg.meta["instagram"]["hours_utc"] == [14, 19, 23]
    assert "META_SYSTEM_TOKEN" in SECRET_NAMES
    assert SYSTEM not in redact_secrets(SYSTEM)
    assert "graph.facebook.com" in cli.CORE_DOMAINS
    for name in ("_candidates", "_word_trim", "_without_prices", "social_title"):
        assert getattr(pinterest, name) is getattr(social, name)
    monkeypatch.setattr("dealradar.config.load_config", lambda path: cfg)
    monkeypatch.setattr(meta, "post", lambda database, config:
                        int(database.path != db.path or config is not cfg))
    assert cli.main(["meta-post", "--db", str(db.path)]) == 0


@pytest.mark.parametrize("reason", ["disabled", "no secrets"])
def test_disabled_or_missing_secret_one_line_no_network(db, cfg, caplog, monkeypatch, reason):
    if reason == "disabled":
        cfg.meta["enabled"] = False
    else:
        monkeypatch.delenv("META_SYSTEM_TOKEN")
    with caplog.at_level("INFO", logger=meta.__name__):
        meta.post(db, cfg, transport=httpx.MockTransport(lambda request: pytest.fail("unexpected request")))
    records = [r for r in caplog.records if r.name == meta.__name__]
    assert [r.message for r in records] == [f"meta: {reason}"]


def test_drafts_make_no_public_write_ignore_hours_and_do_not_block_live(db, cfg, caplog):
    published(db, cfg, [candidate("first", 1)])
    for network in ("facebook", "instagram"):
        cfg.meta[network]["hours_utc"] = []
    calls = []
    with caplog.at_level("INFO", logger=meta.__name__):
        run(db, cfg, calls)
    assert [r.method for r in calls] == ["GET", "POST", "GET"]
    assert not any(r.url.path.endswith(("/photos", "/media_publish")) for r in calls)
    assert db.meta_get("meta_posted") is None
    drafts = history(db, "meta_drafts")
    assert list(drafts["facebook"]) == list(drafts["instagram"]) == ["first"]
    assert drafts["instagram"]["first"]["status"] == "FINISHED"
    assert "image host images.example" in caplog.text and "container status FINISHED" in caplog.text
    assert "meta: facebook posted 1 (today 1/4, draft); instagram posted 1 (today 1/3, draft)" in caplog.text
    calls.clear()
    run(db, cfg, calls)
    assert not calls  # same drafts do not get created again
    live(cfg)
    run(db, cfg, calls)
    assert len(calls) == 5
    assert list(history(db, "meta_posted")["facebook"]) == ["first"]
    assert list(history(db, "meta_posted")["instagram"]) == ["first"]
    calls.clear()
    run(db, cfg, calls)
    assert not calls


@pytest.mark.parametrize("network", ["facebook", "instagram"])
def test_live_hour_gate_separate_networks_and_utc(db, cfg, network):
    published(db, cfg)
    live(cfg)
    cfg.meta[network]["hours_utc"] = []
    calls = []
    # The wall-clock hour differs but the UTC instant is still NOW.
    run(db, cfg, calls, now=NOW.astimezone(timezone(timedelta(hours=5))))
    posted = history(db, "meta_posted")
    assert not posted[network]
    assert len(posted["instagram" if network == "facebook" else "facebook"]) == 1


@pytest.mark.parametrize("mode", ["draft", "live"])
def test_per_run_daily_caps_and_utc_reset(db, cfg, mode):
    published(db, cfg, [candidate(str(i), i) for i in range(1, 11)])
    live(cfg)
    for network in ("facebook", "instagram"):
        cfg.meta[network].update(mode=mode, posts_per_run=2)
    calls = []
    key = "meta_drafts" if mode == "draft" else "meta_posted"
    run(db, cfg, calls)
    assert [len(history(db, key)[n]) for n in ("facebook", "instagram")] == [2, 2]
    run(db, cfg, calls)
    assert [len(history(db, key)[n]) for n in ("facebook", "instagram")] == [2, 2]
    run(db, cfg, calls, now=NOW + timedelta(minutes=10))
    assert [len(history(db, key)[n]) for n in ("facebook", "instagram")] == [4, 3]
    calls.clear()
    run(db, cfg, calls, now=NOW + timedelta(minutes=20))
    assert not calls
    run(db, cfg, calls, now=NOW + timedelta(days=1))
    assert [len(history(db, key)[n]) for n in ("facebook", "instagram")] == [6, 5]


@pytest.mark.parametrize("network", ["facebook", "instagram"])
@pytest.mark.parametrize("which", ["posts_per_run", "daily_max"])
def test_zero_caps_make_no_request(db, cfg, network, which):
    published(db, cfg)
    cfg.meta[network][which] = 0
    cfg.meta["instagram" if network == "facebook" else "facebook"]["posts_per_run"] = 0
    run(db, cfg, [])
    assert db.meta_get("meta_drafts") is None


@pytest.mark.parametrize("endpoint", ["photos", "media", "media_publish"])
def test_image_failure_saved_and_next_candidate_tried(db, cfg, endpoint):
    published(db, cfg, [candidate("first", 1), candidate("second", 2)])
    live(cfg)
    network = "facebook" if endpoint == "photos" else "instagram"
    other = "instagram" if network == "facebook" else "facebook"
    cfg.meta[other]["posts_per_run"] = 0
    attempts = []

    def reject(request, fields):
        if request.url.path.endswith("/" + endpoint):
            attempts.append(fields)
            if len(attempts) == 1:
                return httpx.Response(400, json={"error": {"code": 100, "message": SYSTEM + PAGE}})

    calls = []
    run(db, cfg, calls, reject=reject)
    assert len(attempts) == 2
    assert list(history(db, "meta_failed")[network]) == ["first"]
    assert list(history(db, "meta_posted")[network]) == ["second"]
    calls.clear()
    run(db, cfg, calls)
    assert not calls


@pytest.mark.parametrize("network", ["facebook", "instagram"])
def test_rejected_images_at_most_three_attempts_each_run(db, cfg, network):
    published(db, cfg, [candidate(str(i), i) for i in range(1, 6)])
    live(cfg)
    cfg.meta["instagram" if network == "facebook" else "facebook"]["posts_per_run"] = 0
    endpoint = "photos" if network == "facebook" else "media"

    def reject(request, fields):
        if request.url.path.endswith("/" + endpoint):
            return httpx.Response(400, json={"error": {"code": 100}})

    calls = []
    run(db, cfg, calls, reject=reject)
    assert sum(r.url.path.endswith("/" + endpoint) for r in calls) == 3
    assert len(history(db, "meta_failed")[network]) == 3
    assert db.meta_get("meta_posted") is None


@pytest.mark.parametrize("status,code", [(401, 10), (400, 190)])
@pytest.mark.parametrize("endpoint", ["page", "photos", "media", "media_publish", "status"])
def test_bad_token_stops_entire_run_one_safe_line(db, cfg, caplog, status, code, endpoint):
    published(db, cfg)
    live(cfg)
    calls = []

    def reject(request):
        calls.append(request)
        path = request.url.path
        match = {"page": path.endswith(cfg.meta["page_id"]),
                 "photos": path.endswith("/photos"), "media": path.endswith("/media"),
                 "media_publish": path.endswith("/media_publish"), "status": "container-" in path}
        if match[endpoint]:
            return httpx.Response(status, json={"error": {"code": code, "message": SYSTEM + PAGE}})
        return handler(db, cfg, [], statuses=["FINISHED"])(request)

    with caplog.at_level("INFO", logger=meta.__name__):
        meta.post(db, cfg, transport=httpx.MockTransport(reject), now=NOW)
    assert sum("meta: not connected" in r.message for r in caplog.records) == 1
    assert "meta: not connected" in caplog.records[-1].message
    assert SYSTEM not in caplog.text and PAGE not in caplog.text
    assert db.meta_get("meta_failed") is None


def test_poll_waits_before_publish_and_is_bounded(db, cfg, monkeypatch):
    published(db, cfg, [candidate("first", 1)])
    live(cfg)
    cfg.meta["facebook"]["posts_per_run"] = 0
    sleeps = []
    monkeypatch.setattr(meta.time, "sleep", sleeps.append)
    calls = []
    run(db, cfg, calls, statuses=["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    assert len(calls) == 5 and calls[-1].url.path.endswith("/media_publish")
    assert sleeps.count(2) == 2
    assert len(history(db, "meta_posted")["instagram"]) == 1


@pytest.mark.parametrize("mode", ["draft", "live"])
def test_container_still_processing_never_publishes(db, cfg, mode):
    published(db, cfg, [candidate("first", 1)])
    live(cfg)
    cfg.meta["instagram"]["mode"] = mode
    cfg.meta["facebook"]["posts_per_run"] = 0
    calls = []
    run(db, cfg, calls, statuses=["IN_PROGRESS"] * 5)
    assert len(calls) == 6 and not any(r.url.path.endswith("/media_publish") for r in calls)
    assert db.meta_get("meta_posted") is None and db.meta_get("meta_failed") is None
    if mode == "draft":
        assert history(db, "meta_drafts")["instagram"]["first"]["status"] == "IN_PROGRESS"


def test_container_error_marks_failed_and_tries_next(db, cfg):
    published(db, cfg)
    cfg.meta["facebook"]["posts_per_run"] = 0
    calls = []
    run(db, cfg, calls, statuses=["ERROR", "FINISHED"])
    assert list(history(db, "meta_failed")["instagram"]) == ["first"]
    assert list(history(db, "meta_drafts")["instagram"]) == ["second"]


def test_every_success_is_saved_before_next_call(db, cfg):
    published(db, cfg)
    live(cfg)
    cfg.meta["facebook"]["posts_per_run"] = 2
    calls = []
    original = handler(db, cfg, calls)

    def handle(request):
        previous = sum(r.url.path.endswith("/photos") for r in calls)
        if previous:
            assert len(history(db, "meta_posted")["facebook"]) == previous
        return original(request)

    meta.post(db, cfg, transport=httpx.MockTransport(handle), now=NOW)
    assert len(history(db, "meta_posted")["facebook"]) == 2
    assert len(history(db, "meta_posted")["instagram"]) == 1


@pytest.mark.parametrize("mode", ["draft", "live"])
def test_credentials_never_logged_stored_or_published(db, cfg, caplog, mode):
    published(db, cfg)
    live(cfg)
    for network in ("facebook", "instagram"):
        cfg.meta[network]["mode"] = mode
    calls = []
    with caplog.at_level("DEBUG"):
        run(db, cfg, calls)
    state = "".join(row["v"] for row in db.conn.execute("SELECT v FROM meta"))
    render_site(db, cfg, NOW, deals=[make_deal(image_url="https://images.example/product.jpg")])
    files = [p for p in Path(cfg.site_dir).rglob("*") if p.is_file()]
    assert len(files) > 20
    public = b"".join(p.read_bytes() for p in files)
    for secret in (SYSTEM, PAGE):
        assert secret not in caplog.text and secret not in state and secret.encode() not in public


def test_error_retains_only_status_and_numeric_code(db, cfg, caplog):
    published(db, cfg)
    transport = httpx.MockTransport(lambda request: httpx.Response(
        400, json={"error": {"code": SYSTEM, "message": SYSTEM + PAGE}}))
    meta.post(db, cfg, transport=transport, now=NOW)
    assert "HTTP 400 code None" in caplog.text
    assert SYSTEM not in caplog.text and PAGE not in caplog.text
    api = meta._API(cfg, transport=transport)
    try:
        with pytest.raises(meta._APIError) as caught:
            api.request("GET", "/123", SYSTEM)
        assert vars(caught.value) == {"status": 400, "code": None, "rejected": False}
        assert SYSTEM not in str(caught.value)
    finally:
        api.client.close()


def test_candidates_use_published_live_picks_in_order(db, cfg):
    rows = [candidate("second", 2), candidate("no-pick", 0), candidate("dead", 3, status="gone"),
            candidate("locked", 4, locked=True), candidate("http", 5, image_url="http://images.example/a"),
            candidate("missing-page", 6), candidate("first", 1)]
    published(db, cfg, rows)
    paths = history(db, "seo_deal_paths")
    missing = Path(cfg.site_dir) / paths[rows[5]["key"][:8]].strip("/") / "index.html"
    missing.unlink()
    calls = []
    run(db, cfg, calls)
    assert list(history(db, "meta_drafts")["facebook"]) == ["first"]
    assert list(history(db, "meta_drafts")["instagram"]) == ["first"]


@pytest.mark.parametrize("network", ["facebook", "instagram"])
def test_server_and_transport_failures_do_not_permanently_reject_deals(db, cfg, caplog, network):
    published(db, cfg, [candidate("first", 1)])
    live(cfg)
    cfg.meta["instagram" if network == "facebook" else "facebook"]["posts_per_run"] = 0
    calls = []
    endpoint = "photos" if network == "facebook" else "media"

    def reject(request, fields):
        if request.url.path.endswith("/" + endpoint):
            return httpx.Response(500, json={"error": {"code": 2, "message": SYSTEM + PAGE}})

    run(db, cfg, calls, reject=reject)
    assert db.meta_get("meta_failed") is None and db.meta_get("meta_posted") is None
    assert "HTTP 500 code 2" in caplog.text
    assert SYSTEM not in caplog.text and PAGE not in caplog.text
    meta.post(db, cfg, transport=httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(httpx.ConnectError(SYSTEM + PAGE))), now=NOW)
    assert SYSTEM not in caplog.text and PAGE not in caplog.text


@pytest.mark.parametrize("network", ["facebook", "instagram"])
@pytest.mark.parametrize("status,code", [(400, 4), (400, 9), (400, 32), (400, 368), (400, 613),
                                         (400, 10), (400, 200), (403, 100), (400, 80002)])
def test_throttle_and_permission_errors_never_reject_the_deal(db, cfg, network, status, code):
    published(db, cfg, [candidate("first", 1)])
    live(cfg)
    cfg.meta["instagram" if network == "facebook" else "facebook"]["posts_per_run"] = 0
    endpoint = "photos" if network == "facebook" else "media"

    def reject(request, fields):
        if request.url.path.endswith("/" + endpoint):
            return httpx.Response(status, json={"error": {"code": code}})

    run(db, cfg, [], reject=reject)
    assert db.meta_get("meta_failed") is None and db.meta_get("meta_posted") is None
