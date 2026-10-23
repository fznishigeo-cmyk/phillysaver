"""R2 primary (phase 2, config state.r2_primary): commit_state.sh uploads to R2
first and, at/above state.max_enc_mb, pushes only the pointer state.r2.json;
restore_state.sh follows the pointer or refuses. Offline: a fake `aws` over a
local directory stands in for the bucket, file:// remotes for GitHub."""
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from dealradar.config import load_config
from dealradar.state_size import check_size, validate_limits
from tests.test_workflows import (KEY, RESTORE, SCRIPT, _git, branch_blob, decrypt, openssl_payload,
                                  origin_branches, repo)  # noqa: F401  (repo is a fixture)

SECRETS = {"R2_ACCESS_KEY_ID": "AKIDSECRETVALUE", "R2_SECRET_ACCESS_KEY": "sk-very-secret",
           "R2_ENDPOINT": "https://acct123.r2.cloudflarestorage.com", "R2_BUCKET": "wowpriced-state"}
NEVER_PRINTED = [*SECRETS.values(), "acct123", KEY]
BELOW, ABOVE = 95, 0.0001  # max_enc_mb: the fixtures' encrypted state is a few hundred bytes
ABSENT = object()

FAKE_AWS = r"""#!/usr/bin/env bash
# Fake aws: s3 cp/ls/rm over $FAKE_R2/<bucket>/<key>. Failures print everything
# that must never reach a log (secrets, endpoint, bucket, request details).
printf '%s\n' "$*" >> "$FAKE_LOG"
[[ "$1" == --endpoint-url ]] || exit 2
endpoint="$2"; shift 2
[[ "$1" == s3 ]] || exit 2
op="$2"; shift 2
[[ "${1:-}" == --only-show-errors ]] && shift
local_path() { printf '%s/%s' "$FAKE_R2" "${1#s3://}"; }
fail() {
  echo "fatal error: An error occurred ($1) when calling the $2 operation: $3 $endpoint $AWS_ACCESS_KEY_ID $AWS_SECRET_ACCESS_KEY" >&2
  exit 1
}
[[ -n "${AWS_ACCESS_KEY_ID:-}" && -n "${AWS_SECRET_ACCESS_KEY:-}" ]] || { echo "Unable to locate credentials" >&2; exit 1; }
case "$op" in
  cp)
    if [[ "$2" == s3://* ]]; then
      [[ -z "${FAKE_FAIL_PUT:-}" ]] || fail AccessDenied PutObject "$2"
      # A stalled upload: of every object ("all") or of state/latest.enc only.
      case "${FAKE_SLOW_PUT:-}:$2" in all:*|latest:*/latest.enc) exec sleep 30 ;; esac
      mkdir -p "$(dirname "$(local_path "$2")")" && cp "$1" "$(local_path "$2")"
    else
      [[ -f "$(local_path "$1")" ]] || fail 404 HeadObject "$1"
      cp "$(local_path "$1")" "$2"
    fi ;;
  ls)
    for f in "$(local_path "$1")"*; do
      [[ -f "$f" ]] && printf '2026-10-22 01:00:00 %s %s\n' "$(wc -c < "$f")" "${f##*/}"
    done ;;
  rm) rm -f "$(local_path "$1")" ;;
  *) exit 2 ;;
esac
exit 0
"""


@pytest.fixture
def r2repo(repo):
    bin_dir = repo.parent / "bin"
    bin_dir.mkdir()
    (bin_dir / "aws").write_text(FAKE_AWS)
    (bin_dir / "aws").chmod(0o755)
    (repo.parent / "r2").mkdir()
    return repo


def bucket(repo) -> Path:
    return repo.parent / "r2" / SECRETS["R2_BUCKET"]


def aws_calls(repo) -> list[str]:
    log = repo.parent / "aws.log"
    return log.read_text().splitlines() if log.exists() else []


def configure(repo, *, primary=True, max_mb=BELOW, **extra) -> None:
    state = {"r2_keep": 2, "max_enc_mb": max_mb, **extra}
    if primary is not ABSENT:
        state["r2_primary"] = primary
    (repo / "config.yaml").write_text(yaml.safe_dump({"state": state}))


