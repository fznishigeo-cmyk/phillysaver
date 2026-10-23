"""Cloudflare R2 copy mode (scripts/r2_copy.sh): best effort, silent about secrets."""
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from dealradar.config import load_config
from tests.test_r2_primary import r2repo  # noqa: F401  (fixture)
from tests.test_workflows import repo  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "r2_copy.sh"
SECRETS = {"R2_ACCESS_KEY_ID": "AKIDSECRETVALUE", "R2_SECRET_ACCESS_KEY": "sk-very-secret",
           "R2_ENDPOINT": "https://acct123.r2.cloudflarestorage.com", "R2_BUCKET": "wowpriced-state"}

FAKE_AWS = """#!/usr/bin/env bash
# Records every call; "ls" lists FAKE_LIST; FAKE_FAIL makes "cp" fail.
printf '%s\\n' "$*" >> "$FAKE_LOG"
printf 'KEY=%s SECRET=%s REGION=%s\\n' "$AWS_ACCESS_KEY_ID" "$AWS_SECRET_ACCESS_KEY" "$AWS_DEFAULT_REGION" >> "$FAKE_LOG"
case "$*" in
  *" s3 cp "*) [[ -n "${FAKE_FAIL:-}" ]] && { echo "${FAKE_FAIL} $AWS_SECRET_ACCESS_KEY https://acct123.r2.cloudflarestorage.com" >&2; exit 1; } ;;
  *" s3 ls "*) cat "$FAKE_LIST" 2>/dev/null ;;
esac
exit 0
"""


def run(tmp_path, *, config=None, secrets=True, fail=False, listing="", aws=True, extra=None, call=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if aws:
        (bin_dir / "aws").write_text(FAKE_AWS)
        (bin_dir / "aws").chmod(0o755)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"state": config if config is not None else {"r2_copy": True, "r2_keep": 2}}))
    state = tmp_path / "state.db.enc"
    state.write_bytes(b"x" * 1234)
    (tmp_path / "list.txt").write_text(listing)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_LOG": str(tmp_path / "calls.log"),
           "FAKE_LIST": str(tmp_path / "list.txt"), "GITHUB_RUN_ID": "777", "GITHUB_RUN_ATTEMPT": "1",
           "HOME": str(tmp_path)}
    if secrets:
        env.update(SECRETS)
    if fail:
        env["FAKE_FAIL"] = fail if isinstance(fail, str) else "boom"
    env.update(extra or {})
    call = call or f'r2_copy "{state}" "{cfg}"'
    result = subprocess.run(["bash", "-c", f'set -euo pipefail; . "{SCRIPT}"; {call}; echo rc=$?'],
                            env=env, capture_output=True, text=True, timeout=60)
    calls = (tmp_path / "calls.log").read_text() if (tmp_path / "calls.log").exists() else ""
    return result, calls


def no_secret_in(text):
    return not any(value in text for value in SECRETS.values())


def test_copies_to_a_timestamped_key_and_latest_then_prunes_old_copies(tmp_path):
    listing = "\n".join(f"2026-10-22 01:00:00 100 {name}" for name in (
        "20261020T010000Z-1-1.enc", "20261021T010000Z-2-1.enc", "20261022T010000Z-3-1.enc", "latest.enc",
        "notes.txt"))
    result, calls = run(tmp_path, listing=listing)
    assert result.returncode == 0 and "rc=0" in result.stdout
    assert "r2 copy: ok 1234 bytes -> state/" in result.stdout and "-777-1.enc" in result.stdout
    lines = calls.splitlines()
    cps = [l for l in lines if " s3 cp " in f" {l} "]
    assert len(cps) == 2 and cps[1].endswith("s3://wowpriced-state/state/latest.enc")
    # Both from the local file: no in-bucket copy (its tag copy is NotImplemented on R2, fast #804).
    assert all(" s3 cp --only-show-errors /" in l for l in cps) and "s3://wowpriced-state/state/2" not in cps[1]
    assert all("--endpoint-url https://acct123.r2.cloudflarestorage.com" in l for l in cps)
    removed = [l for l in lines if " s3 rm " in f" {l} "]
    assert removed == ["--endpoint-url https://acct123.r2.cloudflarestorage.com s3 rm --only-show-errors "
                       "s3://wowpriced-state/state/20261020T010000Z-1-1.enc"]
    assert "KEY=AKIDSECRETVALUE SECRET=sk-very-secret REGION=auto" in calls
    assert no_secret_in(result.stdout + result.stderr)


