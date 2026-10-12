"""Pinterest authorization and posting: MockTransport only, no live writes."""

import base64
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from dealradar import cli, util
from dealradar.config import load_config
from dealradar.db import DB
from dealradar.publish import pinterest
from dealradar.publish.site import render_site
from tests.test_publish_site import NOW, make_deal

APP_ID = "1623198"
APP_SECRET = "test-app-secret-sentinel"
CODE = "test-auth-code-sentinel"
ACCESS = "test-access-token-sentinel"
REFRESH = "test-refresh-token-sentinel"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("PINTEREST_APP_ID", APP_ID)
    monkeypatch.setenv("PINTEREST_APP_SECRET", APP_SECRET)
    monkeypatch.setattr(pinterest.time, "sleep", lambda seconds: None)
    config = load_config()
    config.site_dir = str(tmp_path / "site")
    return config


@pytest.fixture
def db(tmp_path):
    database = DB(tmp_path / "pinterest-test.db")
    yield database
    database.close()


def token_response(**changes):
    return {"access_token": ACCESS, "refresh_token": REFRESH,
            "expires_in": 2592000, "refresh_token_expires_in": 5184000, **changes}


def seed_auth(db, *, days=30, environment="sandbox"):
    db.meta_set("pinterest_auth", json.dumps({
        "access_token": ACCESS, "refresh_token": REFRESH,
        "expires_at": util.iso(NOW + timedelta(days=days)),
        "refresh_expires_at": util.iso(NOW + timedelta(days=60)),
        "environment": environment,
    }))