def write_state(repo, label: str, rows: int = 1) -> bytes:
    path = repo / "state.db"
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("CREATE TABLE facts(label TEXT)")
        conn.executemany("INSERT INTO facts VALUES (?)", [(f"{label}-{i}",) for i in range(rows)])
    conn.close()
    return path.read_bytes()


def run(repo, script=SCRIPT, *, secrets=True, key=KEY, run_id="101", fail_put=False, aws=True):
    tmp = repo.parent
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("R2_", "AWS_", "GITHUB_")) and k != "STATE_DB_KEY"}
    # Without the fake, also hide any real aws CLI: tests never reach a network.
    path = env["PATH"] if aws else ":".join(d for d in env["PATH"].split(":") if not (Path(d) / "aws").exists())
    env.update(PATH=f"{tmp / 'bin'}:{path}" if aws else path,
               GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null",
               FAKE_R2=str(tmp / "r2"), FAKE_LOG=str(tmp / "aws.log"),
               GITHUB_RUN_ID=run_id, GITHUB_RUN_ATTEMPT="1")
    if key is not None:
        env["STATE_DB_KEY"] = key
    if secrets:
        env.update(SECRETS)
    if fail_put:
        env["FAKE_FAIL_PUT"] = "1"
    result = subprocess.run(["bash", str(script)], cwd=repo, capture_output=True, text=True,
                            env=env, timeout=300)
    output = result.stdout + result.stderr
    assert not [s for s in NEVER_PRINTED if s in output], output
    return result


def tree(repo) -> set[str]:
    _git(repo, "fetch", "origin", "+refs/heads/data:refs/remotes/origin/data")
    return set(_git(repo, "ls-tree", "--name-only", "origin/data").splitlines())


def tip(repo) -> str:
    _git(repo, "fetch", "origin", "+refs/heads/data:refs/remotes/origin/data")
    return _git(repo, "rev-parse", "origin/data")


def pointer(repo) -> dict:
    tree(repo)
    return json.loads(branch_blob(repo, "state.r2.json"))


def push_to_data(repo, files: dict[str, bytes]) -> None:
    """Commit files on top of the data tip from a separate worktree."""
    work = repo.parent / "edit"
    _git(repo, "fetch", "origin", "+refs/heads/data:refs/remotes/origin/data")
    _git(repo, "worktree", "add", "--detach", str(work), "origin/data")
    for name, data in files.items():
        (work / name).write_bytes(data)
        _git(work, "add", name)
    _git(work, "commit", "-m", "edit")
    _git(work, "push", "origin", "HEAD:data")
    _git(repo, "worktree", "remove", "--force", str(work))


# -------------------------------------------------------------------- save


@pytest.mark.parametrize("primary", [False, ABSENT], ids=["false", "absent"])
def test_off_saves_and_refuses_exactly_as_before(r2repo, primary):
    repo = r2repo
    configure(repo, primary=primary)
    plain = write_state(repo, "v1")
    res = run(repo)
    assert res.returncode == 0, res.stdout + res.stderr
    patterns = [r"\[commit_state\] size \{.*\}", "[commit_state] origin/data does not exist yet — creating an orphan branch",
                r"\[commit_state\] state\.db \d+ -> state\.db\.enc \d+", "[commit_state] state committed to origin/data",
                "[commit_state] r2 copy: off"]
    lines = res.stdout.splitlines()
    assert len(lines) == len(patterns)
    for line, pattern in zip(lines, patterns):
        assert re.fullmatch(pattern, line) or line == pattern, line
    assert tree(repo) == {"state.db.enc"}
    assert decrypt(branch_blob(repo, "state.db.enc"), repo.parent) == plain
    before = tip(repo)
    configure(repo, primary=primary, max_mb=ABOVE)
    write_state(repo, "v2")
    res = run(repo)
    assert res.returncode == 1 and res.stderr == ""
    assert re.fullmatch(r"\[commit_state\] size \{.*\}\n\[commit_state\] ERROR enc_bytes=\d+ at/above "
                        r"state\.max_enc_mb=0\.0001; refusing push\n", res.stdout)
    assert tip(repo) == before and aws_calls(repo) == []


