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
    cfg["amazon_history"] = "hide"  # recorded before amz378_998
    cfg["checker"]["stores"]["amazon"] = "link_only"  # retain that recording's compatible policy
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
