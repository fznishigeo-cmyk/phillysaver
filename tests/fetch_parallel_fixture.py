"""Fixed offline inputs for the task-236 pre-change compatibility recording."""

import dataclasses
import hashlib
import json
from pathlib import Path

import httpx
import yaml

from dealradar import http_client, pipeline
from dealradar.models import SourceConfig
from tests import test_e2e_offline as offline


# run_stats columns added after the recording (task 234); fetch never sets them.
LATER_COLUMNS = {"run_stats": {"posts_left", "deadline_hit"}}


def db_rows(db):
    tables = [r[0] for r in db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    rows = {}
    for table in tables:
        cursor = db.conn.execute(f'SELECT * FROM "{table}"')
        keep = [i for i, d in enumerate(cursor.description)
                if d[0] not in LATER_COLUMNS.get(table, ())]
        rows[table] = sorted([[row[i] for i in keep] for row in cursor],
                             key=lambda row: json.dumps(row, sort_keys=True))
    return rows


def snapshot(tmp_path, monkeypatch, parallel=1):
    env = offline._offline_env(tmp_path, monkeypatch)
    monkeypatch.setattr(http_client, "now_utc", lambda: offline.NOW)
    cfg_path = Path(env["cfg"])
    cfg = yaml.safe_load(cfg_path.read_text())
    cfg["fetch"]["parallel_sources"] = parallel
    cfg["parse"]["critic_tiering"]["enabled"] = False
    cfg["site"]["offscreen_cards"] = False  # recorded before task 237
    cfg["site"]["dark_polish"] = False  # recorded before task 254
    cfg["site"]["search_amazon"] = False  # recorded before task 259
    cfg["publish"]["front_page"].get("everyday_price", {})["enabled"] = False  # recorded before 2026-10-17 tilt
    cfg.get("join", {})["prompt_delay_s"] = 15  # recorded before the 35 s delay
    cfg["keepa"]["buybox_rule"] = "shadow"  # recorded before the Buy Box rule went on
    cfg.get("non_amazon_freshness", {})["stores"] = {}  # recorded before the Woot freshness override
    cfg["publish"]["front_page"].get("top_picks", {}).update(non_amazon_max_age_h=24) if "non_amazon_max_age_h" in cfg["publish"]["front_page"].get("top_picks", {}) else None  # recorded before 8 h
    cfg["site"]["static_photos"] = False  # recorded before task 274
    cfg["keepa"]["click_price_rule"] = "off"  # recorded before task 275
    cfg["publish"]["amazon_reference"] = "history"  # recorded before task 276
    cfg["site"]["strip_comments"] = False  # recorded before task 278
    cfg["publish"]["front_page"]["used_tab_only"] = False  # recorded before task 274
    cfg["site"]["open_box_scarcity"] = False  # recorded before task 273
    cfg["publish"]["front_page"]["appeal"]["want"]["enabled"] = False
    cfg["publish"]["front_page"]["appeal"]["season"] = []
    cfg["amazon_history"] = "hide"  # recorded before amz378_998
    cfg["checker"]["stores"]["amazon"] = "link_only"  # retain that recording's compatible policy
    cfg["seo"]["product_pages"]["enabled"] = True  # recorded before the 2026-10-17 history removal
    cfg["store_policy"] = dict.fromkeys(cfg["store_policy"], "history")  # likewise
    cfg_path.write_text(yaml.safe_dump(cfg))
    sources = offline.SOURCES + [
        SourceConfig("broken", "rss", "https://broken.example/feed"),
        SourceConfig("blocked", "rss", "https://blocked.example/feed"),
        SourceConfig("unknown", "api", "https://unknown.example/api", adapter="unknown"),
        SourceConfig("disabled", "rss", "https://disabled.example/feed", enabled=False),
    ]

    def handler(req):
        if req.url.host == "blocked.example":
            assert req.url.path == "/robots.txt"
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return offline._handler(req)

    observed = {}
    fetch = pipeline._fetch_all

    def capture(sources, fetcher, db, *args, **kwargs):
        posts, errors = fetch(sources, fetcher, db, *args, **kwargs)
        # Text stays in the existing input fixtures; compare every transient
        # field via a digest without copying post bodies to the recording.
        # Task 253 added RawPost.links after this recording; digest the recorded fields.
        observed["posts"] = [hashlib.sha256(json.dumps(
            {k: v for k, v in dataclasses.asdict(p).items() if k != "links"}, sort_keys=True,
            default=lambda v: sorted(v) if isinstance(v, (set, frozenset)) else v.isoformat()
        ).encode()).hexdigest() for p in posts]
        observed["errors"] = list(errors.items())
        observed["db"] = db_rows(db)
        return posts, errors

    monkeypatch.setattr(pipeline, "_fetch_all", capture)
    summary = pipeline.run(
        db_path=env["db"], config_path=env["cfg"], dry_run_telegram=True,
        transport=httpx.MockTransport(handler), sources_override=sources,
        skip_validation=True, claude_client=offline._FakeClaudeClient(),
    )
    observed["site"] = {str(p.relative_to(env["site"])): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted(env["site"].rglob("*")) if p.is_file()}
    return json.loads(json.dumps(observed)), summary