def test_on_below_the_limit_uploads_first_then_pushes_the_enc(r2repo):
    repo = r2repo
    configure(repo)
    plain = write_state(repo, "v1")
    res = run(repo)
    assert res.returncode == 0, res.stdout + res.stderr
    out = res.stdout
    key, = re.findall(r"r2 primary: ok \d+ bytes -> (state/\d{8}T\d{6}Z-101-1\.enc)\n", out)
    # R2 first, before any git remote access; no second (copy mode) upload after the push.
    assert out.index("r2 primary: ok") < out.index("creating an orphan branch") < out.index("state committed")
    assert "r2 copy" not in out and "pointer" not in out
    assert tree(repo) == {"state.db.enc"}
    enc = branch_blob(repo, "state.db.enc")
    assert (bucket(repo) / key).read_bytes() == enc == (bucket(repo) / "state/latest.enc").read_bytes()
    assert decrypt(enc, repo.parent) == plain
    ops = [call.split()[3] for call in aws_calls(repo)]
    assert ops == ["cp", "cp", "ls"]  # the prune lists only after the push landed


def test_on_above_the_limit_pushes_only_the_pointer_and_succeeds(r2repo):
    repo = r2repo
    configure(repo)
    write_state(repo, "v1")
    assert run(repo).returncode == 0
    old_enc = branch_blob(repo, "state.db.enc")
    configure(repo, max_mb=ABOVE)
    plain = write_state(repo, "v2", rows=50)
    (repo / "sources_report.md").write_text("# report\n")
    res = run(repo, run_id="202")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "refusing" not in res.stdout and "ERROR" not in res.stdout
    assert re.search(r"enc_bytes=\d+ at/above state\.max_enc_mb=0\.0001; r2 primary: the data branch gets "
                     r"only the R2 pointer\n", res.stdout)
    assert re.search(r"state\.db \d+ -> R2 \d+ \+ state\.r2\.json\n", res.stdout)
    assert "state committed to origin/data" in res.stdout
    assert tree(repo) == {"state.db.enc", "state.r2.json", "sources_report.md"}
    assert branch_blob(repo, "state.db.enc") == old_enc  # the big file is not pushed
    p = pointer(repo)
    assert set(p) == {"key", "bytes", "sha256", "saved_at", "run_id"} and p["run_id"] == "202"
    assert re.fullmatch(r"state/\d{8}T\d{6}Z-202-1\.enc", p["key"])
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", p["saved_at"])
    obj = (bucket(repo) / p["key"]).read_bytes()
    assert len(obj) == p["bytes"] and hashlib.sha256(obj).hexdigest() == p["sha256"]
    assert decrypt(obj, repo.parent) == plain
    # Restore: the pointer is newer than the enc beside it.
    (repo / "state.db").unlink()
    res = run(repo, RESTORE)
    assert res.returncode == 0, res.stdout + res.stderr
    assert f"state restored from R2 ({p['key']}, {p['bytes']} bytes, sha256 verified)" in res.stdout
    assert (repo / "state.db").read_bytes() == plain


@pytest.mark.parametrize("primary_after", [True, False], ids=["still-on", "turned-off"])
def test_restore_takes_the_enc_once_an_enc_save_follows_the_pointer(r2repo, primary_after):
    repo = r2repo
    configure(repo, max_mb=ABOVE)
    first = write_state(repo, "v1")
    assert run(repo).returncode == 0
    assert tree(repo) == {"state.r2.json"}  # first save ever: no enc at all
    (repo / "state.db").unlink()
    res = run(repo, RESTORE)
    assert res.returncode == 0 and "state restored from R2" in res.stdout
    assert (repo / "state.db").read_bytes() == first
    configure(repo, primary=primary_after)
    second = write_state(repo, "v2")
    res = run(repo)
    assert res.returncode == 0, res.stdout + res.stderr
    assert tree(repo) == {"state.db.enc"}  # the enc save deletes the now-stale pointer
    (repo / "state.db").unlink()
    calls = len(aws_calls(repo))
    res = run(repo, RESTORE, secrets=False)  # git alone, no R2 needed
    assert res.returncode == 0 and "state restored (decrypted)" in res.stdout
    assert (repo / "state.db").read_bytes() == second and len(aws_calls(repo)) == calls


