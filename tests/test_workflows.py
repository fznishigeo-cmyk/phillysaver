"""Tests for the ops layer: GitHub Actions workflows, scripts/commit_state.sh,
and README.md. Fully offline — the commit-state script is exercised against
local temporary git repositories (file:// remotes), never the network.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
HOURLY = WORKFLOWS / "hourly.yml"
FAST = WORKFLOWS / "fast.yml"
SQUASH = WORKFLOWS / "squash-data.yml"
DEPLOY_WORKER = WORKFLOWS / "deploy-subscribe-worker.yml"
WORKER_DIR = ROOT / "cloudflare" / "subscribe-worker"
SCRIPT = ROOT / "scripts" / "commit_state.sh"
RESTORE = ROOT / "scripts" / "restore_state.sh"
CRYPT = ROOT / "scripts" / "state_crypt.sh"
README = ROOT / "README.md"

SECRET_NAMES = [
    "DEALS_CLAUDE_API_KEY",
    "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT",
    "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "TELEGRAM_ADMIN_CHAT_ID",
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
    "WOOT_API_KEY", "MEH_API_KEY", "KEEPA_KEY", "THREADS_TOKEN",
    "STRIPE_SECRET_KEY", "NEWSLETTER_SECRET",
]


def load_workflow(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text())
    assert isinstance(doc, dict)
    return doc


def workflow_on(doc: dict) -> dict:
    # YAML 1.1 parses the bare key `on` as boolean True.
    return doc.get("on", doc.get(True))


def single_job(doc: dict) -> dict:
    jobs = doc["jobs"]
    assert len(jobs) == 1
    return next(iter(jobs.values()))


def run_commands(doc: dict) -> list[str]:
    return [s["run"] for s in single_job(doc)["steps"] if "run" in s]


def assert_state_steps_get_the_key(steps: list[dict]) -> None:
    """Restore and commit both see STATE_DB_KEY (decrypt / encrypt state.db);
    the pipeline step does not need it."""
    for script in ("restore_state.sh", "commit_state.sh"):
        step = next(s for s in steps if script in s.get("run", ""))
        assert step["env"] == {"STATE_DB_KEY": "${{ secrets.STATE_DB_KEY }}"}


# ---------------------------------------------------------------- YAML shape


def test_all_workflows_parse() -> None:
    for p in (HOURLY, FAST, SQUASH, DEPLOY_WORKER):
        assert p.is_file(), p
        load_workflow(p)


def test_hourly_shape() -> None:
    doc = load_workflow(HOURLY)
    assert doc["name"] == "dealradar hourly"
    on = workflow_on(doc)
    assert on["schedule"] == [{"cron": "0 * * * *"}]
    assert "workflow_dispatch" in on
    assert doc["concurrency"] == {"group": "dealradar", "cancel-in-progress": False}
    assert doc["permissions"] == {
        "contents": "write", "pages": "write", "id-token": "write",
    }
    job = single_job(doc)
    assert job["timeout-minutes"] == 15

    steps = job["steps"]
    uses = [s.get("uses", "") for s in steps]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["uses"] == "actions/checkout@v4"
    assert checkout["with"]["fetch-depth"] == 0
    setup = next(s for s in steps if s.get("uses", "").startswith("actions/setup-python@"))
    assert setup["uses"] == "actions/setup-python@v5"
    assert str(setup["with"]["python-version"]) == "3.12"
    assert setup["with"]["cache"] == "pip"

    runs = run_commands(doc)
    assert any(r.strip() == "pip install -e ." for r in runs)
    assert any(r.strip() == "bash scripts/restore_state.sh" for r in runs)
    assert any(r.strip() == "python -m dealradar run" for r in runs)
    assert any("bash scripts/commit_state.sh" in r for r in runs)
    assert_state_steps_get_the_key(steps)

    # Official Pages artifact actions only — no gh-pages branch commits.
    assert "actions/configure-pages@v5" in uses
    upload = next(s for s in steps if s.get("uses") == "actions/upload-pages-artifact@v3")
    assert upload["with"]["path"] == "site"
    assert "actions/deploy-pages@v4" in uses

    run_step = next(s for s in steps if s.get("run", "").strip() == "python -m dealradar run")
    assert set(run_step["env"]) == set(SECRET_NAMES)
    for name, value in run_step["env"].items():
        assert value == "${{ secrets.%s }}" % name


def test_fast_shape() -> None:
    doc = load_workflow(FAST)
    assert doc["name"] == "dealradar fast lane"
    on = workflow_on(doc)
    assert on["schedule"] == [{"cron": "*/10 * * * *"}]
    # Same concurrency group as hourly so the two never overlap.
    assert doc["concurrency"] == {"group": "dealradar", "cancel-in-progress": False}
    job = single_job(doc)
    assert job["timeout-minutes"] == 12

    runs = run_commands(doc)
    assert any(r.strip() == "python -m dealradar run --fast-lane" for r in runs)
    assert any(r.strip() == "bash scripts/restore_state.sh" for r in runs)
    assert any("bash scripts/commit_state.sh" in r for r in runs)
    assert_state_steps_get_the_key(job["steps"])

    # The fast lane deploys the site too: a 15-minute-lane find (Keepa price
    # drop) must reach the page in minutes, not wait for the hourly deploy.
    uses = " ".join(s.get("uses", "") for s in job["steps"])
    assert "actions/upload-pages-artifact" in uses
    assert "actions/deploy-pages" in uses
    assert doc["permissions"]["pages"] == "write"
    assert doc["permissions"]["id-token"] == "write"

    run_step = next(s for s in job["steps"]
                    if s.get("run", "").strip() == "python -m dealradar run --fast-lane")
    assert set(run_step["env"]) == set(SECRET_NAMES)


@pytest.mark.parametrize("path", [HOURLY, FAST])
def test_indexnow_step_runs_after_every_deploy_and_never_fails_the_run(path: Path) -> None:
    steps = single_job(load_workflow(path))["steps"]
    step = next(s for s in steps if s.get("run", "").strip() == "python -m dealradar indexnow")
    assert step["continue-on-error"] is True
    assert "env" not in step  # needs no secret: the key is public by design
    at = steps.index(step)
    for i, s in enumerate(steps):
        if s.get("uses", "").startswith("actions/deploy-pages@") or "pages deploy site" in s.get("run", ""):
            assert i < at, (path.name, s.get("name"))


def test_squash_shape() -> None:
    doc = load_workflow(SQUASH)
    assert doc["name"] == "squash data branch"
    on = workflow_on(doc)
    assert on["schedule"] == [{"cron": "17 3 1 * *"}]
    assert "workflow_dispatch" in on
    assert doc["permissions"] == {"contents": "write"}
    body = "\n".join(run_commands(doc))
    assert "git checkout --orphan" in body
    assert "squash data branch (monthly)" in body
    assert "git push --force-with-lease=refs/heads/data:" in body


def test_deploy_subscribe_worker_shape() -> None:
    """Manual deploy of the subscribe Worker: dispatch only, read-only token,
    KV namespace found-or-created, id substituted on a copy, secret piped in."""
    doc = load_workflow(DEPLOY_WORKER)
    assert doc["name"] == "deploy subscribe worker"
    on = workflow_on(doc)
    assert "workflow_dispatch" in on and "schedule" not in on
    assert doc["permissions"] == {"contents": "read"}
    job = single_job(doc)
    assert job["defaults"]["run"]["working-directory"] == "cloudflare/subscribe-worker"
    assert job["env"] == {
        "CLOUDFLARE_API_TOKEN": "${{ secrets.CLOUDFLARE_API_TOKEN }}",
        "CLOUDFLARE_ACCOUNT_ID": "${{ secrets.CLOUDFLARE_ACCOUNT_ID }}",
    }
    steps = job["steps"]
    node = next(s for s in steps if s.get("uses", "").startswith("actions/setup-node@"))
    assert node["uses"] == "actions/setup-node@v4"
    assert str(node["with"]["node-version"]) == "20"

    body = "\n".join(run_commands(doc))
    assert "npm install -g wrangler" in body
    assert "wrangler kv namespace list" in body
    assert "wrangler kv namespace create wowpriced_subs" in body
    assert 'sed "s/REPLACED_AT_DEPLOY/$KV_ID/" wrangler.toml > wrangler.deploy.toml' in body
    assert "wrangler deploy --config wrangler.deploy.toml" in body
    assert "wrangler secret put NEWSLETTER_SECRET" in body
    assert "GITHUB_STEP_SUMMARY" in body and "newsletter.worker_url" in body
    assert "git push" not in body
    # The list secret reaches wrangler through the step env only.
    put = next(s for s in steps if "wrangler secret put" in s.get("run", ""))
    assert put["env"] == {"NEWSLETTER_SECRET": "${{ secrets.NEWSLETTER_SECRET }}"}


def _stripe_mode_step() -> tuple[list[dict], dict]:
    steps = single_job(load_workflow(DEPLOY_WORKER))["steps"]
    return steps, next(s for s in steps if s.get("name") == "Report the Stripe key mode")


def test_deploy_subscribe_worker_stripe_settings() -> None:
    """STRIPE_PRICE_ID is a dispatch input substituted like the other vars
    (SITE_URL stays in wrangler.toml); the key-mode report reads the secret
    through its step env only, after the deploy wrote wrangler.deploy.toml."""
    inputs = workflow_on(load_workflow(DEPLOY_WORKER))["workflow_dispatch"]["inputs"]
    assert inputs["stripe_price_id"]["required"] is False
    assert inputs["stripe_price_id"]["default"] == ""
    steps, mode = _stripe_mode_step()
    deploy = next(s for s in steps if s.get("id") == "deploy")
    assert deploy["env"]["STRIPE_PRICE_ID"] == "${{ inputs.stripe_price_id }}"
    assert '"STRIPE_PRICE_ID"' in deploy["run"]
    assert "SITE_URL" not in deploy["env"]
    assert mode["env"] == {"STRIPE_SECRET_KEY": "${{ secrets.STRIPE_SECRET_KEY }}"}
    assert steps.index(mode) > steps.index(deploy)
    assert "wrangler.deploy.toml" in mode["run"]


@pytest.mark.parametrize("key, link, want", [
    ("sk_live_FAKEKEY", "https://buy.stripe.com/abc123",
     ["::notice::Stripe key mode: live", "::notice::Payment Link mode: live"]),
    ("rk_live_FAKEKEY", "https://buy.stripe.com/test_abc123",
     ["::notice::Stripe key mode: live", "::warning::Payment Link is a TEST link", "::warning::Stripe key mode (live) and Payment Link mode (test) differ"]),
    ("sk_test_FAKEKEY", "https://buy.stripe.com/abc123",
     ["::warning::Stripe key is a TEST key: live checkouts cannot be verified", "::notice::Payment Link mode: live",
      "::warning::Stripe key mode (test) and Payment Link mode (live) differ"]),
    ("rk_test_FAKEKEY", "https://buy.stripe.com/test_abc123",
     ["::warning::Stripe key is a TEST key", "::warning::Payment Link is a TEST link"]),
    ("whsec_FAKEKEY", "", ["::warning::Stripe key mode: unrecognized", "::notice::Payment Link mode: not set"]),
    ("", "https://buy.stripe.com/abc123", ["::notice::Stripe key mode: not set", "::notice::Payment Link mode: live"]),
])
def test_deploy_subscribe_worker_reports_stripe_mode(tmp_path: Path, key: str, link: str, want: list[str]) -> None:
    """The step's own script, run against a deployed-style wrangler.toml copy:
    live/test from the key prefix and the link path, a warning when they
    differ, and never the key itself in the output."""
    _, mode = _stripe_mode_step()
    toml = (WORKER_DIR / "wrangler.toml").read_text()
    if link:  # what the deploy step writes for a non-empty input
        toml = re.sub(r"^STRIPE_PAYMENT_LINK = .*$", "STRIPE_PAYMENT_LINK = " + json.dumps(link), toml, flags=re.M)
    (tmp_path / "wrangler.deploy.toml").write_text(toml)
    res = subprocess.run(["bash", "-c", mode["run"]], cwd=tmp_path, capture_output=True, text=True, timeout=30,
                         env={"PATH": os.environ["PATH"], "STRIPE_SECRET_KEY": key})
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    for needle in want:
        assert any(line.startswith(needle) for line in lines), (needle, lines)
    assert sum("differ" in line for line in lines) == sum("differ" in w for w in want)
    assert "FAKEKEY" not in res.stdout + res.stderr


@pytest.mark.parametrize("portal, warned", [("", True), ("https://billing.stripe.com/p/login/abc123", False)])
def test_deploy_subscribe_worker_warns_without_portal_link(tmp_path: Path, portal: str, warned: bool) -> None:
    """An empty STRIPE_PORTAL_LINK after substitution is a warning: members
    whose customer the Worker cannot find have no other online way in."""
    _, mode = _stripe_mode_step()
    toml = (WORKER_DIR / "wrangler.toml").read_text()
    if portal:  # what the deploy step writes for a non-empty input
        toml = re.sub(r"^STRIPE_PORTAL_LINK = .*$", "STRIPE_PORTAL_LINK = " + json.dumps(portal), toml, flags=re.M)
    (tmp_path / "wrangler.deploy.toml").write_text(toml)
    res = subprocess.run(["bash", "-c", mode["run"]], cwd=tmp_path, capture_output=True, text=True, timeout=30,
                         env={"PATH": os.environ["PATH"], "STRIPE_SECRET_KEY": "sk_live_FAKEKEY"})
    assert res.returncode == 0, res.stderr
    want = ("::warning::STRIPE_PORTAL_LINK is empty: members without a bound customer "
            "will rely on /api/portal discovery")
    assert (want in res.stdout.splitlines()) is warned, res.stdout


# ------------------------------------------------------------------- safety


def test_no_workflow_pushes_to_main() -> None:
    """Every git push in any workflow or in commit_state.sh targets data only."""
    texts = {p.name: p.read_text() for p in (HOURLY, FAST, SQUASH, DEPLOY_WORKER, SCRIPT)}
    for name, text in texts.items():
        for line in text.splitlines():
            if "git push" in line or re.search(r"\bpush origin\b", line):
                assert "main" not in line and "master" not in line, (name, line)
                assert ":data" in line or '"HEAD:$BRANCH"' in line, (name, line)


def test_no_secret_is_echoed() -> None:
    """`${{ secrets.X }}` appears only as env-map values, never inside run/echo."""
    env_line = re.compile(r"^\s+[A-Z_]+: \$\{\{ secrets\.[A-Z_]+ \}\}$")
    for p in (HOURLY, FAST, SQUASH, DEPLOY_WORKER):
        for line in p.read_text().splitlines():
            if "secrets." in line:
                assert env_line.match(line), (p.name, line)
            if "echo" in line:
                assert "secrets." not in line and "$DEALS" not in line, (p.name, line)
    for path in (SCRIPT, RESTORE, CRYPT):
        script = path.read_text()
        for name in SECRET_NAMES:
            assert name not in script
        # The state key reaches openssl through the environment only.
        assert "$STATE_DB_KEY" not in script and "${STATE_DB_KEY}" not in script


# ----------------------------------------------------------- commit_state.sh


def test_commit_state_script_static() -> None:
    assert SCRIPT.is_file()
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "scripts/commit_state.sh must be executable"
    text = SCRIPT.read_text()
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text
    assert "dealradar-bot" in text and "actions@users.noreply.github.com" in text
    assert "[skip ci]" in text
    assert re.search(r"for delay in 2 4 8 16", text), "backoff schedule 2 4 8 16"
    assert "trap cleanup EXIT" in text
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com",
         "-c", "protocol.file.allow=always", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
             "GIT_CONFIG_SYSTEM": "/dev/null"},
    )
    return res.stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A local clone with a file:// origin holding one commit on main."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "-b", "main", ".")
    clone = tmp_path / "clone"
    clone.mkdir()
    _git(clone, "init", "-b", "main", ".")
    (clone / "hello.txt").write_text("hi\n")
    _git(clone, "add", "hello.txt")
    _git(clone, "commit", "-m", "init")
    _git(clone, "remote", "add", "origin", origin.as_uri())
    _git(clone, "push", "origin", "main")
    return clone


def run_script(repo_dir: Path, script: Path = SCRIPT,
               key: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_CONFIG_SYSTEM": "/dev/null"}
    env.pop("STATE_DB_KEY", None)
    if key is not None:
        env["STATE_DB_KEY"] = key
    return subprocess.run(["bash", str(script)], cwd=repo_dir,
                          capture_output=True, text=True, env=env)


def origin_branches(repo_dir: Path) -> set[str]:
    out = _git(repo_dir, "ls-remote", "--heads", "origin")
    return {line.split("refs/heads/")[-1] for line in out.splitlines() if line}


def test_commit_state_noop_without_state_db(repo: Path) -> None:
    res = run_script(repo)
    assert res.returncode == 0, res.stderr
    assert "no-op" in res.stdout
    assert origin_branches(repo) == {"main"}


def test_commit_state_creates_orphan_data_branch(repo: Path) -> None:
    (repo / "state.db").write_bytes(b"sqlite-bytes-v1")
    res = run_script(repo)
    assert res.returncode == 0, res.stderr + res.stdout
    assert origin_branches(repo) == {"main", "data"}

    _git(repo, "fetch", "origin", "data")
    # Single orphan commit, bot author, [skip ci] message, no parents.
    log = _git(repo, "log", "--format=%an|%ae|%s", "origin/data")
    assert log.splitlines() == [
        "dealradar-bot|actions@users.noreply.github.com|" + log.split("|")[2]
    ]
    subject = log.split("|")[2]
    assert re.fullmatch(r"state: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z \[skip ci\]",
                        subject)
    files = _git(repo, "ls-tree", "--name-only", "origin/data")
    assert files.splitlines() == ["state.db"]
    # main untouched; worktree cleaned up.
    assert "hello.txt" in _git(repo, "ls-tree", "--name-only", "origin/main")
    assert not (repo / ".state-worktree").exists()


def test_commit_state_noop_when_unchanged(repo: Path) -> None:
    (repo / "state.db").write_bytes(b"sqlite-bytes-v1")
    assert run_script(repo).returncode == 0
    res = run_script(repo)  # identical bytes -> nothing to commit
    assert res.returncode == 0, res.stderr
    assert "no-op" in res.stdout
    _git(repo, "fetch", "origin", "data")
    assert _git(repo, "rev-list", "--count", "origin/data") == "1"


def test_commit_state_appends_and_includes_report(repo: Path) -> None:
    (repo / "state.db").write_bytes(b"sqlite-bytes-v1")
    assert run_script(repo).returncode == 0
    (repo / "state.db").write_bytes(b"sqlite-bytes-v2")
    (repo / "sources_report.md").write_text("# report\n")
    res = run_script(repo)
    assert res.returncode == 0, res.stderr + res.stdout
    _git(repo, "fetch", "origin", "data")
    assert _git(repo, "rev-list", "--count", "origin/data") == "2"
    files = set(_git(repo, "ls-tree", "--name-only", "origin/data").splitlines())
    assert files == {"state.db", "sources_report.md"}
    assert not (repo / ".state-worktree").exists()


# ----------------------------------------------- encrypted state (public repo)

KEY = "correct horse battery staple"
SQLITE_V1 = b"SQLite format 3\x00" + b"member deal: secret-link-v1 " * 8
SQLITE_V2 = b"SQLite format 3\x00" + b"member deal: secret-link-v2 " * 8


def branch_blob(repo: Path, path: str) -> bytes:
    _git(repo, "fetch", "origin", "data")
    return subprocess.run(["git", "cat-file", "blob", f"origin/data:{path}"], cwd=repo,
                          check=True, capture_output=True).stdout


def decrypt(blob: bytes, tmp_path: Path, key: str = KEY) -> bytes:
    enc, out = tmp_path / "in.enc", tmp_path / "out.db"
    enc.write_bytes(blob)
    subprocess.run(["bash", "-c", '. "$0"; state_decrypt "$1" "$2"', str(CRYPT), str(enc), str(out)],
                   check=True, capture_output=True, env={**os.environ, "STATE_DB_KEY": key})
    return out.read_bytes()


def test_state_scripts_static() -> None:
    assert RESTORE.stat().st_mode & stat.S_IXUSR
    assert RESTORE.read_text().startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in RESTORE.read_text()
    assert "-pass env:STATE_DB_KEY" in CRYPT.read_text()
    for p in (RESTORE, CRYPT):
        subprocess.run(["bash", "-n", str(p)], check=True)


def test_commit_state_encrypts_with_key(repo: Path, tmp_path: Path) -> None:
    (repo / "state.db").write_bytes(SQLITE_V1)
    res = run_script(repo, key=KEY)
    assert res.returncode == 0, res.stderr + res.stdout
    files = _git(repo, "ls-tree", "--name-only", "origin/data").splitlines()
    assert files == ["state.db.enc"]
    blob = branch_blob(repo, "state.db.enc")
    assert b"secret-link" not in blob and b"SQLite format" not in blob
    assert decrypt(blob, tmp_path) == SQLITE_V1


def test_commit_state_encrypted_noop_when_unchanged(repo: Path) -> None:
    (repo / "state.db").write_bytes(SQLITE_V1)
    assert run_script(repo, key=KEY).returncode == 0
    res = run_script(repo, key=KEY)  # same plaintext -> no new ciphertext
    assert res.returncode == 0, res.stderr
    assert "no-op" in res.stdout
    _git(repo, "fetch", "origin", "data")
    assert _git(repo, "rev-list", "--count", "origin/data") == "1"


def test_commit_state_moves_a_clear_branch_to_encrypted(repo: Path, tmp_path: Path) -> None:
    (repo / "state.db").write_bytes(SQLITE_V1)
    res = run_script(repo)  # no key yet: today's behaviour, with a warning
    assert res.returncode == 0 and "::warning::" in res.stdout
    (repo / "state.db").write_bytes(SQLITE_V2)
    (repo / "sources_report.md").write_text("# report\n")
    assert run_script(repo, key=KEY).returncode == 0
    _git(repo, "fetch", "origin", "data")
    files = set(_git(repo, "ls-tree", "--name-only", "origin/data").splitlines())
    assert files == {"state.db.enc", "sources_report.md"}
    assert decrypt(branch_blob(repo, "state.db.enc"), tmp_path) == SQLITE_V2


def test_commit_state_never_commits_clear_text_over_an_encrypted_branch(repo: Path) -> None:
    (repo / "state.db").write_bytes(SQLITE_V1)
    assert run_script(repo, key=KEY).returncode == 0
    (repo / "state.db").write_bytes(SQLITE_V2)
    res = run_script(repo)  # key missing
    assert res.returncode != 0
    assert "STATE_DB_KEY is not set" in res.stdout
    _git(repo, "fetch", "origin", "data")
    assert _git(repo, "rev-list", "--count", "origin/data") == "1"
    assert _git(repo, "ls-tree", "--name-only", "origin/data").splitlines() == ["state.db.enc"]
    assert not (repo / ".state-worktree").exists()


def test_restore_state_without_a_data_branch(repo: Path) -> None:
    res = run_script(repo, RESTORE, key=KEY)
    assert res.returncode == 0, res.stderr
    assert "no prior state" in res.stdout
    assert not (repo / "state.db").exists()


def test_restore_state_reads_a_clear_branch(repo: Path) -> None:
    (repo / "state.db").write_bytes(SQLITE_V1)
    assert run_script(repo).returncode == 0
    (repo / "state.db").unlink()
    res = run_script(repo, RESTORE)
    assert res.returncode == 0, res.stderr
    assert (repo / "state.db").read_bytes() == SQLITE_V1


def test_restore_state_decrypts(repo: Path) -> None:
    (repo / "state.db").write_bytes(SQLITE_V1)
    assert run_script(repo, key=KEY).returncode == 0
    (repo / "state.db").unlink()
    res = run_script(repo, RESTORE, key=KEY)
    assert res.returncode == 0, res.stderr + res.stdout
    assert (repo / "state.db").read_bytes() == SQLITE_V1


@pytest.mark.parametrize("key", [None, "", "the wrong key"])
def test_restore_state_fails_rather_than_start_empty(repo: Path, key: str | None) -> None:
    """A run that cannot read its state must stop: an empty state.db would be
    committed over the real one."""
    (repo / "state.db").write_bytes(SQLITE_V1)
    assert run_script(repo, key=KEY).returncode == 0
    (repo / "state.db").unlink()
    res = run_script(repo, RESTORE, key=key)
    assert res.returncode != 0
    assert not (repo / "state.db").exists()


# ------------------------------------------------------------------- README


def test_readme_covers_the_contract() -> None:
    text = README.read_text()
    # architecture + badges + floors
    assert "SAVINGS_RULES.md" in text and "sources_report.md" in text
    for badge in ("corroborated", "history-checked", "unverified"):
        assert badge in text
    from dealradar.config import load_config

    # The README's stated floor must match the configured one.
    assert f"min_pct: {load_config().min_pct:g}" in text
    assert "min_abs_savings" in text
    # secrets honesty
    assert "DEALS_CLAUDE_API_KEY" in text and "ANTHROPIC_API_KEY" in text
    assert "MTProto" in text
    # verbatim domain list spot checks
    for host in ("api.anthropic.com", "files.pythonhosted.org", "hip2save.com",
                 "www.tomsguide.com", "oauth.reddit.com", "graph.threads.net",
                 "149.154.160.0/20", "91.108.0.0/16", "consumerqueen.com"):
        assert host in text, host
    assert "python -m dealradar domains" in text
    # scheduling / state story
    assert "data" in text and "scripts/commit_state.sh" in text
    assert "GitHub Actions" in text and "cancel-in-progress" in text
    # cost + cap
    assert "spend_cap_usd_per_run" in text
    assert "$0.10/M input" in text and "$0.50/M output" in text
    # legal
    assert "robots.txt" in text
    assert "PA-API" in text and "rewrite_affiliate()" in text
    assert "ftc_disclosure" in text
    # ops
    assert "first_seen_at" in text and "TELEGRAM_ADMIN_CHAT_ID" in text


# ----------------------------------------------------- subscribe Worker


WORKER_HARNESS = r"""
import worker from "./worker.mjs";