def test_failure_never_fails_the_caller_or_leaks_the_secret(tmp_path):
    result, _ = run(tmp_path, fail=True)
    assert result.returncode == 0 and "rc=0" in result.stdout
    assert "r2 copy: failed: unrecognized error (the git copy is unaffected)" in result.stdout
    assert no_secret_in(result.stdout + result.stderr)


@pytest.mark.parametrize("message,word", [
    ("An error occurred (AccessDenied) when calling the PutObject operation: Access Denied", "AccessDenied on PutObject"),
    ("An error occurred (NoSuchBucket) when calling the PutObject operation", "NoSuchBucket on PutObject"),
    ("An error occurred (NotImplemented) when calling the GetObjectTagging operation: Not Implemented",
     "NotImplemented on GetObjectTagging"),
    ("An error occurred (SlowDown)", "SlowDown"),
    ("Could not connect to the endpoint URL:", "endpoint unreachable"),
    ("Unable to locate credentials", "no credentials")])
def test_failure_names_only_a_safe_reason(tmp_path, message, word):
    # Live 2026-10-22 (fast #799): "failed" alone could not say why.
    result, _ = run(tmp_path, fail=message)
    assert f"r2 copy: failed: {word} (the git copy is unaffected)" in result.stdout
    assert no_secret_in(result.stdout + result.stderr) and "acct123" not in result.stdout


@pytest.mark.parametrize("config", [{"r2_copy": False}, {}, {"r2_copy": "yes"}, {"r2_copy": True, "r2_keep": 0},
                                    {"r2_copy": True, "r2_keep": True}])
def test_off_or_invalid_settings_copy_nothing(tmp_path, config):
    result, calls = run(tmp_path, config=config)
    assert result.returncode == 0 and "r2 copy: off" in result.stdout and calls == ""


def test_missing_secrets_or_cli_skip_quietly(tmp_path):
    result, calls = run(tmp_path, secrets=False)
    assert "r2 copy: skipped (R2 secrets not set)" in result.stdout and calls == ""
    other = tmp_path / "no-cli"
    other.mkdir()
    result, calls = run(other, aws=False)
    assert "r2 copy: skipped (aws cli missing)" in result.stdout and result.returncode == 0 and calls == ""


def test_commit_state_copies_only_after_the_push_and_shipped_config_is_on():
    text = (ROOT / "scripts" / "commit_state.sh").read_text()
    assert text.index('log "state committed to origin/$BRANCH"') < text.index('r2_copy "$WORKTREE/state.db.enc"')
    assert text.rstrip().endswith('r2_copy "$WORKTREE/state.db.enc" "$REPO_DIR/config.yaml"')
    shipped = load_config().raw["state"]
    assert shipped["r2_copy"] is True and shipped["r2_keep"] == 24


def test_restore_drill_is_manual_read_only_and_checks_integrity():
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "r2-drill.yml").read_text())
    on = doc.get("on", doc.get(True))
    assert list(on) == ["workflow_dispatch"] and doc["permissions"] == {"contents": "read"}
    step = doc["jobs"]["drill"]["steps"][1]
    assert set(step["env"]) == {"STATE_DB_KEY", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "R2_BUCKET"}
    for banned in ("git push", "s3 rm", "s3 cp --only-show-errors \"$work", "commit_state", "wrangler"):
        assert banned not in step["run"]
    assert "PRAGMA integrity_check" in step["run"] and "state/latest.enc" in step["run"]


OLD_COPIES = "\n".join(f"2026-10-22 01:00:00 100 {name}" for name in (
    "20261020T010000Z-1-1.enc", "20261021T010000Z-2-1.enc", "20261022T010000Z-3-1.enc", "latest.enc"))
FAKE_TIMEOUT = """#!/usr/bin/env bash
# Records the limit r2_aws gives each call, then runs the call.
printf '%s\\n' "$1" >> "$FAKE_LIMITS"
shift
exec "$@"
"""