@pytest.mark.parametrize("limit", [BELOW, ABOVE], ids=["below", "above"])
@pytest.mark.parametrize("problem,message", [
    ("secrets", "[commit_state] r2 primary: R2 secrets not set; refusing push"),
    ("cli", "[commit_state] r2 primary: aws cli missing; refusing push"),
    ("upload", "[commit_state] r2 primary: upload failed: AccessDenied on PutObject; refusing push"),
])
def test_save_fails_closed_when_r2_cannot_take_the_copy(r2repo, limit, problem, message):
    repo = r2repo
    configure(repo)
    write_state(repo, "v1")
    assert run(repo).returncode == 0
    before, calls = tip(repo), len(aws_calls(repo))
    configure(repo, max_mb=limit)
    write_state(repo, "v2")
    res = run(repo, secrets=problem != "secrets", aws=problem != "cli", fail_put=problem == "upload")
    assert res.returncode == 1 and message in res.stdout.splitlines()
    assert "could not establish remote state" not in res.stdout and "state committed" not in res.stdout
    assert tip(repo) == before
    assert len(aws_calls(repo)) == calls + (problem == "upload")
    assert not (repo / ".state-worktree").exists()


def test_raced_pointer_push_keeps_latest_writer_wins(r2repo, tmp_path):
    """A run that loses the push race re-snapshots its pointer onto the moved tip."""
    repo = r2repo
    configure(repo)
    write_state(repo, "v1")
    assert run(repo).returncode == 0
    other = tmp_path / "other"
    _git(tmp_path, "clone", "--quiet", "--branch", "data", (tmp_path / "origin.git").as_uri(), str(other))
    (other / "state.db.enc").write_bytes(openssl_payload(gzip.compress(write_state(repo, "other"))))
    _git(other, "commit", "--quiet", "-am", "state: competing run")
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text(f"""#!/usr/bin/env bash
[[ -e "{tmp_path}/raced" ]] && exit 0
touch "{tmp_path}/raced"
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_COMMON_DIR
git -C "{other}" push --quiet origin HEAD:data >/dev/null 2>&1
exit 1
""")
    hook.chmod(0o755)
    configure(repo, max_mb=ABOVE)
    plain = write_state(repo, "v2")
    res = run(repo, run_id="303")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "push failed — re-snapshotting from the moved tip" in res.stdout
    assert _git(repo, "log", "-1", "--format=%s", "origin/data~1") == "state: competing run"
    assert tree(repo) == {"state.db.enc", "state.r2.json"}
    assert pointer(repo)["run_id"] == "303"
    (repo / "state.db").unlink()
    assert run(repo, RESTORE).returncode == 0
    assert (repo / "state.db").read_bytes() == plain


# ------------------------------------------------------ pre-send checkpoint

RECORD_TIMEOUT = r"""#!/usr/bin/env bash
# Records the limit r2_aws gives each call, then enforces it with the real timeout.
printf '%s\n' "$1" >> "$FAKE_LIMITS"
exec "$REAL_TIMEOUT" "$@"
"""