// Workers KV, enough of it: values + metadata, prefix listing with cursors.
class FakeKV {
  constructor() { this.m = new Map(); }
  async get(k) { const v = this.m.get(k); return v ? v.value : null; }
  async put(k, value, opts = {}) { this.m.set(k, { value, metadata: opts.metadata || null }); }
  async delete(k) { this.m.delete(k); }
  async list({ prefix = "", cursor, limit = 1000 } = {}) {
    const keys = [...this.m.keys()].filter((k) => k.startsWith(prefix)).sort();
    const start = cursor ? Number(cursor) : 0;
    const page = keys.slice(start, start + limit)
      .map((name) => ({ name, metadata: this.m.get(name).metadata }));
    const done = start + limit >= keys.length;
    return { keys: page, list_complete: done, cursor: done ? undefined : String(start + limit) };
  }
}
const env = { SUBS: new FakeKV(), NEWSLETTER_SECRET: "s3cret" };
const BASE = "https://wowpriced-subscribe.example.workers.dev";
const call = (method, path, init = {}) => worker.fetch(new Request(BASE + path, { method, ...init }), env);
const JSON_HDR = { "Content-Type": "application/json", "Origin": "https://wowpriced.com",
                   "CF-Connecting-IP": "1.2.3.4" };
const FORM_HDR = { "Content-Type": "application/x-www-form-urlencoded", "CF-Connecting-IP": "5.6.7.8" };
const out = {};
let r;

