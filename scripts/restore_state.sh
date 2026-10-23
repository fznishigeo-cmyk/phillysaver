#!/usr/bin/env bash
# restore_state.sh — restore state.db from the `data` branch before a run.
#
#   * state.r2.json on the branch -> R2 primary (config state.r2_primary): the
#                                   newest state is the R2 object it names.
#                                   Every state.db.enc save deletes the pointer,
#                                   so a pointer at the tip is newer than any
#                                   state.db.enc beside it. Download exactly that
#                                   key, check bytes + sha256, decrypt; any
#                                   failure refuses to start. Never falls back to
#                                   the older git copy: stale alerts_sent/outgoing
#                                   rows would resend alerts.
#   * state.db.enc on the branch -> decrypt it with STATE_DB_KEY. A missing or
#                                   wrong key fails the run: starting empty
#                                   would overwrite the real state.
#   * state.db (unencrypted)     -> copy it as is
#   * neither / no data branch   -> first run, start empty
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/state_crypt.sh
. "$SCRIPT_DIR/state_crypt.sh"
# shellcheck source=scripts/r2_copy.sh
. "$SCRIPT_DIR/r2_copy.sh"

REPO_DIR="$(git rev-parse --show-toplevel)"

log() { printf '[restore_state] %s\n' "$*"; }

if git -C "$REPO_DIR" ls-remote --exit-code --heads origin refs/heads/data >/dev/null; then
  if [[ "$(git -C "$REPO_DIR" rev-parse --is-shallow-repository)" == true ]]; then
    # A depth-1 tip cannot prove ancestry; refresh only this remote state ref.
    git -C "$REPO_DIR" fetch --depth=1 origin +refs/heads/data:refs/remotes/origin/data
  else
    git -C "$REPO_DIR" fetch origin +refs/heads/data:refs/remotes/origin/data
  fi
else
  remote_status=$?
  if [[ "$remote_status" -ne 2 ]] || git -C "$REPO_DIR" rev-parse --verify --quiet origin/data >/dev/null; then
    log "could not establish remote state: refusing to start empty"
    exit 1
  fi
  log "no prior state"
  exit 0
fi

r2_refuse() {
  rm -f "$REPO_DIR/state.db"
  log "$1: refusing to start (never from an older git copy)"
  exit 1
}

if git -C "$REPO_DIR" cat-file -e origin/data:state.r2.json 2>/dev/null; then
  [[ -n "${STATE_DB_KEY:-}" ]] || r2_refuse "state.r2.json names an R2 object but STATE_DB_KEY is not set"
  if [[ -z "${R2_ACCESS_KEY_ID:-}" || -z "${R2_SECRET_ACCESS_KEY:-}" || -z "${R2_ENDPOINT:-}" || -z "${R2_BUCKET:-}" ]]; then
    r2_refuse "state.r2.json names an R2 object but the R2 secrets are not set"
  fi
  command -v aws >/dev/null 2>&1 || r2_refuse "state.r2.json names an R2 object but the aws cli is missing"
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  git -C "$REPO_DIR" cat-file blob origin/data:state.r2.json > "$work/pointer.json"
  # Prints "<key> <bytes> <sha256>" for a well-formed pointer, else fails.
  pointer="$(python3 - "$work/pointer.json" <<'PY' 2>/dev/null
import json, re, sys
p = json.load(open(sys.argv[1]))
key, size, sha = p["key"], p["bytes"], p["sha256"]
if not (type(key) is str and re.fullmatch(r"state/[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9]+-[0-9]+\.enc", key)
        and type(size) is int and size > 0 and type(sha) is str and re.fullmatch(r"[0-9a-f]{64}", sha)):
    sys.exit(1)
print(key, size, sha)
PY
  )" || r2_refuse "state.r2.json is invalid"
  read -r key bytes sha <<<"$pointer"
  R2_ERR_FILE="$work/r2.err"
  if ! r2_aws s3 cp --only-show-errors "s3://$R2_BUCKET/$key" "$work/state.db.enc" >/dev/null; then
    r2_refuse "could not download $key from R2: $(r2_reason "$R2_ERR_FILE")"
  fi
  [[ "$(wc -c < "$work/state.db.enc" | tr -d ' ')" == "$bytes" ]] || r2_refuse "$key size differs from state.r2.json"
  got="$(python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' \
    "$work/state.db.enc")" || got=""
  [[ "$got" == "$sha" ]] || r2_refuse "$key sha256 differs from state.r2.json"
  if ! state_decrypt "$work/state.db.enc" "$REPO_DIR/state.db" require-gzip 2>/dev/null \
     || [[ "$(head -c 15 "$REPO_DIR/state.db" 2>/dev/null)" != "SQLite format 3" ]]; then
    r2_refuse "could not decrypt or validate $key (wrong key or corrupt state)"
  fi
  log "state restored from R2 ($key, $bytes bytes, sha256 verified)"
  exit 0
fi

if git -C "$REPO_DIR" cat-file -e origin/data:state.db.enc 2>/dev/null; then
  if [[ -z "${STATE_DB_KEY:-}" ]]; then
    log "state.db is encrypted on the data branch but STATE_DB_KEY is not set"
    exit 1
  fi
  enc="$(mktemp)"
  trap 'rm -f "$enc"' EXIT
  git -C "$REPO_DIR" cat-file blob origin/data:state.db.enc > "$enc"
  if ! state_decrypt "$enc" "$REPO_DIR/state.db" 2>/dev/null \
     || [[ "$(head -c 15 "$REPO_DIR/state.db" 2>/dev/null)" != "SQLite format 3" ]]; then
    rm -f "$REPO_DIR/state.db"
    log "could not decrypt or validate state.db.enc (wrong key or corrupt state): refusing to start empty"
    exit 1
  fi
  log "state restored (decrypted)"
elif git -C "$REPO_DIR" cat-file -e origin/data:state.db 2>/dev/null; then
  git -C "$REPO_DIR" cat-file blob origin/data:state.db > "$REPO_DIR/state.db"
  log "state restored"
else
  log "data branch contains no state: refusing to start empty"
  exit 1
fi
