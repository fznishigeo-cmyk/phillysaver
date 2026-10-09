"""Tests for the ops layer: GitHub Actions workflows, scripts/commit_state.sh,
and README.md. Fully offline — the commit-state script is exercised against
local temporary git repositories (file:// remotes), never the network.
"""

from __future__ import annotations

import os
import re
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
SCRIPT = ROOT / "scripts" / "commit_state.sh"
README = ROOT / "README.md"

SECRET_NAMES = [
    "DEALS_CLAUDE_API_KEY",
    "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT",
    "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "TELEGRAM_ADMIN_CHAT_ID",
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
    "WOOT_API_KEY", "MEH_API_KEY", "KEEPA_KEY", "THREADS_TOKEN",
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


# ---------------------------------------------------------------- YAML shape


def test_all_workflows_parse() -> None:
    for p in (HOURLY, FAST, SQUASH):
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
    assert any("git fetch origin data || true" in r
               and "git checkout origin/data -- state.db" in r for r in runs)
    assert any(r.strip() == "python -m dealradar run" for r in runs)
    assert any("bash scripts/commit_state.sh" in r for r in runs)

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
    assert on["schedule"] == [{"cron": "*/15 * * * *"}]
    # Same concurrency group as hourly so the two never overlap.
    assert doc["concurrency"] == {"group": "dealradar", "cancel-in-progress": False}
    job = single_job(doc)
    assert job["timeout-minutes"] == 10

    runs = run_commands(doc)
    assert any(r.strip() == "python -m dealradar run --fast-lane" for r in runs)
    assert any("git fetch origin data || true" in r for r in runs)
    assert any("bash scripts/commit_state.sh" in r for r in runs)

    # Fast lane skips the Pages deploy: the hourly deploy refreshes the site.
    uses = " ".join(s.get("uses", "") for s in job["steps"])
    assert "pages" not in uses.lower()

    run_step = next(s for s in job["steps"]
                    if s.get("run", "").strip() == "python -m dealradar run --fast-lane")
    assert set(run_step["env"]) == set(SECRET_NAMES)


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


# ------------------------------------------------------------------- safety


def test_no_workflow_pushes_to_main() -> None:
    """Every git push in any workflow or in commit_state.sh targets data only."""
    texts = {p.name: p.read_text() for p in (HOURLY, FAST, SQUASH, SCRIPT)}
    for name, text in texts.items():
        for line in text.splitlines():
            if "git push" in line or re.search(r"\bpush origin\b", line):
                assert "main" not in line and "master" not in line, (name, line)
                assert ":data" in line or '"HEAD:$BRANCH"' in line, (name, line)


def test_no_secret_is_echoed() -> None:
    """`${{ secrets.X }}` appears only as env-map values, never inside run/echo."""
    env_line = re.compile(r"^\s+[A-Z_]+: \$\{\{ secrets\.[A-Z_]+ \}\}$")
    for p in (HOURLY, FAST, SQUASH):
        for line in p.read_text().splitlines():
            if "secrets." in line:
                assert env_line.match(line), (p.name, line)
            if "echo" in line:
                assert "secrets." not in line and "$DEALS" not in line, (p.name, line)
    script = SCRIPT.read_text()
    for name in SECRET_NAMES:
        assert name not in script


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


def run_script(repo_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)], cwd=repo_dir, capture_output=True, text=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
             "GIT_CONFIG_SYSTEM": "/dev/null"},
    )


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


# ------------------------------------------------------------------- README


def test_readme_covers_the_contract() -> None:
    text = README.read_text()
    # architecture + badges + floors
    assert "SAVINGS_RULES.md" in text and "sources_report.md" in text
    for badge in ("corroborated", "history-checked", "unverified"):
        assert badge in text
    assert "min_pct" in text and "70" in text
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