r = await call("POST", "/subscribe", { body: JSON.stringify({ email: " You@Example.COM " }), headers: JSON_HDR });
out.json_subscribe = { status: r.status, body: await r.json(), cors: r.headers.get("Access-Control-Allow-Origin") };
const first = JSON.parse(await env.SUBS.get("sub:you@example.com"));
r = await call("POST", "/subscribe", { body: JSON.stringify({ email: "you@example.com" }), headers: JSON_HDR });
out.resubscribe = { status: r.status, same_token: JSON.parse(await env.SUBS.get("sub:you@example.com")).token === first.token };
out.token_shape = /^[0-9a-f]{32}$/.test(first.token) && typeof first.ts === "string";
out.metadata_token = env.SUBS.m.get("sub:you@example.com").metadata.token === first.token;

r = await call("POST", "/subscribe", { body: new URLSearchParams({ email: "Second@example.com" }),
                                       headers: { ...FORM_HDR, "Referer": "https://www.wowpriced.com/" } });
out.form_subscribe = { status: r.status, location: r.headers.get("Location") };
r = await call("POST", "/subscribe", { body: new URLSearchParams({ email: "not-an-email" }),
                                       headers: { ...FORM_HDR, "Referer": "https://evil.example/" } });
out.form_invalid = { status: r.status, location: r.headers.get("Location") };
r = await call("POST", "/subscribe", { body: JSON.stringify({ email: "nope@" }), headers: JSON_HDR });
out.json_invalid = { status: r.status, body: await r.json() };
r = await call("POST", "/subscribe", { body: JSON.stringify({ email: "x@y.com" }),
                                       headers: { ...JSON_HDR, "Origin": "https://evil.example" } });
