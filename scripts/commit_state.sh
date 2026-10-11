#!/usr/bin/env bash
# commit_state.sh — commit state.db (and sources_report.md when present) to the
# `data` branch of the current repository. NEVER touches main.
#
# Behavior:
#   * no state.db in the repo root            -> no-op, exit 0
#   * state.db identical to origin/data copy  -> no-op, exit 0
#   * STATE_DB_KEY set                        -> commits state.db.enc only
#     (scripts/state_crypt.sh) and removes any unencrypted state.db: the
#     repository is public and state.db holds every member deal
#   * branch encrypted but no STATE_DB_KEY    -> refuses, exit 1
#   * origin/data missing                     -> creates it as an orphan branch
#   * push races (concurrent runs)            -> up to 4 retries, backoff 2 4 8 16 s
#
# Runs against the repository that contains the current working directory, so it
# works the same from GitHub Actions (`bash scripts/commit_state.sh` at the repo
# root), a laptop, or a VPS cron.
set -euo pipefail

BRANCH="data"
BOT_NAME="dealradar-bot"
BOT_EMAIL="actions@users.noreply.github.com"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/state_crypt.sh
. "$SCRIPT_DIR/state_crypt.sh"

REPO_DIR="$(git rev-parse --show-toplevel)"
WORKTREE="$REPO_DIR/.state-worktree"

log() { printf '[commit_state] %s\n' "$*"; }

if [[ ! -f "$REPO_DIR/state.db" ]]; then
  log "state.db not found — nothing to commit (no-op)"
  exit 0
fi

cleanup() {
  git -C "$REPO_DIR" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
  rm -rf "$WORKTREE"
  git -C "$REPO_DIR" worktree prune >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Clear any stale worktree left behind by an earlier interrupted run.
cleanup

git -C "$REPO_DIR" fetch origin "$BRANCH" || true

if git -C "$REPO_DIR" rev-parse --verify --quiet "origin/$BRANCH" >/dev/null; then
  git -C "$REPO_DIR" worktree add "$WORKTREE" "origin/$BRANCH" >/dev/null
else
  log "origin/$BRANCH does not exist yet — creating an orphan branch"
  # A stale local `data` branch (from an earlier failed push) would block
  # `checkout --orphan`; it only ever holds unpushed state snapshots.
  git -C "$REPO_DIR" branch -D "$BRANCH" >/dev/null 2>&1 || true
  git -C "$REPO_DIR" worktree add --detach "$WORKTREE" >/dev/null
  git -C "$WORKTREE" checkout --orphan "$BRANCH" >/dev/null 2>&1
  # Drop everything inherited from the checkout; data holds only state files.
  git -C "$WORKTREE" rm -rf --quiet . >/dev/null 2>&1 || true
  find "$WORKTREE" -mindepth 1 -maxdepth 1 -not -name '.git' -exec rm -rf {} +
fi

report_is_fresh() {
  # Push sources_report.md only when THIS run regenerated it (it differs from
  # the committed copy in the checkout). Otherwise the checkout's committed
  # snapshot would overwrite the real, newer report already on the data branch.
  [[ -f "$REPO_DIR/sources_report.md" ]] || return 1
  if git -C "$REPO_DIR" ls-files --error-unmatch sources_report.md >/dev/null 2>&1; then
    ! git -C "$REPO_DIR" diff --quiet -- sources_report.md
  else
    return 0  # untracked -> definitely produced by this run
  fi
}

same_state() {
  # The committed ciphertext still decrypts to this run's state.db. Encryption
  # salts every write, so comparing ciphertexts would commit on every run.
  local plain rc
  plain="$(mktemp)"
  state_decrypt "$WORKTREE/state.db.enc" "$plain" 2>/dev/null && cmp -s "$plain" "$REPO_DIR/state.db"
  rc=$?
  rm -f "$plain"
  return "$rc"
}

stage_state() {
  if [[ -n "${STATE_DB_KEY:-}" ]]; then
    if ! { [[ -f "$WORKTREE/state.db.enc" ]] && same_state; }; then
      state_encrypt "$REPO_DIR/state.db" "$WORKTREE/state.db.enc"
    fi
    git -C "$WORKTREE" add state.db.enc
    git -C "$WORKTREE" rm --quiet --ignore-unmatch state.db
  elif [[ -f "$WORKTREE/state.db.enc" ]]; then
    log "the data branch is encrypted but STATE_DB_KEY is not set: refusing to commit state.db unencrypted"
    return 1
  else
    printf '::warning::state.db is committed unencrypted to a public branch; add the STATE_DB_KEY repository secret\n'
    cp "$REPO_DIR/state.db" "$WORKTREE/state.db"
    git -C "$WORKTREE" add state.db
  fi
  if report_is_fresh; then
    cp "$REPO_DIR/sources_report.md" "$WORKTREE/sources_report.md"
    git -C "$WORKTREE" add sources_report.md
  fi
}

stage_state

if git -C "$WORKTREE" diff --quiet --cached HEAD -- 2>/dev/null; then
  log "state unchanged — nothing to commit (no-op)"
  exit 0
fi

STAMP="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
git -C "$WORKTREE" -c user.name="$BOT_NAME" -c user.email="$BOT_EMAIL" \
  commit --quiet -m "state: $STAMP [skip ci]"

# Push to the data branch only — never main. Retry up to 4 times on failure
# (a concurrent run may have pushed first) with exponential backoff. A rebase
# can never resolve two state.db commits (binary conflict every time), so each
# retry RE-SNAPSHOTS instead: recreate the worktree at the moved tip, re-copy
# this run's files on top, commit, push. Snapshot semantics: latest writer
# wins, and every commit keeps full history on the branch until the monthly
# squash.
pushed=0
for delay in 2 4 8 16 0; do
  if git -C "$WORKTREE" push origin "HEAD:$BRANCH"; then
    pushed=1
    break
  fi
  if [[ "$delay" -eq 0 ]]; then
    break
  fi
  log "push failed — re-snapshotting from the moved tip and retrying in ${delay}s"
  sleep "$delay"
  git -C "$REPO_DIR" fetch origin "$BRANCH" || true
  if git -C "$REPO_DIR" rev-parse --verify --quiet "origin/$BRANCH" >/dev/null; then
    git -C "$REPO_DIR" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
    rm -rf "$WORKTREE"
    git -C "$REPO_DIR" worktree add "$WORKTREE" "origin/$BRANCH" >/dev/null
    stage_state
    if git -C "$WORKTREE" diff --quiet --cached HEAD -- 2>/dev/null; then
      log "state already on the moved tip — nothing to push"
      pushed=1
      break
    fi
    STAMP="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
    git -C "$WORKTREE" -c user.name="$BOT_NAME" -c user.email="$BOT_EMAIL" \
      commit --quiet -m "state: $STAMP [skip ci]"
  fi
done

if [[ "$pushed" -ne 1 ]]; then
  log "push to origin/$BRANCH failed after 4 retries"
  exit 1
fi
log "state committed to origin/$BRANCH"
