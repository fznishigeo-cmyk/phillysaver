#!/usr/bin/env bash
# restore_state.sh — restore state.db from the `data` branch before a run.
#
#   * state.db.enc on the branch -> decrypt it with STATE_DB_KEY. A missing or
#                                   wrong key fails the run: starting empty
#                                   would overwrite the real state.
#   * state.db (unencrypted)     -> copy it as is
#   * neither / no data branch   -> first run, start empty
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/state_crypt.sh
. "$SCRIPT_DIR/state_crypt.sh"

REPO_DIR="$(git rev-parse --show-toplevel)"

log() { printf '[restore_state] %s\n' "$*"; }

if git -C "$REPO_DIR" ls-remote --exit-code --heads origin refs/heads/data >/dev/null; then
  git -C "$REPO_DIR" fetch origin refs/heads/data:refs/remotes/origin/data
else
  remote_status=$?
  if [[ "$remote_status" -ne 2 ]] || git -C "$REPO_DIR" rev-parse --verify --quiet origin/data >/dev/null; then
    log "could not establish remote state: refusing to start empty"
    exit 1
  fi
  log "no prior state"
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
    log "could not decrypt state.db.enc: STATE_DB_KEY is not the key it was written with"
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