out.foreign_origin_cors = r.headers.get("Access-Control-Allow-Origin");
out.stored_keys = [...env.SUBS.m.keys()].filter((k) => k.startsWith("sub:")).sort();

r = await call("GET", "/list");
out.list_unauth = r.status;
r = await call("GET", "/list", { headers: { "Authorization": "Bearer wrong" } });
out.list_wrong = r.status;
// Double opt-in: new addresses are pending until their confirm link is
// clicked; /list carries confirmed addresses only, ?pending=1 the rest.
r = await call("GET", "/list", { headers: { "Authorization": "Bearer s3cret" } });
out.list_before_confirm = (await r.json()).emails.length;
r = await call("GET", "/list?pending=1", { headers: { "Authorization": "Bearer s3cret" } });
out.pending = { status: r.status, emails: (await r.json()).emails.map((e) => e.email) };
for (const k of out.stored_keys) {
  const rec = JSON.parse(await env.SUBS.get(k));
  r = await call("GET", "/confirm?e=" + k.slice(4) + "&t=" + rec.token);
  out.confirm = { status: r.status, confirmed: JSON.parse(await env.SUBS.get(k)).confirmed };
}
r = await call("GET", "/list", { headers: { "Authorization": "Bearer s3cret" } });
out.list = { status: r.status, body: await r.json() };