def test_pre_send_checkpoint_leaves_the_copy_and_every_prune_to_the_final_save(tmp_path):
    # Live risk: after a good push, a stalled copy (180 s per call) outlived the
    # checkpoint's 150 s timeout; the killed checkpoint then stopped every send.
    checkpoint = {"DEALRADAR_STATE_CHECKPOINT": "1"}
    result, calls = run(tmp_path, listing=OLD_COPIES, extra=checkpoint)
    assert result.returncode == 0 and "rc=0" in result.stdout and calls == ""
    assert "r2 copy: skipped (pre-send checkpoint; the final save copies)" in result.stdout
    result, calls = run(tmp_path, listing=OLD_COPIES, extra=checkpoint, call="r2_prune 2")
    assert result.returncode == 0 and "rc=0" in result.stdout and calls == ""
    result, calls = run(tmp_path, listing=OLD_COPIES)  # the final save still copies and prunes
    assert "r2 copy: ok" in result.stdout and " s3 rm " in calls


def test_copy_and_prune_share_one_allowance(tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "timeout").write_text(FAKE_TIMEOUT)
    (tmp_path / "bin" / "timeout").chmod(0o755)
    limits = tmp_path / "limits.txt"
    extra = {"FAKE_LIMITS": str(limits), "R2_OPTIONAL_BUDGET_S": "50"}
    result, calls = run(tmp_path, listing=OLD_COPIES, extra=extra)
    assert "r2 copy: ok" in result.stdout
    # cp, cp, ls, rm: every call gets what is left of one 50 s allowance, not 180 s.
    assert len(calls.splitlines()) == 8
    assert all(0 < int(limit) <= 50 for limit in limits.read_text().split())
    # A standalone prune (R2 primary, after its push) gets its own allowance.
    limits.unlink()
    result, _ = run(tmp_path, listing=OLD_COPIES, extra=extra, call="r2_prune 2")
    assert result.returncode == 0 and all(0 < int(limit) <= 50 for limit in limits.read_text().split())
    # A spent allowance makes no call at all and never fails the caller.
    other = tmp_path / "spent"
    other.mkdir()
    result, calls = run(other, listing=OLD_COPIES, extra={"R2_OPTIONAL_BUDGET_S": "0"})
    assert result.returncode == 0 and "rc=0" in result.stdout and calls == ""
    assert "r2 copy: failed: timeout (the git copy is unaffected)" in result.stdout


@pytest.mark.parametrize("primary,ops", [(False, []), (True, ["cp", "cp"])])
def test_commit_state_checkpoint_pushes_git_and_keeps_the_required_r2_upload(r2repo, monkeypatch, primary, ops):
    from tests.test_r2_primary import aws_calls, configure, run as save, tree, write_state

    configure(r2repo, primary=primary, r2_copy=True)
    write_state(r2repo, "v1")
    monkeypatch.setenv("DEALRADAR_STATE_CHECKPOINT", "1")
    res = save(r2repo)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "state committed to origin/data" in res.stdout and tree(r2repo) == {"state.db.enc"}
    # R2 primary still uploads first (required); the copy and the prune wait.
    assert [call.split()[3] for call in aws_calls(r2repo)] == ops


def test_checkpoint_runs_commit_state_as_a_pre_send_checkpoint(tmp_path, monkeypatch):
    from dealradar import persistence
    from dealradar.db import DB

    db = DB(tmp_path / "state.db")
    monkeypatch.setenv("DEALRADAR_DURABLE_STATE", "1")
    monkeypatch.setenv("STATE_DB_KEY", "synthetic-checkpoint-key")
    monkeypatch.delenv("DEALRADAR_STATE_CHECKPOINT", raising=False)
    monkeypatch.setattr(persistence, "ROOT", tmp_path)
    envs = []

    class Save:
        def __init__(self, command, **kwargs):
            envs.append(kwargs.get("env") or dict(os.environ))

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(persistence.subprocess, "Popen", Save)
    persistence.checkpoint_state(db)
    db.close()
    assert envs[0]["DEALRADAR_STATE_CHECKPOINT"] == "1"
    assert envs[0]["STATE_DB_KEY"] == "synthetic-checkpoint-key"
    assert "DEALRADAR_STATE_CHECKPOINT" not in os.environ  # the workflow's final save copies
