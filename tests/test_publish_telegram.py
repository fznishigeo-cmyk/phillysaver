"""Tests for dealradar.publish.telegram_pub — offline, fake http + fake sleep."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from dealradar.config import load_config
from dealradar.db import DB
from dealradar.publish.telegram_pub import format_message, send_alerts, tier_rank

NOW = datetime(2026, 10, 9, 14, 0, 0, tzinfo=timezone.utc)


class FakeHttpClient:
    """Records POSTs; returns a canned status code."""

    def __init__(self, status_code: int = 200):
        self.status_code = status_code
        self.posts: list[tuple[str, dict]] = []

    def post(self, url, json=None, **kwargs):
        self.posts.append((url, json))
        return SimpleNamespace(status_code=self.status_code, text="")


class FakeSleep:
    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID",
                 "TELEGRAM_ADMIN_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def db(tmp_path):
    database = DB(tmp_path / "tg_test.db")
    yield database
    database.close()


@pytest.fixture()
def cfg(tmp_path):
    config = load_config()
    config.site_dir = str(tmp_path / "site")
    # Fixture deals carry fixed first-seen dates; the page-age gate has its own test.
    config.telegram_channel["min_age_minutes"] = 0
    return config


def make_deal(**overrides):
    seen = "2026-10-09T12:00:00+00:00"
    deal = {
        "dedupe_key": "walmart|lego set",
        "store": "Walmart",
        "item": "LEGO Set",
        "brand": "LEGO",
        "canonical_url": "https://www.walmart.com/ip/12345",
        "final_price": 10.0,
        "regular_price": 50.0,
        "pct_at_checkout": 80.0,
        "abs_savings": 40.0,
        "conditions": [],
        "verify_tier": "unverified",
        "status": "alive",
        "first_seen_at": seen,
        "last_seen_at": seen,
        "sightings": [
            {"source_name": "Slickdeals", "source_url": "https://slickdeals.net/f/1",
             "seen_at": seen, "final_price": 10.0},
        ],
    }
    deal.update(overrides)
    return deal


def set_creds(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@testchan")


def test_tier_rank_ordering():
    assert tier_rank("unverified") == 0
    assert tier_rank("history-checked") == 1
    assert tier_rank("corroborated") == 2
    assert tier_rank("") == 0
    assert tier_rank("garbage") == 0


def test_format_message_full():
    msg = format_message(make_deal(conditions=["online only", "max 2"]))
    assert msg == (
        "LEGO Set — $10.00 (80% off) at Walmart"
        " · online only, max 2"
        " · via Slickdeals https://slickdeals.net/f/1"
        " · https://www.walmart.com/ip/12345"
    )


def test_format_message_omits_empty_conditions():
    msg = format_message(make_deal(conditions=[]))
    assert "online only" not in msg
    assert " · via Slickdeals" in msg
    assert msg.startswith("LEGO Set — $10.00 (80% off) at Walmart")


def test_format_message_names_corroboration_in_plain_words():
    assert "(80% off, 2+ sources) at" in format_message(
        make_deal(verify_tier="corroborated"))
    assert "(80% off, price history) at" in format_message(
        make_deal(verify_tier="history-checked"))
    assert "unverified" not in format_message(make_deal(verify_tier="unverified"))


def published(db, cfg, rows=None):
    rows = rows if rows is not None else [make_deal()]
    out = Path(cfg.site_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for i, deal in enumerate(rows):
        deal.setdefault("key", hashlib.sha256(deal["dedupe_key"].encode()).hexdigest()[:16])
        deal.setdefault("pick", i + 1)
        deal.setdefault("badge", deal.get("verify_tier", "unverified"))
        deal.setdefault("image_url", "https://images.example/product.jpg")
        key = deal["key"][:8]
        paths[key] = f"/deal/product-{key}/"
        folder = out / paths[key].strip("/")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text("published deal page")
    (out / "deals.json").write_text(json.dumps({"deals": rows}))
    db.meta_set("seo_deal_paths", json.dumps(paths))
    return rows


def seed_alert(db, key, at, channel="telegram", token=None):
    db.record_alert(deal_key=key, channel=channel, dedup_token=token or f"{key}|{channel}",
                    now=at, price_at_send=10.0, tier_at_send="unverified")


def test_channel_config_defaults_and_custom_loading(tmp_path):
    assert load_config().telegram_channel == {
        "posts_per_run": 1, "daily_max": 24, "min_gap_minutes": 30,
        "hours_local": [8, 23], "min_age_minutes": 20, "disclosure": "#ad",
    }
    path = tmp_path / "custom.yaml"
    path.write_text("telegram:\n  channel:\n    posts_per_run: 3\n    daily_max: 5\n"
                    "    min_gap_minutes: 60\n    hours_local: [9, 18]\n    disclosure: '#sponsored'\n")
    assert load_config(path).telegram_channel == {
        "posts_per_run": 3, "daily_max": 5, "min_gap_minutes": 60,
        "hours_local": [9, 18], "disclosure": "#sponsored",
    }


def test_channel_message_is_price_free_with_disclosure_last():
    deal = make_deal(item="LEGO X200 8-piece $25 £30 €40 USD 50 60 USD Set",
                     store="Walmart $10", conditions=["shipping $5", "use CODE"])
    msg = format_message(deal, "/deal/lego-x200-abc12345/", "#sponsored $5")
    assert msg == ("80% off LEGO X200 8-piece Set at Walmart\n"
                   "Verified by wowpriced: https://wowpriced.com/deal/lego-x200-abc12345/\n"
                   "#sponsored")
    assert all(value not in msg for value in ("$", "£", "€", "USD", "25", "30", "40",
                                              "50", "60", "10.00", "via", "slickdeals.net",
                                              "walmart.com", "CODE", "shipping"))


def test_channel_item_word_trimmed_to_120_chars():
    item = "Long product name " * 30
    msg = format_message(make_deal(item=item), "/deal/product-abcd1234/")
    trimmed = msg.splitlines()[0].removeprefix("80% off ").removesuffix(" at Walmart")
    assert len(trimmed) <= 120 and item.startswith(trimmed)
    assert trimmed.split()[-1] in item.split()
    assert msg.splitlines()[-1] == "#ad"


def test_first_pick_sends_with_preview_and_once_token(db, cfg, monkeypatch):
    set_creds(monkeypatch)
    published(db, cfg)
    client = FakeHttpClient()
    result = send_alerts(db, cfg, NOW, http_client=client)
    assert result == {"sent": 1, "skipped": 0, "dry_run": False}
    [(url, payload)] = client.posts
    assert url == "https://api.telegram.org/bottest-token/sendMessage"
    assert payload["chat_id"] == "@testchan"
    assert payload["disable_web_page_preview"] is False and "parse_mode" not in payload
    assert payload["text"].startswith("80% off LEGO Set at Walmart\n")
    assert payload["text"].endswith("\n#ad")
    last = db.last_alert("walmart|lego set", "telegram")
    assert last["dedup_token"] == "walmart|lego set|telegram"
    assert last["price_at_send"] == 10.0 and last["tier_at_send"] == "unverified"


@pytest.mark.parametrize("changes", [
    {}, {"final_price": 9.0}, {"final_price": 1.0}, {"verify_tier": "corroborated"},
])
def test_once_per_deal_even_after_price_or_tier_change(db, cfg, monkeypatch, changes):
    set_creds(monkeypatch)
    cfg.telegram_channel["min_gap_minutes"] = 0
    published(db, cfg)
    client = FakeHttpClient()
    assert send_alerts(db, cfg, NOW, http_client=client)["sent"] == 1
    published(db, cfg, [make_deal(**changes)])
    assert send_alerts(db, cfg, NOW + timedelta(hours=1), http_client=client)["sent"] == 0
    assert len(client.posts) == 1
    assert db.conn.execute("SELECT COUNT(*) FROM alerts_sent").fetchone()[0] == 1


def test_legacy_record_prevents_resend(db, cfg, monkeypatch):
    set_creds(monkeypatch)
    published(db, cfg, [make_deal(final_price=1.0, verify_tier="corroborated")])
    seed_alert(db, "walmart|lego set", NOW - timedelta(days=1),
               token="walmart|lego set|10.00|unverified")
    client = FakeHttpClient()
    assert send_alerts(db, cfg, NOW, http_client=client)["sent"] == 0
    assert client.posts == []


def test_picks_only_in_pick_order_excluding_posted(db, cfg, monkeypatch):
    set_creds(monkeypatch)
    cfg.telegram_channel.update(posts_per_run=3, min_gap_minutes=0)
    rows = [make_deal(dedupe_key=name, item=name, pick=pick, **changes)
            for name, pick, changes in [
                ("third", 3, {}), ("not-picked", 0, {}), ("second", 2, {}),
                ("first", 1, {}), ("posted", 1, {}), ("locked", 1, {"locked": True}),
                ("dead", 1, {"status": "dead"}), ("no-photo", 1, {"image_url": ""}),
                ("http-photo", 1, {"image_url": "http://images.example/product.jpg"}),
                ("no-page", 1, {}), ("no-path", 1, {}),
            ]]
    published(db, cfg, rows)
    paths = json.loads(db.meta_get("seo_deal_paths"))
    no_page, no_path = rows[-2:]
    (Path(cfg.site_dir) / paths[no_page["key"][:8]].strip("/") / "index.html").unlink()
    del paths[no_path["key"][:8]]
    db.meta_set("seo_deal_paths", json.dumps(paths))
    seed_alert(db, "posted", NOW - timedelta(hours=1))
    client = FakeHttpClient()
    assert send_alerts(db, cfg, NOW, http_client=client, sleep_fn=FakeSleep())["sent"] == 3
    assert [payload["text"].splitlines()[0] for _, payload in client.posts] == [
        f"80% off {name} at Walmart" for name in ("first", "second", "third")]


@pytest.mark.parametrize("limit", [0, 1, 3])
def test_1500_deal_backlog_obeys_per_run_cap(db, cfg, monkeypatch, limit):
    set_creds(monkeypatch)
    cfg.telegram_channel.update(posts_per_run=limit, min_gap_minutes=0)
    published(db, cfg, [make_deal(dedupe_key=f"deal-{i}", pick=i + 1) for i in range(1500)])
    client = FakeHttpClient()
    assert send_alerts(db, cfg, NOW, http_client=client, sleep_fn=FakeSleep())["sent"] == limit
    assert len(client.posts) == limit
    assert db.conn.execute("SELECT COUNT(*) FROM alerts_sent").fetchone()[0] == limit


def test_daily_remaining_budget_and_other_channels(db, cfg, monkeypatch):
    set_creds(monkeypatch)
    cfg.telegram_channel.update(posts_per_run=4, daily_max=3, min_gap_minutes=0)
    published(db, cfg, [make_deal(dedupe_key=f"deal-{i}") for i in range(5)])
    seed_alert(db, "old-pick", NOW - timedelta(hours=1))
    seed_alert(db, "admin", NOW, channel="admin")
    seed_alert(db, "email", NOW, channel="email_members")
    client = FakeHttpClient()
    assert send_alerts(db, cfg, NOW, http_client=client, sleep_fn=FakeSleep())["sent"] == 2
    assert send_alerts(db, cfg, NOW + timedelta(hours=1), http_client=client)["sent"] == 0
    assert len(client.posts) == 2


def test_positive_gap_defers_remaining_picks_to_later_run(db, cfg, monkeypatch):
    set_creds(monkeypatch)
    cfg.telegram_channel["posts_per_run"] = 3
    published(db, cfg, [make_deal(dedupe_key=f"deal-{i}") for i in range(3)])
    client = FakeHttpClient()
    assert send_alerts(db, cfg, NOW, http_client=client)["sent"] == 1
    assert send_alerts(db, cfg, NOW + timedelta(minutes=29, seconds=59), http_client=client)["sent"] == 0
    assert send_alerts(db, cfg, NOW + timedelta(minutes=30), http_client=client)["sent"] == 1
    assert len(client.posts) == 2


@pytest.mark.parametrize("at,expected", [
    ("2026-03-07T12:59:59+00:00", 0), ("2026-03-07T13:00:00+00:00", 1),
    ("2026-03-08T11:59:59+00:00", 0), ("2026-03-08T12:00:00+00:00", 1),
    ("2026-10-31T11:59:59+00:00", 0), ("2026-10-31T12:00:00+00:00", 1),
    ("2026-11-01T12:59:59+00:00", 0), ("2026-11-01T13:00:00+00:00", 1),
    ("2026-11-02T03:59:59+00:00", 1), ("2026-11-02T04:00:00+00:00", 0),
    ("2026-03-09T02:59:59+00:00", 1), ("2026-03-09T03:00:00+00:00", 0),
])
def test_hours_window_tracks_new_york_dst(db, cfg, at, expected):
    published(db, cfg)
    assert send_alerts(db, cfg, datetime.fromisoformat(at), dry_run=True)["sent"] == expected


def test_custom_hours_window(db, cfg):
    cfg.telegram_channel["hours_local"] = [11, 12]
    published(db, cfg)
    assert send_alerts(db, cfg, NOW, dry_run=True)["sent"] == 0  # 10 AM local
    assert send_alerts(db, cfg, NOW + timedelta(hours=1), dry_run=True)["sent"] == 1


@pytest.mark.parametrize("now,before_midnight,after_midnight", [
    ("2026-03-08T12:00:00+00:00", "2026-03-08T04:59:59+00:00", "2026-03-08T05:00:00+00:00"),
    ("2026-11-01T13:00:00+00:00", "2026-11-01T03:59:59+00:00", "2026-11-01T04:00:00+00:00"),
])
def test_daily_cap_uses_local_midnight_on_dst_day(db, cfg, now, before_midnight, after_midnight):
    cfg.telegram_channel["daily_max"] = 2
    published(db, cfg)
    seed_alert(db, "previous-local-day", datetime.fromisoformat(before_midnight))
    seed_alert(db, "current-local-day", datetime.fromisoformat(after_midnight))
    assert send_alerts(db, cfg, datetime.fromisoformat(now), dry_run=True)["sent"] == 1
    published(db, cfg, [make_deal(dedupe_key="another-pick")])
    assert send_alerts(db, cfg, datetime.fromisoformat(now) + timedelta(hours=1), dry_run=True)["sent"] == 0


@pytest.mark.parametrize("previous,now", [
    ("2026-03-08T06:50:00+00:00", "2026-03-08T07:10:00+00:00"),
    ("2026-11-01T05:50:00+00:00", "2026-11-01T06:10:00+00:00"),
])
def test_min_gap_measures_elapsed_time_across_dst_edge(db, cfg, previous, now):
    cfg.telegram_channel["hours_local"] = [0, 24]
    published(db, cfg)
    seed_alert(db, "previous", datetime.fromisoformat(previous))
    at = datetime.fromisoformat(now)
    assert send_alerts(db, cfg, at, dry_run=True)["sent"] == 0
    assert send_alerts(db, cfg, at + timedelta(minutes=10), dry_run=True)["sent"] == 1


@pytest.mark.parametrize("credentials", ["none", "bot-only", "channel-only", "admin-only"])
def test_missing_credentials_record_nothing_and_send_after_configuration(
        db, cfg, monkeypatch, capsys, caplog, credentials):
    if credentials in ("bot-only", "admin-only"):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    if credentials == "channel-only":
        monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@testchan")
    if credentials == "admin-only":
        monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "999888")
    published(db, cfg)
    client = FakeHttpClient()
    with caplog.at_level(logging.INFO, logger="dealradar.publish.telegram"):
        result = send_alerts(db, cfg, NOW, http_client=client)
    assert result["dry_run"] is True and result["sent"] == 1
    assert client.posts == [] and db.last_alert("walmart|lego set", "telegram") is None
    assert db.conn.execute("SELECT COUNT(*) FROM alerts_sent").fetchone()[0] == 0
    assert "TELEGRAM DRY-RUN:" in capsys.readouterr().out
    assert any("TELEGRAM DRY-RUN:" in record.getMessage() for record in caplog.records)
    set_creds(monkeypatch)
    assert send_alerts(db, cfg, NOW, http_client=client)["sent"] == 1


@pytest.mark.parametrize("configured", [False, True])
def test_explicit_dry_run_logs_and_records_for_idempotency(db, cfg, monkeypatch, capsys, configured):
    set_creds(monkeypatch)
    cfg.telegram_dry_run = configured
    published(db, cfg)
    client = FakeHttpClient()
    kwargs = {} if configured else {"dry_run": True}
    assert send_alerts(db, cfg, NOW, http_client=client, **kwargs)["sent"] == 1
    assert db.alert_exists("telegram", "walmart|lego set|telegram")
    assert send_alerts(db, cfg, NOW, http_client=client, **kwargs)["sent"] == 0
    assert capsys.readouterr().out.count("TELEGRAM DRY-RUN:") == 1 and client.posts == []


def test_false_dry_run_arg_overrides_config(db, cfg, monkeypatch):
    set_creds(monkeypatch)
    cfg.telegram_dry_run = True
    published(db, cfg)
    client = FakeHttpClient()
    assert send_alerts(db, cfg, NOW, dry_run=False, http_client=client)["dry_run"] is False
    assert len(client.posts) == 1


def test_zero_gap_batch_rate_limit_and_no_dry_run_sleep(db, cfg, monkeypatch):
    set_creds(monkeypatch)
    cfg.telegram_channel.update(posts_per_run=2, min_gap_minutes=0)
    published(db, cfg, [make_deal(dedupe_key="first"), make_deal(dedupe_key="second")])
    client, sleeper = FakeHttpClient(), FakeSleep()
    assert send_alerts(db, cfg, NOW, http_client=client, sleep_fn=sleeper)["sent"] == 2
    assert sleeper.calls == [pytest.approx(4.0)]
    published(db, cfg, [make_deal(dedupe_key="third"), make_deal(dedupe_key="fourth")])
    sleeper.calls.clear()
    assert send_alerts(db, cfg, NOW, dry_run=True, sleep_fn=sleeper)["sent"] == 2
    assert sleeper.calls == []


def test_failed_post_not_recorded_then_retried(db, cfg, monkeypatch):
    set_creds(monkeypatch)
    published(db, cfg, [make_deal(), make_deal(dedupe_key="second-pick")])
    failing = FakeHttpClient(status_code=500)
    assert send_alerts(db, cfg, NOW, http_client=failing) == {
        "sent": 0, "skipped": 1, "dry_run": False}
    assert db.last_alert("walmart|lego set", "telegram") is None
    assert len(failing.posts) == 1  # failures also respect the per-run budget
    assert send_alerts(db, cfg, NOW, http_client=FakeHttpClient())["sent"] == 1


def test_deadline_defers_unrecorded_pick(db, cfg, monkeypatch):
    set_creds(monkeypatch)
    published(db, cfg)
    client = FakeHttpClient()
    assert send_alerts(db, cfg, NOW, http_client=client, deadline=0)["sent"] == 0
    assert client.posts == [] and db.last_alert("walmart|lego set", "telegram") is None


def test_admin_path_unchanged_despite_channel_caps_and_hours(db, cfg, monkeypatch):
    from dealradar.publish.admin_alerts import check_and_alert

    set_creds(monkeypatch)
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "999888")
    cfg.telegram_channel.update(posts_per_run=0, daily_max=0, hours_local=[0, 0])
    at = NOW.replace(hour=6)  # 2 AM New York
    for i in range(cfg.tripped_sources_warn):
        db.record_source_result(f"src{i}", ok=False, error="test", items=0, now=at,
                                trip_threshold=1, cooldown_h=6.0)
    client = FakeHttpClient()
    assert send_alerts(db, cfg, at, http_client=client)["sent"] == 0
    assert check_and_alert(db, cfg, at, items_fetched=0, http_client=client) == ["5 sources tripped"]
    [(_, payload)] = client.posts
    assert payload["chat_id"] == "999888" and payload["disable_web_page_preview"] is True
    assert payload["text"] == "dealradar admin warnings:\n5 sources tripped"
    assert db.alert_exists("admin", "2026-10-09T06|admin")


def test_only_deals_whose_page_an_earlier_deploy_put_live(db, cfg, monkeypatch):
    """The fast lane posts before its own deploy: a deal first seen this run waits."""
    import httpx
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test-bot-token-sentinel")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@wowpriced_test")
    cfg.telegram_channel["min_age_minutes"] = 20
    now = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)  # noon New York
    fresh = make_deal(dedupe_key="walmart|fresh", first_seen_at=(now - timedelta(minutes=5)).isoformat())
    older = make_deal(dedupe_key="walmart|older", first_seen_at=(now - timedelta(minutes=25)).isoformat())
    published(db, cfg, [fresh, older])
    sent = []
    transport = httpx.MockTransport(lambda request: sent.append(request) or httpx.Response(200, json={"ok": True}))
    with httpx.Client(transport=transport) as client:
        result = send_alerts(db, cfg, now, http_client=client, sleep_fn=lambda s: None)
    assert result["sent"] == 1 and len(sent) == 1
    assert "product-" in sent[0].content.decode()
    keys = [row[0] for row in db.conn.execute("SELECT deal_key FROM alerts_sent")]
    assert keys == ["walmart|older"]