const tok = first.token;
r = await call("GET", "/unsubscribe?e=you@example.com&t=badtoken");
out.unsub_wrong = { status: r.status, kept: !!(await env.SUBS.get("sub:you@example.com")) };
r = await call("GET", "/unsubscribe?e=You@example.com&t=" + tok);
out.unsub_page = { status: r.status, text: await r.text(), kept: !!(await env.SUBS.get("sub:you@example.com")) };
r = await call("POST", "/unsubscribe?e=You@example.com&t=" + tok, { body: "List-Unsubscribe=One-Click",
               headers: { "Content-Type": "application/x-www-form-urlencoded" } });
out.unsub_ok = { status: r.status, text: await r.text(), gone: !(await env.SUBS.get("sub:you@example.com")) };
r = await call("POST", "/unsubscribe?e=you@example.com&t=" + tok, { body: "List-Unsubscribe=One-Click",
               headers: { "Content-Type": "application/x-www-form-urlencoded" } });
out.unsub_again = r.status;

let last;
for (let i = 0; i < 20; i++) {
  last = await call("POST", "/subscribe", { body: JSON.stringify({ email: `u${i}@example.com` }),
                                            headers: { ...JSON_HDR, "CF-Connecting-IP": "9.9.9.9" } });
}
out.twentieth = last.status;
r = await call("POST", "/subscribe", { body: JSON.stringify({ email: "u21@example.com" }),
                                       headers: { ...JSON_HDR, "CF-Connecting-IP": "9.9.9.9" } });