def as_checkpoint(repo, monkeypatch, *, slow=""):
    """Route dealradar.persistence's checkpoints to the real commit_state.sh in
    the fixture repo, over the fake R2, with the env the checkpoint builds.
    Returns the file that collects the script's stdout."""
    from dealradar import persistence

    tmp = repo.parent
    (tmp / "bin" / "timeout").write_text(RECORD_TIMEOUT)
    (tmp / "bin" / "timeout").chmod(0o755)
    env = {**SECRETS, "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}", "REAL_TIMEOUT": shutil.which("timeout"),
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "FAKE_R2": str(tmp / "r2"),
           "FAKE_LOG": str(tmp / "aws.log"), "FAKE_LIMITS": str(tmp / "limits.txt"), "FAKE_SLOW_PUT": slow,
           "GITHUB_RUN_ID": "404", "GITHUB_RUN_ATTEMPT": "1", "STATE_DB_KEY": KEY, "DEALRADAR_DURABLE_STATE": "1"}
    for name in [n for n in os.environ if n.startswith(("R2_", "AWS_", "GITHUB_", "DEALRADAR_"))]:
        monkeypatch.delenv(name)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    output = tmp / "checkpoint.log"

    def popen(command, **kwargs):
        assert command == ["bash", str(repo.resolve() / "scripts" / "commit_state.sh")]
        with output.open("w") as out:
            return subprocess.Popen(["bash", str(SCRIPT)], **{**kwargs, "stdout": out})

    monkeypatch.setattr(persistence, "ROOT", repo.resolve())
    monkeypatch.setattr(persistence, "subprocess", SimpleNamespace(
        Popen=popen, DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired))
    return output


@pytest.mark.parametrize("primary,r2_s", [(True, None), (True, 0), (False, 0)],
                         ids=["primary", "primary-r2-share-spent", "off-r2-share-spent"])
def test_checkpoint_r2_deadline_binds_only_the_primary_upload(r2repo, monkeypatch, primary, r2_s):
    from dealradar import persistence

    repo = r2repo
    configure(repo, primary=primary, r2_copy=True)
    plain = write_state(repo, "v1")
    output = as_checkpoint(repo, monkeypatch)
    if r2_s is not None:
        monkeypatch.setattr(persistence, "CHECKPOINT_R2_S", r2_s)  # spent before the first upload
    limits = repo.parent / "limits.txt"
    db = SimpleNamespace(path=str(repo / "state.db"))
    if primary and r2_s == 0:
        with pytest.raises(OSError, match=r"^state checkpoint failed \(exit 1\)$"):
            persistence.checkpoint_state(db)
        assert "[commit_state] r2 primary: upload failed: timeout; refusing push" in output.read_text().splitlines()
        assert aws_calls(repo) == [] and origin_branches(repo) == {"main"}
        return
    persistence.checkpoint_state(db)
    assert tree(repo) == {"state.db.enc"} and decrypt(branch_blob(repo, "state.db.enc"), repo.parent) == plain
    if primary:
        # Normal checkpoint: both uploads first, each limited to the checkpoint's R2 share (not 180 s).
        assert [call.split()[3] for call in aws_calls(repo)] == ["cp", "cp"]
        assert all(persistence.CHECKPOINT_R2_S - 10 < int(s) <= persistence.CHECKPOINT_R2_S
                   for s in limits.read_text().split())
        return
    # Off: the deadline is never read; the checkpoint logs and pushes exactly as before.
    assert aws_calls(repo) == [] and not limits.exists()
    patterns = [r"\[commit_state\] size \{.*\}", "[commit_state] origin/data does not exist yet — creating an orphan branch",
                r"\[commit_state\] state\.db \d+ -> state\.db\.enc \d+", "[commit_state] state committed to origin/data",
                "[commit_state] r2 copy: skipped (pre-send checkpoint; the final save copies)"]
    lines = output.read_text().splitlines()
    assert len(lines) == len(patterns)
    for line, pattern in zip(lines, patterns):
        assert re.fullmatch(pattern, line) or line == pattern, line


@pytest.mark.parametrize("limit,slow", [(BELOW, "all"), (ABOVE, "all"), (ABOVE, "latest")],
                         ids=["enc", "pointer", "pointer-latest-only"])