def published(db, cfg, rows=None):
    rows = rows if rows is not None else [candidate("first", 1), candidate("second", 2)]
    out = Path(cfg.site_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for row in rows:
        key = row.get("key", "")[:8]
        path = f"/deal/product-{key}/"
        paths[key] = path
        folder = out / path.strip("/")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text("published page")
    (out / "deals.json").write_text(json.dumps({"deals": rows}))
    db.meta_set("seo_deal_paths", json.dumps(paths))
    return rows


def candidate(key, pick, **changes):
    return {"dedupe_key": key, "key": hashlib.sha256(key.encode()).hexdigest()[:16],
            "pick": pick, "image_url": "https://images.example/product.jpg",
            "status": "alive", "item": f"Product {key}", "store": "Amazon",
            "pct_at_checkout": 80.0, "final_price": 9.99, "regular_price": 49.95,
            **changes}


def api_handler(db, calls, *, board_items=None, pin_status=201):
    def handle(request):
        assert request.url.host == "api-sandbox.pinterest.com"
        assert request.headers["Authorization"] == f"Bearer {ACCESS}"
        assert "hello@wowpriced.com" in request.headers["User-Agent"]
        calls.append(request)
        if request.url.path == "/v5/boards" and request.method == "GET":
            return httpx.Response(200, json={"items": board_items if board_items is not None else [
                {"id": "board-one", "name": "Deals up to 90% off"}]})
        if request.url.path == "/v5/boards":
            assert json.loads(request.content) == {"name": "Deals up to 90% off", "privacy": "PUBLIC"}
            return httpx.Response(201, json={"id": "board-created"})
        assert request.url.path == "/v5/pins"
        # Every previous success is already durable before another Pin call.
        previous_pins = sum(r.url.path == "/v5/pins" for r in calls) - 1
        if pin_status == 201:
            assert len(json.loads(db.meta_get("pinterest_posted") or "{}")) >= previous_pins
        return httpx.Response(pin_status, json={"id": f"pin-{len(calls)}", "message": CODE + APP_SECRET})
    return handle


def test_auth_url_params_and_random_state(cfg, monkeypatch):
    first = urlsplit(pinterest.auth_url(cfg))
    second = parse_qs(urlsplit(pinterest.auth_url(cfg)).query)
    params = parse_qs(first.query)
    assert first.scheme == "https" and first.netloc == "www.pinterest.com" and first.path == "/oauth/"
    assert params == {"client_id": [APP_ID], "redirect_uri": ["https://wowpriced.com/"],
                      "response_type": ["code"], "scope": [pinterest.SCOPES], "state": params["state"]}
    assert len(params["state"][0]) >= 32 and params["state"] != second["state"]
    assert APP_SECRET not in first.query
    monkeypatch.delenv("PINTEREST_APP_ID")
    assert pinterest.auth_url(cfg) is None


@pytest.mark.parametrize("environment,host", [
    ("sandbox", "api-sandbox.pinterest.com"), ("production", "api.pinterest.com"),
])
def test_code_exchange_stores_tokens_and_safe_output(db, cfg, capsys, environment, host):
    cfg.pinterest["environment"] = environment

    def handle(request):
        assert request.method == "POST" and request.url.host == host
        assert request.url.path == "/v5/oauth/token"
        assert request.headers["Authorization"] == "Basic " + base64.b64encode(
            f"{APP_ID}:{APP_SECRET}".encode()).decode()
        assert parse_qs(request.content.decode()) == {
            "grant_type": ["authorization_code"], "code": [CODE],
            "redirect_uri": ["https://wowpriced.com/"], "continuous_refresh": ["true"],
        }
        return httpx.Response(200, json=token_response())

    assert pinterest.connect(db, cfg, CODE, transport=httpx.MockTransport(handle), now=NOW) == 0
    assert json.loads(db.meta_get("pinterest_auth")) == {
        "access_token": ACCESS, "refresh_token": REFRESH,
        "expires_at": util.iso(NOW + timedelta(days=30)),
        "refresh_expires_at": util.iso(NOW + timedelta(days=60)), "environment": environment,
    }
    assert capsys.readouterr().out == f"connected ({environment}), access token valid 30 days\n"


@pytest.mark.parametrize("rotate", [True, False])
def test_refresh_near_expiry_preserves_or_rotates_refresh_token(db, cfg, rotate):
    seed_auth(db, days=3)
    published(db, cfg, [])
    new_refresh = "rotated-refresh-token-sentinel"

    def handle(request):
        assert request.url.path == "/v5/oauth/token"
        assert parse_qs(request.content.decode()) == {"grant_type": ["refresh_token"], "refresh_token": [REFRESH]}
        data = token_response(access_token="fresh-access", refresh_token=new_refresh)
        if not rotate:
            del data["refresh_token"]
            del data["refresh_token_expires_in"]
        else:
            data["refresh_token_expires_at"] = int((NOW + timedelta(days=61)).timestamp())
        return httpx.Response(200, json=data)

    assert pinterest.post(db, cfg, transport=httpx.MockTransport(handle), now=NOW) == 0
    auth = json.loads(db.meta_get("pinterest_auth"))
    assert auth["access_token"] == "fresh-access"
    assert auth["refresh_token"] == (new_refresh if rotate else REFRESH)
    assert auth["refresh_expires_at"] == util.iso(NOW + timedelta(days=61 if rotate else 60))
    assert auth["expires_at"] == util.iso(NOW + timedelta(days=30))


@pytest.mark.parametrize("reason", ["disabled", "no secrets", "not connected", "environment mismatch", "refresh expired"])
def test_unavailable_is_one_safe_line_and_no_network(db, cfg, caplog, monkeypatch, reason):
    if reason == "disabled":
        cfg.pinterest["enabled"] = False
    elif reason == "no secrets":
        monkeypatch.delenv("PINTEREST_APP_SECRET")
    elif reason == "environment mismatch":
        seed_auth(db, environment="production")
    elif reason == "refresh expired":
        seed_auth(db, days=0)
        auth = json.loads(db.meta_get("pinterest_auth"))
        auth["refresh_expires_at"] = util.iso(NOW - timedelta(seconds=1))
        db.meta_set("pinterest_auth", json.dumps(auth))
    def handle(request):
        pytest.fail("unexpected network request")
    with caplog.at_level("INFO", logger=pinterest.__name__):
        assert pinterest.post(db, cfg, transport=httpx.MockTransport(handle), now=NOW) == 0
    records = [r for r in caplog.records if r.name == pinterest.__name__]
    assert len(records) == 1
    assert ("not connected" if reason == "refresh expired" else reason) in records[0].message


@pytest.mark.parametrize("create", [False, True])
def test_board_found_or_created_and_remembered(db, cfg, create):
    seed_auth(db)
    published(db, cfg)
    calls = []
    transport = httpx.MockTransport(api_handler(db, calls, board_items=[] if create else None))
    assert pinterest.post(db, cfg, transport=transport, now=NOW) == 0
    board = json.loads(db.meta_get("pinterest_board"))
    assert board == {"id": "board-created" if create else "board-one",
                     "name": cfg.pinterest["board_name"], "environment": "sandbox"}
    assert sum(r.method == "POST" and r.url.path == "/v5/boards" for r in calls) == int(create)
    published(db, cfg, [candidate("third", 1)])
    calls.clear()
    pinterest.post(db, cfg, transport=transport, now=NOW)
    assert len(calls) == 1 and calls[0].url.path == "/v5/pins"


def test_board_pagination_including_empty_sandbox_page(db, cfg):
    seed_auth(db)
    published(db, cfg)
    calls = []
    default_handler = api_handler(db, calls)
    def handle(request):
        if request.method == "GET" and "bookmark" not in request.url.params:
            calls.append(request)
            return httpx.Response(200, json={"items": [], "bookmark": "next-page"})
        if request.method == "GET":
            assert request.url.params["bookmark"] == "next-page"
        return default_handler(request)
    pinterest.post(db, cfg, transport=httpx.MockTransport(handle), now=NOW)
    assert sum(r.method == "GET" for r in calls) == 2


def test_candidates_pick_order_image_live_public_and_page_filters(db, cfg):
    seed_auth(db)
    rows = [candidate("second", 2), candidate("unpicked", None),
            candidate("first", 1), candidate("http", 3, image_url="http://images.example/p.jpg"),
            candidate("noimage", 4, image_url=""), candidate("dead", 5, status="dead"),
            candidate("locked", 6, locked=True), candidate("no-page", 7), candidate("done", 8)]
    published(db, cfg, rows)
    paths = json.loads(db.meta_get("seo_deal_paths"))
    (Path(cfg.site_dir) / paths[rows[-2]["key"][:8]].strip("/") / "index.html").unlink()
    db.meta_set("pinterest_posted", json.dumps({"done": {"pin_id": "prior", "at": util.iso(NOW)}}))
    calls = []
    pinterest.post(db, cfg, transport=httpx.MockTransport(api_handler(db, calls)), now=NOW)
    pins = [json.loads(r.content) for r in calls if r.url.path == "/v5/pins"]
    assert [p["alt_text"] for p in pins] == ["Product first", "Product second"]
    for pin, row in zip(pins, [rows[2], rows[0]]):
        assert pin["link"] == "https://wowpriced.com" + paths[row["key"][:8]]
        assert pin["title"] == f"80% off {row['item']}"
        assert pin["description"] == f"{row['item']}. 80% off at Amazon, verified by wowpriced. #ad"
        assert pin["media_source"] == {"source_type": "image_url", "url": row["image_url"]}
        assert pin["board_id"] == "board-one"
        assert "$" not in pin["title"] + pin["description"]


@pytest.mark.parametrize("per_run,daily,already,expected", [(2, 20, 0, 2), (5, 3, 2, 1), (2, 2, 2, 0), (0, 20, 0, 0)])
def test_caps_and_utc_day(db, cfg, per_run, daily, already, expected):
    seed_auth(db)
    published(db, cfg, [candidate(f"item-{i}", i + 1) for i in range(8)])
    cfg.pinterest.update(pins_per_run=per_run, daily_max=daily)
    prior = {f"prior-{i}": {"pin_id": str(i), "at": util.iso(NOW)} for i in range(already)}
    prior["yesterday"] = {"pin_id": "y", "at": util.iso(NOW - timedelta(days=1))}
    db.meta_set("pinterest_posted", json.dumps(prior))
    calls = []
    pinterest.post(db, cfg, transport=httpx.MockTransport(api_handler(db, calls)), now=NOW)
    assert sum(r.url.path == "/v5/pins" for r in calls) == expected
    assert len(json.loads(db.meta_get("pinterest_posted"))) == already + 1 + expected


def test_rerun_after_database_reopen_posts_nothing_twice(db, cfg):
    seed_auth(db)
    published(db, cfg)
    calls = []
    transport = httpx.MockTransport(api_handler(db, calls))
    pinterest.post(db, cfg, transport=transport, now=NOW)
    saved = db.meta_get("pinterest_posted")
    reopened = DB(db.path)
    try:
        pinterest.post(reopened, cfg, transport=transport, now=NOW)
        assert reopened.meta_get("pinterest_posted") == saved
    finally:
        reopened.close()
    assert sum(r.url.path == "/v5/pins" for r in calls) == 2


@pytest.mark.parametrize("status", [401, 429, 500])
def test_failed_pin_status_only_and_retry_next_run(db, cfg, caplog, status):
    seed_auth(db)
    published(db, cfg)
    calls = []
    with caplog.at_level("INFO", logger=pinterest.__name__):
        pinterest.post(db, cfg, transport=httpx.MockTransport(api_handler(db, calls, pin_status=status)), now=NOW)
    assert sum(r.url.path == "/v5/pins" for r in calls) == (1 if status == 401 else 2)
    assert db.meta_get("pinterest_posted") is None
    assert f"HTTP {status}" in caplog.text
    for secret in (ACCESS, REFRESH, APP_SECRET, CODE):
        assert secret not in caplog.text
    calls.clear()
    pinterest.post(db, cfg, transport=httpx.MockTransport(api_handler(db, calls)), now=NOW)
    assert len(json.loads(db.meta_get("pinterest_posted"))) == 2


def test_partial_success_survives_later_401(db, cfg):
    seed_auth(db)
    published(db, cfg)
    calls = []
    default_handler = api_handler(db, calls)
    def handle(request):
        if request.url.path == "/v5/pins" and db.meta_get("pinterest_posted"):
            calls.append(request)
            return httpx.Response(401, json={"message": ACCESS})
        return default_handler(request)
    pinterest.post(db, cfg, transport=httpx.MockTransport(handle), now=NOW)
    assert set(json.loads(db.meta_get("pinterest_posted"))) == {"first"}


def test_title_description_word_limits_prices_and_disclosure():
    item = "Fantastic product " * 50 + " $19.99 USD 50 100 USD"
    deal = candidate("long", 1, item=item, store="Store " * 50)
    pin = pinterest.pin_payload(deal, "/deal/long/", "b", "#ad")
    assert 0 < len(pin["title"]) <= 100 and len(pin["description"]) <= 500
    assert pin["title"].split()[-1] in {"Fantastic", "product"}
    assert pin["description"].endswith("verified by wowpriced. #ad")
    assert pin["alt_text"] == item
    assert "$" not in pin["title"] + pin["description"]
    short = pinterest.pin_payload(candidate("price", 1, item="Gift card $19.99 USD 50 100 USD"), "/deal/gift/", "b", "#ad")
    assert short["title"] == "80% off Gift card"
    assert short["description"] == "Gift card. 80% off at Amazon, verified by wowpriced. #ad"


def test_tokens_code_and_secret_never_in_published_files(db, cfg, caplog, capsys):
    with caplog.at_level("INFO", logger=pinterest.__name__):
        pinterest.connect(db, cfg, CODE, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=token_response())), now=NOW)
    render_site(db, cfg, NOW, deals=[make_deal(image_url="https://images.example/p.jpg")])
    files = [p for p in Path(cfg.site_dir).rglob("*") if p.is_file()]
    assert len(files) > 20
    public_bytes = b"".join(p.read_bytes() for p in files)
    output = caplog.text + capsys.readouterr().out
    for secret in (ACCESS, REFRESH, APP_SECRET, CODE):
        assert secret.encode() not in public_bytes
        assert secret not in output


