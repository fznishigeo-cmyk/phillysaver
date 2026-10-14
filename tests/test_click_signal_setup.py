"""Run the click setup/deploy workflow scripts offline with a fake Wrangler."""
import json
import os
import shutil
import subprocess
import sys
import tomllib

import httpx
import pytest
import yaml

from dealradar import clicks
from dealradar.config import load_config
from dealradar.http_client import PoliteFetcher
from tests.test_click_signal import NOW, response
from tests.test_seo import db
from tests.test_workflows import DEPLOY_WORKER, FAST, HOURLY, ROOT, load_workflow, single_job


DATABASE = "11111111-1111-1111-1111-111111111111"
OVERRIDE = "22222222-2222-2222-2222-222222222222"
TOKEN = "fixture-click-token-never-print-123456789"
LIST = ["d1", "list", "--json"]
CREATE = ["d1", "create", "wowpriced-clicks", "--update-config=false"]


@pytest.fixture
def setup_env(tmp_path):
    for relative in ("cloudflare/subscribe-worker/wrangler.toml", "scripts/click_signal_deploy.py",
                     "dealradar/__init__.py", "dealradar/config.py", "dealradar/models.py",
                     "dealradar/clicks.py", "dealradar/util.py", "dealradar/source_policy.py",
                     "sources.yaml"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    config = yaml.safe_load((ROOT / "config.yaml").read_text())
    config["first_party_clicks"]["enabled"] = True
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(sys.executable)
    wrangler = bin_dir / "wrangler"
    wrangler.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
root = Path(os.environ["FIXTURE_ROOT"])
with (root / "calls").open("a") as f:
    f.write(json.dumps(args) + "\\n")
failure = os.environ.get("FAILURE", "")
if args[:2] == ["d1", "list"]:
    if failure == "list":
        print(os.environ["CLOUDFLARE_API_TOKEN"])
        print(os.environ["CLICK_SIGNAL_TOKEN"], file=sys.stderr)
        sys.exit(1)
    if failure == "malformed":
        print(os.environ["CLICK_SIGNAL_TOKEN"])
    else:
        rows = [{"name": "unrelated", "uuid": "33333333-3333-3333-3333-333333333333"}]
        if (os.environ.get("EXISTS") == "1" or (root / "created").exists()) and failure != "missing":
            rows.append({"name": "wowpriced-clicks", "uuid": os.environ["DATABASE"]})
        print(json.dumps(rows))
elif args[:2] == ["d1", "create"]:
    if failure == "create":
        print(os.environ["CLOUDFLARE_API_TOKEN"])
        print(os.environ["CLICK_SIGNAL_TOKEN"], file=sys.stderr)
        sys.exit(1)
    (root / "created").touch()
elif args[:2] == ["d1", "execute"]:
    pass
elif args[:1] == ["deploy"]:
    print("https://wowpriced-subscribe.wowpriced.workers.dev")
elif args[:3] == ["secret", "put", "CLICK_SIGNAL_TOKEN"]:
    assert sys.stdin.read() == os.environ["CLICK_SIGNAL_TOKEN"]
else:
    sys.exit(2)
''')
    wrangler.chmod(0o755)
    return {
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "FIXTURE_ROOT": str(tmp_path), "DATABASE": DATABASE,
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "CLICK_SIGNAL_TOKEN": TOKEN, "CLICK_SIGNAL_D1_ID": "",
        "CLOUDFLARE_API_TOKEN": "fixture-cloudflare-token-never-print",
        "KV_ID": "fixture-kv-id",
    }


def run_step(tmp_path, env, step_id="clicks"):
    steps = single_job(load_workflow(DEPLOY_WORKER))["steps"]
    step = next(s for s in steps if s.get("id") == step_id)
    result = subprocess.run(["bash", "-c", step["run"]], env=env,
                            cwd=tmp_path / "cloudflare/subscribe-worker",
                            capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    for secret in (TOKEN, env["CLOUDFLARE_API_TOKEN"], OVERRIDE):
        assert secret not in output
    return result


def calls(tmp_path):
    path = tmp_path / "calls"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.parametrize("existing,override,expected", [
    (True, "", [LIST]), (False, "", [LIST, CREATE, LIST]),
    (True, OVERRIDE, []), (False, OVERRIDE, []),
])
def test_lookup_create_override_and_deploy_binding(tmp_path, setup_env, existing, override, expected):
    env = dict(setup_env, EXISTS=str(int(existing)), CLICK_SIGNAL_D1_ID=override)
    original = (tmp_path / "cloudflare/subscribe-worker/wrangler.toml").read_bytes()
    result = run_step(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert calls(tmp_path) == expected
    output = tmp_path / "output"
    if override:
        assert not output.exists()  # GitHub drops step outputs containing secrets.
    else:
        assert output.read_text() == "id=" + DATABASE + "\n"
    # Re-running a successful creation reuses the database.
    result = run_step(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert calls(tmp_path) == expected + ([] if override else [LIST])
    env["CLICK_SIGNAL_D1_ID"] = override or DATABASE
    result = run_step(tmp_path, env, "deploy")
    assert result.returncode == 0, result.stdout + result.stderr
    worker = tmp_path / "cloudflare/subscribe-worker"
    assert (worker / "wrangler.toml").read_bytes() == original
    deployed = tomllib.loads((worker / "wrangler.deploy.toml").read_text())
    assert deployed["d1_databases"] == [{
        "binding": "CLICKS", "database_name": "wowpriced-clicks", "database_id": override or DATABASE,
    }]
    assert deployed["kv_namespaces"][0]["id"] == env["KV_ID"]
    assert deployed["vars"]["CLICK_SIGNAL_ENABLED"] == "true"
    assert TOKEN not in (worker / "wrangler.deploy.toml").read_text()
    assert calls(tmp_path)[-3:] == [
        ["d1", "execute", "wowpriced-clicks", "--remote", "--config", "wrangler.deploy.toml", "--file", "clicks-schema.sql"],
        ["deploy", "--config", "wrangler.deploy.toml"],
        ["secret", "put", "CLICK_SIGNAL_TOKEN", "--config", "wrangler.deploy.toml"],
    ]


@pytest.mark.parametrize("changes,message,expected", [
    ({"CLICK_SIGNAL_TOKEN": ""}, "CLICK_SIGNAL_TOKEN repository secret is not set", []),
    ({"CLICK_SIGNAL_TOKEN": "too-short"}, "32+ characters", []),
    ({"FAILURE": "list"}, "Cannot list D1", [LIST]),
    ({"FAILURE": "create"}, "Cannot create D1", [LIST, CREATE]),
    ({"FAILURE": "missing"}, "not found after creating", [LIST, CREATE, LIST]),
    ({"FAILURE": "malformed"}, "Cannot read the D1 database list", [LIST]),
    ({"CLICK_SIGNAL_D1_ID": "invalid-id"}, "must be a database UUID", []),
    ({"EXISTS": "1", "DATABASE": "invalid-id"}, "must be a database UUID", [LIST]),
])
def test_setup_fails_clearly_without_leaking_api_output(tmp_path, setup_env, changes, message, expected):
    result = run_step(tmp_path, dict(setup_env, **changes))
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "::error::" in output and message in output
    if changes.get("FAILURE") in {"list", "create"}:
        assert "CLOUDFLARE_API_TOKEN needs Account → D1 → Edit" in output
    assert calls(tmp_path) == expected
    assert not (tmp_path / "output").exists()


def test_disabled_setup_and_deploy_need_no_click_secrets_or_d1(tmp_path, setup_env):
    shutil.copyfile(ROOT / "config.yaml", tmp_path / "config.yaml")
    env = dict(setup_env, CLICK_SIGNAL_TOKEN="", CLICK_SIGNAL_D1_ID="")
    assert run_step(tmp_path, env).returncode == 0
    assert calls(tmp_path) == [] and not (tmp_path / "output").exists()
    result = run_step(tmp_path, env, "deploy")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls(tmp_path) == [["deploy", "--config", "wrangler.deploy.toml"]]
    worker = tmp_path / "cloudflare/subscribe-worker"
    assert (worker / "wrangler.deploy.toml").read_text() == (worker / "wrangler.toml").read_text().replace(
        "REPLACED_AT_DEPLOY", env["KV_ID"])


def test_config_docs_and_workflows_share_one_secret_and_existing_worker():
    cfg = load_config()
    assert cfg.raw["first_party_clicks"]["enabled"] is False
    assert clicks.endpoint(cfg) == cfg.newsletter_worker_url == cfg.paywall_api_url
    docs = (ROOT / "docs/first-party-clicks.md").read_text()
    operator = docs.split("Operator:\n", 1)[1].split("Engineer", 1)[0]
    assert "1." in operator and "2. Tell Claude" in operator and "3." not in operator
    assert "CLICK_SIGNAL_TOKEN" in operator and "CLICK_SIGNAL_D1_ID" not in operator
    for phrase in ("`CLICK_SIGNAL_D1_ID` is optional", "Account → D1 → Edit",
                   "first_party_clicks.enabled: true", "deploy subscribe worker",
                   "click_signal.status: ok", clicks.endpoint(cfg)):
        assert phrase in docs
    steps = single_job(load_workflow(DEPLOY_WORKER))["steps"]
    setup = next(s for s in steps if s.get("id") == "clicks")
    deploy = next(s for s in steps if s.get("id") == "deploy")
    assert steps.index(setup) < steps.index(deploy)
    assert setup["env"]["CLICK_SIGNAL_D1_ID"] == "${{ secrets.CLICK_SIGNAL_D1_ID }}"
    assert deploy["env"]["CLICK_SIGNAL_D1_ID"] == "${{ secrets.CLICK_SIGNAL_D1_ID || steps.clicks.outputs.id }}"
    for step in [setup, deploy] + [next(s for s in single_job(load_workflow(p))["steps"]
                                     if s.get("id") == "pipeline") for p in (FAST, HOURLY)]:
        assert step["env"]["CLICK_SIGNAL_TOKEN"] == "${{ secrets.CLICK_SIGNAL_TOKEN }}"


def test_shipped_config_only_needs_switch_and_token_for_ingestion(db, monkeypatch):
    cfg = load_config()
    monkeypatch.setenv("CLICK_SIGNAL_TOKEN", TOKEN)
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url).split("?")[0] == cfg.newsletter_worker_url + "/c/counts"
        assert request.headers["Authorization"] == "Bearer " + TOKEN
        since = int(request.url.params["since"])
        return httpx.Response(200, json=response(since, since + 6, "a" * 16))

    fetcher = PoliteFetcher(db, cfg, transport=httpx.MockTransport(handler), sleep_fn=lambda _: None)
    try:
        assert clicks.ingest(db, cfg, NOW, fetcher, "off")["status"] == "off"
        assert requests == []
        cfg.raw["first_party_clicks"]["enabled"] = True
        assert clicks.ingest(db, cfg, NOW, fetcher, "on")["status"] == "ok"
        assert len(requests) == 1
    finally:
        fetcher.close()