def test_slow_upload_fails_the_checkpoint_cleanly_and_the_final_save_retries(r2repo, monkeypatch, caplog,
                                                                             limit, slow):
    # Code review 2026-10-23: with r2_primary on, each checkpoint upload had
    # up to 180 s, past CHECKPOINT_TIMEOUT_S; the checkpoint was then killed,
    # possibly mid-push. The uploads now stop at the checkpoint's R2 deadline.
    from dealradar import persistence
    from dealradar.db import DB

    repo = r2repo
    configure(repo, max_mb=limit)
    output = as_checkpoint(repo, monkeypatch, slow=slow)
    # Scaled down 15x (150 s / 60 s); the fake upload stalls 30 s, past both.
    monkeypatch.setattr(persistence, "CHECKPOINT_TIMEOUT_S", 10)
    monkeypatch.setattr(persistence, "CHECKPOINT_R2_S", 4)
    limits = repo.parent / "limits.txt"
    db = DB(repo / "state.db")
    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="dealradar.persistence"):
        assert not persistence.prepare_delivery(db, "telegram", "deal-1", datetime(2026, 10, 24, 12, tzinfo=timezone.utc))
    assert time.monotonic() - started < persistence.CHECKPOINT_TIMEOUT_S
    # The script refused on its own; the checkpoint was not killed.
    assert "(state checkpoint failed (exit 1)); no sends this run" in caplog.text
    assert "timed out" not in caplog.text
    assert any(re.fullmatch(r"\[commit_state\] r2 primary: upload failed: [a-z ]+; refusing push", line)
               for line in output.read_text().splitlines()), output.read_text()
    assert not [s for s in NEVER_PRINTED if s in output.read_text()]
    # Refused before git: no data branch (so no pointer), no worktree left over.
    assert origin_branches(repo) == {"main"} and not (repo / ".state-worktree").exists()
    assert [call.split()[3] for call in aws_calls(repo)] == ["cp"] * (2 if slow == "latest" else 1)
    assert not (bucket(repo) / "state" / "latest.enc").exists()
    checkpoint_limits = limits.read_text().split()
    assert all(0 < int(s) <= persistence.CHECKPOINT_R2_S for s in checkpoint_limits)
    # As before: this send waits for the next run, the process sends nothing more.
    assert db.checkpoint_failed
    assert db.conn.execute("SELECT status FROM outgoing WHERE dedup_token='deal-1'").fetchone()[0] == "rejected"
    db.close()
    # The workflow's final save (no checkpoint marker, no deadline: 180 s per call) lands.
    monkeypatch.delenv("FAKE_SLOW_PUT")
    res = run(repo, run_id="505")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "state committed to origin/data" in res.stdout
    assert limits.read_text().split()[len(checkpoint_limits):][:2] == ["180", "180"]
    if limit == ABOVE:
        p = pointer(repo)
        obj = (bucket(repo) / p["key"]).read_bytes()
        assert p["run_id"] == "505" and len(obj) == p["bytes"] and hashlib.sha256(obj).hexdigest() == p["sha256"]
    else:
        assert tree(repo) == {"state.db.enc"}
    (repo / "state.db").unlink()
    res = run(repo, RESTORE)
    assert res.returncode == 0, res.stdout + res.stderr
    with closing(sqlite3.connect(repo / "state.db")) as conn:
        assert conn.execute("SELECT status FROM outgoing WHERE dedup_token='deal-1'").fetchone() == ("rejected",)


# ----------------------------------------------------------------- restore


def tamper(repo, how: str) -> dict:
    p = pointer(repo)
    obj = bucket(repo) / p["key"]
    if how == "sha":
        data = bytearray(obj.read_bytes())
        data[-1] ^= 1
        obj.write_bytes(bytes(data))
    elif how == "size":
        obj.write_bytes(obj.read_bytes() + b"x")
    elif how == "missing":
        obj.unlink()
    elif how == "pointer":
        push_to_data(repo, {"state.r2.json": b'{"key": "../../etc/passwd", "bytes": 1, "sha256": "00"}'})
    elif how == "not-sqlite":
        fake = openssl_payload(gzip.compress(b"not a SQLite database", mtime=0))
        obj.write_bytes(fake)
        push_to_data(repo, {"state.r2.json": json.dumps(
            {**p, "bytes": len(fake), "sha256": hashlib.sha256(fake).hexdigest()}).encode()})
    return p