def test_connect_error_does_not_log_response_or_request(db, cfg, caplog):
    assert pinterest.connect(db, cfg, CODE, transport=httpx.MockTransport(
        lambda request: httpx.Response(400, json={"message": APP_SECRET + CODE})), now=NOW) == 1
    assert "HTTP 400" in caplog.text and APP_SECRET not in caplog.text and CODE not in caplog.text
    assert db.meta_get("pinterest_auth") is None


def test_cli_routes_commands_with_config_and_database(db, cfg, monkeypatch, capsys):
    monkeypatch.setattr("dealradar.config.load_config", lambda path: cfg)
    assert cli.main(["pinterest-auth-url"]) == 0
    assert capsys.readouterr().out.startswith("https://www.pinterest.com/oauth/?")
    monkeypatch.setattr(pinterest, "connect", lambda database, config, code:
                        int(database.path != db.path or config is not cfg or code != CODE))
    monkeypatch.setattr(pinterest, "post", lambda database, config:
                        int(database.path != db.path or config is not cfg))
    assert cli.main(["pinterest-connect", "--db", str(db.path), "--code", CODE]) == 0
    assert cli.main(["pinterest-post", "--db", str(db.path)]) == 0


def test_error_names_status_code_endpoint_and_a_masked_reason(monkeypatch):
    monkeypatch.setenv("PINTEREST_APP_SECRET", APP_SECRET)
    cfg = load_config()

    def handle(request):
        return httpx.Response(400, json={
            "code": 58, "message": "Board name\nexists " + APP_SECRET + " pina_abc123XYZ " + "x" * 200})

    api = pinterest._API(cfg, transport=httpx.MockTransport(handle))
    with pytest.raises(pinterest._APIError) as caught:
        api.request("POST", "/boards", json={"name": "x"})
    text = str(caught.value)
    assert text.startswith("HTTP 400 code 58 (POST /boards): Board name exists <masked> <masked> <masked>")
    assert APP_SECRET not in text and "pina_" not in text and len(text) < 180
    with pytest.raises(pinterest._APIError) as caught:
        api.request("GET", "/boards?bookmark=abc")
    assert str(caught.value).startswith("HTTP 400 code 58 (GET): Board name exists")
    with pytest.raises(pinterest._APIError) as caught:
        api.request("POST", "/oauth/token", data={"code": "c"})
    assert str(caught.value) == "HTTP 400 code 58 (POST /oauth/token)"