out.twenty_first = r.status;

r = await call("OPTIONS", "/subscribe", { headers: { "Origin": "https://wowpriced.com" } });
out.preflight_ok = { status: r.status, origin: r.headers.get("Access-Control-Allow-Origin") };
r = await call("OPTIONS", "/subscribe", { headers: { "Origin": "https://evil.example" } });
out.preflight_bad = r.status;
out.not_found = [(await call("GET", "/")).status, (await call("GET", "/subscribe")).status,
                 (await call("DELETE", "/list")).status];
process.stdout.write(JSON.stringify(out));
"""


def run_worker_harness(tmp_path: Path) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    shutil.copyfile(WORKER_DIR / "worker.js", tmp_path / "worker.mjs")
    for mod in WORKER_DIR.glob("*.js"):  # the ES modules worker.js imports
        shutil.copyfile(mod, tmp_path / mod.name)
    # The .js modules are ES modules on every supported node only when the
    # nearest package.json says so.
    (tmp_path / "package.json").write_text('{"type": "module"}\n')
    (tmp_path / "harness.mjs").write_text(WORKER_HARNESS)
    res = subprocess.run(["node", "harness.mjs"], cwd=tmp_path,
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_worker_static_shape() -> None:
    import tomllib

    js = (WORKER_DIR / "worker.js").read_text()
    assert js.count("\n") <= 175, "keep the Worker small and readable"
    for needle in ('"/subscribe"', '"/unsubscribe"', '"/list"', "crypto.getRandomValues",
                   "env.NEWSLETTER_SECRET", "env.SUBS", "https://wowpriced.com",
                   "https://fznishigeo-cmyk.github.io", "export default"):
        assert needle in js, needle
    toml = tomllib.loads((WORKER_DIR / "wrangler.toml").read_text())
    assert toml["name"] == "wowpriced-subscribe"
    assert toml["main"] == "worker.js"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", toml["compatibility_date"])
    assert toml["kv_namespaces"] == [{"binding": "SUBS", "id": "REPLACED_AT_DEPLOY"}]
    assert "https://wowpriced.com" in toml["vars"]["ALLOWED_ORIGINS"]
    assert (WORKER_DIR / "README.md").is_file()


def test_worker_behaviour(tmp_path: Path) -> None:
    """Runs worker.js under node against an in-memory KV."""
    out = run_worker_harness(tmp_path)
    # POST /subscribe: JSON -> 200 {ok:true} with CORS for an allowed origin;
    # address trimmed + lowercased; idempotent (token kept); token in metadata.
    assert out["json_subscribe"] == {"status": 200, "body": {"ok": True},
                                     "cors": "https://wowpriced.com"}
    assert out["resubscribe"] == {"status": 200, "same_token": True}
    assert out["token_shape"] is True and out["metadata_token"] is True
    # Form posts bounce to the signup page of the (allowed) referring site.
    assert out["form_subscribe"] == {
        "status": 303, "location": "https://www.wowpriced.com/subscribe.html?ok=1"}
    # Invalid address: form -> back with ?err=email (never to a foreign
    # Referer), JSON -> 400.
    assert out["form_invalid"] == {
        "status": 303, "location": "https://wowpriced.com/subscribe.html?err=email"}
    assert out["json_invalid"] == {"status": 400, "body": {"ok": False, "error": "invalid email"}}
    assert out["foreign_origin_cors"] is None
    assert out["stored_keys"] == ["sub:second@example.com", "sub:x@y.com", "sub:you@example.com"]
    # GET /list: bearer secret only; confirmed emails + tokens, nothing else.
    # New addresses are pending (?pending=1) until GET /confirm?e=&t=.
    assert out["list_unauth"] == 401 and out["list_wrong"] == 401
    assert out["list_before_confirm"] == 0
    assert out["pending"] == {"status": 200,
                              "emails": ["second@example.com", "x@y.com", "you@example.com"]}
    assert out["confirm"] == {"status": 200, "confirmed": True}
    assert out["list"]["status"] == 200
    rows = out["list"]["body"]["emails"]
    assert [r["email"] for r in rows] == ["second@example.com", "x@y.com", "you@example.com"]
    assert all(set(r) == {"email", "token"} and re.fullmatch(r"[0-9a-f]{32}", r["token"])
               for r in rows)
    # /unsubscribe: wrong token keeps the record (403); GET with the right
    # token shows a page with one POST button and keeps it; POST (the button
    # or the RFC 8058 one-click form) deletes (200 HTML); twice is still 200.
    assert out["unsub_wrong"] == {"status": 403, "kept": True}
    assert out["unsub_page"]["status"] == 200 and out["unsub_page"]["kept"] is True
    assert 'method="post"' in out["unsub_page"]["text"]
    assert out["unsub_ok"]["status"] == 200 and out["unsub_ok"]["gone"] is True
    assert "You're unsubscribed" in out["unsub_ok"]["text"]
    assert out["unsub_again"] == 200
    # Abuse control: 20 posts per IP per hour, the 21st is refused.
    assert out["twentieth"] == 200 and out["twenty_first"] == 429
    # CORS preflight for allowed origins only; everything else 404.
    assert out["preflight_ok"] == {"status": 204, "origin": "https://wowpriced.com"}
    assert out["preflight_bad"] == 403
    assert out["not_found"] == [404, 404, 404]