@pytest.mark.parametrize("how,kwargs,message", [
    ("sha", {}, "{key} sha256 differs from state.r2.json"),
    ("size", {}, "{key} size differs from state.r2.json"),
    ("missing", {}, "could not download {key} from R2: 404 on HeadObject"),
    ("pointer", {}, "state.r2.json is invalid"),
    ("not-sqlite", {}, "could not decrypt or validate {key} (wrong key or corrupt state)"),
    (None, {"key": "the wrong key"}, "could not decrypt or validate {key} (wrong key or corrupt state)"),
    (None, {"secrets": False}, "state.r2.json names an R2 object but the R2 secrets are not set"),
    (None, {"key": None}, "state.r2.json names an R2 object but STATE_DB_KEY is not set"),
    (None, {"aws": False}, "state.r2.json names an R2 object but the aws cli is missing"),
])
def test_restore_refuses_instead_of_falling_back_to_git(r2repo, how, kwargs, message):
    repo = r2repo
    configure(repo)
    write_state(repo, "v1")
    assert run(repo).returncode == 0  # an older, valid state.db.enc stays on the branch
    configure(repo, max_mb=ABOVE)
    write_state(repo, "v2")
    assert run(repo).returncode == 0
    p = tamper(repo, how)
    (repo / "state.db").write_bytes(b"stale local copy")
    res = run(repo, RESTORE, **kwargs)
    assert res.returncode == 1
    assert (f"[restore_state] {message.format(key=p['key'])}: refusing to start (never from an older git copy)"
            in res.stdout.splitlines()), res.stdout
    assert "state restored" not in res.stdout
    assert not (repo / "state.db").exists()


# ------------------------------------------------------------------ config


@pytest.mark.parametrize("value", ["true", "false", 1, 0, None])
def test_r2_primary_must_be_a_strict_boolean(r2repo, value):
    repo = r2repo
    with pytest.raises(ValueError, match="state.r2_primary must be a boolean"):
        validate_limits({"r2_primary": value})
    configure(repo, primary=value)
    with pytest.raises(ValueError, match="r2_primary"):
        load_config(repo / "config.yaml")
    write_state(repo, "v1")
    res = run(repo)
    assert res.returncode == 1 and "invalid size configuration" in res.stdout and "refusing push" in res.stdout
    assert origin_branches(repo) == {"main"} and aws_calls(repo) == []


def test_unreadable_keep_with_primary_on_refuses(r2repo):
    repo = r2repo
    configure(repo, r2_keep=0)
    write_state(repo, "v1")
    res = run(repo)
    assert res.returncode == 1
    assert "[commit_state] r2 primary: unreadable state.r2_primary/state.r2_keep; refusing push" in res.stdout
    assert origin_branches(repo) == {"main"} and aws_calls(repo) == []


def test_size_guard_returns_the_pointer_code_only_with_primary_on(capsys):
    assert check_size(95_000_000, 70, 95) == 1
    assert "refusing push" in capsys.readouterr().out
    assert check_size(95_000_000, 70, 95, r2_primary=True) == 3
    assert "r2 primary: the data branch gets only the R2 pointer" in capsys.readouterr().out
    assert check_size(94_999_999, None, 95, r2_primary=True) == 0


def test_shipped_off_with_both_lanes_ready_to_restore_from_r2():
    assert load_config().raw["state"]["r2_primary"] is False
    validate_limits({"r2_primary": True, "max_enc_mb": 95})
    root = Path(__file__).resolve().parent.parent
    for lane in ("hourly.yml", "fast.yml"):
        doc = yaml.safe_load((root / ".github" / "workflows" / lane).read_text())
        steps = next(iter(doc["jobs"].values()))["steps"]
        restore = next(s for s in steps if s.get("run") == "bash scripts/restore_state.sh")
        assert set(restore["env"]) == {"STATE_DB_KEY", *SECRETS}