@pytest.mark.parametrize("hidden_board_listed", [True, False])
def test_board_name_taken_but_unlisted_finds_it_or_uses_a_related_name(db, cfg, hidden_board_listed):
    seed_auth(db)
    published(db, cfg)
    calls = []
    default_handler = api_handler(db, calls)

    def handle(request):
        if request.url.path == "/v5/boards" and request.method == "GET":
            calls.append(request)
            if request.url.params.get("privacy") == "ALL" and hidden_board_listed:
                return httpx.Response(200, json={"items": [{"id": "board-secret",
                                                            "name": " deals UP to 90% off "}]})
            return httpx.Response(200, json={"items": []})
        if request.url.path == "/v5/boards":
            calls.append(request)
            name = json.loads(request.content)["name"]
            if name == "Deals up to 90% off":
                return httpx.Response(400, json={"code": 58, "message": "Try a different name."})
            assert name == "Deals up to 90% off (wowpriced)"
            return httpx.Response(201, json={"id": "board-alt"})
        return default_handler(request)

    assert pinterest.post(db, cfg, transport=httpx.MockTransport(handle), now=NOW) == 0
    board = json.loads(db.meta_get("pinterest_board"))
    assert board["id"] == ("board-secret" if hidden_board_listed else "board-alt")
    assert board["name"] == cfg.pinterest["board_name"]
    assert len(json.loads(db.meta_get("pinterest_posted"))) == 2
